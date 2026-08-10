"""Live precursor signals: measured rates surfaced honestly, nothing broken."""

from __future__ import annotations

from geyser_ai.signals import live_signals


class TestLiveSignals:
    def test_turban_lattice_surfaces_on_grand(self):
        sig = live_signals()
        assert any("Turban" in n for n in sig["cards"].get("Grand", []))
        # The fixture's last Beehive is ~18 h back, so its Indicator is stale:
        # no signal may be invented for it.
        assert not any("Indicator" in n for n in sig["cards"].get("Beehive", []))

    def test_signals_attach_to_cards_in_the_payload(self):
        import geyser_ai.service as svc

        payload = svc.get_predictions(do_sync=False, density_points=16, record=False)
        assert "park_signals" in payload
        grand = next(p for p in payload["predictions"] if p.get("geyser") == "Grand")
        # Turban's fixture lattice runs to END_EPOCH, ~10 minutes before "now",
        # so the presence note must be on the Grand card.
        assert any("observers on station" in n for n in grand.get("live_signals", []))

    def test_a_broken_signals_read_returns_empty_not_error(self):
        sig = live_signals(db_path="/nonexistent/nope.duckdb")
        assert sig == {"cards": {}, "park": []}

    def test_every_note_with_a_probability_states_it(self):
        """A 7% signal must say 7%: the number is the honesty."""
        from geyser_ai.signals import CARD_SIGNALS, PARK_SIGNALS

        for _, _, _, template in CARD_SIGNALS:
            if "Bubbler" in template:
                assert "63%" in template
        for _, _, template in PARK_SIGNALS:
            assert "%" in template
