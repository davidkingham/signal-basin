"""The served methodology: it must match production, and match the report.

A page that explains the reasoning is only worth having if it cannot drift from
the thing it explains. Three drifts are possible and all three are asserted
against here:

* the page naming a model production does not serve;
* the page's numbers disagreeing with `reports/calibration_report.md`;
* the page claiming a margin is "inside the noise" when it is not.
"""

from __future__ import annotations

import json
import re

import pytest
from fastapi.testclient import TestClient

from geyser_ai.api import app
from geyser_ai.config import REPORTS_DIR, TARGET_GEYSERS
from geyser_ai.method import (
    CALIBRATION_PATH,
    MODEL_ROSTER,
    NOISE_MARGIN_PCT,
    OPEN_GAPS,
    get_method,
    load_calibration,
)
from geyser_ai.models import BEST_MODEL_BY_GEYSER, default_model_name

client = TestClient(app)

DOC = get_method()
BLOCKS = {g["geyser"]: g for g in DOC["geysers"]}


def _report_rows() -> tuple[dict, dict]:
    """(model rows, honest rows) parsed out of the published markdown report."""
    text = (REPORTS_DIR / "calibration_report.md").read_text()
    models: dict[tuple[str, str], dict] = {}
    honest: dict[str, dict] = {}
    section = ""
    for line in text.splitlines():
        if line.startswith("#"):
            section = line.strip("# ").strip()
        if not line.startswith("|") or line.startswith("|---"):
            continue
        c = [x.strip() for x in line.strip().strip("|").split("|")]
        if c[0] not in TARGET_GEYSERS:
            continue

        def num(s: str) -> float:
            return float(s.replace(",", "").replace("%", "").replace("*", "").strip(" ·⚠"))

        if section == "Metrics" and len(c) == 7:
            models[(c[0], c[1].strip("*"))] = {
                "n": int(c[2].replace(",", "")),
                "crps": num(c[3]),
                "mae": num(c[4]),
                "cov50": num(c[5]) / 100,
                "cov90": num(c[6]) / 100,
            }
        elif section.startswith("Honest coverage") and len(c) == 6:
            honest[c[0]] = {
                "n": int(c[1].replace(",", "")),
                "pct_rejected": num(c[2]),
                "cov50": num(c[3]) / 100,
                "cov90": num(c[4]) / 100,
            }
    return models, honest


class TestArtifactMatchesReport:
    """`calibration.json` is what the site serves; the markdown is what we publish."""

    def test_every_report_row_is_in_the_artifact(self):
        models, _ = _report_rows()
        assert models, "report parsed to nothing -- the parser or the report changed shape"
        cal = load_calibration()["geysers"]
        for (geyser, model), row in models.items():
            served = next(
                (m for m in cal[geyser]["models"] if m["model"] == model),
                None,
            )
            assert served is not None, f"{geyser}/{model} missing from calibration.json"
            assert served["n"] == row["n"]
            for field in ("crps", "mae"):
                assert abs(served[field] - row[field]) < 0.05, f"{geyser}/{model} {field}"
            for field in ("cov50", "cov90"):
                assert abs(served[field] - row[field]) < 0.0005, f"{geyser}/{model} {field}"

    def test_honest_coverage_matches(self):
        _, honest = _report_rows()
        cal = load_calibration()["geysers"]
        for geyser, row in honest.items():
            got = cal[geyser]["honest"]
            assert got["n"] == row["n"]
            assert abs(got["pct_rejected"] - row["pct_rejected"]) < 0.05
            assert abs(got["cov50"] - row["cov50"]) < 0.0005
            assert abs(got["cov90"] - row["cov90"]) < 0.0005

    def test_artifact_is_valid_json_and_covers_the_roster(self):
        cal = json.loads(CALIBRATION_PATH.read_text())
        assert set(TARGET_GEYSERS) <= set(cal["geysers"])
        assert cal["generated"] and cal["backtest_years"] >= 1


class TestServedModelCannotDrift:
    """The page must describe the models production actually runs."""

    @pytest.mark.parametrize("geyser", TARGET_GEYSERS)
    def test_names_the_model_production_serves(self, geyser):
        served = BLOCKS[geyser]["served"]
        assert served["model"] == default_model_name(geyser)
        assert served["pinned"] is (geyser in BEST_MODEL_BY_GEYSER)
        # And its metrics are that model's row, not the leaderboard winner's.
        assert served["metrics"]["model"] == served["model"]
        assert served["metrics"] in BLOCKS[geyser]["leaderboard"]

    @pytest.mark.parametrize("geyser", TARGET_GEYSERS)
    def test_unpinned_margins_really_are_inside_the_noise_rule(self, geyser):
        """The prose says an unpinned winner is inside the noise. Check it is.

        If a future backtest opens a decisive gap on a geyser nothing is pinned
        for, this fails -- which is the point: either pin the model in
        `BEST_MODEL_BY_GEYSER` or stop calling the margin noise.
        """
        served = BLOCKS[geyser]["served"]
        if served["pinned"]:
            return
        assert served["winner_ahead_pct"] < NOISE_MARGIN_PCT, (
            f"{geyser}: the walk-forward winner is {served['winner_ahead_pct']}% ahead of the "
            f"served default, which is no longer noise"
        )

    def test_every_model_on_a_leaderboard_is_described(self):
        seen = {row["model"] for b in BLOCKS.values() for row in b["leaderboard"]}
        assert seen <= set(MODEL_ROSTER), f"undocumented models: {seen - set(MODEL_ROSTER)}"


class TestDocumentShape:
    def test_every_card_has_a_block(self):
        assert set(BLOCKS) == {*TARGET_GEYSERS, "Steamboat"}

    def test_slugs_are_unique_and_linkable(self):
        slugs = [b["slug"] for b in BLOCKS.values()]
        assert len(set(slugs)) == len(slugs)
        assert all(re.fullmatch(r"[a-z0-9-]+", s) for s in slugs)

    @pytest.mark.parametrize("geyser", [*TARGET_GEYSERS, "Steamboat"])
    def test_every_geyser_states_its_own_gaps(self, geyser):
        """The point of the page: no card gets to look flawless."""
        b = BLOCKS[geyser]
        assert b["summary"] and b["why"]
        assert b["gaps"] and all(isinstance(x, str) and len(x) > 40 for x in b["gaps"])

    def test_steamboat_promises_nothing(self):
        b = BLOCKS["Steamboat"]
        assert b["mode"] == "context"
        assert b["served"] is None and not b["leaderboard"]

    def test_phase_limited_geysers_carry_their_window(self):
        for name in ("Lone Star", "Till"):
            assert BLOCKS[name]["mode"] == "phase-limited"
            assert BLOCKS[name]["phase_window_cycles"] > 0

    def test_miscalibration_is_surfaced_not_buried(self):
        """Castle's 50% band covers 61%; the page has to say so itself."""
        assert any("50%" in g and "wrong shape" in g for g in BLOCKS["Castle"]["gaps"])

    def test_project_level_gaps_are_actionable(self):
        assert len(OPEN_GAPS) >= 5
        assert all(g["title"] and g["who"] and len(g["text"]) > 80 for g in OPEN_GAPS)

    def test_honest_coverage_carries_its_comparison(self):
        b = BLOCKS["Riverside"]["honest"]
        assert b["cov90"] < b["cov90_filtered"]
        assert b["cost_points"] > 0


class TestEndpoints:
    def test_method_endpoint(self):
        d = client.get("/api/method").json()
        assert {"overview", "sections", "models", "geysers", "open_gaps", "links"} <= set(d)
        assert d["calibration"]["generated"]
        assert len(d["geysers"]) == len(TARGET_GEYSERS) + 1

    def test_single_geyser(self):
        d = client.get("/api/method/castle").json()
        assert d["geyser"]["geyser"] == "Castle"
        assert d["geyser"]["served"]["model"] == "minor_conditional"

    def test_steamboat_resolves_though_it_is_not_a_target(self):
        d = client.get("/api/method/steamboat").json()
        assert d["geyser"]["mode"] == "context"

    def test_unknown_geyser_404s(self):
        assert client.get("/api/method/Nonesuch").status_code == 404

    def test_needs_no_database(self, monkeypatch):
        """It must answer while the snapshot is still downloading.

        The method document is a read of a committed artifact, so a container
        that has not yet pulled its 200 MB DuckDB file can still explain itself.
        """
        import duckdb

        def refuse(*a, **kw):
            raise AssertionError("the method document must not touch the database")

        monkeypatch.setattr(duckdb, "connect", refuse)
        assert client.get("/api/method").status_code == 200

    def test_method_page_is_served(self):
        r = client.get("/method")
        assert r.status_code == 200
        assert "/api/method" in r.text

    def test_dashboard_links_to_it(self):
        assert 'href="/method' in client.get("/").text
