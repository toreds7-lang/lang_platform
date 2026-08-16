import difflib
import json
import os
import re
from openai import OpenAI

_client = None

# Words handed to the LLM per request. Big enough to keep sentences intact,
# small enough that the model reliably re-emits every word verbatim.
WINDOW_WORDS = 350

# Ceiling on how long one word is assumed to take (~92 wpm). Stops a caption
# that lingers over silence from pushing a sentence's end time seconds late.
MAX_WORD_SECONDS = 0.8

# Fallback splitting (no LLM): break on a silent gap, or after a hard cap.
_GAP_SECONDS = 0.45
_GAP_MIN_WORDS = 8
_MAX_WORDS = 22

_NORM_RE = re.compile(r"[^a-z0-9']")
# lowercase standalone "i", optionally with a contraction suffix -> "I"
_I_RE = re.compile(r"(?<![A-Za-z0-9'’])i(?=(?:['’](?:m|ve|ll|d|s))?(?![A-Za-z0-9'’]))")


def _get_client():
    global _client
    if _client is None:
        _client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    return _client


def _build_words(chunks: list[dict]) -> list[dict]:
    """Flatten caption chunks into a word timeline with non-overlapping timings.

    YouTube's auto-captions use a rolling display, so a chunk's
    start + duration usually runs past the next chunk's start. Clamping to the
    next chunk keeps every word's end time honest, which is what auto-pause
    needs.
    """
    words = []
    for ci, chunk in enumerate(chunks):
        tokens = chunk["text"].split()
        if not tokens:
            continue
        start = chunk["start"]
        end = start + chunk.get("duration", 0.0)
        if ci + 1 < len(chunks):
            end = min(end, chunks[ci + 1]["start"])
        if end <= start:
            end = start + 0.3 * len(tokens)
        step = min((end - start) / len(tokens), MAX_WORD_SECONDS)
        for wi, token in enumerate(tokens):
            words.append(
                {
                    "text": token,
                    "chunk": chunk["index"],
                    "start": round(start + wi * step, 3),
                    "end": round(start + (wi + 1) * step, 3),
                }
            )
    return words


def _norm(token: str) -> str:
    return _NORM_RE.sub("", token.lower().replace("’", "'"))


def _finalize_text(text: str) -> str:
    text = " ".join(text.split())
    if not text:
        return text
    text = _I_RE.sub("I", text)
    for i, ch in enumerate(text):
        if ch.isalpha():
            text = text[:i] + ch.upper() + text[i + 1 :]
            break
    if text[-1].isalnum():
        text += "."
    return text


def _punctuate_window(tokens: list[str]) -> list[str] | None:
    """Ask the LLM to punctuate + capitalize the words, one sentence per item."""
    model = os.environ.get("LLM_MODEL", "gpt-4o-mini")
    prompt = (
        "Below is a stretch of an English video transcript that was auto-generated "
        "with no punctuation and no capitalization. Restore it.\n\n"
        "Rules:\n"
        "- Keep every word exactly as given, in the same order. Do not add, delete, replace, "
        'reorder or "correct" any word. Keep fillers such as um, uh, gonna, kinda as they are.\n'
        "- You may ONLY add punctuation, change letter case, and decide where each sentence "
        "starts and ends.\n"
        '- Capitalize the first word of every sentence, proper nouns, and the pronoun "I" '
        "(including I'm, I'll, I've, I'd).\n"
        "- Split the text into complete, natural sentences: exactly one sentence per array item. "
        "Separate speaker turns and short exclamations are their own sentences.\n"
        "- Keep bracketed sound cues such as [Music] or [Applause] as their own item.\n\n"
        'Respond with strict JSON only: {"sentences": ["...", "..."]}\n\n'
        "Words:\n" + " ".join(tokens)
    )
    try:
        resp = _get_client().chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0,
        )
        parsed = json.loads(resp.choices[0].message.content)
        sentences = parsed.get("sentences")
        if not isinstance(sentences, list):
            return None
        sentences = [s.strip() for s in sentences if isinstance(s, str) and s.strip()]
        return sentences or None
    except Exception:
        return None


def _align(sentences: list[str], window: list[dict]) -> list[tuple[int, int, str]] | None:
    """Map each LLM sentence back onto the source word indices it covers.

    Returns (local_start, local_end, text) spans that contiguously cover the
    whole window, or None if the model strayed too far from the source words.
    """
    src = [_norm(w["text"]) for w in window]
    out, owner = [], []
    for si, sentence in enumerate(sentences):
        for token in sentence.split():
            normalized = _norm(token)
            if normalized:
                out.append(normalized)
                owner.append(si)
    if not out:
        return None

    matcher = difflib.SequenceMatcher(a=src, b=out, autojunk=False)
    if matcher.ratio() < 0.7:
        return None

    out_to_src: list[int | None] = [None] * len(out)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for k in range(j2 - j1):
                out_to_src[j1 + k] = i1 + k
        elif tag == "replace":
            span = j2 - j1
            for k in range(j1, j2):
                frac = (k - j1) / span
                out_to_src[k] = min(i2 - 1, i1 + int(frac * (i2 - i1)))
        elif tag == "insert":
            for k in range(j1, j2):
                out_to_src[k] = min(i1, len(src) - 1)

    # Collect the highest source index each sentence reaches.
    reach: dict[int, int] = {}
    for k, si in enumerate(owner):
        if out_to_src[k] is not None:
            reach[si] = max(reach.get(si, -1), out_to_src[k])

    spans: list[tuple[int, int, str]] = []
    cursor = 0
    for si, sentence in enumerate(sentences):
        text = _finalize_text(sentence)
        if not text:
            continue
        if cursor > len(src) - 1:
            # Model produced more sentences than we have words for; fold the
            # leftover text into the previous span rather than dropping it.
            if spans:
                a, b, prev = spans[-1]
                spans[-1] = (a, b, _finalize_text(prev.rstrip(".") + " " + text))
            continue
        end = max(reach.get(si, cursor), cursor)
        spans.append((cursor, min(end, len(src) - 1), text))
        cursor = min(end, len(src) - 1) + 1

    if not spans:
        return None
    a, b, text = spans[-1]
    spans[-1] = (a, len(src) - 1, text)
    return spans


def _fallback_window(window: list[dict]) -> list[tuple[int, int, str]]:
    """No-LLM splitting: break on silent gaps, with a hard length cap."""
    spans = []
    start = 0
    for i, word in enumerate(window):
        length = i - start + 1
        last = i == len(window) - 1
        gap = window[i + 1]["start"] - word["end"] if not last else 0.0
        if last or (length >= _GAP_MIN_WORDS and gap >= _GAP_SECONDS) or length >= _MAX_WORDS:
            text = _finalize_text(" ".join(w["text"] for w in window[start : i + 1]))
            spans.append((start, i, text))
            start = i + 1
    return spans


def segment_into_sentences(chunks: list[dict]) -> list[dict]:
    """Turn raw caption chunks into punctuated, capitalized sentences.

    Segmentation happens at word level, so a sentence boundary that falls in
    the middle of a caption chunk is honoured, and each sentence carries a
    tight end time taken from its last word.
    """
    words = _build_words(chunks)
    if not words:
        return []

    spans: list[tuple[int, int, str]] = []
    offset = 0
    while offset < len(words):
        window = words[offset : offset + WINDOW_WORDS]
        is_last = offset + len(window) >= len(words)

        mapped = None
        sentences = _punctuate_window([w["text"] for w in window])
        if sentences:
            mapped = _align(sentences, window)
        if not mapped:
            mapped = _fallback_window(window)

        # The final sentence of a non-final window is probably cut off, so
        # leave its words for the next pass.
        if not is_last and len(mapped) > 1:
            mapped = mapped[:-1]

        for local_start, local_end, text in mapped:
            spans.append((offset + local_start, offset + local_end, text))
        offset += mapped[-1][1] + 1

    sentences_out = []
    for i, (start, end, text) in enumerate(spans):
        sentences_out.append(
            {
                "index": i,
                "text": text,
                "start": words[start]["start"],
                "end": words[end]["end"],
                "chunk_range": [words[start]["chunk"], words[end]["chunk"]],
                "word_range": [start, end],
            }
        )
    return sentences_out
