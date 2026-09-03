"""The Harvester — the numeric half of research.

The Scraper reads prose, the Harvester reads numbers, and until now only the
first existed as a role. These tests pin the two things that make the second
worth having: it acquires the series Binance truncates, and it tells the
Strategist the TRUTH about what can be tested — the same truth the Analyst
will later enforce, so the Strategist stops writing specs that get refused
for coverage rather than scored.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trader.brain.harvester import Harvester
from trader.core.journal import Journal
from trader.data.coinalyze import Coinalyze
from trader.strategy.spec_evidence import MIN_COVERAGE

CFG = {"derivatives": {"symbols": ["BTC/USDT"]},
       "strategies": {"backtest_bars": 8000},
       "timeframes": {"execution": "15m"},
       "harvester": {}}


class _Deriv:
    """A DerivFeed stub whose coverage we control, in ms like the real one."""

    def __init__(self, cov=None):
        self._cov = cov or {}
        self.saved = []

    def coverage(self):
        return self._cov

    def save(self, symbol, series, df):
        self.saved.append((symbol, series, len(df)))


def _days(series: str, days: float, symbol="BTC/USDT"):
    hi = 1_800_000_000_000
    return {(symbol, series): (500, hi - int(days * 86400_000), hi)}


def _h(journal, cov=None, cfg=None):
    h = Harvester(journal, cfg or CFG)
    h.deriv = _Deriv(cov)
    return h


@pytest.fixture
def journal(tmp_path) -> Journal:
    return Journal(tmp_path / "h.db")


# ── the coverage bar must be the Analyst's bar, not a guess ──────────────
def test_required_span_is_derived_from_the_backtest_frame(journal):
    """8000 bars x 15m = 83.3 days; the gate wants 90% of it."""
    assert _h(journal).required_days == pytest.approx(
        8000 * 15 / 1440 * MIN_COVERAGE)


def test_a_longer_frame_raises_the_bar(journal):
    cfg = {**CFG, "strategies": {"backtest_bars": 20000}}
    assert _h(journal, cfg=cfg).required_days > _h(journal).required_days


def test_a_coarser_execution_timeframe_raises_the_bar(journal):
    cfg = {**CFG, "timeframes": {"execution": "1h"}}
    assert _h(journal, cfg=cfg).required_days > _h(journal).required_days


# ── the brief must not promise what the Analyst will refuse ──────────────
def test_a_series_shorter_than_the_frame_is_thin_not_usable(journal):
    """Binance's ~31 days of open interest against a 75-day requirement.
    Reporting that as usable is what made the Strategist write three specs
    the Analyst had to reject for coverage."""
    b = _h(journal, _days("oi", 31.0)).brief()
    assert "oi" not in b["usable"]
    assert any(t.startswith("oi(") for t in b["thin"])


def test_a_deep_series_is_usable(journal):
    assert "basis" in _h(journal, _days("basis", 730.0)).brief()["usable"]


def test_a_series_with_no_rows_is_missing(journal):
    b = _h(journal).brief()
    assert set(b["missing"]) >= {"oi", "basis", "funding"}
    assert b["usable"] == []


def test_brief_text_names_the_span_and_warns_off_untestable_series(journal):
    t = _h(journal, _days("oi", 31.0)).brief_text()
    assert "75.0 days" in t
    assert "refused for coverage" in t


def test_coverage_folds_across_symbols(journal):
    cov = {**_days("oi", 40.0, "BTC/USDT"), **_days("oi", 10.0, "ETH/USDT")}
    rec = _h(journal, cov).coverage()["oi"]
    assert rec["symbols"] == 2
    assert rec["days"] == 40.0          # the deepest symbol sets the span


# ── Coinalyze: absent key must be a no-op, never a crash ─────────────────
def test_no_key_means_unavailable(monkeypatch):
    monkeypatch.delenv("COINALYZE_API_KEY", raising=False)
    assert Coinalyze().available is False


def test_no_key_returns_an_empty_frame_not_an_error():
    df = Coinalyze(key="").history("BTC/USDT", "oi")
    assert isinstance(df, pd.DataFrame) and df.empty


def test_deepen_is_a_noop_without_a_key(journal):
    h = _h(journal, _days("oi", 31.0))
    h.coinalyze = Coinalyze(key="")
    assert h.deepen() == {}
    assert h.deriv.saved == []


def test_deepen_stores_what_the_deep_source_returns(journal):
    """With a key, the thin series get filled from a source that keeps them."""
    class _Deep:
        available = True

        def history(self, symbol, series, years=2.0, interval="4h"):
            ts = pd.to_datetime([1_700_000_000_000], unit="ms", utc=True)
            return pd.DataFrame({"ts": ts, "value": [1.0]})

    h = _h(journal, _days("oi", 31.0))
    h.coinalyze, h.delay = _Deep(), 0.0
    got = h.deepen()
    assert got["oi"] == 1
    assert ("BTC/USDT", "oi", 1) in h.deriv.saved


def test_deepen_survives_a_failing_source(journal):
    class _Broken:
        available = True

        def history(self, *a, **kw):
            raise RuntimeError("upstream down")

    h = _h(journal, _days("oi", 31.0))
    h.coinalyze, h.delay = _Broken(), 0.0
    assert h.deepen() == {}          # logged, not raised


# ── MCP: the kernel can reach servers, and a dead one cannot stall it ────
def test_mcp_clients_are_built_from_config(journal):
    cfg = {**CFG, "mcp": {"servers": {"coingecko": {
        "url": "https://mcp.api.coingecko.com/mcp"}}}}
    assert "coingecko" in _h(journal, cfg=cfg).mcp


def test_a_disabled_mcp_server_is_not_built(journal):
    cfg = {**CFG, "mcp": {"servers": {"x": {"url": "http://h", 
                                            "enabled": False}}}}
    assert _h(journal, cfg=cfg).mcp == {}


def test_mcp_probe_reports_a_dead_server_as_empty_not_a_crash(journal):
    class _Dead:
        def tools(self):
            raise RuntimeError("unreachable")

    h = _h(journal)
    h._mcp = {"dead": _Dead()}
    assert h.mcp_probe() == {"dead": []}


# ── the acquisition pass journals what it did ────────────────────────────
def test_harvest_once_journals_the_brief(journal):
    h = _h(journal, _days("basis", 730.0))
    h.deriv.record_all = lambda syms, delay=0.3: {"basis": 12}
    rep = h.harvest_once()
    assert rep["rows"] == 12
    assert "basis" in rep["brief"]["usable"]
    rows = journal.query("SELECT detail FROM brain_events "
                         "WHERE kind='harvest_numeric'")
    assert rows and "basis" in rows[0]["detail"]


def test_harvest_once_with_no_symbols_does_nothing(journal):
    cfg = {**CFG, "derivatives": {"symbols": []}}
    assert _h(journal, cfg=cfg).harvest_once()["symbols"] == 0


# ── Coinalyze: the three faults a live key exposed (2026-09-03) ──────────
# Every one of these was a silent wrong answer, not a crash: a 400 that read
# as "no data", another venue's book read as Binance's, and a funding series
# 100x too large overwriting the one the backtest charges every trade with.

def test_interval_is_translated_to_the_documented_enum():
    """Coinalyze names intervals '4hour', not '4h'. Sending ours 400s, and a
    400 degrades to an empty frame — which reads as 'no history', not as
    'we asked wrongly'."""
    from trader.data.coinalyze import to_interval
    assert to_interval("4h") == "4hour"
    assert to_interval("1h") == "1hour"
    assert to_interval("1d") == "daily"
    assert to_interval("4hour") == "4hour"      # already canonical


def test_an_unmappable_interval_is_refused_not_sent():
    from trader.data.coinalyze import to_interval
    with pytest.raises(ValueError):
        to_interval("3h")


def _markets_payload():
    """/future-markets returns one row per exchange per market. Bybit sorts
    before Binance for BTC, which is how BTC/USDT silently resolved to Bybit."""
    return [
        {"symbol": "BTCUSDT.6", "exchange": "6", "base_asset": "BTC",
         "quote_asset": "USDT", "is_perpetual": True,
         "has_long_short_ratio_data": True, "has_ohlcv_data": True},
        {"symbol": "BTCUSDT_PERP.A", "exchange": "A", "base_asset": "BTC",
         "quote_asset": "USDT", "is_perpetual": True,
         "has_long_short_ratio_data": True, "has_ohlcv_data": True},
        {"symbol": "ETHUSDT_PERP.4", "exchange": "4", "base_asset": "ETH",
         "quote_asset": "USDT", "is_perpetual": True,
         "has_long_short_ratio_data": False, "has_ohlcv_data": True},
    ]


class _Client(Coinalyze):
    """A Coinalyze whose HTTP layer is a canned dict, so resolution and unit
    handling are testable without a key."""

    def __init__(self, payloads):
        super().__init__(key="test-key")
        self.payloads, self.calls = payloads, []

    def _get(self, path, params):
        self.calls.append((path, dict(params)))
        return self.payloads.get(path)


def test_a_market_resolves_to_binance_not_whichever_row_came_first():
    c = _Client({"/future-markets": _markets_payload()})
    assert c.resolve("BTC/USDT") == "BTCUSDT_PERP.A"


def test_a_symbol_binance_does_not_list_resolves_to_nothing():
    """Better no series than another venue's book stored under our key."""
    c = _Client({"/future-markets": _markets_payload()})
    assert c.resolve("ETH/USDT") == ""


def test_long_short_is_not_requested_when_the_venue_has_no_such_data():
    c = _Client({"/future-markets": [
        {"symbol": "XUSDT_PERP.A", "exchange": "A", "base_asset": "X",
         "quote_asset": "USDT", "is_perpetual": True,
         "has_long_short_ratio_data": False, "has_ohlcv_data": True}]})
    assert c.history("X/USDT", "ls_account_ratio").empty
    assert not [p for p, _ in c.calls if "long-short" in p]


def test_funding_is_rescaled_from_percent_to_fraction():
    """Coinalyze serves funding in percent and derivs.db stores a fraction:
    measured against Binance's own series the ratio is exactly 100."""
    c = _Client({"/future-markets": _markets_payload(),
                 "/funding-rate-history": [{"history": [
                     {"t": 1_700_000_000, "c": 0.005}]}]})
    df = c.history("BTC/USDT", "funding")
    assert df["value"].iloc[0] == pytest.approx(0.00005)


def test_open_interest_is_stored_as_served():
    """Measured ratio against the stored Binance series is 0.9998 — same
    quantity, same units, so a scale here would introduce the error."""
    c = _Client({"/future-markets": _markets_payload(),
                 "/open-interest-history": [{"history": [
                     {"t": 1_700_000_000, "c": 107692.0}]}]})
    assert c.history("BTC/USDT", "oi")["value"].iloc[0] == pytest.approx(107692.0)


def test_the_global_account_ratio_is_its_own_series():
    """Coinalyze's long/short is Binance's globalLongShortAccountRatio
    (corr +1.0000, max abs diff 0.00000 over 179 points). The recorder stores
    topLongShortPositionRatio, which is anti-correlated with it at -0.64.
    Merging them would fabricate a measurement."""
    from trader.data.derivatives import SERIES
    assert "ls_account_ratio" in SERIES
    c = _Client({"/future-markets": _markets_payload(),
                 "/long-short-ratio-history": [{"history": [
                     {"t": 1_700_000_000, "r": 1.3137}]}]})
    df = c.history("BTC/USDT", "ls_account_ratio")
    assert df["value"].iloc[0] == pytest.approx(1.3137)
    assert [p for p, _ in c.calls if "long-short" in p]


def test_deepen_never_touches_funding(journal):
    """derivs.db holds 4-5 years of Binance-native funding; Coinalyze offers
    334 days. save() is INSERT OR REPLACE keyed by (symbol, series, ts), so
    fetching funding here can only overwrite good rows with shallower ones."""
    asked = []

    class _Deep:
        available = True

        def history(self, symbol, series, years=2.0, interval="4hour"):
            asked.append(series)
            ts = pd.to_datetime([1_700_000_000_000], unit="ms", utc=True)
            return pd.DataFrame({"ts": ts, "value": [1.0]})

    h = _h(journal, _days("oi", 31.0))
    h.coinalyze, h.delay = _Deep(), 0.0
    h.deepen()
    assert "funding" not in asked
    assert set(asked) == {"oi", "ls_account_ratio"}


def test_deepen_writes_the_account_ratio_without_disturbing_ls_ratio(journal):
    class _Deep:
        available = True

        def history(self, symbol, series, years=2.0, interval="4hour"):
            ts = pd.to_datetime([1_700_000_000_000], unit="ms", utc=True)
            return pd.DataFrame({"ts": ts, "value": [1.0]})

    h = _h(journal, _days("oi", 31.0))
    h.coinalyze, h.delay = _Deep(), 0.0
    h.deepen()
    written = {series for _, series, _ in h.deriv.saved}
    assert "ls_account_ratio" in written
    assert "ls_ratio" not in written
