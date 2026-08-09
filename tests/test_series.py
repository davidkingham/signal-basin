"""Lion: series structure through the whole stack.

Lion's intervals are bimodal -- ~83 minutes while a series runs, ~10 hours
between series -- and the first attempt to ingest it showed the validity
filter deleting ALL 7,410 series gaps since 2015 (the mirror image of the
Castle post-minor deletion). The second-mode band fixed that, guarded by a
ratio (3.5x) chosen so a harmonic at 2x or 3x the true interval can never
qualify as a "mode". These tests pin both sides of that guard, plus the
series-conditional model that the structure exists to feed.
"""

from __future__ import annotations

import pandas as pd

from geyser_ai.backtest import load_intervals
from geyser_ai.models import SeriesConditionalModel


class TestSecondModeBand:
    def test_both_lion_modes_survive_the_filter(self):
        h = load_intervals("Lion")
        short = h[h["interval_min"] < 180]
        long = h[h["interval_min"] >= 300]
        assert len(short) > 300, "in-series intervals must survive"
        assert len(long) > 100, (
            f"series gaps must survive the filter (got {len(long)}) -- "
            "without the second-mode band this is zero"
        )

    def test_harmonics_still_rejected_for_unimodal_geysers(self):
        """The ratio guard: a 2x double must never ride in through the band.

        The fixture drops ~8% of eruptions from every unimodal geyser, so the
        raw gaps contain doubles at exactly 2x the true interval. None of them
        may come through as valid.
        """
        for g in ("Old Faithful", "Daisy", "Riverside"):
            h = load_intervals(g)
            med = float(h["interval_min"].median())
            assert float(h["interval_min"].max()) < 2.0 * med, (
                f"{g}: a valid interval at >=2x the median means a double survived"
            )


class TestSeriesConditionalModel:
    def _fit(self, was_initial: bool):
        h = load_intervals("Lion")
        row = pd.Series({"prev_initial": was_initial, "prev_minor": False})
        return SeriesConditionalModel(window=600).fit_predict(h, row)

    def test_post_initial_expects_the_series_to_continue(self):
        pred = self._fit(True)
        assert pred is not None and pred.model == "series_conditional"
        assert pred.median() < 180, (
            f"after an initial the series usually continues (~83 min); got {pred.median():.0f}"
        )

    def test_post_non_initial_shifts_toward_the_gap(self):
        p_ini, p_mid = self._fit(True), self._fit(False)
        # Compare P(next within 3h): must be clearly higher after an initial.
        c_ini = float(p_ini.dist.cdf(180.0))
        c_mid = float(p_mid.dist.cdf(180.0))
        assert c_ini > c_mid + 0.15, (
            f"continue-probability must depend on the anchor branch "
            f"(post-initial {c_ini:.2f} vs mid-series {c_mid:.2f})"
        )

    def test_mixture_quantiles_are_monotone(self):
        pred = self._fit(False)
        qs = [float(pred.dist.ppf(q)) for q in (0.05, 0.25, 0.5, 0.75, 0.95)]
        assert all(b > a for a, b in zip(qs, qs[1:], strict=False)), qs

    def test_unimodal_window_degrades_to_pooled_fit(self):
        """Safety property that makes the model harmless in any roster."""
        h = load_intervals("Daisy").copy()
        h["prev_initial"] = False
        row = pd.Series({"prev_initial": False})
        pred = SeriesConditionalModel(window=600).fit_predict(h, row)
        assert pred is not None
        med = float(h["interval_min"].tail(600).median())
        assert abs(pred.median() - med) / med < 0.15, (
            "on unimodal data the model must fall back to the pooled fit"
        )

    def test_marginal_is_the_pooled_mixture(self):
        marg = SeriesConditionalModel(window=600).fit_marginal(load_intervals("Lion"))
        assert marg is not None
        # Both modes must carry real mass: a plain lognormal would put ~0
        # probability on one of them.
        p_short = float(marg.cdf(180.0))
        assert 0.2 < p_short < 0.9, f"pooled short-mode mass {p_short:.2f}"


class TestServingPath:
    def test_lion_serves_the_series_model_with_branch_explain(self):
        from geyser_ai.predict import predict_geyser

        r = predict_geyser("Lion")
        assert r is not None
        assert r["model"] == "series_conditional"
        assert "series" in r["explain"]["branch"]["condition"]
