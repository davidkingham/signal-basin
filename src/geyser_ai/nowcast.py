"""Nowcasting: time until the next eruption, from an arbitrary moment.

The interval harness in `backtest.py` asks "how long is the gap after this
eruption?". That is the right question for comparing interval models, but it
cannot express the thing that actually helps a gazer: *standing here now, with
Beehive's Indicator running and Turban having just gone, when does it erupt?*

Conditioning on a neighbour geyser only pays off inside a narrow window, so it
has to be scored from a decision time rather than from the previous eruption.
Decision times are drawn on a fixed grid independent of when eruptions happen,
which keeps the evaluation unbiased -- scoring only at "5 minutes before an
eruption" would be conditioning on the answer.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

import duckdb
import numpy as np
from scipy import stats

from .config import DB_PATH
from .models import SamplePrediction, renewal_forecast

# Neighbour geysers each target is conditioned on. Every one of these is already
# in the archive, so none of this costs a new data source.
NEIGHBORS: dict[str, list[str]] = {
    "Beehive": ["Beehive's Indicator"],
    "Grand": ["Turban", "Rift", "West Triplet"],
}

# Beehive's Indicator starts, then Beehive follows. Measured over 3,035 pairs
# since 2015 (first Indicator after the previous Beehive, which is what an
# observer actually reacts to): mean 11.9 min, sd 4.8, p5-p95 = 3-19.
# A normal fits far better than lognormal or gamma (KS 0.070 vs 0.178 / 0.143),
# and matches Rhee & Yeung (2023), whose optimum was Indicator + 12 min.
INDICATOR_LEAD_MEAN = 11.9
INDICATOR_LEAD_SD = 4.8
INDICATOR_MAX_WAIT = 40.0  # beyond this the episode is over one way or another
# Measured the other way round -- given an Indicator entry, does Beehive follow?
# 93.7% erupt within 25 min. The ~6% that do not are mostly cycles where the
# Beehive itself never got logged rather than genuine false starts.
INDICATOR_RELIABILITY = 0.94

# Grand rides the Turban cycle. Binning Grand starts by minutes-since-preceding
# Turban leaves a hole: 0.1% land in 5-13 min, against 24% in 0-2 min. Grand
# effectively only starts *with* a Turban; apparent 16-24 min lags are Turbans
# nobody logged.
#
# NEGATIVE RESULT, kept switchable rather than deleted: gating on this lattice
# does not improve a nowcast (paired backtest, 21,672 decision times: CRPS
# +0.3%). The structure is real but unusable, for two compounding reasons.
# Turban's own interval scatters (sd 4.2 min on a 19 min period), so extrapolated
# phase decoheres after roughly one cycle; and Grand's own uncertainty is ~100
# min, five times the Turban period, so the model can never localise Grand to
# within a cycle in the first place -- its predicted median never even drops
# below 40 min. The lattice would only pay off given a sharper base model.
# Rift / West Triplet shifts are likewise off by default (CRPS +1.2%).
TURBAN_PERIOD_DEFAULT = 19.0
TURBAN_PERIOD_SD = 4.2
TURBAN_FORBIDDEN = (4.5, 13.5)   # phase band, minutes after a Turban start
TURBAN_FORBIDDEN_W = 0.05        # residual weight (never a hard zero)
TURBAN_FRESH_MAX = 45.0          # older than this and we have lost the phase

# Rift / West Triplet raise Grand's next interval. Measured with a FIXED early
# window after the anchor so long intervals get no mechanical advantage -- the
# naive "did it happen anywhere in between" version is length-biased and
# overstates this by ~40%.
RIFT_SHIFT_MIN = 32.0
WEST_TRIPLET_SHIFT_MIN = 15.0
PRECURSOR_WINDOW_MIN = 90.0


@dataclass
class NowcastResult:
    pred: SamplePrediction
    regime: str
    detail: dict = field(default_factory=dict)


def load_eruption_epochs(
    geyser: str, db_path=DB_PATH, since_year: int = 2010
) -> np.ndarray:
    """Sorted epochs for one geyser, near-duplicate entries collapsed."""
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        df = con.execute(
            """
            WITH e AS (
                SELECT epoch, lag(epoch) OVER (ORDER BY epoch) prev
                FROM eruptions WHERE geyser = ? AND year(ts_local) >= ?
            )
            SELECT epoch FROM e WHERE prev IS NULL OR epoch - prev > 60 ORDER BY epoch
            """,
            [geyser, since_year],
        ).df()
    finally:
        con.close()
    return df["epoch"].to_numpy(dtype=np.int64)


def load_valid_intervals(geyser: str, db_path=DB_PATH) -> np.ndarray:
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        df = con.execute(
            "SELECT epoch, interval_min FROM intervals WHERE geyser=? AND is_valid ORDER BY epoch",
            [geyser],
        ).df()
    finally:
        con.close()
    return df.to_numpy()


def _fit_base(intervals: np.ndarray, window: int = 100) -> stats.rv_continuous | None:
    x = intervals[-window:]
    x = x[np.isfinite(x) & (x > 0)]
    if len(x) < 12:
        return None
    logs = np.log(x)
    return stats.lognorm(s=max(float(np.std(logs, ddof=1)), 1e-3), scale=np.exp(float(np.mean(logs))))


def _last_before(epochs: np.ndarray, t: int) -> int | None:
    i = int(np.searchsorted(epochs, t, side="right"))
    return int(epochs[i - 1]) if i > 0 else None


def _first_between(epochs: np.ndarray, lo: int, hi: int) -> int | None:
    i = int(np.searchsorted(epochs, lo, side="right"))
    return int(epochs[i]) if i < len(epochs) and epochs[i] <= hi else None


def _any_between(epochs: np.ndarray, lo: int, hi: int) -> bool:
    return _first_between(epochs, lo, hi) is not None


def nowcast(
    geyser: str,
    t: int,
    own: np.ndarray,
    intervals: np.ndarray,
    neigh: dict[str, np.ndarray],
    p_obs: float = 0.9,
    n_sims: int = 4000,
    use_indicator: bool = True,
    use_turban: bool = False,
    use_precursors: bool = False,
    seed: int = 0,
) -> NowcastResult | None:
    """Distribution over minutes from `t` until the next eruption of `geyser`.

    Only data at or before `t` is ever consulted.

    `use_turban` and `use_precursors` default to OFF because a paired backtest
    says they do not work -- see the module notes on Grand. They are kept
    switchable so the negative result stays reproducible rather than being
    quietly deleted.
    """
    last_own = _last_before(own, t)
    if last_own is None:
        return None
    base = _fit_base(intervals)
    if base is None:
        return None
    age_min = (t - last_own) / 60.0

    # ── Beehive: mix the Indicator branch against the base branch ──────────
    #
    # A hard switch to "erupting in ~12 minutes" is wrong once the wait exceeds
    # the plausible lead: at 30 minutes elapsed with nothing, the Indicator has
    # failed (or the eruption went unlogged) and the honest answer reverts to
    # the ordinary distribution. So weight the two branches by Bayes rather
    # than choosing between them --
    #     P(indicator branch) ∝ reliability x P(lead > elapsed)
    # which decays to zero on its own as the wait runs past the lead.
    indicator_w = 0.0
    ind_samples = None
    if use_indicator and geyser == "Beehive":
        ind = neigh.get("Beehive's Indicator")
        if ind is not None and len(ind):
            i_ep = _last_before(ind, t)
            if i_ep is not None and i_ep > last_own:
                elapsed = (t - i_ep) / 60.0
                if elapsed <= INDICATOR_MAX_WAIT:
                    survival = float(
                        stats.norm.sf(elapsed, INDICATOR_LEAD_MEAN, INDICATOR_LEAD_SD)
                    )
                    num = INDICATOR_RELIABILITY * survival
                    indicator_w = num / (num + (1.0 - INDICATOR_RELIABILITY))
                    if indicator_w > 0.01:
                        rng = np.random.default_rng(seed)
                        lead = rng.normal(INDICATOR_LEAD_MEAN, INDICATOR_LEAD_SD, n_sims)
                        # conditioned on not having erupted yet
                        lead = lead[lead > elapsed]
                        if len(lead) >= 20:
                            ind_samples = lead - elapsed
                            ind_elapsed = elapsed
                        else:
                            indicator_w = 0.0

    # ── base renewal forecast, shifted by Grand's precursors ───────────────
    dist = base
    shift = 0.0
    if use_precursors and geyser == "Grand":
        win_hi = last_own + int(PRECURSOR_WINDOW_MIN * 60)
        if _any_between(neigh.get("Rift", np.array([])), last_own, min(win_hi, t)):
            shift += RIFT_SHIFT_MIN
        if _any_between(neigh.get("West Triplet", np.array([])), last_own, min(win_hi, t)):
            shift += WEST_TRIPLET_SHIFT_MIN
        if shift:
            dist = stats.lognorm(s=base.kwds["s"], loc=shift, scale=base.kwds["scale"])

    pred, exp_missed = renewal_forecast(dist, max(age_min, 0.0), p_obs, n_sims=n_sims, seed=seed)
    # renewal samples are minutes after `last_own`; we want minutes after `t`
    remaining = pred.samples - age_min
    w = pred.weights * (remaining > 0)
    if w.sum() <= 0:
        return None
    regime = "base"
    detail: dict = {"expected_missed": round(exp_missed, 2)}
    if shift:
        detail["precursor_shift_min"] = shift
        regime = "precursor_shifted"

    # ── Grand: gate onto the Turban lattice, when Turban is fresh ──────────
    if use_turban and geyser == "Grand":
        tur = neigh.get("Turban")
        if tur is not None and len(tur):
            L = _last_before(tur, t)
            if L is not None and (t - L) / 60.0 <= TURBAN_FRESH_MAX:
                period = _turban_period(tur, t)
                abs_min = (t - L) / 60.0 + remaining          # minutes since that Turban
                cycles = np.maximum(abs_min / period, 0.0)
                # Phase knowledge decays as the lattice is extrapolated forward:
                # each cycle adds sd, and once the spread reaches a quarter
                # period the gate carries no information at all.
                phase_sd = TURBAN_PERIOD_SD * np.sqrt(np.maximum(cycles, 1e-9))
                strength = np.clip(1.0 - phase_sd / (period / 4.0), 0.0, 1.0)
                phase = np.mod(abs_min, period)
                inside = (phase > TURBAN_FORBIDDEN[0]) & (phase < TURBAN_FORBIDDEN[1])
                gate = 1.0 - (1.0 - TURBAN_FORBIDDEN_W) * strength * inside
                if strength.max() > 0.02:
                    w = w * gate
                    regime = "turban_gated"
                    detail["turban_age_min"] = round((t - L) / 60.0, 1)
                    detail["turban_period_min"] = round(period, 1)
    if w.sum() <= 0:
        return None

    # Mix in the Indicator branch, if it earned any weight.
    if ind_samples is not None and indicator_w > 0.01:
        w = w / w.sum() * (1.0 - indicator_w)
        iw = np.full(len(ind_samples), indicator_w / len(ind_samples))
        remaining = np.concatenate([remaining, ind_samples])
        w = np.concatenate([w, iw])
        detail["indicator_elapsed_min"] = round(ind_elapsed, 1)
        detail["indicator_weight"] = round(indicator_w, 3)
        if indicator_w >= 0.5:
            regime = "indicator_active"

    return NowcastResult(SamplePrediction(remaining, w, "nowcast"), regime, detail)


def nowcast_backtest(
    geyser: str,
    years: int = 2,
    step_min: int = 30,
    db_path=DB_PATH,
    conditioned: bool = True,
    extra_trigger_times: bool = True,
    return_rows: bool = False,
) -> dict:
    """Score nowcasts on a fixed grid of decision times, reported per regime.

    The grid is independent of eruption times, so this does not condition on the
    answer. Regimes are reported separately because that is the honest way to
    present a conditional model: Beehive's pre-Indicator error stays terrible
    and the whole value sits in the minutes after the Indicator starts.
    """
    own = load_eruption_epochs(geyser, db_path)
    iv = load_valid_intervals(geyser, db_path)
    if len(own) < 400 or len(iv) < 300:
        return {}
    neigh = {n: load_eruption_epochs(n, db_path) for n in NEIGHBORS.get(geyser, [])}

    now = int(dt.datetime.now(dt.timezone.utc).timestamp())
    start = now - years * 365 * 86400
    grid = list(range(start, now, step_min * 60))

    # Oversample the Indicator window so the conditional regime has a sample
    # worth reporting. Regimes are scored separately, so this cannot bias them.
    if extra_trigger_times and geyser == "Beehive":
        ind = neigh.get("Beehive's Indicator", np.array([]))
        for i_ep in ind[(ind >= start) & (ind < now)]:
            grid.extend(int(i_ep) + m * 60 for m in (1, 4, 8))
    grid = sorted(set(grid))

    iv_epochs, iv_vals = iv[:, 0].astype(np.int64), iv[:, 1].astype(float)
    valid_set = set(iv_epochs.tolist())

    rows: list[dict] = []
    for t in grid:
        j = int(np.searchsorted(own, t, side="right"))
        if j == 0 or j >= len(own):
            continue
        nxt = int(own[j])
        # Only score inside gaps that passed the validity filter, so "time until
        # the next LOGGED eruption" really is "time until the next eruption".
        if nxt not in valid_set:
            continue
        actual = (nxt - t) / 60.0
        if not np.isfinite(actual) or actual <= 0:
            continue
        k = int(np.searchsorted(iv_epochs, t, side="right"))
        if k < 300:
            continue
        nb = {n: e[e <= t] for n, e in neigh.items()}
        # Score BOTH models at the same decision time. Comparing regime buckets
        # against each other is apples-to-oranges -- the conditioned regimes are
        # not a random sample of moments (a Rift-shifted moment is an
        # intrinsically longer wait), so only a paired delta is meaningful.
        on = nowcast(geyser, t, own[:j], iv_vals[:k], nb,
                     use_indicator=True, use_turban=True, use_precursors=True)
        off = nowcast(geyser, t, own[:j], iv_vals[:k], nb,
                      use_indicator=False, use_turban=False, use_precursors=False)
        if on is None or off is None:
            continue
        rec = {"regime": on.regime, "actual": actual}
        ok = True
        for tag, res in (("on", on), ("off", off)):
            p = res.pred
            c = p.crps(actual)
            if not np.isfinite(c):
                ok = False
                break
            lo50, hi50 = p.interval(0.50)
            lo90, hi90 = p.interval(0.90)
            rec |= {f"crps_{tag}": c, f"median_{tag}": p.median(),
                    f"in50_{tag}": lo50 <= actual <= hi50,
                    f"in90_{tag}": lo90 <= actual <= hi90}
        if ok:
            rows.append(rec)

    if not rows:
        return {}
    import pandas as pd

    df = pd.DataFrame(rows)

    def agg(d):
        out = {"n": int(len(d))}
        for tag in ("off", "on"):
            out[tag] = {
                "crps": float(d[f"crps_{tag}"].mean()),
                "mae": float((d[f"median_{tag}"] - d["actual"]).abs().mean()),
                "cover50": float(d[f"in50_{tag}"].mean()),
                "cover90": float(d[f"in90_{tag}"].mean()),
            }
        out["crps_delta_pct"] = 100.0 * (out["on"]["crps"] - out["off"]["crps"]) / out["off"]["crps"]
        out["mae_delta_pct"] = 100.0 * (out["on"]["mae"] - out["off"]["mae"]) / out["off"]["mae"]
        return out

    out = {
        "geyser": geyser,
        "overall": agg(df),
        "by_regime": {str(r): agg(g) for r, g in df.groupby("regime")},
    }
    if return_rows:
        out["rows"] = df
    return out


def _turban_period(tur: np.ndarray, t: int, lookback: int = 40) -> float:
    """Median recent Turban interval, so the lattice tracks the current cycle."""
    i = int(np.searchsorted(tur, t, side="right"))
    recent = tur[max(0, i - lookback) : i]
    if len(recent) < 5:
        return TURBAN_PERIOD_DEFAULT
    d = np.diff(recent) / 60.0
    d = d[(d > 8) & (d < 45)]
    return float(np.median(d)) if len(d) >= 4 else TURBAN_PERIOD_DEFAULT
