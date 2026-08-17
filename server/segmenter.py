import difflib
import json
import os
import re
import unicodedata
from openai import OpenAI

import languages

_client = None

# Fallback splitting (no LLM): the silent gap that may end a sentence.
_GAP_SECONDS = 0.45

# Anything that isn't a letter, digit or apostrophe. \w under re.UNICODE already
# covers CJK ideographs, kana and Vietnamese diacritics, so one expression
# serves every language. For lowercased ASCII this matches the old
# English-only [^a-z0-9'] exactly, apart from keeping accented letters that the
# old form silently dropped.
_NORM_RE = re.compile(r"[^\w']", re.UNICODE)

# Lowercase standalone "i", optionally with a contraction suffix -> "I".
# [^\W_] rather than [A-Za-z0-9]: the ASCII form treats every accented letter as
# a word boundary, so in Vietnamese "đi" the "i" looked standalone and came out
# as "đI". Applied only where a language actually has this pronoun, but the
# class is Unicode-aware anyway so it cannot misfire on a loanword either.
_I_RE = re.compile(r"(?<![^\W_]|['’])i(?=(?:['’](?:m|ve|ll|d|s))?(?![^\W_]|['’]))")


def _get_client():
    global _client
    if _client is None:
        _client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    return _client


_units_of = languages.units_of


def _build_units(chunks: list[dict], prof: dict) -> list[dict]:
    """Flatten caption chunks into a unit timeline with non-overlapping timings.

    YouTube's auto-captions use a rolling display, so a chunk's
    start + duration usually runs past the next chunk's start. Clamping to the
    next chunk keeps every unit's end time honest, which is what auto-pause
    needs.
    """
    units = []
    for ci, chunk in enumerate(chunks):
        tokens = _units_of(chunk["text"], prof)
        if not tokens:
            continue
        start = chunk["start"]
        end = start + chunk.get("duration", 0.0)
        if ci + 1 < len(chunks):
            end = min(end, chunks[ci + 1]["start"])
        if end <= start:
            end = start + prof["fallback_secs"] * len(tokens)
        step = min((end - start) / len(tokens), prof["max_secs"])
        for wi, token in enumerate(tokens):
            units.append(
                {
                    "text": token,
                    "chunk": chunk["index"],
                    "start": round(start + wi * step, 3),
                    "end": round(start + (wi + 1) * step, 3),
                }
            )
    return units


def _norm(token: str) -> str:
    return _NORM_RE.sub("", unicodedata.normalize("NFKC", token).lower().replace("’", "'"))


def _bare(text: str) -> str:
    """Normalized text with separators dropped, for equality checks only."""
    return _norm(text).replace("'", "")


def _finalize_text(text: str, prof: dict) -> str:
    text = " ".join(text.split())
    if not text:
        return text
    if prof["capitalize"]:
        if prof.get("pronoun_i"):
            text = _I_RE.sub("I", text)
        for i, ch in enumerate(text):
            if ch.isalpha():
                text = text[:i] + ch.upper() + text[i + 1 :]
                break
    if text[-1].isalnum():
        text += prof["sentence_end"]
    return text


# ---- prompt ---------------------------------------------------------------


def _article(name: str) -> str:
    return "an" if name[:1].upper() in "AEIOU" else "a"


def _response_format(prof: dict) -> str:
    """The JSON shape asked for, which depends on what the language needs.

    Languages whose words are just whitespace tokens keep the original plain
    string form, so their prompt is unchanged from the English-only version.
    """
    if prof["word_grouping"] != "llm":
        return 'Respond with strict JSON only: {"sentences": ["...", "..."]}'

    # Deliberately no readings here, whatever the language needs. This call is
    # already doing punctuation, sentence splitting and word grouping, and asking
    # it for pronunciation as well measurably breaks it: on a real Chinese
    # transcript only 0-48% of sentences came back with readings, varying wildly
    # between identical runs, and demanding a reading per word made the model
    # give up on a full window entirely and return a single sentence -- which
    # then failed alignment and dropped the whole window to gap-splitting.
    # Word grouping alone it does reliably. Readings are built separately, by
    # readings.py, where they can be keyed by word instead of by position.
    return "\n".join(
        [
            'Respond with strict JSON only: {"sentences": [{"t": "...", "w": ["...", "..."]}]}',
            '- "t" is the sentence text, exactly as it should be displayed to a reader.',
            '- "w" is that same sentence split into meaningful words, in order. Joined back '
            'together, ignoring spaces and punctuation, the words must reproduce "t" exactly.',
        ]
    )


def _build_prompt(tokens: list[str], prof: dict) -> str:
    """The punctuation prompt for this language.

    Assembled from the profile rather than written per language. For English
    the rendered string is byte-identical to the original hand-written literal,
    which is what the golden-prompt test pins down.
    """
    unit = "word" if prof["timing_unit"] == "word" else "character"

    head = (
        f"Below is a stretch of {_article(prof['name'])} {prof['name']} video transcript "
        "that was auto-generated with no punctuation"
    )
    head += " and no capitalization." if prof["capitalize"] else "."
    head += " Restore it."

    keep = (
        f"- Keep every {unit} exactly as given, in the same order. Do not add, delete, "
        f'replace, reorder or "correct" any {unit}.'
    )
    if prof["timing_unit"] == "word":
        keep += " Keep fillers such as um, uh, gonna, kinda as they are."

    allowed = ["add punctuation"]
    if prof["capitalize"]:
        allowed.append("change letter case")
    if len(allowed) == 1:
        may = f"- You may ONLY {allowed[0]} and decide where each sentence starts and ends."
    else:
        may = (
            "- You may ONLY "
            + ", ".join(allowed)
            + ", and decide where each sentence starts and ends."
        )

    rules = [keep, may]
    if prof["capitalize"]:
        # Same flag drives the prompt clause and the local fixup, so the two can
        # never disagree about whether this language has an "I" to capitalize.
        note = (
            ", and the pronoun \"I\" (including I'm, I'll, I've, I'd)"
            if prof.get("pronoun_i")
            else ""
        )
        rules.append(
            "- Capitalize the first word of every sentence, proper nouns" + note + "."
        )
    rules.append(
        "- Split the text into complete, natural sentences: exactly one sentence per array "
        "item. Separate speaker turns and short exclamations are their own sentences."
    )
    rules.append("- Keep bracketed sound cues such as [Music] or [Applause] as their own item.")
    if prof["word_grouping"] == "llm":
        grouping = '- Group the text into meaningful words for "w".'
        if prof.get("word_note"):
            grouping += " " + prof["word_note"]
        rules.append(grouping)

    if prof["timing_unit"] == "word":
        label, body = "Words", " ".join(tokens)
    else:
        label, body = "Text", "".join(tokens)

    return (
        head
        + "\n\nRules:\n"
        + "\n".join(rules)
        + "\n\n"
        + _response_format(prof)
        + f"\n\n{label}:\n"
        + body
    )


def _parse_sentences(parsed: dict, prof: dict) -> list[dict] | None:
    """Normalize both response shapes into [{t, w, r}].

    Plain strings (the whitespace-grouping form) and objects are both accepted,
    whatever the profile asked for, so a model that answers in the other shape
    still produces something usable instead of falling back to gap splitting.
    """
    raw = parsed.get("sentences")
    if not isinstance(raw, list):
        return None

    items = []
    for entry in raw:
        if isinstance(entry, str):
            text = entry.strip()
            if text:
                items.append({"t": text, "w": None, "r": None})
            continue
        if not isinstance(entry, dict):
            continue
        text = str(entry.get("t", "")).strip()
        if not text:
            continue
        words, readings = _parse_words(entry)
        items.append({"t": text, "w": words, "r": readings})

    return items or None


def _parse_words(entry: dict) -> tuple[list[str] | None, list[str] | None]:
    """Pull the word list out of a sentence, in whichever shape it arrived.

    Pairs are what the prompt now asks for, because they cannot fall out of
    step. The parallel-array form is still accepted: models drift back to it,
    and half-understanding a response is worse than reading both shapes.
    """
    raw = entry.get("w")
    if not isinstance(raw, list):
        return None, None

    words: list[str] = []
    readings: list[str] = []
    paired = False

    for item in raw:
        if isinstance(item, (list, tuple)):
            paired = True
            word = str(item[0]).strip() if item else ""
            reading = str(item[1]).strip() if len(item) > 1 else ""
        else:
            word, reading = str(item).strip(), ""
        if not word:
            continue
        words.append(word)
        readings.append(reading)

    if not words:
        return None, None
    if paired:
        return words, readings

    # Flat list: any readings live in a separate array of their own.
    separate = entry.get("r")
    separate = [str(r) for r in separate] if isinstance(separate, list) else None
    return words, separate


def _punctuate_window(tokens: list[str], prof: dict) -> list[dict] | None:
    """Ask the LLM to punctuate the units, one sentence per item."""
    model = os.environ.get("LLM_MODEL", "gpt-4o-mini")
    try:
        resp = _get_client().chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": _build_prompt(tokens, prof)}],
            response_format={"type": "json_object"},
            temperature=0,
        )
        return _parse_sentences(json.loads(resp.choices[0].message.content), prof)
    except Exception:
        return None


# ---- words ----------------------------------------------------------------


def _useful_reading(word: str, reading) -> bool:
    reading = str(reading).strip()
    return bool(reading) and _bare(reading) != _bare(word)


def _align_readings(words: list[str], readings) -> list[str] | None:
    """Line a separate readings array up with the words, or give up.

    Only reachable when a model answers with two parallel arrays instead of the
    pairs the prompt asks for. The usual way they disagree is that entries for
    words needing no reading -- "Mac", "CPU", "5098" -- were left out rather
    than sent as empty strings, so a run of readings covers exactly the
    non-ASCII words. Recovering that is much better than the alternative of
    dropping every reading in the sentence, which is what used to happen.
    """
    if not readings:
        return None
    if len(readings) == len(words):
        return [str(r) for r in readings]

    needy = [i for i, w in enumerate(words) if not w.isascii()]
    if len(readings) == len(needy):
        out = [""] * len(words)
        for slot, reading in zip(needy, readings):
            out[slot] = str(reading)
        return out

    # Any other mismatch cannot be positioned safely, and a reading printed over
    # the wrong word teaches the wrong pronunciation.
    return None


def _words_for(item: dict, prof: dict) -> list[dict] | None:
    """The clickable word list for one sentence, or None to let the client split.

    Returning None for whitespace-grouped languages is deliberate: the client
    already tokenizes those correctly, and not emitting a list keeps English
    rendering on exactly the path it is on today.
    """
    if prof["word_grouping"] != "llm":
        return None

    words = item.get("w")
    if words and _bare("".join(words)) == _bare(item["t"]):
        readings = _align_readings(words, item.get("r"))
        if readings:
            # A reading is dropped when it is empty, or when it just repeats the
            # word. Both are pure noise: they double the line height and teach
            # nothing. Asking the model for kanji-only furigana is not enough on
            # its own -- on real transcripts it returns あの for あの and が for
            # が anyway -- and the identity test needs no per-language rule,
            # since a reading only ever equals its word when the word was
            # already written phonetically.
            return [
                {"w": w, "r": readings[i]}
                if _useful_reading(w, readings[i])
                else {"w": w}
                for i, w in enumerate(words)
            ]
        return [{"w": w} for w in words]

    # The model's word list didn't reproduce the sentence, so it can't be
    # trusted to line up with a reading either. Fall back to one unit per word:
    # useless as segmentation, but it keeps every character clickable rather
    # than collapsing the whole sentence into a single unclickable token.
    return [{"w": tok} for tok in _units_of(item["t"], prof)]


def _merge_words(a: list[dict] | None, b: list[dict] | None) -> list[dict] | None:
    if a is None and b is None:
        return None
    return (a or []) + (b or [])


# ---- alignment ------------------------------------------------------------


def _align(items: list[dict], window: list[dict], prof: dict) -> list[dict] | None:
    """Map each LLM sentence back onto the source unit indices it covers.

    Returns spans that contiguously cover the whole window, or None if the
    model strayed too far from the source units.
    """
    # Units that normalize away -- punctuation, mostly -- are kept out of the
    # matched sequence and mapped back afterwards. Leaving them in would make
    # every one a guaranteed non-match: harmless in English, where they are
    # rare, but Chinese captions are full of ，and 。, and enough of them drag
    # ratio() under the bail-out so the window would always fall back.
    src, src_idx = [], []
    for i, unit in enumerate(window):
        normalized = _norm(unit["text"])
        if normalized:
            src.append(normalized)
            src_idx.append(i)
    if not src:
        return None

    out, owner = [], []
    for si, item in enumerate(items):
        for token in _units_of(item["t"], prof):
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

    # The furthest real unit each sentence reaches, translated out of matched
    # positions and back into window indices.
    reach: dict[int, int] = {}
    for k, si in enumerate(owner):
        if out_to_src[k] is not None:
            reach[si] = max(reach.get(si, -1), src_idx[out_to_src[k]])

    last = len(window) - 1
    spans: list[dict] = []
    cursor = 0
    for si, item in enumerate(items):
        text = _finalize_text(item["t"], prof)
        if not text:
            continue
        words = _words_for(item, prof)
        if cursor > last:
            # Model produced more sentences than we have units for; fold the
            # leftover text into the previous span rather than dropping it.
            if spans:
                prev = spans[-1]
                joiner = " " if prof["timing_unit"] == "word" else ""
                prev["text"] = _finalize_text(
                    prev["text"].rstrip(prof["sentence_end"]) + joiner + text, prof
                )
                prev["words"] = _merge_words(prev["words"], words)
            continue
        end = min(max(reach.get(si, cursor), cursor), last)
        spans.append({"start": cursor, "end": end, "text": text, "words": words})
        cursor = end + 1

    if not spans:
        return None
    spans[-1]["end"] = last
    return spans


def _fallback_window(window: list[dict], prof: dict) -> list[dict]:
    """No-LLM splitting: break on silent gaps, with a hard length cap."""
    spans = []
    start = 0
    for i, unit in enumerate(window):
        length = i - start + 1
        last = i == len(window) - 1
        gap = window[i + 1]["start"] - unit["end"] if not last else 0.0
        if (
            last
            or (length >= prof["min_gap_units"] and gap >= _GAP_SECONDS)
            or length >= prof["max_units"]
        ):
            tokens = [u["text"] for u in window[start : i + 1]]
            joiner = " " if prof["timing_unit"] == "word" else ""
            text = _finalize_text(joiner.join(tokens), prof)
            # Without the LLM there is no grouping and no reading, but a
            # character language still needs per-character spans to stay
            # clickable, so the units stand in as words.
            words = None if prof["word_grouping"] != "llm" else [{"w": t} for t in tokens]
            spans.append({"start": start, "end": i, "text": text, "words": words})
            start = i + 1
    return spans


def segment_into_sentences(chunks: list[dict], prof: dict) -> list[dict]:
    """Turn raw caption chunks into punctuated sentences.

    Segmentation happens at unit level, so a sentence boundary that falls in
    the middle of a caption chunk is honoured, and each sentence carries a
    tight end time taken from its last unit.
    """
    units = _build_units(chunks, prof)
    if not units:
        return []

    spans: list[dict] = []
    offset = 0
    while offset < len(units):
        window = units[offset : offset + prof["window"]]
        is_last = offset + len(window) >= len(units)

        mapped = None
        items = _punctuate_window([u["text"] for u in window], prof)
        if items:
            mapped = _align(items, window, prof)
        if not mapped:
            mapped = _fallback_window(window, prof)

        # The final sentence of a non-final window is probably cut off, so
        # leave its units for the next pass.
        if not is_last and len(mapped) > 1:
            mapped = mapped[:-1]

        for span in mapped:
            spans.append({**span, "start": offset + span["start"], "end": offset + span["end"]})
        offset += mapped[-1]["end"] + 1

    sentences_out = []
    for i, span in enumerate(spans):
        start, end = span["start"], span["end"]
        sentence = {
            "index": i,
            "text": span["text"],
            "start": units[start]["start"],
            "end": units[end]["end"],
            "chunk_range": [units[start]["chunk"], units[end]["chunk"]],
            "unit_range": [start, end],
        }
        if span["words"]:
            sentence["words"] = span["words"]
        sentences_out.append(sentence)
    return sentences_out
