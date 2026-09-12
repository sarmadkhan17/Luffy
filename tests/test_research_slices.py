"""The search may see the earliest 70% of the data, by the CALENDAR.

Per-symbol 70% splits leak era across symbols: WLD listed 2023-07, so 70% of
its own history ends inside the era that is meant to be unseen for BTC. One
cut date per horizon makes held-out B a genuinely later era for every market
in it.

WARMUP is a bar count, not a duration: `simulate` discards the first 210 bars
of any slice it is handed, so a slice must be long enough to survive that
before it can carry a trade at all.
"""
import pandas as pd
import pytest

from trader.research import slices
from trader.strategy.vector_backtest import WARMUP

HOUR = 3_600_000


def _frame(start_ms, n, step_ms=4 * HOUR):
    ts = pd.to_datetime([start_ms + i * step_ms for i in range(n)],
                        unit="ms", utc=True)
    return pd.DataFrame({"ts": ts, "open": 1.0, "high": 1.0, "low": 1.0,
                         "close": 1.0, "volume": 1.0})


def test_the_cut_is_one_date_for_the_whole_universe():
    frames = {"OLD": _frame(0, 1000), "YOUNG": _frame(500 * 4 * HOUR, 500)}
    cut = slices.cut_ms(frames)
    span = 999 * 4 * HOUR
    assert cut == int(0.7 * span)


def test_a_young_symbol_gets_the_short_slice_it_deserves():
    frames = {"OLD": _frame(0, 1000), "YOUNG": _frame(800 * 4 * HOUR, 200)}
    cut = slices.cut_ms(frames)
    disc = slices.discovery(frames, cut, min_bars=1)
    assert len(disc["OLD"]) == 700
    assert "YOUNG" not in disc or len(disc["YOUNG"]) == 0


def test_a_slice_too_short_to_survive_warmup_is_dropped():
    frames = {"A": _frame(0, 1000), "B": _frame(0, 1000)}
    cut = slices.cut_ms(frames)
    disc = slices.discovery(frames, cut, min_bars=WARMUP + 600)
    assert disc == {}


def test_the_discovery_slice_holds_no_bar_at_or_after_the_cut():
    frames = {"A": _frame(0, 1000)}
    cut = slices.cut_ms(frames)
    d = slices.discovery(frames, cut, min_bars=1)["A"]
    assert d["ts"].max().value // 10 ** 6 < cut


def test_held_out_b_starts_at_the_cut():
    frames = {"A": _frame(0, 1000)}
    cut = slices.cut_ms(frames)
    h = slices.heldout_b(frames, cut, min_bars=1)["A"]
    assert h["ts"].min().value // 10 ** 6 >= cut
    assert len(h) == 300


def test_the_two_slices_partition_the_frame():
    frames = {"A": _frame(0, 1000)}
    cut = slices.cut_ms(frames)
    d = slices.discovery(frames, cut, min_bars=1)["A"]
    h = slices.heldout_b(frames, cut, min_bars=1)["A"]
    assert len(d) + len(h) == 1000


def test_usable_bars_subtracts_the_warmup():
    assert slices.usable_bars(1000) == 1000 - WARMUP
    assert slices.usable_bars(10) == 0


def test_an_empty_universe_has_no_cut():
    assert slices.cut_ms({}) is None
    assert slices.cut_ms({"A": None}) is None
