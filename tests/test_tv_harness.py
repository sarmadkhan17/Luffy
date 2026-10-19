"""TV Harness unit tests — pure logic, no browser, no network."""
from trader.brain.tv_harness import TVHarness, parse_metrics, sha_code


def _harness(tmp_path, monkeypatch, budget=20):
    import yaml
    from trader.core.journal import Journal
    monkeypatch.setattr("trader.brain.tv_harness.CACHE_PATH",
                        tmp_path / "cache.json")
    cfg = yaml.safe_load(open("config.yaml")) or {}
    cfg["tv_harness"] = {"daily_runs": budget}
    return TVHarness(Journal(tmp_path / "j.db"), cfg)


FIXTURE_TEXT = """
Strategy Tester
Overview Performance Summary
Total Net Profit
+1,234.56 USD 12.34%
Gross Profit
2,500 USD
Gross Loss
-1,265.44 USD
Max strategy drawdown
-345.67 USD 8.9%
Total Closed Trades
35
Number Winning Trades
20
Percent Profitable
57.14 %
Profit Factor
1.98
Sharpe Ratio
1.42
Buy & Hold Return
5.5 %
"""


def test_parse_metrics_extracts_core_fields():
    m = parse_metrics(FIXTURE_TEXT)
    assert m["net_profit_usd"] == 1234.56
    assert m["total_trades"] == 35
    assert abs(m["win_rate_pct"] - 57.14) < 0.01
    assert m["profit_factor"] == 1.98
    assert abs(m["max_drawdown_pct"] - 8.9) < 0.01 or \
        abs(m["max_drawdown_pct"]) == 8.9
    assert m["sharpe"] == 1.42


def test_parse_metrics_free_plan_key_stats():
    """2026-08 free plan: only Total PnL + Max drawdown (+ dash winrate)."""
    text = """Key stats
Total PnL
−83.94
USDT
−0.84%
Max drawdown
328.65
USDT
3.29%
Profitable trades
—
Performance"""
    m = parse_metrics(text)
    assert m["net_profit_usd"] == -83.94
    assert m["net_profit_pct"] == -0.84
    assert m["max_drawdown_usd"] == 328.65
    assert m["max_drawdown_pct"] == 3.29
    assert "total_trades" not in m and "sharpe" not in m


def test_cache_roundtrip(tmp_path, monkeypatch):
    h = _harness(tmp_path, monkeypatch)
    key = sha_code("code", "BINANCE:BTCUSDT", "1h")
    assert h.cached(key) is None
    cache = h._cache_load()
    cache[key] = {"ok": True}
    h._cache_save(cache)
    assert h.cached(key)["ok"] is True
    # symbol/tf change → different key
    assert h.cached(sha_code("code", "X", "1h")) is None


def test_budget_gate_blocks(tmp_path, monkeypatch):
    h = _harness(tmp_path, monkeypatch, budget=0)
    res = h.backtest("//@version=5\nstrategy('t')", force=True)
    assert res.get("skipped") == "daily_budget"


def test_runs_today_counts_events(tmp_path, monkeypatch):
    from trader.core.journal import Journal
    j = Journal(tmp_path / "j.db")
    h = _harness(tmp_path, monkeypatch)
    # patch harness journal to our instance
    h.journal = j
    assert h.runs_today() == 0
    j.log_brain_event("tv_backtest", "a", {"ok": True})
    assert h.runs_today() == 1



# Ordinary test doubles exercise the existing browser adapter without a browser.
class _Page:
    def __init__(self, label='1h', fail=False):
        self.label, self.fail = label, fail
        self.keyboard = self
        self.first = self
    def locator(self, *a): return self
    def click(self, **kw):
        if self.fail: raise RuntimeError('focus lost')
    def type(self, *a, **kw): pass
    def press(self, *a): pass
    def wait_for_timeout(self, *a): pass
    def inner_text(self, **kw): return self.label
    def goto(self, *a, **kw): pass
    def wait_for_selector(self, *a, **kw): pass
    def screenshot(self, *a, **kw): pass


def test_timeframe_setting_requires_observed_interval(tmp_path, monkeypatch):
    import pytest
    h = _harness(tmp_path, monkeypatch)
    h._set_timeframe(_Page('1h'), '1h')
    for page in (_Page('4h'), _Page(''), _Page('1h', fail=True)):
        with pytest.raises(RuntimeError):
            h._set_timeframe(page, '1h')


def _browser(monkeypatch, page):
    import sys
    from types import SimpleNamespace as N
    class Context:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def new_page(self): return page
        def grant_permissions(self, *a, **kw): pass
        def close(self): pass
    ctx = Context()
    ctx.chromium = N(launch_persistent_context=lambda *a, **kw: ctx)
    monkeypatch.setitem(sys.modules, 'playwright.sync_api', N(sync_playwright=lambda: ctx))


def test_wrong_interval_run_is_neither_successful_nor_cached(tmp_path, monkeypatch):
    h = _harness(tmp_path, monkeypatch)
    monkeypatch.setattr('trader.brain.tv_harness.PROFILE_DIR', tmp_path / 'profile')
    monkeypatch.setattr('trader.brain.tv_harness.SHOT_DIR', tmp_path)
    _browser(monkeypatch, _Page('4h'))
    res = h.backtest('code', tf='1h')
    assert not res['ok'] and 'timeframe_unverified' in res['error']
    assert h.cached(sha_code('code', tf='1h')) is None


def test_verified_interval_result_is_cached_with_matching_identity(tmp_path, monkeypatch):
    h = _harness(tmp_path, monkeypatch)
    monkeypatch.setattr('trader.brain.tv_harness.PROFILE_DIR', tmp_path / 'profile')
    _browser(monkeypatch, _Page('1h'))
    for method in ('_open_pine_editor', '_paste_script', '_add_to_chart'):
        monkeypatch.setattr(h, method, lambda *a: None)
    monkeypatch.setattr(h, '_read_tester', lambda *a: {'profit_factor': 1.4})
    res = h.backtest('code')
    assert res['ok'] and res['interval_verified'] is True
    assert h.backtest('code')['cached'] is True


def test_legacy_unverified_cache_does_not_supply_evidence(tmp_path, monkeypatch):
    h = _harness(tmp_path, monkeypatch, budget=0)
    key = sha_code('code')
    h._cache_save({key: {'ok': True, 'metrics': {'profit_factor': 10.}, 'tf': '1h', 'symbol': 'BINANCE:BTCUSDT'}})
    assert h.backtest('code')['skipped'] == 'daily_budget'
