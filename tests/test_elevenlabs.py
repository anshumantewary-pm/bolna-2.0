import asyncio
import base64
import json

import httpx
import pytest

from voice_agent.providers.elevenlabs import (
    ElevenLabsOptions,
    ElevenLabsTTS,
    TtsError,
    WordAligner,
    parse_line,
)


def chunk_line(audio: bytes, chars: str | None) -> str:
    alignment = None
    if chars is not None:
        alignment = {
            "characters": list(chars),
            "character_start_times_seconds": [0.0] * len(chars),
            "character_end_times_seconds": [0.0] * len(chars),
        }
    return json.dumps({"audio_base64": base64.b64encode(audio).decode(), "alignment": alignment})


def test_parse_line():
    assert parse_line("") is None
    assert parse_line("   ") is None
    audio, alignment = parse_line(chunk_line(b"\x01\x02", "Hi"))
    assert audio == b"\x01\x02" and alignment["characters"] == ["H", "i"]
    assert parse_line("data: " + chunk_line(b"\x00", None)) == (b"\x00", None)


def test_word_aligner_emits_words_as_they_complete():
    a = WordAligner()
    assert a.feed({"characters": list("Your or")}) == ["Your"]
    assert a.feed({"characters": list("der has ")}) == ["order", "has"]
    assert a.feed(None) == []
    assert a.feed({"characters": list("shipped.")}) == []
    assert a.flush() == "shipped."


def test_synthesize_streams_pcm_with_the_words_each_chunk_covers():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["key"] = request.headers["xi-api-key"]
        seen["body"] = json.loads(request.content)
        body = "\n".join(
            [chunk_line(b"\x01" * 4, "Your or"), "", chunk_line(b"\x02" * 4, "der shipped.")]
        )
        return httpx.Response(200, text=body + "\n")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    tts = ElevenLabsTTS("k", ElevenLabsOptions(voice_id="voice123"), client=client)

    async def run():
        return [c async for c in tts.synthesize("Your order shipped.")]

    chunks = asyncio.run(run())
    assert seen["url"] == (
        "https://api.elevenlabs.io/v1/text-to-speech/voice123/stream/with-timestamps"
        "?output_format=pcm_16000"
    )
    assert seen["key"] == "k"
    assert seen["body"] == {"text": "Your order shipped.", "model_id": "eleven_flash_v2_5"}
    assert [c.text for c in chunks] == ["Your", "order", "shipped."]
    assert b"".join(c.audio for c in chunks) == b"\x01" * 4 + b"\x02" * 4


def test_http_error_raises():
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(401, json={"detail": "invalid key"}))
    )
    tts = ElevenLabsTTS("bad", ElevenLabsOptions(voice_id="v"), client=client)

    async def run():
        return [c async for c in tts.synthesize("hi")]

    with pytest.raises(TtsError, match="401"):
        asyncio.run(run())
