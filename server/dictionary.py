import json
import os
from openai import OpenAI

_client = None


class ExplainError(Exception):
    """The LLM call failed, or returned something that wasn't usable JSON."""


# Every explanation is aimed at a learner who is still building vocabulary, so
# the register is pinned here once rather than restated in each prompt.
_SIMPLE_ENGLISH_RULE = (
    "Write every explanation in simple English (CEFR A2-B1). Use short sentences and "
    "common words. Never explain a hard word with another hard word."
)


def _get_client():
    global _client
    if _client is None:
        _client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    return _client


def _context_block(sentence: str, before: list[str], after: list[str]) -> str:
    return "\n".join(before) + f"\n>>> {sentence}\n" + "\n".join(after)


def _complete_json(prompt: str) -> dict:
    model = os.environ.get("LLM_MODEL", "gpt-4o-mini")
    try:
        resp = _get_client().chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.3,
        )
        parsed = json.loads(resp.choices[0].message.content)
    except KeyError as exc:
        raise ExplainError("OPENAI_API_KEY is not set. Add it to the .env file.") from exc
    except json.JSONDecodeError as exc:
        raise ExplainError("The model did not return valid JSON. Try again.") from exc
    except Exception as exc:
        raise ExplainError(f"The explanation service failed: {exc}") from exc

    if not isinstance(parsed, dict):
        raise ExplainError("The model returned an unexpected response shape.")
    return parsed


def explain_word(word: str, sentence: str, before: list[str], after: list[str]) -> dict:
    prompt = (
        "You are helping an English learner shadow-practice a video transcript. "
        f'The learner double-clicked the word "{word}" inside the sentence marked with >>> below.\n\n'
        f"Context (2 sentences before and after):\n{_context_block(sentence, before, after)}\n\n"
        f"{_SIMPLE_ENGLISH_RULE}\n\n"
        "Explain the WORD ONLY. Do not explain the whole sentence.\n\n"
        "Respond with strict JSON only, with these keys:\n"
        f'- "part_of_speech": the part of speech of "{word}" as it is used in that sentence '
        '(for example "verb", "noun", "adjective", "phrasal verb").\n'
        f'- "definition": a short, one-line definition of "{word}" as it is used in that specific '
        "sentence (its contextual meaning, not every possible meaning).\n"
        f'- "example": one new example sentence (not the transcript sentence) using "{word}" '
        "with a similar meaning."
    )
    parsed = _complete_json(prompt)
    return {
        "word": word,
        "part_of_speech": parsed.get("part_of_speech", ""),
        "definition": parsed.get("definition", ""),
        "example": parsed.get("example", ""),
    }


def explain_sentence(sentence: str, before: list[str], after: list[str]) -> dict:
    prompt = (
        "You are helping an English learner shadow-practice a video transcript. "
        "The learner asked about the sentence marked with >>> below.\n\n"
        f"Context (2 sentences before and after):\n{_context_block(sentence, before, after)}\n\n"
        f"{_SIMPLE_ENGLISH_RULE}\n\n"
        "Respond with strict JSON only, with these keys:\n"
        '- "easy_english": the marked sentence rewritten so a beginner can understand it. '
        "Keep the same meaning, but use simpler words and simpler structure. You may split it "
        "into two short sentences if that makes it clearer. Write the rewrite in English.\n"
        '- "meaning": 1-2 short lines saying what the speaker really means, using the surrounding '
        "context. Explain any idiom, any implied meaning, and what words like \"it\", \"they\" or "
        '"this" refer to.'
    )
    parsed = _complete_json(prompt)
    return {
        "sentence": sentence,
        "easy_english": parsed.get("easy_english", ""),
        "meaning": parsed.get("meaning", ""),
    }


def explain_grammar(sentence: str, before: list[str], after: list[str]) -> dict:
    prompt = (
        "You are helping an English learner shadow-practice a video transcript. "
        "The learner asked about the grammar of the sentence marked with >>> below.\n\n"
        f"Context (2 sentences before and after):\n{_context_block(sentence, before, after)}\n\n"
        f"{_SIMPLE_ENGLISH_RULE}\n\n"
        "Explain the grammar of the marked sentence at SENTENCE level.\n\n"
        "Respond with strict JSON only, with these keys:\n"
        '- "structure": one line naming the subject, the verb, the object, and any extra clauses '
        "in the marked sentence.\n"
        '- "tense": the main tense of the sentence, and one short line on why it is used here.\n'
        '- "points": an array of 2 to 4 objects, each with "form" and "note". "form" is the exact '
        'fragment from the sentence (for example "would + base verb", "hesitate to call"). "note" '
        "is a short, simple explanation of what that form does. Choose the points a learner would "
        "most likely stumble on: modal verbs, relative clauses, phrasal verbs, contractions, "
        "unusual word order.\n\n"
        "If the sentence is only a fragment or a sound cue such as [Music], say so in "
        '"structure" and return an empty "points" array.'
    )
    parsed = _complete_json(prompt)

    points = []
    for point in parsed.get("points") or []:
        if isinstance(point, dict):
            form = str(point.get("form", "")).strip()
            note = str(point.get("note", "")).strip()
            if form or note:
                points.append({"form": form, "note": note})

    return {
        "sentence": sentence,
        "structure": parsed.get("structure", ""),
        "tense": parsed.get("tense", ""),
        "points": points,
    }
