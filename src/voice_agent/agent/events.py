"""Events an agent streams to the voice session during one caller turn."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol

from ..tracing import TurnTrace


@dataclass(frozen=True)
class TextDelta:
    text: str


@dataclass(frozen=True)
class ToolStarted:
    name: str
    tool_use_id: str


@dataclass(frozen=True)
class ToolFinished:
    name: str
    tool_use_id: str
    ok: bool
    ms: float


@dataclass(frozen=True)
class TurnEnded:
    stop_reason: str


AgentEvent = TextDelta | ToolStarted | ToolFinished | TurnEnded


class ConversationAgent(Protocol):
    def run_turn(
        self, user_text: str, trace: TurnTrace, note: str | None = None
    ) -> AsyncIterator[AgentEvent]:
        """Answer one caller turn. ``note`` is an operator note for this turn (e.g. barge-in)."""
        ...
