import os
from dotenv import load_dotenv

# read the .env file into environment variables
load_dotenv()
API_KEY = os.getenv("SARVAM_API_KEY")
MONGODB_URL = os.getenv("MONGODB_URL")
MONGODB_DB = "voice_agent"

# ---- audio: ONE format for the whole pipeline ----
RATE = 16000          # samples per second (16 kHz)
FRAME_MS = 20         # we handle audio in 20 ms pieces
FRAME = RATE * FRAME_MS // 1000   # 320 samples per frame
BYTES_PER_FRAME = FRAME * 2       # int16 = 2 bytes per sample

# ---- VAD (turn detection) ----
# webrtcvad below mode 3 calls most quiet-room noise "speech", so a frame must
# ALSO be louder than MIN_RMS (int16 RMS; quiet room ~70-120, speech ~2000).
VAD_MODE = 3          # 0 = relaxed ... 3 = very strict
MIN_RMS = 200         # quieter than this is never speech
# ...and the gate rises with the room: it is at least FLOOR_RATIO times the
# rolling noise floor (the quietest fifth of the last FLOOR_FRAMES frames)
FLOOR_FRAMES = 150    # 3 s
FLOOR_RATIO = 4
START_MS = 120        # this much speech = user started. 200 missed short words
                      # like hi and ok; a keystroke is still under 6 frames.
SILENCE_MS = 700      # this much silence = user finished. 450 was tuned
                      # against synthesized speech and cut real speakers off
                      # mid-sentence; people pause longer when thinking.
                      # Costs ~250 ms per turn. Lower it if replies feel slow.
PREROLL_MS = 300      # audio kept from just before speech

# ---- speakers (no headphones) ----
# The mic hears Priya through the speakers. Echo peaks are as loud as speech
# but short; a real interruption is loud AND sustained. While Priya talks,
# a barge-in must clear BARGE_RMS for BARGE_START_MS in a row.
SPEAKERS = None       # None = detect from the output device name
BARGE_RMS = 1600     # measured: echo p90 is ~1500 at a normal volume;
                     # 800 let one false barge-in through. Lower it toward
                     # 1200 if interrupting takes too much effort.
BARGE_START_MS = 300
ECHO_TAIL_MS = 400    # echo keeps arriving this long after our buffer empties

# ---- Sarvam models ----
STT_MODEL = "saaras:v3"
STT_LANG = "en-IN"          # "unknown" = auto detect
STT_MODE = "transcribe"     # "codemix" for Telugu + English
# Streaming STT drops very short clips (hi, ok: 0 of 24 tries). The REST
# endpoint gets them every time in ~300 ms, so it is the fallback whenever
# the stream returns nothing for a turn that had audio.
STT_REST_MODEL = "saarika:v2.5"
STT_SHORT_TURN_S = 1.0      # turns shorter than this wait less for the stream
LLM_MODEL = "sarvam-105b-conversations"
TTS_MODEL = "bulbul:v3"
TTS_VOICE = "priya"
# The emergency desk answers in a different voice, so the caller can hear
# that the call was handed to someone else.
DESK_VOICE = "aditya"
TTS_LANG = "en-IN"

# ---- conversation ----
# Voice turns are short ("Yeah", "Ages 22"), so 12 messages was only 6 exchanges
# and the chosen doctor and time fell out of memory before the phone number
# arrived. Only spoken text is kept (not tool calls), so 40 is ~1000 tokens.
HISTORY_MESSAGES = 40

# ---- instant phrases ----
# Synthesized once and kept in the database, then played straight from memory with
# no API round trip. ACK would play the moment the transcript lands (~0.7 s),
# covering the LLM wait - but a word before every reply gets tiresome, so it
# is off. Set it to e.g. "Okay." to try it.
ACK = ""
FILLER = "One moment."   # short: it must finish before the answer arrives
