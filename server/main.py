import hashlib
import threading
import time
import webbrowser
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

import cache
import languages
import transcript
import segmenter
import dictionary as dictionary_module
import readings
import recommender
import chat as chat_module
import tts

app = FastAPI()

STATIC_DIR = ROOT / "static"

# Bumped when the segmenter's output format changes, so stale caches are ignored.
# v3 renamed word_range to unit_range and added the per-word list.
MODIFIED_KEY = "modified_v3"

# Same versioning idea for the explanation caches: bump when a response shape
# changes, so entries written by an older format are never read back. All of
# these went up a version when the prompts became language-aware.
# v4 split the mixed-language example into "example" plus "example_english",
# so the target-language half can be spoken without a voice reading an English
# gloss in the wrong accent.
DICT_KEY = "dictionary_v4"
SENTENCE_KEY = "sentence_v2"
GRAMMAR_KEY = "grammar_v2"
BREAKDOWN_KEY = "breakdown_v1"
RECOMMEND_KEY = "recommend_v2"

# Every explanation is keyed by sentence index, which re-segmentation shifts.
EXPLAIN_KEYS = (DICT_KEY, SENTENCE_KEY, GRAMMAR_KEY, BREAKDOWN_KEY)

# Everything keyed by sentence index, so everything re-segmentation invalidates.
# Wider than EXPLAIN_KEYS on purpose: "clear explanations" should not throw away
# a scoring pass that costs a call per 50 sentences to rebuild.
INDEXED_KEYS = EXPLAIN_KEYS + (RECOMMEND_KEY,)


class LoadVideoRequest(BaseModel):
    url_or_id: str
    lang: str = languages.DEFAULT_LANG


class DictionaryRequest(BaseModel):
    word: str
    sentence_idx: int


class SentenceRequest(BaseModel):
    sentence_idx: int


class BreakdownRequest(BaseModel):
    sentence_idx: int
    # What the learner selected. None means "the whole sentence", which is what
    # pressing the key with nothing selected asks for.
    text: str | None = None


class BookmarkRequest(BaseModel):
    sentence_idx: int


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage]


class SentenceTTSRequest(BaseModel):
    sentence_idx: int
    voice: str | None = None
    slow: bool = False


class WordTTSRequest(BaseModel):
    text: str
    voice: str | None = None
    slow: bool = False


def _profile(video_id: str, lang: str | None):
    """The language profile a request should be answered under.

    The client always sends ?lang, because it chose the language before the
    video was ever loaded. The meta lookup behind it covers a hand-typed URL
    and, more importantly, a video cached before languages existed -- those
    have no lang at all and can only be English.

    An unrecognised code is rejected rather than ignored. Falling back would
    resolve to some other language's copy of the video and answer as if
    nothing were wrong, which is a far worse failure than a 400.
    """
    if lang is not None and not languages.is_known(lang):
        raise HTTPException(status_code=400, detail=f"Unsupported language: {lang}")
    try:
        return languages.profile(cache.lang_of(video_id, lang))
    except languages.UnknownLanguage as exc:
        raise HTTPException(status_code=400, detail=str(exc))


def _lang(video_id: str, lang: str | None) -> str:
    return _profile(video_id, lang)["code"]


@app.get("/api/languages")
def list_languages():
    return {"languages": languages.public_profiles(), "default": languages.DEFAULT_LANG}


@app.get("/api/videos")
def list_videos():
    return cache.list_videos()


@app.post("/api/videos/load")
def load_video(req: LoadVideoRequest):
    try:
        video_id = transcript.extract_video_id(req.url_or_id)
    except transcript.TranscriptError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    if not languages.is_known(req.lang):
        raise HTTPException(status_code=400, detail=f"Unsupported language: {req.lang}")

    # A load is the one request that must not guess: it is what decides which
    # language dir the video is written into.
    lang = req.lang
    prof = languages.profile(lang)

    meta = cache.read_json(video_id, "meta", lang)
    raw = cache.read_json(video_id, "raw", lang)

    if raw is None:
        try:
            raw = transcript.fetch_raw_transcript(video_id, prof)
        except transcript.TranscriptError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        cache.write_json(video_id, "raw", raw, lang)

    if meta is None:
        title = transcript.fetch_title(video_id)
        meta = {
            "video_id": video_id,
            "title": title,
            "lang": lang,
            "cached_at": datetime.now(timezone.utc).isoformat(),
        }
        cache.write_json(video_id, "meta", meta, lang)

    return {"meta": meta, "raw": raw}


@app.get("/api/videos/{video_id}/transcript")
def get_transcript(video_id: str, mode: str = "raw", lang: str | None = None):
    lang = _lang(video_id, lang)
    if mode == "raw":
        raw = cache.read_json(video_id, "raw", lang)
        if raw is None:
            raise HTTPException(status_code=404, detail="Video not loaded yet.")
        return {"mode": "raw", "sentences": raw}

    if mode == "modified":
        return {"mode": "modified", "sentences": _modified_sentences(video_id, lang)}

    raise HTTPException(status_code=400, detail="mode must be 'raw' or 'modified'")


@app.post("/api/videos/{video_id}/regenerate")
def regenerate(video_id: str, target: str = "modified", lang: str | None = None):
    prof = _profile(video_id, lang)
    lang = prof["code"]

    if target == "modified":
        # Bookmarks and explanations are keyed by sentence index, which
        # re-segmentation invalidates.
        cache.delete_json(video_id, MODIFIED_KEY, lang)
        for key in INDEXED_KEYS:
            cache.delete_json(video_id, key, lang)
        cache.delete_json(video_id, "bookmarks", lang)
        return {"mode": "modified", "sentences": _modified_sentences(video_id, lang)}

    if target == "raw":
        cache.delete_json(video_id, "raw", lang)
        cache.delete_json(video_id, MODIFIED_KEY, lang)
        for key in INDEXED_KEYS:
            cache.delete_json(video_id, key, lang)
        cache.delete_json(video_id, "bookmarks", lang)
        try:
            raw = transcript.fetch_raw_transcript(video_id, prof)
        except transcript.TranscriptError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        cache.write_json(video_id, "raw", raw, lang)
        return {"mode": "raw", "sentences": raw}

    raise HTTPException(status_code=400, detail="target must be 'raw' or 'modified'")


def _modified_sentences(video_id: str, lang: str | None):
    """The sentence list, segmenting and caching it on first use."""
    prof = _profile(video_id, lang)
    lang = prof["code"]
    modified = cache.read_json(video_id, MODIFIED_KEY, lang)
    fresh = modified is None
    if fresh:
        raw = cache.read_json(video_id, "raw", lang)
        if raw is None:
            raise HTTPException(status_code=404, detail="Video not loaded yet.")
        modified = segmenter.segment_into_sentences(raw, prof)

    # Readings are a pass of their own: asking the segmenter for them as well
    # measurably wrecked both, and keying them by word rather than by position
    # is what makes them reliable. Run on every read, not only on a fresh
    # segmentation, so a transcript cached before this existed fills in its
    # gaps without being re-segmented. Costs nothing once the language's
    # vocabulary already covers the video.
    changed = False
    try:
        changed = readings.apply(modified, prof)
    except readings.ReadingError:
        # No key, or the service is down. The transcript is still perfectly
        # usable without pronunciation above it, so serve it rather than
        # failing the whole load.
        pass

    if fresh or changed:
        cache.write_json(video_id, MODIFIED_KEY, modified, lang)
    return modified


def _sentence_context(video_id: str, sentence_idx: int, lang: str | None):
    modified = _modified_sentences(video_id, lang)

    if sentence_idx < 0 or sentence_idx >= len(modified):
        raise HTTPException(status_code=400, detail="sentence_idx out of range")

    sentence = modified[sentence_idx]["text"]
    before = [s["text"] for s in modified[max(0, sentence_idx - 2) : sentence_idx]]
    after = [s["text"] for s in modified[sentence_idx + 1 : sentence_idx + 3]]
    return sentence, before, after


def _cached_explain(video_id: str, lang: str, cache_key: str, entry_key: str, produce):
    """Return a cached explanation, or produce, store and return a fresh one."""
    with cache.lock:
        entries = cache.read_json(video_id, cache_key, lang) or {}
        if entry_key in entries:
            return entries[entry_key]

    try:
        result = produce()
    except dictionary_module.ExplainError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    with cache.lock:
        entries = cache.read_json(video_id, cache_key, lang) or {}
        entries[entry_key] = result
        cache.write_json(video_id, cache_key, entries, lang)
    return result


@app.post("/api/videos/{video_id}/explain/word")
def explain_word(video_id: str, req: DictionaryRequest, lang: str | None = None):
    prof = _profile(video_id, lang)
    sentence, before, after = _sentence_context(video_id, req.sentence_idx, prof["code"])
    return _cached_explain(
        video_id,
        prof["code"],
        DICT_KEY,
        f"{req.sentence_idx}:{req.word.lower()}",
        lambda: dictionary_module.explain_word(req.word, sentence, before, after, prof),
    )


@app.post("/api/videos/{video_id}/explain/sentence")
def explain_sentence(video_id: str, req: SentenceRequest, lang: str | None = None):
    prof = _profile(video_id, lang)
    sentence, before, after = _sentence_context(video_id, req.sentence_idx, prof["code"])
    return _cached_explain(
        video_id,
        prof["code"],
        SENTENCE_KEY,
        str(req.sentence_idx),
        lambda: dictionary_module.explain_sentence(sentence, before, after, prof),
    )


@app.post("/api/videos/{video_id}/explain/grammar")
def explain_grammar(video_id: str, req: SentenceRequest, lang: str | None = None):
    prof = _profile(video_id, lang)
    sentence, before, after = _sentence_context(video_id, req.sentence_idx, prof["code"])
    return _cached_explain(
        video_id,
        prof["code"],
        GRAMMAR_KEY,
        str(req.sentence_idx),
        lambda: dictionary_module.explain_grammar(sentence, before, after, prof),
    )


@app.post("/api/videos/{video_id}/explain/breakdown")
def explain_breakdown(video_id: str, req: BreakdownRequest, lang: str | None = None):
    prof = _profile(video_id, lang)
    sentence, before, after = _sentence_context(video_id, req.sentence_idx, prof["code"])

    text = " ".join((req.text or "").split()) or sentence
    if len(text) > dictionary_module.MAX_BREAKDOWN_CHARS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"That selection is too long to break down ({len(text)} characters, "
                f"limit {dictionary_module.MAX_BREAKDOWN_CHARS})."
            ),
        )

    # Keyed by the text as well as the index, because a selection has no index
    # of its own: several selections inside one line each get their own entry,
    # and the whole-sentence breakdown is a fourth one beside them.
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]
    return _cached_explain(
        video_id,
        prof["code"],
        BREAKDOWN_KEY,
        f"{req.sentence_idx}:{digest}",
        lambda: dictionary_module.break_down(text, sentence, before, after, prof),
    )


@app.post("/api/videos/{video_id}/explain/flush")
def flush_explanations(video_id: str, lang: str | None = None):
    prof = _profile(video_id, lang)
    with cache.lock:
        for key in EXPLAIN_KEYS:
            cache.delete_json(video_id, key, prof["code"])
    return {"status": "ok"}


@app.post("/api/videos/{video_id}/chat")
def chat(video_id: str, req: ChatRequest, lang: str | None = None):
    """Answer a question about the whole video.

    Deliberately uncached and stateless: the conversation lives in the browser,
    so there is nothing here for the cache-key versioning above to invalidate.
    The transcript is read server-side rather than posted up, since the client
    already holds it and re-uploading 185KB per message would be pure waste.
    """
    prof = _profile(video_id, lang)
    sentences = _modified_sentences(video_id, prof["code"])
    try:
        return chat_module.answer(sentences, [m.model_dump() for m in req.messages], prof)
    except chat_module.ChatError as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@app.get("/api/videos/{video_id}/recommendations")
def get_recommendations(video_id: str, lang: str | None = None):
    """Cached shadowing scores, if a scoring pass has already run.

    Never calls the LLM: scoring a long transcript is the most expensive thing
    the app does, so it stays an explicit choice rather than a cost paid on
    every video load.
    """
    scores = cache.read_json(video_id, RECOMMEND_KEY, _lang(video_id, lang))
    if scores is None:
        return {"generated": False, "scores": []}
    return {"generated": True, "scores": scores}


@app.post("/api/videos/{video_id}/recommendations")
def build_recommendations(video_id: str, force: bool = False, lang: str | None = None):
    prof = _profile(video_id, lang)
    lang = prof["code"]

    if force:
        cache.delete_json(video_id, RECOMMEND_KEY, lang)

    with cache.lock:
        scores = cache.read_json(video_id, RECOMMEND_KEY, lang)
    if scores is not None:
        return {"generated": True, "scores": scores}

    sentences = _modified_sentences(video_id, lang)
    try:
        scores = recommender.score_sentences(sentences, prof)
    except recommender.RecommendError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    with cache.lock:
        cache.write_json(video_id, RECOMMEND_KEY, scores, lang)
    return {"generated": True, "scores": scores}


_AUDIO_HEADERS = {"Cache-Control": "public, max-age=31536000, immutable"}


@app.post("/api/videos/{video_id}/tts")
def sentence_tts(video_id: str, req: SentenceTTSRequest, lang: str | None = None):
    """Speak one transcript sentence.

    Served as a file rather than the browser's speechSynthesis because that
    only speaks languages the user's machine has voices for, which for most
    people is one or two of them.
    """
    prof = _profile(video_id, lang)
    sentences = _modified_sentences(video_id, prof["code"])
    if req.sentence_idx < 0 or req.sentence_idx >= len(sentences):
        raise HTTPException(status_code=400, detail="sentence_idx out of range")
    try:
        path = tts.sentence_audio(
            video_id,
            prof["code"],
            req.sentence_idx,
            sentences[req.sentence_idx]["text"],
            req.voice,
            prof,
            req.slow,
        )
    except tts.TTSError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    return FileResponse(path, media_type="audio/mpeg", headers=_AUDIO_HEADERS)


@app.post("/api/tts/word")
def word_tts(req: WordTTSRequest, lang: str = languages.DEFAULT_LANG):
    """Speak a word on its own, shared across every video.

    The video's own audio is the better model for a whole sentence, but it can
    never give you one character in isolation -- which is exactly what you need
    to hear a Mandarin tone clearly.
    """
    if not languages.is_known(lang):
        raise HTTPException(status_code=400, detail=f"Unsupported language: {lang}")
    prof = languages.profile(lang)
    try:
        path = tts.word_audio(lang, req.text, req.voice, prof, req.slow)
    except tts.TTSError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    return FileResponse(path, media_type="audio/mpeg", headers=_AUDIO_HEADERS)


@app.get("/api/videos/{video_id}/bookmarks")
def get_bookmarks(video_id: str, lang: str | None = None):
    return cache.read_json(video_id, "bookmarks", _lang(video_id, lang)) or []


@app.post("/api/videos/{video_id}/bookmarks")
def add_bookmark(video_id: str, req: BookmarkRequest, lang: str | None = None):
    lang = _lang(video_id, lang)
    bookmarks = cache.read_json(video_id, "bookmarks", lang) or []
    if req.sentence_idx not in bookmarks:
        bookmarks.append(req.sentence_idx)
        cache.write_json(video_id, "bookmarks", bookmarks, lang)
    return bookmarks


@app.delete("/api/videos/{video_id}/bookmarks/{sentence_idx}")
def remove_bookmark(video_id: str, sentence_idx: int, lang: str | None = None):
    lang = _lang(video_id, lang)
    bookmarks = cache.read_json(video_id, "bookmarks", lang) or []
    bookmarks = [b for b in bookmarks if b != sentence_idx]
    cache.write_json(video_id, "bookmarks", bookmarks, lang)
    return bookmarks


app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")


def _open_browser_when_ready(url: str):
    import urllib.request

    for _ in range(50):
        try:
            urllib.request.urlopen(url, timeout=0.5)
            webbrowser.open(url)
            return
        except Exception:
            time.sleep(0.2)
    webbrowser.open(url)


if __name__ == "__main__":
    import uvicorn

    port = 8000
    url = f"http://localhost:{port}"
    threading.Thread(target=_open_browser_when_ready, args=(url,), daemon=True).start()
    uvicorn.run(app, host="127.0.0.1", port=port)
