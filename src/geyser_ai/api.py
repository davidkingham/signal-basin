"""FastAPI app: JSON endpoints plus the single-page dashboard."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse

from .config import DB_PATH, TARGET_GEYSERS
from .service import (
    get_geyser_stats,
    get_health,
    get_predictions,
    get_recent_comparisons,
    get_recent_eruptions,
    get_scoreboard,
)

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(
    title="Geyser AI",
    description=(
        "Probabilistic next-eruption predictions for Yellowstone geysers, built on "
        "public GeyserTimes data. Full distributions, honest uncertainty."
    ),
    version="0.1.0",
)


def _resolve(name: str) -> str:
    """Match a geyser name case-insensitively against the target list."""
    for g in TARGET_GEYSERS:
        if g.lower() == name.lower():
            return g
    raise HTTPException(
        status_code=404,
        detail=f"Unknown geyser {name!r}. Available: {list(TARGET_GEYSERS)}",
    )


@app.get("/api/health")
def health() -> dict:
    if not DB_PATH.exists():
        raise HTTPException(503, "No database. Run `uv run geyser-ai ingest` first.")
    return get_health()


@app.get("/api/predictions")
def predictions(
    hours: float = Query(12.0, ge=1, le=48, description="Density window length."),
    points: int = Query(96, ge=8, le=512, description="Density samples in the window."),
) -> dict:
    if not DB_PATH.exists():
        raise HTTPException(503, "No database. Run `uv run geyser-ai ingest` first.")
    return get_predictions(hours=hours, density_points=points)


@app.get("/api/predictions/{geyser}")
def prediction_one(
    geyser: str,
    hours: float = Query(12.0, ge=1, le=48),
    points: int = Query(240, ge=8, le=1024, description="Denser curve for one geyser."),
) -> dict:
    if not DB_PATH.exists():
        raise HTTPException(503, "No database. Run `uv run geyser-ai ingest` first.")
    name = _resolve(geyser)
    res = get_predictions(geysers=[name], hours=hours, density_points=points)
    pred = res["predictions"][0] if res["predictions"] else None
    if pred is None or "error" in pred:
        raise HTTPException(503, (pred or {}).get("error", "no prediction available"))
    return {
        "generated_utc": res["generated_utc"],
        "park_time": res["park_time"],
        "window_hours": hours,
        "prediction": pred,
        "sync": res["sync"],
    }


@app.get("/api/eruptions/recent")
def eruptions_recent(
    hours: int = Query(24, ge=1, le=168),
    geyser: str | None = Query(None, description="Optional single-geyser filter."),
    targets_only: bool = Query(False, description="Restrict to the seven modelled geysers."),
) -> dict:
    if not DB_PATH.exists():
        raise HTTPException(503, "No database. Run `uv run geyser-ai ingest` first.")
    names = [_resolve(geyser)] if geyser else (list(TARGET_GEYSERS) if targets_only else None)
    return get_recent_eruptions(hours=hours, geysers=names)


@app.get("/api/stats")
def stats(geyser: str | None = Query(None)) -> dict:
    if not DB_PATH.exists():
        raise HTTPException(503, "No database. Run `uv run geyser-ai ingest` first.")
    return get_geyser_stats(_resolve(geyser) if geyser else None)


@app.get("/api/scoreboard")
def scoreboard(
    days: float = Query(30.0, ge=1, le=365, description="Rolling window to score over."),
    geyser: str | None = Query(None, description="Optional single-geyser filter."),
) -> dict:
    """How this project, the NPS and Geysers.net have actually done, per geyser.

    Accumulated prospectively: GeyserTimes publishes only currently-open
    predictions, so there is no history to backfill from and `n` starts at zero.
    """
    return get_scoreboard(days=days, geyser=_resolve(geyser) if geyser else None)


@app.get("/api/comparisons/recent")
def comparisons_recent(
    limit: int = Query(20, ge=1, le=200),
    geyser: str | None = Query(None, description="Optional single-geyser filter."),
) -> dict:
    """The most recent scored eruptions, with every source's prediction beside the actual."""
    return get_recent_comparisons(limit=limit, geyser=_resolve(geyser) if geyser else None)


@app.get("/", response_class=HTMLResponse)
def dashboard() -> HTMLResponse:
    index = STATIC_DIR / "index.html"
    if not index.exists():
        raise HTTPException(500, "Dashboard asset missing.")
    return HTMLResponse(index.read_text())


def serve(host: str = "127.0.0.1", port: int = 8000, reload: bool = False) -> None:
    import uvicorn

    uvicorn.run("geyser_ai.api:app" if reload else app, host=host, port=port, reload=reload)
