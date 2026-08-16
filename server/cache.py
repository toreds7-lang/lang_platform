import json
import threading
from pathlib import Path

CACHE_ROOT = Path(__file__).resolve().parent.parent / "cache"

# Routes are sync `def`, so FastAPI runs them on a threadpool and two lookups
# can interleave a read-modify-write of the same cache file. The explain routes
# hold this around each half, so a concurrent lookup can't drop an entry.
lock = threading.Lock()


def video_dir(video_id: str) -> Path:
    d = CACHE_ROOT / video_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def read_json(video_id: str, name: str):
    path = video_dir(video_id) / f"{name}.json"
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_json(video_id: str, name: str, data) -> None:
    path = video_dir(video_id) / f"{name}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def delete_json(video_id: str, name: str) -> None:
    path = video_dir(video_id) / f"{name}.json"
    if path.exists():
        path.unlink()


def list_videos():
    if not CACHE_ROOT.exists():
        return []
    videos = []
    for d in sorted(CACHE_ROOT.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if not d.is_dir():
            continue
        meta = read_json(d.name, "meta")
        if meta:
            videos.append(meta)
    return videos
