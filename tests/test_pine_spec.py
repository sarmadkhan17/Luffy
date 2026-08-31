import json
import pathlib

import pytest

from trader.brain.pine import lint
from trader.strategy.compile import compile_spec
from trader.strategy.pine_spec import to_pine
from trader.strategy.spec import ExitSpec, StrategySpec


def _spec(**kw):
    base = dict(
        id="p1", name="Pine Probe",
        thesis="A test hypothesis long enough to satisfy the spec validator's "
               "minimum thesis length, describing a plausible inefficiency in "
               "short-horizon price behaviour.",
        invalidation="Retire below profit factor 1.0 over 30 trades.",
        provenance={"source_kind": "test"}, universe={"include": []},
        timeframe="15m", direction="long",
        entry_long="close > ema(20) and adx(14) > 25", entry_short="",
        filters=[], exit=ExitSpec(), regime_filter=["TRENDING_UP"],
        markets=["futures"])
    base.update(kw)
    return StrategySpec(**base)


def _all_specs():
    out = []
    for d in ("data/seed_specs", "data/authored_specs"):
        for p in sorted(pathlib.Path(d).glob("*.json")):
            out.append(StrategySpec.from_dict(json.loads(p.read_text())))
    return out


def test_ohlcv_spec_is_tv_testable():
    code, ok, why = to_pine(_spec())
    assert ok, why
    assert "//@version=5" in code and "strategy(" in code


def test_generated_pine_passes_the_existing_lint_gate():
    code, ok, _ = to_pine(_spec())
    assert lint(code) == []


def test_funding_spec_is_excluded_not_substituted():
    """FAMILY_TV mapped a family to a STOCK TradingView strategy, so every
    candidate got an identical verdict. A feature Pine cannot see must
    disqualify the spec, never be swapped for something else."""
    code, ok, why = to_pine(_spec(entry_long="funding_z(1920) > 2.0"))
    assert not ok and code == ""
    assert "funding" in why[0]


def test_adx_is_hoisted_because_dmi_returns_a_tuple():
    code, ok, _ = to_pine(_spec())
    assert "[_dip14, _dim14, _adx14] = ta.dmi(14, 14)" in code
    assert "ta.dmi(14, 14)[2]" not in code


def test_btc_security_call_is_hoisted_once():
    code, ok, why = to_pine(_spec(
        entry_long="btc_ret(4) > 0.01 and corr_btc(96) > 0.5"))
    assert ok, why
    # Pine caps a script at 40 request.security calls
    assert code.count("request.security") == 1


def test_exit_geometry_comes_from_the_spec_not_config():
    tight, _, _ = to_pine(_spec(exit=ExitSpec(
        stop={"kind": "atr", "mult": 1.2}, target={"kind": "rr", "v": 1.5},
        trail={"kind": "none"}, time={"max_bars": 10})))
    wide, _, _ = to_pine(_spec(exit=ExitSpec(
        stop={"kind": "atr", "mult": 3.0}, target={"kind": "rr", "v": 4.0},
        trail={"kind": "none"}, time={"max_bars": 90})))
    assert "ta.atr(14) * 1.2" in tight and "_sd * 1.5" in tight
    assert "ta.atr(14) * 3.0" in wide and "_sd * 4.0" in wide
    assert "bar_index - _bar >= 10" in tight
    assert "bar_index - _bar >= 90" in wide


def test_trailing_stop_is_emitted_when_specified():
    code, ok, _ = to_pine(_spec(exit=ExitSpec(
        stop={"kind": "atr", "mult": 2.0}, target={"kind": "none"},
        trail={"kind": "atr", "mult": 2.0, "arm_at_r": 1.0},
        time={"max_bars": 64})))
    assert ok and "trail_points" in code and "trail_offset" in code


def test_short_only_spec_never_goes_long():
    code, ok, _ = to_pine(_spec(direction="short", entry_long="",
                                entry_short="close < ema(20)"))
    assert ok and "_long  = false" in code


def test_every_shipped_spec_either_compiles_or_says_why():
    for s in _all_specs():
        code, ok, why = to_pine(s)
        if ok:
            assert lint(code) == [], f"{s.name}: {lint(code)}"
        else:
            assert why and isinstance(why[0], str) and len(why[0]) > 10, \
                f"{s.name} excluded with no reason"


def test_compiled_strategy_exposes_to_pine():
    c = compile_spec(_spec())
    code, ok, why = c.to_pine()
    assert ok and "//@version=5" in code


def test_price_only_specs_are_mostly_testable():
    """If the Pine path silently excluded everything it would be useless."""
    n = sum(1 for s in _all_specs() if to_pine(s)[1])
    assert n >= 10, f"only {n} specs are TV-testable"
