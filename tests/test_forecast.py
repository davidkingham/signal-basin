"""Renewal / missed-eruption forecasting and the Beehive Indicator mixture."""

from __future__ import annotations

import numpy as np
import pytest
from scipy import stats

from geyser_ai.models import observation_completeness, renewal_forecast
from geyser_ai.nowcast import (
    INDICATOR_LEAD_MEAN,
    INDICATOR_LEAD_SD,
    INDICATOR_MAX_WAIT,
    load_eruption_epochs,
    load_valid_intervals,
    nowcast,
)

BASE = stats.lognorm(s=0.15, scale=90.0)  # a ~90 min geyser


class TestRenewal:
    def test_fresh_data_reduces_to_the_base_distribution(self):
        pred, missed, _ = renewal_forecast(BASE, age_min=0.0, p_obs=0.95, n_sims=20000)
        assert missed < 0.01
        assert pred.median() == pytest.approx(BASE.ppf(0.5), rel=0.05)

    def test_no_missed_eruptions_when_barely_overdue(self):
        _, missed, _ = renewal_forecast(BASE, age_min=30.0, p_obs=0.98, n_sims=20000)
        assert missed < 0.05, "30 min into a 90 min cycle nothing should be presumed missed"

    def test_expected_missed_grows_with_age(self):
        ages = [0, 100, 200, 400, 1000]
        missed = [renewal_forecast(BASE, a, 0.9, n_sims=8000)[1] for a in ages]
        assert all(b >= a for a, b in zip(missed, missed[1:], strict=False))
        assert missed[-1] > 8, "1000 min of silence on a 90 min geyser is ~11 cycles"

    def test_stale_data_predicts_soon_not_wildly_overdue(self):
        """The whole point: a long silence means nobody was looking, so the next
        eruption is soon -- not that the geyser is hours overdue."""
        pred, missed, _ = renewal_forecast(BASE, age_min=1000.0, p_obs=0.9, n_sims=20000)
        # measured from the last LOGGED eruption, so "soon" means just past `age`
        assert pred.median() - 1000.0 < 90.0
        assert missed > 5

    def test_lower_observation_completeness_presumes_more_missed(self):
        _, hi, _ = renewal_forecast(BASE, age_min=200.0, p_obs=0.98, n_sims=20000)
        _, lo, _ = renewal_forecast(BASE, age_min=200.0, p_obs=0.50, n_sims=20000)
        assert lo > hi

    def test_p_obs_is_clamped_so_the_forecast_never_degenerates(self):
        # p_obs=1.0 would give every missed-eruption path zero weight
        pred, _, _ = renewal_forecast(BASE, age_min=500.0, p_obs=1.0, n_sims=8000)
        assert np.isfinite(pred.median())

    def test_observation_completeness_reads_the_validity_rate(self):
        import pandas as pd

        good = pd.DataFrame({"is_valid": [True] * 300})
        bad = pd.DataFrame({"is_valid": [True] * 100 + [False] * 200})
        assert observation_completeness(good) > observation_completeness(bad)
        assert 0.3 <= observation_completeness(bad) <= 0.995


class TestIndicatorMixture:
    """Beehive's Indicator. The failure mode this guards against is a hard
    switch that keeps insisting 'any second now' long after the Indicator has
    plainly failed -- which scored 141 min of error before it was fixed."""

    @staticmethod
    def _ctx():
        own = load_eruption_epochs("Beehive")
        iv = load_valid_intervals("Beehive")[:, 1].astype(float)
        ind = load_eruption_epochs("Beehive's Indicator")
        return own, iv, ind

    def _at(self, elapsed_min: float):
        own, iv, ind = self._ctx()
        # pick an Indicator that follows a Beehive, so the regime can trigger
        for i_ep in ind[::-1][:400]:
            j = np.searchsorted(own, i_ep)
            if j == 0:
                continue
            t = int(i_ep) + int(elapsed_min * 60)
            if own[j - 1] < i_ep and (t - own[j - 1]) / 60 < 1500:
                return nowcast(
                    "Beehive",
                    t,
                    own[own <= t],
                    iv,
                    {"Beehive's Indicator": ind[ind <= t]},
                    n_sims=6000,
                )
        return None

    def test_fires_shortly_after_an_indicator(self):
        res = self._at(4)
        assert res is not None and res.regime == "indicator_active"
        assert res.pred.median() < 25, "should be counting down, not hours away"

    def test_weight_decays_as_the_wait_outruns_the_lead(self):
        weights = []
        for elapsed in (2, 8, 16, 24, 34):
            res = self._at(elapsed)
            assert res is not None
            weights.append(res.detail.get("indicator_weight", 0.0))
        assert all(b <= a + 1e-9 for a, b in zip(weights, weights[1:], strict=False)), weights
        assert weights[0] > 0.85, "a fresh Indicator should dominate"
        assert weights[-1] < 0.25, "a long-failed Indicator must hand back to the base model"

    def test_reverts_to_base_regime_once_indicator_is_stale(self):
        res = self._at(INDICATOR_MAX_WAIT + 20)
        assert res is not None
        assert res.regime == "base", "past the max wait the Indicator must be ignored"

    def test_no_indicator_leaves_the_forecast_untouched(self):
        """Conditioning must add information only when there is information."""
        own, iv, ind = self._ctx()
        t = int(own[-1]) + 200 * 60  # long after the last eruption, no Indicator since
        on = nowcast(
            "Beehive", t, own, iv, {"Beehive's Indicator": ind[ind <= own[-1]]}, n_sims=6000
        )
        off = nowcast("Beehive", t, own, iv, {}, n_sims=6000, use_indicator=False)
        assert on is not None and off is not None
        assert on.regime == "base"
        assert on.pred.median() == pytest.approx(off.pred.median(), rel=0.05)

    def test_other_geysers_are_unaffected(self):
        own = load_eruption_epochs("Daisy")
        iv = load_valid_intervals("Daisy")[:, 1].astype(float)
        t = int(own[-1]) + 30 * 60
        res = nowcast("Daisy", t, own, iv, {}, n_sims=4000)
        assert res is not None and res.regime == "base"

    def test_lead_constants_match_the_measured_distribution(self):
        assert pytest.approx(11.9, abs=0.5) == INDICATOR_LEAD_MEAN
        assert pytest.approx(4.8, abs=0.5) == INDICATOR_LEAD_SD
