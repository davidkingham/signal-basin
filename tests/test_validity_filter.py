"""The two-stage validity filter.

This is the highest-value thing to test in the project: it has been wrong three
separate times, and each time it moved the headline metrics more than any model
change did. The tests below encode the three failures so they cannot come back.
"""

from __future__ import annotations

import duckdb
import numpy as np
import pytest
from conftest import RAW_COLUMNS, _rows

from geyser_ai.ingest import (
    INTERVAL_MAX_MULT,
    INTERVAL_MIN_MULT,
    _build_eruptions_view,
    _build_intervals,
)


def build(epochs: np.ndarray, geyser: str = "Test", tmp_path=None) -> duckdb.DuckDBPyConnection:
    """Run a bare eruption series through the real ingest SQL."""
    con = duckdb.connect(":memory:")
    cols = ", ".join(f'"{c}" VARCHAR' for c in RAW_COLUMNS)
    con.execute(f"CREATE TABLE eruptions_raw ({cols})")
    con.executemany(
        f"INSERT INTO eruptions_raw VALUES ({', '.join(['?'] * len(RAW_COLUMNS))})",
        _rows(geyser, epochs, 1),
    )
    _build_eruptions_view(con)
    _build_intervals(con)
    return con


def intervals(con) -> np.ndarray:
    return con.execute(
        "SELECT interval_min, is_valid, med_interval FROM intervals ORDER BY epoch"
    ).df()


def clean_series(median_min: float, n: int, start: int = 1_700_000_000) -> np.ndarray:
    """A perfectly regular series -- the control case."""
    return start + np.arange(n, dtype=np.int64) * int(median_min * 60)


class TestHarmonics:
    def test_doubles_are_rejected(self):
        """A missed eruption makes a 2x gap. That is the bug that cost 20-87% CRPS."""
        med = 100.0
        ep = clean_series(med, 900)
        # drop every 10th eruption from the middle onwards -> 2x gaps
        drop = np.zeros(len(ep), dtype=bool)
        drop[300::10] = True
        con = build(ep[~drop])
        df = intervals(con)
        doubles = df[df.interval_min > 1.9 * med]
        singles = df[df.interval_min < 1.2 * med]
        assert len(doubles) > 30, "fixture should contain harmonics"
        assert not doubles.is_valid.any(), "2x harmonics must be rejected"
        assert singles.is_valid.mean() > 0.98, "genuine intervals must survive"

    def test_triples_are_rejected(self):
        med = 100.0
        ep = clean_series(med, 600)
        drop = np.zeros(len(ep), dtype=bool)
        drop[200::12] = True
        drop[201::12] = True  # two in a row -> 3x gap
        con = build(ep[~drop])
        df = intervals(con)
        triples = df[df.interval_min > 2.8 * med]
        assert len(triples) > 10
        assert not triples.is_valid.any()

    def test_duplicate_entries_rejected(self):
        """Two observers logging the same eruption minutes apart is not an interval."""
        med = 100.0
        ep = list(clean_series(med, 500))
        for i in range(200, 400, 5):
            ep.append(ep[i] + 400)  # +6.7 min, well past the 60 s dedup
        con = build(np.array(sorted(ep), dtype=np.int64))
        df = intervals(con)
        tiny = df[df.interval_min < 0.4 * med]
        assert len(tiny) > 20
        assert not tiny.is_valid.any(), "sub-harmonic duplicates must be rejected"


class TestDrift:
    def test_drifting_baseline_does_not_reject_good_intervals(self):
        """Daisy drifted 142 -> 111 min. A global median rejected honest data.

        The local baseline must track the drift, so a clean but drifting series
        should stay almost entirely valid.
        """
        n = 1200
        med = np.linspace(150.0, 100.0, n)
        ep = (1_700_000_000 + np.cumsum(med * 60)).astype(np.int64)
        df = intervals(build(ep))
        assert df.is_valid.mean() > 0.97, "drift alone must not invalidate intervals"

    def test_modern_doubles_rejected_despite_old_era_baseline(self):
        """The exact Daisy failure: doubles of the NEW interval under the OLD median.

        Early era 150 min, late era 100 min. A late double is 200 min, which sits
        under 1.75 x 150 = 262 and so survives a global median. The local
        baseline must still catch it.
        """
        n_old, n_new = 700, 700
        ep = [1_700_000_000]
        for _ in range(n_old):
            ep.append(ep[-1] + 150 * 60)
        for i in range(n_new):
            step = 100 * 60
            if i > 100 and i % 9 == 0:
                step *= 2  # modern double
            ep.append(ep[-1] + step)
        df = intervals(build(np.array(ep, dtype=np.int64)))
        late = df.iloc[n_old + 150 :]
        modern_doubles = late[(late.interval_min > 190) & (late.interval_min < 210)]
        assert len(modern_doubles) > 20, "fixture should contain modern doubles"
        assert not modern_doubles.is_valid.any(), (
            "doubles of the modern interval must be rejected even though they fit "
            "under a threshold set by the older, longer era"
        )


class TestSelfValidatingContamination:
    def test_majority_doubles_do_not_capture_the_baseline(self):
        """Great Fountain's failure: where most gaps are doubles, a local MEDIAN
        drifts up to the doubled value and then blesses the contamination. The
        low-quantile anchor exists to stop that."""
        med = 100.0
        ep = [1_700_000_000]
        for i in range(1200):
            # 60% of gaps in the middle stretch are doubles
            double = 400 < i < 900 and (i % 10) < 6
            ep.append(ep[-1] + int(med * 60 * (2 if double else 1)))
        df = intervals(build(np.array(ep, dtype=np.int64)))
        mid = df.iloc[450:880]
        doubles = mid[mid.interval_min > 1.9 * med]
        singles = mid[mid.interval_min < 1.2 * med]
        assert len(doubles) > 100 and len(singles) > 50
        assert doubles.is_valid.mean() < 0.05, (
            "doubles must stay rejected even when they are the majority of gaps"
        )
        assert singles.is_valid.mean() > 0.9, "the true mode must remain valid"


class TestBounds:
    def test_thresholds_are_the_documented_ones(self):
        assert INTERVAL_MIN_MULT == 0.5
        assert INTERVAL_MAX_MULT == 1.75

    @pytest.mark.parametrize(
        "factor,expected", [(1.0, True), (1.7, True), (1.8, False), (0.6, True), (0.4, False)]
    )
    def test_single_outlier_at_boundary(self, factor, expected):
        med = 100.0
        ep = list(clean_series(med, 800))
        idx = 400
        shift = int(med * 60 * (factor - 1))
        for i in range(idx, len(ep)):
            ep[i] += shift
        df = intervals(build(np.array(ep, dtype=np.int64)))
        row = df.iloc[idx - 1]
        assert bool(row.is_valid) is expected, f"{factor}x median -> expected valid={expected}"


class TestMinorRegime:
    """Castle's fourth failure: one baseline for two physically different processes.

    An eruption that fails to reach the steam phase is logged as a MINOR, and
    the interval that follows it is a genuinely shorter process. Pooled, the
    baseline tracks the long post-major mode, the lower bound lands above the
    entire short mode, and every real post-minor interval is deleted as if it
    were two observers logging the same eruption.
    """

    def build_two_mode(self, n: int = 1200, long_min: float = 1000.0, short_min: float = 350.0):
        """A geyser where every fourth eruption is a minor followed by a short interval."""
        con = duckdb.connect(":memory:")
        cols = ", ".join(f'"{c}" VARCHAR' for c in RAW_COLUMNS)
        con.execute(f"CREATE TABLE eruptions_raw ({cols})")

        epochs, minors = [], []
        t = 1_700_000_000
        for i in range(n):
            epochs.append(t)
            is_minor = i % 4 == 3
            minors.append(is_minor)
            # The interval AFTER a minor is the short one.
            t += int((short_min if is_minor else long_min) * 60)

        rows = []
        for epoch, is_minor in zip(epochs, minors, strict=True):
            batch = _rows("Test", np.array([epoch], dtype=np.int64), len(rows) + 1)
            row = list(batch[0])
            row[RAW_COLUMNS.index("min")] = "1" if is_minor else "0"
            row[RAW_COLUMNS.index("maj")] = "0" if is_minor else "1"
            rows.append(tuple(row))

        con.executemany(
            f"INSERT INTO eruptions_raw VALUES ({', '.join(['?'] * len(RAW_COLUMNS))})", rows
        )
        _build_eruptions_view(con)
        _build_intervals(con)
        return con

    def frame(self, con):
        return con.execute(
            "SELECT interval_min, is_valid, prev_minor, med_interval FROM intervals ORDER BY epoch"
        ).df()

    def test_short_post_minor_intervals_survive(self):
        """The whole point: these are real eruptions, not duplicate entries."""
        df = self.frame(self.build_two_mode())
        post_minor = df[df.prev_minor.astype(bool)]

        # Comfortably over MIN_REGIME_ROWS, or the guard correctly declines to split.
        assert len(post_minor) > 250, "fixture must clear the regime row-count guard"
        assert post_minor.is_valid.mean() > 0.9, (
            "the short mode is the physics, not an artifact -- it must not be filtered away"
        )

    def test_each_regime_gets_its_own_baseline(self):
        df = self.frame(self.build_two_mode())
        med_minor = df[df.prev_minor.astype(bool)].med_interval.median()
        med_major = df[~df.prev_minor.astype(bool)].med_interval.median()

        assert med_minor == pytest.approx(350.0, rel=0.1)
        assert med_major == pytest.approx(1000.0, rel=0.1)

    def test_the_long_regime_is_unharmed(self):
        df = self.frame(self.build_two_mode())
        post_major = df[~df.prev_minor.astype(bool)]
        assert post_major.is_valid.mean() > 0.9

    def test_doubles_are_still_rejected_within_a_regime(self):
        """Splitting by regime must not cost us the harmonic filtering."""
        con = self.build_two_mode()
        df = self.frame(con)
        # A post-major interval at 2x its own regime's baseline is a missed eruption.
        assert not df[
            (~df.prev_minor.astype(bool)) & (df.interval_min > 1.9 * 1000.0)
        ].is_valid.any()

    def test_a_geyser_without_minors_is_untouched(self):
        """`prev_minor` is false for every row, so the partition must be a no-op."""
        plain = clean_series(90.0, 700)
        pooled = intervals(build(plain))
        assert pooled.is_valid.mean() > 0.95
        assert pooled.med_interval.median() == pytest.approx(90.0, rel=0.02)

    def test_a_handful_of_stray_minor_flags_does_not_earn_a_baseline(self):
        """Below the row-count guard the regime split would be fitting noise."""
        from geyser_ai.ingest import MIN_REGIME_ROWS

        assert MIN_REGIME_ROWS >= 100
