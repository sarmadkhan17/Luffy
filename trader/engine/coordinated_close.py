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
from . import entry_authority as A, position_mode as PM
from .recovery import exact_order_match,TERMINAL

TABLE='coordinated_reductions'
VERSIONED_TABLE='versioned_exit_reductions'
# Both historical and current hash spellings remain bound to the root entry.
IDENTITY_FIELDS=('status','strategy_id','version_id','spec_hash','spec_sha256','install_id',
                 'exit_semantics_id','exit_state','hedge_exit_state','coordinated','native_position_side')


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
    _entry_receipt(executor,trade,identity,binding)
    return group


def _entry_receipt(executor,trade,identity,binding):
    if not binding.get('logical_id'):raise ValueError('owned_entry_binding_missing')
    rows=executor.journal.query('SELECT * FROM execution_requests WHERE logical_id=?',(binding['logical_id'],))
    if len(rows)!=1:raise ValueError('coordinated_entry_receipt_missing')
    row=rows[0];intent=json.loads(row['intent_json']);result=json.loads(row.get('result_json') or '{}')
    original=intent.get('strategy_identity') or {}
    from ..data.feed import execution_account_scope
    if (A.digest(intent)!=row['intent_hash']
            or any(identity.get(k)!=original.get(k) for k in IDENTITY_FIELDS)
            or intent.get('symbol')!=trade['symbol'] or intent.get('strategy_id')!=trade['strategy_id']
            or PM.position_side(identity)!=intent.get('position_side','BOTH')
            or result.get('position_id')!=trade['id'] or row['state']!='TERMINAL'
            or execution_account_scope(executor.ex)!=(binding.get('capability') or {}).get('account_scope')
            or getattr(executor.ex,'id',None)!='binanceusdm'):
        raise ValueError('coordinated_entry_receipt_differs')
    return intent



def single_ownership(executor,trade):
    """An existing ungrouped versioned entry, never fresh entry authority."""
    identity=json.loads(trade.get('entry_identity_json') or '{}')
    binding=identity.get('execution_binding') or {}
    if (identity.get('coordinated') or identity.get('status')!='VERIFIED'
            or any(not identity.get(k) for k in ('version_id','install_id','spec_sha256','exit_semantics_id'))):
        raise ValueError('versioned_close_entry_identity_missing')
    intent=_entry_receipt(executor,trade,identity,binding)
    if binding.get('instrument_id')!=intent.get('instrument_id') or binding.get('capability')!=intent.get('capability'):
        raise ValueError('versioned_close_entry_binding_differs')
    return identity


def ensure_versioned(journal):
    with journal._tx() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS versioned_exit_reductions(
            logical_id TEXT PRIMARY KEY,trade_id TEXT NOT NULL,group_id TEXT,
            request_json TEXT NOT NULL,state TEXT NOT NULL,order_json TEXT,result_json TEXT)""")
        db.execute('CREATE INDEX IF NOT EXISTS versioned_exit_reductions_trade ON versioned_exit_reductions(trade_id,state)')
        db.execute("CREATE INDEX IF NOT EXISTS versioned_exit_reductions_instrument ON versioned_exit_reductions(json_extract(request_json,'$.instrument_id'),state,trade_id)")


def _receipt(order):
    """Only order facts needed for exact recovery; never raw private payloads."""
    result = {key:order.get(key) for key in ('id','clientOrderId','symbol','side','type','status','reduceOnly','filled','average','price')}
    if PM.venue_side(order) != 'BOTH':
        result['positionSide'] = PM.venue_side(order)
    return result


class _Transport:
    def __init__(self,executor,request,state,order,*,table=TABLE):
        if table not in (TABLE,VERSIONED_TABLE):raise ValueError('owned_reduction_table_invalid')
        self.table=table
        self.executor=executor;self.journal=executor.journal;self.ex=executor.ex
        self.request=request;self.state=state;self.order=order

    def __getattr__(self,name):return getattr(self.ex,name)

    def _save(self,state,order=None,result=None):
        if result is None and getattr(self,'flat_evidence',None):result={'flat_evidence':self.flat_evidence}
        with self.journal._tx() as db:
            if result is None:
                old=db.execute('SELECT result_json FROM '+self.table+' WHERE logical_id=?',
                               (self.request['logical_id'],)).fetchone()
                if old and old[0]:result=json.loads(old[0])
            db.execute('UPDATE '+self.table+' SET state=?,order_json=?,result_json=? WHERE logical_id=?',
                (state,A.canonical(_receipt(order)) if order is not None else None,
                 A.canonical(result) if result is not None else None,self.request['logical_id']))
        self.state=state;self.order=order

    def _exact(self,order):
        r=self.request
        if not exact_order_match(order,client_order_id=r['client_order_id'],symbol=r['symbol'],side=r['side'],
                                  order_id=(self.order or {}).get('id'),reduce_only=True,
                                  position_side=r.get('position_side','BOTH')):
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
        observation=None
        if self.table==VERSIONED_TABLE or self.request.get('position_side', 'BOTH') != 'BOTH':
            from .coordinated_unwind import _position_observation
            observation=_position_observation(self.executor)
            if any(p.instrument_id.value==raw['instrument_id'] and p.position_present
                   and p.position_side==PM.position_side(trade) for p in observation.positions):
                raise ValueError('versioned_close_flat_position_not_proven')
            positions=[]
            request_start_ms=observation.request_start_ms;observed_ms=observation.response_received_ms
        else:
            positions=self.ex.fetch_positions()
            observed_ms=int(time.time()*1000)
        if not isinstance(positions,list):raise ValueError('coordinated_flat_position_unknown')
        for position in positions:
            if norm_symbol(position.get('symbol',''))!=norm_symbol(trade['symbol']):continue
            if PM.venue_side(position)!=PM.position_side(trade):continue
            market=self.ex.market(position['symbol'])
            if market.get('id')!=raw['record']['instrument_id']['venue_symbol']:
                raise ValueError('coordinated_close_instrument_changed')
            quantity=float(position['contracts'])
            if not math.isfinite(quantity) or quantity!=0:
                raise ValueError('coordinated_flat_position_not_proven')
        self.flat_evidence=dict(schema='coordinated-terminal-flat.v1' if self.table==TABLE else 'versioned-terminal-flat.v1',trade_id=trade['id'],
            logical_id=self.request['logical_id'],order_id=str(self.order['id']),symbol=trade['symbol'],
            account_scope=raw['account_scope'],instrument_id=raw['instrument_id'],
            request_start_ms=request_start_ms,observed_ms=observed_ms,flat=True,quantity=0.,
            source='fresh complete venue position read')
        if PM.position_side(trade) != 'BOTH':
            self.flat_evidence['position_side'] = PM.position_side(trade)
        if observation is not None:self.flat_evidence['observation_id']=observation.observation_id
        # Retain the actual flat observation before booking. A crash after the
        # booking must not erase it or substitute a later reused instrument.
        self._save(self.state,order=self.order,result={'flat_evidence':self.flat_evidence})

    def fetch_my_trades(self,*args,**kwargs):
        kwargs['since']=min(kwargs.get('since',self.request['created_ms']-60000),self.request['created_ms']-60000)
        return self.ex.fetch_my_trades(*args,**kwargs)

    def create_order(self,symbol,type_,side,amount,params=None):
        r=self.request
        if (symbol!=r['symbol'] or side!=r['side'] or type_!='market' or amount!=r['quantity']
                or (params or {}) != PM.order_params(dict(native_position_side=r.get('position_side','BOTH')), reduce_only=True)):
            raise ValueError('coordinated_reduce_request_differs')
        if self.state=='RESERVED':
            if r.get('position_side', 'BOTH') != 'BOTH':
                # Unlike netting reduceOnly, native hedge orders must omit
                # that field. Verify the held side and bound the requested
                # reduction by its current quantity before a first send.
                from .coordinated_unwind import _position_observation
                obs = _position_observation(self.executor)
                matches = [p for p in obs.positions if p.instrument_id.value == r['instrument_id']
                           and p.position_side == r['position_side'] and p.position_present]
                if (len(matches) != 1 or matches[0].side != r['position_side'].lower()
                        or r['quantity'] > matches[0].absolute_quantity):
                    raise ValueError('coordinated_native_reduction_exceeds_owned_side')
            # CAS before the call. Another process can inspect or recover,
            # but it cannot submit this logical reduction a second time.
            with self.journal._tx() as db:
                db.execute('BEGIN IMMEDIATE')
                changed=db.execute("UPDATE "+self.table+" SET state='ATTEMPTED' WHERE logical_id=? AND state='RESERVED'",
                    (r['logical_id'],)).rowcount
            if changed:
                self.state='ATTEMPTED'
                try:
                    order=self.ex.create_order(symbol,type_,side,amount,
                        params=PM.order_params(dict(native_position_side=r.get('position_side','BOTH')), reduce_only=True,
                                               client_id=r['client_order_id']))
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
        return self.resolve()

    def resolve(self):
        """Read the original order only; never submit a recovery order."""
        r=self.request;symbol=r['symbol']
        # A fresh create response is not necessarily terminal. The same exact
        # order is read on this and every later attempt until facts are known.
        order=self.ex.fetch_order((self.order or {}).get('id'),symbol,
            {} if (self.order or {}).get('id') else {'origClientOrderId':r['client_order_id']})
        order=self._exact(order)
        self._save('TERMINAL',order=order)
        return order


def close_once(executor,trade_id,group_id,logical_id,*,price_hint=0.,reason='coordinated_unwind',quantity=None,exit_decision=None):
    return _close_owned(executor,trade_id,group_id,logical_id,price_hint=price_hint,reason=reason,
                        quantity=quantity,exit_decision=exit_decision)


def close_versioned_once(executor,trade_id,logical_id,*,reason,exit_decision):
    return _close_owned(executor,trade_id,None,logical_id,reason=reason,exit_decision=exit_decision,
                        table=VERSIONED_TABLE)


def _close_owned(executor,trade_id,group_id,logical_id,*,price_hint=0.,reason='coordinated_unwind',quantity=None,exit_decision=None,table=TABLE):
    """Close one existing leg once. Unknown outcomes remain pending.

    A confirmed partial fill gets a new logical reduction only after its exact
    booking is proven; callers cannot turn a timeout into a fresh request.
    """
    if table==TABLE:ensure(executor.journal)
    elif table==VERSIONED_TABLE:ensure_versioned(executor.journal)
    else:raise ValueError('owned_reduction_table_invalid')
    with executor._entry_lock,executor._lock_for('coordinated:'+trade_id):
        rows=executor.journal.query('SELECT * FROM trades WHERE id=?',(trade_id,))
        if len(rows)!=1:raise ValueError('coordinated_trade_missing')
        trade=dict(rows[0])
        if table==TABLE:ownership(executor,trade,group_id)
        else:single_ownership(executor,trade)
        with executor.journal._tx() as db:
            db.execute('BEGIN IMMEDIATE')
            old=db.execute('SELECT * FROM '+table+' WHERE logical_id=?',(logical_id,)).fetchone()
            if old is None:
                if trade['status']!='open':return dict(status='ALREADY_CLOSED',submitted=False)
                # No renamed retry can bypass an earlier ambiguous reduction.
                pending=db.execute("SELECT logical_id FROM "+table+" WHERE trade_id=? AND state NOT IN ('BOOKED','REFUSED','NO_FILL') LIMIT 1",(trade_id,)).fetchone()
                if pending:raise ValueError('coordinated_previous_reduction_pending')
                from .executor import quantize
                requested=float(trade['amount']) if quantity is None else float(quantity)
                if not math.isfinite(requested) or not 0<requested<=float(trade['amount']):
                    raise ValueError('coordinated_reduce_quantity_invalid')
                quantity=quantize(executor.ex,trade['symbol'],requested)
                if not math.isfinite(quantity) or not 0<quantity<=requested:
                    raise ValueError('coordinated_reduce_quantity_invalid')
                request=dict(schema='coordinated-reduce.v1' if table==TABLE else 'versioned-owned-reduce.v1',logical_id=logical_id,group_id=group_id,trade_id=trade_id,
                    symbol=trade['symbol'],side='sell' if trade['side']=='long' else 'buy',quantity=quantity,
                    before_quantity=float(trade['amount']),client_order_id=('lc_' if table==TABLE else 'lv_')+A.digest(logical_id)[:28],
                    created_ms=int(time.time()*1000),reason=reason)
                if PM.position_side(trade) != 'BOTH':
                    request['position_side'] = PM.position_side(trade)
                if table==VERSIONED_TABLE or PM.position_side(trade) != 'BOTH':
                    request['instrument_id']=json.loads(trade['entry_identity_json'])['execution_binding']['instrument_id']
                if exit_decision is not None:request['strategy_exit_decision']=exit_decision
                db.execute('INSERT INTO '+table+' VALUES(?,?,?,?,?,?,?)',
                    (logical_id,trade_id,group_id,A.canonical(request),'RESERVED',None,None))
                state='RESERVED';order=None
            else:
                if old['trade_id']!=trade_id or old['group_id']!=group_id:
                    raise ValueError('coordinated_reduce_identity_conflict')
                request=json.loads(old['request_json']);state=old['state'];order=json.loads(old['order_json']) if old['order_json'] else None
                if table==VERSIONED_TABLE and (request.get('reason')!=reason
                        or request.get('strategy_exit_decision')!=exit_decision):
                    raise ValueError('versioned_reduce_decision_conflict')
                if state in ('BOOKED','REFUSED','NO_FILL'):
                    return dict(status=state,submitted=False)
        transport=_Transport(executor,request,state,order,table=table)
        if table==VERSIONED_TABLE and state!='RESERVED':
            order=transport.resolve()
            if order['filled']==0:
                transport._save('NO_FILL',order=order,result={'fill_claim':False})
                return dict(status='NO_FILL',submitted=False)
        # Accounting receipts, not a changed amount alone, prove booking if
        # the process died after Executor committed but before BOOKED here.
        if order and _booked(executor.journal,trade_id,str(order.get('id') or '')):
            transport=_Transport(executor,request,state,order,table=table);transport._save('BOOKED',order=order)
            return dict(status='BOOKED',submitted=False)
        if trade['status']!='open':return dict(status='EXTERNAL_CLOSE_REQUIRES_RECONCILIATION',submitted=False)
        if table==VERSIONED_TABLE and not math.isclose(float(trade['amount']),request['before_quantity'],rel_tol=1e-9,abs_tol=1e-12):
            return dict(status='EXTERNAL_CLOSE_REQUIRES_RECONCILIATION',submitted=False)
        if request.get('strategy_exit_decision') is not None:
            decision=request['strategy_exit_decision'];trade['_strategy_exit_evidence']=decision
            # The initiating leg's benchmark is not the other leg's price.
            reference=decision.get('decision_reference')
            if decision.get('source_trade_id')==trade_id and reference is not None:
                price_hint=float(A.positive(reference['price']))
                trade['_execution_reference']=reference
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
                or proof.get('position_side','BOTH')!=request.get('position_side','BOTH')
                or proof.get('logical_id')!=row['logical_id'] or proof.get('trade_id')!=trade_id
                or str(proof.get('order_id'))!=str(order_id) or proof.get('symbol')!=request['symbol']):
            raise ValueError('coordinated_terminal_flat_receipt_differs')
        return proof
    return None


def close_hedge_once(executor,trade_id,group_id,namespace,*,reason='strategy_hedge_stop',exit_evidence=None):
    """One owned hedge reduction per tick, including partial/unknown recovery."""
    from .coordinated_unwind import active,_exit_decision
    ensure(executor.journal)
    with executor._entry_lock,executor._lock_for('coordinated-hedge:'+trade_id):
        rows=executor.journal.query('SELECT * FROM trades WHERE id=?',(trade_id,))
        if len(rows)!=1:raise ValueError('coordinated_trade_missing')
        trade=rows[0]
        if ownership(executor,trade,group_id)['leg']!='hedge':
            raise ValueError('coordinated_hedge_only_close_required')
        if active(executor.journal,group_id):return dict(status='GROUP_UNWINDING',trade_id=trade_id)
        previous=executor.journal.query('SELECT * FROM coordinated_reductions WHERE trade_id=? AND group_id=?',
                                        (trade_id,group_id))
        prefix='hs_'+A.digest(namespace)[:24]+'_'
        own=[r for r in previous if r['logical_id'].startswith(prefix)]
        if trade['status']!='open':
            proved=any((json.loads(r['result_json'] or '{}').get('flat_evidence') or {}).get('trade_id')==trade_id
                       for r in own if r['state']=='BOOKED')
            return dict(status='CLOSED' if proved else 'ALREADY_CLOSED',trade_id=trade_id)
        decision=_exit_decision(trade,exit_evidence,group_id,reason,scope='HEDGE_ONLY')
        pending=[r for r in previous if r['state'] not in ('BOOKED','REFUSED')]
        if len(pending)>1:raise ValueError('coordinated_multiple_pending_reductions')
        logical=pending[0]['logical_id'] if pending else prefix+str(len(own))
        reduction=close_once(executor,trade_id,group_id,logical,reason=reason,exit_decision=decision)
        closed=executor.journal.query('SELECT status FROM trades WHERE id=?',(trade_id,))[0]['status']=='closed'
        return dict(status=('CLOSED' if closed and reduction['status']=='BOOKED' else
                            'REDUCING' if reduction['status']=='BOOKED' else reduction['status']),
                    trade_id=trade_id,reduction=reduction)
