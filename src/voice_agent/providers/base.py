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
    is_final: bool


class SpeechToText(Protocol):
    def transcribe(self, audio: AsyncIterator[bytes]) -> AsyncIterator[Transcript]:
        """Stream audio frames in; yield partial and final transcripts."""
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
