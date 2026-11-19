"""Runtime settings, read from environment variables (see .env.example)."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _flag(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _float(name: str, default: float) -> float:
    value = os.environ.get(name)
    return float(value) if value else default


@dataclass(frozen=True)
class Settings:
    model: str = "claude-opus-5-5"
    # Voice turns are short and latency-bound; "low" keeps time to first token down.
    effort: str = "low"
    fast: bool = False
    fallbacks: bool = True
    # Thinking tokens count against this, so keep headroom even though spoken replies are short.
    max_tokens: int = 16000
    filler_after_s: float = 1.0
    tool_timeout_s: float = 3.0
    # Speech vendors (voice mode).
    sample_rate: int = 16000
    deepgram_model: str = "nova-3"
    elevenlabs_model: str = "eleven_flash_v2_5"
    elevenlabs_voice_id: str = ""

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            model=os.environ.get("VOICE_AGENT_MODEL") or cls.model,
            effort=os.environ.get("VOICE_AGENT_EFFORT") or cls.effort,
            fast=_flag("VOICE_AGENT_FAST", cls.fast),
            fallbacks=_flag("VOICE_AGENT_FALLBACKS", cls.fallbacks),
            filler_after_s=_float("VOICE_AGENT_FILLER_AFTER_S", cls.filler_after_s),
            tool_timeout_s=_float("VOICE_AGENT_TOOL_TIMEOUT_S", cls.tool_timeout_s),
            deepgram_model=os.environ.get("DEEPGRAM_MODEL") or cls.deepgram_model,
            elevenlabs_model=os.environ.get("ELEVENLABS_MODEL") or cls.elevenlabs_model,
            elevenlabs_voice_id=os.environ.get("ELEVENLABS_VOICE_ID") or cls.elevenlabs_voice_id,
        )
