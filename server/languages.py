"""The one place per-language knowledge lives.

Adding a language should mean adding a dict entry here and nothing else. Every
prompt in segmenter/dictionary/recommender/chat is parameterized by these
fields, so there is no per-language code path anywhere else in the server.

Explanations are always written in English, whatever the transcript language
is, so nothing here describes the *output* language -- only the input one.
"""


class UnknownLanguage(Exception):
    """A language code that isn't registered below."""


# --- per-language profiles -------------------------------------------------
#
# name          how the language is named to the LLM inside prompts.
# native        how it is named to the user, in the language selector.
# captions      YouTube caption codes to try, best first. Only ever matched
#               against real tracks: auto-translated tracks are never accepted,
#               because their text does not match the audio and shadowing a
#               translation is meaningless.
# timing_unit   what one unit of the timeline is. "word" for space-separated
#               scripts, "char" for scripts written without spaces. Chinese
#               characters are ~1 syllable each, so char timing is if anything
#               tighter than English word timing.
# word_grouping where clickable/lookup-able words come from. "whitespace" is
#               free; "llm" rides along on the segmenter call that already
#               re-emits the whole transcript, so it costs no extra request.
# reading       prompt-facing name of the pronunciation system, or None when
#               the script is already phonetic. Rendered as ruby text.
# capitalize    whether letter case exists at all. Gates both the "capitalize
#               sentences" prompt rule and the local _finalize_text fixups.
# sentence_end  terminal punctuation appended when the model omitted it.
# tags          the recommender's tag vocabulary. Language-specific on purpose:
#               "phrasal verb" does not exist in Chinese, and "measure word"
#               does not exist in English.

LANGUAGES = {
    "en": {
        "name": "English",
        "native": "English",
        "captions": ["en", "en-US", "en-GB"],
        "voices": {"British": "en-GB-SoniaNeural", "American": "en-US-AriaNeural"},
        "timing_unit": "word",
        "word_grouping": "whitespace",
        "reading": None,
        "capitalize": True,
        # English is the only registered language with a capitalized standalone
        # pronoun. Vietnamese also has letter case, so this cannot ride on
        # "capitalize" -- doing that turned "đi" into "đI".
        "pronoun_i": True,
        "sentence_end": ".",
        "grammar_focus": (
            "modal verbs, relative clauses, phrasal verbs, contractions, unusual word order"
        ),
        "tags": [
            "key idea",
            "collocation",
            "phrasal verb",
            "discourse marker",
            "natural rhythm",
            "idiom",
        ],
    },
    "zh": {
        "name": "Mandarin Chinese",
        "native": "中文",
        "captions": ["zh-Hans", "zh-CN", "zh", "zh-Hant", "zh-TW"],
        "voices": {"Female": "zh-CN-XiaoxiaoNeural", "Male": "zh-CN-YunxiNeural"},
        "timing_unit": "char",
        "word_grouping": "llm",
        "reading": "Hanyu Pinyin with tone marks",
        "capitalize": False,
        "sentence_end": "。",
        "word_note": (
            "A word is usually one or two characters. Keep set phrases and 成语 whole, "
            "and keep a name together as one word."
        ),
        "grammar_focus": (
            "measure words, aspect particles such as 了 / 着 / 过, 把 and 被 constructions, "
            "resultative and directional complements, topic-comment word order"
        ),
        "tags": [
            "key idea",
            "measure word",
            "chengyu",
            "resultative complement",
            "把-construction",
            "natural rhythm",
        ],
    },
    "ja": {
        "name": "Japanese",
        "native": "日本語",
        "captions": ["ja", "ja-JP"],
        "voices": {"Female": "ja-JP-NanamiNeural", "Male": "ja-JP-KeitaNeural"},
        "timing_unit": "char",
        "word_grouping": "llm",
        # Kana already say how they sound, so a reading over them is noise that
        # doubles the line height for nothing.
        "reading": "furigana in hiragana, given for kanji only and left empty for a word "
        "written entirely in kana",
        "capitalize": False,
        "sentence_end": "。",
        "word_note": (
            "Keep a word together with its okurigana, and keep a verb or adjective together "
            "with its inflected ending. Particles are their own words."
        ),
        "grammar_focus": (
            "particles such as は / が / を / に / で, verb and adjective conjugation, "
            "politeness level, te-form chains, relative clauses that precede the noun"
        ),
        "tags": [
            "key idea",
            "particle",
            "politeness level",
            "set phrase",
            "conjugation",
            "natural rhythm",
        ],
    },
    "vi": {
        "name": "Vietnamese",
        "native": "Tiếng Việt",
        "captions": ["vi", "vi-VN"],
        "voices": {"Female": "vi-VN-HoaiMyNeural", "Male": "vi-VN-NamMinhNeural"},
        # Vietnamese is the reason timing and word grouping are separate fields.
        # Its spaces separate SYLLABLES, so they time the audio perfectly but
        # say nothing about where words begin and end: "thanh pho" is one word
        # in two tokens, and splitting on the space would have you looking up
        # "thanh", which alone means something else entirely.
        "timing_unit": "word",
        "word_grouping": "llm",
        "reading": None,  # already Latin script, with the tones written in
        "capitalize": True,
        "sentence_end": ".",
        "word_note": (
            "A word may span several syllables: \"thành phố\" is one word, not two, and a "
            "name such as \"Hồ Chí Minh\" is one word. Never split a compound at a space."
        ),
        "grammar_focus": (
            "classifiers, tense markers đã / đang / sẽ, question particles, "
            "reduplication, word order in noun phrases"
        ),
        "tags": [
            "key idea",
            "classifier",
            "reduplication",
            "compound word",
            "particle",
            "natural rhythm",
        ],
    },
}


# Derived from timing_unit rather than stored per language, so a new profile
# stays a short, obvious dict instead of a wall of tuning constants.
#
# window          units handed to the LLM per request.
# max_secs        ceiling on how long one unit is assumed to take, so a caption
#                 lingering over silence can't push a sentence end late.
# fallback_secs   assumed unit length when a chunk has no usable duration.
# min_gap_units   no-LLM fallback: shortest run before a silent gap may split.
# max_units       no-LLM fallback: hard length cap.
# min_eligible    below this a line is back-channel, and the recommender skips
#                 it without spending a call on it.
# chars_per_token rough tokens-per-character for the chat context budget.
#                 English runs ~4 chars per token; CJK runs about one token
#                 PER character, so sharing the English figure would let a long
#                 Chinese transcript silently overflow the context window.
UNIT_TUNING = {
    "word": {
        "window": 350,
        "max_secs": 0.80,
        "fallback_secs": 0.30,
        "min_gap_units": 8,
        "max_units": 22,
        "min_eligible": 5,
        "chars_per_token": 4,
    },
    "char": {
        "window": 400,
        "max_secs": 0.35,
        "fallback_secs": 0.15,
        "min_gap_units": 12,
        "max_units": 40,
        "min_eligible": 6,
        "chars_per_token": 1,
    },
}

DEFAULT_LANG = "en"

_merged: dict[str, dict] = {}


def profile(code: str | None) -> dict:
    """The full profile for a language code: its entry merged with its tuning.

    None falls back to English, which is what a cache entry written before
    languages existed implicitly is.
    """
    code = code or DEFAULT_LANG
    if code not in LANGUAGES:
        raise UnknownLanguage(f"Unsupported language: {code}")
    if code not in _merged:
        entry = LANGUAGES[code]
        _merged[code] = {"code": code, **UNIT_TUNING[entry["timing_unit"]], **entry}
    return _merged[code]


def units_of(text: str, prof: dict) -> list[str]:
    """Split text into timeline units: words, or characters for unspaced scripts.

    Lives here rather than in the segmenter because every module that needs to
    measure a sentence -- timing it, or deciding whether it is too short to be
    worth scoring -- has to count it the same way.
    """
    if prof["timing_unit"] == "word":
        return text.split()
    return [ch for ch in text if not ch.isspace()]


def codes() -> list[str]:
    """Registered codes, in declaration order -- which is also UI order."""
    return list(LANGUAGES)


def is_known(code: str | None) -> bool:
    return code in LANGUAGES


def public_profiles() -> list[dict]:
    """What the frontend needs to build the language selector and voice toggle."""
    out = []
    for code in codes():
        prof = profile(code)
        out.append(
            {
                "code": code,
                "name": prof["name"],
                "native": prof["native"],
                "voices": prof["voices"],
                "has_reading": prof["reading"] is not None,
                "reading_name": prof["reading"],
                # The client needs this for the raw caption view, where there is
                # no server-side word list to fall back on.
                "spaced": prof["timing_unit"] == "word",
            }
        )
    return out
