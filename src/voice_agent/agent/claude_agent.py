"""Claude agent loop for voice: streaming text, tool calls, and interruption-safe history.

History rules this module keeps (the API rejects a conversation that breaks them):
- Every assistant ``tool_use`` is answered by a ``tool_result`` in the next user message.
- Assistant messages are appended exactly as returned (thinking blocks included) and
  never edited afterwards. What the caller actually heard after a barge-in is passed
  as a mid-conversation system note on the next turn instead.
- A mid-conversation system message comes right after a user message and is followed
  by an assistant message or is last.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from typing import Any

from ..config import Settings
from ..tools import ToolRegistry
from ..tracing import TurnTrace
from .events import AgentEvent, TextDelta, ToolFinished, ToolStarted, TurnEnded
from .prompts import SYSTEM_PROMPT

FALLBACK_BETA = "server-side-fallback-2026-07-01"
INTERRUPTED_TOOL_RESULT = "Not completed: the caller interrupted before this tool finished."
TRUNCATED_TOOL_RESULT = "Not run: the tool input was cut off. Try again with a shorter request."


class ClaudeAgent:
    def __init__(
        self,
        client: Any,  # anthropic.AsyncAnthropic (or a test double with the same shape)
        tools: ToolRegistry,
        settings: Settings,
        system_prompt: str = SYSTEM_PROMPT,
    ) -> None:
        self.client = client
        self.tools = tools
        self.settings = settings
        self.system_prompt = system_prompt
        self.messages: list[dict[str, Any]] = []
        # Words from a turn that was interrupted before Claude answered it.
        self._carry: str | None = None

    def _request(self) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": self.settings.model,
            "max_tokens": self.settings.max_tokens,
            "system": self.system_prompt,
            "tools": self.tools.api_definitions(),
            "messages": self.messages,
            "output_config": {"effort": self.settings.effort},
            # Caches system prompt + tools + history up to the latest turn.
            "cache_control": {"type": "ephemeral"},
        }
        if self.settings.fallbacks:
            kwargs["betas"] = [FALLBACK_BETA]
            kwargs["fallbacks"] = "default"
        if self.settings.fast:
            kwargs["speed"] = "fast"
        return kwargs

    async def run_turn(
        self, user_text: str, trace: TurnTrace, note: str | None = None
    ) -> AsyncIterator[AgentEvent]:
        if self._carry:
            user_text = f"{self._carry} {user_text}"
            self._carry = None
        start = len(self.messages)
        self.messages.append({"role": "user", "content": user_text})
        if note:
            self.messages.append({"role": "system", "content": note})

        first_request = True
        pending_tools: list[Any] = []
        results: dict[str, dict[str, Any]] = {}
        try:
            while True:
                async with self.client.beta.messages.stream(**self._request()) as stream:
                    async for event in stream:
                        if event.type == "text" and event.text:
                            trace.mark("llm_first_text")
                            yield TextDelta(event.text)
                    final = await stream.get_final_message()
                self.messages.append({"role": "assistant", "content": final.content})
                first_request = False
                trace.add_usage(final.usage)

                tool_uses = [b for b in final.content if b.type == "tool_use"]
                if final.stop_reason == "max_tokens" and tool_uses:
                    # A cut-off tool input can parse as a valid partial object; never run it.
                    self.messages.append(
                        {
                            "role": "user",
                            "content": [_tool_result(b.id, TRUNCATED_TOOL_RESULT, True) for b in tool_uses],
                        }
                    )
                    yield TurnEnded("max_tokens")
                    return
                if final.stop_reason == "refusal" or not tool_uses:
                    yield TurnEnded(final.stop_reason or "end_turn")
                    return

                pending_tools, results = tool_uses, {}
                for block in tool_uses:
                    yield ToolStarted(block.name, block.id)
                outcomes = await asyncio.gather(*(self._run_tool(b, trace) for b in tool_uses))
                for block, (content, is_error, ms) in zip(tool_uses, outcomes):
                    results[block.id] = _tool_result(block.id, content, is_error)
                self.messages.append(
                    {"role": "user", "content": [results[b.id] for b in tool_uses]}
                )
                pending_tools = []
                for block, (_, is_error, ms) in zip(tool_uses, outcomes):
                    yield ToolFinished(block.name, block.id, not is_error, ms)
        except (asyncio.CancelledError, GeneratorExit):
            self._repair_after_interrupt(start, first_request, user_text, pending_tools, results)
            raise

    async def _run_tool(self, block: Any, trace: TurnTrace) -> tuple[str, bool, float]:
        t0 = time.perf_counter()
        content, is_error = await self.tools.execute(block.name, block.input)
        ms = (time.perf_counter() - t0) * 1000
        trace.add_tool(block.name, ms)
        return content, is_error, ms

    def _repair_after_interrupt(
        self,
        start: int,
        first_request: bool,
        user_text: str,
        pending_tools: list[Any],
        results: dict[str, dict[str, Any]],
    ) -> None:
        if first_request:
            # Claude never answered: drop this turn's messages and prepend the
            # caller's words to their next utterance instead.
            del self.messages[start:]
            self._carry = user_text
        elif pending_tools:
            # Assistant asked for tools but we were cut off: every tool_use still needs a result.
            self.messages.append(
                {
                    "role": "user",
                    "content": [
                        results.get(b.id) or _tool_result(b.id, INTERRUPTED_TOOL_RESULT, True)
                        for b in pending_tools
                    ],
                }
            )
        # Otherwise history already ends cleanly (assistant reply or tool results).


def _tool_result(tool_use_id: str, content: str, is_error: bool) -> dict[str, Any]:
    result: dict[str, Any] = {"type": "tool_result", "tool_use_id": tool_use_id, "content": content}
    if is_error:
        result["is_error"] = True
    return result
