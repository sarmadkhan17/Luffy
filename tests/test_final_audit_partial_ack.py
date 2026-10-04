"""Acknowledged working reductions remain pending until their exact terminal fill."""
import pytest
from tests.test_final_audit_partial_intent import book,row
from trader.core.journal import Journal
from trader.core.config import load_config
from trader.core.types import MarketType
from trader.engine.executor import Executor

@pytest.mark.parametrize('terminal,total', [('closed',5),('canceled',2)])
def test_acknowledged_working_partial_waits_for_terminal_truth_after_reopen(book,terminal,total):
    venue,j,e=book
    venue.timeout=False;venue.filled=2;venue.status='open'
    assert e.close_partial(row(j),5) is False
    assert e.recovery_pending() and row(j)['tp1_done']==0
    assert row(j)['amount']==10 and venue.quantity==8
    reopened=Executor(venue,Journal(j.db_path),load_config(),MarketType.FUTURES)
    reopened.fill_retry_s=0
    assert reopened.close_partial(row(j),5) is False
    venue.quantity=10-total
    venue.order.update(filled=total,status=terminal)
    reopened.recover_entries()
    assert row(j)['amount']==10-total and row(j)['tp1_done']==1
    assert not reopened.recovery_pending() and len(venue.calls)==1
    pnl=row(j)['realized_pnl'];reopened.recover_entries()
    assert row(j)['realized_pnl']==pnl and len(venue.calls)==1
