"""The three-way comparison: logging predictions, matching them, scoring them.

The matching rules decide who looks good, so they are tested rather than
trusted. The same politeness properties the eruption sync promises are asserted
again here, because the scoreboard adds a second GeyserTimes endpoint to the
five-minute cycle and that is exactly where request volume creeps in.
"""

from __future__ import annotations

import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from geyser_ai import ledger as ledger_mod
from geyser_ai import sources as sources_mod
from geyser_ai.api import app
from geyser_ai.config import USER_AGENT
from geyser_ai.scoring import (
    Eruption,
    LoggedPrediction,
    match_and_score,
    score_one,
)

client = TestClient(app)

HOUR = 3600
# Anchored to the real clock so scored eruptions land inside the rolling window
# the scoreboard reports on. Every assertion below is on relative offsets, so
# the arithmetic stays deterministic.
NOW = int(time.time()) - 3 * HOUR


def pred(
    source: str,
    geyser: str = "Grand",
    key: str | None = None,
    issued: int = NOW,
    predicted: int = NOW + HOUR,
    window: tuple[int, int] | None = None,
    inner: tuple[int, int] | None = None,
    detail: str = "",
) -> LoggedPrediction:
    return LoggedPrediction(
        source=source,
        geyser=geyser,
        key=key or f"{source}:{issued}:{predicted}",
        issued_epoch=issued,
        predicted_epoch=predicted,
        window_open_epoch=window[0] if window else None,
        window_close_epoch=window[1] if window else None,
        inner_open_epoch=inner[0] if inner else None,
        inner_close_epoch=inner[1] if inner else None,
        detail=detail,
    )


def erupt(geyser: str = "Grand", eid: int = 1, epoch: int = NOW + HOUR) -> Eruption:
    return Eruption(geyser=geyser, eruption_id=eid, epoch=epoch)


class TestScoreOne:
    def test_signed_error_is_positive_when_the_eruption_runs_late(self):
        s = score_one(pred("nps", predicted=NOW + HOUR), erupt(epoch=NOW + HOUR + 600))
        assert s.signed_error_min == 10.0
        assert s.abs_error_min == 10.0

    def test_signed_error_is_negative_when_the_eruption_comes_early(self):
        s = score_one(pred("nps", predicted=NOW + HOUR), erupt(epoch=NOW + HOUR - 300))
        assert s.signed_error_min == -5.0
        assert s.abs_error_min == 5.0

    def test_lead_time_is_measured_from_when_the_prediction_was_issued(self):
        s = score_one(pred("nps", issued=NOW, predicted=NOW + HOUR), erupt(epoch=NOW + HOUR))
        assert s.lead_minutes == 60.0

    def test_in_window_uses_the_sources_own_window(self):
        window = (NOW + HOUR - 600, NOW + HOUR + 600)
        assert score_one(pred("nps", window=window), erupt(epoch=NOW + HOUR + 300)).in_window
        assert not score_one(pred("nps", window=window), erupt(epoch=NOW + HOUR + 900)).in_window

    def test_window_width_is_reported_so_rates_can_be_read_fairly(self):
        s = score_one(pred("nps", window=(NOW, NOW + 2 * HOUR)), erupt(epoch=NOW + HOUR))
        assert s.window_width_min == 120.0

    def test_a_source_that_states_no_window_is_not_scored_on_windows(self):
        s = score_one(pred("geysers_net", window=None), erupt())
        assert s.in_window is None
        assert s.window_width_min is None

    def test_the_inner_window_is_scored_separately(self):
        p = pred(
            "geyser_ai",
            predicted=NOW + HOUR,
            window=(NOW + HOUR - 3600, NOW + HOUR + 3600),
            inner=(NOW + HOUR - 300, NOW + HOUR + 300),
        )
        assert score_one(p, erupt(epoch=NOW + HOUR + 120)).in_inner_window is True
        assert score_one(p, erupt(epoch=NOW + HOUR + 1200)).in_inner_window is False
        assert score_one(pred("nps"), erupt()).in_inner_window is None


class TestMatching:
    def test_the_latest_prediction_before_the_eruption_is_the_one_scored(self):
        early = pred("nps", key="a", issued=NOW, predicted=NOW + HOUR)
        late = pred("nps", key="b", issued=NOW + 1800, predicted=NOW + HOUR + 600)
        res = match_and_score([early, late], [erupt(epoch=NOW + HOUR + 600)], NOW + 2 * HOUR)

        assert len(res.scored) == 1
        assert res.scored[0].abs_error_min == 0.0, "the revision, not the withdrawn guess"
        assert res.superseded == 1

    def test_a_superseded_prediction_is_discarded_not_counted_as_a_miss(self):
        early = pred("nps", key="a", issued=NOW, predicted=NOW + 10 * HOUR)
        late = pred("nps", key="b", issued=NOW + 60, predicted=NOW + HOUR)
        res = match_and_score([early, late], [erupt(epoch=NOW + HOUR)], NOW + 2 * HOUR)

        assert [s.abs_error_min for s in res.scored] == [0.0]
        assert res.still_open == []

    def test_a_prediction_issued_after_the_eruption_stays_open(self):
        later = pred("nps", issued=NOW + 2 * HOUR, predicted=NOW + 3 * HOUR)
        res = match_and_score([later], [erupt(epoch=NOW + HOUR)], NOW + 2 * HOUR)

        assert res.scored == []
        assert res.still_open == [later]

    def test_an_eruption_no_source_predicted_scores_nobody(self):
        res = match_and_score([pred("nps", geyser="Daisy")], [erupt(geyser="Castle")], NOW + HOUR)
        assert res.scored == []

    def test_each_source_is_matched_independently(self):
        preds = [
            pred("nps", key="n", predicted=NOW + HOUR),
            pred("geysers_net", key="g", predicted=NOW + HOUR + 1200),
            pred("geyser_ai", key="m", predicted=NOW + HOUR - 600),
        ]
        res = match_and_score(preds, [erupt(epoch=NOW + HOUR)], NOW + 2 * HOUR)
        assert {s.source for s in res.scored} == {"nps", "geysers_net", "geyser_ai"}

    def test_rescoring_the_same_eruption_does_not_double_count(self):
        p = pred("nps", key="a", predicted=NOW + HOUR)
        first = match_and_score([p], [erupt(eid=7, epoch=NOW + HOUR)], NOW + 2 * HOUR)
        assert len(first.scored) == 1

        again = match_and_score(
            [p],
            [erupt(eid=7, epoch=NOW + HOUR)],
            NOW + 2 * HOUR,
            already_scored={("nps", 7)},
        )
        assert again.scored == []

    def test_consecutive_eruptions_consume_predictions_in_order(self):
        first = pred("nps", key="a", issued=NOW, predicted=NOW + HOUR)
        second = pred("nps", key="b", issued=NOW + HOUR + 60, predicted=NOW + 2 * HOUR)
        res = match_and_score(
            [first, second],
            [erupt(eid=1, epoch=NOW + HOUR), erupt(eid=2, epoch=NOW + 2 * HOUR)],
            NOW + 3 * HOUR,
        )
        assert [(s.eruption_id, s.abs_error_min) for s in res.scored] == [(1, 0.0), (2, 0.0)]


class TestCensoringAndExpiry:
    def test_an_eruption_far_past_the_prediction_is_not_scored(self):
        """Almost certainly an unlogged eruption in between -- not the forecaster's fault."""
        p = pred("nps", predicted=NOW + HOUR, window=(NOW + HOUR - 600, NOW + HOUR + 600))
        res = match_and_score([p], [erupt(epoch=NOW + 20 * HOUR)], NOW + 21 * HOUR)

        assert res.scored == []
        assert res.beyond_horizon == 1

    def test_the_horizon_scales_with_the_stated_window(self):
        """A source claiming a four-hour window gets four hours' worth of benefit."""
        wide = pred("nps", predicted=NOW + HOUR, window=(NOW - HOUR, NOW + 3 * HOUR))
        res = match_and_score([wide], [erupt(epoch=NOW + 9 * HOUR)], NOW + 10 * HOUR)
        assert len(res.scored) == 1, "9h is inside 3x a 4h window"

    def test_a_prediction_nothing_ever_matched_expires(self):
        old = pred("nps", issued=NOW, predicted=NOW + HOUR)
        res = match_and_score([old], [], now_epoch=NOW + 10 * 24 * HOUR)
        assert res.still_open == []
        assert res.expired == 1

    def test_a_recent_unmatched_prediction_stays_open(self):
        fresh = pred("nps", issued=NOW, predicted=NOW + HOUR)
        res = match_and_score([fresh], [], now_epoch=NOW + HOUR)
        assert res.still_open == [fresh]
        assert res.expired == 0


class TestSourceAttribution:
    def test_nps_needs_both_the_account_and_the_upload_marker(self):
        assert (
            sources_mod.classify(
                {"userID": "208", "comment": "Prediction uploaded from NPS/CartoDB system"}
            )
            == "nps"
        )
        assert sources_mod.classify({"userID": "208", "comment": "a human note"}) is None

    def test_geysers_net_is_identified_by_account(self):
        assert sources_mod.classify({"userID": "44", "comment": "Auto-generated"}) == "geysers_net"

    def test_an_unknown_predictor_is_dropped_rather_than_misattributed(self):
        assert sources_mod.classify({"userID": "999", "comment": ""}) is None


class TestFeedParsing:
    def raw(self, **over):
        row = {
            "geyserName": "Grand",
            "userID": "44",
            "comment": "Auto-generated prediction",
            "prediction": "2026-08-04T06:58:00+0000",
            "predictionID": "185619",
            "windowOpen": "2026-08-04T04:58:00+0000",
            "windowClose": "2026-08-04T08:58:00+0000",
            "expiration": "2026-08-04T10:58:00+0000",
            "timeEntered": "1785768602",
            "intervalNumber": "1",
            "futureEruptionNumber": "1",
            "method": "Add average interval",
        }
        row.update(over)
        return row

    def test_a_normal_prediction_round_trips(self):
        got, unclassified = sources_mod.parse_predictions({"predictions": [self.raw()]})
        assert unclassified == 0
        assert len(got) == 1
        p = got[0]
        assert (p.source, p.geyser) == ("geysers_net", "Grand")
        assert p.window_close_epoch - p.window_open_epoch == 4 * HOUR
        assert p.detail == "Add average interval"

    def test_predictions_for_a_later_eruption_are_not_scored_against_the_next_one(self):
        got, _ = sources_mod.parse_predictions(
            {"predictions": [self.raw(futureEruptionNumber="2")]}
        )
        assert got == []

    def test_geysers_we_do_not_model_are_ignored(self):
        got, _ = sources_mod.parse_predictions({"predictions": [self.raw(geyserName="Fan")]})
        assert got == []

    def test_a_doubled_interval_is_carried_into_the_detail(self):
        got, _ = sources_mod.parse_predictions({"predictions": [self.raw(intervalNumber="2")]})
        assert "interval x2" in got[0].detail

    def test_an_empty_last_report_time_is_absent_not_zero(self):
        got, _ = sources_mod.parse_predictions({"predictions": [self.raw(windowOpen="")]})
        assert got[0].window_open_epoch is None

    def test_unattributable_rows_are_counted(self):
        _, unclassified = sources_mod.parse_predictions(
            {"predictions": [self.raw(userID="999", comment="")]}
        )
        assert unclassified == 1


class TestFeedPoliteness:
    @pytest.fixture
    def calls(self, monkeypatch):
        seen = []

        class FakeResponse:
            status_code = 200

            def json(self):
                return {"status": "success", "predictions": []}

            def raise_for_status(self):
                return None

        def fake_get(url, **kw):
            seen.append({"url": url, "headers": kw.get("headers", {})})
            return FakeResponse()

        monkeypatch.setattr(sources_mod.httpx, "get", fake_get)
        sources_mod._state.update(last_attempt=0.0, last_success=None, error=None)
        return seen

    def test_one_request_per_cycle(self, calls):
        sources_mod.fetch_predictions(force=True)
        assert len(calls) == 1, "the scoreboard must not become a crawl"

    def test_it_uses_the_single_latest_endpoint(self, calls):
        sources_mod.fetch_predictions(force=True)
        assert calls[0]["url"].endswith("/predictions_latest")

    def test_identifying_user_agent(self, calls):
        sources_mod.fetch_predictions(force=True)
        ua = calls[0]["headers"].get("User-Agent", "")
        assert ua == USER_AGENT
        assert "Mozilla" not in ua, "must not impersonate a browser"

    def test_ttl_suppresses_a_second_request(self, calls):
        sources_mod.fetch_predictions(force=True)
        sources_mod.fetch_predictions()
        assert len(calls) == 1

    def test_default_ttl_respects_the_stated_policy(self):
        assert sources_mod.TTL_SECONDS >= 300

    def test_network_failure_yields_nothing_rather_than_raising(self, monkeypatch):
        def boom(url, **kw):
            raise httpx.ConnectError("no network")

        monkeypatch.setattr(sources_mod.httpx, "get", boom)
        sources_mod._state.update(last_attempt=0.0, error=None)
        assert sources_mod.fetch_predictions(force=True) == []
        assert "ConnectError" in sources_mod.status()["error"]


class TestLedgerPersistence:
    def test_it_survives_a_restart(self, tmp_path):
        store = ledger_mod.FileLedgerStore(tmp_path / "l.json")
        first = ledger_mod.Ledger(store)
        first.load()
        first.add_open([pred("nps", key="a")])
        first.apply(match_and_score([pred("nps", key="b")], [erupt()], NOW + 2 * HOUR))
        assert first.flush()

        second = ledger_mod.Ledger(ledger_mod.FileLedgerStore(tmp_path / "l.json"))
        second.load()
        assert len(second.scored) == 1
        assert second.scored[0].source == "nps"

    def test_the_same_prediction_seen_twice_is_stored_once(self, tmp_path):
        led = ledger_mod.Ledger(ledger_mod.FileLedgerStore(tmp_path / "l.json"))
        led.load()
        assert led.add_open([pred("nps", key="same")]) == 1
        assert led.add_open([pred("nps", key="same")]) == 0

    def test_open_predictions_per_series_are_bounded(self, tmp_path):
        """Beehive's nowcast re-answers every cycle; the ledger must not grow with it."""
        led = ledger_mod.Ledger(ledger_mod.FileLedgerStore(tmp_path / "l.json"))
        led.load()
        for i in range(40):
            led.add_open([pred("geyser_ai", geyser="Beehive", key=f"k{i}", issued=NOW + i)])

        kept = [p for p in led.open.values() if p.geyser == "Beehive"]
        assert len(kept) == ledger_mod.MAX_OPEN_PER_SERIES
        assert max(p.issued_epoch for p in kept) == NOW + 39, "the newest must survive"

    def test_capping_one_series_leaves_others_alone(self, tmp_path):
        led = ledger_mod.Ledger(ledger_mod.FileLedgerStore(tmp_path / "l.json"))
        led.load()
        led.add_open([pred("nps", geyser="Grand", key="grand")])
        for i in range(40):
            led.add_open([pred("geyser_ai", geyser="Beehive", key=f"k{i}", issued=NOW + i)])
        assert "grand" in led.open

    def test_a_corrupt_ledger_starts_fresh_instead_of_crashing(self, tmp_path):
        path = tmp_path / "l.json"
        path.write_text(json.dumps({"open": [{"nonsense": True}], "scored": []}))
        led = ledger_mod.Ledger(ledger_mod.FileLedgerStore(path))
        led.load()
        assert led.scored == []
        assert led.error and "parse failed" in led.error

    def test_a_write_failure_is_reported_not_raised(self, tmp_path):
        class Broken(ledger_mod.LedgerStore):
            def read(self):
                return None

            def write(self, doc):
                raise OSError("object storage is down")

        led = ledger_mod.Ledger(Broken())
        led.load()
        assert led.flush() is False
        assert "write failed" in led.error


class TestScoreboardEndpoints:
    def test_empty_scoreboard_is_a_designed_state_not_an_error(self):
        d = client.get("/api/scoreboard").json()
        assert [r["geyser"] for r in d["rows"]]
        assert all(r["n_eruptions"] == 0 for r in d["rows"])
        assert all(v is None for r in d["rows"] for v in r["by_source"].values())
        assert d["methodology"]

    def test_every_source_is_declared_with_its_window_semantics(self):
        d = client.get("/api/scoreboard").json()
        keys = {s["key"] for s in d["sources"]}
        assert keys == {"geyser_ai", "nps", "geysers_net"}
        assert all(s["description"] and s["window_label"] for s in d["sources"])

    def test_a_scored_eruption_shows_up_per_source_with_width_beside_the_rate(self):
        led = ledger_mod.get_ledger()
        led.apply(
            match_and_score(
                [
                    pred(
                        "nps",
                        key="n1",
                        geyser="Grand",
                        predicted=NOW + HOUR,
                        window=(NOW + HOUR - 600, NOW + HOUR + 600),
                    ),
                    pred(
                        "geyser_ai",
                        key="m1",
                        geyser="Grand",
                        predicted=NOW + HOUR + 300,
                        window=(NOW + HOUR - 1800, NOW + HOUR + 1800),
                        inner=(NOW + HOUR - 300, NOW + HOUR + 300),
                    ),
                ],
                [erupt(geyser="Grand", eid=42, epoch=NOW + HOUR)],
                NOW + 2 * HOUR,
            )
        )
        row = next(
            r for r in client.get("/api/scoreboard").json()["rows"] if r["geyser"] == "Grand"
        )
        assert row["n_eruptions"] == 1
        nps = row["by_source"]["nps"]
        assert nps["n"] == 1
        assert nps["in_window_rate"] == 1.0
        assert nps["median_window_width_min"] == 20.0, "rate is meaningless without the width"
        assert row["by_source"]["geyser_ai"]["in_50_rate"] == 1.0
        assert row["by_source"]["geysers_net"] is None

    def test_recent_comparisons_pair_each_source_against_the_actual(self):
        led = ledger_mod.get_ledger()
        led.apply(
            match_and_score(
                [pred("nps", key="n1", geyser="Daisy", predicted=NOW + HOUR - 120)],
                [erupt(geyser="Daisy", eid=9, epoch=NOW + HOUR)],
                NOW + 2 * HOUR,
            )
        )
        d = client.get("/api/comparisons/recent").json()
        assert d["count"] == 1
        item = d["comparisons"][0]
        assert item["geyser"] == "Daisy"
        assert item["sources"]["nps"]["signed_error_min"] == 2.0
        assert item["sources"]["geyser_ai"] is None

    def test_predicting_but_unscored_is_not_the_same_as_never_predicting(self):
        """Beehive has no NPS prediction at all; a fresh Grand one just has no outcome yet."""
        ledger_mod.get_ledger().add_open([pred("nps", geyser="Grand", key="open1")])
        rows = {r["geyser"]: r for r in client.get("/api/scoreboard").json()["rows"]}

        grand = rows["Grand"]["by_source"]["nps"]
        assert grand is not None and grand["n"] == 0
        assert grand["awaiting_first_eruption"] is True
        assert rows["Beehive"]["by_source"]["nps"] is None

    def test_days_are_offered_before_the_day_filter_narrows_them(self):
        """Selecting a day must not leave the picker with only that day in it."""
        led = ledger_mod.get_ledger()
        for i, offset in enumerate((0, 30 * HOUR)):
            when = NOW + HOUR - offset
            led.apply(
                match_and_score(
                    # Issued before the eruption it is about, or nothing matches.
                    [pred("nps", key=f"k{i}", geyser="Daisy", issued=when - HOUR, predicted=when)],
                    [erupt(geyser="Daisy", eid=100 + i, epoch=when)],
                    NOW + 2 * HOUR,
                )
            )
        days = client.get("/api/comparisons/recent").json()["available_days"]
        assert len(days) == 2, "two calendar days of eruptions"

        one = client.get(f"/api/comparisons/recent?day={days[0]['date']}").json()
        assert one["total"] == 1
        assert len(one["available_days"]) == 2, "the picker still offers both days"

    def test_paging_reports_where_it_is(self):
        led = ledger_mod.get_ledger()
        for i in range(5):
            when = NOW + (i + 1) * HOUR
            led.apply(
                match_and_score(
                    [pred("nps", key=f"p{i}", geyser="Grand", issued=when - HOUR, predicted=when)],
                    [erupt(geyser="Grand", eid=200 + i, epoch=when)],
                    NOW + 10 * HOUR,
                )
            )
        first = client.get("/api/comparisons/recent?limit=2").json()
        assert (first["total"], first["count"], first["has_more"]) == (5, 2, True)

        last = client.get("/api/comparisons/recent?limit=2&offset=4").json()
        assert (last["count"], last["has_more"]) == (1, False)

        seen = {c["eruption_id"] for c in first["comparisons"]}
        assert not seen & {c["eruption_id"] for c in last["comparisons"]}, "pages must not overlap"

    def test_a_window_longer_than_the_ledger_does_not_claim_to_cover_it(self):
        d = client.get("/api/scoreboard?days=36500").json()
        assert d["since_utc"] >= d["logging_started_utc"], (
            "must never claim to cover time before logging began"
        )

    def test_an_unknown_geyser_filter_is_rejected(self):
        assert client.get("/api/scoreboard?geyser=Nope").status_code == 404
        assert client.get("/api/comparisons/recent?geyser=Nope").status_code == 404


class TestScoreboardNeverBreaksPredictions:
    def test_a_broken_scoreboard_still_returns_predictions(self, monkeypatch):
        import geyser_ai.service as svc

        def boom(*a, **kw):
            raise RuntimeError("ledger exploded")

        monkeypatch.setattr(svc, "update_scoreboard", boom)
        d = svc.get_predictions(do_sync=False, density_points=16)
        # +1: the Steamboat context card rides along without being a target
        assert len(d["predictions"]) == len(svc.TARGET_GEYSERS) + 1
        assert "ledger exploded" in d["scoreboard_error"]

    def test_a_single_geyser_request_does_not_log_a_new_forecast(self, monkeypatch):
        import geyser_ai.service as svc

        seen = []
        monkeypatch.setattr(svc, "update_scoreboard", lambda *a, **kw: seen.append(1))
        svc.get_predictions(geysers=["Grand"], do_sync=False, density_points=16)
        assert seen == [], "a detail view is not a new prediction"


class TestOurPredictionsAreLogged:
    def test_a_full_run_records_every_geyser_once(self):
        import geyser_ai.service as svc

        payload = svc.get_predictions(do_sync=False, density_points=16)
        led = ledger_mod.get_ledger()
        ours = [p for p in led.open.values() if p.source == "geyser_ai"]
        # A planning-mode card makes no clock-time claim, so it is deliberately
        # absent from the ledger. The fixture pins both poles: Lone Star's
        # anchor is 20 h old (past its 2.5-cycle window -> planning), Till's is
        # 5 cycles old (inside its 8-cycle window -> live and logged).
        expected = {
            p["geyser"]
            for p in payload["predictions"]
            if "error" not in p and p.get("display_mode") not in ("planning", "context")
        }
        assert "Till" in expected and "Lone Star" not in expected
        assert {p.geyser for p in ours} == expected
        assert all(p.window_open_epoch and p.inner_open_epoch for p in ours)

    def test_recomputing_a_steady_forecast_does_not_grow_the_ledger(self):
        """Keyed on the predicted minute, so an unchanged answer is stored once.

        Grand is the right subject: its neighbour conditioning is a reported
        negative result, so it always runs in the base regime and its predicted
        time only moves when new data arrives. Beehive is deliberately not
        checked here -- once its Indicator fires the nowcast is anchored to the
        present, so every recompute really is a new answer, and the ledger's
        per-series cap is what bounds that instead.
        """
        import geyser_ai.service as svc

        def grand_keys() -> set[str]:
            return {
                k
                for k, p in ledger_mod.get_ledger().open.items()
                if p.source == "geyser_ai" and p.geyser == "Grand"
            }

        svc.get_predictions(do_sync=False, density_points=16)
        before = grand_keys()
        svc.get_predictions(do_sync=False, density_points=16)

        assert before and grand_keys() == before

    def test_a_logged_prediction_carries_the_model_that_made_it(self):
        import geyser_ai.service as svc

        svc.get_predictions(do_sync=False, density_points=16)
        ours = [p for p in ledger_mod.get_ledger().open.values() if p.source == "geyser_ai"]
        assert all(p.detail for p in ours)


def test_ledger_write_is_not_on_the_clock_of_the_request(tmp_path):
    """A slow object store must not be able to wedge the prediction path."""
    slow = ledger_mod.Ledger(ledger_mod.FileLedgerStore(tmp_path / "l.json"))
    slow.load()
    started = time.monotonic()
    slow.flush()
    assert time.monotonic() - started < 1.0


class TestCalibrationBoundary:
    """Rows scored before the calibrated system went live do not exist publicly.

    The ledger drops this project's pre-calibration rows at load and on every
    trim (owner's decision, 2026-08-10): they measured a serving bug, and the
    official record starts with the calibrated system. The removed rows are
    archived off-display in R2. Third-party rows are untouched -- their
    predictions were never ours to break. Nothing is ever backfilled.
    """

    def test_prefix_own_rows_vanish_third_party_stays(self):

        import geyser_ai.service as svc
        from geyser_ai.config import CALIBRATION_EPOCH

        led = ledger_mod.get_ledger()
        before = CALIBRATION_EPOCH - 86400
        after = CALIBRATION_EPOCH + 86400
        for src, actual, eid in (
            ("geyser_ai", before, 900001),
            ("geyser_ai", after, 900002),
            ("nps", before, 900003),
        ):
            p = pred(
                src,
                geyser="Old Faithful",
                key=f"cal-{src}-{actual}",
                issued=actual - 3600,
                predicted=actual - 600,
                window=(actual - 1800, actual + 600),
            )
            res = match_and_score(
                [p], [Eruption(geyser="Old Faithful", eruption_id=eid, epoch=actual)], actual + 3600
            )
            led.scored.extend(res.scored)
        led._trim()

        sb = svc.get_scoreboard(days=365, geyser="Old Faithful")
        assert "precalibration" not in sb, "no public trace of the removed era"
        epochs = {(s.source, s.actual_epoch) for s in led.scored}
        assert ("geyser_ai", before) not in epochs, "our pre-fix row must be gone"
        assert ("geyser_ai", after) in epochs
        assert ("nps", before) in epochs, "third-party history is untouched"
