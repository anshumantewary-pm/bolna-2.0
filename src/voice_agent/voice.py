"""Voice mode: microphone -> Deepgram -> Claude agent -> ElevenLabs -> speakers.

Run with ``voice-agent --voice`` (local mic) or ``voice-agent --web`` (browser). Needs DEEPGRAM_API_KEY, ELEVENLABS_API_KEY,
ELEVENLABS_VOICE_ID and Claude credentials. Use headphones.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Callable

from .agent.claude_agent import ClaudeAgent
from .config import Settings
from .providers.base import SpeechToText, SttEvent
from .providers.deepgram import DeepgramOptions, DeepgramSTT
from .providers.elevenlabs import ElevenLabsOptions, ElevenLabsTTS
from .session import State, VoiceSession
from .tools import ToolRegistry, lookup_order
from .turns import BargeIn, EndOfTurn, TurnDetector

REQUIRED_ENV = ("DEEPGRAM_API_KEY", "ELEVENLABS_API_KEY", "ELEVENLABS_VOICE_ID")

# Help the STT with the words this agent hears most.
KEYTERMS = ("order ID", "A1001", "A1002", "A1003")


def missing_env() -> list[str]:
    return [name for name in REQUIRED_ENV if not os.environ.get(name)]


async def drive_session(
    session: VoiceSession,
    events: AsyncIterator[SttEvent],
    on_caller: Callable[[str], None] = lambda text: None,
) -> None:
    """Route STT events into the session: barge-in stops the agent, end of turn answers."""
    detector = TurnDetector()
    async for event in events:
        signal = detector.on_event(event)
        if isinstance(signal, BargeIn) and session.state is not State.LISTENING:
            await session.interrupt()
        elif isinstance(signal, EndOfTurn):
            on_caller(signal.text)
            await session.on_user_turn(signal.text)


def make_stt(settings: Settings) -> DeepgramSTT:
    return DeepgramSTT(
        os.environ["DEEPGRAM_API_KEY"],
        DeepgramOptions(model=settings.deepgram_model, sample_rate=settings.sample_rate, keyterms=KEYTERMS),
    )


def make_tts(settings: Settings) -> ElevenLabsTTS:
    if not settings.elevenlabs_voice_id:
        raise SystemExit("Set ELEVENLABS_VOICE_ID (pick a voice in your ElevenLabs voice library).")
    return ElevenLabsTTS(
        os.environ["ELEVENLABS_API_KEY"],
        ElevenLabsOptions(
            voice_id=settings.elevenlabs_voice_id,
            model_id=settings.elevenlabs_model,
            sample_rate=settings.sample_rate,
        ),
    )


def resolve_speech(requested: str) -> str:
    """'auto' picks server speech when every vendor key is set, else browser speech."""
    if requested == "auto":
        return "browser" if missing_env() else "server"
    return requested


async def run_web(host: str, port: int, speech: str = "auto") -> None:
    """Serve browser calls: open http://localhost:<port> and click Start call."""
    import anthropic

    from .web import CallDeps, serve_calls

    settings = Settings.from_env()
    speech = resolve_speech(speech)
    client = anthropic.AsyncAnthropic()

    def make_agent() -> ClaudeAgent:
        return ClaudeAgent(client, ToolRegistry([lookup_order], timeout_s=settings.tool_timeout_s), settings)

    if speech == "server":
        make_tts(settings)  # fail fast on a missing voice ID
        deps = CallDeps(
            settings=settings,
            make_agent=make_agent,
            make_stt=lambda: make_stt(settings),
            make_tts=lambda: make_tts(settings),
        )
        stack = f"STT {settings.deepgram_model} | LLM {settings.model} | TTS {settings.elevenlabs_model}"
    else:
        deps = CallDeps(settings=settings, make_agent=make_agent, speech="browser")
        stack = f"STT + TTS in the browser (Chrome/Edge) | LLM {settings.model}"
    async with serve_calls(deps, host, port) as server:
        shown = "localhost" if host in ("127.0.0.1", "0.0.0.0") else host
        print(f"Open http://{shown}:{port} and click Start call. Ctrl+C to stop.")
        print(stack)
        await server.serve_forever()


async def run_voice() -> None:
    import anthropic

    from .providers.local_audio import MicInput, SpeakerOutput

    settings = Settings.from_env()
    tts = make_tts(settings)
    stt: SpeechToText = make_stt(settings)
    speaker = SpeakerOutput(settings.sample_rate)
    agent = ClaudeAgent(
        anthropic.AsyncAnthropic(),
        ToolRegistry([lookup_order], timeout_s=settings.tool_timeout_s),
        settings,
    )
    session = VoiceSession(agent, tts, speaker, settings)

    def report(text: str) -> None:
        if session.traces:
            print(f"  [{session.traces[-1].summary()}]")
        print(f"you> {text}")

    print("Listening. Use headphones. Say \"Where's my order?\". Ctrl+C to quit.")
    print(f"STT {settings.deepgram_model} | LLM {settings.model} | TTS {settings.elevenlabs_model}\n")
    try:
        await drive_session(session, stt.transcribe(MicInput(settings.sample_rate).frames()), report)
    finally:
        await session.interrupt()
        await tts.aclose()
        speaker.close()
