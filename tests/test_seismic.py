"""The Steamboat seismic watch: every mode, offline.

The detector constants were set by 212 swept days of real YNM data (see
docs/findings/seismic.md); these tests pin the machinery around them --
season policy, fire logic, veto, refractory, state, and the guarantee that
the watch degrades to an honest status rather than ever breaking the card.
All waveform access is via injected fetchers; nothing touches the network.
"""

from __future__ import annotations

import datetime as dt

from geyser_ai.seismic import (
    FIRE_FLOOR,
    watch_mode,
    watch_tick,
)

UTC = dt.UTC


def mk_now(month: int, day: int, hour_utc: int) -> dt.datetime:
    return dt.datetime(2026, month, day, hour_utc, 30, tzinfo=UTC)


def series(now: dt.datetime, minutes: int, level: float) -> list[tuple[int, float]]:
    """Flat minute-RMS series ending at `now`."""
    end = int(now.timestamp() // 60)
    return [(m, level) for m in range(end - minutes + 1, end + 1)]


def fetcher(data):
    """An injected fetch that returns `data` regardless of window."""

    def fetch(station, start, end):
        return data

    return fetch


class TestSeasonPolicy:
    def test_oversnow_season_is_suspended_at_every_hour(self):
        for hour in (0, 6, 12, 18):
            mode, reason = watch_mode(mk_now(1, 20, hour))
            assert mode == "suspended_winter"
            assert "winter operations" in reason

    def test_late_march_boundary(self):
        assert watch_mode(mk_now(3, 20, 12))[0] == "suspended_winter"
        # Mar 22 local: use an afternoon UTC hour so the local date is Mar 22
        assert watch_mode(mk_now(3, 22, 20))[0] == "watching"

    def test_summer_day_pauses_and_night_watches(self):
        # 18:00 UTC July = 12:00 MDT -> paused
        assert watch_mode(mk_now(7, 10, 18))[0] == "paused_daytime"
        # 06:00 UTC July = 00:00 MDT -> watching
        assert watch_mode(mk_now(7, 10, 6))[0] == "watching"

    def test_shoulder_seasons_watch_all_hours(self):
        for hour in (0, 6, 12, 18):
            assert watch_mode(mk_now(10, 20, hour))[0] == "watching", "October"
            assert watch_mode(mk_now(4, 15, hour))[0] == "watching", "April"


class TestFireLogic:
    def now(self):
        return mk_now(10, 20, 12)  # October: watching at all hours

    def test_quiet_series_stays_quiet(self):
        now = self.now()
        st = {"rms": series(now, 500, 900.0), "detections": [], "last_fire_minute": 0}
        out = watch_tick(now=now, fetch=fetcher(None), state=st, persist=False)
        assert out["status"] == "watching"
        assert out["recent_detection"] is None

    def _fired_state(self, now, veto_result=(False, "YNR flat at 1.0x")):
        """Quiet baseline, then a sustained eruption-level rise."""
        end = int(now.timestamp() // 60)
        rms = [(m, 900.0) for m in range(end - 500, end - 20)]
        rms += [(m, 9000.0) for m in range(end - 20, end + 1)]
        st = {"rms": rms, "detections": [], "last_fire_minute": 0}
        return watch_tick(
            now=now,
            fetch=fetcher(None),
            veto=lambda m: veto_result,
            state=st,
            persist=False,
        ), st

    def test_sustained_rise_from_quiet_fires(self):
        out, st = self._fired_state(self.now())
        d = out["recent_detection"]
        assert d is not None and d["peak_rms"] >= FIRE_FLOOR
        assert "consistent" not in str(d), "the wording belongs to the card, not the data"
        assert st["detections"][0]["vetoed"] is False

    def test_regional_veto_suppresses_the_detection(self):
        out, st = self._fired_state(self.now(), veto_result=(True, "YNR lifted 140x"))
        assert out["recent_detection"] is None, "a vetoed fire must not surface"
        assert st["detections"][0]["vetoed"] is True, "but it is recorded for audit"

    def test_short_burst_is_a_minor_not_an_eruption(self):
        now = self.now()
        end = int(now.timestamp() // 60)
        rms = [(m, 900.0) for m in range(end - 500, end - 4)]
        rms += [(m, 9000.0) for m in range(end - 4, end + 1)]  # 5-minute burst
        st = {"rms": rms, "detections": [], "last_fire_minute": 0}
        out = watch_tick(now=now, fetch=fetcher(None), state=st, persist=False)
        assert out["recent_detection"] is None

    def test_elevated_baseline_gates_the_detector(self):
        """Steam phase or noisy conditions: no quiet baseline, no fire claim."""
        now = self.now()
        st = {"rms": series(now, 500, 5000.0), "detections": [], "last_fire_minute": 0}
        out = watch_tick(now=now, fetch=fetcher(None), state=st, persist=False)
        assert out["recent_detection"] is None

    def test_refractory_prevents_refires(self):
        now = self.now()
        out, st = self._fired_state(now)
        n = len(st["detections"])
        again = watch_tick(
            now=now + dt.timedelta(minutes=5),
            fetch=fetcher(None),
            veto=lambda m: (False, ""),
            state=st,
            persist=False,
        )
        assert len(st["detections"]) == n, "one eruption, one detection"
        assert again["recent_detection"] is not None


class TestHonestDegradation:
    def test_dark_station_reports_no_data(self):
        now = mk_now(10, 20, 12)
        st = {"rms": [], "detections": [], "last_fire_minute": 0}
        out = watch_tick(now=now, fetch=fetcher(None), state=st, persist=False)
        assert out["status"] == "no_data"
        assert "offline" in out["reason"]

    def test_suspended_mode_skips_fetching_entirely(self):
        calls = []

        def counting_fetch(*a):
            calls.append(a)
            return None

        st = {"rms": [], "detections": [], "last_fire_minute": 0}
        out = watch_tick(now=mk_now(1, 20, 12), fetch=counting_fetch, state=st, persist=False)
        assert out["status"] == "suspended"
        assert calls == [], "no point polling a station the season has defeated"

    def test_lagging_feed_is_not_watching(self):
        now = mk_now(10, 20, 12)
        stale = series(now - dt.timedelta(minutes=45), 400, 900.0)
        st = {"rms": stale, "detections": [], "last_fire_minute": 0}
        out = watch_tick(now=now, fetch=fetcher(None), state=st, persist=False)
        assert out["status"] == "no_data"
