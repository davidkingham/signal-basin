"""Durable storage for the prediction ledger.

There is no historical prediction data anywhere -- not from the API, not from
the nightly archive -- so every number on the scoreboard has to be earned by
logging predictions as they are published and scoring them as eruptions arrive.
That makes persistence the whole feature: if the ledger does not survive a
container restart, the scoreboard resets to n=0 every time the service sleeps.

Two backends, chosen by environment so the same code runs in both places:

local file
    `uv run geyser-ai serve` writes `ledger.json` next to the DuckDB file.

HTTP
    The deployed container has an ephemeral disk, so it PUTs and GETs the ledger
    through the same virtual hostname it already uses to pull the snapshot. The
    Worker intercepts that host and answers from R2, which means the container
    still holds no object-storage credentials.

Nothing in here raises. A ledger that cannot be read starts empty and says so;
a ledger that cannot be written stays in memory and retries on the next flush.
The dashboard degrades to "no scored eruptions yet", which is honest, rather
than 500ing a page whose main job is predicting geysers.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
import threading
from pathlib import Path
from typing import Any

import httpx

from .config import CALIBRATION_EPOCH, DATA_DIR
from .scoring import LoggedPrediction, ScoredPrediction

LOG = logging.getLogger(__name__)

VERSION = 1
# Scored outcomes older than this fall out. The scoreboard is a rolling window,
# and an unbounded document would eventually be silly to rewrite every cycle.
RETENTION_DAYS = 180
# A ledger far bigger than this means something has gone wrong upstream.
MAX_BYTES = 8_000_000
# Open predictions kept per (source, geyser). Only the latest before an eruption
# is ever scored; the tail exists so supersession stays visible.
MAX_OPEN_PER_SERIES = 12

LEDGER_URL = os.environ.get("GEYSER_AI_LEDGER_URL", "").strip()
LEDGER_PATH = Path(os.environ.get("GEYSER_AI_LEDGER_PATH", DATA_DIR / "ledger.json"))


class LedgerStore:
    """Where the ledger document lives."""

    def read(self) -> dict[str, Any] | None:  # pragma: no cover - interface
        raise NotImplementedError

    def write(self, doc: dict[str, Any]) -> None:  # pragma: no cover - interface
        raise NotImplementedError


class FileLedgerStore(LedgerStore):
    def __init__(self, path: Path) -> None:
        self.path = path

    def read(self) -> dict[str, Any] | None:
        if not self.path.exists():
            return None
        return json.loads(self.path.read_text())

    def write(self, doc: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(doc, separators=(",", ":")))
        os.replace(tmp, self.path)


class HttpLedgerStore(LedgerStore):
    """Object storage reached through the Worker's outbound handler."""

    def __init__(self, url: str) -> None:
        self.url = url

    def read(self) -> dict[str, Any] | None:
        resp = httpx.get(self.url, timeout=30.0)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()

    def write(self, doc: dict[str, Any]) -> None:
        resp = httpx.put(
            self.url,
            content=json.dumps(doc, separators=(",", ":")).encode(),
            headers={"Content-Type": "application/json"},
            timeout=60.0,
        )
        resp.raise_for_status()


def default_store() -> LedgerStore:
    return HttpLedgerStore(LEDGER_URL) if LEDGER_URL else FileLedgerStore(LEDGER_PATH)


class Ledger:
    """In-memory ledger with a durable backing store."""

    def __init__(self, store: LedgerStore | None = None) -> None:
        self._store = store or default_store()
        self._lock = threading.RLock()
        self._loaded = False
        self.open: dict[str, LoggedPrediction] = {}
        self.scored: list[ScoredPrediction] = []
        self.started_utc: str | None = None
        self.stats: dict[str, int] = {"superseded": 0, "expired": 0, "beyond_horizon": 0}
        self.error: str | None = None

    # -- persistence ----------------------------------------------------

    def load(self) -> None:
        """Read the ledger once. A missing or unreadable document starts a new one."""
        with self._lock:
            if self._loaded:
                return
            self._loaded = True
            try:
                doc = self._store.read()
            except Exception as exc:
                self.error = f"load failed: {type(exc).__name__}: {exc}"
                LOG.warning("ledger load failed: %s", exc)
                doc = None

            if not doc:
                self.started_utc = _now_iso()
                return

            try:
                self.open = {p["key"]: LoggedPrediction.from_dict(p) for p in doc.get("open", [])}
                self.scored = [
                    sp
                    for sp in (ScoredPrediction.from_dict(s) for s in doc.get("scored", []))
                    if sp.actual_epoch >= CALIBRATION_EPOCH
                ]
                started = doc.get("started_utc") or _now_iso()
                # The record starts at the calibration epoch for every source
                # alike, so the header never claims coverage it no longer shows.
                floor = dt.datetime.fromtimestamp(CALIBRATION_EPOCH, dt.UTC).isoformat()
                self.started_utc = max(started, floor)
                self.stats.update(doc.get("stats") or {})
                self.error = None
            except (KeyError, TypeError, ValueError) as exc:
                # A corrupt document must not take the service down with it.
                self.error = f"parse failed: {type(exc).__name__}: {exc}"
                LOG.warning("ledger parse failed, starting fresh: %s", exc)
                self.open, self.scored = {}, []
                self.started_utc = _now_iso()

    def flush(self) -> bool:
        """Persist. Returns False (and records why) rather than raising."""
        with self._lock:
            doc = self.to_doc()
            payload = json.dumps(doc, separators=(",", ":"))
            if len(payload) > MAX_BYTES:
                self.error = f"ledger too large ({len(payload)} bytes), not written"
                LOG.error(self.error)
                return False
            try:
                self._store.write(doc)
            except Exception as exc:
                self.error = f"write failed: {type(exc).__name__}: {exc}"
                LOG.warning("ledger write failed: %s", exc)
                return False
            self.error = None
            return True

    def to_doc(self) -> dict[str, Any]:
        return {
            "version": VERSION,
            "started_utc": self.started_utc,
            "stats": dict(self.stats),
            "open": [p.to_dict() for p in self.open.values()],
            "scored": [s.to_dict() for s in self.scored],
        }

    # -- mutation -------------------------------------------------------

    def add_open(self, predictions: list[LoggedPrediction]) -> int:
        """Record newly published predictions. Keyed, so re-seeing one is free."""
        with self._lock:
            added = 0
            touched: set[tuple[str, str]] = set()
            for pred in predictions:
                if pred.key not in self.open:
                    self.open[pred.key] = pred
                    added += 1
                    touched.add((pred.source, pred.geyser))
            for source, geyser in touched:
                self._cap_open(source, geyser)
            return added

    def _cap_open(self, source: str, geyser: str) -> None:
        """Keep only the most recent few open predictions per source and geyser.

        Most predictions are keyed on their predicted minute, so re-running the
        same forecast is free. Beehive during an Indicator event is the exception
        -- its nowcast is anchored to the present, so every recompute is a
        genuinely new answer. Only the latest one before an eruption is ever
        scored, so keeping a short tail costs nothing and bounds the document.
        """
        mine = [p for p in self.open.values() if p.source == source and p.geyser == geyser]
        if len(mine) <= MAX_OPEN_PER_SERIES:
            return
        for stale in sorted(mine, key=lambda p: p.issued_epoch)[:-MAX_OPEN_PER_SERIES]:
            self.open.pop(stale.key, None)

    def already_scored(self) -> set[tuple[str, int]]:
        with self._lock:
            return {(s.source, s.eruption_id) for s in self.scored}

    def apply(self, result: Any) -> None:
        """Absorb a `scoring.MatchResult`."""
        with self._lock:
            self.scored.extend(result.scored)
            self.open = {p.key: p for p in result.still_open}
            self.stats["superseded"] += result.superseded
            self.stats["expired"] += result.expired
            self.stats["beyond_horizon"] += result.beyond_horizon
            self._trim()

    def _trim(self) -> None:
        cutoff = int(dt.datetime.now(dt.UTC).timestamp()) - RETENTION_DAYS * 86400
        self.scored = [s for s in self.scored if s.actual_epoch >= max(cutoff, CALIBRATION_EPOCH)]

    # -- reads ----------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "started_utc": self.started_utc,
                "n_open": len(self.open),
                "n_scored": len(self.scored),
                "stats": dict(self.stats),
                "error": self.error,
            }


def _now_iso() -> str:
    return dt.datetime.now(dt.UTC).isoformat()


_ledger: Ledger | None = None
_ledger_lock = threading.Lock()


def get_ledger() -> Ledger:
    """Process-wide ledger, loaded on first use."""
    global _ledger
    with _ledger_lock:
        if _ledger is None:
            _ledger = Ledger()
    _ledger.load()
    return _ledger


def reset_ledger(store: LedgerStore | None = None) -> Ledger:
    """Replace the process-wide ledger. For tests and for `serve` startup."""
    global _ledger
    with _ledger_lock:
        _ledger = Ledger(store)
    _ledger.load()
    return _ledger
