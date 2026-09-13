"""The served training set must reach the live sync, not stop at the snapshot.

Caught 2026-09-13: production's newest training interval was 41 days old
because `intervals` is built only at ingest and `recent_eruptions` fed only the
anchor. Every "recent window" model was fitting the weeks before the snapshot.
These tests put entries ONLY in the sync table and assert the chain continues
through them with the archive's own rules.
"""

from __future__ import annotations

import os

import duckdb
import numpy as np
import pytest

from geyser_ai.backtest import load_all_intervals, load_intervals

_IDS = list(range(99980001, 99980010))


def _insert_recent(rows: list[tuple[int, str, int, bool]]) -> None:
    """(eruption_id, geyser, epoch, minor) rows, into the live-sync table only."""
    from geyser_ai.sync import _ensure_table

    con = duckdb.connect(os.environ["GEYSER_AI_DB"])
    try:
        _ensure_table(con)
        for eid, geyser, epoch, minor in rows:
            con.execute(
                "INSERT OR REPLACE INTO recent_eruptions "
                "(eruption_id, geyser, epoch, ts_utc, minor, questionable) "
                "VALUES (?, ?, ?, to_timestamp(?), ?, false)",
                [eid, geyser, epoch, epoch, minor],
            )
    finally:
        con.close()


@pytest.fixture
def clean_recent():
    yield
    con = duckdb.connect(os.environ["GEYSER_AI_DB"])
    try:
        con.execute(
            "DELETE FROM recent_eruptions WHERE eruption_id BETWEEN ? AND ?", [_IDS[0], _IDS[-1]]
        )
    finally:
        con.close()


def _archive_last(geyser: str) -> int:
    con = duckdb.connect(os.environ["GEYSER_AI_DB"], read_only=True)
    try:
        return int(
            con.execute("SELECT max(epoch) FROM eruptions WHERE geyser = ?", [geyser]).fetchone()[0]
        )
    finally:
        con.close()


class TestLiveChain:
    def test_live_entries_extend_the_training_set(self, clean_recent):
        archive = load_intervals("Daisy", extend_recent=False)
        last = _archive_last("Daisy")
        step = 140 * 60  # the fixture's Daisy median
        # three clean cycles, then a double (someone missed one), then a clean one
        epochs = [last + step, last + 2 * step, last + 3 * step, last + 5 * step, last + 6 * step]
        _insert_recent([(eid, "Daisy", ep, False) for eid, ep in zip(_IDS, epochs, strict=False)])

        live = load_intervals("Daisy")
        assert len(live) == len(archive) + 4, "the 2x gap is rejected, the four singles are kept"
        assert int(live["epoch"].iloc[-1]) == epochs[-1]
        assert list(live.columns) == list(archive.columns)
        tail = live.tail(4)
        assert np.allclose(tail["interval_min"], [140, 140, 140, 140])
        assert bool(tail["prev_webcam"].iloc[0]) is False  # flags default like the archive's

        everything = load_all_intervals("Daisy", extend_recent=True)
        rejected = everything[~everything["is_valid"].astype(bool)].tail(1)
        assert float(rejected["interval_min"].iloc[0]) == 280.0

    def test_the_backtest_view_is_untouched(self, clean_recent):
        last = _archive_last("Daisy")
        _insert_recent([(_IDS[0], "Daisy", last + 140 * 60, False)])
        assert int(load_intervals("Daisy", extend_recent=False)["epoch"].iloc[-1]) == last

    def test_a_minor_mode_geyser_keeps_its_regime_baseline(self, clean_recent):
        """Plume's post-minor mode is ~70 min against a ~102 post-major mode.

        A 70-minute gap after a live minor must validate against the post-minor
        baseline; against the pooled/post-major one it would fall under 0.5x
        and be deleted -- the Castle deletion, re-enacted on live data.
        """
        last = _archive_last("Plume")  # the fixture's final Plume eruption is a minor
        epochs = [last + 70 * 60, last + 172 * 60]
        _insert_recent([(_IDS[0], "Plume", epochs[0], False), (_IDS[1], "Plume", epochs[1], False)])
        live = load_intervals("Plume")
        tail = live.tail(2)
        assert [bool(v) for v in tail["prev_minor"]] == [True, False]
        assert np.allclose(tail["interval_min"], [70, 102])

    def test_a_precursor_minor_never_joins_the_chain(self, clean_recent):
        """Lone Star's minors precede the major; chained, they shatter the cycle."""
        last = _archive_last("Lone Star")
        _insert_recent(
            [
                (_IDS[0], "Lone Star", last + 150 * 60, True),
                (_IDS[1], "Lone Star", last + 186 * 60, False),
            ]
        )
        live = load_all_intervals("Lone Star", extend_recent=True)
        assert float(live["interval_min"].iloc[-1]) == 186.0
        assert 150.0 not in set(live["interval_min"].tail(2))
