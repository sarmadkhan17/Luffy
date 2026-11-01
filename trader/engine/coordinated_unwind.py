"""Resume a whole economic position's close through existing leg recovery.

No entry, approval, account-mode change or inferred financial result. The
primary must be confirmed flat before reducing its protective hedge. A full
venue response, not journal status, establishes completion. Pending groups
reserve their instruments at the existing final Risk boundary.
"""
import json
import time
from . import entry_authority as A
from . import coordinated_close as C

TABLE='coordinated_group_unwinds'


def owned_group(trade):
    """Recorded group identity only; ownership is checked before any reduction."""
    try:
        identity=json.loads(trade.get('entry_identity_json') or '{}')
        group=identity.get('coordinated') if isinstance(identity,dict) else None
        value=group.get('group_id') if isinstance(group,dict) else None
        return value if isinstance(value,str) and value else None
    except (ValueError,TypeError):return None


def ensure(journal):
    with journal._tx() as db:
        db.execute('''CREATE TABLE IF NOT EXISTS coordinated_group_unwinds(
            group_id TEXT PRIMARY KEY,logical_id TEXT UNIQUE NOT NULL,
            plan_json TEXT NOT NULL,plan_sha256 TEXT NOT NULL,state TEXT NOT NULL)''')
        db.execute('CREATE INDEX IF NOT EXISTS coordinated_unwind_state ON coordinated_group_unwinds(state)')


def _exists(journal):
    return bool(journal.query("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(TABLE,)))


def _plan(row):
    plan=json.loads(row['plan_json'])
    if (A.digest(plan)!=row['plan_sha256'] or plan['group_id']!=row['group_id']
            or plan['logical_id']!=row['logical_id']):
        raise ValueError('coordinated_unwind_plan_corrupt')
    return plan


def active(journal,group_id):
    if not _exists(journal):return False
    rows=journal.query('SELECT * FROM coordinated_group_unwinds WHERE group_id=?',(group_id,))
    if not rows:return False
    _plan(rows[0])
    return rows[0]['state']!='CLOSED'


def entry_block(journal,instrument_id,identity=None):
    from .versioned_close import entry_block as versioned_entry_block
    blocked=versioned_entry_block(journal,instrument_id)
    if blocked:return blocked
    if not _exists(journal):return None
    group=(identity or {}).get('coordinated') if isinstance(identity,dict) else None
    group_id=group.get('group_id') if isinstance(group,dict) else None
    for row in journal.query("SELECT * FROM coordinated_group_unwinds WHERE state!='CLOSED' OR group_id=?",(group_id,)):
        plan=_plan(row)
        if row['state']!='CLOSED' and instrument_id in {m['instrument_id'] for m in plan['members']}:
            return 'coordinated_instrument_unwind_pending'
        if group_id==plan['group_id']:
            # A delayed hedge may name an instrument the primary never held.
            # A finished economic trade cannot be reopened under its old ID.
            return 'coordinated_group_unwind_started'
    return None


def _members(executor,group_id):
    rows=executor.journal.query("SELECT * FROM trades WHERE json_extract("
        "CASE WHEN json_valid(entry_identity_json) THEN entry_identity_json ELSE '{}' END,"
        "'$.coordinated.group_id')=? ORDER BY opened_at,id LIMIT 1025",(group_id,))
    if not rows or len(rows)>1024:raise ValueError('coordinated_unwind_group_missing_or_bound_exceeded')
    members=[];identity=None
    for row in rows:
        group=C.ownership(executor,row,group_id)
        root=json.loads(row['entry_identity_json']);binding=root['execution_binding']
        exact=(row['strategy_id'],root.get('version_id'),root.get('spec_hash'),root.get('install_id'))
        if identity is not None and identity!=exact:raise ValueError('coordinated_unwind_versions_differ')
        identity=exact
        members.append(dict(trade_id=row['id'],leg=group['leg'],instrument_id=binding['instrument_id'],
            symbol=row['symbol'],entry_identity_sha256=A.digest(root)))
    if not any(m['leg']=='primary' for m in members):raise ValueError('coordinated_unwind_primary_missing')
    return sorted(members,key=lambda m:(m['leg']!='primary',m['trade_id']))


def _position_observation(executor):
    from ..observability.portfolio_observation import observe_positions,trading_source
    from ..core.types import MarketType
    start=int(time.time()*1000)
    rows=executor.ex.fetch_positions()
    end=int(time.time()*1000)
    if end-start>30000:raise ValueError('coordinated_unwind_position_read_stale')
    environment,source=trading_source(executor.ex)
    obs=observe_positions(rows,exchange_id=executor.ex.id,market_type=MarketType.FUTURES,
        environment=environment,source_ref=source,request_start_ms=start,response_received_ms=end)
    return obs


def _positions(executor):
    obs=_position_observation(executor)
    return {r.instrument_id.value:r.absolute_quantity for r in obs.positions if r.position_present}


def _owned_replacement(executor,closed,members,positions):
    """A later owned hedge may reuse a closed hedge's net instrument."""
    from ..cognition.outcomes import timestamp
    old=executor.journal.query('SELECT * FROM trades WHERE id=?',(closed['trade_id'],))[0]
    replacements=[]
    for member in members:
        if member['trade_id']==closed['trade_id'] or member['instrument_id']!=closed['instrument_id']:continue
        row=executor.journal.query('SELECT * FROM trades WHERE id=?',(member['trade_id'],))[0]
        if row['status']=='open':replacements.append((member,row))
    if len(replacements)!=1:return False
    member,row=replacements[0]
    if member['leg']!=closed['leg'] or not old.get('closed_at') or not row.get('opened_at'):return False
    return (timestamp(old['closed_at'])<timestamp(row['opened_at'])
            and A.positive(row['amount'])==A.positive(positions[closed['instrument_id']]))


def _adopt_late_members(executor,plan,current):
    """An already-submitted hedge can finish entry recovery after close starts.

    Only additions from an authenticated pre-existing Risk request qualify.
    Existing members cannot disappear or change, and a later new intent cannot
    expand a closing economic position.
    """
    old={m['trade_id']:m for m in plan['members']};actual={m['trade_id']:m for m in current}
    started=plan.get('started_ms')
    if type(started) is not int or any(actual.get(tid)!=m for tid,m in old.items()):
        raise ValueError('coordinated_unwind_members_changed')
    added=[m for m in current if m['trade_id'] not in old]
    if not added or any(m['leg']!='hedge' for m in added):
        raise ValueError('coordinated_unwind_members_changed')
    for member in added:
        row=executor.journal.query('SELECT entry_identity_json FROM trades WHERE id=?',(member['trade_id'],))[0]
        root=json.loads(row['entry_identity_json'])
        request=executor.journal.query('SELECT recovery_json FROM execution_requests WHERE logical_id=?',
                                      (root['execution_binding']['logical_id'],))
        recovery=json.loads(request[0]['recovery_json'] or '{}') if len(request)==1 else {}
        created=recovery.get('created_ms')
        if type(created) is not int or not 0<=created<=started:
            raise ValueError('coordinated_unwind_late_entry_unproven')
    updated=dict(plan,members=current)
    with executor.journal._tx() as db:
        changed=db.execute("UPDATE coordinated_group_unwinds SET plan_json=?,plan_sha256=? "
            "WHERE group_id=? AND plan_sha256=? AND state='UNWINDING'",
            (A.canonical(updated),A.digest(updated),plan['group_id'],A.digest(plan))).rowcount
        if changed!=1:raise ValueError('coordinated_unwind_plan_changed')
    return updated



def new_plan(executor,group_id,logical_id,reason,exit_decision=None):
    plan=dict(group_id=group_id,logical_id=logical_id,reason=reason,
              started_ms=int(time.time()*1000),members=_members(executor,group_id))
    if exit_decision is not None:plan['strategy_exit_decision']=exit_decision
    return plan


def close_group_once(executor,group_id,logical_id,*,reason='combined_position_exit',exit_decision=None):
    """At most one leg reduction per tick; exact retries survive restart.

    A terminal partial fill gets a new child only on a later tick, after its
    predecessor's booking is confirmed. Renaming a group close cannot create
    another sequence. This requests reductions only and grants no entry.
    """
    if not all(isinstance(x,str) and x for x in (group_id,logical_id,reason)):
        raise ValueError('coordinated_unwind_identity_required')
    ensure(executor.journal)
    with executor._entry_lock,executor._lock_for('coordinated-group:'+group_id):
        with executor.journal._tx() as db:
            db.execute('BEGIN IMMEDIATE')
            rows=executor.journal.query('SELECT * FROM coordinated_group_unwinds WHERE group_id=?',(group_id,))
            if rows:
                plan=_plan(rows[0])
                if plan['logical_id']!=logical_id or plan['reason']!=reason:
                    raise ValueError('coordinated_unwind_identity_differs')
                if rows[0]['state']=='CLOSED':return dict(group_id=group_id,status='CLOSED')
            else:
                plan=new_plan(executor,group_id,logical_id,reason,exit_decision)
                db.execute('INSERT INTO coordinated_group_unwinds VALUES(?,?,?,?,?)',
                    (group_id,logical_id,A.canonical(plan),A.digest(plan),'UNWINDING'))
        # The instrument reservation is now durable, even when entry recovery
        # or a failed read prevents a reduction on this attempt.
        if executor.recovery_pending():return dict(group_id=group_id,status='ENTRY_RECOVERY_PENDING')
        current=_members(executor,group_id)
        if current!=plan['members']:plan=_adopt_late_members(executor,plan,current)
        positions=_positions(executor)
        for member in plan['members']:
            row=executor.journal.query('SELECT * FROM trades WHERE id=?',(member['trade_id'],))[0]
            if row['status']=='closed':
                if member['instrument_id'] in positions and not _owned_replacement(executor,member,plan['members'],positions):
                    return dict(group_id=group_id,status='CLOSED_LEG_VENUE_EXPOSURE_UNRESOLVED',leg=member['leg'])
                continue
            C.ensure(executor.journal)
            history=executor.journal.query('SELECT * FROM coordinated_reductions WHERE trade_id=? AND group_id=? ORDER BY rowid',
                (member['trade_id'],group_id))
            pending=[r for r in history if r['state'] not in ('BOOKED','REFUSED')]
            if len(pending)>1:raise ValueError('coordinated_unwind_multiple_pending_reductions')
            child=(pending[0]['logical_id'] if pending else
                A.digest(['coordinated-group-close',logical_id,member['trade_id'],len(history)]))
            if history and history[-1]['state']=='REFUSED':
                return dict(group_id=group_id,status='LEG_REDUCTION_REFUSED',leg=member['leg'])
            result=C.close_once(executor,member['trade_id'],group_id,child,reason=reason,
                                exit_decision=plan.get('strategy_exit_decision'))
            return dict(group_id=group_id,status='UNWINDING',leg=member['leg'],reduction=result)
        with executor.journal._tx() as db:
            db.execute("UPDATE coordinated_group_unwinds SET state='CLOSED' WHERE group_id=?",(group_id,))
        return dict(group_id=group_id,status='CLOSED')


def request_close(executor,trade,reason):
    """Existing full-close entry point; first reason owns the durable unwind."""
    rows=executor.journal.query('SELECT * FROM trades WHERE id=?',(trade['id'],))
    if len(rows)!=1:raise ValueError('coordinated_trade_missing')
    group=owned_group(rows[0])
    if not group:raise ValueError('coordinated_group_missing')
    C.ownership(executor,rows[0],group)
    previous=(executor.journal.query('SELECT * FROM coordinated_group_unwinds WHERE group_id=?',(group,))
              if _exists(executor.journal) else [])
    if previous:
        plan=_plan(previous[0])
        if trade['id'] not in {m['trade_id'] for m in plan['members']}:
            raise ValueError('coordinated_unwind_members_changed')
        return close_group_once(executor,group,plan['logical_id'],reason=plan['reason'])
    decision=_exit_decision(rows[0],trade.get('_strategy_exit_evidence'),group,reason)
    return close_group_once(executor,group,A.digest(['coordinated-position-close',group]),reason=reason,
                            exit_decision=decision)


def _exit_decision(owned,evidence,group,reason,*,scope='WHOLE_POSITION'):
    """Retain a caller's frozen strategy intent, never assert a market fill.

    Missing source receipts remain explicit. This is evidence attached to an
    already-owned reduction, not additional authority to enter or close.
    """
    if evidence is None:return None
    from ..strategy import exit_policy as E
    root=json.loads(owned['entry_identity_json'])
    if (not isinstance(evidence,dict) or evidence.get('authority')!='STRATEGY'
            or evidence.get('scope')!=scope or evidence.get('group_id')!=group
            or evidence.get('intent')!=reason or evidence.get('exit_semantics_id')!=E.EXIT_SEMANTICS_ID
            or evidence.get('group_exit_semantics_id')!=root.get('exit_semantics_id')
            or any(evidence.get(k)!=root.get(k) for k in ('version_id','install_id'))
            or evidence.get('spec_hash')!=root.get('spec_sha256')):
        raise ValueError('coordinated_strategy_exit_decision_differs')
    if scope=='HEDGE_ONLY' or root['coordinated']['leg']=='hedge':
        response=((root['coordinated'].get('management_policy') or {}).get('hedge_stop') or {}).get('on_hit')
        if (root['coordinated']['leg']!='hedge'
                or response!=('close_hedge' if scope=='HEDGE_ONLY' else 'close_group')):
            raise ValueError('coordinated_strategy_exit_scope_differs')
    key='hedge_exit_state' if root['coordinated']['leg']=='hedge' else 'exit_state'
    policy,state=E.decode(A.canonical(evidence['state']))
    expected,_=E.decode(root[key])
    if policy!=expected or state.reason!=reason or A.positive(evidence.get('intended_quantity'))!=A.positive(owned['amount']):
        raise ValueError('coordinated_strategy_exit_policy_differs')
    retained=dict(evidence,source_trade_id=owned['id'],entry_identity_sha256=A.digest(root),
        evidence_scope='STRATEGY_INTENT_NOT_FILL',source_receipt_status=('LINKED_SIGNAL_ELIGIBILITY_NOT_REPLAYED_HERE'
            if evidence.get('signal_eligibility_sha256') else 'NOT_RETAINED_BY_ADAPTER'))
    if len(A.canonical(retained).encode())>32768:raise ValueError('coordinated_strategy_exit_decision_too_large')
    return retained


def _planned_hedge_close(journal,trade_id,group_id):
    """Only an exact booked reduction proves a deliberate hedge removal."""
    from . import booking
    if not journal.query("SELECT 1 FROM sqlite_master WHERE type='table' AND name='trade_accounting_bookings'"):
        return False
    rows=journal.query('SELECT payload FROM trade_accounting_bookings WHERE trade_id=? ORDER BY id DESC LIMIT 1',(trade_id,))
    if not rows:return False
    receipt=json.loads(rows[0]['payload']);booking.replay(receipt)
    if receipt['after']['status']!='closed':return False
    if receipt['kind'] not in ('close:ratio_drift','close:coordinated_delta','close:strategy_hedge_stop'):
        return False
    return bool(C.retained_terminal_flat(journal,trade_id,group_id,receipt['evidence'].get('order_id')))


def _hedge_closure_response(executor,primary,group):
    """Apply the frozen strategy response without guessing the close cause.

    A current replacement hedge supersedes old closed legs. Planned exact
    reductions do not masquerade as protective-stop events. Missing financial
    history cannot postpone a required protective reduction.
    """
    from ..research.compose import managed_hedge as M
    identity=json.loads(primary['entry_identity_json'])
    policy=identity['coordinated'].get('management_policy')
    if not isinstance(policy,dict) or policy.get('version') not in (M.CURRENT_VERSION,M.PAIR_VERSION):return None
    M.validate(policy)
    if policy['hedge_stop']['on_hit']!='close_group':return None
    members=_members(executor,group)
    rows=[executor.journal.query('SELECT * FROM trades WHERE id=?',(m['trade_id'],))[0]
          for m in members if m['leg']=='hedge']
    if not rows or any(row['status']=='open' for row in rows):return None
    from ..cognition.outcomes import timestamp
    latest=max(rows,key=lambda row:(timestamp(row['opened_at']),row['id']))
    if _planned_hedge_close(executor.journal,latest['id'],group):return None
    return request_close(executor,primary,'hedge_closed_unexpectedly')


def resume(executor):
    """Existing 30-second position worker; no new parallel execution loop."""
    out=[];handled=set()
    pending=(executor.journal.query("SELECT * FROM coordinated_group_unwinds WHERE state!='CLOSED' ORDER BY group_id LIMIT 33")
             if _exists(executor.journal) else [])
    if len(pending)>32:raise ValueError('coordinated_unwind_active_group_bound_exceeded')
    for row in pending:
        handled.add(row['group_id'])
        try:
            plan=_plan(row)
            out.append(close_group_once(executor,plan['group_id'],plan['logical_id'],reason=plan['reason']))
        except Exception as exc:
            out.append(_unavailable(row['group_id'],exc))
    # A native stop can close the primary without passing Executor.close.
    # Reconciliation records that close; only a fresh complete venue response
    # may then establish the primary is flat before releasing its hedge.
    hedges=executor.journal.query("SELECT id,entry_identity_json FROM trades WHERE status='open' AND "
        "json_extract(CASE WHEN json_valid(entry_identity_json) THEN entry_identity_json ELSE '{}' END,"
        "'$.coordinated.leg')='hedge' ORDER BY id LIMIT 33")
    if len(hedges)>32:raise ValueError('coordinated_unwind_active_group_bound_exceeded')
    for hedge in hedges:
        group=owned_group(hedge)
        if not group or group in handled:continue
        if len(out)==32:raise ValueError('coordinated_unwind_active_group_bound_exceeded')
        handled.add(group)
        try:
            members=_members(executor,group)
            primary=[m for m in members if m['leg']=='primary']
            if any(executor.journal.query('SELECT status FROM trades WHERE id=?',(m['trade_id'],))[0]['status']!='closed' for m in primary):
                continue
            out.append(request_close(executor,hedge,'primary_position_closed'))
        except Exception as exc:
            out.append(_unavailable(group,exc))
    primaries=executor.journal.query("SELECT * FROM trades WHERE status='open' AND "
        "json_extract(CASE WHEN json_valid(entry_identity_json) THEN entry_identity_json ELSE '{}' END,"
        "'$.coordinated.leg')='primary' ORDER BY id LIMIT 33")
    if len(primaries)>32:raise ValueError('coordinated_unwind_active_group_bound_exceeded')
    for primary in primaries:
        group=owned_group(primary)
        if not group or group in handled:continue
        if len(out)==32:raise ValueError('coordinated_unwind_active_group_bound_exceeded')
        handled.add(group)
        try:
            result=_hedge_closure_response(executor,primary,group)
            if result is not None:out.append(result)
        except Exception as exc:
            out.append(_unavailable(group,exc))
    return out


def _unavailable(group,exc):
    # Never expose private HTTP exception strings. Stable internal reason
    # codes are sufficient to trace a failure without credentials/payloads.
    code=exc.args[0] if isinstance(exc,ValueError) and exc.args else None
    if not (isinstance(code,str) and 0<len(code)<=100 and all(c in 'abcdefghijklmnopqrstuvwxyz_' for c in code)):
        code=type(exc).__name__
    return dict(group_id=group,status='UNAVAILABLE',reason=code)
