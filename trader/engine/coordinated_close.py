"""Idempotent reduce-only transport for an existing coordinated venue leg.

Uses Executor.close for fills, booking and stop handling. The proxy adds a
persisted exact client ID and never repeats an ambiguous submission. It does
not create/approve a version, allow an entry or alter account controls.
"""
from copy import copy
import json
import math
import time
from ccxt import InvalidOrder,InsufficientFunds
from . import entry_authority as A
from .recovery import exact_order_match,TERMINAL

TABLE='coordinated_reductions'
# Both historical and current hash spellings remain bound to the root entry.
IDENTITY_FIELDS=('status','strategy_id','version_id','spec_hash','spec_sha256','install_id',
                 'exit_semantics_id','exit_state','coordinated')


def ensure(journal):
    with journal._tx() as db:
        db.execute('''CREATE TABLE IF NOT EXISTS coordinated_reductions(
            logical_id TEXT PRIMARY KEY,trade_id TEXT NOT NULL,group_id TEXT NOT NULL,
            request_json TEXT NOT NULL,state TEXT NOT NULL,order_json TEXT,result_json TEXT)''')
        db.execute('CREATE INDEX IF NOT EXISTS coordinated_reductions_trade ON coordinated_reductions(trade_id,group_id,state)')


def ownership(executor,trade,group_id):
    """Authenticate the leg from the existing Risk/entry receipt, not its name."""
    identity=json.loads(trade.get('entry_identity_json') or '{}')
    group=identity.get('coordinated') or {};binding=identity.get('execution_binding') or {}
    if (group.get('group_id')!=group_id or group.get('leg') not in ('primary','hedge')
            or not binding.get('logical_id')):
        raise ValueError('coordinated_trade_ownership_unproven')
    rows=executor.journal.query('SELECT * FROM execution_requests WHERE logical_id=?',(binding['logical_id'],))
    if len(rows)!=1:raise ValueError('coordinated_entry_receipt_missing')
    row=rows[0];intent=json.loads(row['intent_json']);result=json.loads(row.get('result_json') or '{}')
    original=intent.get('strategy_identity') or {}
    from ..data.feed import execution_account_scope
    if (A.digest(intent)!=row['intent_hash']
            or any(identity.get(k)!=original.get(k) for k in IDENTITY_FIELDS)
            or intent.get('symbol')!=trade['symbol'] or intent.get('strategy_id')!=trade['strategy_id']
            or result.get('position_id')!=trade['id'] or row['state']!='TERMINAL'
            or execution_account_scope(executor.ex)!=(binding.get('capability') or {}).get('account_scope')
            or getattr(executor.ex,'id',None)!='binanceusdm'):
        raise ValueError('coordinated_entry_receipt_differs')
    return group


def _receipt(order):
    """Only order facts needed for exact recovery; never raw private payloads."""
    return {key:order.get(key) for key in ('id','clientOrderId','symbol','side','type','status','reduceOnly','filled','average','price')}


class _Transport:
    def __init__(self,executor,request,state,order):
        self.executor=executor;self.journal=executor.journal;self.ex=executor.ex
        self.request=request;self.state=state;self.order=order

    def __getattr__(self,name):return getattr(self.ex,name)

    def _save(self,state,order=None,result=None):
        if result is None and getattr(self,'flat_evidence',None):result={'flat_evidence':self.flat_evidence}
        with self.journal._tx() as db:
            if result is None:
                old=db.execute('SELECT result_json FROM coordinated_reductions WHERE logical_id=?',
                               (self.request['logical_id'],)).fetchone()
                if old and old[0]:result=json.loads(old[0])
            db.execute('UPDATE coordinated_reductions SET state=?,order_json=?,result_json=? WHERE logical_id=?',
                (state,A.canonical(_receipt(order)) if order is not None else None,
                 A.canonical(result) if result is not None else None,self.request['logical_id']))
        self.state=state;self.order=order

    def _exact(self,order):
        r=self.request
        if not exact_order_match(order,client_order_id=r['client_order_id'],symbol=r['symbol'],side=r['side'],
                                  order_id=(self.order or {}).get('id'),reduce_only=True):
            raise ValueError('coordinated_reduce_order_identity_unknown')
        filled=order.get('filled')
        if (isinstance(filled,bool) or not isinstance(filled,(int,float)) or not math.isfinite(filled)
                or not 0<=filled<=r['quantity']):
            raise ValueError('coordinated_reduce_fill_quantity_unknown')
        if str(order.get('status','')).lower() not in TERMINAL:
            raise ValueError('coordinated_reduce_order_not_terminal')
        return order

    def confirm_flat(self,trade):
        from ..data.feed import execution_account_scope
        from ..core.types import norm_symbol
        raw=json.loads(trade['entry_identity_json'])['execution_binding']['capability']
        if execution_account_scope(self.ex)!=raw['account_scope']:
            raise ValueError('coordinated_close_account_changed')
        request_start_ms=int(time.time()*1000)
        positions=self.ex.fetch_positions()
        observed_ms=int(time.time()*1000)
        if not isinstance(positions,list):raise ValueError('coordinated_flat_position_unknown')
        for position in positions:
            if norm_symbol(position.get('symbol',''))!=norm_symbol(trade['symbol']):continue
            market=self.ex.market(position['symbol'])
            if market.get('id')!=raw['record']['instrument_id']['venue_symbol']:
                raise ValueError('coordinated_close_instrument_changed')
            quantity=float(position['contracts'])
            if not math.isfinite(quantity) or quantity!=0:
                raise ValueError('coordinated_flat_position_not_proven')
        self.flat_evidence=dict(schema='coordinated-terminal-flat.v1',trade_id=trade['id'],
            logical_id=self.request['logical_id'],order_id=str(self.order['id']),symbol=trade['symbol'],
            account_scope=raw['account_scope'],instrument_id=raw['instrument_id'],
            request_start_ms=request_start_ms,observed_ms=observed_ms,flat=True,quantity=0.,
            source='fresh complete venue position read')
        # Retain the actual flat observation before booking. A crash after the
        # booking must not erase it or substitute a later reused instrument.
        self._save(self.state,order=self.order,result={'flat_evidence':self.flat_evidence})

    def fetch_my_trades(self,*args,**kwargs):
        kwargs['since']=min(kwargs.get('since',self.request['created_ms']-60000),self.request['created_ms']-60000)
        return self.ex.fetch_my_trades(*args,**kwargs)

    def create_order(self,symbol,type_,side,amount,params=None):
        r=self.request
        if (symbol!=r['symbol'] or side!=r['side'] or type_!='market' or amount!=r['quantity']
                or (params or {}).get('reduceOnly') is not True):
            raise ValueError('coordinated_reduce_request_differs')
        if self.state=='RESERVED':
            # CAS before the call. Another process can inspect or recover,
            # but it cannot submit this logical reduction a second time.
            with self.journal._tx() as db:
                db.execute('BEGIN IMMEDIATE')
                changed=db.execute("UPDATE coordinated_reductions SET state='ATTEMPTED' WHERE logical_id=? AND state='RESERVED'",
                    (r['logical_id'],)).rowcount
            if changed:
                self.state='ATTEMPTED'
                try:
                    order=self.ex.create_order(symbol,type_,side,amount,
                        params={'reduceOnly':True,'newClientOrderId':r['client_order_id']})
                except (InvalidOrder,InsufficientFunds) as exc:
                    self._save('REFUSED',result=dict(reason=type(exc).__name__,submission='DEFINITIVELY_REFUSED'))
                    raise
                except Exception:
                    # Accepted-then-timeout remains ATTEMPTED. Recovery is a
                    # read of this client ID; never another create_order.
                    raise
                self._save('ACKNOWLEDGED',order=order)
            else:self.state='ATTEMPTED'
        if self.state=='REFUSED':raise ValueError('coordinated_reduce_definitively_refused')
        # A fresh create response is not necessarily terminal. The same exact
        # order is read on this and every later attempt until facts are known.
        order=self.ex.fetch_order((self.order or {}).get('id'),symbol,
            {} if (self.order or {}).get('id') else {'origClientOrderId':r['client_order_id']})
        order=self._exact(order)
        self._save('TERMINAL',order=order)
        return order


def close_once(executor,trade_id,group_id,logical_id,*,price_hint=0.,reason='coordinated_unwind',quantity=None):
    """Close one existing leg once. Unknown outcomes remain pending.

    A confirmed partial fill gets a new logical reduction only after its exact
    booking is proven; callers cannot turn a timeout into a fresh request.
    """
    ensure(executor.journal)
    with executor._entry_lock,executor._lock_for('coordinated:'+trade_id):
        rows=executor.journal.query('SELECT * FROM trades WHERE id=?',(trade_id,))
        if len(rows)!=1:raise ValueError('coordinated_trade_missing')
        trade=dict(rows[0]);ownership(executor,trade,group_id)
        with executor.journal._tx() as db:
            db.execute('BEGIN IMMEDIATE')
            old=db.execute('SELECT * FROM coordinated_reductions WHERE logical_id=?',(logical_id,)).fetchone()
            if old is None:
                if trade['status']!='open':return dict(status='ALREADY_CLOSED',submitted=False)
                # No renamed retry can bypass an earlier ambiguous reduction.
                pending=db.execute("SELECT logical_id FROM coordinated_reductions WHERE trade_id=? AND state NOT IN ('BOOKED','REFUSED') LIMIT 1",(trade_id,)).fetchone()
                if pending:raise ValueError('coordinated_previous_reduction_pending')
                from .executor import quantize
                requested=float(trade['amount']) if quantity is None else float(quantity)
                if not math.isfinite(requested) or not 0<requested<=float(trade['amount']):
                    raise ValueError('coordinated_reduce_quantity_invalid')
                quantity=quantize(executor.ex,trade['symbol'],requested)
                if not math.isfinite(quantity) or not 0<quantity<=requested:
                    raise ValueError('coordinated_reduce_quantity_invalid')
                request=dict(schema='coordinated-reduce.v1',logical_id=logical_id,group_id=group_id,trade_id=trade_id,
                    symbol=trade['symbol'],side='sell' if trade['side']=='long' else 'buy',quantity=quantity,
                    before_quantity=float(trade['amount']),client_order_id='lc_'+A.digest(logical_id)[:28],
                    created_ms=int(time.time()*1000),reason=reason)
                db.execute('INSERT INTO coordinated_reductions VALUES(?,?,?,?,?,?,?)',
                    (logical_id,trade_id,group_id,A.canonical(request),'RESERVED',None,None))
                state='RESERVED';order=None
            else:
                if old['trade_id']!=trade_id or old['group_id']!=group_id:
                    raise ValueError('coordinated_reduce_identity_conflict')
                request=json.loads(old['request_json']);state=old['state'];order=json.loads(old['order_json']) if old['order_json'] else None
                if state in ('BOOKED','REFUSED'):
                    return dict(status=state,submitted=False)
        # Accounting receipts, not a changed amount alone, prove booking if
        # the process died after Executor committed but before BOOKED here.
        if order and _booked(executor.journal,trade_id,str(order.get('id') or '')):
            transport=_Transport(executor,request,state,order);transport._save('BOOKED',order=order)
            return dict(status='BOOKED',submitted=False)
        if trade['status']!='open':return dict(status='EXTERNAL_CLOSE_REQUIRES_RECONCILIATION',submitted=False)
        transport=_Transport(executor,request,state,order)
        scoped=copy(executor);scoped.ex=transport
        def complete_fills(symbol,order_id,since):
            rows=scoped.__class__._order_fills(scoped,symbol,order_id,since)
            if rows and transport.order:
                quantity=sum(float(row['amount']) for row in rows)
                if not math.isclose(quantity,float(transport.order['filled']),rel_tol=1e-9,abs_tol=1e-9):
                    return [] # order quantity stays known; incomplete financial fills are not complete evidence
            return rows
        scoped._order_fills=complete_fills
        scoped.close(trade,exit_price_hint=price_hint,reason=reason,exact_reduction=True,
                     requested_quantity=request['quantity'])
        if transport.order and _booked(executor.journal,trade_id,str(transport.order.get('id') or '')):
            transport._save('BOOKED',order=transport.order)
        return dict(status=transport.state,submitted=state=='RESERVED',client_order_id=request['client_order_id'])


def _booked(journal,trade_id,order_id):
    if not order_id:return False
    # The existing Executor's evidence writer is the authority. Do not infer
    # booking from flat positions, P&L or a quantity changed by another exit.
    from .booking import replay
    for row in journal.query('SELECT payload FROM trade_accounting_bookings WHERE trade_id=? ORDER BY id',(trade_id,)):
        receipt=json.loads(row['payload']);replay(receipt)
        if receipt['evidence'].get('order_id')==order_id and (receipt['kind'].startswith('close:')
                or receipt['evidence'].get('purpose')=='final_exit_partial_fill'):
            return True
    return False


def retained_terminal_flat(journal,trade_id,group_id,order_id):
    """Historical close proof only; never establishes current position truth."""
    if not journal.query("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(TABLE,)):
        return None
    for row in journal.query('SELECT * FROM coordinated_reductions WHERE trade_id=? AND group_id=? AND state=?',
                             (trade_id,group_id,'BOOKED')):
        request=json.loads(row['request_json']);order=json.loads(row['order_json'] or '{}')
        proof=json.loads(row['result_json'] or '{}').get('flat_evidence')
        if str(order.get('id'))!=str(order_id):continue
        if not proof or proof.get('schema')!='coordinated-terminal-flat.v1':return None
        if (request['logical_id']!=row['logical_id'] or request['trade_id']!=trade_id or request['group_id']!=group_id
                or proof.get('logical_id')!=row['logical_id'] or proof.get('trade_id')!=trade_id
                or str(proof.get('order_id'))!=str(order_id) or proof.get('symbol')!=request['symbol']):
            raise ValueError('coordinated_terminal_flat_receipt_differs')
        return proof
    return None
