import asyncio
import base64
import http.client
import io
import json
import uuid
import wave
from urllib.parse import urlencode

import websockets
import config

URL = "wss://api.sarvam.ai/speech-to-text/ws"
REST_HOST = "api.sarvam.ai"
REST_PATH = "/speech-to-text"


def pcm_to_wav(pcm):
    """Raw PCM + 44 byte header = a small WAV file."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(config.RATE)
        w.writeframes(pcm)
    return buf.getvalue()


class SarvamSTT:
    def __init__(self):
        self.ws = None
        self.reader = None
        self.pending = bytearray()   # audio not sent yet
        self.texts = []              # transcript pieces of this turn
        self.turn_audio = bytearray()  # everything sent this turn, for REST
        self.collecting = False      # accept stream pieces only mid-turn
        self.http = None             # kept-alive HTTPS connection for REST
        self.got_text = asyncio.Event()
        self.on_event = None         # optional: called with a short string

    async def connect(self):
        params = {
            "language-code": config.STT_LANG,
            "model": config.STT_MODEL,
            "mode": config.STT_MODE,
            "sample_rate": str(config.RATE),
            "input_audio_codec": "wav",
            "flush_signal": "true",
        }
        url = URL + "?" + urlencode(params)
        headers = {"Api-Subscription-Key": config.API_KEY}
        if self.reader:
            self.reader.cancel()  # reconnect: don't leak the previous reader
        self.ws = await websockets.connect(
            url, additional_headers=headers
        )
        self.reader = asyncio.create_task(self._receive())
        if self.http is None:
            await asyncio.to_thread(self._warm_rest)
        print("[stt] connected")

    async def _receive(self):
        try:
            async for raw in self.ws:
                msg = json.loads(raw)
                kind = msg.get("type")
                if kind == "data":
                    text = msg["data"].get("transcript", "").strip()
                    if text and self.collecting:
                        print(f"[stt] piece: {text}")
                        self.texts.append(text)
                        self.got_text.set()
                    elif text:
                        # the turn already finished (REST answered, or the
                        # wait timed out); attaching this to the NEXT turn
                        # would corrupt it
                        print(f"[stt] late piece dropped: {text}")
                elif kind == "error":
                    print("[stt] error:", msg.get("data"))
                    if self.on_event:
                        self.on_event(f"stt error: {msg.get('data')}")
        except websockets.ConnectionClosed as e:
            print(f"[stt] closed: {e}")
            if self.on_event:
                self.on_event(f"stt closed: {e}")

    async def _send_pending(self):
        if not self.pending:
            return
        wav = pcm_to_wav(bytes(self.pending))
        msg = json.dumps({
            "audio": {
                "data": base64.b64encode(wav).decode(),
                "sample_rate": config.RATE,
                "encoding": "audio/wav",
            }
        })
        # keep self.pending until the send succeeds, so a dropped socket
        # doesn't silently eat the audio it was carrying
        try:
            await self.ws.send(msg)
        except websockets.ConnectionClosed:
            print("[stt] reconnecting...")
            await self.connect()
            try:
                await self.ws.send(msg)
            except websockets.ConnectionClosed:
                print("[stt] send failed twice, dropping audio")
        self.pending.clear()

    async def send(self, frame):
        # group 5 frames = 100 ms per message
        self.pending.extend(frame)
        self.turn_audio.extend(frame)
        if len(self.pending) >= config.BYTES_PER_FRAME * 5:
            await self._send_pending()

    def _rest_sync(self, pcm):
        """One-shot transcription over a kept-alive HTTPS connection.

        The streaming socket returns nothing for very short clips; this
        endpoint handles them in ~300 ms once the connection is warm. A fresh
        TLS handshake per call would cost 1-2 s, hence the reuse.
        """
        boundary = "----sarvam" + uuid.uuid4().hex
        body = b"".join([
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"model\"\r\n\r\n"
            f"{config.STT_REST_MODEL}\r\n".encode(),
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"language_code\"\r\n\r\n"
            f"{config.STT_LANG}\r\n".encode(),
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
            f"filename=\"turn.wav\"\r\nContent-Type: audio/wav\r\n\r\n".encode(),
            pcm_to_wav(pcm), f"\r\n--{boundary}--\r\n".encode(),
        ])
        headers = {"api-subscription-key": config.API_KEY,
                   "Content-Type": f"multipart/form-data; boundary={boundary}"}
        for attempt in (1, 2):
            try:
                if self.http is None:
                    self.http = http.client.HTTPSConnection(REST_HOST, timeout=10)
                self.http.request("POST", REST_PATH, body=body, headers=headers)
                r = self.http.getresponse()
                data = r.read()
                if r.status != 200:
                    print(f"[stt] rest HTTP {r.status}: {data[:80]!r}")
                    return ""
                return json.loads(data).get("transcript", "").strip()
            except (http.client.HTTPException, OSError) as e:
                self.http = None            # stale keep-alive: reconnect once
                if attempt == 2:
                    print(f"[stt] rest failed: {e}")
                    return ""

    async def _rest(self, pcm):
        return await asyncio.to_thread(self._rest_sync, pcm)

    def _warm_rest(self):
        try:
            self.http = http.client.HTTPSConnection(REST_HOST, timeout=10)
            self.http.connect()             # pay the TLS handshake now
        except OSError as e:
            print(f"[stt] rest warm-up failed: {e}")
            self.http = None

    async def close(self):
        if self.reader:
            self.reader.cancel()
            self.reader = None
        if self.http:
            self.http.close()
            self.http = None
        if self.ws:
            ws, self.ws = self.ws, None
            await ws.close()

    def reset(self):
        """New user turn: forget old pieces."""
        self.texts.clear()
        self.got_text.clear()
        self.turn_audio.clear()
        self.collecting = True

    async def final_text(self):
        """User stopped talking. Get the full text now.

        Short turns go straight to REST: the stream drops them and REST is
        faster than waiting to find that out. Long turns use the stream, which
        has been transcribing while the user talked, with REST as a fallback.
        """
        await self._send_pending()
        self.got_text.clear()
        audio = bytes(self.turn_audio)
        short = len(audio) < config.RATE * 2 * config.STT_SHORT_TURN_S
        try:
            await self.ws.send(json.dumps({"type": "flush"}))
        except websockets.ConnectionClosed:
            # idle sockets get closed; don't surface that as a failed turn
            print("[stt] reconnecting...")
            await self.connect()
        else:
            if not short:
                # if we already have text, don't wait long for more
                wait = 0.4 if self.texts else 1.5
                try:
                    await asyncio.wait_for(self.got_text.wait(), wait)
                except asyncio.TimeoutError:
                    pass
        self.collecting = False           # anything arriving now is stale
        text = " ".join(self.texts).strip()
        self.texts.clear()
        if not text and len(audio) >= config.BYTES_PER_FRAME * 10:
            text = await self._rest(audio)
            if text:
                print(f"[stt] rest: {text}")
        return text
