"""Browser voice calls: one page plus a WebSocket that carries audio both ways.

Protocol on ``/ws``:
- browser -> server: binary = 16 kHz mono 16-bit PCM microphone frames (20 ms);
  text = JSON control (``{"type": "hangup"}``, ``{"type": "barge_in"}``).
- server -> browser: binary = 16 kHz mono 16-bit PCM agent audio; text = JSON events
  (``ready``, ``state``, ``caller``, ``agent_text``, ``turn_done``, ``clear``, ``error``).

Two speech modes, chosen per server and announced in ``ready``:
- ``server``: Deepgram and ElevenLabs run on the server; audio crosses the socket.
- ``browser``: the browser's own speech recognition and voice are used and only
  text crosses the socket (see ``browser_speech.py``). Needs only Claude credentials.

The browser applies echo cancellation and noise suppression to the microphone, so
speakers work. A WebRTC media server (PRD section 6) replaces this transport for
production networks and telephony; the session and providers stay the same.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from http import HTTPStatus
from importlib import resources
from typing import Any

from ..agent import ConversationAgent
from ..config import Settings
from ..providers.base import AudioChunk, SpeechToText, TextToSpeech
from ..session import VoiceSession
from ..voice import drive_session
from .browser_speech import BrowserSpeechOutput, BrowserSpeechTTS, BrowserSTT

Outgoing = bytes | dict[str, Any]


class BrowserAudioOutput:
    """Sends agent audio to the browser, paced to real time.

    ``play`` returns about when the browser has played the chunk (minus a small lead
    that keeps its buffer from running dry), so the session's record of what the
    caller heard stays accurate when they barge in.
    """

    def __init__(self, outbox: asyncio.Queue[Outgoing], sample_rate: int = 16000, lead_s: float = 0.15) -> None:
        self.outbox = outbox
        self.bytes_per_s = sample_rate * 2
        self.lead_s = lead_s
        self._play_until = 0.0
        self._odd_byte = b""

    async def play(self, chunk: AudioChunk) -> None:
        data = self._odd_byte + chunk.audio
        cut = len(data) - len(data) % 2  # never split a 16-bit sample
        data, self._odd_byte = data[:cut], data[cut:]
        if data:
            self.outbox.put_nowait(data)
            now = time.perf_counter()
            self._play_until = max(self._play_until, now) + len(data) / self.bytes_per_s
        wait = self._play_until - self.lead_s - time.perf_counter()
        if wait > 0:
            await asyncio.sleep(wait)

    async def stop(self) -> None:
        self._play_until = 0.0
        self._odd_byte = b""
        self.outbox.put_nowait({"type": "clear"})  # browser drops audio it has queued


SPEECH_MODES = ("server", "browser")


@dataclass
class CallDeps:
    settings: Settings
    make_agent: Callable[[], ConversationAgent]
    # Only used in "server" speech mode.
    make_stt: Callable[[], SpeechToText] | None = None
    make_tts: Callable[[], TextToSpeech] | None = None
    speech: str = "server"

    def __post_init__(self) -> None:
        if self.speech not in SPEECH_MODES:
            raise ValueError(f"speech must be one of {SPEECH_MODES}, not {self.speech!r}")
        if self.speech == "server" and (self.make_stt is None or self.make_tts is None):
            raise ValueError("server speech mode needs make_stt and make_tts")


async def handle_call(ws: Any, deps: CallDeps) -> None:
    """Run one browser call until the caller hangs up or disconnects."""
    outbox: asyncio.Queue[Outgoing] = asyncio.Queue()
    mic: asyncio.Queue[bytes | None] = asyncio.Queue()
    browser_stt: BrowserSTT | None = None
    speech_out: BrowserSpeechOutput | None = None
    if deps.speech == "browser":
        browser_stt = BrowserSTT()
        speech_out = BrowserSpeechOutput(outbox)
        stt: SpeechToText = browser_stt
        tts: TextToSpeech = BrowserSpeechTTS()
        output: Any = speech_out
    else:
        assert deps.make_stt is not None and deps.make_tts is not None
        stt, tts = deps.make_stt(), deps.make_tts()
        output = BrowserAudioOutput(outbox, deps.settings.sample_rate)
    session = VoiceSession(
        deps.make_agent(),
        tts,
        output,
        deps.settings,
        listener=lambda event, data: outbox.put_nowait({"type": event, **data}),
    )

    async def frames() -> AsyncIterator[bytes]:
        while (frame := await mic.get()) is not None:
            yield frame

    async def send_loop() -> None:
        while True:
            item = await outbox.get()
            await ws.send(item if isinstance(item, bytes) else json.dumps(item))

    async def receive_loop() -> None:
        try:
            async for message in ws:
                if isinstance(message, bytes):
                    mic.put_nowait(message)
                    continue
                try:
                    control = json.loads(message)
                except ValueError:
                    continue
                kind = control.get("type") if isinstance(control, dict) else None
                if kind == "hangup":
                    break
                if kind == "barge_in":
                    await session.interrupt()
                elif kind == "stt" and browser_stt is not None:
                    browser_stt.feed(control)
                elif kind == "spoken" and speech_out is not None:
                    speech_out.spoken(control.get("id"))
        finally:
            mic.put_nowait(None)
            if browser_stt is not None:
                browser_stt.close()

    async def converse() -> None:
        await drive_session(
            session,
            stt.transcribe(frames()),
            lambda text: outbox.put_nowait({"type": "caller", "text": text}),
        )

    outbox.put_nowait({"type": "ready", "sample_rate": deps.settings.sample_rate, "speech": deps.speech})
    sender = asyncio.create_task(send_loop())
    tasks = [asyncio.create_task(receive_loop()), asyncio.create_task(converse())]
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            if not task.cancelled() and (exc := task.exception()) is not None:
                outbox.put_nowait({"type": "error", "message": f"{type(exc).__name__}: {exc}"})
    finally:
        for task in tasks:
            task.cancel()
        await session.interrupt()
        # Give the sender a moment to flush a final error or event, then stop it.
        with contextlib.suppress(Exception):
            await asyncio.wait_for(_drain(outbox), timeout=0.5)
        sender.cancel()
        if hasattr(tts, "aclose"):
            await tts.aclose()


async def _drain(queue: asyncio.Queue[Outgoing]) -> None:
    while not queue.empty():
        await asyncio.sleep(0.01)


def _page() -> bytes:
    return resources.files("voice_agent.web").joinpath("static/index.html").read_bytes()


def _process_request(connection: Any, request: Any) -> Any:
    from websockets.datastructures import Headers
    from websockets.http11 import Response

    path = request.path.split("?", 1)[0]
    if path == "/ws":
        return None  # continue with the WebSocket handshake
    if path in ("/", "/index.html"):
        page = _page()
        headers = Headers(
            [
                ("Content-Type", "text/html; charset=utf-8"),
                ("Content-Length", str(len(page))),
                ("Cache-Control", "no-store"),
            ]
        )
        return Response(200, "OK", headers, page)
    return connection.respond(HTTPStatus.NOT_FOUND, "Not found\n")


@contextlib.asynccontextmanager
async def serve_calls(deps: CallDeps, host: str = "127.0.0.1", port: int = 8080) -> AsyncIterator[Any]:
    from websockets.asyncio.server import serve

    async def handler(ws: Any) -> None:
        await handle_call(ws, deps)

    async with serve(handler, host, port, process_request=_process_request, max_size=2**20) as server:
        yield server
