import asyncio
import json
import signal
import time
from collections import deque

import sounddevice as sd
from bson import Binary

import config
import db
import llm
from audio_io import Mic, Player
from latency import LatencyLog
from transcript import Transcript
from prompt import (OPENING, EMERGENCY_LINE, DESK_GREETING,
                    build_desk_prompt, build_prompt, is_emergency)
from stt import SarvamSTT
from tools import TOOLS, alert_emergency, run_tool
from tts import SarvamTTS
from vad import TurnDetector

log = LatencyLog()


class Agent:
    def __init__(self, player=None, mic=None):
        # player must be injected here, not set later: SarvamTTS captures it
        self.player = player or Player()
        self.mic = mic            # None = open the real mic in setup()
        self.stt = SarvamSTT()
        self.tts = SarvamTTS(self.player)
        self.vad = TurnDetector()
        self.history = []
        self.reply_task = None
        self.spoken = ""          # what Priya said this turn
        self.current_text = ""    # what the user said this turn
        self.carry = ""           # user text kept after a pause
        self.preroll = deque(maxlen=config.PREROLL_MS // config.FRAME_MS)
        self.speakers = False     # set in setup()
        self.echo_until = 0.0     # barge gate stays up until this time
        self.phrases = {}         # text -> pcm, played with no API call
        self.transcript = None    # opened in setup()
        self.filler_done = False  # one filler per turn
        self.acked = False        # one ack per turn, even if it arrives in pieces
        self.mode = "priya"       # "desk" after an emergency handoff

    # ---------- main loop: mic -> VAD -> STT ----------

    async def setup(self):
        # warm connections once, at start. The LLM warm-up matters: the first
        # HTTPS call pays ~2.5 s of TLS setup that would otherwise land on
        # the caller's first turn.
        await asyncio.gather(self.stt.connect(), self.tts.connect(),
                             self._warm_llm())
        asyncio.create_task(self.tts.keepalive())
        await self._load_phrases()

        if self.mic is None:
            self.mic = Mic()
        self.player.start()
        self.mic.start()

        # on speakers the mic hears Priya, so interrupting her needs a
        # louder, longer voice than the echo can produce
        if config.SPEAKERS is None:
            out = sd.query_devices(kind="output")["name"]
            self.speakers = "headphone" not in out.lower()
        else:
            self.speakers = config.SPEAKERS
        print("[audio] speakers: barge-in needs a firm voice"
              if self.speakers else "[audio] headphones")

        self.transcript = Transcript()
        self.stt.on_event = self.transcript.note
        print(f"[log] conversation {self.transcript.name}")
        await self.say_cached(OPENING, None)
        self.transcript.priya(OPENING)
        print(f"\nPriya: {OPENING}")

    async def _warm_llm(self):
        try:
            await llm.client.chat.completions.create(
                model=config.LLM_MODEL, max_tokens=1,
                messages=[{"role": "user", "content": "hi"}])
        except Exception as e:
            print(f"[llm] warm-up failed: {e}")

    async def _load_phrases(self):
        """Fixed lines are synthesized once and kept in the database as PCM,
        so the opening, the emergency line and the tool filler play instantly."""
        col = db.connect().phrases
        wanted = [(t, config.TTS_VOICE)
                  for t in (OPENING, EMERGENCY_LINE, config.ACK, config.FILLER)]
        wanted.append((DESK_GREETING, config.DESK_VOICE))
        for text, voice in wanted:
            if not text:
                continue
            key = f"{voice}|{text}"
            doc = await asyncio.to_thread(col.find_one, {"key": key})
            if doc is not None:
                self.phrases[text] = bytes(doc["pcm"])
                continue
            # render in the right voice: a second socket for anything that is
            # not Priya, so her live connection keeps its own speaker
            if voice == self.tts.speaker:
                pcm = await self.tts.render(text)
            else:
                other = SarvamTTS(self.player, speaker=voice)
                await other.connect()
                pcm = await other.render(text)
                await other.close()
            await asyncio.to_thread(col.insert_one, {
                "key": key, "voice": voice, "text": text, "pcm": Binary(pcm)})
            self.phrases[text] = pcm

    async def handoff(self, turn):
        """Hand the caller to the emergency desk: new voice, new brain.

        There is no telephony here, so nothing goes down a phone line. What
        changes is who the caller is talking to - a different voice and a
        different system prompt, with the history carried over so they are
        not made to repeat themselves.
        """
        self.mode = "desk"
        print(f"\n[handoff] booking -> emergency desk")
        self.transcript.note("handoff: booking -> emergency desk")
        try:
            self.tts.speaker = config.DESK_VOICE
            await self.tts.reset()
        except Exception as e:
            # the greeting still plays, just in the old voice; being triaged
            # matters more than sounding like a different person
            print(f"[handoff] voice switch failed: {e}")
        await self.say_cached(DESK_GREETING, turn)

    async def say_cached(self, text, turn, mark=None, record=True):
        """Play a pre-synthesized phrase straight into the player.

        record=False for backchannels like the ack: they must not count as
        Priya having answered, or a mid-sentence pause reads as a barge-in.
        """
        pcm = self.phrases.get(text)
        if pcm is None:                    # not cached: fall back to TTS
            await self.speak(text, turn)
            return
        if turn and mark:
            self.player.watch_next(lambda: turn.mark(mark))
        if record:
            self.spoken += text + " "
        self.player.play(pcm)

    async def step(self, frame):
        """One 20 ms frame through VAD -> STT. Returns the VAD event."""
        now = time.perf_counter()
        if self.player.busy():
            self.echo_until = now + config.ECHO_TAIL_MS / 1000
        if self.speakers and now < self.echo_until:
            event = self.vad.feed(frame, config.BARGE_RMS,
                                  config.BARGE_START_MS)
        else:
            event = self.vad.feed(frame)

        if event == "start":
            print("[SPEECH START]")
            if self.agent_busy():
                await self.barge_in()
            else:
                self.acked = False
            self.stt.reset()
            for old in self.preroll:
                await self.stt.send(old)
            self.preroll.clear()

        if self.vad.speaking or event == "end":
            await self.stt.send(frame)
        else:
            self.preroll.append(frame)

        if event == "end":
            print("[SPEECH END]")
            turn = log.new_turn()
            # reset here, not inside handle_turn: barge_in() reads spoken to
            # tell a pause from an interruption, and the task has not run yet
            self.spoken = ""
            self.current_text = ""
            self.reply_task = asyncio.create_task(
                self.handle_turn(turn)
            )
        return event

    async def run(self):
        await self.setup()
        print("\nListening... (Ctrl+C to stop)\n")
        while True:
            await self.step(await self.mic.queue.get())

    def agent_busy(self):
        thinking = self.reply_task and not self.reply_task.done()
        return thinking or self.player.busy()

    async def barge_in(self):
        running = self.reply_task and not self.reply_task.done()
        if running and not self.spoken:
            # Priya said nothing yet: the user only paused.
            # Keep their words and join them with the next part.
            print("[PAUSE] user continued, merging text")
            self.transcript.note("pause, merging")
            if self.current_text:
                # current_text already has any earlier carry merged in
                self.carry = self.current_text
                if self.history and self.history[-1]["role"] == "user":
                    self.history.pop()
            else:
                # cancelled before the transcript landed: add to the carry,
                # never replace it, or a second pause drops the first half
                more = " ".join(self.stt.texts)
                self.carry = (self.carry + " " + more).strip()
            self.reply_task.cancel()
            return

        print("[BARGE-IN] stopping Priya")
        self.transcript.note("barge-in")
        self.acked = False
        if running:
            self.reply_task.cancel()
        old = self.tts.detach()   # now, or in-flight audio refills the buffer
        self.player.stop()
        asyncio.create_task(self.tts.reset(old))

    # ---------- one user turn ----------

    async def handle_turn(self, turn):
        text = await self.stt.final_text()
        turn.mark("stt_final")
        if self.carry:
            text = (self.carry + " " + text).strip()
            self.carry = ""
        if not text:
            print("[empty transcript, ignoring]")
            return

        print(f"\nYou  : {text}")
        self.transcript.user(text)
        self.current_text = text
        self.history.append({"role": "user", "content": text})
        self.filler_done = False

        try:
            if is_emergency(text) and self.mode == "priya":
                # speak first, escalate second: the caller hears the
                # instruction in ~0.8 s and the write happens behind it
                await self.say_cached(EMERGENCY_LINE, turn, "playback_start")
                ref = await asyncio.to_thread(alert_emergency, text)
                print(f"\n[emergency] escalation logged: {ref}")
                self.transcript.note(f"emergency escalated: {ref}")
                await self.handoff(turn)
            else:
                if config.ACK and not self.acked:
                    # instant, from cache: the caller hears something now,
                    # not after the LLM and TTS round trips
                    self.acked = True
                    await self.say_cached(config.ACK, turn, "ack_start",
                                          record=False)
                await self.think_and_speak(turn)
        except asyncio.CancelledError:
            print("\n[reply cancelled]")
            raise
        except Exception as e:
            print(f"\n[error] {e}")
            await self.speak("Sorry, can you say that again?", turn)
        finally:
            if self.spoken:
                self.transcript.priya(self.spoken.strip(), self.mode)
                self.history.append(
                    {"role": "assistant", "content": self.spoken.strip()}
                )
            self.history = self.history[-config.HISTORY_MESSAGES:]
            while self.history and self.history[0]["role"] != "user":
                self.history.pop(0)

        # Report from its own task: waiting here would keep reply_task alive
        # for seconds, which reads as "busy" and turns the next thing the user
        # says into a barge-in that cancels the report.
        asyncio.create_task(self._report(turn))

    async def _report(self, turn):
        # wait until sound actually starts, then print timings
        for _ in range(100):
            if turn.has("playback_start"):
                break
            await asyncio.sleep(0.05)
        turn.report()
        self.transcript.timing(turn.number, {
            name: round(turn.ms(name)) for name in turn.marks})

    async def think_and_speak(self, turn):
        # rebuilt per turn: it embeds NOW and a 15 day calendar, which go
        # stale in a long session and break "tomorrow" / "next Friday"
        desk = self.mode == "desk"
        system = build_desk_prompt() if desk else build_prompt()
        tools = None if desk else TOOLS          # the desk cannot book
        messages = [{"role": "system", "content": system}]
        messages += self.history
        print("Desk : " if desk else "Priya: ", end="", flush=True)

        for _ in range(4):          # max 4 tool rounds
            buf = ""
            text_this_round = ""
            calls = None

            async for kind, value in llm.stream_reply(messages, tools):
                if kind == "text":
                    turn.mark("llm_first_token")
                    print(value, end="", flush=True)
                    buf += value
                    text_this_round += value
                    done, buf = llm.split_sentences(llm.tidy(buf))
                    for sentence in done:
                        await self.speak(sentence, turn)
                elif kind == "tool_start":
                    # say it now, while the arguments are still streaming,
                    # instead of after the whole tool round finishes
                    if config.FILLER and not self.filler_done:
                        self.filler_done = True
                        await self.say_cached(config.FILLER, turn,
                                              "filler_start")
                else:
                    calls = value

            if buf.strip():
                await self.speak(buf.strip(), turn)

            if not calls:
                break

            msg = {"role": "assistant", "tool_calls": [
                {"id": c["id"], "type": "function",
                 "function": {"name": c["name"], "arguments": c["args"]}}
                for c in calls
            ]}
            if text_this_round:
                msg["content"] = text_this_round
            messages.append(msg)

            for c in calls:
                result = await asyncio.to_thread(run_tool, c["name"], c["args"])
                self.transcript.tool(c["name"], c["args"], result)
                print(f"\n[tool] {c['name']}({c['args']}) -> {result}")
                messages.append({
                    "role": "tool",
                    "tool_call_id": c["id"],
                    "content": json.dumps(result),
                })
        print()

    async def speak(self, text, turn):
        text = text.strip()
        if not text:
            return
        if turn and not turn.has("first_sentence_ready"):
            turn.mark("first_sentence_ready")
            self.tts.on_first_audio = lambda: turn.mark("tts_first_audio")
            self.player.watch_next(lambda: turn.mark("playback_start"))
        self.spoken += text + " "
        await self.tts.say(text)

    async def close(self):
        """Shut everything down. Each step on its own: one failure must not
        skip the rest, or a socket leaks and Python complains at exit."""
        for step in (self._close_mic, self.player.close,
                     self.stt.close, self.tts.close):
            try:
                r = step()
                if asyncio.iscoroutine(r):
                    await r
            except Exception as e:
                print(f"[close] {step.__name__}: {e}")
        if self.transcript:
            self.transcript.close()

    def _close_mic(self):
        if self.mic:
            self.mic.stop()


async def main():
    if not config.API_KEY:
        print("SARVAM_API_KEY missing. Put it in the .env file.")
        return
    if not config.MONGODB_URL:
        print("MONGODB_URL missing. Put it in the .env file.")
        return
    agent = Agent()

    # Ctrl+C: cancel only this task, so close() runs while the sockets are
    # still healthy. Left to asyncio.run(), KeyboardInterrupt tears every task
    # down at once and the websocket readers die before close() reaches them,
    # which leaks an SSL transport and prints a traceback at exit.
    me = asyncio.current_task()
    loop = asyncio.get_running_loop()
    signal.signal(signal.SIGINT,
                  lambda *_: loop.call_soon_threadsafe(me.cancel))
    try:
        await agent.run()
    except asyncio.CancelledError:
        pass
    finally:
        await agent.close()
        try:
            await llm.client.close()   # module-level, so it belongs to main()
        except Exception:
            pass
        await asyncio.sleep(0.2)       # let transports finish closing
        db.close()


if __name__ == "__main__":
    asyncio.run(main())
    log.summary()
    print()
    print("Bye!")
