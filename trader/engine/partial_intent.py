"""Durable single-TP1 intent. UNKNOWN never grants another submission.

SQLite reservation is the cross-process admission boundary. Recovery queries
one exact client order and current venue position; it never sends an order.
The existing exit plan has one TP1 per trade, so recalculated retry quantities
and reason text cannot manufacture another logical reduction.
"""
import hashlib
import json
import math
import time
from ..core.types import norm_symbol

TERMINAL = {'closed', 'canceled', 'expired', 'rejected'}


def ensure(journal):
    with journal._tx() as db:
        db.execute('''CREATE TABLE IF NOT EXISTS partial_exit_intents(
            trade_id TEXT PRIMARY KEY, state TEXT NOT NULL,
            client_order_id TEXT NOT NULL UNIQUE, payload TEXT NOT NULL)''')


def reserve(executor, trade, requested):
    journal = executor.journal
    ensure(journal)
    with journal._tx() as db:
        db.execute('BEGIN IMMEDIATE')
        old = db.execute('SELECT * FROM partial_exit_intents WHERE trade_id=?', (trade['id'],)).fetchone()
        if old:
            return dict(json.loads(old['payload']), state=old['state']), False
        current = db.execute('SELECT * FROM trades WHERE id=?', (trade['id'],)).fetchone()
        if (current is None or current['status'] != 'open' or current['tp1_done']
                or current['symbol'] != trade['symbol'] or current['side'] != trade['side']):
            raise ValueError('partial_trade_not_eligible')
        before = float(current['amount'])
        if not math.isfinite(requested) or not 0 < requested < before:
            raise ValueError('partial_quantity_invalid')
        from ..data.feed import execution_account_scope
        binding = json.loads(current['entry_identity_json'] or '{}').get('execution_binding')
        intent = dict(schema='partial-exit-intent.v1', trade_id=trade['id'], symbol=current['symbol'],
            side=current['side'], before_quantity=before, requested_quantity=requested,
            entry_price=float(current['entry_price']), created_ms=int(time.time()*1000),
            venue=getattr(executor.ex,'id',None), account_scope=execution_account_scope(executor.ex),
            binding=binding, client_order_id='lp_'+hashlib.sha256(('tp1:'+trade['id']).encode()).hexdigest()[:28])
        validate(executor, intent)
        db.execute('INSERT INTO partial_exit_intents VALUES(?,?,?,?)',
            (trade['id'],'SUBMISSION_ATTEMPTED',intent['client_order_id'],json.dumps(intent,allow_nan=False)))
        return dict(intent,state='SUBMISSION_ATTEMPTED'), True


def validate(executor, intent):
    from ..data.feed import execution_account_scope
    if (getattr(executor.ex,'id',None) != intent['venue']
            or execution_account_scope(executor.ex) != intent['account_scope']):
        raise ValueError('partial_execution_account_changed')
    binding = intent.get('binding')
    if binding:
        cap = binding['capability']
        market = executor.ex.market(intent['symbol'])
        if (execution_account_scope(executor.ex) != cap['account_scope']
                or market.get('id') != cap['record']['instrument_id']['venue_symbol']):
            raise ValueError('partial_canonical_binding_mismatch')


def acknowledge(executor, intent, order):
    intent = dict(intent,order_id=str(order.get('id') or ''))
    with executor.journal._tx() as db:
        db.execute("UPDATE partial_exit_intents SET state='ACKNOWLEDGED',payload=? WHERE trade_id=? AND state!='CONSUMED'",
            (json.dumps(intent,allow_nan=False),intent['trade_id']))
    return intent


def resolve(executor, intent):
    validate(executor,intent)
    oid = intent.get('order_id')
    order = executor.ex.fetch_order(oid, intent['symbol']) if oid else executor.ex.fetch_order(
        None,intent['symbol'],{'origClientOrderId':intent['client_order_id']})
    from .recovery import exact_order_match
    if (not isinstance(order,dict) or not order.get('id')
            or not exact_order_match(order, client_order_id=intent['client_order_id'],
                symbol=intent['symbol'], side='sell' if intent['side']=='long' else 'buy',
                order_id=oid, reduce_only=True)
            or str(order.get('status','')).lower() not in TERMINAL):
        raise ValueError('partial_exact_terminal_order_unconfirmed')
    filled = float(order.get('filled') or 0)
    if not math.isfinite(filled) or not 0 < filled <= intent['requested_quantity']:
        raise ValueError('partial_executed_quantity_unconfirmed')
    positions = executor.ex.fetch_positions([intent['symbol']])
    if not isinstance(positions,list):
        raise ValueError('partial_venue_positions_unconfirmed')
    matches = [p for p in positions if norm_symbol(p.get('symbol',''))==norm_symbol(intent['symbol'])
               and float(p.get('contracts') or 0)>0]
    remaining = intent['before_quantity']-filled
    if (len(matches)!=1 or matches[0].get('side')!=intent['side']
            or not math.isclose(float(matches[0]['contracts']),remaining,rel_tol=1e-9,abs_tol=1e-9)):
        raise ValueError('partial_venue_position_conflict')
    return order


def consume(executor, intent, trade, filled, pnl, evidence):
    if not math.isfinite(filled) or not 0 < filled <= intent['requested_quantity']:
        raise ValueError('partial_fill_exceeds_intent')
    amount = intent['before_quantity']-filled
    notional = round(amount*intent['entry_price'],2)
    with executor.journal._tx() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT state FROM partial_exit_intents WHERE trade_id=?',(intent['trade_id'],)).fetchone()
        current = db.execute('SELECT * FROM trades WHERE id=?',(intent['trade_id'],)).fetchone()
        if not row or current is None:
            raise ValueError('partial_persisted_intent_missing')
        if row['state']=='CONSUMED':
            trade.update(amount=current['amount'],notional_usdt=current['notional_usdt'],tp1_done=1)
            return
        current_amount = float(current['amount'])
        aligned = math.isclose(current_amount,amount,rel_tol=1e-9,abs_tol=1e-9)
        if (current['status']!='open' or not (aligned or math.isclose(current_amount,intent['before_quantity'],rel_tol=1e-9,abs_tol=1e-9))):
            raise ValueError('partial_journal_position_conflict')
        evidence.update(partial_intent_id=intent['client_order_id'],client_order_id=intent['client_order_id'],
                        position_already_reconciled=aligned)
        executor.journal.align_trade_amount(intent['trade_id'],amount,notional,
            pnl_delta=0 if aligned else round(pnl,8),accounting=evidence,tp1_done=True,_connection=db)
        result = dict(intent,confirmed_quantity=filled,remaining_quantity=amount,accounting=evidence)
        db.execute("UPDATE partial_exit_intents SET state='CONSUMED',payload=? WHERE trade_id=?",
            (json.dumps(result,allow_nan=False),intent['trade_id']))
    trade.update(amount=round(amount,8),notional_usdt=notional,tp1_done=1)


def pending(journal):
    if not journal.query("SELECT 1 FROM sqlite_master WHERE name='partial_exit_intents'"):
        return []
    return journal.query("SELECT * FROM partial_exit_intents WHERE state!='CONSUMED' ORDER BY trade_id")


def recover(executor):
    for row in pending(executor.journal):
        intent = json.loads(row['payload'])
        trades = executor.journal.query('SELECT * FROM trades WHERE id=?',(intent['trade_id'],))
        if trades:
            # Reservation already exists: this path can only query and consume.
            executor.close_partial(dict(trades[0]),intent['requested_quantity'],reason='tp1_recovery')
