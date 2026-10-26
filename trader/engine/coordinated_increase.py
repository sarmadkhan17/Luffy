"""Delta increases of an owned net position, through the existing Risk boundary.

No independent entry authority. An increase uses the same permission, durable
submission and recovery barrier as an ordinary entry, with a frozen before state.
"""
import json
import math
from . import entry_authority as A
from . import protective

FIELDS=('id','symbol','side','amount','entry_price','notional_usdt','leverage','stop_loss','take_profit','sl_order_id',
        'strategy_id','status','entry_identity_json')


def before(row):
    return {k:row[k] for k in FIELDS}


def binding(executor,trade_id,identity,symbol,strategy_id,direction):
    from .coordinated_close import ownership
    rows=executor.journal.query('SELECT * FROM trades WHERE id=?',(trade_id,))
    if len(rows)!=1 or rows[0]['status']!='open':raise ValueError('increase_trade_not_open')
    row=rows[0];root=json.loads(row['entry_identity_json'])
    group=(identity or {}).get('coordinated') or {}
    ownership(executor,row,group.get('group_id'))
    if (group!=root.get('coordinated') or symbol!=row['symbol'] or strategy_id!=row['strategy_id']
            or direction.lower()!=row['side']
            or any((identity or {}).get(k)!=root.get(k) for k in ('version_id','spec_hash'))):
        raise ValueError('increase_position_identity_differs')
    return dict(trade_id=trade_id,before=before(row),before_sha256=A.digest(before(row)))


def risk_binding(journal,cap,trade_id,identity,symbol,strategy_id,direction):
    rows=journal.query('SELECT * FROM trades WHERE id=?',(trade_id,))
    if len(rows)!=1 or rows[0]['status']!='open':raise ValueError('increase_trade_not_open')
    row=rows[0];root=json.loads(row['entry_identity_json']);group=(identity or {}).get('coordinated')
    bound=root.get('execution_binding') or {}
    if (not isinstance(group,dict) or not group.get('group_id') or group.get('leg') not in ('primary','hedge')
            or group!=root.get('coordinated') or row['symbol']!=symbol or row['strategy_id']!=strategy_id
            or row['side']!=direction.lower() or bound.get('instrument_id')!=cap['instrument_id']
            or (bound.get('capability') or {}).get('account_scope')!=cap['account_scope']
            or any((identity or {}).get(k)!=root.get(k) for k in ('version_id','spec_hash'))):
        raise ValueError('increase_position_identity_differs')
    requests=journal.query('SELECT * FROM execution_requests WHERE logical_id=?',(bound.get('logical_id'),))
    if len(requests)!=1:raise ValueError('increase_root_receipt_missing')
    req=requests[0];raw=json.loads(req['intent_json']);result=json.loads(req.get('result_json') or '{}')
    if (A.digest(raw)!=req['intent_hash'] or req['state']!='TERMINAL' or result.get('position_id')!=trade_id
            or raw.get('symbol')!=row['symbol'] or raw.get('strategy_id')!=row['strategy_id']
            or raw.get('instrument_id')!=cap['instrument_id'] or raw.get('logical_id')!=bound.get('logical_id')
            or raw.get('strategy_identity')!=bound.get('strategy_identity')
            or (raw.get('strategy_identity') or {}).get('coordinated')!=group):
        raise ValueError('increase_root_receipt_differs')
    return dict(trade_id=trade_id,before=before(row),before_sha256=A.digest(before(row)))


def verify(journal,adjustment):
    rows=journal.query('SELECT * FROM trades WHERE id=?',(adjustment['trade_id'],))
    if (len(rows)!=1 or rows[0]['status']!='open' or before(rows[0])!=adjustment['before']
            or A.digest(before(rows[0]))!=adjustment['before_sha256']):
        raise ValueError('increase_before_state_changed')
    return rows[0]


def book(journal,intent,amount,entry,stop,order,*,protection_status='VERIFIED_FULL'):
    """Actual aggregate position and verified full stop commit with one receipt.

    The root entry identity and realized PnL are preserved. This is not a close
    or a second journal position for the same net venue instrument.
    """
    from .booking import persist,snapshot
    adj=intent['adjustment'];tid=adj['trade_id']
    with journal._tx() as db:
        existing=db.execute('SELECT payload FROM trade_accounting_bookings WHERE trade_id=?',(tid,)).fetchall()
        for r in existing:
            receipt=json.loads(r[0])
            if receipt['kind']=='increase' and receipt['evidence'].get('logical_id')==intent['logical_id']:
                from .booking import replay
                replay(receipt)
                if receipt['evidence'].get('order_id')!=str(order['id']):raise ValueError('increase_booking_order_differs')
                return
        row=db.execute('SELECT * FROM trades WHERE id=?',(tid,)).fetchone()
        if row is None or before(row)!=adj['before']:raise ValueError('increase_before_state_changed')
        old=snapshot(row)
        db.execute('UPDATE trades SET amount=?,entry_price=?,notional_usdt=?,sl_order_id=?,stop_loss=? WHERE id=?',
            (amount,entry,amount*entry,str(stop['id']),float(stop['stop_price']),tid))
        persist(db,tid,'increase',old,dict(basis='recovered_aggregate_position_and_full_stop',
            purpose='increase',logical_id=intent['logical_id'],order_id=str(order['id']),
            client_order_id=intent['client_order_id'],confirmed_quantity=float(order['filled']),
            aggregate_quantity=amount,aggregate_entry_price=entry,adjustment=adj,
            stop_observation=dict(id=str(stop['id']),stop_price=float(stop['stop_price']),status=protection_status),
            risk_binding=intent['position']['entry_identity']['execution_binding'],
            accounting='aggregate exposure only; fees and funding not fully attributed'))


def recover(recovery,intent,order,positions):
    """Recover only this delta; never adopt its fill as the whole position."""
    ex=recovery.ex;journal=recovery.journal;adj=intent['adjustment'];template=intent['position']
    if intent.get('increase_unwind'):
        return unwind(recovery,intent,positions)
    booked=None
    for r in journal.query('SELECT payload FROM trade_accounting_bookings WHERE trade_id=?',(adj['trade_id'],)):
        receipt=json.loads(r['payload'])
        if receipt['kind']=='increase' and receipt['evidence'].get('logical_id')==intent['logical_id']:
            from .booking import replay
            replay(receipt)
            if receipt['evidence'].get('order_id')!=str(order['id']):raise ValueError('increase_booking_order_differs')
            booked=receipt
    if booked:
        row=journal.query('SELECT * FROM trades WHERE id=?',(adj['trade_id'],))[0]
        from .booking import snapshot
        if snapshot(row)!=booked['after']:raise ValueError('increase_booked_state_changed')
    else:row=verify(journal,adj)
    cap=template['entry_identity']['execution_binding']['capability']
    matches=[]
    for p in positions:
        market=ex.market(p['symbol'])
        if (p.get('info') or {}).get('symbol',market.get('id'))!=market.get('id'):
            raise ValueError('increase_venue_position_identity_mismatch')
        if market.get('id')!=cap['record']['instrument_id']['venue_symbol']:continue
        q=float(p['contracts'])
        if not math.isfinite(q) or q<0:raise ValueError('increase_position_quantity_unknown')
        if q:matches.append(p)
    filled=float(order['filled'])
    requested=float(template['amount'])
    if not math.isfinite(filled) or not 0<=filled<=requested:raise ValueError('increase_fill_unknown')
    if (len(matches)!=1 or matches[0]['side']!=row['side']
            or not math.isclose(float(matches[0]['contracts']),float(adj['before']['amount'])+filled,rel_tol=1e-10,abs_tol=1e-12)):
        recovery.save(intent,'increase_aggregate_position_mismatch');return
    p=matches[0];amount=float(p['contracts']);entry=float(p['entryPrice']);sl=float(row['stop_loss'])
    if not math.isfinite(entry) or entry<=0:raise ValueError('increase_aggregate_entry_unknown')
    if filled==0:
        recovery.finish(intent,'increase_terminal_without_fill');return
    stops=protective.open_stops(ex,intent['symbol'],strict=True)
    candidates=[s for s in stops if protective.protection_match(ex,intent['symbol'],row['side'],amount,sl,s).matches]
    if not candidates:
        # The old stop remains armed. An unknown placement is inspected, never
        # blindly repeated. Global recovery keeps every new entry blocked.
        if intent.get('stop_attempted'):
            contain(recovery,intent,amount,entry,order);return
        mark=float(p.get('markPrice') or 0)
        if not math.isfinite(mark) or mark<=0 or (sl>=mark if row['side']=='long' else sl<=mark):
            contain(recovery,intent,amount,entry,order);return
        intent['stop_attempted']=True
        recovery.save(intent,'increase_full_stop_intent_persisted')
        try:protective.place_stop(ex,intent['symbol'],'sell' if row['side']=='long' else 'buy',amount,sl)
        except Exception:
            # Unknown placement is read on the next pass before any unwind.
            recovery.save(intent,'increase_full_stop_placement_unknown');return
        recovery.save(intent,'increase_full_stop_submitted_unconfirmed');return
    chosen=candidates[0]
    # Keep the full stop while cancelling only the superseded stop we own.
    old=str(adj['before']['sl_order_id'] or '')
    if old and old!=str(chosen['id']) and any(str(s['id'])==old for s in stops):
        protective.cancel_stop(ex,old,intent['symbol'])
        remaining=protective.open_stops(ex,intent['symbol'],strict=True)
        if any(str(s['id'])==old for s in remaining):
            recovery.save(intent,'increase_old_stop_cleanup_pending');return
        if not any(str(s['id'])==str(chosen['id']) and protective.protection_match(
                ex,intent['symbol'],row['side'],amount,sl,s).matches for s in remaining):
            recovery.save(intent,'increase_full_stop_disappeared');return
    book(journal,intent,amount,entry,chosen,order)
    recovery.finish(intent,'increase_confirmed_protected_and_journalled')


def contain(recovery,intent,amount,entry,order):
    """Record actual exposure, then undo only the added delta.

    Flattening an existing hedge can remove protection for its other leg.
    The parent portfolio controller owns any later whole-book unwind.
    """
    old=intent['adjustment']['before']
    book(recovery.journal,intent,amount,entry,dict(id=old['sl_order_id'],stop_price=old['stop_loss']),
         order,protection_status='UNCONFIRMED_FULL_REQUIRES_DELTA_ROLLBACK')
    intent['increase_unwind']=True
    recovery.save(intent,'increase_unprotected_delta_rollback_required')


def unwind(recovery,intent,positions):
    """Restore the prior quantity, preserving the existing protected leg."""
    from .coordinated_close import close_once
    tid=intent['adjustment']['trade_id'];journal=recovery.journal
    row=journal.query('SELECT * FROM trades WHERE id=?',(tid,))[0]
    # A timed-out reduction may already have changed the venue quantity while
    # its booking is still pending. Resolve that EXACT attempted action before
    # comparing the journal to venue; never interpret the mismatch as a reason
    # to submit a fresh reduction. RESERVED has not been attempted and still
    # requires the fresh before-state check below.
    action=intent.get('unwind_logical_id')
    if action and journal.query("SELECT 1 FROM sqlite_master WHERE name='coordinated_reductions'"):
        records=journal.query('SELECT state FROM coordinated_reductions WHERE logical_id=?',(action,))
        if records and records[0]['state']!='RESERVED':
            group=intent['position']['entry_identity']['coordinated']['group_id']
            result=close_once(recovery.executor,tid,group,action)
            if result['status']=='BOOKED':intent.pop('unwind_logical_id',None)
            recovery.save(intent,'increase_delta_rollback_'+result['status'].lower())
            return
    cap=intent['position']['entry_identity']['execution_binding']['capability']
    matches=[]
    for p in positions:
        market=recovery.ex.market(p['symbol'])
        if (p.get('info') or {}).get('symbol',market.get('id'))!=market.get('id'):
            raise ValueError('increase_rollback_venue_identity_mismatch')
        if market['id']!=cap['record']['instrument_id']['venue_symbol']:continue
        q=float(A.positive(p['contracts'],zero=True))
        if q:matches.append(p)
    if row['status']=='closed':
        if matches:
            recovery.save(intent,'increase_rollback_flat_unproven');return
        if protective.open_stops(recovery.ex,intent['symbol'],strict=True):
            recovery.save(intent,'increase_rollback_remaining_protection');return
        recovery.finish(intent,'increase_existing_position_closed_and_flat');return
    if (len(matches)!=1 or matches[0]['side']!=row['side']
            or not math.isclose(float(matches[0]['contracts']),float(row['amount']),rel_tol=1e-10,abs_tol=1e-12)):
        recovery.save(intent,'increase_rollback_reconciliation_required');return
    excess=max(0.,float(row['amount'])-float(intent['adjustment']['before']['amount']))
    if excess<=1e-12:
        stops=protective.open_stops(recovery.ex,intent['symbol'],strict=True)
        confirmed=[s for s in stops if protective.protection_match(recovery.ex,intent['symbol'],row['side'],
            float(row['amount']),float(row['stop_loss']),s).matches]
        if not confirmed:
            recovery.save(intent,'increase_prior_position_protection_unconfirmed');return
        journal.record_stop_order(tid,confirmed[0]['id'],confirmed[0]['stop_price'])
        recovery.finish(intent,'increase_delta_rolled_back_prior_position_protected');return
    if not intent.get('unwind_logical_id'):
        intent['unwind_logical_id']=intent['logical_id']+'-rollback-'+A.digest(before(row))[:16]
        recovery.save(intent,'increase_delta_rollback_action_persisted')
    group=intent['position']['entry_identity']['coordinated']['group_id']
    result=close_once(recovery.executor,tid,group,intent['unwind_logical_id'],
        quantity=excess,reason='increase_protection_failure_delta_rollback')
    if result['status']=='BOOKED':
        intent.pop('unwind_logical_id',None)
    recovery.save(intent,'increase_delta_rollback_'+result['status'].lower())
