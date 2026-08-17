from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import (
    TranscriptsDisabled,
    NoTranscriptFound,
    VideoUnavailable,
    RequestBlocked,
    IpBlocked,
)

from errors import TranscriptError


def _available(transcript_list) -> str:
    """A readable list of the tracks this video really has.

    Worth the effort: "no Chinese transcript" alone leaves you guessing,
    while the list tells you immediately whether to switch language or to
    give up on the video.
    """
    seen = []
    for item in transcript_list:
        label = item.language_code
        if item.is_generated:
            label += " (auto)"
        if label not in seen:
            seen.append(label)
    return ", ".join(seen) if seen else "none"


def fetch(video_id: str, prof: dict) -> list[dict]:
    """Caption chunks for this video in the profile's language.

    Only real tracks are accepted -- manually written first, then the
    auto-generated one. YouTube's machine-TRANSLATED tracks are never
    requested: their text does not match what the speaker says, so shadowing
    one would train you to say something the audio never contains.
    """
    try:
        api = YouTubeTranscriptApi()
        transcript_list = api.list(video_id)
        try:
            transcript = transcript_list.find_transcript(prof["captions"])
        except NoTranscriptFound:
            try:
                transcript = transcript_list.find_generated_transcript(prof["captions"])
            except NoTranscriptFound:
                raise TranscriptError(
                    f"No {prof['name']} transcript for this video. "
                    f"Available: {_available(transcript_list)}."
                )
        fetched = transcript.fetch()
        raw_items = fetched.to_raw_data()
    except TranscriptError:
        raise
    except TranscriptsDisabled:
        raise TranscriptError("Transcripts are disabled for this video.")
    except NoTranscriptFound:
        raise TranscriptError(f"No {prof['name']} transcript is available for this video.")
    except VideoUnavailable:
        raise TranscriptError("This video is unavailable.")
    except (RequestBlocked, IpBlocked):
        raise TranscriptError(
            "YouTube blocked the transcript request from this network. Try again later."
        )
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
