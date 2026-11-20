"""Browser speech mode: the browser does speech-to-text and text-to-speech.

Only Claude credentials are needed on the server. Chrome/Edge's Web Speech API
recognises the caller and ``speechSynthesis`` speaks the agent's lines; the server
runs the same session, turn detection and Claude agent as the vendor mode.

Protocol additions on ``/ws`` (all JSON):
- browser -> server: ``{"type": "stt", "text", "is_final", "speech_final"}`` for each
  recognition result; ``{"type": "spoken", "id"}`` when a line finished playing;
  ``{"type": "barge_in"}`` when the caller interrupts with the button.
- server -> browser: ``{"type": "speak", "id", "text"}`` to say a line; ``clear`` to
  stop speaking (same as vendor mode).

Speech is tracked one sentence at a time: a sentence counts as heard only once the
browser reports it finished, so after a barge-in the agent is told the caller heard
the sentences before the one that was cut off.
"""

from __future__ import annotations

import asyncio
import itertools
from collections.abc import AsyncIterator
from typing import Any

from ..providers.base import AudioChunk, SttEvent, Transcript

# Speaking rate used to bound how long we wait for a "spoken" ack (~150 wpm is typical).
_WORDS_PER_S = 2.5


class BrowserSpeechTTS:
    """'Synthesizes' a line as one text-only chunk; the browser speaks it."""

    async def synthesize(self, text: str) -> AsyncIterator[AudioChunk]:
        yield AudioChunk(audio=b"", text=text)


class BrowserSpeechOutput:
    """Asks the browser to speak each line and waits until it reports it was spoken."""

    def __init__(self, outbox: asyncio.Queue[Any], slack_s: float = 5.0) -> None:
        self.outbox = outbox
        self.slack_s = slack_s
        self._ids = itertools.count(1)
        self._pending: dict[int, asyncio.Future[None]] = {}

    async def play(self, chunk: AudioChunk) -> None:
        if not chunk.text.strip():
            return
        line_id = next(self._ids)
        done = asyncio.get_running_loop().create_future()
        self._pending[line_id] = done
        self.outbox.put_nowait({"type": "speak", "id": line_id, "text": chunk.text})
        # Never hang a turn on a lost ack: allow ~3x normal speaking time plus slack.
        timeout = 3 * len(chunk.text.split()) / _WORDS_PER_S + self.slack_s
        try:
            await asyncio.wait_for(asyncio.shield(done), timeout)
        except asyncio.TimeoutError:
            pass
        finally:
            self._pending.pop(line_id, None)

    def spoken(self, line_id: Any) -> None:
        future = self._pending.get(line_id) if isinstance(line_id, int) else None
        if future is not None and not future.done():
            future.set_result(None)

    async def stop(self) -> None:
        self.outbox.put_nowait({"type": "clear"})
        for future in self._pending.values():
            if not future.done():
                future.cancel()
        self._pending.clear()


class BrowserSTT:
    """Turns ``stt`` messages from the page into the session's STT events."""

    def __init__(self) -> None:
        self._events: asyncio.Queue[SttEvent | None] = asyncio.Queue()

    def feed(self, message: dict[str, Any]) -> None:
        text = str(message.get("text") or "").strip()
        speech_final = bool(message.get("speech_final"))
        if text or speech_final:
            self._events.put_nowait(
                Transcript(text=text, is_final=bool(message.get("is_final")), speech_final=speech_final)
            )

    def close(self) -> None:
        self._events.put_nowait(None)

    async def transcribe(self, audio: AsyncIterator[bytes]) -> AsyncIterator[SttEvent]:
        # Browser speech sends no audio; ``audio`` is ignored.
        while (event := await self._events.get()) is not None:
            yield event
