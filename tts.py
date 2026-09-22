import asyncio
import base64
import json
from urllib.parse import urlencode

import websockets
import config

URL = "wss://api.sarvam.ai/text-to-speech/ws"


def strip_wav_header(data):
    if data[:4] == b"RIFF":
        i = data.find(b"data")
        if i != -1:
            return data[i + 8:]
    return data


class SarvamTTS:
    def __init__(self, player, speaker=None):
        self.player = player
        self.speaker = speaker or config.TTS_VOICE
        self.ws = None
        self.ready = asyncio.Event()
        self.on_first_audio = None
        # idle is set when every flush we sent has come back "final".
        # It is how callers know a reply is really finished speaking.
        self.flushes = 0
        self.completions = 0
        self.idle = asyncio.Event()
        self.idle.set()

    async def connect(self):
        params = {
            "model": config.TTS_MODEL,
            "send_completion_event": "true",
        }
        url = URL + "?" + urlencode(params)
        headers = {"Api-Subscription-Key": config.API_KEY}
        ws = await websockets.connect(url, additional_headers=headers)

        # first message must be the config
        cfg = {
            "type": "config",
            "data": {
                "speaker": self.speaker,
                "language_code": config.TTS_LANG,
                "pace": 1.0,
                "speech_sample_rate": config.RATE,
                "output_audio_codec": "linear16",
                "min_buffer_size": 50,
                "max_chunk_length": 150,
            },
        }
        await ws.send(json.dumps(cfg))
        # a fresh socket has no outstanding flushes
        self.flushes = self.completions = 0
        self.idle.set()
        self.ws = ws
        asyncio.create_task(self._receive(ws))
        self.ready.set()
        print("[tts] connected")

    async def _receive(self, ws):
        try:
            async for raw in ws:
                if ws is not self.ws:
                    break          # old socket after barge-in
                msg = json.loads(raw)
                kind = msg.get("type")
                if kind == "audio":
                    audio = base64.b64decode(msg["data"]["audio"])
                    if self.on_first_audio:
                        self.on_first_audio()
                        self.on_first_audio = None
                    self.player.play(strip_wav_header(audio))
                elif kind == "event":
                    if msg.get("data", {}).get("event_type") == "final":
                        self.completions += 1
                        if self.completions >= self.flushes:
                            self.idle.set()
                    else:
                        print("[tts] event:", raw[:160])
                elif kind == "error":
                    # the flush this belonged to will never produce audio;
                    # count it or idle stays clear forever
                    print("[tts] error:", msg.get("data"))
                    self.completions += 1
                    if self.completions >= self.flushes:
                        self.idle.set()
                else:
                    print("[tts] unhandled event:", raw[:160])
        except websockets.ConnectionClosed:
            pass

    async def say(self, text, _retry=True):
        await self.ready.wait()
        try:
            await self.ws.send(json.dumps(
                {"type": "text", "data": {"text": text}}
            ))
            await self.ws.send(json.dumps({"type": "flush"}))
        except websockets.ConnectionClosed:
            if not _retry:
                print("[tts] send failed twice, dropping this sentence")
                return
            print("[tts] reconnecting...")
            self.ready.clear()
            await self.connect()
            await self.say(text, _retry=False)
            return
        # No await between the flush and this, so a completion event for
        # this flush cannot slip in before idle is cleared.
        self.flushes += 1
        self.idle.clear()

    async def render(self, text):
        """Synthesize to bytes instead of the speaker (for cached phrases)."""
        class _Buf:
            def __init__(self):
                self.buf = bytearray()

            def play(self, pcm):
                self.buf.extend(pcm)

            def stop(self):
                self.buf.clear()

        real, self.player = self.player, _Buf()
        try:
            await self.say(text)
            await asyncio.wait_for(self.idle.wait(), 30)
            return bytes(self.player.buf)
        finally:
            self.player = real

    def detach(self):
        """Orphan the current socket NOW, without awaiting.

        Barge-in needs this synchronous: _receive() only drops frames once
        self.ws has changed, so deferring it lets in-flight audio refill the
        buffer that player.stop() just cleared. Idempotent.
        """
        self.ready.clear()
        old, self.ws = self.ws, None
        self.flushes = self.completions = 0
        self.idle.set()
        return old

    async def reset(self, old=None):
        """Barge-in: drop the old socket, open a fresh one.

        barge_in() detaches synchronously and passes the socket in; if it
        were detached again here, the second call would find nothing and
        the socket would be orphaned with its reader blocked forever.
        """
        if old is None:
            old = self.detach()
        self.player.stop()
        if old:
            await old.close()
            # A socket abandoned mid-synthesis never gets its TLS close
            # answered by the server, so the transport would sit half-closed
            # until exit and print a traceback. Abort it: nothing is owed.
            if old.transport:
                old.transport.abort()
        await self.connect()

    async def close(self):
        old = self.detach()
        if old:
            await old.close()

    async def keepalive(self):
        # server closes idle sockets after ~1 minute
        while True:
            await asyncio.sleep(30)
            if self.ws and self.ready.is_set():
                try:
                    await self.ws.send(json.dumps({"type": "ping"}))
                except websockets.ConnectionClosed:
                    pass
