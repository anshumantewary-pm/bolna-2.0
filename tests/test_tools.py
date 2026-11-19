import asyncio
import json

from voice_agent.tools import Tool, ToolRegistry, lookup_order, validate_input

SCHEMA = lookup_order.input_schema


def test_validate_input():
    assert validate_input(SCHEMA, {"order_id": "A1001"}) is None
    assert "missing" in validate_input(SCHEMA, {})
    assert "unexpected" in validate_input(SCHEMA, {"order_id": "A1", "x": 1})
    assert "must be string" in validate_input(SCHEMA, {"order_id": 1001})
    assert "object" in validate_input(SCHEMA, "A1001")


def test_lookup_order_normalizes_spoken_ids():
    registry = ToolRegistry([lookup_order])
    content, is_error = asyncio.run(registry.execute("lookup_order", {"order_id": "a-1001"}))
    assert not is_error
    assert json.loads(content)["status"] == "shipped"

    content, is_error = asyncio.run(registry.execute("lookup_order", {"order_id": "Z9"}))
    assert not is_error
    assert json.loads(content)["found"] is False


def test_errors_become_results_not_exceptions():
    async def slow(args):
        await asyncio.sleep(1)

    async def broken(args):
        raise RuntimeError("db down")

    schema = {"type": "object", "properties": {}, "additionalProperties": False}
    registry = ToolRegistry(
        [Tool("slow", "", schema, slow), Tool("broken", "", schema, broken)], timeout_s=0.01
    )

    async def run():
        return (
            await registry.execute("slow", {}),
            await registry.execute("broken", {}),
            await registry.execute("nope", {}),
            await registry.execute("slow", {"x": 1}),
        )

    slow_r, broken_r, unknown_r, invalid_r = asyncio.run(run())
    assert slow_r[1] and "timed out" in slow_r[0]
    assert broken_r[1] and "db down" in broken_r[0]
    assert unknown_r[1] and "unknown tool" in unknown_r[0]
    assert invalid_r[1] and "invalid input" in invalid_r[0]


def test_api_definitions_are_strict_and_sorted():
    schema = {"type": "object", "properties": {}, "additionalProperties": False}

    async def noop(args):
        return "ok"

    registry = ToolRegistry([Tool("b", "", schema, noop), Tool("a", "", schema, noop)])
    defs = registry.api_definitions()
    assert [d["name"] for d in defs] == ["a", "b"]
    assert all(d["strict"] is True for d in defs)
