"""Durable full close of one already-owned, ungrouped versioned trade.

The existing reduction transport/Executor books actual fills. A plan retains
its first reason and frozen decision across partial fills, ambiguous replies
and restarts. It confers no entry, approval or financial qualification.
"""
import json
import math
import time

from . import coordinated_close as C,coordinated_unwind as U,entry_authority as A
from ..strategy import exit_policy as E

TABLE='versioned_close_plans'


def exists(journal):
    return bool(journal.query("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(TABLE,)))


def ensure(journal):
    with journal._tx() as db:
        db.execute('''CREATE TABLE IF NOT EXISTS versioned_close_plans(
            trade_id TEXT PRIMARY KEY,payload TEXT NOT NULL,sha256 TEXT NOT NULL,state TEXT NOT NULL)''')
        db.execute("CREATE INDEX IF NOT EXISTS versioned_close_instrument ON versioned_close_plans(json_extract(payload,'$.instrument_id'),state)")


def load(row):
    plan=json.loads(row['payload'])
    if A.digest(plan)!=row['sha256'] or plan['trade_id']!=row['trade_id']:
        raise ValueError('versioned_close_plan_corrupt')
    return plan


def registered(journal,trade_id,identity):
    if not exists(journal):return False
    rows=journal.query('SELECT * FROM '+TABLE+' WHERE trade_id=?',(trade_id,))
    if not rows:return False
    if load(rows[0])['entry_identity_sha256']!=A.digest(identity):
        raise ValueError('versioned_close_entry_differs')
    return True


def entry_block(journal,instrument_id):
    if exists(journal):
        for row in journal.query("SELECT * FROM versioned_close_plans WHERE json_extract(payload,'$.instrument_id')=? AND state!='CLOSED'",(instrument_id,)):
            load(row)
            return 'versioned_position_exit_pending'
    if journal.query("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(C.VERSIONED_TABLE,)):
        rows=journal.query('SELECT r.state,r.request_json,t.status FROM '+C.VERSIONED_TABLE+
            " r LEFT JOIN trades t ON t.id=r.trade_id WHERE json_extract(r.request_json,'$.instrument_id')=? AND "
            "(r.state NOT IN ('BOOKED','REFUSED','NO_FILL') OR t.status='open')",(instrument_id,))
        for row in rows:
            if json.loads(row['request_json'])['instrument_id']==instrument_id:
                return 'versioned_reduction_recovery_pending'
    return None


def decision(trade,root,evidence,reason):
    if (not isinstance(evidence,dict) or evidence.get('authority')!='STRATEGY'
            or evidence.get('intent')!=reason or evidence.get('exit_semantics_id')!=root['exit_semantics_id']
            or any(evidence.get(k)!=root.get(k) for k in ('version_id','install_id'))
            or evidence.get('spec_hash')!=root['spec_sha256']):
        raise ValueError('versioned_close_strategy_decision_differs')
    policy,state=E.decode(A.canonical(evidence['state']));original,_=E.decode(root['exit_state'])
    if policy!=original or state.reason!=reason or A.positive(evidence['intended_quantity'])!=A.positive(trade['amount']):
        raise ValueError('versioned_close_strategy_policy_differs')
    result=dict(evidence,scope='SINGLE_POSITION',source_trade_id=trade['id'],entry_identity_sha256=A.digest(root),
                evidence_scope='STRATEGY_INTENT_NOT_FILL')
    if len(A.canonical(result).encode())>32768:raise ValueError('versioned_close_strategy_decision_bound')
    return result


def new_plan(trade,root,reason,evidence):
    frozen=decision(trade,root,evidence,reason)
    return dict(schema='versioned-whole-close.v1',trade_id=trade['id'],reason=reason,
        instrument_id=root['execution_binding']['instrument_id'],entry_identity_sha256=A.digest(root),
        started_ms=int(time.time()*1000),decision=frozen)


def _positions(executor,trade,plan):
    observation=U._position_observation(executor)
    actual=next((p for p in observation.positions if p.instrument_id.value==plan['instrument_id'] and p.position_present),None)
    if trade['status']=='closed':
        if actual is not None:raise ValueError('versioned_closed_trade_has_venue_exposure')
    elif (actual is None or actual.side!=trade['side']
            or not math.isclose(actual.absolute_quantity,float(trade['amount']),rel_tol=1e-9,abs_tol=1e-12)):
        raise ValueError('versioned_close_owned_quantity_unconfirmed')
    return observation


def request_close(executor,trade,reason,*,evidence=None):
    ensure(executor.journal)
    with executor._entry_lock,executor._lock_for('versioned-close:'+trade['id']):
        rows=executor.journal.query('SELECT * FROM trades WHERE id=?',(trade['id'],))
        if len(rows)!=1:raise ValueError('versioned_close_trade_missing')
        current=rows[0];root=C.single_ownership(executor,current)
        old=executor.journal.query('SELECT * FROM '+TABLE+' WHERE trade_id=?',(trade['id'],))
        if old:
            plan=load(old[0])
            if plan['entry_identity_sha256']!=A.digest(root):raise ValueError('versioned_close_entry_differs')
            if old[0]['state']=='CLOSED':return dict(trade_id=trade['id'],status='CLOSED')
        else:
            if current['status']!='open':return dict(trade_id=trade['id'],status='ALREADY_CLOSED')
            plan=new_plan(current,root,reason,evidence)
            with executor.journal._tx() as db:
                db.execute('INSERT INTO '+TABLE+' VALUES(?,?,?,?)',
                           (trade['id'],A.canonical(plan),A.digest(plan),'CLOSING'))
        if executor.recovery_pending():return dict(trade_id=trade['id'],status='ENTRY_RECOVERY_PENDING')
        C.ensure_versioned(executor.journal)
        history=executor.journal.query('SELECT * FROM '+C.VERSIONED_TABLE+' WHERE trade_id=? ORDER BY rowid LIMIT 65',(trade['id'],))
        if len(history)>64:raise ValueError('versioned_close_history_bound')
        pending=[r for r in history if r['state'] not in ('BOOKED','REFUSED','NO_FILL')]
        if len(pending)>1:raise ValueError('versioned_close_multiple_pending_reductions')
        if not pending:
            if history and history[-1]['state']=='REFUSED':return dict(trade_id=trade['id'],status='REDUCTION_REFUSED')
            observation=_positions(executor,current,plan)
            if current['status']=='closed':
                # Journal close plus current absence completes this plan's
                # control state, not financial attribution or a fill claim.
                with executor.journal._tx() as db:
                    db.execute("UPDATE "+TABLE+" SET state='CLOSED' WHERE trade_id=?",(trade['id'],))
                return dict(trade_id=trade['id'],status='CLOSED',observation_id=observation.observation_id,fill_claim=False)
        logical=(pending[0]['logical_id'] if pending else
                 A.digest(['versioned-whole-close',trade['id'],A.digest(plan),len(history)]))
        result=C.close_versioned_once(executor,trade['id'],logical,reason=plan['reason'],exit_decision=plan['decision'])
        current=executor.journal.query('SELECT status FROM trades WHERE id=?',(trade['id'],))[0]
        closed=current['status']=='closed' and result['status']=='BOOKED'
        if closed:
            with executor.journal._tx() as db:db.execute("UPDATE "+TABLE+" SET state='CLOSED' WHERE trade_id=?",(trade['id'],))
        status=('CLOSED' if closed else 'REDUCTION_REFUSED' if result['status']=='REFUSED' else
                'EXTERNAL_CLOSE_RECONCILIATION_REQUIRED' if result['status']=='EXTERNAL_CLOSE_REQUIRES_RECONCILIATION' else 'CLOSING')
        return dict(trade_id=trade['id'],status=status,reduction=result)


def resume(executor):
    if not exists(executor.journal):return []
    rows=executor.journal.query("SELECT * FROM "+TABLE+" WHERE state!='CLOSED' ORDER BY trade_id LIMIT 33")
    if len(rows)>32:raise ValueError('versioned_close_active_plan_bound')
    out=[]
    for row in rows:
        try:
            plan=load(row)
            out.append(request_close(executor,dict(id=plan['trade_id']),plan['reason']))
        except Exception as exc:
            out.append(dict(trade_id=row['trade_id'],status='UNAVAILABLE',reason=U._unavailable(None,exc)['reason']))
    return out
