import asyncio
import json
from urllib.parse import parse_qs, urlparse

import pytest
from websockets.asyncio.server import serve

from voice_agent.providers.base import SpeechStarted, Transcript, UtteranceEnd
from voice_agent.providers.deepgram import (
    DeepgramOptions,
    DeepgramSTT,
    SttError,
    parse_message,
)


def results(text, is_final=False, speech_final=False):
    return json.dumps(
        {
            "type": "Results",
            "is_final": is_final,
            "speech_final": speech_final,
            "channel": {"alternatives": [{"transcript": text, "confidence": 0.98, "words": []}]},
        }
    )


def test_parse_message():
    assert parse_message(results("hi", is_final=True)) == Transcript("hi", True, False)
    assert parse_message(results("", is_final=True, speech_final=True)) == Transcript("", True, True)
    assert parse_message(results("")) is None
    assert parse_message(json.dumps({"type": "UtteranceEnd", "last_word_end": 2.1})) == UtteranceEnd()
    assert parse_message(json.dumps({"type": "SpeechStarted", "timestamp": 0.5})) == SpeechStarted()
    assert parse_message(json.dumps({"type": "Metadata", "request_id": "x"})) is None
    with pytest.raises(SttError, match="bad audio"):
        parse_message(json.dumps({"type": "Error", "description": "bad audio"}))


def test_query_parameters():
    url = DeepgramOptions(keyterms=("A1001", "order ID")).full_url()
    q = parse_qs(urlparse(url).query)
    assert url.startswith("wss://api.deepgram.com/v1/listen?")
    assert q["model"] == ["nova-3"] and q["encoding"] == ["linear16"] and q["sample_rate"] == ["16000"]
    assert q["interim_results"] == ["true"] and q["vad_events"] == ["true"]
    assert q["endpointing"] == ["300"] and q["utterance_end_ms"] == ["1000"]
    assert q["keyterm"] == ["A1001", "order ID"]


def test_streams_audio_and_yields_events_from_a_live_socket():
    received = {"frames": [], "auth": None, "control": []}

    async def handler(ws):
        received["auth"] = ws.request.headers.get("Authorization")
        async for message in ws:
            if isinstance(message, bytes):
                received["frames"].append(message)
                if len(received["frames"]) == 2:
                    await ws.send(json.dumps({"type": "SpeechStarted", "timestamp": 0.1}))
                    await ws.send(results("where is", is_final=False))
                    await ws.send(results("Where is my order?", is_final=True, speech_final=True))
            else:
                received["control"].append(json.loads(message))
                if received["control"][-1]["type"] == "CloseStream":
                    await ws.close()

    async def run():
        async with serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            stt = DeepgramSTT("test-key", DeepgramOptions(url=f"ws://127.0.0.1:{port}/v1/listen"))

            async def audio():
                for frame in (b"\x00\x01" * 160, b"\x02\x03" * 160):
                    yield frame
                await asyncio.sleep(0.2)  # keep the mic "open" while results arrive

            return [e async for e in stt.transcribe(audio())]

    events = asyncio.run(run())
    assert received["auth"] == "Token test-key"
    assert len(received["frames"]) == 2
    assert received["control"] == [{"type": "CloseStream"}]
    assert events == [
        SpeechStarted(),
        Transcript("where is", False, False),
        Transcript("Where is my order?", True, True),
    ]
