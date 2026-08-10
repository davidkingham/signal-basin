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
from .ledger import get_ledger
from .models import Prediction, SamplePrediction
from .predict import predict_geyser
from .scoring import Eruption, LoggedPrediction, match_and_score
from .sources import SOURCE_LABELS, THIRD_PARTY_SOURCES, fetch_predictions
from .sync import sync_recent, sync_status

PARK_TZ = "America/Denver"

# How far back the matcher looks for eruptions each cycle. Comfortably longer
# than a prediction stays open, short enough that the query stays cheap.
SCORING_LOOKBACK_HOURS = 72


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
    record: bool = True,
) -> dict[str, Any]:
    """Renewal-adjusted next-eruption predictions, sorted by soonest.

    `record` also logs this round of predictions to the scoreboard ledger and
    scores anything that has erupted since. Only a full run does so: a
    single-geyser request is a detail view, not a new forecast.
    """
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
    if geysers is None:
        try:
            ctx = _steamboat_context(db_path)
            if ctx:
                ok.append(ctx)
        except Exception:  # the context card must never break the page
            pass
    # Planning-mode cards make no clock-time claim, so "soonest first" does
    # not apply to them -- they sit below every live prediction; context
    # cards (Steamboat) sit below those.
    _MODE_RANK = {"planning": 1, "context": 2}
    ok.sort(
        key=lambda r: (
            _MODE_RANK.get(r.get("display_mode"), 0),
            r.get("minutes_until") if r.get("minutes_until") is not None else float("inf"),
        )
    )
    # Live precursor signals: cheap reads, attached to their cards, never
    # able to break the page. See signals.py for each signal's measured rate.
    try:
        from .signals import live_signals

        sig = live_signals(db_path=db_path)
        for r in ok:
            notes = sig["cards"].get(r.get("geyser"))
            if notes:
                r["live_signals"] = notes
        park_signals = sig["park"]
    except Exception:
        park_signals = []
    payload = {
        "park_signals": park_signals,
        "generated_utc": pd.Timestamp.now(tz="UTC").isoformat(),
        "park_time": pd.Timestamp.now(tz=PARK_TZ).strftime("%Y-%m-%d %H:%M %Z"),
        "window_hours": hours,
        "predictions": ok + bad,
        "sync": _sync_summary(),
    }

    # The scoreboard is a side effect of predicting, on the same cadence and
    # under its own TTL. It must never be able to break the prediction that
    # earned it, so every failure mode ends here.
    if record and geysers is None:
        try:
            update_scoreboard(payload, db_path=db_path)
        except Exception as exc:  # pragma: no cover - defensive
            payload["scoreboard_error"] = f"{type(exc).__name__}: {exc}"

    return payload


def _steamboat_context(db_path=DB_PATH) -> dict[str, Any] | None:
    """The Steamboat card: context, explicitly not a prediction.

    Steamboat is the geyser people ask about most and the one nobody can
    honestly predict -- its current active phase runs weeks-to-months between
    majors with no reliable precursor (and no, seismic detection is not ready:
    see docs/findings/seismic.md for why the single-station detector cannot
    yet be trusted). What CAN be said honestly: when it last went, and what
    the recent intervals have looked like. Saying exactly that, and no more,
    is the point.
    """
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        has_recent = con.execute(
            "SELECT count(*) FROM duckdb_tables() WHERE table_name = 'recent_eruptions'"
        ).fetchone()[0]
        recent_sql = (
            "UNION ALL SELECT epoch FROM recent_eruptions WHERE geyser = 'Steamboat' AND major"
            if has_recent
            else ""
        )
        rows = con.execute(
            f"""
            WITH majors AS (
                SELECT epoch FROM eruptions WHERE geyser = 'Steamboat' AND major
                {recent_sql}
            )
            SELECT DISTINCT epoch FROM majors ORDER BY epoch DESC LIMIT 9
            """
        ).fetchall()
    finally:
        con.close()
    if len(rows) < 2:
        return None
    epochs = [r[0] for r in rows]  # newest first
    now = pd.Timestamp.now(tz="UTC")
    last = pd.Timestamp(epochs[0], unit="s", tz="UTC")
    gaps_days = [(a - b) / 86400.0 for a, b in zip(epochs[:-1], epochs[1:], strict=False)]
    # The seismic watch rides on the same tick, guarded the same way: it can
    # only ever degrade to an honest status, never break the card.
    try:
        from .seismic import watch_tick

        seismic = watch_tick()
    except Exception as exc:  # pragma: no cover - defensive
        seismic = {"status": "no_data", "reason": f"watch error: {type(exc).__name__}"}
    return {
        "geyser": "Steamboat",
        "display_mode": "context",
        "seismic_watch": seismic,
        "last_eruption_utc": last.isoformat(),
        "last_eruption_local": last.tz_convert(PARK_TZ).strftime("%Y-%m-%d %H:%M %Z"),
        "days_since": round((now - last).total_seconds() / 86400.0, 1),
        "recent_intervals_days": {
            "n": len(gaps_days),
            "min": round(min(gaps_days), 1),
            "median": round(float(pd.Series(gaps_days).median()), 1),
            "max": round(max(gaps_days), 1),
        },
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


METHODOLOGY = (
    "GeyserTimes publishes only the predictions that are open right now -- there is no "
    "historical predictions endpoint and none in the nightly archive -- so every number here "
    "was accumulated prospectively, from the moment logging started. "
    "Each source is scored against the window it states itself: the National Park Service and "
    "Geysers.net publish an explicit window with every prediction, and this project's stated "
    "window is its nominal 90% interval. In-window rate is therefore only meaningful beside the "
    "median window width, which is why both are always shown. "
    "When a source re-predicts, only the last prediction issued before the eruption is scored; "
    "the ones it replaced are discarded rather than counted as misses. "
    "Coverage is the share of scored eruptions for which this source had a prediction open, out "
    "of the eruptions any source predicted. "
    "Eruptions that land more than three window widths past a prediction are dropped for every "
    "source alike: in crowd-sourced data that usually means an eruption went unlogged in "
    "between, and charging that to the forecaster would be measuring the observers instead."
)


def _our_key(pred: dict[str, Any], predicted_epoch: int) -> str:
    """Identify one forecast, so recomputing it does not fill the ledger with copies.

    In the base regime a forecast is "the next eruption after this anchor", so
    the anchor is the identity: the answer only becomes a genuinely new forecast
    when a new eruption is logged. The point estimate wobbles by a few seconds
    between runs because the renewal adjustment is a Monte-Carlo simulation, and
    that wobble is not new information.

    A neighbour-conditioned nowcast is different. Beehive's Indicator regime is
    anchored to the present, not to the last eruption, so each recompute really
    does say something new and is keyed on the predicted minute instead. The
    ledger's per-series cap is what bounds that.
    """
    geyser = pred["geyser"]
    regime = str(pred.get("regime") or "base")
    anchor = pred.get("last_eruption_utc")
    if regime == "base" and anchor:
        return f"geyser_ai:{geyser}:{int(pd.Timestamp(anchor).timestamp())}"
    return f"geyser_ai:{geyser}:{regime}:{predicted_epoch // 60}"


def _our_logged_predictions(payload: dict[str, Any]) -> list[LoggedPrediction]:
    """Turn this project's own predictions into ledger records."""
    out: list[LoggedPrediction] = []
    for pred in payload.get("predictions") or []:
        if "error" in pred or not pred.get("predicted_utc"):
            continue
        # A planning-mode card makes no claim about a clock time, so there is
        # nothing to hold it to. Scoring it would score a claim never made.
        if pred.get("display_mode") == "planning":
            continue
        try:
            predicted = int(pd.Timestamp(pred["predicted_utc"]).timestamp())
            w90 = [int(pd.Timestamp(t).timestamp()) for t in pred["window_90_utc"]]
            w50 = [int(pd.Timestamp(t).timestamp()) for t in pred["window_50_utc"]]
        except (KeyError, TypeError, ValueError):
            continue

        geyser = pred["geyser"]
        out.append(
            LoggedPrediction(
                source="geyser_ai",
                geyser=geyser,
                key=_our_key(pred, predicted),
                issued_epoch=int(dt.datetime.now(dt.UTC).timestamp()),
                predicted_epoch=predicted,
                window_open_epoch=w90[0],
                window_close_epoch=w90[1],
                inner_open_epoch=w50[0],
                inner_close_epoch=w50[1],
                detail=str(pred.get("model") or ""),
            )
        )
    return out


def _eruptions_for_scoring(db_path=DB_PATH) -> list[Eruption]:
    recent = get_recent_eruptions(
        hours=SCORING_LOOKBACK_HOURS,
        geysers=list(TARGET_GEYSERS),
        do_sync=False,
        db_path=db_path,
    )
    out = []
    for row in recent["eruptions"]:
        try:
            epoch = int(pd.Timestamp(row["time_utc"]).timestamp())
        except (KeyError, TypeError, ValueError):
            continue
        out.append(Eruption(geyser=row["geyser"], eruption_id=row["eruption_id"], epoch=epoch))
    return out


def update_scoreboard(
    our_predictions: dict[str, Any] | None = None, db_path=DB_PATH
) -> dict[str, Any]:
    """Log what everyone predicted, then score whatever has since erupted.

    Runs on the same cadence as the eruption sync and costs one extra HTTP
    request per cycle -- the single `predictions_latest` call that returns every
    open prediction from every predictor at once.
    """
    led = get_ledger()

    if our_predictions:
        led.add_open(_our_logged_predictions(our_predictions))

    for pred in fetch_predictions():
        led.add_open(
            [
                LoggedPrediction(
                    source=pred.source,
                    geyser=pred.geyser,
                    key=pred.key,
                    issued_epoch=pred.issued_epoch,
                    predicted_epoch=pred.predicted_epoch,
                    window_open_epoch=pred.window_open_epoch,
                    window_close_epoch=pred.window_close_epoch,
                    detail=pred.detail,
                )
            ]
        )

    result = match_and_score(
        list(led.open.values()),
        _eruptions_for_scoring(db_path),
        now_epoch=int(dt.datetime.now(dt.UTC).timestamp()),
        already_scored=led.already_scored(),
    )
    led.apply(result)
    led.flush()
    return {**led.snapshot(), "newly_scored": len(result.scored)}


def _summarise(
    rows: list[Any], source: str, n_eruptions: int, has_open: bool = False
) -> dict[str, Any] | None:
    """Per-source statistics for one geyser.

    `None` means this source does not predict this geyser at all -- neither the
    NPS nor Geysers.net publishes anything for Beehive, for instance. A zeroed
    row means it does predict it and nothing has been scored yet, which is a
    completely different statement and must not be shown as the same thing.
    """
    mine = [r for r in rows if r.source == source]
    if not mine:
        return {"n": 0, "awaiting_first_eruption": True} if has_open else None

    windowed = [r for r in mine if r.in_window is not None]
    widths = [r.window_width_min for r in mine if r.window_width_min is not None]
    inner = [r for r in mine if r.in_inner_window is not None]

    def median(values: list[float]) -> float | None:
        return round(float(np.median(values)), 1) if values else None

    def half_widths(low_attr: str, high_attr: str) -> tuple[float | None, float | None]:
        """Median distance from the point prediction to each edge of the window.

        Reported separately because a predictive distribution is skewed -- a
        geyser is far more often late than impossibly early -- and collapsing
        that to one width would hide which side of the prediction the slack is on.
        """
        lows, highs = [], []
        for r in mine:
            low, high = getattr(r, low_attr), getattr(r, high_attr)
            if low is None or high is None:
                continue
            lows.append((r.predicted_epoch - low) / 60.0)
            highs.append((high - r.predicted_epoch) / 60.0)
        return median(lows), median(highs)

    lo, hi = half_widths("window_open_epoch", "window_close_epoch")
    inner_lo, inner_hi = half_widths("inner_open_epoch", "inner_close_epoch")
    inner_widths = [
        (r.inner_close_epoch - r.inner_open_epoch) / 60.0
        for r in mine
        if r.inner_open_epoch is not None and r.inner_close_epoch is not None
    ]
    n_in_window = sum(1 for r in windowed if r.in_window)
    n_in_50 = sum(1 for r in inner if r.in_inner_window)

    return {
        "n": len(mine),
        "mae_min": round(float(np.mean([r.abs_error_min for r in mine])), 1),
        "median_signed_error_min": median([r.signed_error_min for r in mine]),
        "in_window_rate": (round(n_in_window / len(windowed), 3) if windowed else None),
        # Exact counts, so the dashboard never has to reconstruct "8 of 9" from
        # a rounded rate and get it off by one.
        "n_in_window": n_in_window if windowed else None,
        "n_windowed": len(windowed) or None,
        "median_window_width_min": median(widths),
        "median_window_lo_min": lo,
        "median_window_hi_min": hi,
        "coverage": round(len(mine) / n_eruptions, 3) if n_eruptions else None,
        "in_50_rate": (round(n_in_50 / len(inner), 3) if inner else None),
        "n_in_50": n_in_50 if inner else None,
        "n_inner": len(inner) or None,
        "median_window_50_width_min": median(inner_widths),
        # Both edges, so the inner band can be drawn where it actually sat
        # rather than assumed symmetric about the prediction.
        "median_window_50_lo_min": inner_lo,
        "median_window_50_hi_min": inner_hi,
    }


def get_scoreboard(days: float = 30.0, geyser: str | None = None) -> dict[str, Any]:
    """Rolling accuracy per geyser per source, over the last `days`."""
    led = get_ledger()
    now = dt.datetime.now(dt.UTC)
    cutoff = int((now - dt.timedelta(days=days)).timestamp())
    scored = [s for s in led.scored if s.actual_epoch >= cutoff]
    if geyser:
        scored = [s for s in scored if s.geyser == geyser]

    sources = [{"key": key, **SOURCE_LABELS[key]} for key in ("geyser_ai", *THIRD_PARTY_SOURCES)]

    # Who currently has a prediction open, so "nothing scored yet" can be told
    # apart from "never predicts this geyser".
    open_pairs = {(p.source, p.geyser) for p in led.open.values()}

    rows = []
    for name in TARGET_GEYSERS:
        if geyser and name != geyser:
            continue
        mine = [s for s in scored if s.geyser == name]
        n_eruptions = len({s.eruption_id for s in mine})
        rows.append(
            {
                "geyser": name,
                "n_eruptions": n_eruptions,
                "by_source": {
                    src["key"]: _summarise(
                        mine,
                        src["key"],
                        n_eruptions,
                        has_open=(src["key"], name) in open_pairs,
                    )
                    for src in sources
                },
            }
        )

    # Never claim to cover time before the ledger existed: "last 365 days" on a
    # two-day-old ledger is two days of data, and the page should say so.
    since = dt.datetime.fromtimestamp(cutoff, tz=dt.UTC)
    if led.started_utc:
        try:
            started = dt.datetime.fromisoformat(led.started_utc)
            since = max(since, started)
        except ValueError:
            pass

    return {
        "generated_utc": now.isoformat(),
        "window_days": days,
        "since_utc": since.isoformat(),
        "logging_started_utc": led.started_utc,
        "sources": sources,
        "rows": rows,
        "ledger": led.snapshot(),
        "methodology": METHODOLOGY,
    }


def _park_day(epoch: int) -> str:
    return pd.Timestamp(epoch, unit="s", tz="UTC").tz_convert(PARK_TZ).strftime("%Y-%m-%d")


def get_recent_comparisons(
    limit: int = 20,
    geyser: str | None = None,
    day: str | None = None,
    offset: int = 0,
) -> dict[str, Any]:
    """Scored eruptions with what each source had said, newest first.

    `day` selects a single park-local calendar day (YYYY-MM-DD); `offset` pages
    through whatever the filters leave. `available_days` always describes the
    whole ledger, so a date picker can be built without a second request.
    """
    led = get_ledger()
    scored = [s for s in led.scored if not geyser or s.geyser == geyser]

    by_eruption: dict[int, list[Any]] = {}
    for row in scored:
        by_eruption.setdefault(row.eruption_id, []).append(row)

    def actual_of(rows: list[Any]) -> int:
        return max(r.actual_epoch for r in rows)

    # Days are offered before the day filter is applied, or selecting one would
    # leave the picker with a single option.
    day_counts: dict[str, int] = {}
    for rows in by_eruption.values():
        day_counts[_park_day(actual_of(rows))] = day_counts.get(_park_day(actual_of(rows)), 0) + 1
    available_days = [
        {"date": d, "n_eruptions": n} for d, n in sorted(day_counts.items(), reverse=True)
    ]

    selected = list(by_eruption.items())
    if day:
        selected = [kv for kv in selected if _park_day(actual_of(kv[1])) == day]

    total = len(selected)
    ordered = sorted(selected, key=lambda kv: actual_of(kv[1]), reverse=True)[
        offset : offset + limit
    ]

    def local(epoch: int, fmt: str) -> str:
        return pd.Timestamp(epoch, unit="s", tz="UTC").tz_convert(PARK_TZ).strftime(fmt)

    comparisons = []
    for eruption_id, rows in ordered:
        actual = rows[0].actual_epoch
        sources: dict[str, Any] = {}
        for key in ("geyser_ai", *THIRD_PARTY_SOURCES):
            row = next((r for r in rows if r.source == key), None)
            if row is None:
                sources[key] = None
                continue

            def pair(low: int | None, high: int | None, fmt: str | None = None):
                """Both edges of a window, or None when the source states none."""
                if low is None or high is None:
                    return None
                if fmt:
                    return [local(low, fmt), local(high, fmt)]
                return [
                    dt.datetime.fromtimestamp(low, tz=dt.UTC).isoformat(),
                    dt.datetime.fromtimestamp(high, tz=dt.UTC).isoformat(),
                ]

            sources[key] = {
                "predicted_utc": dt.datetime.fromtimestamp(
                    row.predicted_epoch, tz=dt.UTC
                ).isoformat(),
                "predicted_local": local(row.predicted_epoch, "%H:%M"),
                "signed_error_min": row.signed_error_min,
                "abs_error_min": row.abs_error_min,
                "in_window": row.in_window,
                "in_50": row.in_inner_window,
                "window_local": pair(row.window_open_epoch, row.window_close_epoch, "%H:%M"),
                # Unambiguous across midnight and daylight saving, which the
                # local HH:MM pair on its own is not.
                "window_utc": pair(row.window_open_epoch, row.window_close_epoch),
                "window_50_local": pair(row.inner_open_epoch, row.inner_close_epoch, "%H:%M"),
                "window_50_utc": pair(row.inner_open_epoch, row.inner_close_epoch),
                "window_width_min": row.window_width_min,
                "lead_minutes": row.lead_minutes,
                "detail": row.detail,
            }

        comparisons.append(
            {
                "geyser": rows[0].geyser,
                "eruption_id": eruption_id,
                "actual_utc": dt.datetime.fromtimestamp(actual, tz=dt.UTC).isoformat(),
                "actual_local": local(actual, "%H:%M"),
                "actual_date_local": local(actual, "%Y-%m-%d"),
                "sources": sources,
            }
        )

    return {
        "generated_utc": dt.datetime.now(dt.UTC).isoformat(),
        "count": len(comparisons),
        "total": total,
        "offset": offset,
        "limit": limit,
        "has_more": offset + len(comparisons) < total,
        "day": day,
        "available_days": available_days,
        "logging_started_utc": led.started_utc,
        "comparisons": comparisons,
    }


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
