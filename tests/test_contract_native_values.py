"""Native inverse units and historical research arithmetic stay distinct."""
import pytest

from trader.core.contract_values import Contract
from trader.research.compose import hedge as H


@pytest.mark.parametrize('kind,multiplier', [('linear',1.),('inverse',100.)])
@pytest.mark.parametrize('side', [-1,1])
def test_partial_exit_cashflows_plus_mark_match_native_pnl(kind,multiplier,side):
    contract=Contract(kind,multiplier)
    entry_side='BUY' if side>0 else 'SELL';exit_side='SELL' if side>0 else 'BUY'
    qty=5.;partial=2.;entry=10000.;exit_price=11000.;mark=9500.
    cash=contract.cashflow_native(qty,entry,entry_side)
    cash+=contract.cashflow_native(partial,exit_price,exit_side)
    cash+=contract.cashflow_native(qty-partial,mark,exit_side)
    expected=contract.pnl_native(side*partial,entry,exit_price)
    expected+=contract.pnl_native(side*(qty-partial),entry,mark)
    assert cash==pytest.approx(expected)


def test_inverse_profit_is_coin_until_an_explicit_reporting_mark_is_applied():
    contract=Contract('inverse',100.)
    native=contract.pnl_native(1.,10000.,11000.)
    assert native==pytest.approx(.0009090909090909091)
    assert contract.pnl_usd(1.,10000.,11000.)==pytest.approx(native*11000.)
    assert contract.notional(1.,11000.)==100.
    assert contract.settlement_notional(1.,11000.)==pytest.approx(100./11000.)


@pytest.mark.parametrize('kind,multiplier',[('linear',1.),('inverse',100.)])
def test_research_public_contract_and_original_operations_are_preserved(kind,multiplier):
    assert H.Contract is Contract
    c=H.Contract(kind,multiplier)
    for qty in (-17.25,0.,9.5):
        for p0,p1 in ((12.3,17.8),(50000.,41234.56)):
            old=qty*(p1-p0) if kind=='linear' else qty*multiplier*(1./p0-1./p1)*p1
            assert c.pnl_usd(qty,p0,p1)==old


def test_flat_native_position_needs_no_artificial_mark_price():
    c=Contract('inverse',100.)
    assert c.pnl_native(0.,0.,0.)==0.
    assert c.cashflow_native(0.,0.,'BUY')==0.


def test_contract_arithmetic_change_invalidates_frozen_research_identity(monkeypatch):
    from pathlib import Path
    from trader.research.closure import manifest
    original=Path.read_bytes
    before=manifest.evaluator_id()
    def changed(path):
        data=original(path)
        return data+b'\n# changed arithmetic\n' if path.as_posix().endswith('/core/contract_values.py') else data
    monkeypatch.setattr(Path,'read_bytes',changed)
    assert manifest.evaluator_id()!=before
