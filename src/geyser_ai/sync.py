"""Pull recent entries from the GeyserTimes REST API v5 into DuckDB.

The full archive is downloaded once and never re-fetched automatically. This
module only tops it up with the entries logged since the snapshot, using the
documented `entries_recent/{minutes}` endpoint.

GeyserTimes' own usage policy says polling the same URL more than once a minute
is abusive, so the default TTL here is five minutes and every response is
cached. One request per sync, never a crawl.
"""

from __future__ import annotations

import datetime as dt
import threading
import time
from typing import Any

import duckdb
import httpx

from .config import DB_PATH, USER_AGENT

API_BASE = "https://www.geysertimes.org/api/v5"
TTL_SECONDS = 300
# Never ask for more than a week in one request, however stale the snapshot is.
MAX_LOOKBACK_MIN = 7 * 24 * 60
MIN_LOOKBACK_MIN = 60

_lock = threading.Lock()
_state: dict[str, Any] = {
    "last_attempt": 0.0,
    "last_success": None,
    "n_last": 0,
    "n_total": 0,
    "error": None,
    "lookback_min": 0,
}

# API field -> `recent_eruptions` column. The API uses camelCase where the
# archive TSV uses snake_case, but the underlying fields are the same.
_FLAGS = {
    "exact": "exact",
    "ns": "near_start",
    "ie": "in_eruption",
    "E": "electronic",
    "A": "approximate",
    "wc": "webcam",
    "ini": "initial",
    "maj": "major",
    "min": "minor",
    "q": "questionable",
}


def _ensure_table(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS recent_eruptions (
            eruption_id BIGINT PRIMARY KEY,
            geyser      VARCHAR,
            epoch       BIGINT,
            ts_utc      TIMESTAMP WITH TIME ZONE,
            exact       BOOLEAN, near_start BOOLEAN, in_eruption BOOLEAN,
            electronic  BOOLEAN, approximate BOOLEAN, webcam BOOLEAN,
            initial     BOOLEAN, major BOOLEAN, minor BOOLEAN, questionable BOOLEAN,
            duration_seconds DOUBLE,
            entrant     VARCHAR,
            observer    VARCHAR,
            comment     VARCHAR,
            primary_id  BIGINT,
            fetched_at  TIMESTAMP
        )
        """
    )


def _archive_max_epoch(con: duckdb.DuckDBPyConnection) -> int | None:
    row = con.execute("SELECT max(epoch) FROM eruptions").fetchone()
    return int(row[0]) if row and row[0] is not None else None


def _needed_lookback(con: duckdb.DuckDBPyConnection) -> int:
    """Minutes to request: enough to bridge the gap since the newest known row."""
    epochs = [_archive_max_epoch(con)]
    try:
        row = con.execute("SELECT max(epoch) FROM recent_eruptions").fetchone()
        if row and row[0] is not None:
            epochs.append(int(row[0]))
    except duckdb.Error:
        pass
    known = max([e for e in epochs if e is not None], default=None)
    if known is None:
        return MIN_LOOKBACK_MIN
    gap_min = (time.time() - known) / 60.0
    # Overlap by an hour so nothing falls between two syncs.
    return int(min(max(gap_min + 60, MIN_LOOKBACK_MIN), MAX_LOOKBACK_MIN))


def _as_bool(v: Any) -> bool:
    return str(v) in {"1", "true", "True"}


def _as_int(v: Any) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _as_float(v: Any) -> float | None:
    try:
        f = float(v)
        return f if f > 0 else None
    except (TypeError, ValueError):
        return None


def sync_recent(
    ttl: int = TTL_SECONDS, force: bool = False, db_path=DB_PATH, minutes: int | None = None
) -> dict[str, Any]:
    """Fetch and store entries logged since the newest row we already have.

    Returns a status dict. Never raises on network failure -- a stale prediction
    with an honest age is better than a broken endpoint, and callers surface the
    `error` field rather than 500ing.
    """
    with _lock:
        now = time.time()
        age = now - _state["last_attempt"]
        if not force and age < ttl:
            return {**_state, "cached": True, "seconds_until_refresh": int(ttl - age)}
        _state["last_attempt"] = now

        con = duckdb.connect(str(db_path))
        try:
            _ensure_table(con)
            lookback = minutes or _needed_lookback(con)
            url = f"{API_BASE}/entries_recent/{lookback}"
            try:
                resp = httpx.get(
                    url,
                    headers={"User-Agent": USER_AGENT},
                    follow_redirects=True,
                    timeout=30.0,
                )
                resp.raise_for_status()
                payload = resp.json()
            except (httpx.HTTPError, ValueError) as exc:
                _state["error"] = f"{type(exc).__name__}: {exc}"
                return {**_state, "cached": False}

            if payload.get("status") != "success":
                _state["error"] = f"API status {payload.get('status')!r}"
                return {**_state, "cached": False}

            entries = payload.get("entries") or []
            rows = []
            for e in entries:
                eid = _as_int(e.get("eruptionID"))
                epoch = _as_int(e.get("time"))
                geyser = (e.get("geyser") or "").strip()
                if eid is None or epoch is None or not geyser:
                    continue
                pid = _as_int(e.get("primaryID"))
                # Same rule as the archive: keep primaries, drop questionable.
                if pid is not None and pid != eid:
                    continue
                if _as_bool(e.get("q")):
                    continue
                rows.append(
                    (
                        eid,
                        geyser,
                        epoch,
                        dt.datetime.fromtimestamp(epoch, tz=dt.timezone.utc),
                        *[_as_bool(e.get(k)) for k in _FLAGS],
                        _as_float(e.get("durationSec")),
                        e.get("entrant"),
                        e.get("observer"),
                        e.get("comment"),
                        pid,
                        dt.datetime.now(),
                    )
                )

            inserted = 0
            if rows:
                con.executemany(
                    """
                    INSERT OR REPLACE INTO recent_eruptions VALUES
                    (?,?,?,?, ?,?,?,?,?,?,?,?,?,?, ?,?,?,?,?,?)
                    """,
                    rows,
                )
                inserted = len(rows)

            total = con.execute("SELECT count(*) FROM recent_eruptions").fetchone()[0]
            _state.update(
                last_success=now,
                n_last=inserted,
                n_total=int(total),
                error=None,
                lookback_min=lookback,
            )
            return {**_state, "cached": False}
        finally:
            con.close()


def sync_status() -> dict[str, Any]:
    return dict(_state)
