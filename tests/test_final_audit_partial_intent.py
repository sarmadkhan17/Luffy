"""Durable reduce-only TP1 execution intent and UNKNOWN reconciliation."""
from types import SimpleNamespace
import json
import threading
import pytest
from ccxt import OrderNotFound
from trader.core.config import load_config
from trader.core.journal import Journal
from trader.core.types import MarketType, Position, Side
from trader.engine.executor import Executor


class Venue:
    def __init__(self, *, timeout=True, filled=5, status='closed'):
        self.quantity=10.;self.calls=[];self.order=None;self.timeout=timeout
        self.filled=filled;self.status=status;self.unavailable=False
    def create_order(self,symbol,kind,side,amount,params):
        assert params['reduceOnly'] is True
        self.calls.append((amount,dict(params)))
        filled=min(self.quantity, self.filled, amount)
        self.quantity-=filled
        self.order=dict(id='order-1',symbol=symbol,side=side,type=kind,amount=amount,
            filled=filled,average=110,status=self.status,reduceOnly=True,
            clientOrderId=params.get('newClientOrderId'))
        if self.timeout: raise TimeoutError('executed then response lost')
        return self.order
    def fetch_order(self,oid,symbol,params=None):
        if self.unavailable or self.order is None: raise OrderNotFound('unconfirmed')
        if oid: assert oid==self.order['id']
        else: assert params['origClientOrderId']==self.order['clientOrderId']
        return dict(self.order)
    def fetch_positions(self,symbols):
        return [dict(symbol='BTC/USDT:USDT',side='long',contracts=self.quantity)]
    def fetch_my_trades(self,*a,**kw):return []


@pytest.fixture
def book(tmp_path):
    j=Journal(tmp_path/'journal.db')
    j.add_trade(Position(id='trade',symbol='BTC/USDT',side=Side.LONG,amount=10,
        entry_price=100,notional_usdt=1000,stop_loss=95,market_type='futures',exec_mode='live'))
    venue=Venue()
    executor=Executor(venue,j,load_config(),MarketType.FUTURES);executor.fill_retry_s=0
    return venue,j,executor


def row(j):return dict(j.query("SELECT * FROM trades WHERE id='trade'")[0])


def test_lost_response_restart_reconciles_exact_intent_without_second_reduction(book):
    venue,j,e=book
    assert e.close_partial(row(j),5,110,'tp1') is False
    assert venue.quantity==5 and row(j)['amount']==10
    reopened=Executor(venue,Journal(j.db_path),load_config(),MarketType.FUTURES);reopened.fill_retry_s=0
    assert reopened.close_partial(row(j),5,110,'tp1') is True
    assert len(venue.calls)==1 and venue.quantity==5
    settled=row(j)
    assert settled['amount']==5 and settled['tp1_done']==1
    pnl=settled['realized_pnl']
    assert reopened.close_partial(row(j),2.5,110,'tp1') is True
    assert row(j)['realized_pnl']==pnl and len(venue.calls)==1
    intents=j.query('SELECT * FROM partial_exit_intents')
    assert len(intents)==1 and intents[0]['state']=='CONSUMED'
    assert venue.calls[0][1]['newClientOrderId']==intents[0]['client_order_id']


def test_position_reduction_alone_does_not_resolve_unknown_exact_order(book):
    venue,j,e=book
    assert e.close_partial(row(j),5) is False
    venue.unavailable=True
    for _ in range(2): assert e.close_partial(row(j),5) is False
    assert len(venue.calls)==1 and row(j)['tp1_done']==0
    venue.unavailable=False
    assert e.close_partial(row(j),5) is True
    assert venue.quantity==5 and len(venue.calls)==1


def test_live_partial_order_is_not_repeated_and_terminal_partial_consumes_once(book):
    venue,j,e=book;venue.filled=2;venue.status='open'
    assert e.close_partial(row(j),5) is False
    assert e.close_partial(row(j),5) is False
    assert row(j)['amount']==10 and len(venue.calls)==1
    venue.order['status']='canceled'
    assert e.close_partial(row(j),5) is True
    assert row(j)['amount']==8 and row(j)['tp1_done']==1 and venue.quantity==8
    assert e.close_partial(row(j),4) is True
    assert len(venue.calls)==1


def test_success_and_acknowledgement_write_failure_are_restart_safe(book,monkeypatch):
    from trader.engine import partial_intent as P
    venue,j,e=book;venue.timeout=False
    original=P.acknowledge
    monkeypatch.setattr(P,'acknowledge',lambda *a:(_ for _ in ()).throw(OSError('lost ack write')))
    assert e.close_partial(row(j),5) is False
    assert venue.quantity==5
    monkeypatch.setattr(P,'acknowledge',original)
    reopened=Executor(venue,Journal(j.db_path),load_config(),MarketType.FUTURES);reopened.fill_retry_s=0
    assert reopened.close_partial(row(j),5) is True
    assert len(venue.calls)==1 and row(j)['amount']==5


def test_concurrent_executors_share_one_durable_partial_intent(book):
    venue,j,e=book
    started=threading.Event();release=threading.Event()
    original=venue.create_order
    def slow(*a,**kw):
        started.set();assert release.wait(10)
        return original(*a,**kw)
    venue.create_order=slow
    worker=threading.Thread(target=lambda:e.close_partial(row(j),5))
    worker.start();assert started.wait(10)
    try:
        other=Executor(venue,Journal(j.db_path),load_config(),MarketType.FUTURES);other.fill_retry_s=0
        assert other.close_partial(row(j),5) is False
    finally:
        release.set();worker.join(10)
    assert not worker.is_alive() and len(venue.calls)==1


def test_normal_recovery_caller_after_restart_consumes_intent_and_barrier(book):
    venue,j,e=book
    assert e.close_partial(row(j),5) is False
    assert e.recovery_pending()
    e=Executor(venue,Journal(j.db_path),load_config(),MarketType.FUTURES);e.fill_retry_s=0
    e.recover_entries()
    assert row(j)['tp1_done']==1 and row(j)['amount']==5
    assert not e.recovery_pending() and len(venue.calls)==1
    e.recover_entries()
    assert len(venue.calls)==1


def test_prior_venue_reconciliation_alignment_is_consumed_without_double_pnl(book):
    venue,j,e=book
    assert not e.close_partial(row(j),5)
    j.align_trade_amount('trade',5,500,pnl_total=40)
    e.recover_entries()
    assert row(j)['amount']==5 and row(j)['tp1_done']==1
    assert row(j)['realized_pnl']==40 and len(venue.calls)==1
