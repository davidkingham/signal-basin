"""Next-eruption models. Every model returns a full predictive distribution.

A `Prediction` wraps a frozen scipy distribution so the backtest can score any
model the same way: CRPS, median error, and interval coverage all come from the
CDF/PPF, never from a point estimate.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import numpy as np
import pandas as pd
from scipy import stats


@dataclass(frozen=True)
class Prediction:
    """A predictive distribution over the next interval, in minutes."""

    dist: stats.rv_continuous  # actually a frozen distribution
    model: str

    def median(self) -> float:
        return float(self.dist.ppf(0.5))

    def interval(self, level: float) -> tuple[float, float]:
        lo = (1.0 - level) / 2.0
        return float(self.dist.ppf(lo)), float(self.dist.ppf(1.0 - lo))

    def crps(self, actual: float, n_grid: int = 512) -> float:
        """CRPS by numeric integration of (F(x) - 1{x >= y})^2.

        Integrated over a grid spanning the distribution's 0.001-0.999 range
        widened to include the actual value, which keeps the estimate honest
        when a model is badly miscalibrated.
        """
        lo = float(self.dist.ppf(0.0005))
        hi = float(self.dist.ppf(0.9995))
        lo = min(lo, actual) - 1e-9
        hi = max(hi, actual) + 1e-9
        if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
            return float("nan")
        grid = np.linspace(lo, hi, n_grid)
        cdf = self.dist.cdf(grid)
        step = (grid >= actual).astype(float)
        return float(np.trapezoid((cdf - step) ** 2, grid))

    def logpdf(self, actual: float) -> float:
        return float(self.dist.logpdf(actual))


@dataclass(frozen=True)
class SamplePrediction:
    """A predictive distribution held as a weighted Monte Carlo sample.

    Used where no closed form exists -- specifically the renewal/missed-eruption
    forecast, which is a mixture over "how many eruptions went unlogged".
    Exposes the same median/interval surface as `Prediction`.
    """

    samples: np.ndarray
    weights: np.ndarray
    model: str

    def _q(self, q: float) -> float:
        order = np.argsort(self.samples)
        s, w = self.samples[order], self.weights[order]
        cw = np.cumsum(w)
        if cw[-1] <= 0:
            return float("nan")
        return float(np.interp(q, cw / cw[-1], s))

    def median(self) -> float:
        return self._q(0.5)

    def interval(self, level: float) -> tuple[float, float]:
        lo = (1.0 - level) / 2.0
        return self._q(lo), self._q(1.0 - lo)

    def cdf(self, x: float) -> float:
        w = self.weights.sum()
        return float((self.weights * (self.samples <= x)).sum() / w) if w > 0 else float("nan")

    def crps(self, actual: float, n_grid: int = 512) -> float:
        """Same numeric-integration CRPS as `Prediction`, on the weighted ECDF."""
        w = self.weights
        if w.sum() <= 0:
            return float("nan")
        lo = min(float(self._q(0.0005)), actual)
        hi = max(float(self._q(0.9995)), actual)
        if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
            return float("nan")
        grid = np.linspace(lo, hi, n_grid)
        order = np.argsort(self.samples)
        s, ws = self.samples[order], w[order]
        cw = np.cumsum(ws) / ws.sum()
        cdf = np.interp(grid, s, cw, left=0.0, right=1.0)
        step = (grid >= actual).astype(float)
        return float(np.trapezoid((cdf - step) ** 2, grid))


class Model(Protocol):
    name: str

    def fit_predict(self, history: pd.DataFrame, row: pd.Series) -> Prediction | None:
        """Predict the interval preceding `row` using only `history` (strictly earlier)."""
        ...


# Guard against degenerate fits producing absurd distributions.
_MIN_SCALE = 1e-3


class RollingMeanModel:
    """Baseline approximating a GeyserTimes-style "mean +/- window" prediction.

    The public dashboard shows a point prediction with a fixed-ish window; we
    express that as a normal centered on the rolling mean with the rolling SD,
    which is the fairest probabilistic reading of it.
    """

    name = "rolling_normal"

    def __init__(self, window: int = 30) -> None:
        self.window = window

    def fit_predict(self, history: pd.DataFrame, row: pd.Series) -> Prediction | None:
        x = history["interval_min"].to_numpy()[-self.window :]
        if len(x) < 8:
            return None
        mu = float(np.mean(x))
        sd = max(float(np.std(x, ddof=1)), _MIN_SCALE)
        return Prediction(stats.norm(loc=mu, scale=sd), self.name)


class LogNormalModel:
    """Lognormal MLE on a rolling window of recent intervals."""

    name = "lognormal"

    def __init__(self, window: int = 100) -> None:
        self.window = window

    def fit_predict(self, history: pd.DataFrame, row: pd.Series) -> Prediction | None:
        x = history["interval_min"].to_numpy()[-self.window :]
        x = x[x > 0]
        if len(x) < 12:
            return None
        logs = np.log(x)
        mu = float(np.mean(logs))
        sigma = max(float(np.std(logs, ddof=1)), _MIN_SCALE)
        # scipy parameterization: s=sigma (shape), scale=exp(mu)
        return Prediction(stats.lognorm(s=sigma, scale=np.exp(mu)), self.name)


class WeibullModel:
    """Weibull MLE on a rolling window (floc=0, i.e. two-parameter Weibull)."""

    name = "weibull"

    def __init__(self, window: int = 100) -> None:
        self.window = window

    def fit_predict(self, history: pd.DataFrame, row: pd.Series) -> Prediction | None:
        x = history["interval_min"].to_numpy()[-self.window :]
        x = x[x > 0]
        if len(x) < 12:
            return None
        try:
            c, loc, scale = stats.weibull_min.fit(x, floc=0)
        except Exception:
            return None
        if not np.isfinite(c) or not np.isfinite(scale) or scale <= 0 or c <= 0:
            return None
        return Prediction(stats.weibull_min(c=c, loc=0, scale=scale), self.name)


class BestParametricModel:
    """Lognormal vs Weibull, chosen per prediction by held-out log-likelihood.

    The most recent 25% of the training window is held out; both families are
    fit on the earlier 75% and scored on the holdout, then the winner is refit
    on the full window. This is the "pick better by held-out likelihood" step,
    done online rather than once globally, so the choice can change over time.
    """

    name = "best_parametric"

    def __init__(self, window: int = 150) -> None:
        self.window = window
        self._ln = LogNormalModel(window)
        self._wb = WeibullModel(window)
        self.choices: list[str] = []

    def fit_predict(self, history: pd.DataFrame, row: pd.Series) -> Prediction | None:
        x = history["interval_min"].to_numpy()[-self.window :]
        x = x[x > 0]
        if len(x) < 24:
            return None
        cut = int(len(x) * 0.75)
        train, held = x[:cut], x[cut:]
        if len(train) < 12 or len(held) < 4:
            return None

        def ll_lognorm(tr: np.ndarray, ho: np.ndarray) -> float:
            logs = np.log(tr)
            mu, sd = float(np.mean(logs)), max(float(np.std(logs, ddof=1)), _MIN_SCALE)
            return float(np.sum(stats.lognorm.logpdf(ho, s=sd, scale=np.exp(mu))))

        def ll_weibull(tr: np.ndarray, ho: np.ndarray) -> float:
            try:
                c, _, scale = stats.weibull_min.fit(tr, floc=0)
                if not np.isfinite(c) or c <= 0 or scale <= 0:
                    return -np.inf
                return float(np.sum(stats.weibull_min.logpdf(ho, c=c, loc=0, scale=scale)))
            except Exception:
                return -np.inf

        pick_ln = ll_lognorm(train, held) >= ll_weibull(train, held)
        self.choices.append("lognormal" if pick_ln else "weibull")
        chosen = self._ln if pick_ln else self._wb
        pred = chosen.fit_predict(history, row)
        if pred is None:
            return None
        return Prediction(pred.dist, self.name)


class DurationConditionalModel:
    """Old Faithful's classic duration -> interval relationship.

    A short eruption (< ~2.5 min) empties less of the reservoir and is followed
    by a much shorter interval than a long one. We fit a separate lognormal to
    the short-preceding-duration and long-preceding-duration subsets of the
    rolling window and pick by the *previous* eruption's duration. Falls back to
    the pooled lognormal when duration is missing, which is common.
    """

    name = "duration_lognormal"

    def __init__(self, window: int = 400, split_seconds: float = 150.0) -> None:
        self.window = window
        self.split_seconds = split_seconds

    @staticmethod
    def _lognorm_from(x: np.ndarray) -> stats.rv_continuous | None:
        x = x[x > 0]
        if len(x) < 12:
            return None
        logs = np.log(x)
        sd = max(float(np.std(logs, ddof=1)), _MIN_SCALE)
        return stats.lognorm(s=sd, scale=np.exp(float(np.mean(logs))))

    def fit_predict(self, history: pd.DataFrame, row: pd.Series) -> Prediction | None:
        h = history.tail(self.window)
        if len(h) < 24:
            return None
        prev_dur = row.get("prev_duration_seconds")
        pooled = self._lognorm_from(h["interval_min"].to_numpy())
        if prev_dur is None or not np.isfinite(prev_dur) or prev_dur <= 0:
            return Prediction(pooled, self.name) if pooled is not None else None

        d = h["prev_duration_seconds"].to_numpy(dtype=float)
        v = h["interval_min"].to_numpy(dtype=float)
        mask = np.isfinite(d) & (d > 0)
        if mask.sum() < 24:
            return Prediction(pooled, self.name) if pooled is not None else None

        want_short = float(prev_dur) < self.split_seconds
        sub = v[mask & ((d < self.split_seconds) if want_short else (d >= self.split_seconds))]
        dist = self._lognorm_from(sub) or pooled
        return Prediction(dist, self.name) if dist is not None else None


def _last_changepoint(x: np.ndarray, min_seg: int = 40, penalty: float = 8.0) -> int:
    """Index of the most recent level shift in `x`, or 0 if none is credible.

    A single-changepoint scan on the mean, scored by the drop in within-segment
    sum-of-squares and accepted only when the drop beats a BIC-style penalty.
    We only care about the *most recent* shift because everything before it is
    the stale regime we want to discard.
    """
    n = len(x)
    if n < 2 * min_seg:
        return 0
    # Search only the recent portion; an ancient changepoint is not actionable.
    lo, hi = max(min_seg, n - 600), n - min_seg
    if hi <= lo:
        return 0
    total_ss = float(np.sum((x - x.mean()) ** 2))
    if total_ss <= 0:
        return 0
    csum = np.concatenate([[0.0], np.cumsum(x)])
    csq = np.concatenate([[0.0], np.cumsum(x**2)])

    def seg_ss(a: int, b: int) -> float:
        m = b - a
        s = csum[b] - csum[a]
        return float(csq[b] - csq[a] - s * s / m)

    idxs = np.arange(lo, hi)
    ss = np.array([seg_ss(0, k) + seg_ss(k, n) for k in idxs])
    best = int(idxs[int(np.argmin(ss))])
    # Accept only a materially better two-segment fit.
    if total_ss - ss.min() < penalty * total_ss / n * np.log(n):
        return 0
    return best


class AdaptiveLogNormalModel:
    """Lognormal on an adaptively-chosen recent window.

    Motivation: for a very regular geyser (Daisy) a window long enough to fit
    stably also spans level shifts, so the fitted *marginal* is much wider than
    the local *conditional* -- which is exactly the over-coverage we measured
    (a nominal 50% interval covering 87%).

    Two mechanisms, both using only past data:
      * cut the window at the most recent detected changepoint, and
      * among a set of candidate window lengths, pick the one with the best
        held-out log-likelihood on the most recent observations.
    """

    name = "adaptive_lognormal"

    def __init__(
        self,
        candidates: tuple[int, ...] = (25, 50, 100, 200),
        holdout: int = 25,
        use_changepoint: bool = True,
    ) -> None:
        self.candidates = candidates
        self.holdout = holdout
        self.use_changepoint = use_changepoint

    @staticmethod
    def _fit(x: np.ndarray) -> tuple[float, float] | None:
        x = x[x > 0]
        if len(x) < 10:
            return None
        logs = np.log(x)
        return float(np.mean(logs)), max(float(np.std(logs, ddof=1)), _MIN_SCALE)

    def fit_predict(self, history: pd.DataFrame, row: pd.Series) -> Prediction | None:
        x = history["interval_min"].to_numpy(dtype=float)
        x = x[np.isfinite(x) & (x > 0)]
        if len(x) < 60:
            return None

        if self.use_changepoint:
            cp = _last_changepoint(x[-800:])
            if cp > 0:
                seg = x[-800:][cp:]
                if len(seg) >= 40:
                    x = seg

        # choose the window length by held-out likelihood on the most recent block
        best_w, best_ll = None, -np.inf
        if len(x) > self.holdout + 20:
            tr_all, ho = x[: -self.holdout], x[-self.holdout :]
            for w in self.candidates:
                if len(tr_all) < max(w, 12):
                    continue
                fit = self._fit(tr_all[-w:])
                if fit is None:
                    continue
                mu, sd = fit
                ll = float(np.sum(stats.lognorm.logpdf(ho, s=sd, scale=np.exp(mu))))
                if np.isfinite(ll) and ll > best_ll:
                    best_w, best_ll = w, ll
        w = best_w or min(self.candidates[-1], len(x))
        fit = self._fit(x[-w:])
        if fit is None:
            return None
        mu, sd = fit
        return Prediction(stats.lognorm(s=sd, scale=np.exp(mu)), self.name)


class MinorConditionalModel:
    """Condition the next interval on whether the PREVIOUS eruption was a minor.

    Castle erupts in two modes: a full major that discharges the system, and a
    minor that does not. The interval following a minor is physically a
    different quantity, and pooling the two inflates the predicted spread. This
    fits a separate lognormal to the post-minor and post-major subsets of the
    rolling window and selects by the anchor eruption's `prev_minor` flag,
    falling back to the pooled fit when a subset is too thin.
    """

    name = "minor_conditional"

    def __init__(self, window: int = 400) -> None:
        self.window = window

    @staticmethod
    def _lognorm_from(x: np.ndarray, min_n: int = 25) -> stats.rv_continuous | None:
        x = x[np.isfinite(x) & (x > 0)]
        if len(x) < min_n:
            return None
        logs = np.log(x)
        sd = max(float(np.std(logs, ddof=1)), _MIN_SCALE)
        return stats.lognorm(s=sd, scale=np.exp(float(np.mean(logs))))

    def fit_predict(self, history: pd.DataFrame, row: pd.Series) -> Prediction | None:
        h = history.tail(self.window)
        if len(h) < 60:
            return None
        v = h["interval_min"].to_numpy(dtype=float)
        pooled = self._lognorm_from(v)
        if "prev_minor" not in h.columns:
            return Prediction(pooled, self.name) if pooled is not None else None

        was_minor = bool(row.get("prev_minor", False))
        flag = h["prev_minor"].astype(bool).to_numpy()
        sub = v[flag] if was_minor else v[~flag]
        dist = self._lognorm_from(sub) or pooled
        return Prediction(dist, self.name) if dist is not None else None


class EntryTypeConditionalModel:
    """Condition on whether the ANCHOR eruption came from an electronic logger.

    Great Fountain is ~60% logger-recorded, and the two entry types behave very
    differently. For 2015+ valid intervals:

        logger -> logger    median 669 min, sd  95
        human  -> human     median 708 min, sd 276   (mean 813 -- heavy right tail)

    That is NOT a timestamp offset (an offset would push logger->human and
    human->logger apart symmetrically while leaving the like-to-like pairs
    alone, which is not what the data shows). It is a *data quality* difference:
    a logger records every eruption, so consecutive logger entries are true
    consecutive intervals, whereas human-only stretches still contain missed
    eruptions that survived the validity filter.

    So the useful move is a separate scale per anchor type rather than an offset
    correction: predictions anchored to a logger entry deserve to be much
    sharper than predictions anchored to a human report.
    """

    name = "entry_conditional"

    def __init__(self, window: int = 400) -> None:
        self.window = window

    @staticmethod
    def _lognorm_from(x: np.ndarray, min_n: int = 25) -> stats.rv_continuous | None:
        x = x[np.isfinite(x) & (x > 0)]
        if len(x) < min_n:
            return None
        logs = np.log(x)
        sd = max(float(np.std(logs, ddof=1)), _MIN_SCALE)
        return stats.lognorm(s=sd, scale=np.exp(float(np.mean(logs))))

    def fit_predict(self, history: pd.DataFrame, row: pd.Series) -> Prediction | None:
        h = history.tail(self.window)
        if len(h) < 60 or "prev_electronic" not in h.columns:
            return None
        v = h["interval_min"].to_numpy(dtype=float)
        pooled = self._lognorm_from(v)
        is_e = bool(row.get("prev_electronic", False))
        flag = h["prev_electronic"].astype(bool).to_numpy()
        sub = v[flag] if is_e else v[~flag]
        dist = self._lognorm_from(sub) or pooled
        return Prediction(dist, self.name) if dist is not None else None


class WeibullAFTModel:
    """Weibull AFT (lifelines) with covariates, refit periodically.

    Covariates: previous interval, and the clock time / day-of-year *of the
    anchor eruption* (sin/cos), plus the observation-quality flags. The anchor
    detail matters: the target eruption's own hour-of-day is not knowable when
    the prediction is made, and using it leaks the answer.

    Refitting at every eruption would be far too slow across a multi-year
    backtest, so the model is refit every `refit_every` predictions and reused
    in between -- which is also how you'd actually deploy it.
    """

    name = "weibull_aft"

    COVARIATES = [
        "prev_interval_min",
        "hour_sin",
        "hour_cos",
        "doy_sin",
        "doy_cos",
        "prev_webcam",
        "prev_electronic",
        "prev_approximate",
        "prev_in_eruption",
    ]

    def __init__(self, window: int = 1500, refit_every: int = 250) -> None:
        self.window = window
        self.refit_every = refit_every
        self._fitter = None
        self._since_refit = 10**9
        self._cols: list[str] = []

    @staticmethod
    def _design(df: pd.DataFrame) -> pd.DataFrame:
        out = pd.DataFrame(index=df.index)
        out["prev_interval_min"] = pd.to_numeric(
            df["prev_interval_min"], errors="coerce"
        ).astype(float)
        # anchor-eruption clock time, never the target's own
        hour = pd.to_numeric(df["prev_hour_local"], errors="coerce").fillna(12).astype(float)
        out["hour_sin"] = np.sin(2 * np.pi * hour / 24.0)
        out["hour_cos"] = np.cos(2 * np.pi * hour / 24.0)
        doy = pd.to_numeric(df["prev_doy"], errors="coerce").fillna(180).astype(float)
        out["doy_sin"] = np.sin(2 * np.pi * doy / 365.25)
        out["doy_cos"] = np.cos(2 * np.pi * doy / 365.25)
        for flag in ("prev_webcam", "prev_electronic", "prev_approximate", "prev_in_eruption"):
            out[flag] = df[flag].astype(float)
        return out

    def _refit(self, history: pd.DataFrame) -> None:
        from lifelines import WeibullAFTFitter

        h = history.tail(self.window)
        X = self._design(h)
        X["_duration"] = h["interval_min"].astype(float).to_numpy()
        X = X.replace([np.inf, -np.inf], np.nan).dropna()
        X = X[X["_duration"] > 0]
        # drop zero-variance covariates; lifelines will not converge with them
        keep = [c for c in X.columns if c != "_duration" and X[c].std() > 1e-8]
        if len(X) < 200 or not keep:
            self._fitter = None
            return
        X = X[keep + ["_duration"]]
        try:
            f = WeibullAFTFitter(penalizer=0.01)
            f.fit(X, duration_col="_duration")
            self._fitter = f
            self._cols = keep
        except Exception:
            self._fitter = None

    def fit_predict(self, history: pd.DataFrame, row: pd.Series) -> Prediction | None:
        if len(history) < 200:
            return None
        if self._since_refit >= self.refit_every:
            self._refit(history)
            self._since_refit = 0
        self._since_refit += 1
        if self._fitter is None:
            return None

        Xrow = self._design(row.to_frame().T)
        Xrow = Xrow.reindex(columns=self._cols)
        if Xrow.isna().any().any():
            # fall back to the training medians for any missing covariate
            Xrow = Xrow.fillna(self._fitter._norm_mean.reindex(self._cols))
        if Xrow.isna().any().any():
            return None
        try:
            params = self._fitter.params_
            # WeibullAFT: lambda_ = exp(X @ beta), rho_ = exp(intercept)
            lam_lp = float(
                sum(params[("lambda_", c)] * float(Xrow.iloc[0][c]) for c in self._cols)
                + params[("lambda_", "Intercept")]
            )
            rho_lp = float(params[("rho_", "Intercept")])
            scale = float(np.exp(lam_lp))
            shape = float(np.exp(rho_lp))
        except Exception:
            return None
        if not np.isfinite(scale) or not np.isfinite(shape) or scale <= 0 or shape <= 0:
            return None
        return Prediction(stats.weibull_min(c=shape, loc=0, scale=scale), self.name)


# Geysers with a meaningful minor-eruption mode. Elsewhere the flag is unused
# (Daisy, Grand, Riverside, Beehive and Great Fountain are ~0% minor), so the
# conditional model would just be a slower copy of `lognormal`.
MINOR_MODE_GEYSERS = frozenset({"Castle", "Old Faithful"})

# Geysers where electronic loggers supply enough entries for a per-entry-type
# fit to have data on both sides of the split.
LOGGER_HEAVY_GEYSERS = frozenset({"Great Fountain", "Daisy", "Castle", "Grand"})


def renewal_forecast(
    dist: stats.rv_continuous,
    age_min: float,
    p_obs: float,
    n_sims: int = 40_000,
    seed: int = 0,
) -> tuple[SamplePrediction, float]:
    """Distribution of the next eruption given nothing has been LOGGED for `age_min`.

    The naive forecast conditions on survival -- "it has not erupted yet, so it
    is overdue" -- which is only sound when we would certainly have seen it. In
    crowdsourced data that assumption fails constantly: nobody watches Riverside
    at 3am in February, and a silent 14 hours usually means nobody was looking,
    not that the geyser held its breath.

    So treat the geyser as a renewal process from the last logged eruption and
    let each eruption be *logged* independently with probability `p_obs`. A path
    on which k eruptions occurred inside the silent window is consistent with
    what we know only if all k went unlogged, which carries weight
    (1 - p_obs)^k. Weighting the simulated paths that way interpolates between
    the two regimes automatically:

      * fresh data (age << typical interval): almost no path has a missed
        eruption, so this reduces to ordinary survival conditioning;
      * stale data (age >> typical interval): survival paths are astronomically
        unlikely, the weight shifts onto the k-missed hypotheses, and the
        forecast correctly becomes "it probably already went, and the next one
        is roughly one interval from whenever that was".

    Returns the forecast (minutes after the last LOGGED eruption) and the
    weighted expected number of missed eruptions.
    """
    rng = np.random.default_rng(seed)
    # Cap p_obs: at exactly 1.0 every stale-data path gets zero weight and the
    # forecast degenerates. 0.995 keeps survival dominant without collapsing.
    p_obs = float(min(max(p_obs, 0.05), 0.995))

    # Draw generously; paths need enough intervals to cross `age_min`.
    med = float(dist.ppf(0.5))
    max_steps = int(np.clip(age_min / max(med, 1e-6) + 6, 3, 200))
    draws = dist.rvs(size=(n_sims, max_steps), random_state=rng)
    draws = np.clip(np.nan_to_num(draws, nan=med), 1e-6, None)

    cum = np.cumsum(draws, axis=1)
    # k = eruptions that fell inside the silent window (these were missed)
    k = (cum < age_min).sum(axis=1)
    ok = k < max_steps
    if not ok.any():
        k = np.minimum(k, max_steps - 1)
        ok = np.ones_like(k, dtype=bool)
    next_time = cum[np.arange(n_sims), np.minimum(k, max_steps - 1)]

    log_w = k * np.log1p(-p_obs)
    log_w -= log_w.max()
    w = np.exp(log_w) * ok
    if w.sum() <= 0:
        w = np.ones_like(w)
    exp_missed = float(np.sum(w * k) / np.sum(w))
    return SamplePrediction(next_time, w, "renewal"), exp_missed


def observation_completeness(history: pd.DataFrame, lookback: int = 400) -> float:
    """Estimate the probability that an eruption actually gets logged.

    Uses the recent share of consecutive gaps that came through the validity
    filter as a single primary interval. A stretch where most gaps are doubles
    is a stretch where most eruptions went unlogged, so this is a direct read on
    observation coverage rather than a tuned constant.
    """
    if "is_valid" not in history.columns or history.empty:
        return 0.9
    recent = history.tail(lookback)["is_valid"].astype(bool)
    if len(recent) < 20:
        return 0.9
    return float(np.clip(recent.mean(), 0.3, 0.995))


def default_models(geyser: str) -> list[Model]:
    """Model roster. Some models are only added where the physics warrants it."""
    models: list[Model] = [
        RollingMeanModel(window=30),
        LogNormalModel(window=100),
        WeibullModel(window=100),
        BestParametricModel(window=150),
        AdaptiveLogNormalModel(),
        WeibullAFTModel(window=1500, refit_every=250),
    ]
    if geyser == "Old Faithful":
        models.append(DurationConditionalModel(window=400))
    if geyser in MINOR_MODE_GEYSERS:
        models.append(MinorConditionalModel(window=400))
    if geyser in LOGGER_HEAVY_GEYSERS:
        models.append(EntryTypeConditionalModel(window=400))
    return models
