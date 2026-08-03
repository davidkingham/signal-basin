"""Regression: a watched geyser running late must not jump a whole cycle.

Reported from the field. Daisy had not erupted -- observers were standing there
watching it -- and shortly after the predicted time passed, the dashboard swapped
to the NEXT eruption's time. Two causes, both fixed here:

1. `p_obs` was a single per-geyser constant (0.838 for Daisy), ignoring that
   Daisy at 1pm in August is one of the most-watched geysers in the park.
2. The rolling fit was extremely sharp (sd ~4 min), so being 15 minutes late was
   a 3.7-sigma event and *any* missed-eruption weight beat it. The validity
   filter censors the right tail, so a model fitted to what survives cannot
   represent a genuinely long interval.

The invariant: while observation is plausible, passing the median moves the
prediction forward smoothly through an explicit overdue state. Where observation
is NOT plausible, the missed-eruption branch must still engage -- that behaviour
is load-bearing and must not be lost while fixing this.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pytest

from geyser_ai.backtest import load_intervals
from geyser_ai.models import fit_tail_mixture, renewal_forecast
from geyser_ai.observation import (
    _MAX_P_OBS,
    basin_activity,
    hourly_observation_rate,
    observation_completeness_at,
)


def base_for(geyser: str):
    d = fit_tail_mixture(load_intervals(geyser)["interval_min"].to_numpy())
    assert d is not None
    return d, float(d.ppf(0.5))


class TestWatchedGeyserStaysInCycle:
    def test_daisy_past_median_does_not_jump_a_cycle(self):
        """The exact reported symptom."""
        base, med = base_for("Daisy")
        pred, _, p_current = renewal_forecast(base, med + 15, _MAX_P_OBS, n_sims=40000)
        assert p_current > 0.5, (
            f"with observation near-certain the current cycle must still dominate "
            f"15 min past the median (got {p_current:.2f})"
        )
        assert pred.median() < med + 0.5 * med, (
            f"predicted time jumped a cycle: median {pred.median():.0f} vs anchor median {med:.0f}"
        )

    def test_prediction_creeps_forward_rather_than_jumping(self):
        base, med = base_for("Daisy")
        ages = [med + e for e in (0, 5, 10, 15, 25, 40)]
        meds = [renewal_forecast(base, a, _MAX_P_OBS, n_sims=30000)[0].median() for a in ages]
        assert all(b >= a - 1.0 for a, b in zip(meds, meds[1:], strict=False)), meds
        # no single step may leap most of a cycle
        steps = np.diff(meds)
        assert steps.max() < 0.5 * med, f"discontinuous jump of {steps.max():.0f} min: {meds}"

    def test_current_cycle_probability_decays_monotonically(self):
        base, med = base_for("Daisy")
        probs = [
            renewal_forecast(base, med + e, _MAX_P_OBS, n_sims=30000)[2]
            for e in (0, 10, 20, 40, 80, 150)
        ]
        assert all(b <= a + 0.02 for a, b in zip(probs, probs[1:], strict=False)), probs
        assert probs[0] > 0.9, "at the median almost all mass is on the current cycle"
        assert probs[-1] < 0.2, "eventually the missed-eruption branch must take over"


class TestMissedBranchStillEngages:
    def test_stale_night_data_still_concludes_missed(self):
        """Old Faithful at 3am with 17-hour-old data: the branch must engage.

        This is the behaviour the fix must not destroy.
        """
        base, med = base_for("Old Faithful")
        p_night, _ = observation_completeness_at(
            "Old Faithful",
            int(dt.datetime(2026, 2, 3, 10, 0, tzinfo=dt.UTC).timestamp()),  # 03:00 MST
            use_live_activity=False,
        )
        pred, missed, p_current = renewal_forecast(base, 17 * 60, p_night, n_sims=40000)
        assert missed > 5, f"17 h on a ~90 min geyser is many cycles (got {missed:.1f})"
        assert p_current < 0.05, "the current-cycle hypothesis must be abandoned here"
        assert pred.median() - 17 * 60 < 2 * med, "next eruption should be soon, not a cycle away"

    def test_poor_observation_engages_sooner_than_good(self):
        base, med = base_for("Daisy")
        _, _, good = renewal_forecast(base, med + 20, _MAX_P_OBS, n_sims=30000)
        _, _, poor = renewal_forecast(base, med + 20, 0.55, n_sims=30000)
        assert good > poor, "better observation must keep more weight on the current cycle"


class TestObservationModel:
    def test_hourly_table_shape_and_bounds(self):
        t = hourly_observation_rate("Daisy")
        assert t.shape == (2, 24)
        assert (t >= 0.3).all() and (t <= 0.995).all()

    def test_never_certain(self):
        """p_obs = 1.0 would make a missed eruption unrepresentable forever."""
        for g in ("Daisy", "Old Faithful", "Beehive"):
            p, _ = observation_completeness_at(g)
            assert p < 1.0

    def test_live_activity_raises_p_obs(self, monkeypatch):
        import geyser_ai.observation as obs

        monkeypatch.setattr(obs, "basin_activity", lambda *a, **k: 0)
        quiet, _ = obs.observation_completeness_at("Daisy")
        monkeypatch.setattr(obs, "basin_activity", lambda *a, **k: 25)
        busy, detail = obs.observation_completeness_at("Daisy")
        assert busy > quiet, "gazers logging in the basin means the geyser is watched"
        assert detail["basin_entries_45min"] == 25

    def test_basin_activity_counts_any_geyser(self):
        """Someone logging Castle tells us Daisy is being watched too."""
        assert basin_activity(int(dt.datetime.now(dt.UTC).timestamp())) >= 0

    def test_detail_explains_the_number(self):
        _, d = observation_completeness_at("Daisy")
        assert {"hour_local", "season", "historical", "p_obs"} <= set(d)


class TestTailMixture:
    def test_widens_without_moving_the_centre(self):
        """The centre must be unchanged; only the tail gets heavier.

        What matters operationally is not any particular quantile but that
        running late has *non-negligible* probability. Under the bare rolling
        fit (sd 0.04 in log space) being 15% late is a 3.5-sigma event at
        p ~ 2e-4, which is what made the renewal forecast prefer "we missed one"
        over "it is running late".
        """
        from scipy import stats

        x = np.random.default_rng(0).lognormal(np.log(100), 0.04, 3000)
        med = float(np.exp(np.mean(np.log(x[-100:]))))
        mix = fit_tail_mixture(x)
        assert mix.ppf(0.5) == pytest.approx(med, rel=0.03)

        late = med * 1.15
        bare = stats.lognorm(s=0.04, scale=med)
        assert bare.sf(late) < 1e-3, "fixture should make 'late' near-impossible bare"
        assert mix.sf(late) > 0.02, f"a late eruption must stay plausible (got {mix.sf(late):.4f})"
        assert mix.ppf(0.99) > med * 1.2, "far tail must reach genuinely long intervals"

    def test_quantiles_monotone(self):
        mix = fit_tail_mixture(np.random.default_rng(1).lognormal(np.log(100), 0.05, 3000))
        qs = [mix.ppf(q) for q in (0.05, 0.25, 0.5, 0.75, 0.95)]
        assert all(b > a for a, b in zip(qs, qs[1:], strict=False)), qs

    def test_cdf_and_ppf_are_consistent(self):
        mix = fit_tail_mixture(np.random.default_rng(2).lognormal(np.log(100), 0.05, 3000))
        for q in (0.1, 0.5, 0.9):
            assert mix.cdf(mix.ppf(q)) == pytest.approx(q, abs=0.02)

    def test_sampling_matches_the_cdf(self):
        mix = fit_tail_mixture(np.random.default_rng(3).lognormal(np.log(100), 0.05, 3000))
        s = mix.rvs(40000, random_state=np.random.default_rng(4))
        assert (s < mix.ppf(0.5)).mean() == pytest.approx(0.5, abs=0.02)
