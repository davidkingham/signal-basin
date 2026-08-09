"""Minimal MCP server (stdio) exposing the same reads as the HTTP API.

Thin wrappers over `service.py` -- no logic lives here, so the two transports
cannot disagree about what a prediction is. Tool schemas are derived from the
type hints and docstrings below.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from mcp.server import MCPServer
from pydantic import Field

from .config import TARGET_GEYSERS
from .service import (
    get_geyser_stats,
    get_predictions,
    get_recent_comparisons,
    get_recent_eruptions,
    get_scoreboard,
)

mcp = MCPServer(
    name="geyser-ai",
    version="0.1.0",
    instructions=(
        "Probabilistic next-eruption predictions for Yellowstone geysers, built "
        "on public GeyserTimes data. Predictions are full probability distributions: "
        "always report the 50%/90% windows alongside the point estimate, and mention "
        "expected_missed_eruptions when it is non-trivial, since a long silence usually "
        "means nobody was observing rather than that the geyser is overdue."
    ),
)

GeyserName = Literal[
    "Old Faithful",
    "Grand",
    "Daisy",
    "Riverside",
    "Castle",
    "Great Fountain",
    "Beehive",
    "Fountain",
    "Lion",
    "Artemisia",
    "Lone Star",
    "Till",
    "Little Squirt",
]


@mcp.tool(name="get_predictions")
def get_predictions_tool(
    geyser: Annotated[
        GeyserName | None, Field(description="Limit to one geyser; omit for all.")
    ] = None,
    include_density: Annotated[
        bool, Field(description="Include the probability density curve (verbose).")
    ] = False,
) -> dict[str, Any]:
    """Next-eruption predictions as full probability distributions.

    Returns the most likely time plus 50% and 90% windows for each geyser, sorted
    by soonest. Accounts for eruptions that may have occurred without being logged.
    """
    res = get_predictions(geysers=[geyser] if geyser else None, density_points=48)
    if not include_density:
        for p in res["predictions"]:
            p.pop("density", None)
    return res


@mcp.tool(name="get_recent_eruptions")
def get_recent_eruptions_tool(
    hours: Annotated[int, Field(ge=1, le=168, description="Lookback window.")] = 24,
    geyser: Annotated[GeyserName | None, Field(description="Optional filter.")] = None,
    targets_only: Annotated[bool, Field(description="Restrict to the modelled geysers.")] = False,
) -> dict[str, Any]:
    """Eruptions logged recently, newest first, from GeyserTimes data."""
    names = [geyser] if geyser else (list(TARGET_GEYSERS) if targets_only else None)
    return get_recent_eruptions(hours=hours, geysers=names)


@mcp.tool(name="get_geyser_stats")
def get_geyser_stats_tool(
    geyser: Annotated[GeyserName | None, Field(description="Omit for all.")] = None,
) -> dict[str, Any]:
    """Interval statistics per geyser.

    Count, median, mean, standard deviation and the 5th/95th percentiles over
    validity-filtered intervals, plus the same for the last 12 months.
    """
    return get_geyser_stats(geyser)


@mcp.tool(name="get_scoreboard")
def get_scoreboard_tool(
    days: Annotated[float, Field(ge=1, le=365, description="Rolling window.")] = 30.0,
    geyser: Annotated[GeyserName | None, Field(description="Omit for all.")] = None,
) -> dict[str, Any]:
    """How this project, the NPS and Geysers.net have actually done, per geyser.

    Accumulated prospectively -- GeyserTimes publishes only currently-open
    predictions, so there is no history to backfill and `n` may be very small.
    Each source is scored in the window it states itself, so always read
    `in_window_rate` next to `median_window_width_min`; a wide claimed window is
    easier to hit. Report `n` whenever quoting any of these numbers.
    """
    return get_scoreboard(days=days, geyser=geyser)


@mcp.tool(name="get_recent_comparisons")
def get_recent_comparisons_tool(
    limit: Annotated[int, Field(ge=1, le=200, description="How many eruptions.")] = 20,
    geyser: Annotated[GeyserName | None, Field(description="Optional filter.")] = None,
) -> dict[str, Any]:
    """Recent eruptions with each source's prediction beside what actually happened."""
    return get_recent_comparisons(limit=limit, geyser=geyser)


def main() -> None:
    # stdio is the protocol channel; keep httpx's per-request chatter off it.
    import logging

    logging.getLogger("httpx").setLevel(logging.WARNING)
    mcp.run("stdio")


if __name__ == "__main__":
    main()
