import json
import threading
from pathlib import Path

import languages

CACHE_ROOT = Path(__file__).resolve().parent.parent / "cache"

# Routes are sync `def`, so FastAPI runs them on a threadpool and two lookups
# can interleave a read-modify-write of the same cache file. The explain routes
# hold this around each half, so a concurrent lookup can't drop an entry.
lock = threading.Lock()

# Dirs whose name starts with this are shared stores, not videos: currently
# just the isolated-word TTS cache, which is keyed by text rather than by video.
_RESERVED = "_"


def _lang_dirs() -> list[Path]:
    """Existing per-language dirs, newest-touched first."""
    if not CACHE_ROOT.exists():
        return []
    dirs = [
        d
        for d in CACHE_ROOT.iterdir()
        if d.is_dir() and not d.name.startswith(_RESERVED) and languages.is_known(d.name)
    ]
    return sorted(dirs, key=lambda p: p.stat().st_mtime, reverse=True)


def resolve_dir(video_id: str, lang: str | None = None) -> Path | None:
    """The dir holding this video, or None if it has never been cached.

    Three steps, cheapest first:
      1. the language the caller named -- the normal path, since the client
         always knows which language it loaded the video under;
      2. a scan of the language dirs, for a caller that didn't say;
      3. the flat pre-language layout, so videos cached before this existed
         keep working in place. That fallback IS the migration: there is no
         move script to get wrong and nothing breaks if the move never happens.
    """
    if lang:
        candidate = CACHE_ROOT / lang / video_id
        if candidate.is_dir():
            return candidate
    else:
        for d in _lang_dirs():
            candidate = d / video_id
            if candidate.is_dir():
                return candidate

    legacy = CACHE_ROOT / video_id
    return legacy if legacy.is_dir() else None


def video_dir(video_id: str, lang: str | None = None) -> Path:
    """Like resolve_dir, but creates the dir when it doesn't exist yet.

    Only ever reached from a write, and every write path knows its language,
    so a missing lang here means a caller bug rather than something to guess at.
    """
    existing = resolve_dir(video_id, lang)
    if existing is not None:
        return existing
    if not lang:
        raise ValueError(f"No cache dir for {video_id} and no language given.")
    d = CACHE_ROOT / lang / video_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def read_json(video_id: str, name: str, lang: str | None = None):
    d = resolve_dir(video_id, lang)
    if d is None:
        return None
    path = d / f"{name}.json"
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_json(video_id: str, name: str, data, lang: str | None = None) -> None:
    path = video_dir(video_id, lang) / f"{name}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def delete_json(video_id: str, name: str, lang: str | None = None) -> None:
    d = resolve_dir(video_id, lang)
    if d is None:
        return
    path = d / f"{name}.json"
    if path.exists():
        path.unlink()


def lang_of(video_id: str, lang: str | None = None) -> str:
    """The language a cached video was loaded under.

    A video cached before languages existed has no `lang` in its meta and sits
    in the flat layout; English is the only thing it can be.
    """
    if lang and languages.is_known(lang):
        return lang
    meta = read_json(video_id, "meta", lang)
    if meta and languages.is_known(meta.get("lang")):
        return meta["lang"]
    return languages.DEFAULT_LANG


def _video_dirs() -> list[Path]:
    """Every cached video dir: per-language ones plus any legacy flat ones."""
    if not CACHE_ROOT.exists():
        return []
    found = []
    for entry in CACHE_ROOT.iterdir():
        if not entry.is_dir() or entry.name.startswith(_RESERVED):
            continue
        if languages.is_known(entry.name):
            found.extend(d for d in entry.iterdir() if d.is_dir())
        else:
            found.append(entry)
    return sorted(found, key=lambda p: p.stat().st_mtime, reverse=True)


def list_videos():
    """Cached videos, newest first, each meta carrying the language it belongs to.

    The client groups the history dropdown on that field, and uses it to switch
    the language selector when a video is picked -- which is what stops a
    cached video ever being reopened under the wrong profile.
    """
    videos = []
    for d in _video_dirs():
        path = d / "meta.json"
        if not path.exists():
            continue
        with open(path, "r", encoding="utf-8") as f:
            meta = json.load(f)
        if not languages.is_known(meta.get("lang")):
            parent = d.parent.name
            meta["lang"] = parent if languages.is_known(parent) else languages.DEFAULT_LANG
        videos.append(meta)
    return videos
