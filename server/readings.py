"""Pronunciation for words: pinyin, furigana, or whatever a language needs.

This is a pass of its own rather than part of segmentation, because measurement
said so. Asked to punctuate, split into sentences, group words AND give a
reading in one call, gpt-4o-mini produced readings for only 0-48% of sentences
on a real Chinese transcript, varying wildly between identical runs at
temperature 0; demanding a reading for every word made it abandon a full window
and return a single sentence, which then failed alignment and dropped the whole
window to gap-splitting. Word grouping alone it does reliably every time.

Two design points, both of which came out of that measurement:

* The answer is a MAP keyed by the word, never a positional array. Every
  positional form drifted -- an array of readings came back with 116, 121, 122
  or 126 entries for 120 words, in 4 or 5 batches out of 6 -- and one dropped
  entry silently shifts every reading after it onto the wrong word. Keyed by
  word, a missing entry costs exactly that word and nothing else. Measured at
  1073/1073 words covered, on two consecutive runs.

* The table is shared per language, not per video, because it is keyed by word
  and nothing in it depends on which video a word appeared in. Common words are
  bought once. It also survives re-segmentation, unlike anything keyed by
  sentence index.
"""

import concurrent.futures
import json
import os
import re
import threading
from openai import OpenAI

import cache

_client = None

# Sound cues such as [音楽] or [Applause], which the segmenter keeps whole.
_CUE_RE = re.compile(r"^\[.*\]$")

# Unique words per request. Small enough that the model answers the whole batch,
# large enough that a video is a handful of calls.
BATCH_WORDS = 150

# Batches are independent, so they run at once -- the same shape the recommender
# uses. Without this a first video would sit for ~45s instead of ~15s.
MAX_WORKERS = 4

# One growing vocabulary file per language, outside the per-video dirs.
_STORE = "_readings"

# Guards the read-modify-write of a language's table. Batches finish on several
# threads at once and all merge into the same file.
_lock = threading.Lock()


class ReadingError(Exception):
    """The readings pass could not run at all."""


def _get_client():
    global _client
    if _client is None:
        _client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    return _client


def _path(lang: str):
    directory = cache.CACHE_ROOT / _STORE
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{lang}.json"


def load(lang: str) -> dict:
    path = _path(lang)
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            table = json.load(f)
        return table if isinstance(table, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _merge(lang: str, new: dict) -> dict:
    """Fold new readings into the language's table and return the whole thing."""
    with _lock:
        table = load(lang)
        table.update(new)
        path = _path(lang)
        tmp = path.with_suffix(".part")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(table, f, ensure_ascii=False, indent=0, sort_keys=True)
        tmp.replace(path)
        return table


def needs_reading(word: str) -> bool:
    """Whether a word could have a reading at all.

    Pure ASCII is a number or a Latin name -- "Mac", "CPU", "M4" -- which has no
    reading in any of these languages. Bracketed sound cues such as [音楽] and
    [拍手] are not speech either; the segmenter keeps them as their own
    sentences, and they were the only things the model ever declined to read.
    """
    word = word.strip()
    if not word or word.isascii():
        return False
    return not _CUE_RE.match(word)


def _ask(words: list[str], prof: dict) -> dict:
    prompt = (
        f"Below is a JSON array of {prof['name']} words taken from a video transcript.\n\n"
        f"Give the {prof['reading']} for each one, as it is pronounced in {prof['name']}. "
        "Where a character has more than one possible reading, choose the one that fits "
        "the word it appears in.\n\n"
        "Rules:\n"
        "- Write each word's reading as one token, with no spaces inside it.\n"
        "- Include an entry for EVERY word given.\n"
        "- If a word genuinely has no reading, map it to an empty string.\n\n"
        "Respond with strict JSON only, an object mapping each word to its reading:\n"
        '{"readings": {"<word>": "<reading>", "<word>": "<reading>"}}\n\n'
        f"Words:\n{json.dumps(words, ensure_ascii=False)}"
    )
    model = os.environ.get("LLM_MODEL", "gpt-4o-mini")
    try:
        resp = _get_client().chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0,
        )
        parsed = json.loads(resp.choices[0].message.content)
    except Exception:
        # A failed batch costs those words their readings and nothing more; the
        # next video that meets them will try again.
        return {}

    table = parsed.get("readings") if isinstance(parsed, dict) else None
    if not isinstance(table, dict):
        return {}

    wanted = set(words)
    return {
        word: str(reading).strip()
        for word, reading in table.items()
        if word in wanted and str(reading).strip()
    }


def ensure(words, prof: dict) -> dict:
    """The reading table for these words, fetching whatever is missing.

    Returns the language's whole table, so callers can look up any word in it.
    """
    lang = prof["code"]
    if not prof["reading"]:
        return {}

    table = load(lang)
    missing = list(dict.fromkeys(w for w in words if needs_reading(w) and w not in table))
    if not missing:
        return table

    if not os.environ.get("OPENAI_API_KEY"):
        raise ReadingError("OPENAI_API_KEY is not set. Add it to the .env file.")

    batches = [missing[i : i + BATCH_WORDS] for i in range(0, len(missing), BATCH_WORDS)]
    workers = min(MAX_WORKERS, len(batches))
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(lambda b: _ask(b, prof), batches))

    fetched = {}
    for result in results:
        fetched.update(result)

    # Words the model returned nothing for are recorded as empty rather than
    # left absent, so a word it cannot read is not re-requested for every video
    # from now on.
    for word in missing:
        fetched.setdefault(word, "")

    return _merge(lang, fetched)


def apply(sentences: list[dict], prof: dict) -> bool:
    """Attach readings to every word of every sentence, in place.

    Returns whether anything changed, so the caller knows to write the
    transcript back. Running this on every read, not only on a fresh
    segmentation, is what lets a transcript cached before this existed fill in
    its missing readings without being re-segmented -- which would cost the
    segmentation calls again and take that video's bookmarks with it.

    A reading equal to its own word is dropped: it carries no information and
    only doubles the line height. Real transcripts return あの for あの.
    """
    if not prof["reading"]:
        return False

    words = [w["w"] for s in sentences for w in (s.get("words") or [])]
    if not words:
        return False

    table = ensure(words, prof)
    changed = False
    for sentence in sentences:
        for word in sentence.get("words") or []:
            reading = table.get(word["w"], "")
            wanted = reading if reading and reading != word["w"] else None
            if wanted != word.get("r"):
                changed = True
                if wanted:
                    word["r"] = wanted
                else:
                    word.pop("r", None)
    return changed
