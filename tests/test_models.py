"""Distributional sanity every model must satisfy, whatever its internals."""

from __future__ import annotations

import numpy as np
import pytest

from geyser_ai.backtest import load_intervals
from geyser_ai.models import Prediction, SamplePrediction, default_models

GEYSERS = ["Old Faithful", "Grand", "Daisy", "Beehive", "Castle"]


def _predictions(geyser: str):
    df = load_intervals(geyser)
    assert len(df) > 400, f"fixture too small for {geyser}"
    i = len(df) - 1
    out = []
    for m in default_models(geyser):
        p = m.fit_predict(df.iloc[:i], df.iloc[i])
        if p is not None:
            out.append((m.name, p))
    return out


@pytest.mark.parametrize("geyser", GEYSERS)
def test_every_model_produces_a_usable_distribution(geyser):
    preds = _predictions(geyser)
    assert len(preds) >= 5, f"{geyser}: expected most of the roster to fit"
    for name, p in preds:
        assert np.isfinite(p.median()), f"{name}: non-finite median"
        assert p.median() > 0, f"{name}: non-positive median interval"


@pytest.mark.parametrize("geyser", GEYSERS)
def test_quantiles_monotone_and_windows_nested(geyser):
    for name, p in _predictions(geyser):
        lo50, hi50 = p.interval(0.50)
        lo90, hi90 = p.interval(0.90)
        med = p.median()
        assert lo90 <= lo50 <= med <= hi50 <= hi90, (
            f"{name}: 50% window must sit inside the 90% window, around the median"
        )


@pytest.mark.parametrize("geyser", GEYSERS)
def test_crps_is_finite_and_minimised_near_the_truth(geyser):
    for name, p in _predictions(geyser):
        med = p.median()
        near = p.crps(med)
        far = p.crps(med * 6)
        assert np.isfinite(near), f"{name}: CRPS not finite"
        assert near >= 0
        assert near < far, f"{name}: CRPS must penalise a badly wrong outcome"


def test_prediction_density_integrates_to_one():
    from scipy import stats

    p = Prediction(stats.lognorm(s=0.2, scale=100.0), "t")
    xs = np.linspace(1e-6, 600, 40000)
    assert np.trapezoid(p.dist.pdf(xs), xs) == pytest.approx(1.0, abs=1e-3)


class TestSamplePrediction:
    def _p(self):
        rng = np.random.default_rng(0)
        s = rng.lognormal(np.log(100), 0.2, 20000)
        return SamplePrediction(s, np.ones_like(s), "t")

    def test_quantiles_ordered(self):
        p = self._p()
        assert p.interval(0.9)[0] < p.interval(0.5)[0] < p.median()
        assert p.median() < p.interval(0.5)[1] < p.interval(0.9)[1]

    def test_cdf_bounds_and_monotone(self):
        p = self._p()
        xs = [1, 50, 100, 200, 1000]
        c = [p.cdf(x) for x in xs]
        assert all(0.0 <= v <= 1.0 for v in c)
        assert all(b >= a for a, b in zip(c, c[1:], strict=False))

    def test_weighting_shifts_the_median(self):
        rng = np.random.default_rng(1)
        s = rng.lognormal(np.log(100), 0.3, 20000)
        flat = SamplePrediction(s, np.ones_like(s), "t")
        # up-weight the short half
        w = np.where(s < np.median(s), 3.0, 1.0)
        skewed = SamplePrediction(s, w, "t")
        assert skewed.median() < flat.median()

    def test_crps_matches_the_analytic_version_closely(self):
        from scipy import stats

        rng = np.random.default_rng(2)
        dist = stats.lognorm(s=0.2, scale=100.0)
        s = dist.rvs(200000, random_state=rng)
        emp = SamplePrediction(s, np.ones_like(s), "t")
        ana = Prediction(dist, "t")
        for actual in (80.0, 100.0, 140.0):
            assert emp.crps(actual) == pytest.approx(ana.crps(actual), rel=0.05)
