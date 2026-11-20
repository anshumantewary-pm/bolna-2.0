"""Browser speech mode over the real WebSocket server (no browser, no vendors)."""

import asyncio
import json

import pytest
from fakes import ScriptedAgent, TextDelta, TurnEnded
from websockets.asyncio.client import connect

from voice_agent.config import Settings
from voice_agent.providers.base import AudioChunk
from voice_agent.web import CallDeps, serve_calls
from voice_agent.web.browser_speech import BrowserSpeechOutput, BrowserSTT


def run_call(agent, client_steps):
    deps = CallDeps(settings=Settings(), make_agent=lambda: agent, speech="browser")

    async def main():
        async with serve_calls(deps, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            async with connect(f"ws://127.0.0.1:{port}/ws") as ws:
                return await asyncio.wait_for(client_steps(ws), 10)

    return asyncio.run(main())


async def next_event(ws, wanted, events):
    """Read until an event of type ``wanted`` arrives; keep everything in ``events``."""
    async for message in ws:
        assert isinstance(message, str), "browser speech mode never sends audio"
        event = json.loads(message)
        events.append(event)
        if event["type"] == wanted:
            return event
    raise AssertionError(f"socket closed before {wanted}")


def stt(text, final=True):
    return json.dumps({"type": "stt", "text": text, "is_final": final, "speech_final": final})


def test_browser_speech_round_trip():
    agent = ScriptedAgent([[TextDelta("Your order has shipped. It arrives Friday. "), TurnEnded("end_turn")]])

    async def steps(ws):
        events = []
        ready = await next_event(ws, "ready", events)
        assert ready["speech"] == "browser"
        await ws.send(stt("Where is my order?"))
        spoken = []
        while len(spoken) < 2:
            line = await next_event(ws, "speak", events)
            spoken.append(line["text"])
            await ws.send(json.dumps({"type": "spoken", "id": line["id"]}))
        done = await next_event(ws, "turn_done", events)
        return events, spoken, done

    events, spoken, done = run_call(agent, steps)
    assert {"type": "caller", "text": "Where is my order?"} in events
    assert spoken == ["Your order has shipped.", "It arrives Friday."]
    assert not done["interrupted"]
    assert {"first_sentence", "first_audio", "done"} <= done["marks_ms"].keys()
    assert agent.calls == [("Where is my order?", None)]


def test_barge_in_button_stops_speech_and_tells_agent_what_was_heard():
    agent = ScriptedAgent(
        [
            [TextDelta("First sentence. Second sentence. Third sentence. "), TurnEnded("end_turn")],
            [TextDelta("Sure. "), TurnEnded("end_turn")],
        ]
    )

    async def steps(ws):
        events = []
        await next_event(ws, "ready", events)
        await ws.send(stt("Tell me everything."))
        first = await next_event(ws, "speak", events)
        await ws.send(json.dumps({"type": "spoken", "id": first["id"]}))
        await next_event(ws, "speak", events)  # second line starts; caller cuts in
        await ws.send(json.dumps({"type": "barge_in"}))
        await next_event(ws, "clear", events)  # turn_done for turn 1 arrives just before it
        done1 = next(e for e in events if e["type"] == "turn_done")
        await ws.send(stt("Just the date please."))
        line = await next_event(ws, "speak", events)
        await ws.send(json.dumps({"type": "spoken", "id": line["id"]}))
        await next_event(ws, "turn_done", events)
        return done1

    done1 = run_call(agent, steps)
    assert done1["interrupted"]
    text, note = agent.calls[1]
    assert text == "Just the date please."
    assert note is not None and "First sentence." in note and "Second" not in note


def test_spoken_interim_result_barges_in_while_agent_talks():
    agent = ScriptedAgent(
        [
            [TextDelta("A long answer. "), TurnEnded("end_turn")],
            [TextDelta("Okay. "), TurnEnded("end_turn")],
        ]
    )

    async def steps(ws):
        events = []
        await next_event(ws, "ready", events)
        await ws.send(stt("Hello."))
        await next_event(ws, "speak", events)  # don't ack: still speaking
        await ws.send(stt("wait a second", final=False))  # headphones mode: caller talks over it
        await next_event(ws, "clear", events)
        await ws.send(stt("Wait a second."))
        await next_event(ws, "caller", events)
        return events

    events = run_call(agent, steps)
    assert {"type": "caller", "text": "Wait a second."} in events


def test_lost_ack_does_not_hang_the_turn():
    async def main():
        outbox = asyncio.Queue()
        out = BrowserSpeechOutput(outbox, slack_s=0.05)
        loop = asyncio.get_running_loop()
        t0 = loop.time()
        await out.play(AudioChunk(b"", "Hi."))  # no ack ever arrives
        return loop.time() - t0, outbox.get_nowait()

    elapsed, sent = asyncio.run(main())
    assert sent["type"] == "speak" and sent["text"] == "Hi."
    assert elapsed < 2.0


def test_browser_stt_ignores_empty_results():
    async def main():
        stt_ = BrowserSTT()
        stt_.feed({"text": "  "})
        stt_.feed({"text": "hi", "is_final": True, "speech_final": True})
        stt_.close()
        return [e async for e in stt_.transcribe(None)]

    events = asyncio.run(main())
    assert [(e.text, e.speech_final) for e in events] == [("hi", True)]


def test_server_mode_requires_vendor_factories():
    with pytest.raises(ValueError):
        CallDeps(settings=Settings(), make_agent=lambda: None)
    with pytest.raises(ValueError):
        CallDeps(settings=Settings(), make_agent=lambda: None, speech="telepathy")
