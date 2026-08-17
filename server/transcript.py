import re
import httpx

import sources
from errors import TranscriptError  # noqa: F401  (re-exported for callers)

_ID_RE = re.compile(r"(?:v=|youtu\.be/|embed/|shorts/)([A-Za-z0-9_-]{11})")


def extract_video_id(url_or_id: str) -> str:
    url_or_id = url_or_id.strip()
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", url_or_id):
        return url_or_id
    match = _ID_RE.search(url_or_id)
    if match:
        return match.group(1)
    raise TranscriptError(f"Could not extract a YouTube video ID from: {url_or_id}")


def fetch_title(video_id: str) -> str:
    try:
        resp = httpx.get(
            "https://www.youtube.com/oembed",
            params={"url": f"https://www.youtube.com/watch?v={video_id}", "format": "json"},
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json().get("title", video_id)
    except Exception:
        return video_id


def fetch_raw_transcript(video_id: str, prof: dict, source: str | None = None) -> list[dict]:
    """Caption chunks for this video, from whichever source is configured."""
    return sources.get(source).fetch(video_id, prof)
