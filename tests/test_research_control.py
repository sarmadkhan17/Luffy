"""A gate that cannot see the incumbent inside a window cannot refute a
challenger inside it.

Measured 2026-09-11: the positioning screen found nothing, and
CONTROL_oi_window — the known-good rule confined to the same 333-day window —
read discovery p=2.5e-01 against its usual 3.5e-04. So "no positioning
mechanism survives" was a statement about the window's power, not about the
market, and the family is UNTESTED at that depth rather than refuted.

The control runs on the incumbent's DECLARED 16, not on the discovery
universe, because on the discovery universe the incumbent is itself measured
as no-edge (p=2.3e-01). The question a control answers is "could an edge of
known size register in a window this short" — and the declared 16 is where
that edge is known to have a size.
"""
import pytest

from trader.research import control
from trader.research.combo import MAX_PARTS
from trader.strategy.compile import compile_spec


def test_the_channel_is_duration_matched_across_horizons():
    """100 bars of 4h is ~17 days; the same duration at 1h is 400 bars."""
    assert control.CHANNEL["4h"] == 100
    assert control.CHANNEL["1h"] == 400
    assert control.CHANNEL["15m"] == 1600


def test_the_plain_window_control_is_the_incumbent_rule():
    c = control.control_combo("4h", "ohlcv")
    assert c.long == "close > donchian_hi(100)"
    assert c.short == "close < donchian_lo(100)"
    assert c.round == "control"
    compile_spec(c.to_spec())


def test_a_restricted_window_masks_the_control_to_that_window():
    c = control.control_combo("4h", "open_interest")
    assert "oi_z(360) > -99" in c.long
    assert "donchian_hi(100)" in c.long
    compile_spec(c.to_spec())


def test_a_reference_window_masks_on_the_reference_itself():
    c = control.control_combo("4h", "ref:spx_1h")
    assert 'ref("spx_1h", close) > -99' in c.long
    compile_spec(c.to_spec())


def test_a_multi_requirement_window_masks_on_each():
    c = control.control_combo("4h", "funding|ref:btcdom")
    assert "funding_z(360) > -99" in c.long
    assert 'ref("btcdom", close) > -99' in c.long


def test_a_window_needing_more_masks_than_a_rule_may_hold_is_refused():
    window = "|".join(["funding", "basis", "open_interest",
                       "ls_account_ratio", "ref:spx", "ref:vix"])
    assert control.control_combo("4h", window) is None


def test_an_unknown_requirement_has_no_mask():
    assert control.mask_part("taker_ratio") is not None
    assert control.mask_part("not_a_series") is None


def test_the_incumbent_universe_is_the_sixteen_it_declares():
    u = control.INCUMBENT_UNIVERSE
    assert len(u) == 16
    for s in ("BTC/USDT", "HYPE/USDT", "TAO/USDT"):
        assert s in u


class _Journal:
    """Just enough journal to answer the one query control.py makes."""

    def __init__(self, rows):
        self._rows = rows

    def query(self, _sql, _params=()):
        return self._rows


def _row(symbols):
    import json
    return [{"spec_json": json.dumps({"universe": {"include": symbols}})}]


def test_the_book_s_own_declared_universe_wins_when_it_is_full():
    syms = [f"S{i}/USDT" for i in range(16)]
    assert control.incumbent_universe(_Journal(_row(syms))) == syms


def test_a_truncated_universe_falls_back_rather_than_weakening_the_control():
    """8 symbols cannot reach p<0.01 at all, so a control run on them would
    read as unpowered forever and label every window UNDERPOWERED."""
    short = [f"S{i}/USDT" for i in range(8)]
    assert control.incumbent_universe(_Journal(_row(short))) == \
        list(control.INCUMBENT_UNIVERSE)


def test_a_malformed_or_missing_row_falls_back_without_raising():
    for rows in ([], [{"spec_json": ""}], [{"spec_json": "{not json"}],
                 [{"spec_json": "{}"}]):
        assert control.incumbent_universe(_Journal(rows)) == \
            list(control.INCUMBENT_UNIVERSE)


def test_a_journal_that_raises_falls_back():
    class _Boom:
        def query(self, *_a, **_k):
            raise RuntimeError("database is locked")

    assert control.incumbent_universe(_Boom()) == \
        list(control.INCUMBENT_UNIVERSE)


def test_powered_reads_the_gate_the_admission_uses():
    assert control.powered({"consistency_p": 0.0005}, max_p=0.01)
    assert not control.powered({"consistency_p": 0.25}, max_p=0.01)
    assert not control.powered({"consistency_p": None}, max_p=0.01)


def test_a_negative_in_an_unpowered_window_is_labelled_underpowered():
    assert control.label({"verdict": "scored", "consistency_p": 0.4},
                         is_powered=False) == "underpowered"
    assert control.label({"verdict": "scored", "consistency_p": 0.4},
                         is_powered=True) == "no_edge"
    assert control.label({"verdict": "scored", "consistency_p": 0.001},
                         is_powered=False) == "scored"
