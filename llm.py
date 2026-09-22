import re
from openai import AsyncOpenAI
import config

# Sarvam's chat API speaks the OpenAI format,
# so the openai library works as the client.
client = AsyncOpenAI(
    api_key=config.API_KEY or "missing",
    base_url="https://api.sarvam.ai/v1",
    timeout=15,
    max_retries=1,
)


async def stream_reply(messages, tools=None):
    """Yields ("text", piece) while streaming, ("tool_start", None) the
    moment the model begins a tool call (so a filler can play while the
    arguments stream in), and at the end ("tools", calls)."""
    extra = {"tools": tools} if tools else {}
    stream = await client.chat.completions.create(
        model=config.LLM_MODEL,
        messages=messages,
        stream=True,
        temperature=0.3,
        max_tokens=300,
        **extra,
    )

    calls = {}
    async for chunk in stream:
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta

        if delta.content:
            yield ("text", delta.content)

        if delta.tool_calls and not calls:
            yield ("tool_start", None)
        for tc in delta.tool_calls or []:
            i = tc.index if tc.index is not None else 0
            c = calls.setdefault(i, {"id": "", "name": "", "args": ""})
            if tc.id:
                c["id"] = tc.id
            if tc.function and tc.function.name and not c["name"]:
                c["name"] = tc.function.name
            if tc.function and tc.function.arguments:
                c["args"] += tc.function.arguments

    for i, c in calls.items():
        if not c["id"]:
            c["id"] = f"call_{i}"
    if calls:
        yield ("tools", [calls[i] for i in sorted(calls)])


ABBREV = {"dr", "mr", "mrs", "ms", "no"}


def tidy(text):
    """The model sometimes drops the space between tokens ("KavithaReddy",
    "rupees.Doctor"). TTS then mispronounces the name and the sentence
    splitter misses the boundary, so restore the space."""
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)
    return re.sub(r"([.?!])([A-Z])", r"\1 \2", text)


def split_sentences(buf):
    """Cut finished sentences off the front of buf.
    Returns (sentences, leftover)."""
    out = []
    start = 0
    for m in re.finditer(r"[.?!]\s", buf):
        end = m.end()
        words = buf[start:m.start()].split()
        last = words[-1].lower() if words else ""
        if last in ABBREV:
            continue
        piece = buf[start:end].strip()
        if piece:
            out.append(piece)
        start = end
    return out, buf[start:]
