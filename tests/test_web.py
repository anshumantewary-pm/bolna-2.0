import asyncio
import json

import httpx
from fakes import ScriptedAgent, TextDelta, TurnEnded
from websockets.asyncio.client import connect

from voice_agent.config import Settings
from voice_agent.providers.base import AudioChunk, Transcript
from voice_agent.web import BrowserAudioOutput, CallDeps, serve_calls

WORD_PCM = b"\x01\x00" * 160  # 10 ms of 16 kHz audio per word


class PcmTTS:
    async def synthesize(self, text):
        for word in text.split():
            yield AudioChunk(WORD_PCM, word)


class FramesSTT:
    """Fake STT: emits a scripted transcript after receiving N mic frames from the browser."""

    def __init__(self, script):
        self.script = script  # list of (frames_needed, event)
        self.frames = 0

    async def transcribe(self, audio):
        pending = list(self.script)
        async for _ in audio:
            self.frames += 1
            while pending and self.frames >= pending[0][0]:
                yield pending.pop(0)[1]


def run_call(agent_scripts, stt_script, client_steps):
    """Start the server, run ``client_steps(ws, port)``, return what it collected."""
    deps = CallDeps(
        settings=Settings(),
        make_agent=lambda: ScriptedAgent(agent_scripts),
        make_stt=lambda: FramesSTT(stt_script),
        make_tts=PcmTTS,
    )

    async def main():
        async with serve_calls(deps, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            return await client_steps(port)

    return asyncio.run(main())


async def collect_until(ws, stop_type, events, audio, timeout=5.0):
    async def loop():
        async for message in ws:
            if isinstance(message, bytes):
                audio.append(message)
                continue
            event = json.loads(message)
            events.append(event)
            if event["type"] == stop_type:
                return

    await asyncio.wait_for(loop(), timeout)


def test_page_is_served_and_unknown_paths_404():
    async def steps(port):
        async with httpx.AsyncClient() as http:
            page = await http.get(f"http://127.0.0.1:{port}/")
            missing = await http.get(f"http://127.0.0.1:{port}/nope")
        return page, missing

    page, missing = run_call([[]], [], steps)
    assert page.status_code == 200
    assert page.headers["content-type"].startswith("text/html")
    assert "<title>Voice Agent Call</title>" in page.text
    assert missing.status_code == 404


def test_browser_call_round_trip():
    agent_scripts = [[TextDelta("Your order has shipped. "), TurnEnded("end_turn")]]
    stt_script = [(3, Transcript("Where is my order?", is_final=True, speech_final=True))]

    async def steps(port):
        events, audio = [], []
        async with connect(f"ws://127.0.0.1:{port}/ws") as ws:
            for _ in range(3):
                await ws.send(b"\x00\x00" * 320)  # 20 ms mic frames
            await collect_until(ws, "turn_done", events, audio)
            await ws.send(json.dumps({"type": "hangup"}))
        return events, audio

    events, audio = run_call(agent_scripts, stt_script, steps)
    types = [e["type"] for e in events]
    assert types[0] == "ready"
    assert {"type": "caller", "text": "Where is my order?"} in events
    agent_text = [e["text"] for e in events if e["type"] == "agent_text"]
    assert agent_text == ["Your order has shipped."]
    assert b"".join(audio) == WORD_PCM * 4  # four words of audio, in order
    done = events[-1]
    assert done["type"] == "turn_done" and not done["interrupted"]
    assert {"first_sentence", "first_audio", "done"} <= done["marks_ms"].keys()
    assert "speaking" in [e.get("state") for e in events]


def test_barge_in_clears_browser_audio():
    long_reply = " ".join(f"w{i}" for i in range(200)) + ". "  # ~2 s of audio
    agent_scripts = [
        [TextDelta(long_reply), TurnEnded("end_turn")],
        [TextDelta("Okay. "), TurnEnded("end_turn")],
    ]
    stt_script = [
        (1, Transcript("Tell me everything.", is_final=True, speech_final=True)),
        (2, Transcript("wait", is_final=False)),
        (3, Transcript("Wait, stop.", is_final=True, speech_final=True)),
    ]

    async def steps(port):
        events, audio = [], []
        async with connect(f"ws://127.0.0.1:{port}/ws") as ws:
            await ws.send(b"\x00\x00" * 320)
            await collect_until(ws, "agent_text", events, audio)
            await asyncio.sleep(0.3)  # let some of the reply play
            await ws.send(b"\x00\x00" * 320)  # caller talks over the agent
            await ws.send(b"\x00\x00" * 320)
            await collect_until(ws, "clear", events, audio)
            while not any(e["type"] == "turn_done" and e["turn"] == 2 for e in events):
                await collect_until(ws, "turn_done", events, audio)
        return events, audio

    events, _ = run_call(agent_scripts, stt_script, steps)
    first_done = next(e for e in events if e["type"] == "turn_done" and e["turn"] == 1)
    assert first_done["interrupted"]
    assert any(e["type"] == "clear" for e in events)
    assert {"type": "caller", "text": "Wait, stop."} in events


def test_audio_output_paces_to_real_time_and_never_splits_samples():
    async def main():
        outbox = asyncio.Queue()
        out = BrowserAudioOutput(outbox, sample_rate=16000, lead_s=0.0)
        loop = asyncio.get_running_loop()
        t0 = loop.time()
        await out.play(AudioChunk(b"\x00" * 3201, "a"))  # 100 ms + one stray byte
        await out.play(AudioChunk(b"\x00" * 3199, "b"))
        elapsed = loop.time() - t0
        await out.stop()
        sent = [outbox.get_nowait() for _ in range(outbox.qsize())]
        return elapsed, sent

    elapsed, sent = asyncio.run(main())
    assert [len(s) for s in sent[:2]] == [3200, 3200]
    assert sent[2] == {"type": "clear"}
    assert 0.18 <= elapsed < 0.4
