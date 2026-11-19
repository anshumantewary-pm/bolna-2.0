import asyncio

from fakes import ScriptedAgent, TextDelta, ToolFinished, ToolStarted, TurnEnded

from voice_agent.agent.prompts import FILLER_LINE, REFUSAL_LINE
from voice_agent.config import Settings
from voice_agent.providers.fake import RecordingAudio, WordTTS
from voice_agent.session import State, VoiceSession


def make_session(agent, delay_s=0.0, filler_after_s=1.0):
    audio = RecordingAudio(delay_s=delay_s)
    session = VoiceSession(agent, WordTTS(), audio, Settings(filler_after_s=filler_after_s))
    return session, audio


def test_reply_is_spoken_and_traced():
    agent = ScriptedAgent([[TextDelta("Your order "), TextDelta("has shipped. "), TurnEnded("end_turn")]])
    session, audio = make_session(agent)

    async def run():
        await (await session.on_user_turn("where is my order"))

    asyncio.run(run())
    assert audio.played == ["Your", "order", "has", "shipped."]
    trace = session.traces[0]
    assert {"first_sentence", "first_audio", "done"} <= trace.marks.keys()
    assert not trace.interrupted
    assert session.state is State.LISTENING


def test_barge_in_stops_speech_and_tells_the_agent_what_was_heard():
    long_reply = "one two three four five six seven eight nine ten. "
    agent = ScriptedAgent(
        [
            [TextDelta(long_reply), TurnEnded("end_turn")],
            [TextDelta("Okay. "), TurnEnded("end_turn")],
        ]
    )
    session, audio = make_session(agent, delay_s=0.02)

    async def run():
        await session.on_user_turn("tell me something long")
        await asyncio.sleep(0.07)  # about three words in
        await (await session.on_user_turn("stop"))

    asyncio.run(run())
    assert session.traces[0].interrupted
    assert audio.stops == 1
    first_reply_words = audio.played[: audio.played.index("Okay.")]
    assert 0 < len(first_reply_words) < 10
    _, note = agent.calls[1]
    assert note is not None and "heard only" in note
    assert " ".join(first_reply_words) in note


def test_no_note_when_reply_was_heard_in_full():
    agent = ScriptedAgent([[TextDelta("Hi. "), TurnEnded("end_turn")], [TurnEnded("end_turn")]])
    session, _ = make_session(agent)

    async def run():
        await (await session.on_user_turn("hello"))
        await (await session.on_user_turn("again"))

    asyncio.run(run())
    assert agent.calls[1][1] is None


def test_filler_plays_when_a_tool_is_slow_and_not_when_it_is_fast():
    slow = [ToolStarted("lookup_order", "t1"), 0.05, ToolFinished("lookup_order", "t1", True, 50), TextDelta("Found it. "), TurnEnded("end_turn")]
    fast = [ToolStarted("lookup_order", "t1"), ToolFinished("lookup_order", "t1", True, 1), TextDelta("Found it. "), TurnEnded("end_turn")]

    for script, expect_filler in ((slow, True), (fast, False)):
        session, audio = make_session(ScriptedAgent([script]), filler_after_s=0.01)

        async def run():
            await (await session.on_user_turn("A1001"))

        asyncio.run(run())
        spoken = " ".join(audio.played)
        assert (FILLER_LINE in spoken) is expect_filler
        assert spoken.endswith("Found it.")


def test_refusal_speaks_handoff_line():
    agent = ScriptedAgent([[TurnEnded("refusal")]])
    session, audio = make_session(agent)

    async def run():
        await (await session.on_user_turn("something off-limits"))

    asyncio.run(run())
    assert " ".join(audio.played) == REFUSAL_LINE
