"""Vendor-neutral interfaces for the speech stages (PRD section 5, "Portability").

Real vendor adapters (STT, TTS, telephony/WebRTC audio) implement these after the
vendor bake-off; until then the stand-ins in ``fake.py`` are used.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class AudioChunk:
    audio: bytes
    # The words this audio covers, so the session knows what the caller actually heard.
    text: str


@dataclass(frozen=True)
class Transcript:
    text: str
    # Final for this stretch of audio; it won't be revised.
    is_final: bool
    # The STT's endpointing thinks the caller stopped talking here.
    speech_final: bool = False


@dataclass(frozen=True)
class SpeechStarted:
    """Voice activity detected (may be noise; confirm with a transcript)."""


@dataclass(frozen=True)
class UtteranceEnd:
    """A gap in speech long enough to end the utterance, even if endpointing missed it."""


SttEvent = Transcript | SpeechStarted | UtteranceEnd


class SpeechToText(Protocol):
    def transcribe(self, audio: AsyncIterator[bytes]) -> AsyncIterator[SttEvent]:
        """Stream audio frames in; yield transcripts and speech events."""
        ...


class TextToSpeech(Protocol):
    def synthesize(self, text: str) -> AsyncIterator[AudioChunk]:
        """Yield audio for one sentence or clause as soon as it is ready."""
        ...


class AudioOutput(Protocol):
    async def play(self, chunk: AudioChunk) -> None:
        """Return once the chunk has been played to the caller."""
        ...

    async def stop(self) -> None:
        """Stop playback immediately (barge-in)."""
        ...
