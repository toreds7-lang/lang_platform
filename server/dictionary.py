import json
import os
from openai import OpenAI

_client = None


class ExplainError(Exception):
    """The LLM call failed, or returned something that wasn't usable JSON."""


# The explanation language is a request-level setting, independent of the
# target language being learned (see languages.py) -- explaining Japanese
# grammar in Korean is exactly as valid as explaining it in English.
def _explain_lang_name(explain_lang: str) -> str:
    return "Korean" if explain_lang == "ko" else "English"


def _register_phrase(explain_lang: str) -> str:
    """"simple English" / "natural Korean" -- dropped into inline field rules."""
    if explain_lang == "ko":
        return "natural Korean"
    return "simple English"


# Every explanation is aimed at a learner who is still building vocabulary, so
# the register is pinned here once rather than restated in each prompt.
# English is deliberately simplified (CEFR A2-B1): many learners read it as a
# second language too. Korean is not simplified the same way -- a Korean
# interface implies a fluent Korean reader, not one still learning Korean.
def _explanation_rule(explain_lang: str) -> str:
    if explain_lang == "ko":
        return (
            "Write every explanation in natural, fluent Korean. Use clear, precise "
            "wording aimed at a fluent Korean reader -- there is no need to simplify "
            "the Korean itself."
        )
    return (
        "Write every explanation in simple English (CEFR A2-B1). Use short sentences and "
        "common words. Never explain a hard word with another hard word."
    )


# A breakdown is produced in ONE call, so the part-by-part gloss and the
# vocabulary list under it are two views of the same analysis rather than two
# guesses that can disagree about where a word ends. The ceiling keeps that call
# bounded: a caption sentence runs well under it, and a selection spanning half
# the transcript would return a wall of lines nobody reads.
MAX_BREAKDOWN_CHARS = 400

# The output format is far easier to specify by showing it than by describing
# it. The example is Korean. Korean is a valid *explanation* language (see
# _explanation_rule above) but still not a registered *target* language in
# languages.py, so the model reads this purely as a demonstration of the
# shape, with no temptation to copy its content into the target language it
# is actually working in.
#
# Three joiners appear in it on purpose, and all three have to survive into the
# real output: ", " between separate words, " + " between the pieces of one
# word, and no brackets at all for a part that cannot be broken down further.
_BREAKDOWN_EXAMPLE = """또 날씨도 좋기 때문에 결혼을 하시는 분들이 굉장히 많습니다

translation: Also, because the weather is good, there are a lot of people getting married.
parts:
  또 -> also
  날씨도 -> 날씨 (weather), 도 (also)
  좋기 때문에 -> 좋다 (to be good), 기 때문에 (because)
  결혼을 -> 결혼 (wedding), 을 (object marker)
  하시는 -> 하다 (to do) + 시 (honorific) + 는 (noun modifier)
  분들이 -> 분 (people, honorific), 들 (plural marker), 이 (subject marker)
  굉장히 -> very, extremely
  많습니다 -> 많다 (to be many), 습니다 (deferential ending)"""


def _get_client():
    global _client
    if _client is None:
        _client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    return _client


def _learner(prof: dict) -> str:
    """"an English learner", "a Mandarin Chinese learner", ..."""
    article = "an" if prof["name"][:1].upper() in "AEIOU" else "a"
    return f"{article} {prof['name']} learner"


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


def explain_word(
    word: str, sentence: str, before: list[str], after: list[str], prof: dict, explain_lang: str = "en"
) -> dict:
    keys = ""
    if prof["reading"]:
        keys += f'- "reading": the {prof["reading"]} of "{word}" as it is pronounced here.\n'
    keys += (
        f'- "part_of_speech": the part of speech of "{word}" as it is used in that sentence '
        '(for example "verb", "noun", "adjective", "phrasal verb").\n'
        f'- "definition": a short, one-line definition of "{word}" as it is used in that specific '
        "sentence (its contextual meaning, not every possible meaning).\n"
        f'- "example": one new example sentence (not the transcript sentence) using "{word}" '
        "with a similar meaning."
    )
    if prof["code"] != "en":
        # The example has to be in the language being learned to be worth
        # practising, and it has to be readable to someone who does not speak it
        # yet. Those are two different jobs, so they are two different fields:
        # the translation is kept OUT of "example" because that field is handed
        # to a text-to-speech voice, which would otherwise read the English
        # gloss aloud in the target language's accent.
        keys += (
            f" Write it in {prof['name']} and nothing else: no translation, no "
            "romanization, and nothing in brackets. This field is read aloud by a "
            f"{prof['name']} voice, so anything that is not {prof['name']} will be "
            "mispronounced.\n"
            f'- "example_english": a plain {_explain_lang_name(explain_lang)} translation of '
            "that example sentence."
        )

    prompt = (
        f"You are helping {_learner(prof)} shadow-practice a video transcript. "
        f'The learner double-clicked the word "{word}" inside the sentence marked with >>> below.\n\n'
        f"Context (2 sentences before and after):\n{_context_block(sentence, before, after)}\n\n"
        f"{_explanation_rule(explain_lang)}\n\n"
        "Explain the WORD ONLY. Do not explain the whole sentence.\n\n"
        "Respond with strict JSON only, with these keys:\n" + keys
    )
    parsed = _complete_json(prompt)
    result = {
        "word": word,
        "part_of_speech": parsed.get("part_of_speech", ""),
        "definition": parsed.get("definition", ""),
        "example": parsed.get("example", ""),
    }
    if prof["reading"]:
        result["reading"] = parsed.get("reading", "")
    if prof["code"] != "en":
        result["example_english"] = parsed.get("example_english", "")
    return result


def explain_sentence(
    sentence: str, before: list[str], after: list[str], prof: dict, explain_lang: str = "en"
) -> dict:
    lang_name = _explain_lang_name(explain_lang)
    if prof["code"] == "en":
        easy = (
            '- "easy_english": the marked sentence rewritten so a beginner can understand it. '
            "Keep the same meaning, but use simpler words and simpler structure. You may split it "
            f"into two short sentences if that makes it clearer. Write the rewrite in {lang_name}.\n"
        )
    else:
        # For a language the learner is only starting, "simplify it" is useless
        # -- they need to know what it says at all before anything else.
        easy = (
            f'- "easy_english": a plain {lang_name} translation of the marked sentence, in simple '
            "words. Keep the same meaning. You may split it into two short sentences if that "
            "makes it clearer.\n"
        )
    prompt = (
        f"You are helping {_learner(prof)} shadow-practice a video transcript. "
        "The learner asked about the sentence marked with >>> below.\n\n"
        f"Context (2 sentences before and after):\n{_context_block(sentence, before, after)}\n\n"
        f"{_explanation_rule(explain_lang)}\n\n"
        "Respond with strict JSON only, with these keys:\n"
        + easy
        + '- "meaning": 1-2 short lines saying what the speaker really means, using the surrounding '
        "context. Explain any idiom, any implied meaning, and what words like \"it\", \"they\" or "
        '"this" refer to.'
    )
    parsed = _complete_json(prompt)
    return {
        "sentence": sentence,
        "easy_english": parsed.get("easy_english", ""),
        "meaning": parsed.get("meaning", ""),
    }


def explain_grammar(
    sentence: str, before: list[str], after: list[str], prof: dict, explain_lang: str = "en"
) -> dict:
    focus = prof.get("grammar_focus", "")
    focus = f" Choose the points a learner would most likely stumble on: {focus}." if focus else ""
    prompt = (
        f"You are helping {_learner(prof)} shadow-practice a video transcript. "
        "The learner asked about the grammar of the sentence marked with >>> below.\n\n"
        f"Context (2 sentences before and after):\n{_context_block(sentence, before, after)}\n\n"
        f"{_explanation_rule(explain_lang)}\n\n"
        "Explain the grammar of the marked sentence at SENTENCE level.\n\n"
        "Respond with strict JSON only, with these keys:\n"
        '- "structure": one line naming the subject, the verb, the object, and any extra clauses '
        "in the marked sentence.\n"
        # Tense is not universal -- Chinese and Vietnamese mark aspect instead --
        # so the question is asked in a way every language can answer.
        '- "tense": the main tense or aspect of the sentence, and one short line on why it is '
        "used here.\n"
        '- "points": an array of 2 to 4 objects, each with "form" and "note". "form" is the exact '
        "fragment from the sentence. \"note\" is a short, simple explanation of what that form "
        "does." + focus + "\n\n"
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


def break_down(
    text: str, sentence: str, before: list[str], after: list[str], prof: dict, explain_lang: str = "en"
) -> dict:
    """Translate a passage, then gloss it part by part and list its vocabulary.

    `text` is what the learner selected; `sentence` is the transcript line it
    came from, which is only ever used to build the context block. When nothing
    was selected the caller passes the sentence as both, and the breakdown is of
    the whole line.

    The vocabulary comes back in dictionary form so it can be carried out to a
    flashcard deck, where the inflected form off a single caption would be close
    to useless.
    """
    name = prof["name"]
    is_en = prof["code"] == "en"
    lang_name = _explain_lang_name(explain_lang)

    if is_en:
        translation = (
            '- "translation": the passage rewritten so a beginner can understand it. Keep the '
            "same meaning, but use simpler words and simpler structure.\n"
        )
        # "Translate English into English" is not a task. What an English
        # learner actually needs glossed is the machinery a transcript is full
        # of and a textbook is not.
        gloss_rule = (
            "Gloss what a learner trips over rather than what is obvious: phrasal verbs, "
            "contractions, reduced and spoken forms, idioms, and what a pronoun refers to."
        )
    else:
        translation = (
            f'- "translation": a plain {lang_name} translation of the whole passage, in simple '
            "words. Keep the same meaning.\n"
        )
        gloss_rule = (
            f"Break each part into its pieces exactly the way the example does: the {name} "
            f'piece, then its {lang_name} meaning in brackets. Use ", " between separate words and '
            '" + " between the pieces of a single word. When a part cannot be broken down any '
            f"further, give its {lang_name} meaning on its own with no brackets."
        )

    # Same split, and the same reason, as "example" / "example_english" above:
    # this field is handed to a text-to-speech voice.
    purity = (
        f"It must be in {name} and nothing else: no translation, no romanization, and nothing "
        f"in brackets. This field is read aloud by a {name} voice, so anything that is not "
        f"{name} will be mispronounced."
    )

    word_note = prof.get("word_note", "")
    word_note = f" {word_note}" if word_note else ""

    vocab_keys = f'  - "word": the dictionary form (the citation form you would look up). {purity}\n'
    if prof["reading"]:
        vocab_keys += f'  - "reading": the {prof["reading"]} of that dictionary form.\n'
    vocab_keys += (
        f'  - "part_of_speech": one or two words, in {lang_name}, for example "noun", "verb", '
        '"adjective", "particle".\n'
        f'  - "meaning": a very short {lang_name} meaning. A few words, not a sentence.\n'
        f'  - "explanation": one or two short lines of {_register_phrase(explain_lang)} about the '
        "word: what it means, and anything worth knowing to use it correctly. This is the back of "
        "a flashcard."
    )

    prompt = (
        f"You are helping {_learner(prof)} shadow-practice a video transcript. "
        "The learner asked for a part-by-part breakdown of the passage below.\n\n"
        f"Passage to break down:\n{text}\n\n"
        f"Where it appears (the transcript line is marked with >>>):\n"
        f"{_context_block(sentence, before, after)}\n\n"
        f"{_explanation_rule(explain_lang)}\n\n"
        "This is the format, shown on a Korean example. Yours must be in "
        f"{name}, not Korean -- the example is here only to show the shape:\n\n"
        f"{_BREAKDOWN_EXAMPLE}\n\n"
        "Respond with strict JSON only, with these keys:\n"
        + translation
        + '- "parts": an array of objects covering the passage IN ORDER, with nothing left out '
        "and nothing added. Each object has:\n"
        f'  - "part": the next chunk of the passage, copied exactly as written. {purity}'
        f"{word_note}\n"
        f'  - "gloss": the {lang_name} breakdown of that part. {gloss_rule}\n'
        '- "vocab": an array of at most 12 objects, one per word in the passage that is worth '
        "learning. Content words only -- skip particles, endings, and grammar markers, which the "
        '"gloss" column already explains. List each word once, even if it appears twice. Each '
        "object has:\n" + vocab_keys
    )

    parsed = _complete_json(prompt)

    parts = []
    for part in parsed.get("parts") or []:
        if not isinstance(part, dict):
            continue
        form = str(part.get("part", "")).strip()
        gloss = str(part.get("gloss", "")).strip()
        # A row with no target-language text has nothing to show and nothing to
        # speak, so it would render as an orphaned English fragment.
        if form:
            parts.append({"part": form, "gloss": gloss})

    vocab = []
    seen = set()
    for entry in parsed.get("vocab") or []:
        if not isinstance(entry, dict):
            continue
        word = str(entry.get("word", "")).strip()
        if not word or word in seen:
            continue
        seen.add(word)
        item = {
            "word": word,
            "part_of_speech": str(entry.get("part_of_speech", "")).strip(),
            "meaning": str(entry.get("meaning", "")).strip(),
            "explanation": str(entry.get("explanation", "")).strip(),
        }
        if prof["reading"]:
            item["reading"] = str(entry.get("reading", "")).strip()
        vocab.append(item)

    return {
        "text": text,
        "translation": str(parsed.get("translation", "")).strip(),
        "parts": parts,
        "vocab": vocab,
    }
