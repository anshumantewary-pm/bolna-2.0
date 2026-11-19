"""Test doubles: a scripted agent and a fake Anthropic client with the SDK's stream shape."""

from __future__ import annotations

import asyncio
import copy
from types import SimpleNamespace
from typing import Any

from voice_agent.agent import TextDelta, ToolFinished, ToolStarted, TurnEnded


class ScriptedAgent:
    """Yields a fixed list of events per turn; a float in the script means sleep that long."""

    def __init__(self, scripts: list[list[Any]]) -> None:
        self.scripts = scripts
        self.calls: list[tuple[str, str | None]] = []

    async def run_turn(self, user_text, trace, note=None):
        self.calls.append((user_text, note))
        for item in self.scripts[len(self.calls) - 1]:
            if isinstance(item, (int, float)):
                await asyncio.sleep(item)
            else:
                yield item


def text_block(text: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=text)


def tool_block(tool_id: str, name: str, args: dict) -> SimpleNamespace:
    return SimpleNamespace(type="tool_use", id=tool_id, name=name, input=args)


class FakeStream:
    def __init__(self, content: list, stop_reason: str, delay_s: float = 0.0) -> None:
        self.content = content
        self.stop_reason = stop_reason
        self.delay_s = delay_s

    async def __aenter__(self) -> "FakeStream":
        return self

    async def __aexit__(self, *exc) -> None:
        return None

    def __aiter__(self):
        return self._events()

    async def _events(self):
        for block in self.content:
            if block.type == "text":
                for word in block.text.split(" "):
                    await asyncio.sleep(self.delay_s)
                    yield SimpleNamespace(type="text", text=word + " ")

    async def get_final_message(self) -> SimpleNamespace:
        return SimpleNamespace(
            content=self.content,
            stop_reason=self.stop_reason,
            usage=SimpleNamespace(input_tokens=100, output_tokens=10, cache_read_input_tokens=90),
        )


class FakeClient:
    """``client.beta.messages.stream(**kwargs)`` returns the next scripted FakeStream."""

    def __init__(self, streams: list[FakeStream]) -> None:
        self._streams = list(streams)
        self.requests: list[dict] = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(stream=self._stream))

    def _stream(self, **kwargs) -> FakeStream:
        self.requests.append(copy.deepcopy(kwargs))
        return self._streams.pop(0)


__all__ = [
    "FakeClient",
    "FakeStream",
    "ScriptedAgent",
    "TextDelta",
    "ToolFinished",
    "ToolStarted",
    "TurnEnded",
    "text_block",
    "tool_block",
]
