"""MCP tools. Thin wrappers over `service`, so this checks wiring and schemas."""

from __future__ import annotations

import anyio
import pytest

from geyser_ai.config import TARGET_GEYSERS
from geyser_ai.mcp_server import mcp

EXPECTED = {
    "get_predictions",
    "get_recent_eruptions",
    "get_geyser_stats",
    "get_scoreboard",
    "get_recent_comparisons",
}


def run(coro):
    return anyio.run(lambda: coro)


@pytest.fixture(scope="module")
def tools():
    return {t.name: t for t in anyio.run(mcp.list_tools)}


class TestSchemas:
    def test_exactly_the_documented_tools(self, tools):
        assert set(tools) == EXPECTED

    def test_every_tool_is_described(self, tools):
        for name, t in tools.items():
            assert t.description and len(t.description) > 30, f"{name} needs a real description"

    def test_geyser_arguments_are_enumerated(self, tools):
        """Enumerated names stop a model inventing 'Old Faithfull'."""
        for name in EXPECTED:
            schema = tools[name].input_schema
            prop = schema["properties"]["geyser"]
            blob = str(prop)
            for g in TARGET_GEYSERS:
                assert g in blob, f"{name}: {g} missing from the geyser schema"


class TestCalls:
    def test_predictions_returns_all_geysers(self):
        res = anyio.run(lambda: mcp.call_tool("get_predictions", {}))
        data = _payload(res)
        # targets plus the Steamboat context card
        assert len(data["predictions"]) == len(TARGET_GEYSERS) + 1

    def test_predictions_strips_density_by_default(self):
        data = _payload(anyio.run(lambda: mcp.call_tool("get_predictions", {"geyser": "Grand"})))
        p = data["predictions"][0]
        assert p["geyser"] == "Grand"
        assert "density" not in p, "density is verbose; omit unless asked"

    def test_predictions_can_include_density(self):
        data = _payload(
            anyio.run(
                lambda: mcp.call_tool(
                    "get_predictions", {"geyser": "Grand", "include_density": True}
                )
            )
        )
        assert data["predictions"][0]["density"]

    def test_recent_eruptions(self):
        data = _payload(
            anyio.run(
                lambda: mcp.call_tool("get_recent_eruptions", {"hours": 48, "targets_only": True})
            )
        )
        assert data["hours"] == 48
        assert set(e["geyser"] for e in data["eruptions"]) <= set(TARGET_GEYSERS)

    def test_geyser_stats(self):
        data = _payload(anyio.run(lambda: mcp.call_tool("get_geyser_stats", {"geyser": "Daisy"})))
        s = data["stats"][0]
        assert s["geyser"] == "Daisy"
        assert s["p05_interval_min"] <= s["median_interval_min"] <= s["p95_interval_min"]

    def test_stats_for_all_geysers(self):
        data = _payload(anyio.run(lambda: mcp.call_tool("get_geyser_stats", {})))
        assert len(data["stats"]) == len(TARGET_GEYSERS)


def _payload(res):
    """Unwrap a tool result across mcp package shapes: CallToolResult, a
    (content, structured) tuple, or a bare content list."""
    import json

    structured = getattr(res, "structuredContent", None) or getattr(res, "structured_content", None)
    if isinstance(structured, dict) and structured:
        return structured
    content = getattr(res, "content", res)
    if isinstance(content, tuple):
        content = content[0]
    if isinstance(content, list):
        return json.loads(content[0].text)
    return content
