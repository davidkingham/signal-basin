"""Produce a next-eruption prediction from the latest data in the database."""

from __future__ import annotations

import duckdb
import numpy as np
import pandas as pd

from .backtest import load_intervals
from .config import DB_PATH, TARGET_GEYSERS
from .models import (
    default_model_name,
    default_models,
    fit_tail_mixture,
    renewal_forecast,
)
from .observation import hourly_logging_profile, observation_completeness_at


def _anchor(geyser: str, db_path=DB_PATH) -> pd.Series | None:
    """The two most recent eruptions, from the archive AND the recent-sync table.

    Predictions are only as good as their anchor, so this unions the archive
    snapshot with anything `sync_recent` has pulled from the REST API since.
    The second-newest row supplies the preceding interval, which several models
    use as a covariate.
    """
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        has_recent = con.execute(
            "SELECT count(*) FROM duckdb_tables() WHERE table_name = 'recent_eruptions'"
        ).fetchone()[0]
        recent_sql = (
            """
            UNION ALL
            SELECT eruption_id, geyser, epoch, ts_utc, duration_seconds,
                   webcam, electronic, approximate, in_eruption, minor, major
            FROM recent_eruptions WHERE geyser = ?
            """
            if has_recent
            else ""
        )
        params = [geyser, geyser] if has_recent else [geyser]
        df = con.execute(
            f"""
            WITH combined AS (
                SELECT eruption_id, geyser, epoch, ts_utc, duration_seconds,
                       webcam, electronic, approximate, in_eruption, minor, major
                FROM eruptions WHERE geyser = ?
                {recent_sql}
            ),
            deduped AS (SELECT DISTINCT ON (eruption_id) * FROM combined)
            SELECT * FROM deduped ORDER BY epoch DESC LIMIT 2
            """,
            params,
        ).df()
    finally:
        con.close()
    if df.empty:
        return None
    last = df.iloc[0].copy()
    # Preceding interval, when we have a second-newest eruption to measure from.
    if len(df) > 1:
        prev_gap = (int(last["epoch"]) - int(df.iloc[1]["epoch"])) / 60.0
        last["interval_min"] = prev_gap if prev_gap > 0 else np.nan
    else:
        last["interval_min"] = np.nan
    ts = pd.to_datetime(last["ts_utc"], utc=True)
    last["ts_local"] = ts.tz_convert("America/Denver")
    return last


def predict_geyser(
    geyser: str,
    model_name: str | None = None,
    db_path=DB_PATH,
    include_dist: bool = False,
) -> dict | None:
    """Predict the next interval for `geyser` from its most recent eruption.

    The whole valid history is the training set and the last recorded eruption is
    the anchor. `model_name` selects a specific model; by default each geyser
    gets whichever model the walk-forward backtest actually favours, which is
    `best_parametric` everywhere except the two geysers with a real minor mode.
    See `models.BEST_MODEL_BY_GEYSER`.
    """
    hist = load_intervals(geyser, db_path)
    if len(hist) < 50:
        return None
    last = _anchor(geyser, db_path)
    if last is None:
        return None

    # Fall back to the geyser's own median when the anchor has no measurable
    # preceding interval (single eruption, or a gap we could not measure).
    prev_int = last.get("interval_min")
    if prev_int is None or not np.isfinite(prev_int):
        prev_int = float(hist["interval_min"].tail(200).median())

    # The "row" being predicted is the *next* eruption: its previous interval and
    # previous duration come from the last observed eruption.
    row = pd.Series(
        {
            "prev_interval_min": float(prev_int),
            "prev_duration_seconds": last.get("duration_seconds"),
            "prev_minor": bool(last.get("minor", False)),
            "prev_major": bool(last.get("major", False)),
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
    chosen = model_name or default_model_name(geyser)
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
        return (
            (last_ts + pd.Timedelta(minutes=float(minutes)))
            .tz_convert("America/Denver")
            .strftime("%Y-%m-%d %H:%M %Z")
        )

    now = pd.Timestamp.now(tz="UTC")
    age_min = float((now - last_ts).total_seconds()) / 60.0

    # Naive forecast: the raw fitted interval distribution, which implicitly
    # assumes the last logged eruption really was the last eruption.
    naive_med = pred.median()
    naive50 = pred.interval(0.50)
    naive90 = pred.interval(0.90)

    # Renewal forecast: folds in the possibility that eruptions went unlogged
    # during the silent window. Reduces to the naive answer on fresh data.
    # Observation-aware: a well-watched geyser running late is overdue, not
    # missed. A single per-geyser constant made the forecast jump a whole cycle
    # the moment a closely-watched geyser passed its median.
    #
    # The completeness must be evaluated at the hour each missed eruption would
    # have OCCURRED, not at the present instant. Evaluated "now", a 12-hour
    # overnight gap at Fountain scored p_obs 0.995 -- morning gazers were
    # logging all over the basin -- and the forecast called it overdue instead
    # of concluding the 2am eruption went unlogged.
    p_obs, p_obs_detail = observation_completeness_at(geyser, db_path=db_path)
    p_profile, profile_detail = hourly_logging_profile(geyser, db_path=db_path)
    p_obs_detail["window_profile"] = profile_detail
    anchor_local = last_ts.tz_convert("America/Denver")
    anchor_hour = float(anchor_local.hour) + float(anchor_local.minute) / 60.0
    # Widen the model's fit so being late is surprising, not impossible -- but
    # keep it ANCHORED on that fit. An earlier version substituted the
    # unconditional marginal here, which silently discarded the conditional
    # models' branch selection: Old Faithful served a constant ~93 min against
    # a bimodal 70/102 reality. See docs/findings/live-scoreboard.md.
    intervals = hist["interval_min"].to_numpy()
    marginal = fit_tail_mixture(intervals)
    base_dist = fit_tail_mixture(intervals, narrow=pred.dist) or pred.dist
    # Past the first simulated eruption the branch is unknown again, so chained
    # missed-eruption draws revert to the marginal.
    rpred, exp_missed, p_current = renewal_forecast(
        base_dist,
        max(age_min, 0.0),
        p_profile,
        rest_dist=marginal or base_dist,
        anchor_hour=anchor_hour,
    )
    med = rpred.median()
    lo50, hi50 = rpred.interval(0.50)
    lo90, hi90 = rpred.interval(0.90)

    # Flag the regime so the caller knows which answer they are looking at.
    stale = exp_missed >= 0.5
    result = {
        "geyser": geyser,
        "model": chosen,
        "last_eruption_utc": last_ts.isoformat(),
        "last_eruption_local": last_ts.tz_convert("America/Denver").strftime("%Y-%m-%d %H:%M %Z"),
        "data_age_hours": round(age_min / 60.0, 1),
        "n_training_intervals": int(len(hist)),
        "observation_completeness": round(p_obs, 3),
        "observation_detail": p_obs_detail,
        "expected_missed_eruptions": round(exp_missed, 2),
        "current_cycle_prob": round(p_current, 3),
        # "Overdue" asserts the geyser is still in its current cycle, so it must
        # be the DOMINANT hypothesis, not merely non-negligible. At 0.1 Castle
        # wore an "expected any minute" badge while the model itself said 74%
        # missed-overnight (and the empirical post-major tail past that age is
        # 0.3%). Past the median with p_current <= 0.5, the story is "likely
        # unlogged", which the stale/expected-missed fields already tell.
        "overdue": bool(age_min > float(base_dist.ppf(0.5)) and p_current > 0.5),
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
    if include_dist:
        # Non-serialisable; callers that ask for it must pop it before JSON.
        result["_prediction"] = rpred
    return result


def predict_all(
    geysers: list[str] | None = None, model_name: str | None = None, db_path=DB_PATH
) -> list[dict]:
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
