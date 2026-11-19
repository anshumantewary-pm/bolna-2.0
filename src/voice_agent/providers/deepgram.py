"""Deepgram live streaming speech-to-text over WebSocket.

API: wss://api.deepgram.com/v1/listen, header ``Authorization: Token <key>``, raw audio
sent as binary frames, JSON ``Results`` / ``UtteranceEnd`` / ``SpeechStarted`` messages
back, ``{"type": "CloseStream"}`` to finish.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from urllib.parse import urlencode

from .base import SpeechStarted, SttEvent, Transcript, UtteranceEnd

DEEPGRAM_URL = "wss://api.deepgram.com/v1/listen"


class SttError(RuntimeError):
    pass


@dataclass(frozen=True)
class DeepgramOptions:
    model: str = "nova-3"
    language: str = "en"
    sample_rate: int = 16000
    encoding: str = "linear16"
    # Silence (ms) after speech before Deepgram marks speech_final.
    endpointing_ms: int = 300
    # Backstop gap (ms) that ends an utterance when endpointing doesn't fire (noisy lines).
    utterance_end_ms: int = 1000
    # Words to recognise reliably: product names, order-ID formats, street names.
    keyterms: tuple[str, ...] = ()
    url: str = DEEPGRAM_URL

    def query(self) -> list[tuple[str, str]]:
        params = [
            ("model", self.model),
            ("language", self.language),
            ("encoding", self.encoding),
            ("sample_rate", str(self.sample_rate)),
            ("channels", "1"),
            ("interim_results", "true"),
            ("punctuate", "true"),
            ("smart_format", "true"),
            ("vad_events", "true"),
            ("endpointing", str(self.endpointing_ms)),
            ("utterance_end_ms", str(self.utterance_end_ms)),
        ]
        params += [("keyterm", term) for term in self.keyterms]
        return params

    def full_url(self) -> str:
        return f"{self.url}?{urlencode(self.query())}"


def parse_message(raw: str) -> SttEvent | None:
    msg = json.loads(raw)
    kind = msg.get("type")
    if kind == "Results":
        alternatives = (msg.get("channel") or {}).get("alternatives") or [{}]
        text = (alternatives[0].get("transcript") or "").strip()
        speech_final = bool(msg.get("speech_final"))
        if not text and not speech_final:
            return None
        return Transcript(text=text, is_final=bool(msg.get("is_final")), speech_final=speech_final)
    if kind == "UtteranceEnd":
        return UtteranceEnd()
    if kind == "SpeechStarted":
        return SpeechStarted()
    if kind == "Error":
        raise SttError(msg.get("description") or msg.get("message") or "Deepgram error")
    return None  # Metadata, ConfigureSuccess, ...


class DeepgramSTT:
    def __init__(self, api_key: str, options: DeepgramOptions | None = None) -> None:
        self.api_key = api_key
        self.options = options or DeepgramOptions()

    async def transcribe(self, audio: AsyncIterator[bytes]) -> AsyncIterator[SttEvent]:
        from websockets.asyncio.client import connect

        headers = {"Authorization": f"Token {self.api_key}"}
        async with connect(self.options.full_url(), additional_headers=headers) as ws:

            async def send_audio() -> None:
                try:
                    async for frame in audio:
                        await ws.send(frame)
                finally:
                    try:
                        await ws.send(json.dumps({"type": "CloseStream"}))
                    except Exception:
                        pass  # connection already closed

            sender = asyncio.create_task(send_audio())
            try:
                async for raw in ws:
                    if isinstance(raw, bytes):
                        continue
                    event = parse_message(raw)
                    if event is not None:
                        yield event
            finally:
                sender.cancel()
