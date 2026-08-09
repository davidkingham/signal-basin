"""Lone Star: the planning card, and the data rules underneath it.

Lone Star is more regular than most served geysers (major-chain log-sd 0.133
at a 186-minute median) but backcountry: entries arrive with a median latency
of one full cycle, and the cycle forgets its phase in ~2-3 intervals, so an
anchor fresh enough to predict from exists ~7% of summer-daytime moments.
The serving contract: a live prediction only inside the phase window, a
planning card otherwise, and the ledger never scores a claim that was never
made. Three data rules feed it: minors are precursors (excluded from the
interval chain), the baseline anchors at the local p10 (singles are the
minority of gaps), and the second-mode band stays off (a sparse-singles
geyser's "long mode" is a smear of missed-day multiples, not a mode).
"""

from __future__ import annotations

import numpy as np

from geyser_ai.backtest import load_intervals
from geyser_ai.predict import predict_geyser


class TestLoneStarIntervals:
    def test_minors_are_not_cycle_events(self):
        """The chain must be majors-only: median near 186, not 37 or 150."""
        h = load_intervals("Lone Star")
        med = float(h["interval_min"].median())
        assert 150 < med < 220, f"median {med:.0f}: minor precursors leaked into the chain"

    def test_harmonics_rejected_despite_sparse_singles(self):
        """Half the fixture's majors are unlogged; no 2x+ gap may be valid."""
        h = load_intervals("Lone Star")
        med = float(h["interval_min"].median())
        assert float(h["interval_min"].max()) < 2.0 * med, (
            "a valid interval at >=2x the median means the sparse-singles "
            "anchor or the second-mode exclusion failed"
        )
        assert len(h) > 100, "enough singles must survive to fit a model on"

    def test_the_cycle_is_tight(self):
        v = load_intervals("Lone Star")["interval_min"].to_numpy()
        assert float(np.std(np.log(v), ddof=1)) < 0.25, (
            "Lone Star's whole point is regularity; a loose fit means contamination"
        )


class TestPlanningMode:
    def test_stale_anchor_serves_planning_not_a_time(self):
        r = predict_geyser("Lone Star")
        assert r is not None
        assert r["display_mode"] == "planning", (
            f"anchor is {r['data_age_hours']}h old against a "
            f"{r['phase_window_min']:.0f}-minute phase window"
        )
        assert r["phase_window_min"] > 0

    def test_till_inside_its_window_serves_live(self):
        """Till's fixture anchor is 5 cycles old -- inside its 8-cycle window."""
        r = predict_geyser("Till")
        assert r is not None
        assert r["display_mode"] == "live"
        assert r["model"] == "adaptive_lognormal"

    def test_till_intervals_exclude_afterplay_minors(self):
        h = load_intervals("Till")
        med = float(h["interval_min"].median())
        assert 600 < med < 850, f"median {med:.0f}: afterplay minors leaked into the chain"

    def test_fresh_anchor_serves_live(self, monkeypatch):
        """The same gate must open when the anchor carries phase."""
        import geyser_ai.predict as predict_mod

        # Plume's fixture anchor is minutes old; borrowing it isolates the
        # gate logic from the fixture's deliberately-stale Lone Star anchor.
        monkeypatch.setattr(predict_mod, "PHASE_LIMITED_GEYSERS", frozenset({"Plume", "Lone Star"}))
        monkeypatch.setattr(
            predict_mod, "PHASE_WINDOW_CYCLES", {**predict_mod.PHASE_WINDOW_CYCLES, "Plume": 2.5}
        )
        r = predict_geyser("Plume")
        assert r["display_mode"] == "live"

    def test_unlimited_geysers_carry_no_mode_flag(self):
        r = predict_geyser("Old Faithful")
        assert "display_mode" not in r

    def test_planning_mode_is_never_scored(self):
        """The ledger must not log a claim the card never made."""
        from geyser_ai.service import _our_logged_predictions

        payload = {
            "predictions": [
                {
                    "geyser": "Lone Star",
                    "display_mode": "planning",
                    "predicted_utc": "2026-08-09T20:00:00+00:00",
                    "window_90_utc": ["2026-08-09T19:00:00+00:00", "2026-08-09T21:00:00+00:00"],
                    "window_50_utc": ["2026-08-09T19:30:00+00:00", "2026-08-09T20:30:00+00:00"],
                    "model": "best_parametric",
                    "regime": "base",
                    "last_eruption_utc": "2026-08-09T10:00:00+00:00",
                }
            ]
        }
        assert _our_logged_predictions(payload) == []


class TestPlanningCardsSortLast:
    def test_planning_mode_sits_below_every_live_prediction(self):
        import geyser_ai.service as svc

        payload = svc.get_predictions(do_sync=False, density_points=16, record=False)
        rank = {"planning": 1, "context": 2}
        ranks = [
            rank.get(p.get("display_mode"), 0) for p in payload["predictions"] if "error" not in p
        ]
        # live < planning < context; no card may sort above its class.
        assert ranks == sorted(ranks), f"cards out of class order: {ranks}"
        assert 1 in ranks, "the fixture's stale Lone Star must appear as planning"
        assert ranks[-1] == 2, "the Steamboat context card must be last"


class TestSteamboatContextCard:
    def test_context_entry_present_and_last(self):
        import geyser_ai.service as svc

        payload = svc.get_predictions(do_sync=False, density_points=16, record=False)
        preds = [p for p in payload["predictions"] if "error" not in p]
        assert preds[-1]["geyser"] == "Steamboat", "context sorts below everything"
        sb = preds[-1]
        assert sb["display_mode"] == "context"
        assert 25 < sb["days_since"] < 35
        ri = sb["recent_intervals_days"]
        assert ri["n"] >= 5 and 35 < ri["min"] <= ri["median"] <= ri["max"] < 95
        assert "predicted_utc" not in sb, "a context card must never carry a time claim"

    def test_context_is_never_scored(self):
        from geyser_ai.service import _our_logged_predictions

        payload = {"predictions": [{"geyser": "Steamboat", "display_mode": "context"}]}
        assert _our_logged_predictions(payload) == []
