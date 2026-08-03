"""Shared read layer behind both the HTTP API and the MCP server.

Everything user-facing goes through here so the two transports can never drift
apart. Nothing in this module knows about FastAPI or MCP.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import duckdb
import numpy as np
import pandas as pd

from .config import DB_PATH, TARGET_GEYSERS
from .models import Prediction, SamplePrediction
from .predict import predict_geyser
from .sync import sync_recent, sync_status

PARK_TZ = "America/Denver"


def _density_curve(
    pred: Prediction | SamplePrediction,
    anchor_utc: pd.Timestamp,
    hours: float,
    n_points: int,
) -> list[dict[str, Any]]:
    """Probability density over wall-clock time, ready to plot.

    Returned as (iso timestamp, density) pairs on a uniform grid running from
    now to now + `hours`, normalised so the peak inside the window is 1.0. The
    chart only ever needs relative height, and normalising keeps geysers with
    wildly different interval scales visually comparable.
    """
    now = pd.Timestamp.now(tz="UTC")
    grid = pd.date_range(now, now + pd.Timedelta(hours=hours), periods=n_points)
    # Model time is measured in minutes after the anchor eruption.
    mins = (grid - anchor_utc).total_seconds() / 60.0

    if isinstance(pred, SamplePrediction):
        # Weighted histogram of the renewal samples, lightly smoothed.
        edges = np.linspace(mins[0], mins[-1], n_points + 1)
        dens, _ = np.histogram(pred.samples, bins=edges, weights=pred.weights)
        if dens.sum() > 0:
            k = np.exp(-0.5 * (np.linspace(-2, 2, 9) ** 2))
            dens = np.convolve(dens, k / k.sum(), mode="same")
    else:
        dens = pred.dist.pdf(np.clip(mins, 1e-9, None))

    dens = np.nan_to_num(np.asarray(dens, dtype=float), nan=0.0, posinf=0.0)
    peak = float(dens.max())
    if peak > 0:
        dens = dens / peak
    return [
        {"t": ts.tz_convert(PARK_TZ).isoformat(), "d": round(float(v), 5)}
        for ts, v in zip(grid, dens, strict=True)
    ]


def _nowcast_override(geyser: str, hours: float, n_points: int, db_path) -> dict | None:
    """Neighbour-conditioned nowcast, when a neighbour is actually telling us something.

    Only Beehive currently qualifies: once its Indicator starts, the eruption
    follows in about 12 minutes and the ordinary interval model is hopelessly
    wide. Returns None whenever no neighbour signal is active, so the six other
    geysers and the quiet 93% of Beehive's cycle are untouched.
    """
    from .nowcast import NEIGHBORS, load_eruption_epochs, load_valid_intervals, nowcast

    if geyser not in NEIGHBORS:
        return None
    now = int(dt.datetime.now(dt.UTC).timestamp())
    try:
        own = load_eruption_epochs(geyser, db_path)
        iv = load_valid_intervals(geyser, db_path)
        neigh = {n: load_eruption_epochs(n, db_path) for n in NEIGHBORS[geyser]}
    except Exception:
        return None
    if len(own) < 100 or len(iv) < 100:
        return None
    from .observation import observation_completeness_at

    p_obs, _ = observation_completeness_at(geyser, now, db_path=db_path)
    res = nowcast(geyser, now, own, iv[:, 1].astype(float), neigh, p_obs=p_obs, n_sims=8000)
    if res is None or res.regime == "base":
        return None

    p = res.pred
    t0 = pd.Timestamp.now(tz="UTC")
    med = max(p.median(), 0.0)
    lo50, hi50 = (max(x, 0.0) for x in p.interval(0.50))
    lo90, hi90 = (max(x, 0.0) for x in p.interval(0.90))
    grid = pd.date_range(t0, t0 + pd.Timedelta(hours=hours), periods=n_points)
    mins = (grid - t0).total_seconds() / 60.0
    edges = np.linspace(mins[0], mins[-1], n_points + 1)
    dens, _ = np.histogram(p.samples, bins=edges, weights=p.weights)
    k = np.exp(-0.5 * (np.linspace(-2, 2, 9) ** 2))
    dens = np.convolve(dens, k / k.sum(), mode="same")
    peak = float(dens.max())
    if peak > 0:
        dens = dens / peak

    def at(m: float) -> str:
        return (
            (t0 + pd.Timedelta(minutes=float(m))).tz_convert(PARK_TZ).strftime("%Y-%m-%d %H:%M %Z")
        )

    return {
        "regime": res.regime,
        "regime_detail": res.detail,
        "median_interval_min": round(med, 1),
        "interval_50_min": [round(lo50, 1), round(hi50, 1)],
        "interval_90_min": [round(lo90, 1), round(hi90, 1)],
        "predicted_time_local": at(med),
        "window_50_local": [at(lo50), at(hi50)],
        "window_90_local": [at(lo90), at(hi90)],
        "predicted_utc": (t0 + pd.Timedelta(minutes=med)).isoformat(),
        "window_50_utc": [
            (t0 + pd.Timedelta(minutes=lo50)).isoformat(),
            (t0 + pd.Timedelta(minutes=hi50)).isoformat(),
        ],
        "window_90_utc": [
            (t0 + pd.Timedelta(minutes=lo90)).isoformat(),
            (t0 + pd.Timedelta(minutes=hi90)).isoformat(),
        ],
        "minutes_until": round(med, 1),
        "density": [
            {"t": ts.tz_convert(PARK_TZ).isoformat(), "d": round(float(v), 5)}
            for ts, v in zip(grid, dens, strict=True)
        ],
    }


def get_predictions(
    geysers: list[str] | None = None,
    hours: float = 12.0,
    density_points: int = 96,
    do_sync: bool = True,
    db_path=DB_PATH,
) -> dict[str, Any]:
    """Renewal-adjusted next-eruption predictions, sorted by soonest."""
    if do_sync:
        sync_recent(db_path=db_path)

    targets = geysers or list(TARGET_GEYSERS)
    out: list[dict[str, Any]] = []
    for g in targets:
        try:
            r = predict_geyser(g, db_path=db_path, include_dist=True)
        except Exception as exc:  # one bad geyser must not take down the page
            out.append({"geyser": g, "error": f"{type(exc).__name__}: {exc}"})
            continue
        if not r:
            out.append({"geyser": g, "error": "insufficient data"})
            continue

        anchor = pd.Timestamp(r["last_eruption_utc"])
        pred_obj = r.pop("_prediction", None)
        if pred_obj is not None:
            r["density"] = _density_curve(pred_obj, anchor, hours, density_points)
        r["predicted_utc"] = (anchor + pd.Timedelta(minutes=r["median_interval_min"])).isoformat()
        r["window_50_utc"] = [
            (anchor + pd.Timedelta(minutes=m)).isoformat() for m in r["interval_50_min"]
        ]
        r["window_90_utc"] = [
            (anchor + pd.Timedelta(minutes=m)).isoformat() for m in r["interval_90_min"]
        ]
        now = pd.Timestamp.now(tz="UTC")
        r["minutes_until"] = round(
            (pd.Timestamp(r["predicted_utc"]) - now).total_seconds() / 60.0, 1
        )
        r["regime"] = "base"

        # A live neighbour signal (Beehive's Indicator) beats the interval model
        # outright, so let it take over the timing when it fires.
        try:
            over = _nowcast_override(g, hours, density_points, db_path)
        except Exception:
            over = None
        if over:
            r.update(over)
            r["model"] = f"{r['model']} + {over['regime']}"
        out.append(r)

    ok = [r for r in out if "error" not in r]
    bad = [r for r in out if "error" in r]
    ok.sort(key=lambda r: r["minutes_until"])
    return {
        "generated_utc": pd.Timestamp.now(tz="UTC").isoformat(),
        "park_time": pd.Timestamp.now(tz=PARK_TZ).strftime("%Y-%m-%d %H:%M %Z"),
        "window_hours": hours,
        "predictions": ok + bad,
        "sync": _sync_summary(),
    }


def _sync_summary() -> dict[str, Any]:
    s = sync_status()
    last = s.get("last_success")
    return {
        "last_success_utc": (
            dt.datetime.fromtimestamp(last, tz=dt.UTC).isoformat() if last else None
        ),
        "entries_last_sync": s.get("n_last", 0),
        "entries_total": s.get("n_total", 0),
        "lookback_minutes": s.get("lookback_min", 0),
        "error": s.get("error"),
    }


def get_recent_eruptions(
    hours: int = 24, geysers: list[str] | None = None, do_sync: bool = True, db_path=DB_PATH
) -> dict[str, Any]:
    """Eruptions logged in the last `hours`, archive and recent sync combined."""
    if do_sync:
        sync_recent(db_path=db_path)
    cutoff = int((dt.datetime.now(dt.UTC) - dt.timedelta(hours=hours)).timestamp())

    con = duckdb.connect(str(db_path), read_only=True)
    try:
        has_recent = con.execute(
            "SELECT count(*) FROM duckdb_tables() WHERE table_name = 'recent_eruptions'"
        ).fetchone()[0]
        recent_sql = (
            """
            UNION ALL
            SELECT eruption_id, geyser, epoch, ts_utc, duration_seconds,
                   webcam, electronic, in_eruption, major, minor, observer
            FROM recent_eruptions WHERE epoch >= ?
            """
            if has_recent
            else ""
        )
        params: list[Any] = [cutoff] + ([cutoff] if has_recent else [])
        df = con.execute(
            f"""
            WITH combined AS (
                SELECT eruption_id, geyser, epoch, ts_utc, duration_seconds,
                       webcam, electronic, in_eruption, major, minor, observer
                FROM eruptions WHERE epoch >= ?
                {recent_sql}
            )
            SELECT DISTINCT ON (eruption_id) * FROM combined ORDER BY eruption_id, epoch DESC
            """,
            params,
        ).df()
    finally:
        con.close()

    if not df.empty:
        if geysers:
            df = df[df["geyser"].isin(geysers)]
        df = df.sort_values("epoch", ascending=False)

    items = []
    for _, r in df.iterrows():
        ts = pd.to_datetime(r["ts_utc"], utc=True)
        items.append(
            {
                "eruption_id": int(r["eruption_id"]),
                "geyser": str(r["geyser"]),
                "time_utc": ts.isoformat(),
                "time_local": ts.tz_convert(PARK_TZ).strftime("%H:%M"),
                "date_local": ts.tz_convert(PARK_TZ).strftime("%Y-%m-%d"),
                "minutes_ago": round((pd.Timestamp.now(tz="UTC") - ts).total_seconds() / 60.0),
                "duration_seconds": (
                    float(r["duration_seconds"]) if pd.notna(r["duration_seconds"]) else None
                ),
                "webcam": bool(r["webcam"]),
                "electronic": bool(r["electronic"]),
                "in_eruption": bool(r["in_eruption"]),
                "major": bool(r["major"]),
                "minor": bool(r["minor"]),
                "observer": (str(r["observer"]) if pd.notna(r["observer"]) else None),
            }
        )
    return {"hours": hours, "count": len(items), "eruptions": items}


def get_geyser_stats(geyser: str | None = None, db_path=DB_PATH) -> dict[str, Any]:
    """Interval statistics per geyser, over valid intervals."""
    targets = [geyser] if geyser else list(TARGET_GEYSERS)
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        rows = []
        for g in targets:
            df = con.execute(
                """
                SELECT count(*) n,
                       median(interval_min) med,
                       avg(interval_min) mean,
                       stddev(interval_min) sd,
                       quantile_cont(interval_min, 0.05) p05,
                       quantile_cont(interval_min, 0.95) p95,
                       min(year_local) first_year,
                       max(year_local) last_year
                FROM intervals WHERE geyser = ? AND is_valid
                """,
                [g],
            ).df()
            recent = con.execute(
                """
                SELECT count(*) n, median(interval_min) med, stddev(interval_min) sd
                FROM intervals
                WHERE geyser = ? AND is_valid AND ts_local >= now() - INTERVAL 1 YEAR
                """,
                [g],
            ).df()
            if df.empty or not df.iloc[0]["n"]:
                continue
            r, rc = df.iloc[0], recent.iloc[0]

            def num(v):
                return round(float(v), 1) if pd.notna(v) else None

            rows.append(
                {
                    "geyser": g,
                    "n_valid_intervals": int(r["n"]),
                    "median_interval_min": num(r["med"]),
                    "mean_interval_min": num(r["mean"]),
                    "sd_interval_min": num(r["sd"]),
                    "p05_interval_min": num(r["p05"]),
                    "p95_interval_min": num(r["p95"]),
                    "first_year": int(r["first_year"]),
                    "last_year": int(r["last_year"]),
                    "last_12mo": {
                        "n": int(rc["n"]) if pd.notna(rc["n"]) else 0,
                        "median_interval_min": num(rc["med"]),
                        "sd_interval_min": num(rc["sd"]),
                    },
                }
            )
    finally:
        con.close()
    return {"stats": rows}


def get_health(db_path=DB_PATH) -> dict[str, Any]:
    """Snapshot age, row counts and sync state."""
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        n_raw = con.execute("SELECT count(*) FROM eruptions_raw").fetchone()[0]
        newest = con.execute("SELECT max(epoch) FROM eruptions").fetchone()[0]
        try:
            n_recent, newest_recent = con.execute(
                "SELECT count(*), max(epoch) FROM recent_eruptions"
            ).fetchone()
        except duckdb.Error:
            n_recent, newest_recent = 0, None
    finally:
        con.close()

    latest = max([e for e in (newest, newest_recent) if e is not None], default=None)
    now = dt.datetime.now(dt.UTC)
    age_h = (now.timestamp() - latest) / 3600.0 if latest else None
    return {
        "status": "ok",
        "now_utc": now.isoformat(),
        "park_time": pd.Timestamp.now(tz=PARK_TZ).strftime("%Y-%m-%d %H:%M %Z"),
        "archive_rows": int(n_raw),
        "archive_newest_utc": (
            dt.datetime.fromtimestamp(newest, tz=dt.UTC).isoformat() if newest else None
        ),
        "recent_sync_rows": int(n_recent),
        "newest_eruption_utc": (
            dt.datetime.fromtimestamp(latest, tz=dt.UTC).isoformat() if latest else None
        ),
        "data_age_hours": round(age_h, 2) if age_h is not None else None,
        "sync": _sync_summary(),
        "target_geysers": list(TARGET_GEYSERS),
    }
