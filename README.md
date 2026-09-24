# Nova Suraksha Voice Agent

A real-time voice agent for hospital appointment booking. You talk into your
microphone, it listens, thinks, and talks back through your speakers — with
interruption handling, tool calling and per-turn latency instrumentation.

Built **without any voice-agent framework** (no LiveKit, no Pipecat, no Vapi).
Every stage of the pipeline is written by hand in Python with `asyncio`, so
every step can be understood, measured and tuned.

```text
 You speak
    │
    ▼
┌───────┐   ┌─────┐   ┌─────┐   ┌─────┐   ┌─────┐   ┌─────────┐
│  Mic  │──▶│ VAD │──▶│ STT │──▶│ LLM │──▶│ TTS │──▶│ Speaker │
└───────┘   └─────┘   └─────┘   └─────┘   └─────┘   └─────────┘
  20 ms      "are     speech    decide     text      you hear
  frames     they     to text   what to    to audio  the reply
             talking?"          say, call
                                tools
```

---

## What it does

**Priya** is the appointment assistant for a sample hospital. She can:

- **Book, reschedule and cancel** appointments across 8 doctors in 7 departments
- **Explain** what a department treats and suggest the right one for a symptom
- **Answer** questions about timings, fees, the address and parking
- **Escalate emergencies** — "chest pain" writes an escalation record for the
  emergency desk and answers in ~0.8s, without ever reaching the language model
- **Refuse medical advice** — no diagnoses, no medicine names, no dosages

Every booking rule is enforced in Python, never trusted to the model: the 14-day
window, 30-minute minimum lead time, no Sunday, no lunch hour, children under 14
to Pediatrics, free follow-ups within 7 days, and a 2-hour cancellation cutoff.

### A real conversation

Taken verbatim from the database, trimmed for length:

```text
Priya  Hello, this is Priya from Nova Suraksha Hospital. How can I help you today?
You    Okay, I want to book an appointment.
Priya  Sure, I can help with that. Which department or doctor would you like to see?
You    I want general medicine
       [tool] check_slots(D01, 2026-09-21) -> 16 free slots
       [tool] check_slots(D02, 2026-09-21) -> 24 free slots
Priya  Doctor Ramesh Varma has slots tomorrow morning from nine AM to twelve
       forty five. Doctor Kavitha Reddy has the afternoon, two PM to eight PM.
       Which would you prefer?
You    Doctor Ramesh Varma, twelve fifteen
       ... name, age and phone collected, then read back ...
You    Yes
       [tool] book(Karthik, 22, 8885345499, D01, 2026-09-21, 12:15, new)
              -> {ok: True, booking_id: NS6855, fee: 500}
Priya  Your appointment is confirmed. Please come fifteen minutes early. Take care!
```

---

## Setup

### Requirements

- Python 3.10+
- A microphone and speakers (or headphones)
- A [Sarvam AI](https://www.sarvam.ai/) API key — one key covers speech-to-text,
  the language model and text-to-speech
- A MongoDB database — a free [Atlas](https://www.mongodb.com/atlas) cluster works

### Install

```bash
git clone https://github.com/NitinSukthe-G/voiceagent.git
cd voice-agent

python -m venv venv
venv\Scripts\activate          # macOS/Linux: source venv/bin/activate

pip install -r requirements.txt
```

### Configure

```bash
copy .env.example .env         # macOS/Linux: cp .env.example .env
```

Then edit `.env`:

```ini
SARVAM_API_KEY=your_sarvam_key_here
MONGODB_URL=mongodb+srv://user:password@cluster.mongodb.net/
```

### Seed the database and run

```bash
python seed.py       # once: loads the hospital, doctors and departments
python agent.py      # talk to it
```

On the first run the agent synthesizes a few fixed phrases (the greeting, the
emergency line) and stores them, so they play instantly from then on.

Press **Ctrl+C** to stop. The latency table for the session prints on exit.

> **Speakers vs headphones.** The agent detects which you are using at startup.
> There is no acoustic echo cancellation, so on speakers the microphone hears
> the agent — interrupting takes a firm voice, by design (see
> [How it works](#barge-in-and-speakers)). On headphones, interruption is
> instant and sensitive.

---

## How it works

### The pipeline

| Stage | Technology | Where it runs |
| --- | --- | --- |
| Microphone | `sounddevice` (PortAudio) | Local, 16 kHz mono, 20 ms frames |
| Voice activity detection | `webrtcvad` + an adaptive loudness gate | Local, no network |
| Speech to text | Sarvam `saaras:v3` (WebSocket) + REST fallback | Cloud, streaming |
| Language model | Sarvam `sarvam-105b-conversations` (OpenAI-compatible) | Cloud, streaming with tool calls |
| Text to speech | Sarvam `bulbul:v3`, voice `priya` (WebSocket) | Cloud, streaming |
| Speaker | `sounddevice` with an instantly clearable buffer | Local |

This is a **cascading pipeline** — separate models, one feeding the next. The
alternative, speech-to-speech, is faster but leaves no text in the middle to
inspect, and the text in the middle is exactly where the booking rules and tool
calls live.

### Streaming everywhere

The single biggest latency technique in the project. Speech is transcribed
*while* you talk; the model's tokens are consumed as they arrive; each finished
sentence goes to text-to-speech immediately, so Priya starts speaking sentence
one while sentence three is still being written. A sum of waits becomes an
overlap of waits.

### Barge-in and speakers

Interrupting the agent requires three things to happen at once: cancel the reply
task, detach the text-to-speech socket **synchronously** so no more audio
arrives, and clear the player buffer so queued audio stops. Miss any one and it
keeps talking over you.

It also distinguishes a real interruption from you simply pausing mid-sentence:
if Priya has not spoken yet, the partial transcript is kept and merged with what
you say next, so *"I want to book…"* [pause] *"…with the heart doctor"* is
understood as one sentence.

On speakers the microphone hears the agent. Measured at normal volume, the echo
peaks as loud as a real voice, so no simple threshold separates them — but echo
is **bursty** and speech is **sustained**. At RMS ≥ 800, echo never ran longer
than 160 ms, while real speech sustained 600–800 ms. So while Priya is talking,
an interruption must be loud for 300 ms straight.

### Short words

Sarvam's streaming speech-to-text returns nothing for clips under about a
second — "hi" and "ok" failed 24 times out of 24 in testing. The REST endpoint
transcribes every one of them in ~300 ms, so short turns go there directly, with
REST as a fallback for any turn the stream drops. Measured: 10 of 10 short words
now transcribed, in ~400 ms.

### Business rules in code, not in the prompt

The model never touches the database. It emits a structured tool call, Python
validates every rule and returns a result the model must relay. A model can be
talked into anything; `validate_slot()` cannot.

---

## Latency

Measured end to end, from the moment you stop speaking to the moment you hear
the first word of the reply.

| Moment | Typical | What it is |
| --- | --- | --- |
| ~0.8 s | Emergency response | Pre-synthesized, no API call at all |
| ~1.7 s | "One moment." | Plays the instant a tool call begins |
| 1.7–3.0 s | The answer | Best turns land at 1.65 s |

The floor is the sum of three cloud services, each measured directly:
**speech-to-text ~250 ms + language model ~950 ms + text-to-speech ~450 ms ≈
1.65 s**, plus a 450 ms endpointing wait. None of them can be made faster from
this side — there is one usable model on the API, no prompt caching, and the
text-to-speech is already at its fastest accepted settings.

So the agent covers the gap instead: fixed phrases are synthesized once and
stored as audio, the tool-call filler fires on the *first* streamed tool-call
chunk rather than after the round completes, and the language-model connection
is warmed at startup so the first turn does not pay a 2.5 s TLS handshake.

Full measurements, including what was tried and rejected, are in
[`latency.md`](latency.md).

---

## Project structure

```text
agent.py          the turn state machine, barge-in, the reply loop
config.py         every tunable number in one place
audio_io.py       Mic (frames into a queue), Player (instant stop)
vad.py            20 ms frames -> "start" / "end" events
stt.py            Sarvam speech-to-text: streaming + REST fallback
llm.py            Sarvam chat: token streaming, tool calls, sentence splitting
tts.py            Sarvam text-to-speech, with a completion latch
tools.py          the six booking functions; every rule lives here
prompt.py         Priya's prompt, the emergency desk's prompt, the emergency check
db.py             the MongoDB connection
transcript.py     writes every conversation to the database, live
latency.py        per-turn timing marks and the summary table
seed.py           loads the hospital, doctors and departments
```

### Data

All data lives in MongoDB — nothing is stored on disk.

| Collection | Contents |
| --- | --- |
| `hospital` | One document: name, address, hours, rule constants |
| `doctors` | One per doctor: department, days, hours, fee |
| `departments` | One per department: what it treats, in plain words |
| `bookings` | Every booking — the source of truth |
| `appointments` | One event per action: `BOOKED` / `RESCHEDULED` / `CANCELLED` |
| `conversations` | One per session: every line timestamped, plus per-turn latency |
| `emergencies` | One per escalation: what was said, when, and its status |
| `phrases` | Fixed lines pre-synthesized to audio |

Every conversation is recorded as it happens, so when something goes wrong you
can read exactly what was heard and said — including the tool calls, the
barge-ins and any transcription failures.

---

## Configuration

The numbers worth knowing, all in [`config.py`](config.py):

| Setting | Default | What it does |
| --- | --- | --- |
| `SILENCE_MS` | 450 | Silence before a turn is considered finished. Swept against real speech — below this, natural pauses split one sentence into several turns. |
| `START_MS` | 120 | Speech needed to start a turn. 200 ms missed "hi" and "ok" entirely. |
| `MIN_RMS` | 200 | Loudness floor. The gate also rises to 4× the rolling room noise. |
| `BARGE_RMS` / `BARGE_START_MS` | 800 / 300 | How loud and how sustained an interruption must be on speakers. |
| `HISTORY_MESSAGES` | 40 | Conversation memory. At 12, a booking lost the chosen doctor before the phone number arrived — voice turns are short. |
| `SPEAKERS` | auto | Force speaker or headphone mode instead of detecting it. |

---

## Documentation

- **[`notes.md`](notes.md)** — the full technical write-up: every file explained,
  one conversation traced end to end, the hard problems, ten real bugs with how
  each was diagnosed, and a glossary
- **[`latency.md`](latency.md)** — measurements, what was tried, where the floor is

---

## Known limitations

- **No acoustic echo cancellation.** On speakers, interrupting needs a firm
  voice. Headphones give instant, sensitive barge-in.
- **Voice activity detection cannot ignore a person talking nearby.** That is
  speech, and no VAD distinguishes it from you.
- **Speech recognition accuracy varies** with accent and speaking rate. Speaking
  a little slower helps noticeably.
- **The language model is the latency floor** at ~950 ms to first token. Sarvam
  currently offers one usable conversational model.

---

## Built as a learning project

The goal was to understand how a voice agent works from the inside — which is
why there is no framework, why every stage has its own latency mark, and why the
measurements in `latency.md` include the things that turned out *not* to help.
