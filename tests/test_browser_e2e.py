"""End-to-end: the real call page in headless Chromium against the real server.

Speech vendors and Claude are replaced by offline fakes, so these run without keys:
- server speech mode: Chromium's fake microphone streams a WAV file through the
  page's capture worklet to the server; fake STT/TTS turn it into a reply with audio.
- browser speech mode: the Web Speech API is stubbed in the page, so the test can
  "say" things and see what the page asks the browser to speak.

Skipped when Playwright or its Chromium is not installed:
    pip install -e ".[dev,e2e]" && playwright install chromium
"""

from __future__ import annotations

import asyncio
import math
import struct
import threading
import wave

import pytest
from fakes import ScriptedAgent, TextDelta, TurnEnded

from voice_agent.config import Settings
from voice_agent.providers.base import AudioChunk, Transcript
from voice_agent.web import CallDeps, serve_calls

playwright_api = pytest.importorskip("playwright.sync_api")

pytestmark = pytest.mark.e2e

TONE_PCM = b"".join(  # 50 ms of a 440 Hz tone at 16 kHz per word
    struct.pack("<h", int(8000 * math.sin(2 * math.pi * 440 * i / 16000))) for i in range(800)
)


class ToneTTS:
    async def synthesize(self, text):
        for word in text.split():
            yield AudioChunk(TONE_PCM, word)


class FramesSTT:
    """Emits a scripted transcript once enough microphone frames have arrived."""

    def __init__(self, script):
        self.script = script

    async def transcribe(self, audio):
        pending, frames = list(self.script), 0
        async for _ in audio:
            frames += 1
            while pending and frames >= pending[0][0]:
                yield pending.pop(0)[1]


class ServerThread:
    """Runs ``serve_calls`` on its own event loop in a background thread."""

    def __init__(self, deps: CallDeps) -> None:
        self.deps = deps
        self.port: int | None = None
        self._ready = threading.Event()
        self._stop: asyncio.Event | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        async def main() -> None:
            self._loop = asyncio.get_running_loop()
            self._stop = asyncio.Event()
            async with serve_calls(self.deps, "127.0.0.1", 0) as server:
                self.port = server.sockets[0].getsockname()[1]
                self._ready.set()
                await self._stop.wait()

        asyncio.run(main())

    def __enter__(self) -> str:
        self._thread.start()
        assert self._ready.wait(10), "server did not start"
        return f"http://127.0.0.1:{self.port}/"

    def __exit__(self, *exc) -> None:
        assert self._loop is not None and self._stop is not None
        self._loop.call_soon_threadsafe(self._stop.set)
        self._thread.join(10)


@pytest.fixture(scope="module")
def mic_wav(tmp_path_factory):
    path = tmp_path_factory.mktemp("audio") / "caller.wav"
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(TONE_PCM * 40)  # 2 s, looped by Chromium
    return path


@pytest.fixture(scope="module")
def pw():
    with playwright_api.sync_playwright() as p:
        yield p


def launch(pw, *args):
    try:
        return pw.chromium.launch(args=list(args))
    except Exception as exc:  # browser binary missing
        pytest.skip(f"Chromium not available: {exc}")


def test_server_speech_call_in_real_browser(pw, mic_wav):
    agent = ScriptedAgent([[TextDelta("Your order A1001 has shipped. "), TurnEnded("end_turn")]])
    deps = CallDeps(
        settings=Settings(),
        make_agent=lambda: agent,
        make_stt=lambda: FramesSTT([(25, Transcript("Where is my order?", is_final=True, speech_final=True))]),
        make_tts=ToneTTS,
    )
    browser = launch(
        pw,
        "--use-fake-ui-for-media-stream",
        "--use-fake-device-for-media-stream",
        f"--use-file-for-fake-audio-capture={mic_wav}",
        "--autoplay-policy=no-user-gesture-required",
    )
    try:
        with ServerThread(deps) as url:
            page = browser.new_context(permissions=["microphone"]).new_page()
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(url)
            page.click("#start")
            page.get_by_text("Connected. Start talking.").wait_for(timeout=10_000)
            expect = playwright_api.expect
            expect(page.locator(".line.you")).to_have_text("YouWhere is my order?", timeout=10_000)
            expect(page.locator(".line.agent")).to_contain_text("Your order A1001 has shipped.")
            expect(page.locator("#lat tr:not(.empty)")).to_have_count(1, timeout=10_000)
            expect(page.locator("#mode")).to_contain_text("Deepgram")
            stats = page.evaluate("window.__voiceStats")
            assert stats["micBytes"] >= 25 * 640, stats  # mic audio really reached the server
            assert stats["audioBytes"] == len(TONE_PCM) * 5, stats  # five words of agent audio
            page.click("#stop")
            expect(page.locator("#state")).to_have_text("idle")
            assert not errors, errors
    finally:
        browser.close()


# Stand-ins for the Web Speech API, installed before the page's own script runs.
FAKE_WEB_SPEECH = """
(() => {
  window.__spoken = [];
  window.__cancels = 0;
  window.__speakMs = 30;
  const queue = [];
  function playNext() {
    const u = queue[0];
    if (!u || u.__started) return;
    u.__started = true;
    u.__timer = setTimeout(() => { queue.shift(); u.onend && u.onend(); playNext(); }, window.__speakMs);
  }
  window.SpeechSynthesisUtterance = function (text) { this.text = text; };
  const synth = {
    speak(u) { window.__spoken.push(u.text); queue.push(u); playNext(); },
    cancel() {
      window.__cancels += 1;
      const dropped = queue.splice(0);
      for (const u of dropped) { clearTimeout(u.__timer); u.onerror && u.onerror({ error: 'interrupted' }); }
    },
    getVoices() { return []; },
    onvoiceschanged: null,
  };
  Object.defineProperty(window, 'speechSynthesis', { value: synth, configurable: true });
  class FakeRecognition {
    constructor() { window.__rec = this; }
    start() { this.started = true; }
    abort() { this.started = false; }
    stop() { this.started = false; }
  }
  // say(text, final): deliver a recognition result the way Chrome does.
  window.__say = (text, final = true) => {
    const result = [{ transcript: text }];
    result.isFinal = final;
    window.__rec.onresult({ resultIndex: 0, results: [result] });
  };
  window.SpeechRecognition = FakeRecognition;
  window.webkitSpeechRecognition = FakeRecognition;
})();
"""


def test_browser_speech_call_in_real_browser(pw):
    agent = ScriptedAgent(
        [
            [TextDelta("Sure. What's your order ID? "), TurnEnded("end_turn")],
            [TextDelta("Order A1001 has shipped. It arrives Friday. "), TurnEnded("end_turn")],
            [TextDelta("No problem. "), TurnEnded("end_turn")],
        ]
    )
    deps = CallDeps(settings=Settings(), make_agent=lambda: agent, speech="browser")
    browser = launch(pw)
    try:
        with ServerThread(deps) as url:
            page = browser.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.add_init_script(FAKE_WEB_SPEECH)
            page.goto(url)
            page.click("#start")
            expect = playwright_api.expect
            page.get_by_text("Connected. Start talking.").wait_for(timeout=10_000)
            expect(page.locator("#duplex-row")).to_be_visible()

            # Turn 1: the caller speaks, the page speaks Claude's reply and reports it heard.
            page.evaluate("window.__say(\"Where's my order?\")")
            expect(page.locator(".line.agent").first).to_contain_text("What's your order ID?")
            expect(page.locator("#lat tr:not(.empty)")).to_have_count(1, timeout=10_000)
            assert page.evaluate("window.__spoken") == ["Sure.", "What's your order ID?"]

            assert page.evaluate("window.__voiceStats.linesSpoken") == 2

            # Turn 2: slow speech, so the agent is still talking when the caller interrupts.
            page.evaluate("window.__speakMs = 3000")
            page.wait_for_timeout(500)  # the page ignores the mic for 400 ms after the agent stops
            page.evaluate("window.__say('A1001')")
            expect(page.locator("#state")).to_have_text("speaking", timeout=10_000)
            sent_before = page.evaluate("window.__voiceStats.sttSent")
            page.evaluate("window.__say('this is the agent echoing back')")  # no headphones: ignored
            assert page.evaluate("window.__voiceStats.sttSent") == sent_before
            page.click("#interrupt")
            expect(page.locator(".line.agent").nth(1)).to_contain_text("(interrupted)", timeout=10_000)
            assert page.evaluate("window.__cancels") >= 1

            # Turn 3: Claude is told the caller heard none of the interrupted reply.
            page.evaluate("window.__speakMs = 30")
            page.wait_for_timeout(500)  # echo guard after the interruption
            page.evaluate("window.__say('Never mind.')")
            expect(page.locator(".line.agent").nth(2)).to_contain_text("No problem.", timeout=10_000)
            note = agent.calls[2][1]
            assert note is not None and "heard none" in note.lower()
            page.click("#stop")
            expect(page.locator("#state")).to_have_text("idle")
            assert not errors, errors
    finally:
        browser.close()


def test_browser_without_speech_recognition_shows_clear_error(pw):
    deps = CallDeps(settings=Settings(), make_agent=lambda: ScriptedAgent([]), speech="browser")
    browser = launch(pw)
    try:
        with ServerThread(deps) as url:
            page = browser.new_page()
            page.add_init_script("delete window.SpeechRecognition; delete window.webkitSpeechRecognition;")
            page.goto(url)
            page.click("#start")
            playwright_api.expect(page.locator(".line.error")).to_contain_text("Chrome or Edge", timeout=10_000)
            playwright_api.expect(page.locator("#state")).to_have_text("idle")
    finally:
        browser.close()
