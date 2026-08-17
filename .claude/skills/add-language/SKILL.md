---
name: add-language
description: Register a new target language in this shadowing platform - write its profile in server/languages.py, verify its TTS voices, add a regression case, and check it against a real video. Use when asked to add, support, or register a language (e.g. "add Thai", "support Korean", "can it do Portuguese").
---

# Adding a language

Adding a language to this project is a **data** change, not a code change. All
per-language knowledge lives in one dict in `server/languages.py`, and every prompt in
the segmenter, dictionary, recommender and chat is built from it. A finished run should
touch **only** `server/languages.py` and `tests/regression.py`.

> If you find yourself editing any other file, stop and say so. That is a real finding:
> it means the language needs something the profile cannot express, and the right fix is
> to add a field to the registry, not a branch to the code.

The mechanical work is about ten lines. The risk is entirely in choosing the fields —
every bug this project has had came from a field that looked obvious and failed
**silently**, producing plausible output that was subtly wrong. Work through the steps.

---

## 1. Read the existing profiles

Read `server/languages.py` before anything else. It is the spec, and the four registered
languages are worked examples that between them cover every combination that matters:

| | timing_unit | word_grouping | reading | capitalize |
|---|---|---|---|---|
| English | word | whitespace | None | yes |
| Mandarin | char | llm | pinyin | no |
| Japanese | char | llm | furigana | no |
| Vietnamese | **word** | **llm** | None | yes |

Vietnamese is the row to understand. The other three have `timing_unit` and
`word_grouping` agreeing, which makes it tempting to treat them as one field. They are
not.

## 2. Decide the risky fields, and confirm before writing

Research the language, then present **these fields with your reasoning** and get
confirmation (AskUserQuestion) before writing anything. Fill in the rest yourself —
`name`, `native`, `captions`, `tags`, `grammar_focus`, `word_note` are low-risk.

| Field | How to decide | The trap |
|---|---|---|
| `timing_unit` | `"char"` if the script is normally written without spaces between words, else `"word"` | This sets **timing granularity only**. It does not decide how words are looked up |
| `word_grouping` | `"whitespace"` **only** if a space reliably ends a word. Otherwise `"llm"` | Vietnamese is spaced yet needs `"llm"`: its spaces separate *syllables*, so `thành phố` is one word in two tokens. Splitting on the space would look up `thành`, which alone means something else |
| `reading` | Name the system as a prompt phrase, or `None` if the script is already phonetic | Say what to **skip**, as Japanese does for kana. Do not set `None` merely because a romanization would be work — a learner cannot shadow a script they cannot read |
| `capitalize` | Does the script have letter case at all | Entirely separate from `pronoun_i` |
| `pronoun_i` | **English only. Omit it.** | It capitalizes a standalone `i`. Vietnamese also has letter case, so riding it on `capitalize` turned `đi` into `đI` |
| `sentence_end` | The terminal punctuation of that script | Not universally `.` — CJK uses `。`, and **Thai has none at all**, separating sentences with a space |
| `tags` | Grammar features a learner of *this* language actually notices | Copying English's list hands Chinese "phrasal verb". Chinese wants measure words and 成语; Japanese wants particles and politeness level |

`UNIT_TUNING` is derived from `timing_unit`. Do not add tuning constants per language.

## 3. Verify the TTS voices exist

Never invent a voice name — they look guessable and are not. Check against the service:

```python
import asyncio, edge_tts
names = {v["ShortName"] for v in asyncio.run(edge_tts.list_voices())}
print(sorted(n for n in names if n.startswith("ko-")))
```

Register two if available (a female and a male read differently, and the toggle is built
from whatever is here). **The first entry becomes the default voice**, both server-side
in `tts._validate_voice` and in the client's toggle — they must agree, so order matters.

If the language has no edge-tts voice, say so plainly before going further: audio is a
core feature here, and the browser's own voices cover very little.

## 4. Write the entry

Add to `LANGUAGES` in `server/languages.py`, after the existing entries — declaration
order is the order of the language selector in the UI. Match the surrounding style:
comment the fields whose reasoning is not self-evident, especially any that surprised you.

## 5. Add a regression case

Add one entry to `cases` in `check_synthetic()` in `tests/regression.py`, following the
existing ones: two short caption chunks, the model response written **by hand**, and the
expected `units` / `spans` / `texts` / `first_words`.

This costs nothing — it supplies the model's answer rather than calling it — and it
verifies unit building, alignment, finalizing and word grouping for the new language
before a single video is loaded. For a language with readings, include at least one word
that needs none (a Latin name or a number) and, if relevant, one identity reading.

## 6. Run the offline gate

```
.venv\Scripts\python.exe tests\regression.py
```

Must print **PASSED**. `check_profiles()` validates every registered profile and builds
its prompt, so a missing or malformed field fails here rather than at runtime.

## 7. Live check against a real video

The offline tests use answers you wrote, so they prove the plumbing and never the model's
real behaviour on that script. **Every real bug in this project surfaced here and nowhere
else.** Ask the user for a video in the new language, and tell them this step costs
roughly one video's worth of API calls before running it.

Then report, in order:

1. **Caption track found?** If not, say which tracks the video does have. Machine-
   *translated* tracks are never acceptable — the text does not match the audio, so
   shadowing one is meaningless.
2. **Words grouped sensibly?** Check a multi-syllable or compound word specifically.
3. **Reading coverage.** Count words that should have one against words that do. Anything
   below ~95% is a finding, not a rounding error.
4. **TTS plays**, and the second play is instant (served from disk).
5. **Recommender tags** come back language-appropriate, not English ones.
6. The language appears in the header selector, gets its own `<optgroup>` in the history
   dropdown, and its cache lands in `cache/<code>/`.

---

## Standing rules

**Never put readings in the segmentation prompt.** They belong to `server/readings.py`,
which asks for them separately and keys them **by word, never by position**. This is
measured, not stylistic: folding them into segmentation produced readings for only 0–48%
of sentences — varying between identical runs at `temperature=0` — and demanding one per
word made the model abandon a whole 400-unit window and return a single sentence, which
then failed alignment and dropped the window to gap-splitting. Keyed by word, coverage
measured 100% twice. Every positional form drifted; an array of 120 readings came back
with 116, 121, 122 and 126 entries in separate batches.

**Readings are shared per language** in `cache/_readings/<code>.json` and applied on every
transcript read, so a language added later automatically fills in transcripts cached
before it existed. Nothing extra is needed for this to work.

**Explanations are always English.** The profile describes the language being *learned*.
There is no second axis, and `prof["code"] == "en"` checks in `dictionary.py` mean
"target is also the explanation language" — a new language correctly takes the other
branch with no work.
