"""Local microphone and speaker for trying the agent by voice on your own computer.

16 kHz, 16-bit mono PCM in 20 ms frames, matching Deepgram linear16 input and
ElevenLabs pcm_16000 output. Use headphones: with speakers the microphone hears the
agent and it will interrupt itself (no echo cancellation here; WebRTC adds that later).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from .base import AudioChunk

FRAME_MS = 20


class MicInput:
    def __init__(self, sample_rate: int = 16000) -> None:
        self.sample_rate = sample_rate

    async def frames(self) -> AsyncIterator[bytes]:
        import sounddevice as sd

        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[bytes] = asyncio.Queue()

        def callback(indata: Any, frames: int, time: Any, status: Any) -> None:
            loop.call_soon_threadsafe(queue.put_nowait, bytes(indata))

        with sd.RawInputStream(
            samplerate=self.sample_rate,
            channels=1,
            dtype="int16",
            blocksize=self.sample_rate * FRAME_MS // 1000,
            callback=callback,
        ):
            while True:
                yield await queue.get()


class SpeakerOutput:
    def __init__(self, sample_rate: int = 16000) -> None:
        import sounddevice as sd

        self._stream = sd.RawOutputStream(samplerate=sample_rate, channels=1, dtype="int16")
        self._stream.start()
        self._odd_byte = b""  # 16-bit samples can be split across network chunks

    async def play(self, chunk: AudioChunk) -> None:
        data = self._odd_byte + chunk.audio
        cut = len(data) - len(data) % 2
        data, self._odd_byte = data[:cut], data[cut:]
        if data:
            # Blocking write returns once the audio is queued in the device buffer.
            await asyncio.to_thread(self._stream.write, data)

    async def stop(self) -> None:
        self._odd_byte = b""
        # abort() drops audio still in the buffer, so the agent goes quiet immediately.
        await asyncio.to_thread(self._stream.abort)
        await asyncio.to_thread(self._stream.start)

    def close(self) -> None:
        self._stream.close()
