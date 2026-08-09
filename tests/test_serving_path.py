"""Regression: the serving path must serve the model it just ran.

The first live scoreboard (2026-08-04..08) caught production predicting a
constant ~93 minutes for every Old Faithful eruption -- MAE 19.2 vs the NPS's
5.6 -- while the backtest-winning `minor_conditional` branch answer sat unused
in the `naive_*` diagnostic fields. The renewal wrapper was anchored on an
unconditionally-fitted marginal (`fit_tail_mixture(hist)`) instead of the
model's own fit, so switching production models changed nothing but the label.
See docs/findings/live-scoreboard.md.

`Plume` in the fixture has the real Old Faithful's structure -- a ~30% minor
mode followed by ~70-minute intervals against ~102 after a full eruption --
and its final eruption is a minor, so a serving path that discards the branch
is ~22 minutes wrong here, far outside any Monte-Carlo wobble.
"""

from __future__ import annotations

import numpy as np
import pytest
from scipy import stats

import geyser_ai.models as models_mod
from geyser_ai.models import fit_tail_mixture, renewal_forecast
from geyser_ai.predict import predict_geyser


class TestServedPredictionKeepsTheBranch:
    def test_post_minor_branch_is_what_gets_served(self, monkeypatch):
        # The roster gates `minor_conditional` to geysers with a real minor
        # mode; the synthetic one qualifies by construction.
        monkeypatch.setattr(
            models_mod, "MINOR_MODE_GEYSERS", models_mod.MINOR_MODE_GEYSERS | {"Plume"}
        )
        r = predict_geyser("Plume", model_name="minor_conditional")
        assert r is not None
        # Fresh anchor: the renewal forecast must reduce to the model's own
        # (naive) answer, not to the marginal's.
        assert r["median_interval_min"] == pytest.approx(r["naive_median_interval_min"], abs=6.0), (
            "served median drifted from the model's answer on fresh data"
        )
        # The anchor is a minor, so both must sit on the short branch. The
        # pooled marginal median is ~92; anything near it means the branch
        # selection was discarded on the way out.
        assert r["median_interval_min"] < 85.0, (
            f"served {r['median_interval_min']:.1f} min: post-minor branch (~70) was not served"
        )

    def test_widening_keeps_the_conditional_centre(self):
        """`fit_tail_mixture(narrow=...)` may widen the fit, never move it."""
        rng = np.random.default_rng(0)
        minor = rng.random(3000) < 0.30
        x = np.where(
            minor,
            rng.lognormal(np.log(70.0), 0.06, 3000),
            rng.lognormal(np.log(102.0), 0.06, 3000),
        )
        cond = stats.lognorm(s=0.06, scale=70.0)
        mix = fit_tail_mixture(x, narrow=cond)
        assert float(mix.ppf(0.5)) == pytest.approx(70.0, rel=0.02)
        # Sanity on the trap itself: the marginal fit of the same data sits
        # near the pooled geometric mean, nowhere near either mode's centre.
        marg = fit_tail_mixture(x)
        assert float(marg.ppf(0.5)) > 85.0


class TestChainedDrawsRevertToTheMarginal:
    def test_stale_data_with_a_short_branch_does_not_collapse(self):
        """Past the first simulated eruption the branch is unknown again.

        A post-minor branch fit says the *next* interval is short; it says
        nothing about the ones after. Chaining every missed-eruption draw from
        the short branch would systematically overcount missed eruptions on
        stale data.
        """
        cond = stats.lognorm(s=0.06, scale=70.0)
        marg = stats.lognorm(s=0.15, scale=95.0)
        pred, missed, _ = renewal_forecast(
            cond, age_min=300.0, p_obs=0.9, n_sims=20000, rest_dist=marg
        )
        assert pred.median() > 300.0, "forecast fell inside the silent window"
        # ~300 minutes of silence over ~95-minute marginal intervals: about
        # three eruptions went unlogged, not the four-plus a 70-minute chain
        # would imply.
        assert 1.5 < missed < 4.0, missed
