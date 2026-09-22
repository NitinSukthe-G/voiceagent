from collections import deque

import numpy as np
import webrtcvad
import config


def rms(frame):
    x = np.frombuffer(frame, dtype=np.int16).astype(np.float32)
    return float(np.sqrt(np.mean(x * x)))


class TurnDetector:
    """Feeds 20 ms frames to VAD. Says when speech starts and ends.

    A frame is speech only if it is loud enough AND webrtcvad agrees.
    Callers can raise the bar for one frame (barge-in on speakers).
    """

    def __init__(self):
        self.vad = webrtcvad.Vad(config.VAD_MODE)
        self.speaking = False
        self.speech_ms = 0
        self.silence_ms = 0
        self.recent = deque(maxlen=config.FLOOR_FRAMES)

    def floor(self):
        """Rolling noise floor: the 20th percentile of recent frame RMS."""
        if len(self.recent) < 25:
            return 0.0
        return sorted(self.recent)[len(self.recent) // 5]

    def gate(self, min_rms=None):
        base = config.MIN_RMS if min_rms is None else min_rms
        return max(base, config.FLOOR_RATIO * self.floor())

    def feed(self, frame, min_rms=None, start_ms=None):
        level = rms(frame)
        self.recent.append(level)
        need = config.START_MS if start_ms is None else start_ms

        is_speech = (level >= self.gate(min_rms)
                     and self.vad.is_speech(frame, config.RATE))

        if is_speech:
            self.speech_ms += config.FRAME_MS
            self.silence_ms = 0
        else:
            self.silence_ms += config.FRAME_MS
            self.speech_ms = 0

        if not self.speaking and self.speech_ms >= need:
            self.speaking = True
            return "start"

        if self.speaking and self.silence_ms >= config.SILENCE_MS:
            self.speaking = False
            return "end"

        return None
