"""A spec's declared trail arming point must be the one the engine arms at.

The same class of fault `SpecExit` was written to close, one field further
in. `SpecExit.from_spec` captured the trail MULTIPLE but not the R it arms
at, and `manage()` read `ExitConfig.trail_after_r` — so every spec armed at
the 1.0 default no matter what it declared. A spec validated with a trail
armed at 2.0R would have ratcheted its stop from 1.0R live, tightening it
through a band the backtest left the stop alone in. That is trading a
geometry the evidence was never gathered under.

Donchian Breakout Trail declares 1.0, which is why this stayed latent: the
declared value and the default coincided. These tests pin both halves — the
declared value is honoured, and the Donchian/omitted cases still arm at 1.0.
"""
from datetime import datetime, timedelta, timezone

import pytest

from trader.engine.exits import ExitConfig, ExitEngine, SpecExit


def _engine(spec_exits=None):
    e = object.__new__(ExitEngine)
    e.genomes = {}
    e.spec_exits = spec_exits or {}
    e.c = ExitConfig()
    return e


def _trailing(spec_exits, side, r_now, *, strategy_id="s1"):
    """Run one manage() cycle at `r_now` and report the stop move, if any.

    Everything but the trail is neutralised: no target (so no TP1 partial),
    a fresh open (so no time stop) and no score (so no flip exit). The only
    ladder rung that can fire is the trail.
    """
    entry, risk = 100.0, 10.0
    direction = 1.0 if side == "long" else -1.0
    mark = entry + direction * r_now * risk
    stop = entry - direction * risk
    trade = {"id": 1, "symbol": "BTC/USDT", "side": side,
             "entry_price": entry, "stop_loss": stop, "amount": 1.0,
             "initial_risk": risk, "tp1_done": 0, "status": "open",
             "strategy_id": strategy_id,
             "opened_at": (datetime.now(timezone.utc)
                           - timedelta(minutes=5)).isoformat()}

    e = _engine(spec_exits)
    e.journal = type("J", (), {"query": lambda self, q, a: [dict(trade)]})()
    e.executor = None
    moved = []
    e._move_stop = lambda t, sl: moved.append(sl)
    assert e.manage(trade, mark, atr=1.0, current_score=None) is None
    return moved


# ── the declared value is honoured ──────────────────────────────────────
def test_an_explicit_arm_at_r_of_two_is_honoured():
    se = SpecExit(max_bars=500, timeframe="4h", trail_atr_mult=4.0,
                  has_target=False, arm_at_r=2.0)
    assert _engine({"s1": se})._arm_at_r({"strategy_id": "s1"}) == 2.0


def test_a_spec_armed_at_two_does_not_move_its_stop_at_one_r():
    """The defect made concrete: at 1.5R the old code ratcheted, the spec
    says stand pat."""
    se = SpecExit(max_bars=500, timeframe="4h", trail_atr_mult=4.0,
                  has_target=False, arm_at_r=2.0)
    assert _trailing({"s1": se}, "long", 1.0) == []
    assert _trailing({"s1": se}, "long", 1.5) == []
    assert _trailing({"s1": se}, "long", 1.99) == []
    assert _trailing({"s1": se}, "long", 2.0) != []


def test_no_stop_moves_anywhere_below_the_declared_threshold():
    se = SpecExit(max_bars=500, timeframe="4h", trail_atr_mult=4.0,
                  has_target=False, arm_at_r=3.0)
    for r in (0.0, 0.5, 1.0, 2.0, 2.9):
        assert _trailing({"s1": se}, "long", r) == [], f"armed early at {r}R"
    assert _trailing({"s1": se}, "long", 3.0) != []


# ── long/short symmetry ─────────────────────────────────────────────────
@pytest.mark.parametrize("side,sign", [("long", 1.0), ("short", -1.0)])
def test_arming_is_symmetric_in_r_not_in_price(side, sign):
    """R is signed by direction, so both sides arm at the same R and the
    stop lands the trail distance on the correct side of the mark."""
    se = SpecExit(max_bars=500, timeframe="4h", trail_atr_mult=4.0,
                  has_target=False, arm_at_r=2.0)
    assert _trailing({"s1": se}, side, 1.5) == []
    moved = _trailing({"s1": se}, side, 2.5)
    assert len(moved) == 1
    mark = 100.0 + sign * 2.5 * 10.0
    assert moved[0] == pytest.approx(mark - sign * 1.0 * 4.0)


# ── the default is preserved ────────────────────────────────────────────
def test_an_omitted_arm_at_r_keeps_the_configured_default():
    se = SpecExit(max_bars=500, timeframe="4h", trail_atr_mult=4.0,
                  has_target=False)
    e = _engine({"s1": se})
    assert se.arm_at_r is None
    assert e._arm_at_r({"strategy_id": "s1"}) == e.c.trail_after_r == 1.0


def test_an_unknown_strategy_keeps_the_configured_default():
    e = _engine()
    assert e._arm_at_r({"strategy_id": "nope"}) == e.c.trail_after_r == 1.0
    assert e._arm_at_r({}) == 1.0


def test_a_spec_omitting_arm_at_r_arms_at_one_r():
    se = SpecExit(max_bars=500, timeframe="4h", trail_atr_mult=4.0,
                  has_target=False)
    assert _trailing({"s1": se}, "long", 0.9) == []
    assert _trailing({"s1": se}, "long", 1.0) != []


# ── Donchian, the one live mechanism, is unchanged ──────────────────────
def _donchian_spec():
    from trader.strategy.geometries import GEOS
    from trader.strategy.spec import StrategySpec
    return StrategySpec(
        id="auth_donchian_breakout_trail", name="Donchian Breakout Trail",
        thesis="A break of a multi-week range persists.",
        invalidation="Retire below profit factor 1.0 over 40 trades.",
        provenance={}, universe={"include": []}, timeframe="4h",
        direction="both", entry_long="close > donchian_hi(100)",
        entry_short="close < donchian_lo(100)", filters=[],
        exit=GEOS["trail"], regime_filter=[], markets=["futures"])


def test_donchian_declares_one_r_and_still_arms_at_one_r():
    """Its declared value and the old hardcoded default coincide, so wiring
    the field through must not shift it by a hair."""
    se = SpecExit.from_spec(_donchian_spec())
    assert se.arm_at_r == 1.0
    e = _engine({"auth_donchian_breakout_trail": se})
    assert e._arm_at_r({"strategy_id": "auth_donchian_breakout_trail"}) \
        == 1.0 == e.c.trail_after_r


@pytest.mark.parametrize("side", ["long", "short"])
def test_donchian_ratchets_at_exactly_the_same_r_as_before(side):
    se = SpecExit.from_spec(_donchian_spec())
    sx = {"auth_donchian_breakout_trail": se}
    kw = {"strategy_id": "auth_donchian_breakout_trail"}
    assert _trailing(sx, side, 0.99, **kw) == []
    assert _trailing(sx, side, 1.0, **kw) != []


def test_wiring_arm_at_r_left_the_rest_of_the_geometry_alone():
    """No change to trail width, stop geometry or hold."""
    se = SpecExit.from_spec(_donchian_spec())
    assert (se.max_bars, se.timeframe, se.trail_atr_mult, se.has_target,
            se.stop_atr_mult, se.stop_pct) \
        == (500, "4h", 4.0, False, 2.0, 0.0)


def test_a_spec_with_no_atr_trail_declares_no_arming_point():
    """`{"kind": "none"}` carries no trail to arm; the default stands."""
    from trader.strategy.geometries import GEOS
    from trader.strategy.spec import StrategySpec
    spec = StrategySpec(
        id="fixed", name="fixed", thesis="t", invalidation="i",
        provenance={}, universe={"include": []}, timeframe="4h",
        direction="both", entry_long="close > 0", entry_short="close < 0",
        filters=[], exit=GEOS["fixed"], regime_filter=[], markets=["futures"])
    se = SpecExit.from_spec(spec)
    assert se.arm_at_r is None
    assert _engine({"fixed": se})._arm_at_r({"strategy_id": "fixed"}) == 1.0
