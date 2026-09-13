"""Extend the archive's interval chain with what the live sync has pulled since.

`intervals` is built once, at ingest, from the archive snapshot. The five-minute
sync writes `recent_eruptions`, and until now only the ANCHOR read from it: every
model trained on a history that ended the day the snapshot was published. On
2026-09-13 production's newest training interval was 41 days old, which the
backtest prices at Daisy +18% CRPS, Grand +9%, Beehive and Fountain +7% -- more
than any model change in the calibration report, and growing daily.

This module chains the synced entries onto the archive's last eruption with the
same rules ingest applies -- cycle-event filter, 60-second dedupe, anchor-side
covariates -- and validates each gap against the archive's LAST local baseline
carried forward. The baseline is a centred +/-300-row median, slowly varying by
design, so the value at the snapshot's edge is the right one for the weeks that
follow; recomputing it over a few dozen new rows would be noisier, not better.
"""

from __future__ import annotations

import duckdb
import pandas as pd

from .ingest import (
    INTERVAL_MAX_MULT,
    INTERVAL_MIN_MULT,
    NON_CYCLE_MINOR_GEYSERS,
    SECOND_MODE_RATIO,
    SPARSE_SINGLES_GEYSERS,
)

# Every column `backtest.load_all_intervals` selects, in its order, so the two
# frames concatenate without a reindex.
COLUMNS = [
    "geyser", "ts_utc", "ts_local", "epoch", "interval_min", "prev_interval_min",
    "prev_duration_seconds", "duration_seconds", "hour_local", "month_local",
    "year_local", "prev_hour_local", "prev_doy",
    "prev_webcam", "prev_electronic", "prev_approximate", "prev_in_eruption",
    "prev_minor", "prev_major", "prev_initial", "minor", "major", "initial",
    "webcam", "electronic", "approximate", "in_eruption", "near_start", "exact",
    "med_interval", "is_valid",
]  # fmt: skip


def _has_recent(con: duckdb.DuckDBPyConnection) -> bool:
    return bool(
        con.execute(
            "SELECT count(*) FROM duckdb_tables() WHERE table_name = 'recent_eruptions'"
        ).fetchone()[0]
    )


def recent_intervals(geyser: str, con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Intervals formed by synced entries newer than the archive, archive-style.

    Returns every such interval with `is_valid` and `med_interval` set, so the
    caller can keep the valid ones for training or all of them for diagnostics.
    Empty when the sync table is absent or holds nothing newer than the archive.
    """
    if not _has_recent(con):
        return pd.DataFrame(columns=COLUMNS)
    minor_ok = "AND NOT minor" if geyser in NON_CYCLE_MINOR_GEYSERS else ""
    sparse = geyser in SPARSE_SINGLES_GEYSERS
    df = con.execute(
        f"""
        WITH arch_max AS (
            SELECT max(epoch) AS m FROM eruptions WHERE geyser = ?
        ),
        -- The archive's last local baseline, per regime. A regime that never
        -- earned its own baseline (no minors) simply has no row here and falls
        -- back to the pooled one below.
        last_base AS (
            SELECT prev_minor, med_interval, med_long FROM (
                SELECT prev_minor, med_interval, med_long,
                       row_number() OVER (PARTITION BY prev_minor ORDER BY epoch DESC) AS rn
                FROM intervals WHERE geyser = ? AND med_interval IS NOT NULL
            ) WHERE rn = 1
        ),
        pooled_base AS (
            SELECT med_interval, med_long FROM intervals
            WHERE geyser = ? AND med_interval IS NOT NULL ORDER BY epoch DESC LIMIT 1
        ),
        last_interval AS (
            SELECT interval_min FROM intervals WHERE geyser = ? ORDER BY epoch DESC LIMIT 1
        ),
        arch_last AS (
            SELECT eruption_id, geyser, epoch, ts_utc, exact, near_start, in_eruption,
                   electronic, approximate, webcam, initial, major, minor, duration_seconds
            FROM eruptions WHERE geyser = ? {minor_ok}
            ORDER BY epoch DESC LIMIT 1
        ),
        fresh AS (
            SELECT eruption_id, geyser, epoch, ts_utc, exact, near_start, in_eruption,
                   electronic, approximate, webcam, initial, major, minor, duration_seconds
            FROM recent_eruptions
            WHERE geyser = ? AND epoch > (SELECT m FROM arch_max)
              AND NOT COALESCE(questionable, false) {minor_ok}
        ),
        ev AS (SELECT * FROM arch_last UNION ALL SELECT * FROM fresh),
        deduped AS (
            SELECT *, LAG(epoch) OVER (ORDER BY epoch) AS prev_epoch_all FROM ev
        ),
        singles AS (
            SELECT * FROM deduped WHERE prev_epoch_all IS NULL OR epoch - prev_epoch_all > 60
        ),
        seq AS (
            SELECT *,
                   LAG(epoch)            OVER w AS prev_epoch,
                   LAG(ts_utc)           OVER w AS prev_ts_utc,
                   LAG(duration_seconds) OVER w AS prev_duration_seconds,
                   LAG(webcam)           OVER w AS prev_webcam,
                   LAG(electronic)       OVER w AS prev_electronic,
                   LAG(approximate)      OVER w AS prev_approximate,
                   LAG(in_eruption)      OVER w AS prev_in_eruption,
                   LAG(minor)            OVER w AS prev_minor,
                   LAG(major)            OVER w AS prev_major,
                   LAG(initial)          OVER w AS prev_initial
            FROM singles WINDOW w AS (ORDER BY epoch)
        ),
        raw_int AS (
            SELECT *, (epoch - prev_epoch) / 60.0 AS interval_min FROM seq
            WHERE prev_epoch IS NOT NULL
        ),
        based AS (
            SELECT r.*,
                   COALESCE(b.med_interval, p.med_interval) AS med_interval,
                   COALESCE(b.med_long, p.med_long)         AS med_long
            FROM raw_int r
            LEFT JOIN last_base b ON b.prev_minor = COALESCE(r.prev_minor, false)
            CROSS JOIN pooled_base p
        )
        SELECT
            r.geyser, r.ts_utc, timezone('America/Denver', r.ts_utc) AS ts_local, r.epoch,
            r.interval_min,
            COALESCE(LAG(r.interval_min) OVER (ORDER BY r.epoch),
                     (SELECT interval_min FROM last_interval)) AS prev_interval_min,
            r.prev_duration_seconds, r.duration_seconds,
            hour(timezone('America/Denver', r.ts_utc))  AS hour_local,
            month(timezone('America/Denver', r.ts_utc)) AS month_local,
            year(timezone('America/Denver', r.ts_utc))  AS year_local,
            hour(timezone('America/Denver', r.prev_ts_utc))      AS prev_hour_local,
            dayofyear(timezone('America/Denver', r.prev_ts_utc)) AS prev_doy,
            COALESCE(r.prev_webcam, false)      AS prev_webcam,
            COALESCE(r.prev_electronic, false)  AS prev_electronic,
            COALESCE(r.prev_approximate, false) AS prev_approximate,
            COALESCE(r.prev_in_eruption, false) AS prev_in_eruption,
            COALESCE(r.prev_minor, false)       AS prev_minor,
            COALESCE(r.prev_major, false)       AS prev_major,
            COALESCE(r.prev_initial, false)     AS prev_initial,
            COALESCE(r.minor, false) AS minor, COALESCE(r.major, false) AS major,
            COALESCE(r.initial, false) AS initial, COALESCE(r.webcam, false) AS webcam,
            COALESCE(r.electronic, false) AS electronic,
            COALESCE(r.approximate, false) AS approximate,
            COALESCE(r.in_eruption, false) AS in_eruption,
            COALESCE(r.near_start, false) AS near_start, COALESCE(r.exact, false) AS exact,
            r.med_interval,
            ((r.med_interval IS NOT NULL
              AND r.interval_min >= {INTERVAL_MIN_MULT} * r.med_interval
              AND r.interval_min <= {INTERVAL_MAX_MULT} * r.med_interval)
             OR ({"false" if sparse else "true"}
                 AND r.med_interval IS NOT NULL AND r.med_long IS NOT NULL
                 AND r.med_long >= {SECOND_MODE_RATIO} * r.med_interval
                 AND r.interval_min >= {INTERVAL_MIN_MULT} * r.med_long
                 AND r.interval_min <= {INTERVAL_MAX_MULT} * r.med_long)) AS is_valid
        FROM based r
        WHERE r.interval_min > 0
        ORDER BY r.epoch
        """,
        [geyser] * 6,
    ).df()
    return df[COLUMNS] if not df.empty else pd.DataFrame(columns=COLUMNS)
