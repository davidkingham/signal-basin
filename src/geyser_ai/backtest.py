"""Walk-forward backtesting harness.

The rule that matters: at eruption *i*, a model may only see intervals
strictly before *i*. There is no refitting on the future, no global
normalization, no peeking. Every model is scored on exactly the same set of
target eruptions so the comparison is apples-to-apples.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

import duckdb
import numpy as np
import pandas as pd

from .config import DB_PATH, TARGET_GEYSERS
from .models import LogNormalModel, default_models


@dataclass
class ScoreRow:
    geyser: str
    model: str
    n: int
    crps: float
    mae_median: float
    cover50: float
    cover90: float
    mean_median_pred: float
    pit: list[float] = field(default_factory=list)


def load_intervals(geyser: str, db_path=DB_PATH) -> pd.DataFrame:
    """Valid intervals for one geyser, chronologically ordered."""
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        df = con.execute(
            """
            SELECT geyser, ts_utc, ts_local, epoch, interval_min, prev_interval_min,
                   prev_duration_seconds, duration_seconds, hour_local, month_local,
                   year_local, prev_hour_local, prev_doy,
                   prev_webcam, prev_electronic, prev_approximate, prev_in_eruption,
                   prev_minor, prev_major, minor, major,
                   webcam, electronic, approximate, in_eruption, near_start, exact
            FROM intervals
            WHERE geyser = ? AND is_valid
            ORDER BY epoch
            """,
            [geyser],
        ).df()
    finally:
        con.close()
    return df.reset_index(drop=True)


def load_all_intervals(geyser: str, db_path=DB_PATH) -> pd.DataFrame:
    """Every interval including filter-rejected ones, with `is_valid` retained.

    Needed for two things the valid-only view cannot answer: estimating how
    completely a geyser is being observed, and measuring honest coverage
    against the intervals a filtered backtest quietly drops.
    """
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        df = con.execute(
            """
            SELECT geyser, ts_utc, ts_local, epoch, interval_min, prev_interval_min,
                   prev_duration_seconds, duration_seconds, hour_local, month_local,
                   year_local, prev_hour_local, prev_doy,
                   prev_webcam, prev_electronic, prev_approximate, prev_in_eruption,
                   prev_minor, prev_major, minor, major,
                   webcam, electronic, approximate, in_eruption, near_start, exact,
                   med_interval, is_valid
            FROM intervals
            WHERE geyser = ?
            ORDER BY epoch
            """,
            [geyser],
        ).df()
    finally:
        con.close()
    return df.reset_index(drop=True)


def honest_coverage(
    geyser: str, years: int = 3, min_train: int = 300, max_eval: int = 1500, db_path=DB_PATH
) -> dict | None:
    """Coverage measured against ALL intervals, not just filter-passing ones.

    The headline backtest scores each model only on intervals that survived the
    validity filter, which quietly excludes exactly the cases the filter exists
    to remove: stretches where an eruption went unlogged. A gazer on the
    boardwalk does not get that exemption. This re-scores the best simple model
    on every interval in the window -- training still only on valid history --
    so the gap between the two numbers is visible.
    """
    allv = load_all_intervals(geyser, db_path)
    if len(allv) < min_train + 100:
        return None
    cutoff = pd.Timestamp(dt.date.today() - dt.timedelta(days=365 * years))
    ts = pd.to_datetime(allv["ts_utc"]).dt.tz_localize(None)
    start = max(int(np.searchsorted(ts.to_numpy(), cutoff.to_numpy())), min_train)
    idx = list(range(start, len(allv)))
    if not idx:
        return None
    if len(idx) > max_eval:
        idx = sorted(set(np.linspace(start, len(allv) - 1, max_eval).astype(int)))

    model = LogNormalModel(window=100)
    in50 = in90 = n = 0
    n_invalid = 0
    for i in idx:
        row = allv.iloc[i]
        actual = float(row["interval_min"])
        if not np.isfinite(actual) or actual <= 0:
            continue
        hist = allv.iloc[:i]
        hist = hist[hist["is_valid"].astype(bool)]
        if len(hist) < 50:
            continue
        try:
            pred = model.fit_predict(hist, row)
        except Exception:
            pred = None
        if pred is None:
            continue
        lo50, hi50 = pred.interval(0.50)
        lo90, hi90 = pred.interval(0.90)
        in50 += lo50 <= actual <= hi50
        in90 += lo90 <= actual <= hi90
        n += 1
        n_invalid += not bool(row["is_valid"])
    if n < 50:
        return None
    return {
        "geyser": geyser,
        "n": n,
        "pct_filtered_out": 100.0 * n_invalid / n,
        "cover50": in50 / n,
        "cover90": in90 / n,
    }


def backtest_geyser(
    geyser: str,
    years: int = 3,
    min_train: int = 300,
    max_eval: int | None = 2000,
    db_path=DB_PATH,
) -> tuple[list[ScoreRow], pd.DataFrame]:
    """Walk-forward over the last `years` years of eruptions for one geyser.

    Returns per-model scores and the raw per-prediction records (for plotting).
    """
    df = load_intervals(geyser, db_path)
    if len(df) < min_train + 50:
        print(f"  {geyser}: only {len(df)} valid intervals -- skipping")
        return [], pd.DataFrame()

    cutoff = pd.Timestamp(dt.date.today() - dt.timedelta(days=365 * years))
    ts = pd.to_datetime(df["ts_utc"]).dt.tz_localize(None)
    start_idx = int(np.searchsorted(ts.to_numpy(), cutoff.to_numpy()))
    start_idx = max(start_idx, min_train)
    if start_idx >= len(df) - 10:
        start_idx = max(min_train, len(df) - 2000)

    eval_idx = list(range(start_idx, len(df)))
    # Sub-sample evenly if the window is huge (Old Faithful has ~10k/3yr);
    # evaluation cost is dominated by the AFT refits and CRPS integration.
    if max_eval and len(eval_idx) > max_eval:
        eval_idx = list(np.linspace(start_idx, len(df) - 1, max_eval).astype(int))
        eval_idx = sorted(set(eval_idx))

    models = default_models(geyser)
    print(f"  {geyser}: {len(df):,} valid intervals, evaluating {len(eval_idx):,} predictions")

    records: list[dict] = []
    for model in models:
        for i in eval_idx:
            history = df.iloc[:i]
            row = df.iloc[i]
            actual = float(row["interval_min"])
            if not np.isfinite(actual) or actual <= 0:
                continue
            try:
                pred = model.fit_predict(history, row)
            except Exception:
                pred = None
            if pred is None:
                continue
            lo50, hi50 = pred.interval(0.50)
            lo90, hi90 = pred.interval(0.90)
            med = pred.median()
            crps = pred.crps(actual)
            if not np.isfinite(crps):
                continue
            records.append(
                {
                    "geyser": geyser,
                    "model": model.name,
                    "idx": i,
                    "ts": row["ts_utc"],
                    "actual": actual,
                    "median": med,
                    "crps": crps,
                    "in50": lo50 <= actual <= hi50,
                    "in90": lo90 <= actual <= hi90,
                    "pit": float(pred.dist.cdf(actual)),
                    "lo90": lo90,
                    "hi90": hi90,
                }
            )

    recs = pd.DataFrame(records)
    if recs.empty:
        return [], recs

    # Score every model on the common set of eruptions all models predicted,
    # so a model isn't rewarded for silently skipping hard cases.
    counts = recs.groupby("idx")["model"].nunique()
    common = set(counts[counts == recs["model"].nunique()].index)
    scored = recs[recs["idx"].isin(common)]
    if len(scored) < 50:
        scored = recs

    out: list[ScoreRow] = []
    for name, g in scored.groupby("model"):
        out.append(
            ScoreRow(
                geyser=geyser,
                model=str(name),
                n=len(g),
                crps=float(g["crps"].mean()),
                mae_median=float((g["median"] - g["actual"]).abs().mean()),
                cover50=float(g["in50"].mean()),
                cover90=float(g["in90"].mean()),
                mean_median_pred=float(g["median"].mean()),
                pit=g["pit"].tolist(),
            )
        )
    out.sort(key=lambda r: r.crps)
    return out, scored


def run_backtest(
    geysers: list[str] | None = None, years: int = 3, db_path=DB_PATH
) -> tuple[list[ScoreRow], pd.DataFrame]:
    targets = geysers or list(TARGET_GEYSERS)
    all_scores: list[ScoreRow] = []
    all_recs: list[pd.DataFrame] = []
    print(f"Walk-forward backtest over the last {years} years:\n")
    for g in targets:
        scores, recs = backtest_geyser(g, years=years, db_path=db_path)
        all_scores.extend(scores)
        if not recs.empty:
            all_recs.append(recs)
        for s in scores:
            print(
                f"    {s.model:<20} CRPS={s.crps:7.2f}  MAE={s.mae_median:7.2f}  "
                f"50%={s.cover50:5.1%}  90%={s.cover90:5.1%}  n={s.n}"
            )
        print()
    recs = pd.concat(all_recs, ignore_index=True) if all_recs else pd.DataFrame()
    return all_scores, recs
