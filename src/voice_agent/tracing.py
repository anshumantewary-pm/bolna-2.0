"""Per-turn latency trace: milliseconds from the end of the caller's turn to each stage."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

# Stages in the order they normally happen (PRD section 5, latency budget).
STAGES = ("llm_first_text", "first_sentence", "first_audio", "done")


@dataclass
class TurnTrace:
    turn: int
    t0: float = field(default_factory=time.perf_counter)
    marks: dict[str, float] = field(default_factory=dict)
    tool_ms: list[tuple[str, float]] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)
    interrupted: bool = False

    def mark(self, stage: str) -> None:
        """Record the first time a stage is reached; later calls are ignored."""
        self.marks.setdefault(stage, (time.perf_counter() - self.t0) * 1000)

    def add_tool(self, name: str, ms: float) -> None:
        self.tool_ms.append((name, ms))

    def add_usage(self, usage: object) -> None:
        for key in (
            "input_tokens",
            "output_tokens",
            "cache_read_input_tokens",
            "cache_creation_input_tokens",
        ):
            self.usage[key] = self.usage.get(key, 0) + (getattr(usage, key, 0) or 0)

    def summary(self) -> str:
        parts = [f"turn {self.turn}"]
        parts += [f"{s} {self.marks[s]:.0f} ms" for s in STAGES if s in self.marks]
        if self.tool_ms:
            parts.append("tools " + ", ".join(f"{n} {ms:.0f} ms" for n, ms in self.tool_ms))
        if self.usage:
            parts.append(
                f"tokens in {self.usage.get('input_tokens', 0)}"
                f" (cache read {self.usage.get('cache_read_input_tokens', 0)})"
                f" out {self.usage.get('output_tokens', 0)}"
            )
        if self.interrupted:
            parts.append("INTERRUPTED")
        return " | ".join(parts)
