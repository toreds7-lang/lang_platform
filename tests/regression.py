"""Phase 1 regression gate: prove the multi-language refactor did not change English.

Runs entirely offline against the transcripts already in cache/ -- no API key,
no network, no LLM spend. Run it from the repo root:

    .venv\\Scripts\\python.exe tests\\regression.py

Two checks:

1. GOLDEN PROMPT. The punctuation prompt is now assembled from a language
   profile instead of being a hand-written literal. For English the rendered
   string must still be byte-identical to the literal at commit 5a33ed9,
   because a single changed word would silently change every future
   segmentation.

2. REPLAY ALIGNMENT. Feed each cached video's already-accepted sentence texts
   back through the refactored _build_units / _norm / _align / _finalize_text
   and assert the spans come out where they came out before. This exercises
   the whole non-LLM half of the segmenter against real transcripts, using the
   stored word_range values as the expected answer.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))

import languages  # noqa: E402
import segmenter  # noqa: E402

EN = languages.profile("en")

# The literal from commit 5a33ed9, server/segmenter.py:87-102, verbatim.
GOLDEN = (
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
    "Words:\nalpha beta gamma"
)


def check_golden_prompt() -> list[str]:
    built = segmenter._build_prompt(["alpha", "beta", "gamma"], EN)
    if built == GOLDEN:
        return []

    # Report the first divergence rather than dumping two walls of text.
    for i, (a, b) in enumerate(zip(GOLDEN, built)):
        if a != b:
            return [
                f"prompt diverges at char {i}\n"
                f"  expected: ...{GOLDEN[max(0, i - 40):i + 40]!r}\n"
                f"  built:    ...{built[max(0, i - 40):i + 40]!r}"
            ]
    return [f"prompt length differs: expected {len(GOLDEN)}, built {len(built)}"]


def _cached_videos() -> list[Path]:
    """Every dir holding both a raw transcript and an accepted segmentation."""
    root = ROOT / "cache"
    found = []
    for path in sorted(root.rglob("raw.json")):
        for key in ("modified_v3.json", "modified_v2.json"):
            if (path.parent / key).exists():
                found.append(path.parent)
                break
    return found


def check_replay(video_dir: Path) -> list[str]:
    raw = json.loads((video_dir / "raw.json").read_text(encoding="utf-8"))
    old_path = video_dir / "modified_v3.json"
    if not old_path.exists():
        old_path = video_dir / "modified_v2.json"
    old = json.loads(old_path.read_text(encoding="utf-8"))

    meta_path = video_dir / "meta.json"
    lang = "en"
    if meta_path.exists():
        lang = json.loads(meta_path.read_text(encoding="utf-8")).get("lang") or "en"
    prof = languages.profile(lang)

    errors = []
    units = segmenter._build_units(raw, prof)

    total_units = sum(len(segmenter._units_of(c["text"], prof)) for c in raw)
    if len(units) != total_units:
        errors.append(f"{video_dir.name}: built {len(units)} units, expected {total_units}")

    # Timings must stay monotonic and inside their chunk, which is what
    # auto-pause depends on.
    for i, unit in enumerate(units):
        if unit["end"] < unit["start"]:
            errors.append(f"{video_dir.name}: unit {i} ends before it starts")
            break
        if i and unit["start"] < units[i - 1]["start"]:
            errors.append(f"{video_dir.name}: unit {i} starts before unit {i - 1}")
            break

    # Replay the stored sentences through alignment in WINDOWS, exactly as
    # segment_into_sentences does. Aligning one sentence at a time would be a
    # different and much harsher test: on a 3-word line a single caption the
    # model corrected ("adors" -> "adores") drops the match ratio under the 0.7
    # bail-out, which never happens inside a real 350-unit window.
    span_key = "unit_range" if "unit_range" in old[0] else "word_range"
    expected = [(s[span_key][0], s[span_key][1], s["text"]) for s in old]

    batches, batch = [], []
    for entry in expected:
        batch.append(entry)
        if entry[1] - batch[0][0] + 1 >= prof["window"]:
            batches.append(batch)
            batch = []
    if batch:
        batches.append(batch)

    # A leftover trailing batch can be a single very short line, and judging one
    # in isolation is harsher than anything production does: "Bye bye!" built
    # from the one caption unit "bye!" scores 0.667 against the 0.7 bail-out,
    # while inside a real 350-unit window it aligns without trouble. Fold a
    # small tail back into the batch before it so every window under test is a
    # realistic size.
    if len(batches) > 1:
        tail = batches[-1]
        if tail[-1][1] - tail[0][0] + 1 < prof["window"] // 2:
            batches[-2].extend(batches.pop())

    checked = 0
    for batch in batches:
        base, last = batch[0][0], batch[-1][1]
        if last >= len(units):
            break
        window = units[base : last + 1]
        items = [{"t": text, "w": None, "r": None} for _, _, text in batch]
        spans = segmenter._align(items, window, prof)
        if not spans:
            errors.append(
                f"{video_dir.name}: window at unit {base} ({len(batch)} sentences) "
                "failed to align"
            )
            continue
        if len(spans) != len(batch):
            errors.append(
                f"{video_dir.name}: window at unit {base} produced {len(spans)} spans, "
                f"expected {len(batch)}"
            )
            continue
        for span, (want_start, want_end, want_text) in zip(spans, batch):
            got = (span["start"] + base, span["end"] + base)
            if got != (want_start, want_end):
                errors.append(
                    f"{video_dir.name}: span moved, expected units "
                    f"{want_start}-{want_end} got {got[0]}-{got[1]}\n"
                    f"    {want_text[:70]!r}"
                )
                continue
            if span["text"] != want_text:
                errors.append(
                    f"{video_dir.name}: finalize changed the text at unit {want_start}\n"
                    f"    before: {want_text!r}\n"
                    f"    after:  {span['text']!r}"
                )
                continue
            checked += 1

    if checked == 0:
        errors.append(f"{video_dir.name}: nothing was replayed")
    else:
        print(
            f"    {video_dir.name}: {checked}/{len(expected)} sentences replayed clean "
            f"across {len(batches)} window(s)"
        )
    return errors


def check_cache() -> list[str]:
    """The language-partitioned cache must still find pre-language videos.

    Those live in the flat cache/<video_id>/ layout with no `lang` in their
    meta. Nothing migrates them, so the resolver's legacy fallback is the only
    thing keeping them readable -- which makes it worth a permanent test.
    """
    import cache

    errors = []
    legacy = [d for d in (ROOT / "cache").iterdir() if d.is_dir() and (d / "meta.json").exists()]

    for d in legacy:
        vid = d.name
        if cache.resolve_dir(vid) != d:
            errors.append(f"resolve_dir({vid!r}) with no lang did not find the flat dir")
        if cache.resolve_dir(vid, "en") != d:
            errors.append(f"resolve_dir({vid!r}, 'en') did not fall back to the flat dir")
        if cache.lang_of(vid) != "en":
            errors.append(f"lang_of({vid!r}) should default to 'en' for a pre-language video")
        if cache.read_json(vid, "meta") is None:
            errors.append(f"read_json({vid!r}, 'meta') came back empty")

    if cache.resolve_dir("nosuchvideo0") is not None:
        errors.append("resolve_dir found a dir for a video that was never cached")

    listed = cache.list_videos()
    if len(listed) < len(legacy):
        errors.append(f"list_videos returned {len(listed)}, expected at least {len(legacy)}")
    for meta in listed:
        if not languages.is_known(meta.get("lang")):
            errors.append(f"list_videos left {meta.get('video_id')!r} without a usable lang")

    print(f"    {len(legacy)} legacy dir(s) resolved, {len(listed)} video(s) listed")
    return errors


def check_profiles() -> list[str]:
    """Every registered language must be complete enough to build a prompt."""
    errors = []
    required = (
        "name", "native", "captions", "voices", "timing_unit",
        "word_grouping", "reading", "capitalize", "sentence_end", "tags",
    )
    for code in languages.codes():
        prof = languages.profile(code)
        for field in required:
            if field not in prof:
                errors.append(f"{code}: profile is missing {field!r}")
        if prof.get("timing_unit") not in languages.UNIT_TUNING:
            errors.append(f"{code}: unknown timing_unit {prof.get('timing_unit')!r}")
        if prof.get("word_grouping") not in ("whitespace", "llm"):
            errors.append(f"{code}: unknown word_grouping {prof.get('word_grouping')!r}")
        if not prof.get("voices"):
            errors.append(f"{code}: no TTS voice registered")
        # A prompt that raises is a language that can never be segmented.
        try:
            built = segmenter._build_prompt(["x", "y"], prof)
            if "Rules:" not in built or "strict JSON" not in built:
                errors.append(f"{code}: built prompt is missing its core sections")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{code}: _build_prompt raised {exc!r}")

    errors += _check_voice_defaults()
    print(f"    {len(languages.codes())} language(s): {', '.join(languages.codes())}")
    return errors


def _check_voice_defaults() -> list[str]:
    """Voice resolution across languages, which Shift+T depends on.

    Speaking a selection in English while learning Japanese posts to ?lang=en
    while state.voice is still the Japanese one. The client sends null so the
    English profile picks its own default; if that default were ever unset, or
    if a foreign voice were quietly accepted instead of rejected, the shortcut
    would either 502 or read English in a Japanese accent.
    """
    import tts

    errors = []
    for code in languages.codes():
        prof = languages.profile(code)
        registered = list(prof["voices"].values())

        default = tts._validate_voice(None, prof)
        if default != registered[0]:
            errors.append(
                f"{code}: default voice is {default!r}, expected the first registered "
                f"voice {registered[0]!r}"
            )

        # A voice belonging to some other language must be refused, not used.
        foreign = next(
            (
                v
                for other in languages.codes()
                if other != code
                for v in languages.profile(other)["voices"].values()
            ),
            None,
        )
        if foreign:
            try:
                tts._validate_voice(foreign, prof)
                errors.append(f"{code}: accepted {foreign!r}, a voice from another language")
            except tts.TTSError:
                pass
    return errors


def check_synthetic() -> list[str]:
    """Exercise each non-English pipeline with a hand-written model response.

    The expensive, variable half of segmentation is the LLM call; everything
    after it is deterministic. Feeding in the response by hand tests unit
    building, alignment, finalizing and word grouping for a new language for
    free, and catches the structural mistakes long before a real video does.
    """
    errors = []

    cases = [
        {
            "lang": "zh",
            "chunks": [
                {"index": 0, "text": "今天天气很好", "start": 0.0, "duration": 2.0},
                {"index": 1, "text": "我们去公园吧", "start": 2.0, "duration": 2.4},
            ],
            "units": 12,
            "items": [
                {
                    "t": "今天天气很好。",
                    "w": ["今天", "天气", "很", "好"],
                    "r": ["jīntiān", "tiānqì", "hěn", "hǎo"],
                },
                {
                    "t": "我们去公园吧。",
                    "w": ["我们", "去", "公园", "吧"],
                    "r": ["wǒmen", "qù", "gōngyuán", "ba"],
                },
            ],
            "spans": [(0, 5), (6, 11)],
            "texts": ["今天天气很好。", "我们去公园吧。"],
            "first_words": [
                ("今天", "jīntiān"),
                ("天气", "tiānqì"),
                ("很", "hěn"),
                ("好", "hǎo"),
            ],
        },
        {
            "lang": "ja",
            "chunks": [
                {"index": 0, "text": "今日はいい天気ですね", "start": 0.0, "duration": 2.4},
                {"index": 1, "text": "公園に行きましょう", "start": 2.4, "duration": 2.4},
            ],
            "units": 19,
            "items": [
                {
                    "t": "今日はいい天気ですね。",
                    "w": ["今日", "は", "いい", "天気", "です", "ね"],
                    # Mixed on purpose. The prompt asks for kanji-only furigana,
                    # but real transcripts come back with the kana word repeated
                    # as its own reading ("は" -> "は"), so both the empty form
                    # and the identity form have to be dropped.
                    "r": ["きょう", "は", "", "てんき", "です", ""],
                },
                {
                    "t": "公園に行きましょう。",
                    "w": ["公園", "に", "行きましょう"],
                    "r": ["こうえん", "", "い"],
                },
            ],
            "spans": [(0, 9), (10, 18)],
            "texts": ["今日はいい天気ですね。", "公園に行きましょう。"],
            # Kana-only words carry no reading key at all, not an empty one.
            "first_words": [
                ("今日", "きょう"),
                ("は", None),
                ("いい", None),
                ("天気", "てんき"),
                ("です", None),
                ("ね", None),
            ],
        },
        {
            # The case that forced timing and grouping apart. Whitespace times
            # the audio correctly (9 syllables) but must NOT decide words:
            # "thanh pho" is one word written as two tokens.
            "lang": "vi",
            "chunks": [
                {"index": 0, "text": "hôm nay thành phố rất đẹp", "start": 0.0, "duration": 2.5},
                {"index": 1, "text": "chúng ta đi công viên", "start": 2.5, "duration": 2.5},
            ],
            "units": 11,
            "items": [
                {
                    "t": "Hôm nay thành phố rất đẹp.",
                    "w": ["Hôm nay", "thành phố", "rất", "đẹp"],
                    "r": None,
                },
                {
                    "t": "Chúng ta đi công viên.",
                    "w": ["Chúng ta", "đi", "công viên"],
                    "r": None,
                },
            ],
            "spans": [(0, 5), (6, 10)],
            "texts": ["Hôm nay thành phố rất đẹp.", "Chúng ta đi công viên."],
            "first_words": [
                ("Hôm nay", None),
                ("thành phố", None),
                ("rất", None),
                ("đẹp", None),
            ],
        },
    ]

    for case in cases:
        lang = case["lang"]
        if lang not in languages.LANGUAGES:
            continue
        prof = languages.profile(lang)
        units = segmenter._build_units(case["chunks"], prof)

        if len(units) != case["units"]:
            errors.append(f"{lang}: built {len(units)} units, expected {case['units']}")
            continue
        for i, unit in enumerate(units):
            if unit["end"] < unit["start"]:
                errors.append(f"{lang}: unit {i} ends before it starts")
                break
            if i and unit["start"] < units[i - 1]["end"] - 1e-6:
                errors.append(f"{lang}: unit {i} overlaps unit {i - 1}")
                break

        spans = segmenter._align(case["items"], units, prof)
        if not spans:
            errors.append(f"{lang}: alignment returned nothing")
            continue
        got = [(s["start"], s["end"]) for s in spans]
        if got != case["spans"]:
            errors.append(f"{lang}: spans {got}, expected {case['spans']}")
        if [s["text"] for s in spans] != case["texts"]:
            errors.append(f"{lang}: texts {[s['text'] for s in spans]}")
        first = [(w["w"], w.get("r")) for w in (spans[0]["words"] or [])]
        if first != case["first_words"]:
            errors.append(f"{lang}: first sentence words {first}")

        # Every span must cover the window with no gap and no overlap, or
        # sentence timings drift out of step with the audio.
        cursor = 0
        for s in spans:
            if s["start"] != cursor:
                errors.append(f"{lang}: span gap or overlap at unit {s['start']}")
                break
            cursor = s["end"] + 1
        if cursor != len(units):
            errors.append(f"{lang}: spans cover {cursor} of {len(units)} units")

        # A word list that doesn't reproduce the sentence can't be trusted to
        # line up with its readings either, so it must degrade to one span per
        # character rather than silently mispairing them.
        broken = [{"t": case["items"][0]["t"], "w": ["完全", "不同"], "r": ["a", "b"]}]
        fallback = segmenter._align(broken, units[: case["spans"][0][1] + 1], prof)
        if fallback:
            words = fallback[0]["words"] or []
            if any(w.get("r") for w in words):
                errors.append(f"{lang}: kept readings from a word list that didn't match")
            if len(words) < 2:
                errors.append(f"{lang}: bad word list did not degrade to per-character spans")

        print(f"    {lang}: {len(units)} units -> {len(spans)} sentences, words + readings ok")

    errors += _check_reading_shapes()
    return errors


def _check_reading_shapes() -> list[str]:
    """Readings must survive every shape a model actually answers in.

    On a real Chinese transcript, 64% of sentences lost every reading they had:
    the model returned "w" and "r" as parallel arrays and left out the entries
    for words needing no reading ("Mac", "CPU", "M4"), so the lengths disagreed
    and the whole sentence was discarded. The prompt now asks for pairs, which
    cannot disagree, and a mismatched pair of arrays is realigned rather than
    thrown away.
    """
    if "zh" not in languages.LANGUAGES:
        return []
    prof = languages.profile("zh")
    errors = []
    text = "三月苹果更新了Mac产品线。"
    words = ["三月", "苹果", "更新", "了", "Mac", "产品线"]
    full = ["sānyuè", "píngguǒ", "gēngxīn", "le", "", "chǎnpǐnxiàn"]
    short = ["sānyuè", "píngguǒ", "gēngxīn", "le", "chǎnpǐnxiàn"]  # "Mac" entry omitted

    # Raw entries as a model would send them, run through the real normalizer
    # before _words_for sees them -- which is the order the pipeline uses.
    cases = [
        ("pairs", {"t": text, "w": [[w, full[i]] for i, w in enumerate(words)]}),
        ("parallel arrays, padded", {"t": text, "w": words, "r": full}),
        ("parallel arrays, Latin word skipped", {"t": text, "w": words, "r": short}),
    ]
    for label, entry in cases:
        parsed_w, parsed_r = segmenter._parse_words(entry)
        got = segmenter._words_for({"t": text, "w": parsed_w, "r": parsed_r}, prof)
        by_word = {w["w"]: w.get("r") for w in got or []}
        if by_word.get("三月") != "sānyuè" or by_word.get("产品线") != "chǎnpǐnxiàn":
            errors.append(f"zh {label}: readings lost or misplaced -> {by_word}")
        if by_word.get("Mac"):
            errors.append(f"zh {label}: gave the Latin word a reading -> {by_word['Mac']!r}")

    # A mismatch that cannot be positioned must drop the readings, never guess:
    # pinyin printed over the wrong character teaches the wrong pronunciation.
    got = segmenter._words_for({"t": text, "w": words, "r": ["a", "b"]}, prof)
    if any(w.get("r") for w in got or []):
        errors.append("zh: kept readings from an unpositionable array")

    # And the parser has to read the pair form back out.
    parsed = segmenter._parse_sentences(
        {"sentences": [{"t": text, "w": [[w, full[i]] for i, w in enumerate(words)]}]}, prof
    )
    if not parsed or parsed[0]["w"] != words or parsed[0]["r"] != full:
        errors.append(f"zh: pair form did not parse back -> {parsed}")

    print(f"    reading shapes: {len(cases)} response forms + 2 failure modes ok")
    errors += _check_readings_pass()
    return errors


def _check_readings_pass() -> list[str]:
    """The separate readings pass, without calling the model.

    Readings are keyed by word rather than by position because every positional
    form drifted: folded into segmentation only 0-48% of sentences got them,
    and a dedicated array of 120 readings came back with 116, 121, 122 or 126
    entries. Keyed by word, coverage measured 100% twice. These checks pin the
    parts that decide correctness: what gets sent, and how a table is applied.
    """
    import readings

    errors = []

    # Only things that can actually be pronounced should cost tokens.
    for word, want in [
        ("天气", True), ("お久しぶり", True), ("thành", True),
        ("Mac", False), ("CPU", False), ("M4", False), ("5098", False),
        ("[音楽]", False), ("[Applause]", False), ("", False), ("   ", False),
    ]:
        if readings.needs_reading(word) != want:
            errors.append(f"needs_reading({word!r}) should be {want}")

    prof = languages.profile("zh") if "zh" in languages.LANGUAGES else None
    if prof:
        sents = [
            {"words": [{"w": "今天"}, {"w": "天气"}, {"w": "Mac"}, {"w": "好", "r": "stale"}]}
        ]
        table = {"今天": "jīntiān", "天气": "tiānqì", "好": "hǎo"}
        readings.ensure = lambda words, p, _t=table: _t  # no network
        changed = readings.apply(sents, prof)
        got = {w["w"]: w.get("r") for w in sents[0]["words"]}
        if not changed:
            errors.append("apply reported no change while it rewrote every reading")
        if got != {"今天": "jīntiān", "天气": "tiānqì", "Mac": None, "好": "hǎo"}:
            errors.append(f"apply produced {got}")
        # Re-running with the same table must be a no-op, or every transcript
        # read would rewrite its cache file.
        if readings.apply(sents, prof):
            errors.append("apply reported a change on an already-correct transcript")

        # A reading equal to its word carries nothing and must not be attached.
        sents = [{"words": [{"w": "あの"}]}]
        readings.ensure = lambda words, p: {"あの": "あの"}
        readings.apply(sents, prof)
        if sents[0]["words"][0].get("r"):
            errors.append("apply kept a reading identical to its word")

    # A language with no reading system must never reach the model at all.
    if "vi" in languages.LANGUAGES:
        called = []
        readings.ensure = lambda words, p: called.append(1) or {}
        if readings.apply([{"words": [{"w": "thành phố"}]}], languages.profile("vi")):
            errors.append("vi reported a reading change; it has no reading system")
        if called:
            errors.append("vi asked for readings despite having none")

    print("    readings pass: word filter, apply, idempotence, no-reading languages ok")
    return errors


def check_breakdown() -> list[str]:
    """The Alt+O breakdown, with the model response supplied by hand.

    Two things decide whether this feature is correct, and neither needs the
    network. First, "part" and "word" are handed straight to a TTS voice, so
    they must survive as target-language text and a row that lost its
    target-language half must be dropped rather than rendered as an orphaned
    English fragment. Second, the vocabulary is what leaves the app into a
    flashcard deck, so its shape has to be exactly what the exporter expects.
    """
    import dictionary

    errors = []

    # Deliberately messy: a duplicate word, an entry with no word at all, a
    # part with no target-language half, a non-dict in each array, and padding
    # whitespace on every field.
    response = {
        "translation": "  Also, because the weather is good, many people get married.  ",
        "parts": [
            {"part": " 날씨도 ", "gloss": " 날씨 (weather), 도 (also) "},
            {"part": "굉장히", "gloss": "very, extremely"},
            {"part": "  ", "gloss": "an orphan with no text to speak"},
            "not an object",
        ],
        "vocab": [
            {
                "word": " 날씨 ",
                "reading": " nalssi ",
                "part_of_speech": " noun ",
                "meaning": " weather ",
                "explanation": " The state of the sky and air. ",
            },
            {"word": "날씨", "meaning": "a duplicate that must be dropped"},
            {"word": "", "meaning": "no headword at all"},
            ["not an object"],
        ],
    }

    prompts = []

    def fake_complete(prompt):
        prompts.append(prompt)
        return response

    real = dictionary._complete_json
    dictionary._complete_json = fake_complete
    try:
        for code in languages.codes():
            prof = languages.profile(code)
            prompts.clear()
            result = dictionary.break_down(
                "또 날씨도 좋기 때문에 결혼을 하시는 분들이 굉장히 많습니다",
                "a transcript line",
                ["before"],
                ["after"],
                prof,
            )

            if result["translation"] != "Also, because the weather is good, many people get married.":
                errors.append(f"{code}: translation was not trimmed -> {result['translation']!r}")

            parts = result["parts"]
            if len(parts) != 2:
                errors.append(f"{code}: expected 2 usable parts, got {len(parts)} -> {parts}")
            elif parts[0] != {"part": "날씨도", "gloss": "날씨 (weather), 도 (also)"}:
                errors.append(f"{code}: part was not trimmed -> {parts[0]}")

            vocab = result["vocab"]
            if len(vocab) != 1:
                errors.append(f"{code}: expected 1 vocab entry after de-duping, got {len(vocab)}")
                continue

            entry = vocab[0]
            wanted = {"word", "part_of_speech", "meaning", "explanation"}
            if prof["reading"]:
                wanted.add("reading")
            if set(entry) != wanted:
                errors.append(f"{code}: vocab keys are {sorted(entry)}, expected {sorted(wanted)}")
            if entry["word"] != "날씨" or entry["meaning"] != "weather":
                errors.append(f"{code}: vocab fields were not trimmed -> {entry}")

            # The prompt is what carries the per-language knowledge. If a
            # profile field stops reaching it, every breakdown quietly gets
            # worse without anything failing.
            prompt = prompts[0]
            if prof["name"] not in prompt:
                errors.append(f"{code}: prompt never names the language")
            if prof.get("word_note") and prof["word_note"] not in prompt:
                errors.append(f"{code}: prompt dropped word_note")
            if prof["reading"] and prof["reading"] not in prompt:
                errors.append(f"{code}: prompt asked for no reading despite the language having one")
            # The example is Korean; without the disclaimer the model has been
            # seen answering in Korean.
            if "Korean" not in prompt or f"in {prof['name']}, not Korean" not in prompt:
                errors.append(f"{code}: prompt does not disclaim the Korean example")
    finally:
        dictionary._complete_json = real

    print(f"    breakdown: {len(languages.codes())} profile(s), shape + prompt + de-dupe ok")
    return errors


def check_routes() -> list[str]:
    """Route-level behaviour, in-process. Still no network and no LLM calls.

    The case that matters here is an unrecognised ?lang. Because the cache
    resolver falls back to the flat legacy layout, an unknown code used to miss
    its language dir, land on the legacy dir and answer as though nothing were
    wrong -- a wrong-language answer dressed up as a correct one.
    """
    from fastapi.testclient import TestClient
    import cache
    import main

    errors = []
    client = TestClient(main.app)

    resp = client.get("/api/languages")
    if resp.status_code != 200 or "languages" not in resp.json():
        errors.append("GET /api/languages did not return a language list")

    videos = client.get("/api/videos").json()
    if not videos:
        print("    SKIP: no cached videos to exercise routes against")
        return errors

    video = videos[0]
    vid, lang = video["video_id"], video["lang"]
    # A registered language this video is NOT filed under, to prove the
    # partitioning actually isolates it rather than quietly finding it anyway.
    other = next((c for c in languages.codes() if c != lang), None)

    read_paths = [
        f"/api/videos/{vid}/bookmarks",
        f"/api/videos/{vid}/recommendations",
        f"/api/videos/{vid}/transcript?mode=raw&",
    ]
    for path in read_paths:
        sep = "" if path.endswith("&") else "?"

        if client.get(f"{path}{sep}lang=zz").status_code != 400:
            errors.append(f"{path} accepted an unregistered lang, expected 400")

        good = client.get(f"{path}{sep}lang={lang}")
        if good.status_code != 200:
            errors.append(f"{path} rejected its own lang={lang} with {good.status_code}")

        # No ?lang at all still resolves, by scanning: that is the path a
        # pre-language cache dir and a hand-typed URL both take.
        if client.get(path.rstrip("&?")).status_code != 200:
            errors.append(f"{path} without lang failed, expected the scan to find it")

    # Reading a video under the wrong language must not fall through to it.
    # Only meaningful for a video that has a language dir of its own; a legacy
    # flat-layout video is reachable from any language by design.
    if other and (cache.CACHE_ROOT / lang / vid).is_dir():
        wrong = client.get(f"/api/videos/{vid}/transcript?mode=raw&lang={other}")
        if wrong.status_code != 404:
            errors.append(
                f"transcript for a {lang} video answered under lang={other} "
                f"with {wrong.status_code}, expected 404"
            )

    if client.post("/api/videos/load", json={"url_or_id": vid, "lang": "zz"}).status_code != 400:
        errors.append("load accepted an unregistered lang, expected 400")

    # Both of the breakdown route's rejection paths, neither of which reaches
    # the model. The length cap matters most: without it a selection spanning
    # the whole transcript is one very large, very slow, very expensive call.
    import dictionary

    breakdown = f"/api/videos/{vid}/explain/breakdown"
    if client.post(f"{breakdown}?lang=zz", json={"sentence_idx": 0}).status_code != 400:
        errors.append("breakdown accepted an unregistered lang, expected 400")

    too_long = "가" * (dictionary.MAX_BREAKDOWN_CHARS + 1)
    resp = client.post(
        f"{breakdown}?lang={lang}", json={"sentence_idx": 0, "text": too_long}
    )
    if resp.status_code != 400:
        errors.append(f"breakdown accepted {len(too_long)} characters, expected 400")

    print(f"    {len(read_paths)} read route(s) against {vid} (lang={lang}, isolated from {other})")
    return errors


def main() -> int:
    failures = []

    print("1. golden prompt (English must be byte-identical to 5a33ed9)")
    errs = check_golden_prompt()
    failures += errs
    print("   FAIL" if errs else "   ok")

    print("2. replay alignment against cached transcripts")
    videos = _cached_videos()
    if not videos:
        print("   SKIP: no cached videos with both raw.json and a segmentation")
    for video_dir in videos:
        failures += check_replay(video_dir)

    print("3. language-partitioned cache resolution")
    failures += check_cache()

    print("4. language profiles are complete")
    failures += check_profiles()

    print("5. non-English pipelines, with the model response supplied by hand")
    failures += check_synthetic()

    print("6. breakdown shape and prompt, with the model response supplied by hand")
    failures += check_breakdown()

    print("7. routes reject an unknown ?lang instead of falling back")
    failures += check_routes()

    print()
    if failures:
        print(f"FAILED ({len(failures)} problem(s))")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
