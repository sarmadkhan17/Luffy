"""Every quantity sent to the venue must be a whole lot, and the journal
must record what the venue actually filled.

UNI/USDT trades in whole coins (precision.amount = 1). Luffy passed raw
floats and let Binance truncate, while the journal kept the untruncated
number. Each leg then rounded down independently against a size the venue
never held:

    entry   607.73360606  -> venue filled 607   (journal kept 607.73360606)
    TP1     303.86680303  -> venue sold   303   (journal kept 303.86680303)
    stop    303.86680303  -> venue sold   303
                             607 - 303 - 303  =  1 UNI stranded

That 1 coin is dust — $6.20 of notional holding $1.24 of isolated margin —
and, because it keeps the symbol present in fetch_positions(), it hid a
completed exit from the kernel for hours. Quantize at the boundary and the
residue cannot accumulate.
"""
import pytest

from tests.entry_authority_fixtures import permission
from tests.test_entry_recovery import setup  # noqa: F401  (real Risk/capability harness)

# Executor.open() now admits only an exact Risk-issued permission, so these
# tests drive the real entry boundary (tests/entry_authority_fixtures) against a
# venue that truncates to whole lots, instead of the pre-Stage-1 bare fake.
REQUESTED = 5.7          # what Risk permits; the venue's step is 1


@pytest.fixture
def lots(setup):
    ex, j, e, d = setup
    ex.amount_to_precision = lambda symbol, amount: str(int(float(amount)))
    return ex, j, e, d


def _open(e, d, amount=REQUESTED):
    kw = permission(e, d, amount=amount)
    return e.open(d, amount, 2., 95., 110., 'strategy', 'strategy', **kw)


def _reduce_only(ex):
    return [s for s in ex.sent if s[4].get('reduceOnly') and 'stopLossPrice' not in s[4]]


def test_entry_is_quantized_before_it_reaches_the_venue(lots):
    ex, j, e, d = lots
    assert _open(e, d) is not None
    entries = [s for s in ex.sent if s[1] == 'market' and not s[4].get('reduceOnly')
               and 'stopLossPrice' not in s[4]]
    assert entries[0][3] == pytest.approx(5.0), \
        f"venue was handed {entries[0][3]}, which it silently truncates to 5"


def test_the_journal_records_what_the_venue_filled(lots):
    ex, j, e, d = lots
    pos = _open(e, d)
    assert pos.amount == pytest.approx(5.0)
    row = j.query("SELECT * FROM trades WHERE id=?", (pos.id,))[0]
    assert row["amount"] == pytest.approx(5.0)
    assert row["notional_usdt"] == pytest.approx(round(5 * 100.0, 2))


def test_the_protective_stop_covers_the_whole_filled_position(lots):
    ex, j, e, d = lots
    _open(e, d)
    assert float(ex.stops[0]['quantity']) == pytest.approx(5.0), \
        "a stop for less than the position leaves the remainder naked"


def test_a_partial_sells_a_whole_number_of_lots(lots):
    ex, j, e, d = lots
    pos = _open(e, d)
    t = j.query("SELECT * FROM trades WHERE id=?", (pos.id,))[0]
    assert e.close_partial(dict(t), float(t["amount"]) * 0.5, 100.0, "tp1")
    assert _reduce_only(ex)[-1][3] == pytest.approx(2.0)
    row = j.query("SELECT * FROM trades WHERE id=?", (pos.id,))[0]
    assert row["amount"] == pytest.approx(3.0), \
        "the remainder is what the venue still holds, not 5/2"


def test_the_full_cycle_strands_nothing(lots):
    """The regression itself: open, take a half, close the rest — the
    reduce-only quantity sent equals the quantity the venue filled."""
    ex, j, e, d = lots
    pos = _open(e, d)
    t = dict(j.query("SELECT * FROM trades WHERE id=?", (pos.id,))[0])
    e.close_partial(t, float(t["amount"]) * 0.5, 100.0, "tp1")
    t = dict(j.query("SELECT * FROM trades WHERE id=?", (pos.id,))[0])
    e.close(t, 100.0, "sl_fill")
    sold = sum(s[3] for s in _reduce_only(ex))
    assert sold == pytest.approx(5.0), \
        f"{5.0 - sold} lots left on the venue as unmanaged dust"


def test_a_sub_lot_partial_is_refused_rather_than_sent_as_zero(lots):
    """Rounding 0.4 lots down to 0 would be an order the venue rejects —
    and, if it did not, a 'partial' that closes nothing while the journal
    books a fill."""
    ex, j, e, d = lots
    pos = _open(e, d)
    t = dict(j.query("SELECT * FROM trades WHERE id=?", (pos.id,))[0])
    before = len(ex.sent)
    assert e.close_partial(t, 0.4, 100.0, "tp1") is False
    assert len(ex.sent) == before, "no order may be sent for zero lots"
    row = j.query("SELECT * FROM trades WHERE id=?", (pos.id,))[0]
    assert row["amount"] == pytest.approx(5.0), "nothing was sold"


def test_a_venue_without_precision_helpers_is_left_alone():
    """Spot books, tests and backtests hand in plain fakes; quantization
    must degrade to the requested amount, never crash."""
    from trader.engine.executor import quantize

    class Bare:
        pass

    class Broken:
        def amount_to_precision(self, symbol, amount):
            raise RuntimeError("markets not loaded")

    assert quantize(Bare(), "BTC/USDT", 1.2345) == pytest.approx(1.2345)
    assert quantize(Broken(), "BTC/USDT", 1.2345) == pytest.approx(1.2345)
