import asyncio

from fakes import ScriptedAgent, TextDelta, TurnEnded

from voice_agent.config import Settings
from voice_agent.providers.base import Transcript
from voice_agent.providers.fake import RecordingAudio, WordTTS
from voice_agent.session import VoiceSession
from voice_agent.voice import drive_session


def test_caller_speech_drives_turns_and_barge_in():
    long_reply = " ".join(f"word{i}" for i in range(30)) + ". "
    agent = ScriptedAgent(
        [
            [TextDelta(long_reply), TurnEnded("end_turn")],
            [TextDelta("Sure. "), TurnEnded("end_turn")],
        ]
    )
    audio = RecordingAudio(delay_s=0.01)
    session = VoiceSession(agent, WordTTS(), audio, Settings())
    heard_from_caller = []

    async def stt_events():
        yield Transcript("tell me a story", is_final=True, speech_final=True)
        await asyncio.sleep(0.08)  # agent is mid-reply
        yield Transcript("stop", is_final=False)  # barge-in
        await asyncio.sleep(0.03)
        yield Transcript("Stop, check my order.", is_final=True, speech_final=True)
        await asyncio.sleep(0.1)

    async def run():
        await drive_session(session, stt_events(), heard_from_caller.append)
        await session._task

    asyncio.run(run())
    assert heard_from_caller == ["tell me a story", "Stop, check my order."]
    assert session.traces[0].interrupted
    assert audio.played[-1] == "Sure."
    assert len([w for w in audio.played if w.startswith("word")]) < 30
    assert agent.calls[1][0] == "Stop, check my order."
    assert "heard only" in agent.calls[1][1]
