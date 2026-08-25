"""Pine Forge tests — generation, folds, lint gate, LLM-refine safety."""
import pytest

from trader.brain.pine import (forge, fold_windows, forge_and_store, lint,
                               refine_with_llm, save, sha)
from trader.strategy.genome import Genome

HYP = ("Mean-reverting liquidity provision: statistical extremes revert "
       "as panic sellers pay the liquidity premium over time.")


def _genome(family="rsi_extreme", **params):
    defaults = {
        "rsi_extreme": {"rsi_len": 14, "os_level": 30.0, "ob_level": 70.0},
        "ma_cross": {"fast_len": 20, "slow_len": 50},
        "vwap_fade": {"z_entry": 2.5, "anchor_bars": 96,
                      "max_hold_bars": 24},
    }
    return Genome(strategy_id=f"test_{family}", family=family,
                  hypothesis=HYP, invalidation="Demote on PF<0.85/20 trades.",
                  regime_filter=frozenset({"RANGING"}),
                  markets=frozenset({"futures"}), params=params or
                  defaults.get(family, {}))


ALL_FAMILIES = {
    "ema_trend": {"adx_min": 22.0, "pullback_atr": 0.8, "trend_tf": "15m"},
    "vwap_fade": {"z_entry": 2.5, "anchor_bars": 96, "max_hold_bars": 24},
    "breakout_retest": {"range_lookback": 48, "vol_mult": 1.4,
                        "retest_atr": 0.5},
    "sweep_reversal": {"max_reclaim_bars": 3, "min_sweep_frac": 0.002},
    "rotation_momo": {"btc_ret_1h_min": 0.008, "lag_lookback": 4},
    "rsi_extreme": {"rsi_len": 14, "os_level": 30.0, "ob_level": 70.0},
    "ma_cross": {"fast_len": 20, "slow_len": 50},
    "bb_fade": {"bb_len": 20, "bb_k": 2.0},
}


@pytest.mark.parametrize("family,params", list(ALL_FAMILIES.items()))
def test_all_families_forge_and_lint(family, params):
    g = _genome(family, **params)
    assert not Genome.validate(g)
    code = forge(g)
    assert lint(code) == []
    assert f"strategy_id=test_{family}" in code
    assert "inWin" in code


def test_fold_variant_hardcodes_window():
    g = _genome()
    full = forge(g)
    folds = fold_windows(3)
    f1 = forge(g, window=folds[0])
    f2 = forge(g, window=folds[1])
    assert full != f1 != f2
    y = int(folds[0][0][:4])
    assert f"timestamp({y}," in f1
    assert "timestamp(1970," not in f1          # full-run default replaced


def test_lint_catches_unfilled_placeholder():
    bad = "//@version=5\nstrategy(\"x\")\nx = {fast_len}\n"
    errs = lint(bad)
    assert any("placeholder" in e for e in errs)


def test_params_injected_as_literals():
    g = _genome("rsi_extreme", rsi_len=21, os_level=25.0, ob_level=75.0)
    code = forge(g)
    assert "ta.rsi(close, 21)" in code
    assert "ta.crossover(r, 25)" in code
    assert "crossunder(r, 75)" in code


def test_empty_params_fall_back_to_defaults():
    """Regression: params={} once baked literal 'None' into the Pine
    source (ta.rsi(close, None)) → TV compile error at add-to-chart."""
    from trader.brain.pine import TEMPLATES
    for fam in TEMPLATES:
        g = _genome(fam)
        code = forge(g)
        assert "None" not in code, f"{fam} forged a 'None' literal"
        assert lint(code) == [], f"{fam} failed lint with empty params"


class FakeLLM:
    available = True

    def __init__(self, reply):
        self.reply = reply

    def chat_json(self, prompt, deep=False):
        return self.reply


def test_refine_accepts_good_edit():
    g = _genome()
    base = forge(g)
    improved = base.replace(
        'buySig = inWin and ta.crossover(r, 30)',
        'buySig = inWin and ta.crossover(r, 30) and volume > ta.sma(volume, 48)')
    out, refined = refine_with_llm(g, base, FakeLLM({"code": improved}))
    assert refined and "volume > ta.sma(volume, 48)" in out


def test_refine_rejects_structure_tampering():
    g = _genome()
    base = forge(g)
    broken = base.replace("inWin  = time >= tStart",
                          "inWin  = true // window removed")
    out, refined = refine_with_llm(g, base,
                                   FakeLLM({"code": broken}))
    assert not refined and out == base


def test_refine_rejects_unlintable():
    g = _genome()
    base = forge(g)
    bad = base.replace("ta.rsi(close, 14)", "ta.rsi(close, {oops}")
    out, refined = refine_with_llm(g, base, FakeLLM({"code": bad}))
    assert not refined and out == base


def test_forge_and_store_writes_files(tmp_path, monkeypatch):
    import trader.brain.pine as pine_mod
    from trader.core.journal import Journal
    monkeypatch.setattr(pine_mod, "PINE_DIR", tmp_path / "pine")
    j = Journal(tmp_path / "j.db")
    manifest = forge_and_store(_genome(), j, llm=None, n_folds=3)
    d = tmp_path / "pine" / "test_rsi_extreme"
    files = sorted(p.name for p in d.glob("*.pine"))
    assert files == ["fold1.pine", "fold2.pine", "fold3.pine", "full.pine"]
    assert manifest["files"]["full"] == sha((d / "full.pine").read_text())
    ev = j.query("SELECT kind FROM brain_events WHERE "
                 "kind='pine_forged'")
    assert len(ev) == 1
