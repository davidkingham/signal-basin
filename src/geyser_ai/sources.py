"""Next-eruption predictions published by other people on GeyserTimes.

GeyserTimes exposes exactly one predictions route, `predictions_latest`: the
predictions that are open *right now*. There is no date-ranged predictions
endpoint (`/predictions/{from}/{to}` and `/predictions_recent/{minutes}` both
404) and the nightly archive dump contains eruptions and notes only. So there is
no retrospective scoring to be had at any price -- the only honest comparison is
to log what each source says, as it says it, and score it afterwards.

One request returns every open prediction for every geyser, so logging all
sources costs exactly one HTTP call per refresh cycle, on the same five-minute
TTL and with the same identifying User-Agent as the eruption sync.

Two predictors publish for the geysers modelled here:

`nps`
    The National Park Service's visitor-centre predictions. They reach
    GeyserTimes through an upload rather than a human observer, posted by the
    `GeyserTimes` account (userID 208) and marked in the comment as coming from
    the NPS/CartoDB system. Both conditions are required to classify a
    prediction as NPS, so anything else that account ever posts is not silently
    attributed to the Park Service.

`geysers_net`
    Geysers.net (userID 44), a long-running third-party predictor. Its
    predictions carry a stated method -- typically "Add average interval" -- and
    a self-reported probability.

Both publish a point prediction *and* an explicit window. Those windows are what
each source actually claims, and they differ by an order of magnitude between
geysers, so scoring uses each source's own window rather than imposing ours.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any

import httpx

from .config import TARGET_GEYSERS, USER_AGENT

API_BASE = "https://www.geysertimes.org/api/v5"
PREDICTIONS_URL = f"{API_BASE}/predictions_latest"

# Same policy as the eruption sync: GeyserTimes calls polling one URL more than
# once a minute abusive, so nothing here runs more often than every five.
TTL_SECONDS = 300

NPS_USER_ID = "208"
GEYSERS_NET_USER_ID = "44"

SOURCE_LABELS: dict[str, dict[str, str]] = {
    "geyser_ai": {
        "label": "Signal Basin",
        "window_label": "90%",
        "description": (
            "This project. A full predictive distribution rather than a point; the stated "
            "window is its nominal 90% interval, and the 50% interval is reported alongside."
        ),
    },
    "nps": {
        "label": "NPS",
        "window_label": "stated",
        "description": (
            "National Park Service visitor-centre predictions, uploaded to GeyserTimes from "
            "the NPS/CartoDB system. Scored against the window the Park Service states."
        ),
    },
    "geysers_net": {
        "label": "Geysers.net",
        "window_label": "stated",
        "description": (
            "Geysers.net algorithmic predictions published on GeyserTimes, usually by adding "
            "an average interval. Scored against the window Geysers.net states."
        ),
    },
}

THIRD_PARTY_SOURCES = ("nps", "geysers_net")

_lock = threading.Lock()
_state: dict[str, Any] = {
    "last_attempt": 0.0,
    "last_success": None,
    "n_last": 0,
    "n_unclassified": 0,
    "error": None,
}

# Case-insensitive lookup back to the canonical spelling we model under.
_TARGETS = {g.lower(): g for g in TARGET_GEYSERS}


@dataclass(frozen=True)
class SourcePrediction:
    """One published prediction, normalised across predictors."""

    source: str
    geyser: str
    prediction_id: str
    issued_epoch: int
    predicted_epoch: int
    window_open_epoch: int | None
    window_close_epoch: int | None
    expiration_epoch: int | None
    detail: str

    @property
    def key(self) -> str:
        return f"{self.source}:{self.prediction_id}"


def classify(row: dict[str, Any]) -> str | None:
    """Which source a raw prediction belongs to, or None if we cannot tell.

    Deliberately conservative: an unrecognised predictor is dropped rather than
    lumped in with one of the two we name, because the whole point of the
    comparison is that the attribution is trustworthy.
    """
    user_id = str(row.get("userID") or "").strip()
    comment = str(row.get("comment") or "")
    if user_id == NPS_USER_ID and "NPS" in comment.upper():
        return "nps"
    if user_id == GEYSERS_NET_USER_ID:
        return "geysers_net"
    return None


def _epoch(value: Any) -> int | None:
    """Parse the API's epoch-or-ISO datetimes. Empty strings are genuinely absent."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        pass
    try:
        import datetime as dt

        return int(dt.datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


def _detail(row: dict[str, Any]) -> str:
    """The predictor's own description of what it did, for display."""
    method = str(row.get("method") or "").strip()
    comment = str(row.get("comment") or "").strip()
    text = method or comment
    interval = str(row.get("intervalNumber") or "1").strip()
    if interval not in ("", "1"):
        # The predictor is assuming an eruption was missed and predicting off a
        # doubled interval. Worth carrying: it explains a wide window.
        text = f"{text} (interval x{interval})".strip()
    return text[:160]


def parse_predictions(payload: dict[str, Any]) -> tuple[list[SourcePrediction], int]:
    """Normalise an API payload. Returns (predictions, count of rows we could not attribute)."""
    out: list[SourcePrediction] = []
    unclassified = 0

    for row in payload.get("predictions") or []:
        source = classify(row)
        if source is None:
            unclassified += 1
            continue

        geyser = _TARGETS.get(str(row.get("geyserName") or "").strip().lower())
        if geyser is None:
            continue

        # `futureEruptionNumber` 2 and up predict the eruption *after* next.
        # Scoring those against the next eruption would be plain unfair.
        future = str(row.get("futureEruptionNumber") or "1").strip()
        if future not in ("", "1"):
            continue

        predicted = _epoch(row.get("prediction"))
        issued = _epoch(row.get("timeEntered"))
        if predicted is None or issued is None:
            continue

        pid = str(row.get("predictionID") or "").strip()
        if not pid:
            continue

        out.append(
            SourcePrediction(
                source=source,
                geyser=geyser,
                prediction_id=pid,
                issued_epoch=issued,
                predicted_epoch=predicted,
                window_open_epoch=_epoch(row.get("windowOpen")),
                window_close_epoch=_epoch(row.get("windowClose")),
                expiration_epoch=_epoch(row.get("expiration")),
                detail=_detail(row),
            )
        )
    return out, unclassified


def fetch_predictions(ttl: int = TTL_SECONDS, force: bool = False) -> list[SourcePrediction]:
    """One polite request for every open third-party prediction.

    Returns an empty list rather than raising on any failure: a scoreboard that
    stops updating is a much smaller problem than a dashboard that 500s.
    """
    with _lock:
        now = time.time()
        if not force and now - _state["last_attempt"] < ttl:
            return []
        _state["last_attempt"] = now

        try:
            resp = httpx.get(
                PREDICTIONS_URL,
                headers={"User-Agent": USER_AGENT},
                follow_redirects=True,
                timeout=30.0,
            )
            resp.raise_for_status()
            payload = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            _state["error"] = f"{type(exc).__name__}: {exc}"
            return []

        if payload.get("status") != "success":
            _state["error"] = f"API status {payload.get('status')!r}"
            return []

        predictions, unclassified = parse_predictions(payload)
        _state.update(
            last_success=now,
            n_last=len(predictions),
            n_unclassified=unclassified,
            error=None,
        )
        return predictions


def status() -> dict[str, Any]:
    return dict(_state)
