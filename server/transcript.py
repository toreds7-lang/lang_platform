import re
import httpx
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import (
    TranscriptsDisabled,
    NoTranscriptFound,
    VideoUnavailable,
    RequestBlocked,
    IpBlocked,
)

_ID_RE = re.compile(r"(?:v=|youtu\.be/|embed/|shorts/)([A-Za-z0-9_-]{11})")


class TranscriptError(Exception):
    pass


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


def fetch_raw_transcript(video_id: str):
    try:
        api = YouTubeTranscriptApi()
        transcript_list = api.list(video_id)
        try:
            transcript = transcript_list.find_transcript(["en", "en-US", "en-GB"])
        except NoTranscriptFound:
            transcript = transcript_list.find_generated_transcript(["en", "en-US", "en-GB"])
        fetched = transcript.fetch()
        raw_items = fetched.to_raw_data()
    except TranscriptsDisabled:
        raise TranscriptError("Transcripts are disabled for this video.")
    except NoTranscriptFound:
        raise TranscriptError("No English transcript is available for this video.")
    except VideoUnavailable:
        raise TranscriptError("This video is unavailable.")
    except (RequestBlocked, IpBlocked):
        raise TranscriptError("YouTube blocked the transcript request from this network. Try again later.")
    except Exception as exc:
        raise TranscriptError(f"Failed to fetch transcript: {exc}")

    chunks = []
    for i, item in enumerate(raw_items):
        chunks.append(
            {
                "index": i,
                "text": item["text"].replace("\n", " ").strip(),
                "start": round(item["start"], 3),
                "duration": round(item.get("duration", 0.0), 3),
            }
        )
    return chunks
