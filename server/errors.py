"""Errors shared across modules.

TranscriptError lives here rather than in transcript.py so that a source can
raise it without importing the module that imports the sources.
"""


class TranscriptError(Exception):
    pass
