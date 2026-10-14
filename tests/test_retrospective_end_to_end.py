"""Offline discovery and compiled strategy use one bounded stored dataset."""
import dataclasses
import hashlib
import json
from pathlib import Path
import sqlite3

import numpy as np
import pytest

from trader.data.feed import DataFeed
from trader.research import evaluate, job, slices
from trader.research.combo import Combination
from trader.research.vocab import Part
from trader.strategy.compile import compile_spec
from trader.strategy.spec import StrategySpec
from trader.strategy.vector_backtest import simulate

H = 14_400_000


@pytest.fixture
def historical(tmp_path):
    candles, ledger = tmp_path / "candles.db", tmp_path / "preserved.db"
    rng = np.random.default_rng(17)
    with sqlite3.connect(candles) as con:
        con.execute("CREATE TABLE candles(symbol TEXT,tf TEXT,ts INTEGER,open REAL,high REAL,low REAL,close REAL,volume REAL,taker_buy REAL)")
        for i in range(17):
            close = 100 * np.cumprod(1 + rng.normal(0, .012, 1000))
            con.executemany("INSERT INTO candles VALUES (?,?,?,?,?,?,?,?,?)",
                            [(f"S{i}/USDT", "4h", b * H, p, p * 1.01, p * .99, p, 100, None)
                             for b, p in enumerate(close)])
    cut = 700 * H + H // 2
    with sqlite3.connect(ledger) as con:
        con.execute("CREATE TABLE research_slices(tf TEXT,cut_ms INTEGER,counts TEXT)")
        con.execute("INSERT INTO research_slices VALUES (?,?,?)", ("4h", cut, "{}"))
        con.execute("CREATE TABLE research_budget(id TEXT,spent REAL)")
        con.execute("INSERT INTO research_budget VALUES ('immutable-look',.001)")
        con.execute("CREATE TABLE learning_receipts(id TEXT,source_hash TEXT)")
        con.execute("INSERT INTO learning_receipts VALUES ('old-receipt','registered-source-hash')")
    contract = {"dataset_id": "synthetic-preserved", "cut_ms": cut,
                "discovery_cut_source": str(ledger),
                "discovery_cut_source_sha256": hashlib.sha256(ledger.read_bytes()).hexdigest()}
    cfg = {"risk": {"risk_per_trade_pct": .5, "taker_fee_pct": .04,
                    "slippage_atr_frac": .015, "funding_rate_8h": .0001,
                    "real_funding": False, "max_open_trades": 8},
           "research": {"retrospective": contract, "referee": False, "handoff": False}}
    return candles, ledger, cfg


def test_sixteen_symbol_discovery_evaluator_and_backtest_reproduce(historical):
    candles, ledger, cfg = historical
    before = (candles.read_bytes(), ledger.read_bytes())
    symbols = [f"S{i}/USDT" for i in range(16)]
    paths = {"candles": str(candles)}
    bundle = evaluate.load_bundle("4h", symbols, cfg, ("ohlcv",), ["S16/USDT"], paths)
    assert len(bundle.frames) == 16 and job.admit_bundle(bundle, 16)["admitted"]
    assert set(bundle.heldout_bars["a"]) == {"S16/USDT"}
    assert all(len(d) == 699 or len(d) == 700 for d in bundle.frames.values())
    assert all(int(slices._ms(d).max()) + H <= bundle.cut for d in bundle.frames.values())
    part = Part("ev:donch20", "event", "ev:donch20", "close > donchian_hi(20)", "close < donchian_lo(20)")
    combo = Combination((part,), "4h", "trail")
    payload = {"tf": "4h", "symbols": symbols, "heldout_symbols": ["S16/USDT"],
               "cfg": cfg, "paths": paths, "requires": ["ohlcv"],
               "combos": [combo.as_dict()], "min_symbols": 16, "draws": 30, "seed": 17}
    first, second = job.evaluate_job(payload), job.evaluate_job(payload)
    assert first["done"] == 1 and first["loaded_symbols"] == 16
    assert first["results"] == second["results"]
    assert first["results"][0]["verdict"] != "error"
    root = Path(__file__).resolve().parents[1]
    spec = StrategySpec.from_dict(json.loads((root / "data/seed_specs/ma_crossover.json").read_text()))
    spec.timeframe = "4h"
    compiled = compile_spec(spec)
    df = bundle.frames[symbols[0]]
    frames = bundle.sym_frames[symbols[0]]
    lo, sh = compiled.entries(frames, symbol=symbols[0])
    first_bt = simulate(lo, sh, df, spec.exit, bundle.risk)
    second_bt = simulate(*compiled.entries(frames, symbol=symbols[0]), df, spec.exit, bundle.risk)
    assert first_bt.trades > 0
    assert dataclasses.asdict(first_bt) == dataclasses.asdict(second_bt)
    assert bundle.evidence["factory_validated"] is False
    assert before == (candles.read_bytes(), ledger.read_bytes())


def test_discovery_cut_authentication_refuses_changed_source(historical):
    candles, ledger, cfg = historical
    with sqlite3.connect(ledger) as con:
        con.execute("UPDATE research_slices SET cut_ms=cut_ms+1")
    with pytest.raises(ValueError, match="cut_source_hash_mismatch"):
        evaluate.load_bundle("4h", ["S0/USDT"], cfg, paths={"candles": str(candles)})


def test_protected_symbols_have_no_price_loader_access(historical, monkeypatch):
    candles, _, cfg = historical
    original = DataFeed.cached_ohlcv
    calls = []
    def audited(self, symbol, tf="15m", limit=20000, **kw):
        calls.append(symbol)
        assert symbol != "S16/USDT", "protected held-out prices requested"
        return original(self, symbol, tf, limit, **kw)
    monkeypatch.setattr(DataFeed, "cached_ohlcv", audited)
    bundle = evaluate.load_bundle("4h", [f"S{i}/USDT" for i in range(16)], cfg,
                                  heldout_symbols=["S16/USDT"], paths={"candles": str(candles)})
    assert len(calls) == 16 and bundle.heldout_bars["a"]["S16/USDT"] > 0


def test_protected_symbol_cannot_be_relabelled_as_discovery(historical):
    candles, _, cfg = historical
    with pytest.raises(ValueError, match='protected_symbol_refused'):
        evaluate.load_bundle('4h', ['S16/USDT'], cfg, heldout_symbols=['S16/USDT'],
                             paths={'candles': str(candles)})
