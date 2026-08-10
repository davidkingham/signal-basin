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

import itertools
import os
import tempfile
import time
from pathlib import Path

import duckdb
import numpy as np
import pytest

_TMP = Path(tempfile.mkdtemp(prefix="geyser-ai-tests-"))
_LEDGER_SEQ = itertools.count()
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
    "Fountain": (305.0, 0.21),
    "Artemisia": (1320.0, 0.21),
    "Little Squirt": (3500.0, 0.24),
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

    # A geyser with a true minor mode, shaped like the real Old Faithful: ~30%
    # of eruptions are minors, and the interval FOLLOWING a minor comes from a
    # different, shorter distribution (70 vs 102 min medians). The final
    # eruption is forced to be a minor so serving-path tests can assert that
    # the post-minor branch is what actually gets served -- a path that
    # discards the branch lands near the pooled ~92, over 20 minutes away.
    # No observation gaps: the point is the branch structure, not the filter.
    n = 1200
    is_minor = _rng.random(n) < 0.30
    is_minor[-1] = True
    gap_min = np.where(
        is_minor,
        _rng.lognormal(np.log(70.0), 0.06, n),
        _rng.lognormal(np.log(102.0), 0.06, n),
    )
    ep = np.concatenate([[0.0], np.cumsum(gap_min[:-1] * 60.0)])
    ep = (ep - ep[-1] + END_EPOCH).astype(np.int64)
    rows += _rows("Plume", ep[~is_minor], next_id)
    next_id += int((~is_minor).sum()) + 10
    rows += _rows("Plume", ep[is_minor], next_id, **{"min": "1"})
    next_id += int(is_minor.sum()) + 10

    # Lion erupts in SERIES: an initial (flagged `ini`), one to three more at
    # ~83-minute spacing, then ~10 hours of quiet before the next initial. The
    # fixture mirrors that structure so the series-conditional model and the
    # ingest filter's second-mode band are both exercised by the real SQL --
    # a filter without the second-mode band deletes every series gap here.
    ep_l: list[float] = []
    ini_l: list[bool] = []
    t = 0.0
    while len(ep_l) < 1300:
        ep_l.append(t)
        ini_l.append(True)
        for _ in range(int(_rng.integers(1, 4))):
            t += _rng.lognormal(np.log(83.0), 0.08) * 60.0
            ep_l.append(t)
            ini_l.append(False)
        t += _rng.lognormal(np.log(600.0), 0.25) * 60.0
    ep_lion = np.asarray(ep_l)
    ep_lion = (ep_lion - ep_lion[-1] + END_EPOCH).astype(np.int64)
    ini_arr = np.asarray(ini_l)
    rows += _rows("Lion", ep_lion[~ini_arr], next_id)
    next_id += int((~ini_arr).sum()) + 10
    rows += _rows("Lion", ep_lion[ini_arr], next_id, ini="1")
    next_id += int(ini_arr.sum()) + 10

    # Lone Star: a ~186-minute cycle whose MINORS are precursors (~37 min
    # before the major of the same cycle) rather than cycle events -- the
    # ingest chain must exclude them or the intervals collapse into 37/150
    # phantom modes. The last eruption sits 20 hours back, so the serving
    # path's default state is the PLANNING card; sparse-singles behaviour is
    # exercised by dropping half the majors.
    n = 700
    ivs = _rng.lognormal(np.log(186.0), 0.13, n) * 60.0
    maj = END_EPOCH - 20 * 3600 - np.cumsum(ivs)[::-1]
    keep = _rng.random(n) > 0.5
    keep[-60:] = _rng.random(60) > 0.3  # recent era better-logged, like reality
    maj_kept = maj[keep].astype(np.int64)
    rows += _rows("Lone Star", maj_kept, next_id, maj="1")
    next_id += n + 10
    has_minor = _rng.random(len(maj_kept)) < 0.35
    minors = (maj_kept[has_minor] - _rng.normal(37, 6, has_minor.sum()) * 60).astype(np.int64)
    rows += _rows("Lone Star", minors, next_id, **{"min": "1"})
    next_id += len(minors) + 10

    # Till: like Lone Star but with AFTERPLAY minors (~1 h after the major)
    # instead of precursors, and a 12-hour cycle. Last major 5 cycles back:
    # inside Till's 8-cycle phase window, so its default serving state is
    # LIVE -- the opposite pole from the Lone Star fixture.
    n = 500
    ivs = _rng.lognormal(np.log(729.0), 0.08, n) * 60.0
    maj = END_EPOCH - 5 * 729 * 60 - np.cumsum(ivs)[::-1]
    keep = _rng.random(n) > 0.5
    maj_kept = maj[keep].astype(np.int64)
    rows += _rows("Till", maj_kept, next_id, maj="1")
    next_id += n + 10
    has_after = _rng.random(len(maj_kept)) < 0.5
    afters = (maj_kept[has_after] + _rng.normal(60, 15, has_after.sum()) * 60).astype(np.int64)
    rows += _rows("Till", afters, next_id, **{"min": "1"})
    next_id += len(afters) + 10

    # Steamboat: ten majors, weeks-to-months apart, newest ~30 days back --
    # enough for the context card's days-since and recent-interval numbers.
    gaps_d = _rng.uniform(40, 90, 9)
    sb = END_EPOCH - 30 * 86400 - np.concatenate([[0.0], np.cumsum(gaps_d)]) * 86400
    rows += _rows("Steamboat", sb[::-1].astype(np.int64), next_id, maj="1")
    next_id += len(sb) + 10

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
    import geyser_ai.ledger as ledger_mod
    import geyser_ai.service as svc

    monkeypatch.setattr(svc, "sync_recent", lambda **kw: {"cached": True, "n_last": 0})
    # The seismic watch would poll EarthScope; offline it reports a dark
    # station, which is exactly its honest degraded mode.
    import geyser_ai.seismic as seismic_mod

    monkeypatch.setattr(seismic_mod, "fetch_minute_rms", lambda *a, **k: None)
    # The scoreboard reaches for `predictions_latest` on the same cadence, so it
    # gets the same treatment. Tests that exercise the feed mock httpx directly.
    monkeypatch.setattr(svc, "fetch_predictions", lambda *a, **kw: [])

    # Every test gets its own ledger, in its own file, so scoring tests cannot
    # leak state into each other or into the developer's real ledger.
    ledger_path = _TMP / f"ledger-{next(_LEDGER_SEQ)}.json"
    monkeypatch.setattr(ledger_mod, "LEDGER_URL", "")
    monkeypatch.setattr(ledger_mod, "LEDGER_PATH", ledger_path)
    ledger_mod.reset_ledger(ledger_mod.FileLedgerStore(ledger_path))
