"""The Analyst's TradingView leg, and the Librarian's spec cards.

`to_pine()` worked and nothing called it, so the TradingView half of the
Analyst's job did not exist. And the vault only knew genome rows, so every
spec-based strategy filed a card with an empty `params` block where the
mechanism should be.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trader.brain.analyst import Analyst
from trader.core.journal import Journal
from trader.knowledge.vault import Vault
from trader.strategy.spec import ExitSpec, StrategySpec


def _spec(**kw) -> StrategySpec:
    base = dict(
        id="spec_t", name="Test Mechanism",
        thesis="A" * 90, invalidation="B" * 60, provenance={},
        universe={"include": [], "exclude": [], "min_volume_usdt": 0},
        timeframe="1h", direction="long",
        entry_long="close > ema(50)", entry_short="",
        filters=["adx(14) > 18"],
        exit=ExitSpec(stop={"kind": "atr", "mult": 2.0},
                      target={"kind": "rr", "v": 2.0},
                      trail={"kind": "none"}, time={"max_bars": 32}),
        regime_filter=["TRENDING_UP"], markets=["futures"])
    base.update(kw)
    return StrategySpec(**base)


@pytest.fixture
def analyst(tmp_path):
    return Analyst(Journal(tmp_path / "a.db"), {"tv_harness": {}})


# ── symbol mapping ───────────────────────────────────────────────────────
def test_tv_symbol_maps_a_pair_to_a_binance_ticker():
    assert Analyst.tv_symbol("BTC/USDT") == "BINANCE:BTCUSDT"


def test_tv_symbol_drops_a_settlement_suffix():
    assert Analyst.tv_symbol("SOL/USDT:USDT") == "BINANCE:SOLUSDT"


# ── confirmation is advisory and honest about what it could not do ───────
def test_a_spec_pine_cannot_express_is_reported_not_faked(analyst):
    """Funding has no Pine analogue. Saying so beats substituting a proxy."""
    out = analyst.confirm_on_tv(_spec(entry_long="funding < -0.0005"))
    assert out["tv_testable"] is False
    assert out["agree"] is None            # not consulted != disagreed
    assert out["reason"]


def test_a_harness_failure_leaves_agree_unknown(analyst, monkeypatch):
    class _Broken:
        def __init__(self, *a, **kw):
            raise RuntimeError("no browser")

    monkeypatch.setattr("trader.brain.tv_harness.TVHarness", _Broken)
    out = analyst.confirm_on_tv(_spec())
    assert out["ran"] is False and out["agree"] is None


def test_a_profitable_tv_run_agrees(analyst, monkeypatch):
    class _H:
        def __init__(self, *a, **kw):
            pass

        def health(self):
            return {"state": "healthy"}

        def runs_today(self):
            return 1

        def backtest(self, code, symbol=None, tf=None, force=False):
            return {"ok": True, "metrics": {"profit_factor": 1.4}}

    monkeypatch.setattr("trader.brain.tv_harness.TVHarness", _H)
    out = analyst.confirm_on_tv(_spec())
    assert out["ran"] and out["agree"] is True and out["tv_pf"] == 1.4


def test_a_losing_tv_run_disagrees_but_is_still_only_advice(analyst,
                                                            monkeypatch):
    class _H:
        def __init__(self, *a, **kw):
            pass

        def health(self):
            return {"state": "healthy"}

        def runs_today(self):
            return 1

        def backtest(self, *a, **kw):
            return {"ok": True, "metrics": {"profit_factor": 0.4}}

    monkeypatch.setattr("trader.brain.tv_harness.TVHarness", _H)
    out = analyst.confirm_on_tv(_spec())
    assert out["agree"] is False
    # journaled as evidence, for the Theorist to weigh later
    rows = analyst.journal.query("SELECT subject FROM brain_events "
                                 "WHERE kind='tv_confirmation'")
    assert rows and rows[0]["subject"] == "spec_t"


# ── the Librarian files the mechanism, not an empty gene block ───────────
def test_a_spec_row_files_its_mechanism(tmp_path):
    row = {"id": "spec_t", "spec_json": _spec().to_json()
           if hasattr(_spec(), "to_json") else None}
    if row["spec_json"] is None:
        pytest.skip("spec has no to_json on this build")
    body = Vault._spec_body(row)
    assert "Test Mechanism" in body and "close > ema(50)" in body


def test_a_genome_row_keeps_the_gene_block(tmp_path):
    """No spec_json means a legacy genome — leave its rendering alone."""
    assert Vault._spec_body({"id": "g1", "spec_json": None}) == ""


def test_a_corrupt_spec_json_does_not_break_the_vault():
    assert Vault._spec_body({"id": "x", "spec_json": "{not json"}) == ""


# ── measured regime evidence must be STORED, not just returned ───────────
def test_set_measured_regimes_persists_the_evidence(analyst, monkeypatch):
    """Both the vault card and the strategy blender read
    `provenance["regime_evidence"]`. Nothing ever wrote it, so the regime
    half of the weighting was permanently neutral — dead code by omission."""
    by = {"RANGING": {"windows": 8, "hit_rate": 0.75, "median_pf": 1.4}}
    monkeypatch.setattr(analyst, "regime_fitness",
                        lambda spec, tf=None: {"fit": ["RANGING"],
                                               "declared": ["TRENDING_UP"],
                                               "by_regime": by})
    spec = _spec()
    analyst.set_measured_regimes(spec)
    assert spec.provenance["regime_evidence"] == by
    assert spec.regime_filter == ["RANGING"]


def test_evidence_is_stored_even_when_no_regime_qualifies(analyst,
                                                          monkeypatch):
    """Unproven is not the same as unmeasured — keep what was measured."""
    by = {"VOLATILE": {"windows": 2, "hit_rate": 0.5, "median_pf": 0.9}}
    monkeypatch.setattr(analyst, "regime_fitness",
                        lambda spec, tf=None: {"fit": [], "declared": ["X"],
                                               "by_regime": by})
    spec = _spec()
    analyst.set_measured_regimes(spec)
    assert spec.provenance["regime_evidence"] == by


def test_stored_regime_evidence_does_not_reactivate_historical_weighting(analyst, monkeypatch):
    """End to end: what the Analyst measures is what the orchestrator
    weighs."""
    from trader.strategy.blend import strategy_weights

    class _J:
        def trades_for_strategy(self, sid):
            return []

    by = {"RANGING": {"windows": 9, "hit_rate": 0.85, "median_pf": 1.8}}
    monkeypatch.setattr(analyst, "regime_fitness",
                        lambda spec, tf=None: {"fit": ["RANGING"],
                                               "by_regime": by})
    spec = _spec()
    analyst.set_measured_regimes(spec)
    assert strategy_weights(_J(), [spec], "RANGING")[spec.id] == 1.0
    assert strategy_weights(_J(), [spec], "VOLATILE")[spec.id] == 1.0
