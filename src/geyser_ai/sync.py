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
    "n_revisions": 0,
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
    # Edits to entries we have already served. GeyserTimes exposes no edit
    # history, only the current row, so the only way to know that an anchor's
    # `ini` flag was removed two hours after entry is to have seen both states.
    # The dashboard reads this to say WHY a prediction jumped: on 2026-09-07 a
    # Lion visitor watched the card flip from ~19:00 to a 22:00-07:00 window
    # with no explanation, because the entrant un-flagged the initial.
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS entry_revisions (
            eruption_id BIGINT,
            geyser      VARCHAR,
            field       VARCHAR,
            old_value   VARCHAR,
            new_value   VARCHAR,
            observed_at TIMESTAMP WITH TIME ZONE
        )
        """
    )


# The fields whose edit changes a prediction: the anchor's time, and the flags
# the conditional models branch on. Everything else (comments, observer) is
# noise for this purpose.
_REVISION_FIELDS = ("epoch", "initial", "major", "minor")
REVISION_RETENTION_DAYS = 7


def _record_revisions(
    con: duckdb.DuckDBPyConnection,
    rows: list[tuple],
    withdrawn: list[tuple[int, str]],
    observed_at: dt.datetime,
) -> int:
    """Diff incoming entries against what we already hold and log every change.

    `rows` are the tuples about to be upserted; `withdrawn` are (id, geyser)
    pairs now flagged questionable, which are deleted so a retracted anchor
    stops anchoring anything.
    """
    ids = [r[0] for r in rows] + [w[0] for w in withdrawn]
    if not ids:
        return 0
    existing = {
        int(eid): (int(epoch), bool(ini), bool(maj), bool(mnr))
        for eid, epoch, ini, maj, mnr in con.execute(
            "SELECT eruption_id, epoch, initial, major, minor FROM recent_eruptions "
            "WHERE eruption_id IN (SELECT unnest(?::BIGINT[]))",
            [ids],
        ).fetchall()
    }
    flag_idx = {name: 4 + i for i, name in enumerate(_FLAGS.values())}
    revs: list[tuple] = []
    for r in rows:
        prev = existing.get(r[0])
        if prev is None:
            continue
        incoming = (
            int(r[2]),
            bool(r[flag_idx["initial"]]),
            bool(r[flag_idx["major"]]),
            bool(r[flag_idx["minor"]]),
        )
        for field, old, new in zip(_REVISION_FIELDS, prev, incoming, strict=True):
            if old != new:
                revs.append((r[0], r[1], field, str(old), str(new), observed_at))
    for eid, geyser in withdrawn:
        if eid in existing:
            revs.append((eid, geyser, "questionable", "False", "True", observed_at))
            con.execute("DELETE FROM recent_eruptions WHERE eruption_id = ?", [eid])
    if revs:
        con.executemany("INSERT INTO entry_revisions VALUES (?,?,?,?,?,?)", revs)
    con.execute(
        "DELETE FROM entry_revisions WHERE observed_at < ?",
        [observed_at - dt.timedelta(days=REVISION_RETENTION_DAYS)],
    )
    return len(revs)


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
            withdrawn: list[tuple[int, str]] = []
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
                    withdrawn.append((eid, geyser))
                    continue
                rows.append(
                    (
                        eid,
                        geyser,
                        epoch,
                        dt.datetime.fromtimestamp(epoch, tz=dt.UTC),
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
            try:
                n_rev = _record_revisions(con, rows, withdrawn, dt.datetime.now(tz=dt.UTC))
            except duckdb.Error as exc:  # bookkeeping must never block the sync
                n_rev = 0
                _state["revision_error"] = f"{type(exc).__name__}: {exc}"
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
                n_revisions=n_rev,
                error=None,
                lookback_min=lookback,
            )
            return {**_state, "cached": False}
        finally:
            con.close()


def sync_status() -> dict[str, Any]:
    return dict(_state)


def entry_revisions(eruption_id: int, hours: float = 24.0, db_path=DB_PATH) -> list[dict[str, Any]]:
    """Edits we have witnessed to one entry, oldest first. Empty when none."""
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        has = con.execute(
            "SELECT count(*) FROM duckdb_tables() WHERE table_name = 'entry_revisions'"
        ).fetchone()[0]
        if not has:
            return []
        rows = con.execute(
            """
            SELECT field, old_value, new_value, observed_at FROM entry_revisions
            WHERE eruption_id = ? AND observed_at >= now() - to_seconds(?)
            ORDER BY observed_at
            """,
            [int(eruption_id), int(hours * 3600)],
        ).fetchall()
    finally:
        con.close()
    out = []
    for field, old, new, at in rows:
        at = at if at.tzinfo else at.replace(tzinfo=dt.UTC)
        out.append(
            {
                "field": field,
                "old": _from_str(old),
                "new": _from_str(new),
                "observed_utc": at.astimezone(dt.UTC).isoformat(),
            }
        )
    return out


def _from_str(v: str) -> Any:
    if v in ("True", "False"):
        return v == "True"
    try:
        return int(v)
    except ValueError:
        return v
