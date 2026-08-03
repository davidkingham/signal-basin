"""Produce a next-eruption prediction from the latest data in the database."""

from __future__ import annotations

import datetime as dt

import duckdb
import numpy as np
import pandas as pd

from .backtest import load_all_intervals, load_intervals
from .config import DB_PATH, TARGET_GEYSERS
from .models import default_models, observation_completeness, renewal_forecast


def _last_eruption(geyser: str, db_path=DB_PATH) -> pd.Series | None:
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        df = con.execute(
            "SELECT * FROM intervals WHERE geyser=? ORDER BY epoch DESC LIMIT 1", [geyser]
        ).df()
    finally:
        con.close()
    return None if df.empty else df.iloc[0]


def predict_geyser(geyser: str, model_name: str | None = None, db_path=DB_PATH) -> dict | None:
    """Predict the next interval for `geyser` from its most recent eruption.

    The whole valid history is the training set and the last recorded eruption is
    the anchor. `model_name` selects a specific model; by default we use
    `best_parametric`, which is the roster's per-prediction lognormal/Weibull
    chooser.
    """
    hist = load_intervals(geyser, db_path)
    if len(hist) < 50:
        return None
    last = _last_eruption(geyser, db_path)
    if last is None:
        return None

    # The "row" being predicted is the *next* eruption: its previous interval and
    # previous duration come from the last observed eruption.
    row = pd.Series(
        {
            "prev_interval_min": float(last["interval_min"]),
            "prev_duration_seconds": last.get("duration_seconds"),
            # anchor covariates come from the last observed eruption
            "prev_hour_local": int(pd.to_datetime(last["ts_local"]).hour),
            "prev_doy": int(pd.to_datetime(last["ts_local"]).dayofyear),
            "prev_webcam": bool(last["webcam"]),
            "prev_electronic": bool(last["electronic"]),
            "prev_approximate": bool(last["approximate"]),
            "prev_in_eruption": bool(last["in_eruption"]),
        }
    )

    models = {m.name: m for m in default_models(geyser)}
    chosen = model_name or "best_parametric"
    model = models.get(chosen)
    if model is None:
        raise ValueError(f"Unknown model {chosen!r}. Available: {sorted(models)}")

    pred = model.fit_predict(hist, row)
    if pred is None:
        return None

    last_ts = pd.to_datetime(last["ts_utc"])
    if last_ts.tzinfo is None:
        last_ts = last_ts.tz_localize("UTC")

    def at(minutes: float) -> str:
        return (last_ts + pd.Timedelta(minutes=float(minutes))).tz_convert(
            "America/Denver"
        ).strftime("%Y-%m-%d %H:%M %Z")

    now = pd.Timestamp.now(tz="UTC")
    age_min = float((now - last_ts).total_seconds()) / 60.0

    # Naive forecast: the raw fitted interval distribution, which implicitly
    # assumes the last logged eruption really was the last eruption.
    naive_med = pred.median()
    naive50 = pred.interval(0.50)
    naive90 = pred.interval(0.90)

    # Renewal forecast: folds in the possibility that eruptions went unlogged
    # during the silent window. Reduces to the naive answer on fresh data.
    p_obs = observation_completeness(load_all_intervals(geyser, db_path))
    rpred, exp_missed = renewal_forecast(pred.dist, max(age_min, 0.0), p_obs)
    med = rpred.median()
    lo50, hi50 = rpred.interval(0.50)
    lo90, hi90 = rpred.interval(0.90)

    # Flag the regime so the caller knows which answer they are looking at.
    stale = exp_missed >= 0.5
    return {
        "geyser": geyser,
        "model": chosen,
        "last_eruption_utc": last_ts.isoformat(),
        "last_eruption_local": last_ts.tz_convert("America/Denver").strftime(
            "%Y-%m-%d %H:%M %Z"
        ),
        "data_age_hours": round(age_min / 60.0, 1),
        "n_training_intervals": int(len(hist)),
        "observation_completeness": round(p_obs, 3),
        "expected_missed_eruptions": round(exp_missed, 2),
        "data_is_stale": bool(stale),
        "median_interval_min": round(med, 1),
        "interval_50_min": [round(lo50, 1), round(hi50, 1)],
        "interval_90_min": [round(lo90, 1), round(hi90, 1)],
        "predicted_time_local": at(med),
        "window_50_local": [at(lo50), at(hi50)],
        "window_90_local": [at(lo90), at(hi90)],
        # kept for comparison: what you'd get ignoring possible missed eruptions
        "naive_median_interval_min": round(naive_med, 1),
        "naive_interval_50_min": [round(naive50[0], 1), round(naive50[1], 1)],
        "naive_interval_90_min": [round(naive90[0], 1), round(naive90[1], 1)],
    }


def predict_all(geysers: list[str] | None = None, model_name: str | None = None,
                db_path=DB_PATH) -> list[dict]:
    out = []
    for g in geysers or list(TARGET_GEYSERS):
        try:
            r = predict_geyser(g, model_name=model_name, db_path=db_path)
        except Exception as exc:
            print(f"  {g}: prediction failed ({exc})")
            continue
        if r:
            out.append(r)
    return out
