"""An ATR multiple is meaningless without the bar it was measured on.

Every ATR in the live path was read off the 15m execution frame, including
the stop and the trail of a spec validated on 4h bars. Measured on real
candles, 4h ATR runs 5-8x the 15m figure, so Donchian Breakout Trail —
validated with a 2.0x4h stop (2.12% of BTC) and a 4.0x4h trail (4.23%) —
would have traded live behind a 0.40% stop and a 0.38% trail. Four to
eleven times too tight is a different strategy, and a trend mechanism
cannot survive it.

The exit geometry (max_bars, trail mult, partial) was already spec-aware;
the FRAME those multiples were applied on was not.
"""
import json
import pytest

from trader.engine.exits import SpecExit
from trader.kernel import Kernel
from trader.strategy.spec import StrategySpec


def _spec():
    return StrategySpec.from_dict(
        json.load(open("data/authored_specs/donchian_breakout_trail.json")))


def _kernel_stub(tp_mult=4.5):
    """Only the two collaborators _protection_for touches."""
    k = Kernel.__new__(Kernel)

    class _Risk:
        sl_atr_mult = 2.5

        def protection_levels(self, price, atr, side, tp):
            d = max(atr * self.sl_atr_mult, price * 0.004)
            return ((price - d, price + atr * tp) if side == "long"
                    else (price + d, price - atr * tp))

    class _Exec:
        tp_atr_mult = tp_mult

    k.risk, k.executor = _Risk(), _Exec()
    return k


# ── SpecExit carries the entry geometry, not only the exit ─────────────
def test_spec_exit_reads_the_stop_multiple():
    se = SpecExit.from_spec(_spec())
    assert se.stop_atr_mult == 2.0
    assert se.timeframe == "4h"


def test_a_pct_stop_is_carried_separately_from_an_atr_stop():
    sp = _spec()
    sp.exit.stop = {"kind": "pct", "v": 0.03}
    se = SpecExit.from_spec(sp)
    assert se.stop_atr_mult == 0.0
    assert se.stop_pct == 0.03


# ── the stop comes from the spec, not from config ──────────────────────
def test_the_stop_uses_the_specs_multiple_not_the_config_one():
    k, se = _kernel_stub(), SpecExit.from_spec(_spec())
    price, atr4h = 77467.0, 819.89          # measured, BTC 2026-09-02
    sl, _ = k._protection_for(se, price, atr4h, "long")
    assert sl == pytest.approx(price - 2.0 * atr4h)
    # and NOT the config's 2.5
    assert sl != pytest.approx(price - 2.5 * atr4h)


def test_a_15m_atr_would_have_given_a_stop_five_times_too_tight():
    """The regression, in the numbers that produced it.

    BTC/USDT on 2026-09-02: ATR 819.89 on 4h, 105.13 on 15m, price 77467.
    The 0.4% venue floor caught the 15m figure before it got sillier still.
    """
    k, se = _kernel_stub(), SpecExit.from_spec(_spec())
    price, atr4h, atr15m = 77467.0, 819.89, 105.13
    right, _ = k._protection_for(se, price, atr4h, "long")
    wrong, _ = k._protection_for(se, price, atr15m, "long")
    assert (price - right) / price == pytest.approx(0.0212, abs=1e-3)
    assert (price - wrong) / price == pytest.approx(0.0040, abs=1e-3)
    assert (price - right) > 5 * (price - wrong)


def test_a_short_puts_the_stop_above_the_price():
    k, se = _kernel_stub(), SpecExit.from_spec(_spec())
    sl, _ = k._protection_for(se, 100.0, 2.0, "short")
    assert sl == pytest.approx(104.0)


def test_a_pct_stop_is_honoured():
    sp = _spec()
    sp.exit.stop = {"kind": "pct", "v": 0.03}
    sl, _ = _kernel_stub()._protection_for(SpecExit.from_spec(sp),
                                           100.0, 2.0, "long")
    assert sl == pytest.approx(97.0)


def test_the_venue_noise_floor_still_applies():
    sp = _spec()
    sp.exit.stop = {"kind": "pct", "v": 0.001}     # 0.1%, under the floor
    sl, _ = _kernel_stub()._protection_for(SpecExit.from_spec(sp),
                                           100.0, 2.0, "long")
    assert sl == pytest.approx(99.6)               # 0.4% floor


def test_no_spec_keeps_the_config_geometry():
    """Legacy genomes were tuned on the execution frame. Leave them there."""
    k = _kernel_stub()
    sl, tp = k._protection_for(None, 100.0, 2.0, "long")
    assert sl == pytest.approx(95.0)               # 2.5 ATR
    assert tp == pytest.approx(109.0)              # 4.5 ATR


# ── a spec with no target gets no target ───────────────────────────────
def test_a_no_target_spec_journals_no_target():
    se = SpecExit.from_spec(_spec())
    assert se.has_target is False
    _, tp = _kernel_stub()._protection_for(se, 100.0, 2.0, "long")
    assert tp == 0.0, "a fabricated level reads as one the strategy chose"


def test_a_spec_with_a_target_still_gets_one():
    sp = _spec()
    sp.exit.target = {"kind": "rr", "mult": 2.0}
    _, tp = _kernel_stub()._protection_for(SpecExit.from_spec(sp),
                                           100.0, 2.0, "long")
    assert tp == pytest.approx(109.0)


# ── the trail is measured on the same frame ────────────────────────────
def test_the_exit_engine_names_the_frame_to_trail_on():
    from trader.engine.exits import ExitEngine
    e = ExitEngine.__new__(ExitEngine)
    e.spec_exits = {"auth_donchian_breakout_trail": SpecExit.from_spec(_spec())}
    assert e.atr_timeframe(
        {"strategy_id": "auth_donchian_breakout_trail"}) == "4h"


def test_a_trade_no_spec_owns_names_no_frame():
    from trader.engine.exits import ExitEngine
    e = ExitEngine.__new__(ExitEngine)
    e.spec_exits = {}
    assert e.atr_timeframe({"strategy_id": "ematrend_g_edc6"}) is None
    assert e.atr_timeframe({}) is None
