import asyncio
import json

from fakes import FakeClient, FakeStream, text_block, tool_block

from voice_agent.agent import TextDelta, ToolFinished, ToolStarted, TurnEnded
from voice_agent.agent.claude_agent import FALLBACK_BETA, INTERRUPTED_TOOL_RESULT, ClaudeAgent
from voice_agent.config import Settings
from voice_agent.tools import Tool, ToolRegistry, lookup_order
from voice_agent.tracing import TurnTrace


def collect(agent, text, note=None):
    async def run():
        return [e async for e in agent.run_turn(text, TurnTrace(1), note)]

    return asyncio.run(run())


def test_tool_loop_streams_text_and_keeps_history_valid():
    client = FakeClient(
        [
            FakeStream([text_block("Let me check."), tool_block("t1", "lookup_order", {"order_id": "A1001"})], "tool_use"),
            FakeStream([text_block("It shipped.")], "end_turn"),
        ]
    )
    agent = ClaudeAgent(client, ToolRegistry([lookup_order]), Settings())
    events = collect(agent, "where is A1001")

    kinds = [type(e) for e in events]
    assert kinds[-1] is TurnEnded
    assert ToolStarted in kinds and ToolFinished in kinds
    assert kinds.index(ToolStarted) < kinds.index(ToolFinished)
    assert "".join(e.text for e in events if isinstance(e, TextDelta)).split() == [
        "Let", "me", "check.", "It", "shipped."
    ]

    roles = [m["role"] for m in agent.messages]
    assert roles == ["user", "assistant", "user", "assistant"]
    result = agent.messages[2]["content"][0]
    assert result["tool_use_id"] == "t1" and "is_error" not in result
    assert json.loads(result["content"])["status"] == "shipped"


def test_request_settings():
    client = FakeClient([FakeStream([text_block("Hi.")], "end_turn")])
    agent = ClaudeAgent(client, ToolRegistry([lookup_order]), Settings(fast=True))
    collect(agent, "hello")

    req = client.requests[0]
    assert req["model"] == "claude-opus-5-5"
    assert req["output_config"] == {"effort": "low"}
    assert req["cache_control"] == {"type": "ephemeral"}
    assert req["betas"] == [FALLBACK_BETA] and req["fallbacks"] == "default"
    assert req["speed"] == "fast"
    assert req["tools"][0]["strict"] is True


def test_note_is_a_system_message_right_after_the_user_turn():
    client = FakeClient([FakeStream([text_block("Okay.")], "end_turn")])
    agent = ClaudeAgent(client, ToolRegistry([]), Settings())
    collect(agent, "stop", note="The caller interrupted your previous reply.")

    sent = client.requests[0]["messages"]
    assert [m["role"] for m in sent] == ["user", "system"]


def test_interrupt_before_reply_rolls_back_and_carries_the_words():
    client = FakeClient(
        [
            FakeStream([text_block("one two three four five six")], "end_turn", delay_s=0.05),
            FakeStream([text_block("Sure.")], "end_turn"),
        ]
    )
    agent = ClaudeAgent(client, ToolRegistry([]), Settings())

    async def run():
        gen = agent.run_turn("I want to", TurnTrace(1))
        await gen.__anext__()  # first word streamed, reply not finished
        await gen.aclose()  # caller barged in
        return [e async for e in agent.run_turn("check my order", TurnTrace(2))]

    asyncio.run(run())
    assert client.requests[1]["messages"] == [{"role": "user", "content": "I want to check my order"}]


def test_interrupt_during_tool_call_still_answers_every_tool_use():
    async def hang(args):
        await asyncio.sleep(10)

    schema = {"type": "object", "properties": {}, "additionalProperties": False}
    client = FakeClient([FakeStream([tool_block("t1", "hang", {})], "tool_use")])
    agent = ClaudeAgent(client, ToolRegistry([Tool("hang", "", schema, hang)], timeout_s=30), Settings())

    async def run():
        async def consume():
            async for _ in agent.run_turn("go", TurnTrace(1)):
                pass

        task = asyncio.create_task(consume())
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(run())
    roles = [m["role"] for m in agent.messages]
    assert roles == ["user", "assistant", "user"]
    result = agent.messages[2]["content"][0]
    assert result["tool_use_id"] == "t1" and result["is_error"] is True
    assert result["content"] == INTERRUPTED_TOOL_RESULT
