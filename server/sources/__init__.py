"""Where transcripts come from.

A source turns a video id plus a language profile into caption chunks:

    fetch(video_id, prof) -> [{"index": int, "text": str,
                               "start": float, "duration": float}]

Only YouTube captions exist today. The interface is here so that a second
source -- Whisper over downloaded audio, or pasted text -- can be added as one
new file without touching main.py, which matters because a lot of Chinese and
Vietnamese content ships with burned-in subtitles and no caption track at all.
"""

from sources import youtube_captions

SOURCES = {
    "youtube_captions": youtube_captions,
}

DEFAULT_SOURCE = "youtube_captions"


def get(name: str | None = None):
    return SOURCES[name or DEFAULT_SOURCE]
