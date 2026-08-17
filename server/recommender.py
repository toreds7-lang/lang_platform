import concurrent.futures
import json
import os
import re
from openai import OpenAI

import languages

_client = None


class RecommendError(Exception):
    """The scoring pass could not be run at all (no API key, no usable sentences)."""


# Sentences handed to the LLM per request. The observed transcripts average
# ~7 words a sentence, so 50 is a small request that still gives the model
# enough of the video to judge what counts as a main point.
BATCH_SENTENCES = 50

# Neighbouring sentences shown with each batch as read-only context. A line
# like "That's the whole problem." only earns a high score once you can see
# what it points back to.
CONTEXT_SENTENCES = 2

# Batches are independent, unlike the segmenter's windows, so they run at once.
MAX_WORKERS = 4

# Keep tooltips skimmable, and keep the output tokens down on long transcripts.
MAX_REASON_WORDS = 12

# Sound cues such as [Music] or [Applause], which the segmenter keeps as their
# own sentences.
_CUE_RE = re.compile(r"^\[.*\]$")


def _rubric(prof: dict) -> str:
    article = "an" if prof["name"][:1].upper() in "AEIOU" else "a"
    return (
        f"You are choosing which sentences of a video transcript {article} {prof['name']} "
        "learner should shadow (listen, then repeat aloud, copying the rhythm).\n\n"
        "Score each sentence 0-100 on TWO things together:\n"
        "- MEANING: does the sentence carry a main point of the video? Would missing it leave "
        "a hole in your understanding of what the speaker is saying?\n"
        "- LANGUAGE: is the phrasing worth being able to say yourself? Natural collocations, "
        "set phrases, discourse markers and the reductions of everyday speech all count.\n\n"
        "Score bands:\n"
        "- 85-100: carries a main point AND the phrasing is worth reusing.\n"
        "- 60-84: strong on one of the two - a key idea, or a rich piece of natural language.\n"
        "- 30-59: ordinary. Clear enough, but unremarkable to say.\n"
        "- 0-29: filler, back-channel, a fragment, or a line that means nothing on its own.\n\n"
        "Judge every sentence against these bands on its own merits. Do not grade on a curve "
        "within this batch. As a rough calibration, in a typical batch of 50 sentences about 10 "
        "deserve 70 or above.\n\n"
        'Also give each sentence a "tag", copied verbatim as exactly one of: '
        f"{', '.join(prof['tags'])}. Prefer the most specific one: reach for \"key idea\" only "
        "when no language tag describes what makes the sentence worth practising.\n"
        f'And a "reason": at most {MAX_REASON_WORDS} words on why it scores that way. '
        "No full sentences, no restating the line.\n"
    )


def _get_client():
    global _client
    if _client is None:
        _client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    return _client


def _is_eligible(text: str, prof: dict) -> bool:
    """Whether a line is worth spending a scoring slot on.

    Below the threshold a line is back-channel ("Why?", "Yeah, right.") --
    understandable on sight and worth nothing as shadowing practice. Counted in
    the language's own units, so a 6-character Chinese line is judged on the
    same footing as a 5-word English one.
    """
    stripped = text.strip()
    if not stripped or _CUE_RE.match(stripped):
        return False
    return len(languages.units_of(stripped, prof)) >= prof["min_eligible"]


def _clean_reason(reason: str) -> str:
    words = str(reason).split()
    return " ".join(words[:MAX_REASON_WORDS])


def _clean_tag(tag: str, prof: dict) -> str:
    """Prefer the fixed vocabulary, but keep an off-list label rather than lose it.

    The tag is only ever shown in a tooltip, so an invented-but-short one still
    tells the reader something; only rambling ones are dropped.
    """
    tag = str(tag).strip().lower()
    if tag in prof["tags"]:
        return tag
    words = tag.split()
    return tag if 0 < len(words) <= 3 else ""


def _score_batch(
    batch: list[dict], context_before: list[str], context_after: list[str], prof: dict
):
    """Score one batch. Returns ({index: {...}}, error_message_or_None).

    A batch that fails degrades to unscored rather than sinking the whole pass -
    the same posture the segmenter takes when the model returns nothing usable.
    The message is kept so that a pass where *every* batch failed can say why
    instead of just reporting no results.
    """
    model = os.environ.get("LLM_MODEL", "gpt-4o-mini")

    lines = "\n".join(f'{item["index"]}: {item["text"]}' for item in batch)
    context = ""
    if context_before:
        context += "Sentences just before this batch (context only, do not score):\n"
        context += "\n".join(context_before) + "\n\n"
    if context_after:
        context += "Sentences just after this batch (context only, do not score):\n"
        context += "\n".join(context_after) + "\n\n"

    prompt = (
        _rubric(prof) + "\n"
        + context
        + "Score every numbered sentence below. Use the number given, and score each one "
        "exactly once.\n\n"
        f"{lines}\n\n"
        'Respond with strict JSON only: {"scores": [{"index": 12, "score": 74, '
        '"tag": "phrasal verb", "reason": "..."}]}'
    )

    try:
        resp = _get_client().chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0,
        )
        parsed = json.loads(resp.choices[0].message.content)
    except Exception as exc:
        return {}, str(exc)

    entries = parsed.get("scores") if isinstance(parsed, dict) else None
    if not isinstance(entries, list):
        return {}, "the model did not return a scores array"

    allowed = {item["index"] for item in batch}
    out = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        try:
            index = int(entry.get("index"))
            score = int(float(entry.get("score", 0)))
        except (TypeError, ValueError):
            continue
        if index not in allowed:
            continue
        out[index] = {
            "score": max(0, min(100, score)),
            "tag": _clean_tag(entry.get("tag", ""), prof),
            "reason": _clean_reason(entry.get("reason", "")),
        }
    return out, None


def score_sentences(sentences: list[dict], prof: dict) -> list[dict]:
    """Score every sentence for shadowing value.

    Returns one entry per input sentence, in index order. Sentences that are
    sound cues or too short never reach the LLM and keep a score of 0, so they
    can't take a slot in the recommended set.
    """
    scores = [
        {"index": i, "score": 0, "tag": "", "reason": ""} for i in range(len(sentences))
    ]

    eligible = [
        {"index": i, "text": s["text"]}
        for i, s in enumerate(sentences)
        if _is_eligible(s["text"], prof)
    ]
    if not eligible:
        return scores

    # Checked here rather than per batch: a missing key fails every batch, and
    # the generic "nothing usable" message would hide the real cause.
    if not os.environ.get("OPENAI_API_KEY"):
        raise RecommendError("OPENAI_API_KEY is not set. Add it to the .env file.")

    batches = [eligible[i : i + BATCH_SENTENCES] for i in range(0, len(eligible), BATCH_SENTENCES)]

    def context_for(batch):
        first, last = batch[0]["index"], batch[-1]["index"]
        before = [s["text"] for s in sentences[max(0, first - CONTEXT_SENTENCES) : first]]
        after = [s["text"] for s in sentences[last + 1 : last + 1 + CONTEXT_SENTENCES]]
        return before, after

    workers = min(MAX_WORKERS, len(batches))
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = []
        for batch in batches:
            before, after = context_for(batch)
            futures.append(pool.submit(_score_batch, batch, before, after, prof))
        results = [f.result() for f in futures]

    scored_any = False
    first_error = None
    for result, error in results:
        for index, value in result.items():
            scores[index].update(value)
            scored_any = True
        if error and first_error is None:
            first_error = error

    if not scored_any:
        detail = f": {first_error}" if first_error else "."
        raise RecommendError(f"The scoring service failed{detail}")

    return scores
