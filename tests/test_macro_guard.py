"""Tests for MacroGuard — economic calendar hard-freeze agent."""
from __future__ import annotations
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
