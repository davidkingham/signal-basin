"""HTTP contract. Anything here is what a dashboard or client depends on."""

from __future__ import annotations

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from geyser_ai.api import app
from geyser_ai.config import TARGET_GEYSERS

client = TestClient(app)

PREDICTION_KEYS = {
    "geyser",
    "model",
    "explain",
    "last_eruption_utc",
    "last_eruption_local",
    "data_age_hours",
    "median_interval_min",
    "interval_50_min",
    "interval_90_min",
    "predicted_time_local",
    "window_50_local",
    "window_90_local",
    "predicted_utc",
    "window_50_utc",
    "window_90_utc",
    "minutes_until",
    "expected_missed_eruptions",
    "data_is_stale",
    "observation_completeness",
    "regime",
    "density",
}


class TestHealth:
    def test_shape(self):
        d = client.get("/api/health").json()
        assert d["status"] == "ok"
        assert d["archive_rows"] > 0
        assert list(d["target_geysers"]) == list(TARGET_GEYSERS)
        assert d["data_age_hours"] is not None


class TestPredictions:
    def test_all_geysers_present_and_sorted(self):
        d = client.get("/api/predictions?hours=12&points=16").json()
        preds = [
            p for p in d["predictions"] if "error" not in p and p.get("display_mode") != "context"
        ]
        assert len(preds) == len(TARGET_GEYSERS)
        # Soonest-first among live predictions; planning-mode cards name no
        # time, so they sit below every live prediction regardless of their
        # internal renewal median.
        live = [p["minutes_until"] for p in preds if p.get("display_mode") != "planning"]
        assert live == sorted(live), "live predictions must be soonest-first"
        flags = [p.get("display_mode") == "planning" for p in preds]
        assert flags == sorted(flags), "planning cards must sort last"

    def test_contract_keys(self):
        p = client.get("/api/predictions?points=16").json()["predictions"][0]
        assert set(p) >= PREDICTION_KEYS, f"missing: {PREDICTION_KEYS - set(p)}"

    def test_explain_block_shape(self):
        """The "why this time?" panel renders these verbatim; they must exist."""
        for p in client.get("/api/predictions?points=16").json()["predictions"]:
            if "error" in p or p.get("display_mode") == "context":
                continue
            ex = p["explain"]
            assert {"entry_type"} <= set(ex["anchor"])
            assert ex["anchor"]["entry_type"] in ("in-person", "webcam", "electronic logger")
            if "branch" in ex:
                assert ex["branch"]["condition"] in (
                    "after a minor",
                    "after a full eruption",
                    "after a series initial",
                    "after a mid-series eruption",
                )
                assert 0 < ex["branch"]["n_branch"] <= ex["branch"]["n_window"]

    def test_windows_nested_and_ordered(self):
        for p in client.get("/api/predictions?points=16").json()["predictions"]:
            if p.get("display_mode") == "context":
                continue
            lo50, hi50 = p["interval_50_min"]
            lo90, hi90 = p["interval_90_min"]
            assert lo90 <= lo50 <= hi50 <= hi90
            assert lo50 <= p["median_interval_min"] <= hi50

    def test_density_spans_the_requested_window(self):
        d = client.get("/api/predictions?hours=6&points=48").json()
        p = d["predictions"][0]
        assert len(p["density"]) == 48
        assert all(0.0 <= q["d"] <= 1.0 for q in p["density"])
        span = pd.Timestamp(p["density"][-1]["t"]) - pd.Timestamp(p["density"][0]["t"])
        assert span == pytest.approx(pd.Timedelta(hours=6), abs=pd.Timedelta(minutes=2))

    def test_utc_and_local_agree(self):
        p = client.get("/api/predictions?points=16").json()["predictions"][0]
        anchor = pd.Timestamp(p["last_eruption_utc"])
        predicted = pd.Timestamp(p["predicted_utc"])
        assert (predicted - anchor).total_seconds() / 60 == pytest.approx(
            p["median_interval_min"], abs=1.0
        )

    def test_single_geyser_is_case_insensitive(self):
        d = client.get("/api/predictions/old%20faithful").json()
        assert d["prediction"]["geyser"] == "Old Faithful"

    def test_single_geyser_denser_curve(self):
        d = client.get("/api/predictions/Grand?points=240").json()
        assert len(d["prediction"]["density"]) == 240

    def test_unknown_geyser_404s(self):
        r = client.get("/api/predictions/Nonesuch")
        assert r.status_code == 404
        assert "Nonesuch" in r.json()["detail"]

    @pytest.mark.parametrize("qs", ["hours=0", "hours=999", "points=1", "points=99999"])
    def test_parameter_validation(self, qs):
        assert client.get(f"/api/predictions?{qs}").status_code == 422


class TestRecent:
    def test_shape_and_ordering(self):
        d = client.get("/api/eruptions/recent?hours=48").json()
        assert d["hours"] == 48
        assert d["count"] == len(d["eruptions"])
        ago = [e["minutes_ago"] for e in d["eruptions"]]
        assert ago == sorted(ago), "newest first"

    def test_targets_only_filter(self):
        d = client.get("/api/eruptions/recent?hours=168&targets_only=true").json()
        assert set(e["geyser"] for e in d["eruptions"]) <= set(TARGET_GEYSERS)

    def test_single_geyser_filter(self):
        d = client.get("/api/eruptions/recent?hours=168&geyser=Daisy").json()
        assert {e["geyser"] for e in d["eruptions"]} in ({"Daisy"}, set())

    @pytest.mark.parametrize("qs", ["hours=0", "hours=1000"])
    def test_parameter_validation(self, qs):
        assert client.get(f"/api/eruptions/recent?{qs}").status_code == 422


class TestStats:
    def test_all_and_single(self):
        allst = client.get("/api/stats").json()["stats"]
        assert len(allst) == len(TARGET_GEYSERS)
        one = client.get("/api/stats?geyser=Castle").json()["stats"]
        assert len(one) == 1 and one[0]["geyser"] == "Castle"

    def test_percentiles_ordered(self):
        for s in client.get("/api/stats").json()["stats"]:
            assert s["p05_interval_min"] <= s["median_interval_min"] <= s["p95_interval_min"]
            assert s["n_valid_intervals"] > 0

    def test_unknown_geyser_404s(self):
        assert client.get("/api/stats?geyser=Nope").status_code == 404


class TestDashboard:
    def test_serves_html(self):
        r = client.get("/")
        assert r.status_code == 200
        body = r.text
        assert "<title>" in body and "Geyser AI" in body
        # the page depends on these endpoints existing
        assert "/api/predictions" in body and "/api/eruptions/recent" in body
