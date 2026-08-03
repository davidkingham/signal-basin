"""Synthetic fixtures.

The database is built at import time, before any `geyser_ai` module is loaded,
because `DB_PATH` is resolved from the environment when the package is imported.
Nothing here touches the network or the 27 MB archive: the tests must run
offline, and a fixture you can reason about beats a slice of real data you
cannot.

Crucially the synthetic rows go through the *real* ingest SQL, so the validity
filter under test is the one that ships.
"""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path

import duckdb
import numpy as np
import pytest

_TMP = Path(tempfile.mkdtemp(prefix="geyser-ai-tests-"))
_DB = _TMP / "test.duckdb"
os.environ["GEYSER_AI_DB"] = str(_DB)
os.environ["GEYSER_AI_DATA_DIR"] = str(_TMP)

RAW_COLUMNS = [
    "eruptionID",
    "geyser",
    "eruption_time_epoch",
    "has_seconds",
    "exact",
    "ns",
    "ie",
    "E",
    "A",
    "wc",
    "ini",
    "maj",
    "min",
    "q",
    "duration",
    "duration_seconds",
    "duration_resolution",
    "duration_modifier",
    "entrant",
    "observer",
    "eruption_comment",
    "time_updated",
    "time_entered",
    "associated_primaryID",
    "other_comments",
]

# Roughly true-to-life medians (minutes) so model fits behave realistically.
GEYSER_SPECS = {
    "Old Faithful": (92.0, 0.10),
    "Grand": (410.0, 0.20),
    "Daisy": (140.0, 0.12),
    "Riverside": (378.0, 0.10),
    "Castle": (820.0, 0.18),
    "Great Fountain": (690.0, 0.14),
    "Beehive": (1100.0, 0.22),
}

# Anchored just before "now" so the recent-eruptions endpoint and data-age
# logic have something to work with. Intervals are seeded, so only the absolute
# offset moves between runs -- the series itself stays deterministic.
END_EPOCH = int(time.time()) - 600
_rng = np.random.default_rng(20260803)


def _series(median: float, log_sd: float, n: int, end: int) -> np.ndarray:
    """Lognormal intervals walked backwards from `end`, returned ascending."""
    iv = _rng.lognormal(np.log(median), log_sd, n) * 60.0
    return (end - np.cumsum(iv)[::-1]).astype(np.int64)


def _rows(geyser: str, epochs, start_id: int, **flags) -> list[tuple]:
    out = []
    for k, ep in enumerate(epochs):
        r = {c: None for c in RAW_COLUMNS}
        eid = start_id + k
        r["eruptionID"] = str(eid)
        r["geyser"] = geyser
        r["eruption_time_epoch"] = str(int(ep))
        r["associated_primaryID"] = str(eid)  # self-referential = primary
        for c in ("has_seconds", "exact", "ns", "ie", "E", "A", "wc", "ini", "maj", "min", "q"):
            r[c] = "0"
        for c, v in flags.items():
            r[c] = v
        out.append(tuple(r[c] for c in RAW_COLUMNS))
    return out


def _build() -> None:
    from geyser_ai.ingest import _build_eruptions_view, _build_intervals

    con = duckdb.connect(str(_DB))
    cols = ", ".join(f'"{c}" VARCHAR' for c in RAW_COLUMNS)
    con.execute(f"CREATE TABLE eruptions_raw ({cols})")

    rows: list[tuple] = []
    next_id = 1
    for name, (med, sd) in GEYSER_SPECS.items():
        n = 1400
        ep = _series(med, sd, n, END_EPOCH)

        # Drop ~8% of eruptions to mimic observation gaps. The resulting doubles
        # are exactly the harmonics the validity filter has to reject.
        keep = _rng.random(n) > 0.08
        keep[:50] = True
        ep_kept = ep[keep]
        rows += _rows(name, ep_kept, next_id)
        next_id += n + 10

        if name == "Beehive":
            # Beehive's Indicator, ~12 min before each Beehive, present 80% of
            # the time -- enough to exercise both regimes of the mixture.
            has = _rng.random(len(ep_kept)) < 0.8
            lead = _rng.normal(11.9, 4.8, len(ep_kept)).clip(2, 30)
            ind = (ep_kept[has] - lead[has] * 60).astype(np.int64)
            rows += _rows("Beehive's Indicator", ind, next_id)
            next_id += len(ind) + 10

        if name == "Grand":
            # Turban on its own ~19 min cycle across the same span.
            t0, t1 = int(ep_kept[0]), int(ep_kept[-1])
            tur = np.arange(t0, t1, 19 * 60) + _rng.normal(0, 120, len(range(t0, t1, 19 * 60)))
            rows += _rows("Turban", tur.astype(np.int64), next_id)
            next_id += len(tur) + 10

    con.executemany(
        f"INSERT INTO eruptions_raw VALUES ({', '.join(['?'] * len(RAW_COLUMNS))})", rows
    )
    _build_eruptions_view(con)
    _build_intervals(con)
    con.close()


_build()


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    """No test may touch the GeyserTimes API.

    `service.get_predictions` syncs by default, which is right in production and
    unacceptable in a test suite, so it is stubbed for every test. Tests that
    exercise the sync itself mock `httpx` directly instead.
    """
    import geyser_ai.service as svc

    monkeypatch.setattr(svc, "sync_recent", lambda **kw: {"cached": True, "n_last": 0})
