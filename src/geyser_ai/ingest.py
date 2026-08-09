"""Download the GeyserTimes complete archive and load it into DuckDB.

Data source: https://geysertimes.org/archive/complete/
The archive is a gzipped TSV of every eruption entry ever recorded, regenerated
daily; only the current day's snapshot is retained server-side. We cache one
snapshot locally and never re-download it (GeyserTimes is a small nonprofit).
"""

from __future__ import annotations

import datetime as dt
import gzip
import json
import shutil
from pathlib import Path

import duckdb
import httpx

from .config import (
    ARCHIVE_BASE,
    DB_PATH,
    GEYSERS_API,
    RAW_DIR,
    TARGET_GEYSERS,
    USER_AGENT,
)

# Interval plausibility bounds, as multiples of each geyser's own median.
#
# These were tightened from an initial 0.35x-3.0x after the interval histograms
# showed unmistakable HARMONICS: Riverside clusters at ~390, ~780 and ~1150
# minutes, Great Fountain at ~686 and ~1400. Those secondary peaks sit at
# exactly 2x and 3x the median -- they are one and two *missed* eruptions, not
# real intervals. A 3x ceiling admits both harmonics and wrecks calibration
# (models trained on them predict distributions far wider than reality).
#
# 1.75x sits safely below the 2x harmonic while still allowing genuinely long
# intervals; 0.5x removes duplicate entries and sub-harmonics. Both bounds are
# deliberately crude and per-geyser -- the raw interval is always retained in
# `interval_min`, and `is_valid` is just a flag, so this is easy to revisit.
#
# The median these multiply is LOCAL and computed in two robust stages, not
# global. See the `base_anchor` / `local_med` CTEs below -- that distinction
# turned out to matter far more than the multipliers themselves.
INTERVAL_MIN_MULT = 0.5
INTERVAL_MAX_MULT = 1.75
# A geyser may have a genuine SECOND interval mode (Lion: ~80 min in-series vs
# ~10 h between series). The second-mode band only engages when the local long
# mode sits at least this factor above the short one. 3.5 is chosen so that
# harmonics can never qualify: a missed eruption puts a phantom mode at exactly
# 2x (or 3x) the true interval, and both stay below the ratio. Lion's real
# ratio is ~7. Without this, the filter deleted ALL 7,410 of Lion's series
# gaps since 2015 -- the mirror image of the Castle post-minor deletion.
SECOND_MODE_RATIO = 3.5
# The p25 anchor assumes true single intervals are at least a quarter of local
# gaps. Backcountry logging breaks that: Lone Star's singles are ~31% of gaps
# in the well-logged recent years and fewer before, so the 25th percentile
# sits ON a harmonic and the median then self-validates it (valid median came
# out 1270 min against a true 186-minute cycle). For these geysers the anchor
# drops to the 10th percentile -- still safely above duplicate-entry noise,
# which the 60-second dedupe pass and the 0.5x floor already handle.
SPARSE_SINGLES_GEYSERS = frozenset({"Lone Star"})
# Rows a regime needs before it gets its own validity baseline. Below this the
# geyser almost certainly has no real minor mode, just a few stray flags.
MIN_REGIME_ROWS = 200


def _archive_url(version: str) -> str:
    return f"{ARCHIVE_BASE}/geysertimes_eruptions_complete_{version}.tsv.gz"


def find_cached_archive() -> Path | None:
    """Return the newest already-downloaded archive, if any."""
    candidates = sorted(RAW_DIR.glob("geysertimes_eruptions_complete_*.tsv.gz"))
    return candidates[-1] if candidates else None


def download_archive(version: str | None = None, force: bool = False) -> Path:
    """Fetch one archive snapshot into data/raw/, or reuse the cached copy.

    Only the current day's file exists on the server, so we try today, then
    yesterday, then tomorrow (matching the CRAN `geysertimes` R package).
    """
    RAW_DIR.mkdir(parents=True, exist_ok=True)

    if not force:
        cached = find_cached_archive()
        if cached is not None:
            print(f"Using cached archive: {cached.name}")
            return cached

    today = dt.date.today()
    versions = (
        [version]
        if version
        else [
            str(today),
            str(today - dt.timedelta(days=1)),
            str(today + dt.timedelta(days=1)),
        ]
    )

    headers = {"User-Agent": USER_AGENT}
    last_error: str = ""
    with httpx.Client(headers=headers, follow_redirects=True, timeout=120.0) as client:
        for v in versions:
            url = _archive_url(v)
            dest = RAW_DIR / f"geysertimes_eruptions_complete_{v}.tsv.gz"
            tmp = dest.with_suffix(".part")
            print(f"Trying {url} ...")
            try:
                with client.stream("GET", url) as resp:
                    if resp.status_code != 200:
                        last_error = f"HTTP {resp.status_code}"
                        continue
                    ctype = resp.headers.get("content-type", "")
                    if "html" in ctype:
                        last_error = f"got HTML (content-type={ctype})"
                        continue
                    total = int(resp.headers.get("content-length", 0))
                    written = 0
                    with tmp.open("wb") as fh:
                        for chunk in resp.iter_bytes(1 << 16):
                            fh.write(chunk)
                            written += len(chunk)
                    print(f"  downloaded {written:,} bytes (expected {total:,})")
                tmp.rename(dest)
                return dest
            except httpx.HTTPError as exc:  # network-level failure
                last_error = str(exc)
                if tmp.exists():
                    tmp.unlink()

    raise RuntimeError(
        f"Could not download the GeyserTimes archive (last error: {last_error}). "
        f"Tried versions: {versions}"
    )


def download_geysers_table(force: bool = False) -> Path:
    """Fetch the geysers reference table from the v5 REST API (one request)."""
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    dest = RAW_DIR / "geysers.json"
    if dest.exists() and not force:
        print(f"Using cached geysers table: {dest.name}")
        return dest
    print(f"Fetching {GEYSERS_API} ...")
    resp = httpx.get(
        GEYSERS_API, headers={"User-Agent": USER_AGENT}, follow_redirects=True, timeout=60.0
    )
    resp.raise_for_status()
    dest.write_text(json.dumps(resp.json()))
    return dest


def _decompress(archive: Path) -> Path:
    """DuckDB reads .gz directly, but a plain TSV is faster to re-scan."""
    plain = RAW_DIR / archive.name.replace(".tsv.gz", ".tsv")
    if plain.exists():
        return plain
    print(f"Decompressing {archive.name} ...")
    with gzip.open(archive, "rb") as src, plain.open("wb") as dst:
        shutil.copyfileobj(src, dst)
    return plain


def build_database(archive: Path, geysers_json: Path | None, db_path: Path = DB_PATH) -> None:
    """Load the raw archive into DuckDB and build the cleaned `eruptions` view."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    tsv = _decompress(archive)

    con = duckdb.connect(str(db_path))
    try:
        print("Loading raw eruptions ...")
        # quote='' because comment fields contain unbalanced quotes; the archive
        # is not RFC4180-quoted, it is plain tab-delimited.
        con.execute("DROP TABLE IF EXISTS eruptions_raw")
        con.execute(
            """
            CREATE TABLE eruptions_raw AS
            SELECT * FROM read_csv(
                ?,
                delim = '\t',
                header = true,
                quote = '',
                escape = '',
                nullstr = ['NULL', ''],
                all_varchar = true,
                ignore_errors = true
            )
            """,
            [str(tsv)],
        )
        n_raw = con.execute("SELECT count(*) FROM eruptions_raw").fetchone()[0]
        print(f"  eruptions_raw: {n_raw:,} rows")

        if geysers_json is not None and geysers_json.exists():
            con.execute("DROP TABLE IF EXISTS geysers_raw")
            con.execute(
                """
                CREATE TABLE geysers_raw AS
                SELECT unnest(geysers) AS g
                FROM read_json(?, maximum_object_size = 100000000)
                """,
                [str(geysers_json)],
            )
            con.execute("CREATE OR REPLACE TABLE geysers AS SELECT g.* FROM geysers_raw")
            n_g = con.execute("SELECT count(*) FROM geysers").fetchone()[0]
            print(f"  geysers: {n_g:,} rows")

        _build_eruptions_view(con)
        _build_intervals(con)
    finally:
        con.close()


def _build_eruptions_view(con: duckdb.DuckDBPyConnection) -> None:
    """Typed, primary-eruptions-only view with flags preserved as booleans.

    Key decisions, all reversible because eruptions_raw is untouched:
      * `eruption_time_epoch` is a Unix epoch (seconds, UTC). It is NEGATIVE for
        historical records -- the archive reaches back to 1872 -- so we must not
        filter on `epoch > 0`.
      * "Primary" = `associated_primaryID` equals the row's own `eruptionID`.
        This column is self-referential, never NULL; rows pointing at a
        *different* ID are secondary duplicate observations of an eruption
        someone else already logged (~180k of 1.53M rows).
      * Yellowstone runs on America/Denver; hour-of-day covariates must use
        local time, so we materialize both.
      * `q` (questionable) rows are excluded outright -- the community flags
        these as probably-wrong.
    """
    print("Building `eruptions` view ...")
    con.execute("DROP VIEW IF EXISTS eruptions")
    con.execute(
        """
        CREATE VIEW eruptions AS
        SELECT
            TRY_CAST(eruptionID AS BIGINT)                      AS eruption_id,
            trim(geyser)                                        AS geyser,
            to_timestamp(TRY_CAST(eruption_time_epoch AS BIGINT)) AS ts_utc,
            timezone('America/Denver',
                to_timestamp(TRY_CAST(eruption_time_epoch AS BIGINT))) AS ts_local,
            TRY_CAST(eruption_time_epoch AS BIGINT)             AS epoch,
            -- observation-quality flags, kept so models can use or exclude them
            COALESCE(TRY_CAST(has_seconds AS INT), 0) = 1       AS has_seconds,
            COALESCE(TRY_CAST(exact AS INT), 0) = 1             AS exact,
            COALESCE(TRY_CAST(ns AS INT), 0) = 1                AS near_start,
            COALESCE(TRY_CAST(ie AS INT), 0) = 1                AS in_eruption,
            COALESCE(TRY_CAST(E AS INT), 0) = 1                 AS electronic,
            COALESCE(TRY_CAST(A AS INT), 0) = 1                 AS approximate,
            COALESCE(TRY_CAST(wc AS INT), 0) = 1                AS webcam,
            COALESCE(TRY_CAST(ini AS INT), 0) = 1               AS initial,
            COALESCE(TRY_CAST(maj AS INT), 0) = 1               AS major,
            COALESCE(TRY_CAST(min AS INT), 0) = 1               AS minor,
            COALESCE(TRY_CAST(q AS INT), 0) = 1                 AS questionable,
            TRY_CAST(duration_seconds AS DOUBLE)                AS duration_seconds,
            duration                                            AS duration_text,
            entrant,
            observer,
            eruption_comment                                    AS comment,
            to_timestamp(TRY_CAST(time_entered AS BIGINT))      AS time_entered,
            to_timestamp(TRY_CAST(time_updated AS BIGINT))      AS time_updated,
            TRY_CAST(associated_primaryID AS BIGINT)            AS primary_id
        FROM eruptions_raw
        WHERE TRY_CAST(eruption_time_epoch AS BIGINT) IS NOT NULL
          AND trim(geyser) IS NOT NULL
          AND trim(geyser) <> ''
          -- primary eruptions only (self-referential ID; see docstring)
          AND (TRY_CAST(associated_primaryID AS BIGINT) IS NULL
               OR TRY_CAST(associated_primaryID AS BIGINT)
                  = TRY_CAST(eruptionID AS BIGINT))
          -- drop community-flagged questionable entries
          AND COALESCE(TRY_CAST(q AS INT), 0) = 0
        """
    )


def _build_intervals(con: duckdb.DuckDBPyConnection) -> None:
    """Consecutive-eruption intervals with a documented validity filter.

    This is crowdsourced data with large observation gaps (nobody watches
    Riverside at 3am in February), so a raw consecutive difference is often
    *several* eruption cycles, not one. We mark an interval `is_valid` only when
    it is plausible for that geyser, using per-geyser robust statistics:

        valid  <=>  INTERVAL_MIN_MULT * median <= interval <= INTERVAL_MAX_MULT * median

    computed against the geyser's own median interval. See the constants at the
    top of this module for why the ceiling is 1.75x rather than the more obvious
    3x: harmonics at exactly 2x and 3x the median are missed eruptions, and a 3x
    ceiling admits them.

    The upper bound catches missed eruptions; the lower bound catches duplicate entries of the same
    eruption logged by two observers seconds apart. Both multipliers are
    deliberately generous -- Grand and Beehive genuinely vary a lot -- and the
    raw interval is retained so the thresholds can be revisited.

    Near-duplicate rows (same geyser, within 60s) are collapsed first: two
    gazers logging the same eruption is the single most common artifact.
    """
    print("Building `intervals` table ...")
    con.execute("DROP TABLE IF EXISTS intervals")
    sparse_singles = ", ".join(f"'{g}'" for g in sorted(SPARSE_SINGLES_GEYSERS))
    con.execute(
        f"""
        CREATE TABLE intervals AS
        WITH cycle_events AS (
            -- Lone Star's minors are PRECURSORS, not cycle events: a minor
            -- precedes the major of the same cycle by ~37 min (IQR 28-44,
            -- n=107 over 3y). Chaining them as eruptions injects ~37 and
            -- ~150-minute phantom intervals into a 186-minute cycle and put
            -- its log-sd at 1.5; the major-only chain is log-sd 0.124. The
            -- same reasoning already keeps Beehive's Indicator out of
            -- Beehive's chain -- there it is a separate geyser name, here it
            -- is a flag on the same name.
            SELECT * FROM eruptions
            WHERE NOT (geyser = 'Lone Star' AND minor)
        ),
        deduped AS (
            -- collapse multiple observers logging the same eruption
            SELECT *,
                   LAG(epoch) OVER (PARTITION BY geyser ORDER BY epoch) AS prev_epoch_all
            FROM cycle_events
        ),
        singles AS (
            SELECT * FROM deduped
            WHERE prev_epoch_all IS NULL OR epoch - prev_epoch_all > 60
        ),
        seq AS (
            SELECT
                eruption_id, geyser, ts_utc, ts_local, epoch,
                exact, approximate, webcam, in_eruption, electronic,
                near_start, major, minor, duration_seconds,
                LAG(epoch)            OVER w AS prev_epoch,
                LAG(ts_utc)           OVER w AS prev_ts_utc,
                LAG(duration_seconds) OVER w AS prev_duration_seconds,
                -- Anchor-eruption entry flags. A flag describes how an eruption
                -- *was recorded*, which is only known after the fact, so models
                -- get the previous entry's flags as a proxy for current
                -- observing conditions (webcam-only at night, in-person in
                -- summer) rather than the target's.
                LAG(webcam)           OVER w AS prev_webcam,
                LAG(electronic)       OVER w AS prev_electronic,
                LAG(approximate)      OVER w AS prev_approximate,
                LAG(in_eruption)      OVER w AS prev_in_eruption,
                -- Castle (and to a lesser extent Old Faithful) has genuine
                -- MINOR eruptions that do not fully discharge the system, so
                -- the interval that FOLLOWS a minor is physically different.
                -- This is a real state variable, not an entry artifact.
                LAG(minor)            OVER w AS prev_minor,
                LAG(major)            OVER w AS prev_major,
                -- Lion erupts in SERIES: an initial, then a few more at ~80
                -- minute spacing, then hours of quiet. Whether the anchor was
                -- the series initial is a real state variable in the same way
                -- prev_minor is, and observers record it at logging time.
                initial,
                LAG(initial)          OVER w AS prev_initial
            FROM singles
            WINDOW w AS (PARTITION BY geyser ORDER BY epoch)
        ),
        raw_int AS (
            SELECT *, (epoch - prev_epoch) / 60.0 AS interval_min
            FROM seq
            WHERE prev_epoch IS NOT NULL
        ),
        -- TWO-STAGE LOCAL BASELINE.
        --
        -- Stage 0 problem: a single global median is wrong because intervals
        -- drift over decades (Daisy ran a 142-minute median in 2019 and 111 in
        -- 2026), so the ceiling gets set by the old era and doubles of the
        -- MODERN interval survive as a phantom second mode.
        --
        -- Stage 1 problem: a plain LOCAL median is not enough either. Where
        -- observation is poor, missed eruptions can be the MAJORITY of recorded
        -- gaps, so the local median tracks the doubled value and then
        -- self-validates the contamination. Great Fountain did exactly this --
        -- its local median ranged up to 1361 against a true interval near 690.
        --
        -- The fix exploits an asymmetry: a missed eruption only ever ADDS time,
        -- never subtracts it. So a LOW quantile is robust to the contamination
        -- in a way the median is not.
        --   base0 = local 25th percentile  -> robust anchor, sits in the true mode
        --   med   = local median computed ONLY over gaps near that anchor
        -- Stage 2's CASE returns NULL outside the near-mode band and median()
        -- skips NULLs, so the refined median never sees the harmonics.
        --
        -- Measured effect on the post-filter p95/median ratio (~1.2-1.5 means a
        -- clean unimodal interval distribution, ~2 means harmonics survived):
        -- Great Fountain 2.01 -> 1.28, Beehive 1.59 -> 1.52, Grand 1.46 -> 1.40,
        -- with Old Faithful, Daisy and Riverside unchanged and already clean.
        --
        -- The windows are centered. That is deliberate: identifying corrupt
        -- records is preprocessing, not prediction, and a centered baseline
        -- tracks a drifting interval far better than a trailing one. It is
        -- smooth and slowly-varying, so it carries no meaningful information
        -- about any individual interval.
        -- STAGE 3: REGIME. Castle breaks the assumption underneath stages 1
        -- and 2, which is that a geyser has ONE interval distribution to be
        -- local about. Castle has two: an eruption that fails to reach the
        -- steam phase is logged as a MINOR, and the interval that follows it
        -- is a genuinely different, much shorter process. Pooled, the baseline
        -- tracks the ~1000-minute post-major mode, the floor lands at 500
        -- minutes, and every short post-minor interval is deleted as if it
        -- were a duplicate entry -- 103 of them under 400 minutes, not one
        -- surviving. The model is then taught that a minor is followed by a
        -- LONGER wait than a major, which is the opposite of the truth.
        --
        -- So the baseline is computed per regime where there is enough of a
        -- regime to compute one. `prev_minor` is false for essentially every
        -- row of a geyser without minors, which makes this a no-op for the
        -- other six, and the row-count guard stops a handful of stray minor
        -- flags from producing a baseline out of nothing.
        regime AS (
            SELECT *, COALESCE(prev_minor, false) AS regime_minor
            FROM raw_int
            WHERE interval_min > 0
        ),
        regime_sized AS (
            SELECT *, count(*) OVER (PARTITION BY geyser, regime_minor) AS regime_n
            FROM regime
        ),
        base_anchor AS (
            SELECT *,
                   quantile_cont(interval_min, 0.25) OVER (
                       PARTITION BY geyser ORDER BY epoch
                       ROWS BETWEEN 400 PRECEDING AND 400 FOLLOWING
                   ) AS base0_pooled,
                   quantile_cont(interval_min, 0.25) OVER (
                       PARTITION BY geyser, regime_minor ORDER BY epoch
                       ROWS BETWEEN 400 PRECEDING AND 400 FOLLOWING
                   ) AS base0_regime,
                   -- Anchor for a possible SECOND (long) mode: the local 75th
                   -- percentile. For a unimodal geyser this sits within ~1.5x
                   -- of the p25 anchor and the ratio guard below keeps the
                   -- second band inert; for a series geyser like Lion it sits
                   -- in the between-series mode.
                   quantile_cont(interval_min, 0.75) OVER (
                       PARTITION BY geyser ORDER BY epoch
                       ROWS BETWEEN 400 PRECEDING AND 400 FOLLOWING
                   ) AS base_hi_pooled,
                   -- Sparse-singles anchor; see SPARSE_SINGLES_GEYSERS.
                   quantile_cont(interval_min, 0.10) OVER (
                       PARTITION BY geyser ORDER BY epoch
                       ROWS BETWEEN 400 PRECEDING AND 400 FOLLOWING
                   ) AS base0_p10
            FROM regime_sized
        ),
        base_pick AS (
            SELECT *,
                   CASE WHEN geyser IN ({sparse_singles})
                        THEN base0_p10
                        WHEN regime_n >= {MIN_REGIME_ROWS}
                        THEN base0_regime ELSE base0_pooled END AS base0
            FROM base_anchor
        ),
        med_both AS (
            SELECT *,
                   median(
                       CASE WHEN interval_min BETWEEN 0.55 * base0 AND 1.4 * base0
                            THEN interval_min END
                   ) OVER (
                       PARTITION BY geyser ORDER BY epoch
                       ROWS BETWEEN 300 PRECEDING AND 300 FOLLOWING
                   ) AS med_pooled,
                   median(
                       CASE WHEN interval_min BETWEEN 0.55 * base0 AND 1.4 * base0
                            THEN interval_min END
                   ) OVER (
                       PARTITION BY geyser, regime_minor ORDER BY epoch
                       ROWS BETWEEN 300 PRECEDING AND 300 FOLLOWING
                   ) AS med_regime,
                   median(
                       CASE WHEN interval_min BETWEEN 0.55 * base_hi_pooled
                                              AND 1.4 * base_hi_pooled
                            THEN interval_min END
                   ) OVER (
                       PARTITION BY geyser ORDER BY epoch
                       ROWS BETWEEN 300 PRECEDING AND 300 FOLLOWING
                   ) AS med_long
            FROM base_pick
        ),
        local_med AS (
            SELECT *,
                   CASE WHEN regime_n >= {MIN_REGIME_ROWS}
                        THEN med_regime ELSE med_pooled END AS med_interval
            FROM med_both
        )
        SELECT
            r.eruption_id, r.geyser, r.ts_utc, r.ts_local, r.epoch,
            r.prev_ts_utc, r.interval_min,
            LAG(r.interval_min) OVER (PARTITION BY r.geyser ORDER BY r.epoch)
                                                        AS prev_interval_min,
            r.prev_duration_seconds,
            r.exact, r.approximate, r.webcam, r.in_eruption, r.electronic,
            r.near_start, r.major, r.minor, r.initial, r.duration_seconds,
            COALESCE(r.prev_webcam, false)      AS prev_webcam,
            COALESCE(r.prev_electronic, false)  AS prev_electronic,
            COALESCE(r.prev_approximate, false) AS prev_approximate,
            COALESCE(r.prev_in_eruption, false) AS prev_in_eruption,
            COALESCE(r.prev_minor, false)       AS prev_minor,
            COALESCE(r.prev_major, false)       AS prev_major,
            COALESCE(r.prev_initial, false)     AS prev_initial,
            hour(r.ts_local)                            AS hour_local,
            month(r.ts_local)                           AS month_local,
            year(r.ts_local)                            AS year_local,
            -- Anchor-time covariates. Predicting an interval means standing at
            -- the PREVIOUS eruption, so only its clock time is knowable; using
            -- the target eruption's own hour-of-day would leak the answer.
            hour(timezone('America/Denver', r.prev_ts_utc))      AS prev_hour_local,
            dayofyear(timezone('America/Denver', r.prev_ts_utc)) AS prev_doy,
            r.med_interval,
            r.med_long,
            ((r.med_interval IS NOT NULL
              AND r.interval_min >= {INTERVAL_MIN_MULT} * r.med_interval
              AND r.interval_min <= {INTERVAL_MAX_MULT} * r.med_interval)
             -- Second-mode band: a gap near the LONG local mode of a genuinely
             -- bimodal geyser is a real interval, not a missed eruption. The
             -- ratio guard keeps this branch inert everywhere 2x/3x harmonics
             -- could masquerade as a mode (see SECOND_MODE_RATIO) -- but a
             -- sparse-singles geyser defeats the guard differently: its p75
             -- sits in the SMEAR of 4x-12x missed-day multiples, which is not
             -- a mode at all. Where singles are the minority, there is no
             -- trustworthy long mode by construction, so the band stays off.
             OR (r.geyser NOT IN ({sparse_singles})
                 AND r.med_interval IS NOT NULL AND r.med_long IS NOT NULL
                 AND r.med_long >= {SECOND_MODE_RATIO} * r.med_interval
                 AND r.interval_min >= {INTERVAL_MIN_MULT} * r.med_long
                 AND r.interval_min <= {INTERVAL_MAX_MULT} * r.med_long)) AS is_valid
        FROM local_med r
        ORDER BY r.geyser, r.epoch
        """
    )
    rows = con.execute("SELECT count(*) FROM intervals").fetchone()[0]
    valid = con.execute("SELECT count(*) FROM intervals WHERE is_valid").fetchone()[0]
    print(f"  intervals: {rows:,} rows ({valid:,} valid, {100 * valid / max(rows, 1):.1f}%)")

    print("\n  Target-geyser summary (valid intervals):")
    q = """
        SELECT geyser,
               count(*)                     AS n_valid,
               round(median(interval_min),1) AS median_min,
               min(year_local)              AS first_year,
               max(year_local)              AS last_year
        FROM intervals
        WHERE is_valid AND geyser IN ({})
        GROUP BY geyser ORDER BY n_valid DESC
    """.format(", ".join(f"'{g}'" for g in TARGET_GEYSERS))
    for row in con.execute(q).fetchall():
        print(f"    {row[0]:<16} n={row[1]:>7,}  median={row[2]:>7} min  {row[3]}-{row[4]}")


def run_ingest(force_download: bool = False, version: str | None = None) -> None:
    archive = download_archive(version=version, force=force_download)
    try:
        geysers = download_geysers_table(force=force_download)
    except Exception as exc:  # the geysers table is a nice-to-have, not required
        print(f"Warning: could not fetch geysers table ({exc}); continuing without it.")
        geysers = None
    build_database(archive, geysers)
    print(f"\nDatabase ready at {DB_PATH}")
