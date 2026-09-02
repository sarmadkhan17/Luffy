"""A position must be managed even after its symbol leaves the universe.

Exit management and exchange-exit detection both sat inside
`for symbol in self.universe.symbols()`. A position whose symbol rotated out
of the scan therefore got no trailing stop, no time exit, no target and no
fill detection — its stop could fire on the exchange and the journal would
still call it open, forever.

This was survivable while trades lasted hours. Donchian Breakout Trail holds
for up to 500 bars — 83 days at 4h — against a universe that rescans its top
12 alts every 4 hours, so stranding is not an edge case for it, it is the
expected case.
"""
from trader.kernel import Kernel


class _Journal:
    def __init__(self, trades): self._t = trades
    def open_trades(self): return list(self._t)


class _Exits:
    def __init__(self): self.managed = []
    def atr_timeframe(self, t): return None      # no spec owns these trades
    def manage(self, t, price, atr, score):
        self.managed.append(t["symbol"])
        return None


class _Universe:
    def __init__(self, syms): self._s = syms
    def symbols(self): return list(self._s)


def _kernel(open_symbols, universe_symbols, snap_ok=True):
    k = object.__new__(Kernel)                 # bypass the heavy constructor
    k.journal = _Journal([{"id": f"p{i}", "symbol": s, "side": "long",
                           "market_type": "futures", "entry_price": 1.0,
                           "amount": 1.0, "notional_usdt": 1.0}
                          for i, s in enumerate(open_symbols)])
    k.exits = _Exits()
    k.universe = _Universe(universe_symbols)
    k.detected = []
    k._detect_exchange_exits = lambda sym: k.detected.append(sym) or 0
    k._snapshot_for = lambda sym, universe=None: (
        _Snap(sym) if snap_ok else None)
    k.cfg = {"timeframes": {"execution": "15m"}}
    return k


class _Snap:
    def __init__(self, sym):
        self.symbol, self.price = sym, 100.0
    def df(self, tf):
        import numpy as np, pandas as pd
        n = 60
        c = np.linspace(100, 110, n)
        return pd.DataFrame({"ts": pd.date_range("2026-01-01", periods=n,
                                                 freq="15min", tz="UTC"),
                             "open": c, "high": c * 1.01, "low": c * 0.99,
                             "close": c, "volume": 1.0})


def test_a_position_outside_the_universe_is_still_managed():
    k = _kernel(open_symbols=["MOVR/USDT"], universe_symbols=["BTC/USDT"])
    k._manage_orphan_positions({"BTC/USDT"})
    assert k.exits.managed == ["MOVR/USDT"]


def test_its_exchange_exit_is_still_detected():
    k = _kernel(open_symbols=["MOVR/USDT"], universe_symbols=["BTC/USDT"])
    k._manage_orphan_positions({"BTC/USDT"})
    assert k.detected == ["MOVR/USDT"], \
        "a stop that fired off-universe must still be reconciled"


def test_a_scanned_symbol_is_not_managed_twice():
    k = _kernel(open_symbols=["BTC/USDT"], universe_symbols=["BTC/USDT"])
    k._manage_orphan_positions({"BTC/USDT"})
    assert k.exits.managed == [] and k.detected == []


def test_several_orphans_are_all_managed():
    k = _kernel(open_symbols=["A/USDT", "B/USDT", "C/USDT"],
                universe_symbols=["BTC/USDT"])
    k._manage_orphan_positions({"BTC/USDT"})
    assert sorted(k.exits.managed) == ["A/USDT", "B/USDT", "C/USDT"]


def test_an_unavailable_snapshot_does_not_abort_the_other_orphans():
    """One dead symbol must not strand every other position behind it."""
    k = _kernel(open_symbols=["DEAD/USDT", "B/USDT"],
                universe_symbols=["BTC/USDT"])
    real = k._snapshot_for
    k._snapshot_for = lambda sym, universe=None: (
        None if sym == "DEAD/USDT" else real(sym))
    k._manage_orphan_positions({"BTC/USDT"})
    assert k.exits.managed == ["B/USDT"]


def test_no_open_trades_is_a_no_op():
    k = _kernel(open_symbols=[], universe_symbols=["BTC/USDT"])
    k._manage_orphan_positions({"BTC/USDT"})
    assert k.exits.managed == [] and k.detected == []
