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
