"""ElevenLabs streaming text-to-speech with character timestamps.

API: POST /v1/text-to-speech/{voice_id}/stream/with-timestamps, header ``xi-api-key``,
``output_format=pcm_16000`` for raw 16-bit mono PCM. The response streams JSON objects
(one per line) with ``audio_base64`` and an ``alignment`` of the characters it covers,
which tells us exactly which words the caller has heard when they barge in.
"""

from __future__ import annotations

import base64
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from .base import AudioChunk

ELEVENLABS_URL = "https://api.elevenlabs.io"


class TtsError(RuntimeError):
    pass


@dataclass(frozen=True)
class ElevenLabsOptions:
    voice_id: str
    # Flash: ~100 ms median model latency, built for real-time agents.
    model_id: str = "eleven_flash_v2_5"
    sample_rate: int = 16000
    base_url: str = ELEVENLABS_URL


def parse_line(line: str) -> tuple[bytes, dict[str, Any] | None] | None:
    """One streamed line -> (pcm audio, alignment). Tolerates SSE-style 'data:' prefixes."""
    line = line.strip()
    if line.startswith("data:"):
        line = line[5:].strip()
    if not line:
        return None
    obj = json.loads(line)
    audio = base64.b64decode(obj.get("audio_base64") or "")
    return audio, obj.get("alignment")


class WordAligner:
    """Turn per-chunk character alignments into the words each audio chunk completes."""

    def __init__(self) -> None:
        self._current = ""

    def feed(self, alignment: dict[str, Any] | None) -> list[str]:
        if not alignment:
            return []
        words: list[str] = []
        for ch in alignment.get("characters") or []:
            if ch.isspace():
                if self._current:
                    words.append(self._current)
                    self._current = ""
            else:
                self._current += ch
        return words

    def flush(self) -> str:
        word, self._current = self._current, ""
        return word


class ElevenLabsTTS:
    def __init__(self, api_key: str, options: ElevenLabsOptions, client: Any = None) -> None:
        import httpx

        self.api_key = api_key
        self.options = options
        # One pooled client per session keeps the TLS connection warm between sentences.
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(10.0, read=30.0))

    async def aclose(self) -> None:
        await self._client.aclose()

    async def synthesize(self, text: str) -> AsyncIterator[AudioChunk]:
        o = self.options
        url = f"{o.base_url}/v1/text-to-speech/{o.voice_id}/stream/with-timestamps"
        async with self._client.stream(
            "POST",
            url,
            params={"output_format": f"pcm_{o.sample_rate}"},
            headers={"xi-api-key": self.api_key},
            json={"text": text, "model_id": o.model_id},
        ) as response:
            if response.status_code >= 400:
                body = (await response.aread()).decode(errors="replace")[:300]
                raise TtsError(f"ElevenLabs {response.status_code}: {body}")
            aligner = WordAligner()
            async for line in response.aiter_lines():
                parsed = parse_line(line)
                if parsed is None:
                    continue
                audio, alignment = parsed
                yield AudioChunk(audio=audio, text=" ".join(aligner.feed(alignment)))
            if last := aligner.flush():
                yield AudioChunk(audio=b"", text=last)
