"""Stand-in providers for local development and tests: no audio, no vendor accounts."""

from __future__ import annotations

import asyncio
import sys
from collections.abc import AsyncIterator
from typing import TextIO

from .base import AudioChunk


class WordTTS:
    """Pretend TTS: one chunk per word, the 'audio' is the word's bytes."""

    def __init__(self, first_chunk_delay_s: float = 0.0) -> None:
        self.first_chunk_delay_s = first_chunk_delay_s

    async def synthesize(self, text: str) -> AsyncIterator[AudioChunk]:
        if self.first_chunk_delay_s:
            await asyncio.sleep(self.first_chunk_delay_s)
        for word in text.split():
            yield AudioChunk(audio=word.encode(), text=word)


class ConsoleAudio:
    """Prints words at speaking pace (~180 words a minute), so you can interrupt mid-reply."""

    def __init__(self, words_per_second: float = 3.0, out: TextIO = sys.stdout) -> None:
        self.delay_s = 1.0 / words_per_second
        self.out = out
        self.at_line_start = True

    async def play(self, chunk: AudioChunk) -> None:
        if self.at_line_start:
            self.out.write("agent> ")
            self.at_line_start = False
        self.out.write(chunk.text + " ")
        self.out.flush()
        await asyncio.sleep(self.delay_s)

    async def stop(self) -> None:
        if not self.at_line_start:
            self.out.write("[interrupted]\n")
            self.out.flush()
        self.at_line_start = True

    def end_line(self) -> None:
        if not self.at_line_start:
            self.out.write("\n")
            self.out.flush()
        self.at_line_start = True


class RecordingAudio:
    """Records what was played; for tests."""

    def __init__(self, delay_s: float = 0.0) -> None:
        self.delay_s = delay_s
        self.played: list[str] = []
        self.stops = 0

    async def play(self, chunk: AudioChunk) -> None:
        await asyncio.sleep(self.delay_s)
        self.played.append(chunk.text)

    async def stop(self) -> None:
        self.stops += 1
