"""Regression: the nowcast must see LIVE entries, not just the snapshot.

Caught in production 2026-08-10: a Beehive eruption was scored against the
18-hour base prediction even though its Indicator was logged in GeyserTimes
ten minutes before the eruption. load_eruption_epochs read only the frozen
archive table, so every live Indicator since deployment was invisible and
the flagship nowcast never fired outside of tests. The fixture here puts an
Indicator entry ONLY in recent_eruptions -- the live-sync table -- which is
exactly where a real one lives between snapshot publishes.
"""

from __future__ import annotations

import datetime as dt
import os

import duckdb

from geyser_ai.nowcast import load_eruption_epochs


def _insert_recent(geyser: str, epoch: int) -> None:
    from geyser_ai.sync import _ensure_table

    con = duckdb.connect(os.environ["GEYSER_AI_DB"])
    try:
        _ensure_table(con)  # the real schema, so later sync tests are unharmed
        con.execute(
            "INSERT OR REPLACE INTO recent_eruptions (eruption_id, geyser, epoch, ts_utc) "
            "VALUES (?, ?, ?, to_timestamp(?))",
            [99990001, geyser, epoch, epoch],
        )
    finally:
        con.close()


class TestNowcastSeesLiveEntries:
    def test_live_only_indicator_is_visible(self):
        now = int(dt.datetime.now(dt.UTC).timestamp())
        fresh = now - 240  # logged four minutes ago, exists ONLY in the sync table
        _insert_recent("Beehive's Indicator", fresh)
        try:
            epochs = load_eruption_epochs("Beehive's Indicator")
            assert len(epochs) > 0
            assert int(epochs[-1]) == fresh, (
                "the live Indicator entry must be the newest epoch the nowcast sees"
            )
        finally:
            con = duckdb.connect(os.environ["GEYSER_AI_DB"])
            con.execute("DELETE FROM recent_eruptions WHERE eruption_id = 99990001")
            con.close()
