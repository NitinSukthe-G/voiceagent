import asyncio
import threading
import time
import sounddevice as sd
import config


class Mic:
    """Reads the microphone and puts 20 ms frames in a queue."""

    def __init__(self):
        self.queue = asyncio.Queue()
        self.loop = asyncio.get_running_loop()
        self.stream = sd.RawInputStream(
            samplerate=config.RATE,
            channels=1,
            dtype="int16",
            blocksize=config.FRAME,
            callback=self._on_audio,
        )

    def _on_audio(self, data, frames, time_info, status):
        # runs in the sound card thread, not in asyncio
        frame = bytes(data)
        self.loop.call_soon_threadsafe(self.queue.put_nowait, frame)

    def start(self):
        self.stream.start()

    def stop(self):
        self.stream.stop()
        self.stream.close()


class Player:
    """Plays PCM bytes as they arrive. stop() = instant silence."""

    def __init__(self):
        self.buf = bytearray()
        self.lock = threading.Lock()
        self.queued = 0           # bytes ever given to play()
        self.played = 0           # bytes ever handed to the sound card
        self.watchers = []        # (offset, callback): fires once played > offset
        self.stream = sd.RawOutputStream(
            samplerate=config.RATE,
            channels=1,
            dtype="int16",
            blocksize=config.FRAME,
            callback=self._fill,
        )

    def _fill(self, out, frames, time_info, status):
        need = len(out)
        with self.lock:
            chunk = bytes(self.buf[:need])
            del self.buf[:need]
            self.played += len(chunk)
            due = [cb for off, cb in self.watchers if self.played > off]
            self.watchers = [w for w in self.watchers if self.played <= w[0]]
        for cb in due:
            cb()
        out[:len(chunk)] = chunk
        if len(chunk) < need:
            out[len(chunk):] = b"\x00" * (need - len(chunk))

    def play(self, pcm):
        with self.lock:
            self.buf.extend(pcm)
            self.queued += len(pcm)

    def watch_next(self, callback):
        """Call back when audio queued from now on starts playing, even if
        something earlier is still in the buffer."""
        with self.lock:
            self.watchers.append((self.queued, callback))

    def stop(self):
        with self.lock:
            self.played += len(self.buf)   # dropped, but keep offsets honest
            self.buf.clear()
            self.watchers.clear()          # that audio will never play

    def busy(self):
        return len(self.buf) > 0

    def start(self):
        self.stream.start()

    def close(self):
        self.stream.stop()
        self.stream.close()
