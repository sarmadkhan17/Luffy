"""Dispatch shared-HedgeBook intents through existing execution boundaries.

The caller must durably retain the book and its pending instruction until an
actual terminal receipt is adopted. This module never fabricates a fill or
advances the model. Runtime admission stays disabled until that caller is wired.
"""
from dataclasses import asdict,is_dataclass
import json
import math
import time

from . import entry_authority as A,coordinated_authority as CA
from . import coordinated_close as C,coordinated_unwind as U
from ..core.types import Action,Decision,now_utc
from ..research.compose import hedge as H
from ..strategy import factory_handoff as F,exit_policy as E


def _receipt(journal,action_id,group_id,leg,version_id,instruction):
    if not journal.query("SELECT 1 FROM sqlite_master WHERE type='table' AND name='execution_requests'"):
        return None
    rows=journal.query('SELECT * FROM execution_requests WHERE logical_id=?',(action_id,))
    if not rows:return None
    row=rows[0];raw=json.loads(row['intent_json']);identity=raw['strategy_identity']
    if (A.digest(raw)!=row['intent_hash'] or identity.get('version_id')!=version_id
            or identity.get('coordinated_action_id')!=action_id
            or identity.get('coordinated_instruction')!=instruction
            or identity.get('coordinated',{}).get('group_id')!=group_id
            or identity['coordinated'].get('leg')!=leg):
        raise ValueError('coordinated_action_receipt_differs')
    # Terminal means execution recovery ended; it does not necessarily mean
    # an admitted fill survived protection. Only its explicit result can say so.
    result=json.loads(row.get('result_json') or '{}')
    recovery=json.loads(row.get('recovery_json') or '{}')
    filled=None
    if row['state']=='TERMINAL':
        reason=result.get('reason')
        if reason=='entry_protected_and_journalled':
            from . import booking
            receipts=journal.query('SELECT payload FROM trade_accounting_bookings WHERE trade_id=? ORDER BY id',
                                   (result['position_id'],))
            exact=[]
            for saved in receipts:
                receipt=json.loads(saved['payload']);booking.replay(receipt)
                evidence=receipt['evidence']
                if (receipt['kind']=='entry' and evidence.get('order_id')==row['order_id']
                        and evidence.get('client_order_id')==row['client_order_id']):
                    exact.append(float(evidence['confirmed_quantity']))
            if len(exact)!=1:raise ValueError('coordinated_action_booking_missing')
            filled=exact[0]
        elif reason in ('confirmed_protected_and_journalled','increase_confirmed_protected_and_journalled'):
            filled=float(recovery['entry_observation']['filled'])
        elif reason in ('terminal_orders_and_flat_venue','increase_terminal_without_fill',
                        'increase_delta_rolled_back_prior_position_protected'):
            filled=0.
        if filled is not None and (not math.isfinite(filled) or not 0<=filled<=float(raw['requested'])):
            raise ValueError('coordinated_action_fill_invalid')
    return dict(action_id=action_id,status=row['state'],filled_quantity=filled,
                position_id=result.get('position_id'),reason=result.get('reason'))


def _reduction(executor,book,ins,action_id,gid,vid):
    if not executor.journal.query("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(C.TABLE,)):
        return None
    rows=executor.journal.query('SELECT * FROM coordinated_reductions WHERE logical_id=?',(action_id,))
    if not rows:return None
    saved=rows[0];request=json.loads(saved['request_json'])
    trade=executor.journal.query('SELECT * FROM trades WHERE id=?',(saved['trade_id'],))[0]
    owner=C.ownership(executor,trade,gid)
    from .executor import quantize
    if (saved['group_id']!=gid or request['logical_id']!=action_id or request['trade_id']!=trade['id']
            or request['group_id']!=gid or request['symbol']!=trade['symbol']
            or request['side']!=('buy' if ins.side>0 else 'sell') or request['reason']!=ins.reason
            or request['quantity']!=quantize(executor.ex,trade['symbol'],ins.qty)
            or request['before_quantity']!=abs(book.pos[ins.leg]) or owner['leg']!=ins.leg
            or json.loads(trade['entry_identity_json']).get('version_id')!=vid):
        raise ValueError('coordinated_action_reduction_differs')
    result=C.close_once(executor,trade['id'],gid,action_id,quantity=ins.qty,reason=ins.reason)
    saved=executor.journal.query('SELECT * FROM coordinated_reductions WHERE logical_id=?',(action_id,))[0]
    order=json.loads(saved['order_json'] or '{}')
    filled=float(order['filled']) if saved['state']=='BOOKED' else 0. if saved['state']=='REFUSED' else None
    if filled is not None and (not math.isfinite(filled) or not 0<=filled<=request['quantity']):
        raise ValueError('coordinated_action_fill_invalid')
    return dict(result,action_id=action_id,filled_quantity=filled,position_id=trade['id'])


def dispatch(executor,book,instruction,*,identity,reference,atr):
    """One owned leg action; all increases reuse existing exact Risk authority.

    Refused/pending actions are never renamed. A subsequent clock tick reads
    their original receipt; the existing recovery worker owns reconciliation.
    Reductions remain possible when new-entry authority has been withdrawn.
    """
    if not isinstance(book,H.HedgeBook):raise ValueError('coordinated_shared_book_required')
    ins=instruction if isinstance(instruction,H.Instruction) else H.Instruction(**instruction)
    pending=book.pending.get(ins.oid)
    logged=[asdict(x) if is_dataclass(x) else x for x in book.log]
    if (not ins.oid or pending is None or tuple(pending)!=(ins.leg,ins.side,ins.qty,ins.bar)
            or asdict(ins) not in logged or ins.leg not in ('primary','hedge')
            or ins.side not in (-1,1) or not math.isfinite(ins.qty) or ins.qty<=0
            or ins.kind not in ('ENTER','REBALANCE','REDUCE_PRIMARY','UNWIND')):
        raise ValueError('coordinated_pending_instruction_differs')
    identity=dict(identity);group=identity.get('coordinated') or {}
    gid=group.get('group_id');vid=identity.get('version_id')
    if not gid or not vid or group.get('leg')!=ins.leg:
        raise ValueError('coordinated_action_identity_required')
    action_id='cg_'+A.digest([gid,vid,ins.oid])
    identity['coordinated_action_id']=action_id
    identity['coordinated_instruction']=asdict(ins)
    # The existing entry lock serializes mutation. Group unwind acquires its
    # own non-reentrant group lock; holding that here would deadlock it.
    with executor._entry_lock:
        version=F.load_version(executor.journal,vid)
        hs=version['lineage']['research']['multi_leg']['hedge_spec']
        if book.spec!=hs or book.primary_exit_definition!=version['spec']['exit']:
            raise ValueError('coordinated_book_version_differs')
        if (group.get('management_policy')!=hs.get('management')
                or group.get('maintenance_fraction')!=hs['maint_margin']
                or A.positive(group.get('allocated_equity_usdt'))!=A.positive(book.management_entry_equity)):
            raise ValueError('coordinated_book_management_identity_differs')
        previous=_receipt(executor.journal,action_id,gid,ins.leg,vid,asdict(ins))
        if previous is not None:return previous
        reduction=_reduction(executor,book,ins,action_id,gid,vid)
        if reduction is not None:return reduction
        rows=executor.journal.query("SELECT * FROM trades WHERE json_extract("
            "CASE WHEN json_valid(entry_identity_json) THEN entry_identity_json ELSE '{}' END,"
            "'$.coordinated.group_id')=? ORDER BY opened_at,id LIMIT 65",(gid,))
        if len(rows)>64:raise ValueError('coordinated_action_group_bound_exceeded')
        owned=[]
        for row in rows:
            owner=C.ownership(executor,row,gid)
            root=json.loads(row['entry_identity_json'])
            if (root.get('version_id')!=vid or root.get('spec_hash')!=version['spec_hash']
                    or root.get('install_id')!=identity.get('install_id')):
                raise ValueError('coordinated_action_group_version_differs')
            if row['status']=='open':owned.append((row,owner))
        hedge_only=(ins.leg=='hedge' and ins.reason=='strategy_hedge_stop'
                    and ((hs.get('management') or {}).get('hedge_stop') or {}).get('on_hit')=='close_hedge')
        if ins.kind=='UNWIND' and not hedge_only:
            target=[r for r in rows if json.loads(r['entry_identity_json'])['coordinated']['leg']==ins.leg]
            if not target:raise ValueError('coordinated_unwind_owned_leg_missing')
            # A whole-position exit follows current owned exposure even if
            # native reconciliation changed its size since the model tick.
            return U.request_close(executor,target[-1],ins.reason)
        current=[row for row,owner in owned if owner['leg']==ins.leg]
        if len(current)>1:raise ValueError('coordinated_multiple_open_leg_rows')
        row=current[0] if current else None
        signed=(float(row['amount'])*(1 if row['side']=='long' else -1)) if row else 0.
        if not math.isclose(signed,book.pos[ins.leg],rel_tol=1e-10,abs_tol=1e-12):
            raise ValueError('coordinated_book_owned_quantity_differs')
        reducing=bool(signed and signed*ins.side<0)
        if reducing:
            if ins.qty>abs(signed):raise ValueError('coordinated_reduction_crosses_flat')
            if U.active(executor.journal,gid):return U.request_close(executor,row,ins.reason)
            C.close_once(executor,row['id'],gid,action_id,quantity=ins.qty,reason=ins.reason)
            return _reduction(executor,book,ins,action_id,gid,vid)
        if ins.kind in ('REDUCE_PRIMARY','UNWIND'):
            raise ValueError('coordinated_reduction_would_increase')
        if U.active(executor.journal,gid):raise ValueError('coordinated_group_unwind_started')
        if executor.recovery_pending():return dict(action_id=action_id,status='ENTRY_RECOVERY_PENDING',filled_quantity=None)
        with CA.execution_scope(executor.journal,executor.cfg,vid,group_id=gid,leg=ins.leg,action_id=action_id):
            if ins.position_side!='BOTH':raise ValueError('coordinated_position_side_unsupported')
            if ins.leg=='primary' and not row and rows:
                raise ValueError('coordinated_primary_group_cannot_reopen')
            if ins.leg=='primary' and not row and (ins.kind!='ENTER' or ins.reason!='signal' or ins.side!=book.direction):
                raise ValueError('coordinated_primary_signal_required')
            if ins.leg=='hedge' and not row:
                primary=[r for r,o in owned if o['leg']=='primary']
                if (len(primary)!=1 or book.direction*ins.side>=0
                        or primary[0]['side']!=('long' if book.direction>0 else 'short')
                        or not book.management_activated or ins.reason!='risk_management_activation'):
                    raise ValueError('coordinated_conditional_hedge_required')
            observed=(reference or {}).get('observed_ms');now=int(time.time()*1000)
            if type(observed) is not int or not 0<=now-observed<=30_000:
                raise ValueError('coordinated_action_reference_stale')
            prepared=identity.get('coordinated_instruction_observed_ms')
            if type(prepared) is not int or not 0<=observed-prepared<=H.MAX_QUOTE_INCREASE_AGE_MS:
                raise ValueError('coordinated_action_instruction_stale')
            symbol=hs[ins.leg]['symbol']
            if row:
                root=json.loads(row['entry_identity_json'])
                if any(identity.get(k)!=root.get(k) for k in C.IDENTITY_FIELDS):
                    raise ValueError('coordinated_action_root_identity_differs')
                stop=float(row['stop_loss']);target=float(row['take_profit'])
            else:
                state=identity.get('exit_state' if ins.leg=='primary' else 'hedge_exit_state')
                model=book.primary_exit_state if ins.leg=='primary' else book.hedge_exit_state
                if not state or not model or E.decode(state)!=E.decode(model):
                    raise ValueError('coordinated_action_stop_state_differs')
                policy,_=E.decode(state);stop=policy.stop;target=policy.target or 0.
            decision=Decision(action_id,'cycle_'+action_id,symbol,Action.BUY if ins.side>0 else Action.SELL,
                              1.,1.,1.,[],[],ts=now_utc().isoformat())
            decision.instrument_binding_json=A.proposal_binding(executor.journal,symbol)
            proposed=json.loads(decision.instrument_binding_json or '{}')
            if not proposed or (reference or {}).get('instrument_id')!=proposed.get('instrument_id') or not (reference or {}).get('source'):
                raise ValueError('coordinated_action_quote_identity_differs')
            with executor.journal._tx() as db:
                db.execute('INSERT OR IGNORE INTO cycles(id,ts,symbol) VALUES(?,?,?)',
                           (decision.cycle_id,decision.ts,symbol))
            executor.journal.log_decision(decision)
            permission=executor.risk_manager.authorize_entry(decision,ins.qty,atr,stop,target,version['strategy_id'],
                identity=identity,reference=reference,increase_trade_id=row['id'] if row else None)
            if row:
                executor.increase(row['id'],decision,ins.qty,atr,stop,target,version['strategy_id'],identity,reference,permission)
            else:
                executor.open(decision,ins.qty,atr,stop,target,version['strategy_id'],version['strategy_id'],
                    entry_identity=identity,reference=reference,risk_permission=permission)
            return _receipt(executor.journal,action_id,gid,ins.leg,vid,asdict(ins)) or dict(
                action_id=action_id,status='ENTRY_REFUSED',filled_quantity=None,reason=decision.skip_reason)
