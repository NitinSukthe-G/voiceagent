import time
import config

ORDER = [
    "vad_end",
    "stt_final",
    "ack_start",              # cached "Okay." - first sound the caller hears
    "filler_start",           # cached "One moment" - the instant a tool call starts
    "llm_first_token",
    "first_sentence_ready",
    "tts_first_audio",
    "playback_start",
]


class Turn:
    def __init__(self, number):
        self.number = number
        now = time.perf_counter()
        # the user really stopped SILENCE_MS before VAD said "end"
        self.t0 = now - config.SILENCE_MS / 1000
        self.marks = {"vad_end": now}

    def mark(self, name):
        self.marks.setdefault(name, time.perf_counter())

    def has(self, name):
        return name in self.marks

    def ms(self, name):
        return (self.marks[name] - self.t0) * 1000

    def report(self):
        print(f"\n[turn {self.number}]")
        print(f" {'user_speech_end':<22}: 0 ms")
        names = sorted(self.marks, key=self.marks.get)
        for name in names:
            if name in ORDER:
                tag = "   <-- total" if name == "playback_start" else ""
                print(f" {name:<22}: {self.ms(name):.0f} ms{tag}")


class LatencyLog:
    def __init__(self):
        self.turns = []

    def new_turn(self):
        t = Turn(len(self.turns) + 1)
        self.turns.append(t)
        return t

    def summary(self):
        done = [t for t in self.turns if t.has("playback_start")]
        if not done:
            print("\nNo complete turns to report.")
            return
        print(f"\n===== latency over {len(done)} turns =====")
        print(f" {'point':<22} {'avg':>8} {'worst':>8}")
        for name in ORDER:
            vals = [t.ms(name) for t in done if t.has(name)]
            if vals:
                avg = sum(vals) / len(vals)
                print(f" {name:<22} {avg:>6.0f}ms {max(vals):>6.0f}ms")
