"""How likely is it that an eruption, had it happened, would have been logged?

A single per-geyser constant is wrong, and wrong in a way users notice. Daisy at
1pm in August is one of the most-watched geysers in the park; Daisy at 3am in
February is watched by nobody. Treating those identically makes the renewal
forecast conclude "we must have missed one" the moment a well-observed geyser
runs a few minutes late, which shows up as the prediction jumping a whole cycle
forward while observers are standing there watching it not erupt.

Two signals, combined by taking the stronger:

* **Historical propensity** -- the share of gaps that came through the validity
  filter as single intervals, bucketed by local hour and season. A stretch where
  most gaps are doubles is a stretch where eruptions went unlogged, so this
  reads observation coverage straight off the data.
* **Live basin activity** -- if gazers are logging *anything* in the basin right
  now, they are physically present and the geyser is being watched. This is the
  more decisive signal when it is available, and it needs no model at all.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import threading

import duckdb
import numpy as np

from .config import DB_PATH

# Blend each bucket toward the geyser's overall rate; with few observations in a
# bucket the prior should win. Roughly "this bucket needs ~200 intervals before
# we trust it over the geyser-wide average".
_SMOOTHING = 200.0

# Bounds. Never 1.0: that would give every missed-eruption path zero weight and
# make the forecast incapable of ever concluding an eruption went unlogged.
_MIN_P_OBS, _MAX_P_OBS = 0.30, 0.995

# Live activity: entries logged anywhere in the basin within this window.
_ACTIVITY_WINDOW_MIN = 45
# Entries in that window that mean "gazers are definitely out there".
_ACTIVITY_SATURATION = 8.0

_cache: dict[str, np.ndarray] = {}
_lock = threading.Lock()


def hourly_observation_rate(geyser: str, db_path=DB_PATH) -> np.ndarray:
    """Per-hour logging propensity, shape (2, 24) as [warm, cold] x local hour.

    Bucketed on the *anchor* eruption's local hour, which is what a forecast
    knows. Cached: this is a slowly-varying historical quantity.
    """
    key = f"{geyser}:{db_path}"
    with _lock:
        if key in _cache:
            return _cache[key]

    con = duckdb.connect(str(db_path), read_only=True)
    try:
        df = con.execute(
            """
            SELECT
                CASE WHEN month_local IN (5,6,7,8,9) THEN 0 ELSE 1 END AS season,
                prev_hour_local AS hr,
                count(*) AS n,
                avg(CASE WHEN is_valid THEN 1.0 ELSE 0.0 END) AS rate
            FROM intervals
            WHERE geyser = ? AND year_local >= 2015 AND prev_hour_local IS NOT NULL
            GROUP BY 1, 2
            """,
            [geyser],
        ).df()
        overall = con.execute(
            """
            SELECT avg(CASE WHEN is_valid THEN 1.0 ELSE 0.0 END)
            FROM intervals WHERE geyser = ? AND year_local >= 2015
            """,
            [geyser],
        ).fetchone()[0]
    except duckdb.Error:
        overall, df = None, None
    finally:
        con.close()

    prior = float(overall) if overall is not None else 0.9
    table = np.full((2, 24), prior, dtype=float)
    if df is not None and not df.empty:
        for _, r in df.iterrows():
            n, rate = float(r["n"]), float(r["rate"])
            # shrink toward the geyser-wide rate in proportion to sample size
            table[int(r["season"]), int(r["hr"])] = (n * rate + _SMOOTHING * prior) / (
                n + _SMOOTHING
            )
    table = np.clip(table, _MIN_P_OBS, _MAX_P_OBS)
    with _lock:
        _cache[key] = table
    return table


def basin_activity(t_epoch: int | None = None, db_path=DB_PATH) -> int:
    """Entries logged for ANY geyser in the last `_ACTIVITY_WINDOW_MIN` minutes.

    Presence is the point: someone logging Castle tells us gazers are in the
    basin, which tells us Daisy is being watched.
    """
    t = t_epoch if t_epoch is not None else int(dt.datetime.now(dt.UTC).timestamp())
    lo = t - _ACTIVITY_WINDOW_MIN * 60
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        n = con.execute(
            "SELECT count(*) FROM eruptions WHERE epoch > ? AND epoch <= ?", [lo, t]
        ).fetchone()[0]
        # recent_eruptions only exists once a sync has run
        with contextlib.suppress(duckdb.Error):
            n += con.execute(
                "SELECT count(*) FROM recent_eruptions WHERE epoch > ? AND epoch <= ?", [lo, t]
            ).fetchone()[0]
    except duckdb.Error:
        return 0
    finally:
        con.close()
    return int(n)


def hourly_logging_profile(
    geyser: str,
    t_epoch: int | None = None,
    db_path=DB_PATH,
    use_live_activity: bool = True,
) -> tuple[np.ndarray, dict]:
    """P(an eruption occurring at local hour h would be logged), length 24.

    `hourly_observation_rate` buckets validity by the ANCHOR's hour, which
    answers "was the NEXT eruption logged" -- the right quantity attributed to
    the wrong clock position: the miss happens hours after the anchor, and
    survivorship keeps its night buckets high (the only people logging 2am
    anchors are all-night gazers who also catch the next one; Fountain's 2am
    bucket reads 0.83 while nobody is there). Weighting missed eruptions needs
    the probability at the hour the eruption would have OCCURRED.

    Geysers do not keep clock time, so true eruptions are ~uniform over the 24
    hours and the density of logged entries by hour is proportional to exactly
    that probability. Scaled so the daily mean equals the geyser's overall
    single-interval share, the units match the scalar estimate. Live basin
    activity lifts only the CURRENT hour: gazers present now say nothing about
    who was watching at 3am.
    """
    t = t_epoch if t_epoch is not None else int(dt.datetime.now(dt.UTC).timestamp())
    local = dt.datetime.fromtimestamp(t, dt.UTC).astimezone(dt.timezone(dt.timedelta(hours=-6)))
    season = 0 if local.month in (5, 6, 7, 8, 9) else 1

    key = f"profile:{geyser}:{season}:{db_path}"
    with _lock:
        cached = _cache.get(key)
    if cached is None:
        con = duckdb.connect(str(db_path), read_only=True)
        try:
            df = con.execute(
                """
                SELECT prev_hour_local AS hr, count(*) AS n
                FROM intervals
                WHERE geyser = ? AND year_local >= 2015 AND prev_hour_local IS NOT NULL
                  AND (CASE WHEN month_local IN (5,6,7,8,9) THEN 0 ELSE 1 END) = ?
                GROUP BY 1
                """,
                [geyser, season],
            ).df()
            overall = con.execute(
                """
                SELECT avg(CASE WHEN is_valid THEN 1.0 ELSE 0.0 END)
                FROM intervals WHERE geyser = ? AND year_local >= 2015
                """,
                [geyser],
            ).fetchone()[0]
        except duckdb.Error:
            overall, df = None, None
        finally:
            con.close()

        base = float(overall) if overall is not None else 0.9
        counts = np.ones(24)  # pseudo-count so an empty hour is rare, not impossible
        if df is not None and not df.empty:
            for _, r in df.iterrows():
                counts[int(r["hr"])] += float(r["n"])
        cached = np.clip(base * counts * 24.0 / counts.sum(), 0.05, _MAX_P_OBS)
        with _lock:
            _cache[key] = cached

    profile = cached.copy()
    detail = {
        "season": "warm" if season == 0 else "cold",
        "profile_mean": round(float(profile.mean()), 3),
        "profile_night_min": round(float(profile.min()), 3),
    }
    if use_live_activity:
        n = basin_activity(t, db_path)
        detail["basin_entries_45min"] = n
        if n > 0:
            live = _MIN_P_OBS + (_MAX_P_OBS - _MIN_P_OBS) * min(n / _ACTIVITY_SATURATION, 1.0)
            profile[local.hour] = max(profile[local.hour], live)
            detail["live_hour_lift"] = round(live, 3)
    return profile, detail


def observation_completeness_at(
    geyser: str,
    t_epoch: int | None = None,
    db_path=DB_PATH,
    use_live_activity: bool = True,
) -> tuple[float, dict]:
    """P(an eruption now would be logged), for this geyser at this moment.

    Returns the probability and a small explanation, because this number moves
    predictions a lot and a user asking "why did it say that?" deserves an answer.
    """
    t = t_epoch if t_epoch is not None else int(dt.datetime.now(dt.UTC).timestamp())
    local = dt.datetime.fromtimestamp(t, dt.UTC).astimezone(
        dt.timezone(dt.timedelta(hours=-6))  # America/Denver, DST-agnostic is fine here
    )
    season = 0 if local.month in (5, 6, 7, 8, 9) else 1
    table = hourly_observation_rate(geyser, db_path)
    historical = float(table[season, local.hour])

    detail = {
        "hour_local": local.hour,
        "season": "warm" if season == 0 else "cold",
        "historical": round(historical, 3),
    }

    p = historical
    if use_live_activity:
        n = basin_activity(t, db_path)
        detail["basin_entries_45min"] = n
        if n > 0:
            # Saturating: a handful of recent entries already means "gazers present".
            live = _MIN_P_OBS + (_MAX_P_OBS - _MIN_P_OBS) * min(n / _ACTIVITY_SATURATION, 1.0)
            detail["live"] = round(live, 3)
            p = max(p, live)

    p = float(np.clip(p, _MIN_P_OBS, _MAX_P_OBS))
    detail["p_obs"] = round(p, 3)
    return p, detail
