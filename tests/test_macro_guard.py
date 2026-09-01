"""Tests for MacroGuard — economic calendar hard-freeze agent."""
from __future__ import annotations

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

from trader.agents.macro_guard import MacroGuard


def _cfg(enabled=True, pre=30, post=120, token="test_token"):
    return {"scouts": {"macro_guard": {
        "enabled": enabled,
        "pre_event_min": pre,
        "post_event_min": post,
        "min_impact": "high",
        "finnhub_token": token,
    }}}


def _event(offset_minutes: float, impact="high", country="US") -> dict:
    """Build a fake Finnhub event N minutes from now (ET = UTC-4)."""
    et_offset = timedelta(hours=4)
    event_utc = datetime.now(timezone.utc) + timedelta(minutes=offset_minutes)
    event_et = event_utc - et_offset
    return {
        "date": event_et.strftime("%Y-%m-%d"),
        "time": event_et.strftime("%H:%M:%S"),
        "event": "FOMC Rate Decision",
        "impact": impact,
        "country": country,
    }


def _mock_response(events: list[dict]):
    r = MagicMock()
    r.json.return_value = {"economicCalendar": events}
    return r


@pytest.fixture(autouse=True)
def _isolate_calendar_cache(tmp_path, monkeypatch):
    """The guard parks a good fetch on disk. Without this, one test's
    calendar leaks into the next one's and into the real data/ directory."""
    from trader.agents import macro_guard as mg
    monkeypatch.setattr(mg, "_CACHE_PATH", tmp_path / "macro_calendar.json")


class TestMacroGuardDisabled:
    def test_disabled_always_clears(self):
        mg = MacroGuard(_cfg(enabled=False))
        result = mg.check()
        assert result["active"] is False
        assert result["why"] == "disabled"


class TestMacroGuardNoToken:
    def test_no_token_clears(self):
        mg = MacroGuard(_cfg(token=""))
        result = mg.check()
        assert result["active"] is False


class TestMacroGuardPreWindow:
    def test_event_within_pre_window_freezes(self):
        mg = MacroGuard(_cfg(pre=30, post=120))
        with patch("requests.get", return_value=_mock_response([_event(+15)])):
            result = mg.check()
        assert result["active"] is True
        assert "FOMC" in result["event"]

    def test_event_outside_pre_window_clears(self):
        mg = MacroGuard(_cfg(pre=30))
        with patch("requests.get", return_value=_mock_response([_event(+45)])):
            result = mg.check()
        assert result["active"] is False

    def test_event_within_post_window_freezes(self):
        mg = MacroGuard(_cfg(post=120))
        with patch("requests.get", return_value=_mock_response([_event(-60)])):
            result = mg.check()
        assert result["active"] is True

    def test_event_outside_post_window_clears(self):
        mg = MacroGuard(_cfg(post=120))
        with patch("requests.get", return_value=_mock_response([_event(-150)])):
            result = mg.check()
        assert result["active"] is False


class TestMacroGuardFiltering:
    def test_non_us_events_ignored(self):
        mg = MacroGuard(_cfg())
        with patch("requests.get",
                   return_value=_mock_response([_event(+10, country="EU")])):
            result = mg.check()
        assert result["active"] is False

    def test_low_impact_events_ignored(self):
        mg = MacroGuard(_cfg())
        with patch("requests.get",
                   return_value=_mock_response([_event(+10, impact="low")])):
            result = mg.check()
        assert result["active"] is False


class TestMacroGuardCaching:
    def test_second_call_uses_cache(self):
        mg = MacroGuard(_cfg())
        with patch("requests.get",
                   return_value=_mock_response([_event(+10)])) as mock_get:
            mg.check()
            mg.check()
        assert mock_get.call_count == 1  # fetched once, cached on second call

    def test_cache_expires_after_refresh_seconds(self):
        mg = MacroGuard(_cfg())
        mg._refresh_s = 0   # force state cache expiry
        mg._cal_ttl = 0     # force calendar cache expiry
        with patch("requests.get",
                   return_value=_mock_response([_event(+10)])) as mock_get:
            mg.check()
            mg.check()
        assert mock_get.call_count == 2


class TestMacroGuardFetchError:
    def test_fetch_error_is_fail_open(self):
        """Network errors should NOT trigger a freeze (fail-open)."""
        mg = MacroGuard(_cfg())
        with patch("requests.get", side_effect=Exception("timeout")):
            result = mg.check()
        assert result["active"] is False


# ── a key that cannot reach the calendar must say so ──────────────────────
class _Resp403:
    """Finnhub's economic calendar is a paid endpoint; a free key 403s."""
    status_code = 403
    url = ("https://finnhub.io/api/v1/calendar/economic"
           "?from=2026-09-01&to=2026-09-08&token=SECRETKEY123")

    def raise_for_status(self):
        import requests
        raise requests.HTTPError(
            f"403 Client Error: Forbidden for url: {self.url}")

    def json(self):
        return {"error": "You don't have access to this resource."}


def _guard_with_403(monkeypatch):
    import requests
    from trader.agents.macro_guard import MacroGuard
    monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp403())
    return MacroGuard({"scouts": {"macro_guard": {"enabled": True,
                                                  "finnhub_token": "SECRETKEY123"}}})


def test_a_key_without_calendar_access_is_reported_not_hidden(monkeypatch):
    """With every source dead, the state must not read as 'no active event
    window' — that is indistinguishable from a genuinely quiet week, and it
    is how a guard silently stops guarding."""
    g = _guard_with_403(monkeypatch)

    state = g.check()

    assert state["active"] is False
    assert "no calendar source reachable" in state["why"], state["why"]


def test_the_api_key_never_reaches_the_log(monkeypatch, caplog):
    """requests puts the full URL — including ?token= — in its error text."""
    import logging
    g = _guard_with_403(monkeypatch)

    with caplog.at_level(logging.DEBUG):
        g.check()

    assert "SECRETKEY123" not in caplog.text


# ── ForexFactory: a free calendar that needs no key ───────────────────────
_FF_SAMPLE = [
    {"title": "G20 Meetings", "country": "All",
     "date": "2026-08-30T11:15:00-04:00", "impact": "Low",
     "forecast": "", "previous": ""},
    {"title": "Non-Farm Employment Change", "country": "USD",
     "date": "2026-09-04T08:30:00-04:00", "impact": "High",
     "forecast": "150K", "previous": "142K"},
    {"title": "German Retail Sales", "country": "EUR",
     "date": "2026-09-04T08:30:00-04:00", "impact": "High",
     "forecast": "", "previous": ""},
]


def _guard_ff(monkeypatch, now_iso):
    """A guard whose only calendar source is the ForexFactory feed."""
    import requests
    from trader.agents import macro_guard as mg

    class _R:
        status_code = 200
        def raise_for_status(self): pass
        def json(self): return _FF_SAMPLE

    monkeypatch.setattr(requests, "get", lambda *a, **k: _R())
    g = mg.MacroGuard({"scouts": {"macro_guard": {"enabled": True}}})
    g._now = lambda: datetime.fromisoformat(now_iso)
    return g


def test_forexfactory_feed_yields_us_high_impact_events(monkeypatch):
    """country is 'USD' not 'US', impact is 'High' not 'high', and the date
    carries its own UTC offset — all three differ from the Finnhub shape."""
    g = _guard_ff(monkeypatch, "2026-09-04T12:35:00+00:00")

    state = g.check()

    assert state["active"] is True, state
    assert "Non-Farm" in state["event"], state


def test_a_non_us_high_impact_event_does_not_freeze(monkeypatch):
    """German data at the same instant must not halt a crypto book."""
    g = _guard_ff(monkeypatch, "2026-09-04T12:35:00+00:00")
    g._fetch_calendar()
    titles = [e["event"] for e in g._calendar]

    assert "German Retail Sales" not in titles, titles


def test_the_window_closes_after_the_event(monkeypatch):
    """Well past the post-event window, trading resumes."""
    g = _guard_ff(monkeypatch, "2026-09-04T20:00:00+00:00")

    assert g.check()["active"] is False


def test_a_restart_reuses_the_cached_calendar(monkeypatch):
    """The kernel restarts often. Refetching from scratch every time is how
    the feed rate-limits us into fail-open."""
    g = _guard_ff(monkeypatch, "2026-09-04T12:35:00+00:00")
    assert g.check()["active"] is True          # one real fetch, now on disk

    import requests
    from trader.agents import macro_guard as mg
    calls = []

    def _boom(*a, **k):
        calls.append(a)
        raise RuntimeError("network down")

    monkeypatch.setattr(requests, "get", _boom)
    fresh = mg.MacroGuard({"scouts": {"macro_guard": {"enabled": True}}})
    fresh._now = lambda: datetime.fromisoformat("2026-09-04T12:35:00+00:00")

    assert fresh.check()["active"] is True, "restart lost the calendar"
    assert calls == [], "refetched despite a warm cache"


def test_both_sources_dead_backs_off_instead_of_hammering(monkeypatch):
    """A guard that retries a dead feed every cycle earns itself a 429."""
    import requests
    from trader.agents import macro_guard as mg
    calls = []

    def _boom(*a, **k):
        calls.append(a)
        raise RuntimeError("network down")

    monkeypatch.setattr(requests, "get", _boom)
    g = mg.MacroGuard({"scouts": {"macro_guard": {"enabled": True}}})

    for _ in range(5):
        g.check()
    first_round = len(calls)
    for _ in range(5):
        g.check()

    assert len(calls) == first_round, (
        f"kept fetching: {first_round} calls, then {len(calls)}")
    assert first_round <= 2, f"more than one attempt per source: {first_round}"


def test_a_blind_guard_says_so_even_with_no_finnhub_key(monkeypatch):
    """The common case: no Finnhub key at all, and the free feed is down.
    That must not read as a quiet week."""
    import requests
    from trader.agents import macro_guard as mg
    monkeypatch.delenv("FINNHUB_API_KEY", raising=False)
    monkeypatch.setattr(requests, "get",
                        lambda *a, **k: (_ for _ in ()).throw(
                            RuntimeError("network down")))
    g = mg.MacroGuard({"scouts": {"macro_guard": {"enabled": True}}})

    why = g.check()["why"]

    assert "no calendar source reachable" in why, why


def test_a_genuinely_quiet_week_is_not_reported_as_blind(monkeypatch):
    """A source that answers with no high-impact US events is a quiet week,
    and must be distinguishable from a dead feed."""
    import requests
    from trader.agents import macro_guard as mg

    class _R:
        status_code = 200
        headers = {}
        def raise_for_status(self): pass
        def json(self): return [{"title": "German Retail Sales",
                                 "country": "EUR", "impact": "High",
                                 "date": "2026-09-04T08:30:00-04:00"}]

    monkeypatch.delenv("FINNHUB_API_KEY", raising=False)
    monkeypatch.setattr(requests, "get", lambda *a, **k: _R())
    g = mg.MacroGuard({"scouts": {"macro_guard": {"enabled": True}}})

    assert g.check()["why"] == "no active event window"
