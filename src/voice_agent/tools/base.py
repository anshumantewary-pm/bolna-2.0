"""Tool gateway (PRD section 7.6): schemas, input validation, timeouts, error results."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from typing import Any

_JSON_TYPES: dict[str, type | tuple[type, ...]] = {
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "object": dict,
    "array": list,
}


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[[dict[str, Any]], Awaitable[Any]]
    # Write tools must get a spoken confirmation first (PRD F8). None exist in Phase 0.
    is_write: bool = False

    def to_api(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
            "strict": True,
        }


def validate_input(schema: dict[str, Any], args: Any) -> str | None:
    """Return an error message, or None when ``args`` fits the (flat) object schema."""
    if not isinstance(args, dict):
        return "input must be an object"
    props: dict[str, Any] = schema.get("properties", {})
    for key in schema.get("required", []):
        if key not in args:
            return f"missing required field '{key}'"
    if schema.get("additionalProperties") is False:
        extra = sorted(set(args) - set(props))
        if extra:
            return f"unexpected field(s): {', '.join(extra)}"
    for key, value in args.items():
        expected = _JSON_TYPES.get(props.get(key, {}).get("type", ""))
        if expected is None:
            continue
        # bool is a subclass of int in Python; JSON keeps them apart.
        if isinstance(value, bool) and expected is not bool:
            return f"field '{key}' must be {props[key]['type']}"
        if not isinstance(value, expected):
            return f"field '{key}' must be {props[key]['type']}"
    return None


class ToolRegistry:
    def __init__(self, tools: Iterable[Tool], timeout_s: float = 3.0) -> None:
        self._tools = {t.name: t for t in tools}
        self.timeout_s = timeout_s

    def api_definitions(self) -> list[dict[str, Any]]:
        # Fixed order keeps the prompt-cache prefix stable between requests.
        return [self._tools[name].to_api() for name in sorted(self._tools)]

    async def execute(self, name: str, args: Any) -> tuple[str, bool]:
        """Run a tool. Returns (content, is_error); never raises except on cancellation."""
        tool = self._tools.get(name)
        if tool is None:
            return f"unknown tool '{name}'", True
        error = validate_input(tool.input_schema, args)
        if error:
            return f"invalid input: {error}", True
        try:
            result = await asyncio.wait_for(tool.handler(args), self.timeout_s)
        except TimeoutError:
            return f"tool '{name}' timed out after {self.timeout_s:g} s", True
        except Exception as exc:  # a tool bug must not end the call
            return f"tool '{name}' failed: {exc}", True
        return (result if isinstance(result, str) else json.dumps(result)), False
