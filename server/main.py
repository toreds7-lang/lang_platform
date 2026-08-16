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
import transcript
import segmenter
import dictionary as dictionary_module
import recommender
import chat as chat_module

app = FastAPI()

STATIC_DIR = ROOT / "static"

# Bumped when the segmenter's output format changes, so stale caches are ignored.
MODIFIED_KEY = "modified_v2"

# Same versioning idea for the explanation caches: bump when a response shape
# changes, so entries written by an older format are never read back.
DICT_KEY = "dictionary_v2"
SENTENCE_KEY = "sentence_v1"
GRAMMAR_KEY = "grammar_v1"
RECOMMEND_KEY = "recommend_v1"

# Every explanation is keyed by sentence index, which re-segmentation shifts.
EXPLAIN_KEYS = (DICT_KEY, SENTENCE_KEY, GRAMMAR_KEY)

# Everything keyed by sentence index, so everything re-segmentation invalidates.
# Wider than EXPLAIN_KEYS on purpose: "clear explanations" should not throw away
# a scoring pass that costs a call per 50 sentences to rebuild.
INDEXED_KEYS = EXPLAIN_KEYS + (RECOMMEND_KEY,)


class LoadVideoRequest(BaseModel):
    url_or_id: str


class DictionaryRequest(BaseModel):
    word: str
    sentence_idx: int


class SentenceRequest(BaseModel):
    sentence_idx: int


class BookmarkRequest(BaseModel):
    sentence_idx: int


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage]


@app.get("/api/videos")
def list_videos():
    return cache.list_videos()


@app.post("/api/videos/load")
def load_video(req: LoadVideoRequest):
    try:
        video_id = transcript.extract_video_id(req.url_or_id)
    except transcript.TranscriptError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    meta = cache.read_json(video_id, "meta")
    raw = cache.read_json(video_id, "raw")

    if raw is None:
        try:
            raw = transcript.fetch_raw_transcript(video_id)
        except transcript.TranscriptError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        cache.write_json(video_id, "raw", raw)

    if meta is None:
        title = transcript.fetch_title(video_id)
        meta = {
            "video_id": video_id,
            "title": title,
            "cached_at": datetime.now(timezone.utc).isoformat(),
        }
        cache.write_json(video_id, "meta", meta)

    return {"meta": meta, "raw": raw}


@app.get("/api/videos/{video_id}/transcript")
def get_transcript(video_id: str, mode: str = "raw"):
    if mode == "raw":
        raw = cache.read_json(video_id, "raw")
        if raw is None:
            raise HTTPException(status_code=404, detail="Video not loaded yet.")
        return {"mode": "raw", "sentences": raw}

    if mode == "modified":
        modified = cache.read_json(video_id, MODIFIED_KEY)
        if modified is None:
            raw = cache.read_json(video_id, "raw")
            if raw is None:
                raise HTTPException(status_code=404, detail="Video not loaded yet.")
            modified = segmenter.segment_into_sentences(raw)
            cache.write_json(video_id, MODIFIED_KEY, modified)
        return {"mode": "modified", "sentences": modified}

    raise HTTPException(status_code=400, detail="mode must be 'raw' or 'modified'")


@app.post("/api/videos/{video_id}/regenerate")
def regenerate(video_id: str, target: str = "modified"):
    if target == "modified":
        # Bookmarks and explanations are keyed by sentence index, which
        # re-segmentation invalidates.
        cache.delete_json(video_id, MODIFIED_KEY)
        for key in INDEXED_KEYS:
            cache.delete_json(video_id, key)
        cache.delete_json(video_id, "bookmarks")
        raw = cache.read_json(video_id, "raw")
        if raw is None:
            raise HTTPException(status_code=404, detail="Video not loaded yet.")
        modified = segmenter.segment_into_sentences(raw)
        cache.write_json(video_id, MODIFIED_KEY, modified)
        return {"mode": "modified", "sentences": modified}

    if target == "raw":
        cache.delete_json(video_id, "raw")
        cache.delete_json(video_id, MODIFIED_KEY)
        for key in INDEXED_KEYS:
            cache.delete_json(video_id, key)
        cache.delete_json(video_id, "bookmarks")
        try:
            raw = transcript.fetch_raw_transcript(video_id)
        except transcript.TranscriptError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        cache.write_json(video_id, "raw", raw)
        return {"mode": "raw", "sentences": raw}

    raise HTTPException(status_code=400, detail="target must be 'raw' or 'modified'")


def _modified_sentences(video_id: str):
    """The sentence list, segmenting and caching it on first use."""
    modified = cache.read_json(video_id, MODIFIED_KEY)
    if modified is None:
        raw = cache.read_json(video_id, "raw")
        if raw is None:
            raise HTTPException(status_code=404, detail="Video not loaded yet.")
        modified = segmenter.segment_into_sentences(raw)
        cache.write_json(video_id, MODIFIED_KEY, modified)
    return modified


def _sentence_context(video_id: str, sentence_idx: int):
    modified = _modified_sentences(video_id)

    if sentence_idx < 0 or sentence_idx >= len(modified):
        raise HTTPException(status_code=400, detail="sentence_idx out of range")

    sentence = modified[sentence_idx]["text"]
    before = [s["text"] for s in modified[max(0, sentence_idx - 2) : sentence_idx]]
    after = [s["text"] for s in modified[sentence_idx + 1 : sentence_idx + 3]]
    return sentence, before, after


def _cached_explain(video_id: str, cache_key: str, entry_key: str, produce):
    """Return a cached explanation, or produce, store and return a fresh one."""
    with cache.lock:
        entries = cache.read_json(video_id, cache_key) or {}
        if entry_key in entries:
            return entries[entry_key]

    try:
        result = produce()
    except dictionary_module.ExplainError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    with cache.lock:
        entries = cache.read_json(video_id, cache_key) or {}
        entries[entry_key] = result
        cache.write_json(video_id, cache_key, entries)
    return result


@app.post("/api/videos/{video_id}/explain/word")
def explain_word(video_id: str, req: DictionaryRequest):
    sentence, before, after = _sentence_context(video_id, req.sentence_idx)
    return _cached_explain(
        video_id,
        DICT_KEY,
        f"{req.sentence_idx}:{req.word.lower()}",
        lambda: dictionary_module.explain_word(req.word, sentence, before, after),
    )


@app.post("/api/videos/{video_id}/explain/sentence")
def explain_sentence(video_id: str, req: SentenceRequest):
    sentence, before, after = _sentence_context(video_id, req.sentence_idx)
    return _cached_explain(
        video_id,
        SENTENCE_KEY,
        str(req.sentence_idx),
        lambda: dictionary_module.explain_sentence(sentence, before, after),
    )


@app.post("/api/videos/{video_id}/explain/grammar")
def explain_grammar(video_id: str, req: SentenceRequest):
    sentence, before, after = _sentence_context(video_id, req.sentence_idx)
    return _cached_explain(
        video_id,
        GRAMMAR_KEY,
        str(req.sentence_idx),
        lambda: dictionary_module.explain_grammar(sentence, before, after),
    )


@app.post("/api/videos/{video_id}/explain/flush")
def flush_explanations(video_id: str):
    with cache.lock:
        for key in EXPLAIN_KEYS:
            cache.delete_json(video_id, key)
    return {"status": "ok"}


@app.post("/api/videos/{video_id}/chat")
def chat(video_id: str, req: ChatRequest):
    """Answer a question about the whole video.

    Deliberately uncached and stateless: the conversation lives in the browser,
    so there is nothing here for the cache-key versioning above to invalidate.
    The transcript is read server-side rather than posted up, since the client
    already holds it and re-uploading 185KB per message would be pure waste.
    """
    sentences = _modified_sentences(video_id)
    try:
        return chat_module.answer(sentences, [m.model_dump() for m in req.messages])
    except chat_module.ChatError as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@app.get("/api/videos/{video_id}/recommendations")
def get_recommendations(video_id: str):
    """Cached shadowing scores, if a scoring pass has already run.

    Never calls the LLM: scoring a long transcript is the most expensive thing
    the app does, so it stays an explicit choice rather than a cost paid on
    every video load.
    """
    scores = cache.read_json(video_id, RECOMMEND_KEY)
    if scores is None:
        return {"generated": False, "scores": []}
    return {"generated": True, "scores": scores}


@app.post("/api/videos/{video_id}/recommendations")
def build_recommendations(video_id: str, force: bool = False):
    if force:
        cache.delete_json(video_id, RECOMMEND_KEY)

    with cache.lock:
        scores = cache.read_json(video_id, RECOMMEND_KEY)
    if scores is not None:
        return {"generated": True, "scores": scores}

    sentences = _modified_sentences(video_id)
    try:
        scores = recommender.score_sentences(sentences)
    except recommender.RecommendError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    with cache.lock:
        cache.write_json(video_id, RECOMMEND_KEY, scores)
    return {"generated": True, "scores": scores}


@app.get("/api/videos/{video_id}/bookmarks")
def get_bookmarks(video_id: str):
    return cache.read_json(video_id, "bookmarks") or []


@app.post("/api/videos/{video_id}/bookmarks")
def add_bookmark(video_id: str, req: BookmarkRequest):
    bookmarks = cache.read_json(video_id, "bookmarks") or []
    if req.sentence_idx not in bookmarks:
        bookmarks.append(req.sentence_idx)
        cache.write_json(video_id, "bookmarks", bookmarks)
    return bookmarks


@app.delete("/api/videos/{video_id}/bookmarks/{sentence_idx}")
def remove_bookmark(video_id: str, sentence_idx: int):
    bookmarks = cache.read_json(video_id, "bookmarks") or []
    bookmarks = [b for b in bookmarks if b != sentence_idx]
    cache.write_json(video_id, "bookmarks", bookmarks)
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
