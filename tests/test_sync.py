"""The GeyserTimes REST sync. Fully mocked -- no test may hit their server.

The politeness properties here are a promise made to a nonprofit in the README,
so they are tested rather than trusted: one request per sync, a cache TTL, an
identifying User-Agent, and no crash when the network fails.
"""

from __future__ import annotations

import time

import httpx
import pytest

from geyser_ai import sync as sync_mod
from geyser_ai.config import USER_AGENT


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("boom", request=None, response=None)


def entry(eid: int, geyser: str, epoch: int, **over):
    e = {
        "eruptionID": str(eid),
        "geyser": geyser,
        "time": str(epoch),
        "primaryID": str(eid),
        "q": "0",
        "exact": "1",
        "ns": "0",
        "ie": "0",
        "E": "0",
        "A": "0",
        "wc": "0",
        "ini": "0",
        "maj": "0",
        "min": "0",
        "hasSeconds": "0",
        "durationSec": None,
        "entrant": "tester",
        "observer": "tester",
        "comment": "",
    }
    e.update(over)
    return e


@pytest.fixture
def calls(monkeypatch):
    """Record every outbound request and answer it locally."""
    seen = []

    def fake_get(url, **kw):
        seen.append({"url": url, "headers": kw.get("headers", {})})
        now = int(time.time())
        return FakeResponse(
            {
                "status": "success",
                "entries": [
                    entry(90001, "Old Faithful", now - 600),
                    entry(90002, "Daisy", now - 1200),
                    entry(90003, "Beehive", now - 300, wc="1"),
                ],
            }
        )

    monkeypatch.setattr(sync_mod.httpx, "get", fake_get)
    sync_mod._state.update(last_attempt=0.0, last_success=None, n_last=0, error=None)
    return seen


class TestSync:
    def test_stores_entries_and_reports_them(self, calls):
        res = sync_mod.sync_recent(force=True)
        assert res["error"] is None
        assert res["n_last"] == 3
        assert res["n_total"] >= 3

    def test_makes_exactly_one_request(self, calls):
        sync_mod.sync_recent(force=True)
        assert len(calls) == 1, "a sync must never become a crawl"

    def test_sends_an_identifying_user_agent(self, calls):
        sync_mod.sync_recent(force=True)
        ua = calls[0]["headers"].get("User-Agent", "")
        assert ua == USER_AGENT
        assert "signal-basin" in ua
        assert "Mozilla" not in ua, "must not impersonate a browser"

    def test_lookback_is_a_path_segment(self, calls):
        sync_mod.sync_recent(force=True)
        url = calls[0]["url"]
        assert "/entries_recent/" in url
        tail = url.rsplit("/", 1)[1]
        assert tail.isdigit() and int(tail) >= sync_mod.MIN_LOOKBACK_MIN

    def test_lookback_is_capped(self, calls, monkeypatch):
        monkeypatch.setattr(sync_mod, "_archive_max_epoch", lambda con: 0)
        sync_mod.sync_recent(force=True)
        assert int(calls[0]["url"].rsplit("/", 1)[1]) <= sync_mod.MAX_LOOKBACK_MIN

    def test_cache_ttl_suppresses_a_second_request(self, calls):
        sync_mod.sync_recent(force=True)
        res = sync_mod.sync_recent(ttl=300)
        assert res["cached"] is True
        assert len(calls) == 1, "TTL must prevent a second call"
        assert res["seconds_until_refresh"] > 0

    def test_default_ttl_respects_the_stated_policy(self):
        assert sync_mod.TTL_SECONDS >= 300, "README promises >= 5 minutes"

    def test_network_failure_is_reported_not_raised(self, monkeypatch):
        def boom(url, **kw):
            raise httpx.ConnectError("no network")

        monkeypatch.setattr(sync_mod.httpx, "get", boom)
        sync_mod._state.update(last_attempt=0.0, error=None)
        res = sync_mod.sync_recent(force=True)
        assert res["error"] and "ConnectError" in res["error"]

    def test_api_error_status_is_reported(self, monkeypatch):
        monkeypatch.setattr(
            sync_mod.httpx, "get", lambda url, **kw: FakeResponse({"status": "failure"})
        )
        sync_mod._state.update(last_attempt=0.0, error=None)
        res = sync_mod.sync_recent(force=True)
        assert res["error"] and "failure" in res["error"]

    def test_secondary_and_questionable_entries_are_dropped(self, monkeypatch):
        now = int(time.time())
        payload = {
            "status": "success",
            "entries": [
                entry(95001, "Grand", now - 100),
                entry(95002, "Grand", now - 90, primaryID="95001"),  # duplicate observation
                entry(95003, "Grand", now - 80, q="1"),  # flagged questionable
            ],
        }
        monkeypatch.setattr(sync_mod.httpx, "get", lambda url, **kw: FakeResponse(payload))
        sync_mod._state.update(last_attempt=0.0, error=None)
        res = sync_mod.sync_recent(force=True)
        assert res["n_last"] == 1, "only the primary, non-questionable entry is kept"

    def test_repeated_sync_is_idempotent(self, calls):
        sync_mod.sync_recent(force=True)
        n1 = sync_mod.sync_status()["n_total"]
        sync_mod.sync_recent(force=True)
        assert sync_mod.sync_status()["n_total"] == n1, "re-syncing must not duplicate rows"
