"""TV Harness unit tests — pure logic, no browser, no network."""
import json

from trader.brain.tv_harness import (TVHarness, evaluate_manifest,
                                     parse_metrics, sha_code)


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


def test_evaluate_manifest_verdict_logic(tmp_path, monkeypatch):
    import tempfile, pathlib, yaml
    from trader.brain import pine as pine_mod
    from trader.core.journal import Journal
    monkeypatch.setattr(pine_mod, "PINE_DIR", pathlib.Path(tempfile.mkdtemp()))
    sid_dir = pine_mod.PINE_DIR / "s1"
    sid_dir.mkdir(parents=True)
    (sid_dir / "full.pine").write_text("//@version=5\nstrategy('x')")
    for i in (1, 2, 3):
        (sid_dir / f"fold{i}.pine").write_text(f"// fold{i}")
    cfg = yaml.safe_load(open("config.yaml")) or {}
    h = TVHarness(Journal(pathlib.Path(tempfile.mkdtemp()) / "j.db"), cfg)

    good_metrics = {"net_profit_pct": 14.9, "total_trades": 66,
                    "win_rate_pct": 66.7, "sharpe": 7.4,
                    "max_drawdown_pct": 3.2, "profit_factor": 2.4}
    fold_metrics = [{"net_profit_usd": 10}, {"net_profit_usd": 5},
                    {"net_profit_usd": -2}]
    calls = {"n": 0}

    def stub(code, s="BINANCE:BTCUSDT", t="1h", force=False):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"ok": True, "metrics": good_metrics}
        return {"ok": True,
                "metrics": fold_metrics.pop(0) if fold_metrics else {}}

    monkeypatch.setattr(h, "backtest", stub)
    manifest = {"strategy_id": "s1"}
    verdict = evaluate_manifest(h, manifest)
    assert verdict["valid"] and verdict["judge"] == "real_tv"
    assert verdict["positive_folds"] == 2

    # failing drawdown gate → invalid
    bad = {**good_metrics, "max_drawdown_pct": 33.0}
    calls["n"] = 0
    fold_metrics = [{"net_profit_usd": 10}, {"net_profit_usd": 5},
                    {"net_profit_usd": -2}]
    monkeypatch.setattr(h, "backtest",
                        lambda code, s="B", t="1h", force=False:
                        {"ok": True, "metrics": bad})
    v2 = evaluate_manifest(h, manifest)
    assert not v2["valid"] and not v2["checks"]["drawdown_ok"]

    # harness failure surfaces reason
    monkeypatch.setattr(h, "backtest",
                        lambda *a, **k: {"ok": False,
                                         "error": "selector broke"})
    v3 = evaluate_manifest(h, manifest)
    assert not v3["valid"] and "selector" in v3["reason"]

    # free-plan metrics (no trades/sharpe) → those checks simply absent
    free = {"net_profit_pct": 5.0, "max_drawdown_pct": 4.0}
    calls["n"] = 0
    fold_metrics = [{"net_profit_usd": 10}, {"net_profit_usd": -30},
                    {"net_profit_usd": 8}]
    monkeypatch.setattr(h, "backtest",
                        lambda code, s="B", t="1h", force=False:
                        {"ok": True, "metrics": free})
    v4 = evaluate_manifest(h, manifest)
    assert "enough_trades" not in v4["checks"]
    assert "quality" not in v4["checks"]
    assert v4["valid"] is False            # only 1/3 folds positive

    # low-WR trend profile passes quality on profit factor
    trend = {"net_profit_pct": 5.0, "max_drawdown_pct": 4.0,
             "win_rate_pct": 34.0, "profit_factor": 1.3}
    fold_metrics = [{"net_profit_usd": 10}, {"net_profit_usd": -30},
                    {"net_profit_usd": 8}]
    monkeypatch.setattr(h, "backtest",
                        lambda code, s="B", t="1h", force=False:
                        {"ok": True, "metrics": trend})
    v5 = evaluate_manifest(h, manifest)
    assert v5["checks"]["quality"] is True
    # ...but a breakeven PF fails it
    trend["profit_factor"] = 1.02
    v6 = evaluate_manifest(h, manifest)
    assert v6["checks"]["quality"] is False

    assert json.dumps(v4)                  # serializable for the brain dossier
