# Latency

Measured during development with a mic-less test script — a scripted conversation through the real
pipeline with no microphone (Sarvam TTS speaks the user's lines, fed in as 20 ms
mic frames). 7 turns: an emergency, a barge-in, a complete booking.

**Caveat:** synthesized speech is cleaner than a human voice and there is no real
mic capture in the path. These numbers are indicative; a real mic run is the
final test.

## What the caller hears now

Zero is the moment the user stopped speaking (`latency.py` backdates `t0` by
`SILENCE_MS`, so the endpointing wait is counted, not hidden).

| moment | typical | what it is |
| --- | --- | --- |
| ~1.7 s | `filler_start` | "One moment." — the instant the LLM begins a tool call, from cache |
| ~1.7–3.0 s | `playback_start` | the actual answer |
| ~0.8 s | emergency, total | the whole emergency line, from cache |

(An instant "Okay." at ~0.75 s is available via `config.ACK` and was measured to
land reliably, but a word before every reply got tiresome in real use and it
is off by default.)

Per point, latest run (7 turns):

| point | avg | worst | best turn |
| --- | --- | --- | --- |
| vad_end | 450 ms | 450 ms | 450 ms |
| stt_final | 764 ms | 914 ms | 658 ms |
| **ack_start** | **771 ms** | 919 ms | 694 ms |
| filler_start (tool turns) | 1725 ms | 1957 ms | 1486 ms |
| llm_first_token | 2542 ms | 5300 ms | 1328 ms |
| tts_first_audio | 3010 ms | 5941 ms | 1647 ms |
| **playback_start** | **2712 ms** | 5954 ms | **1654 ms** |

The 5.3 s worst case is one LLM call; the same turn took 1.3 s in other runs.
Sarvam's LLM latency varies a lot run to run (medians between 950 ms and 2 s
were seen across the same afternoon).

## Where the floor is, and why

Three things were measured directly, and none of them can be made faster from
this side:

| stage | measured | what was tried |
| --- | --- | --- |
| LLM first token | ~950 ms median, tiny prompt, warm connection | Only one usable model: `sarvam-105b-conversations`. `sarvam-105b` is a reasoning model (streams thinking, never text). `sarvam-m`/`30b` are deprecated. Prompt size costs ~160 ms for the full 700-token prompt; there is **no prefix caching** (identical repeated prompts do not speed up). `reasoning_effort` makes no difference. |
| TTS first byte | ~450 ms median | Current `min_buffer_size=50 / max_chunk_length=150` is the fastest accepted; smaller values are rejected. `bulbul:v2` lacks the `priya` voice. |
| STT final after flush | ~230 ms probe; 200–500 ms in practice | Long turns: stream, 1.5 s cap (a timeout, not a delay). Turns under 1 s: the stream drops them ("ok": 0 of 24 tries), so they go straight to REST over a kept-alive connection, ~300–500 ms and 10 of 10 transcribed. |

So a real answer cannot arrive before roughly **STT + LLM + TTS ≈ 1.65 s**, plus
the endpointing wait. The best turns above sit exactly there.

## What changed

**Instant phrases from cache.** The opening, the emergency line, "Okay." and
"One moment." are synthesized once (stored in the `phrases` collection) and played straight into the
player with no API round trip. Emergency turns went from ~1.5 s to ~0.8 s. The
opening plays the moment the agent starts.

**An optional acknowledgment at ~0.75 s.** With `config.ACK = "Okay."` it plays
as soon as the transcript lands, so the caller hears a response in under a
second. It is a backchannel: not recorded as something Priya "said", and it
plays once per user turn even if a pause splits the turn. In a real
conversation "Okay." before every single reply was tiresome, so it ships off.

**The tool filler plays when the tool call *starts*, not after it finishes.**
`llm.stream_reply` now yields `tool_start` on the first tool-call delta. On
booking turns the caller hears "One moment." ~1 s earlier than before. The
filler was shortened from "One moment, let me check." (2.0 s of audio) to
"One moment." (0.7 s) because the long one was still playing when the answer
arrived and the answer queued behind it — measured as a 550–650 ms delay on
`playback_start`.

**`SILENCE_MS` 500 → 450.** Swept 250–500 ms against 11 real speech samples with
the new stricter VAD: below 450 ms, natural phrase pauses split sentences into
2–3 turns. 450 is the honest floor. (300 ms fragmented the booking line into
three turns and was reverted.)

**LLM warm-up at startup.** The first HTTPS call pays ~2.5 s of TLS setup; it now
happens before the opening line, not on the caller's first turn.

**`Player.watch_next()`** replaced the single `on_start` callback so
`playback_start` marks when *the answer's* audio starts, even if a cached phrase
is still draining ahead of it. Without this the ack would have been reported as
the reply.

## Not done, and why

- **Splitting the first sentence at a comma** to start TTS earlier: saves
  200–400 ms on long first sentences, but each TTS flush is synthesized
  independently and the seam is audible. Audio quality was kept.
- **Trimming the system prompt**: ~80 ms for halving it, against real risk to
  date and fee accuracy. Not worth it.
- **Speculative early STT flush** before `SILENCE_MS` elapses: unknown server
  behaviour when audio continues after a flush. Not tested.
