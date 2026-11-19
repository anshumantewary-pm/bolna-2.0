"""Voice mode: microphone -> Deepgram -> Claude agent -> ElevenLabs -> speakers.

Run with ``voice-agent --voice``. Needs DEEPGRAM_API_KEY, ELEVENLABS_API_KEY,
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


async def run_voice() -> None:
    import anthropic

    from .providers.local_audio import MicInput, SpeakerOutput

    settings = Settings.from_env()
    if not settings.elevenlabs_voice_id:
        raise SystemExit("Set ELEVENLABS_VOICE_ID (pick a voice in your ElevenLabs voice library).")
    stt: SpeechToText = DeepgramSTT(
        os.environ["DEEPGRAM_API_KEY"],
        DeepgramOptions(model=settings.deepgram_model, sample_rate=settings.sample_rate, keyterms=KEYTERMS),
    )
    tts = ElevenLabsTTS(
        os.environ["ELEVENLABS_API_KEY"],
        ElevenLabsOptions(
            voice_id=settings.elevenlabs_voice_id,
            model_id=settings.elevenlabs_model,
            sample_rate=settings.sample_rate,
        ),
    )
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
