"""Execute an already-owned position signal after retained D6 evidence.

The retained open is a timing benchmark, the later received quote is an
execution reference, and only Executor's actual order/fill books the exit.
This does not approve entries or reinterpret legacy missing source evidence.
"""
import json
import math
import time
from datetime import datetime,timezone
from types import SimpleNamespace

from . import live_signal_evidence as S,paper_exit_evidence as X,exit_signal_context as K
from . import versioned_close as V,coordinated_close as C,entry_authority as A,coordinated_unwind as U
from ..strategy import exit_policy as E
from ..data import market_provenance as P

TABLE='versioned_live_signal_eligibility'
LIVE_POLICY_ID='post-committed-decision-quote.v1'


def ensure(journal):
    with journal._tx() as db:
        db.executescript('''CREATE TABLE IF NOT EXISTS versioned_live_signal_eligibility(
            trade_id TEXT PRIMARY KEY,payload TEXT NOT NULL,sha256 TEXT NOT NULL);
            CREATE TRIGGER IF NOT EXISTS versioned_signal_eligibility_no_update BEFORE UPDATE ON versioned_live_signal_eligibility
            BEGIN SELECT RAISE(ABORT,'signal eligibility is immutable'); END;
            CREATE TRIGGER IF NOT EXISTS versioned_signal_eligibility_no_delete BEFORE DELETE ON versioned_live_signal_eligibility
            BEGIN SELECT RAISE(ABORT,'signal eligibility is immutable'); END;''')


def execute(executor,trade,spec,snap,policy,state,*,quote_reader=None):
    with executor._entry_lock:
        rows=executor.journal.query('SELECT * FROM trades WHERE id=?',(trade['id'],))
        if len(rows)!=1:raise ValueError('signal_owned_trade_missing')
        current=rows[0];identity=json.loads(current['entry_identity_json'])
        group=U.owned_group(current)
        if group and U._exists(executor.journal) and executor.journal.query('SELECT 1 FROM '+U.TABLE+' WHERE group_id=?',(group,)):
            return U.request_close(executor,current,'signal')
        if not group and V.registered(executor.journal,current['id'],identity):
            return V.request_close(executor,current,'signal')
        return _execute(executor,current,spec,snap,policy,state,quote_reader=quote_reader)


def _execute(executor,trade,spec,snap,policy,state,*,quote_reader=None):
    group=U.owned_group(trade)
    if group:
        owned=C.ownership(executor,trade,group)
        if owned['leg']!='primary':raise ValueError('signal_group_primary_required')
        root=json.loads(trade['entry_identity_json'])
    else:root=C.single_ownership(executor,trade)
    with executor.journal._tx() as db:
        body=S.load(db,trade['id'],root)
        row=db.execute('SELECT sha256 FROM '+S.TABLE+' WHERE trade_id=?',(trade['id'],)).fetchone()
    # The quote request follows reading an already committed decision.
    # recorded_ms is capture metadata, not a claimed commit timestamp.
    committed_read_ms=int(time.time()*1000)
    if not body or body['legacy'] or 'context' not in body:return None
    if (body['context']['spec']!=spec.to_dict() or body['context']['symbol']!=trade['symbol']):raise ValueError('signal_source_spec_differs')
    pending=body['signal_decision'];bar=pending['bar_ms']
    if body['context']['as_of_ms']!=pending['observed_at_ms']:
        raise ValueError('signal_source_cut_differs')
    original_policy,before=E.decode(A.canonical(dict(policy=body['policy'],state=body['state_before'])))
    expected,_=E.decode(root['exit_state'])
    observation=E.Observation(**body['decision_observation'])
    decided=E.advance(original_policy,before,observation,body['quantity_at_decision'])
    if original_policy!=expected or policy!=expected or decided.reason!='signal' or observation.bar_ms!=bar:
        raise ValueError('signal_original_transition_differs')
    benchmark=X.executable_fill(body,spec,snap) # optional historical comparison, never execution authority
    sources=K.verify_sources(body['context'],bar)
    inputs=pending['inputs']
    frame=K.unpack(body['context']['groups'])['frames'][spec.timeframe]
    rows=X._receipt_rows(frame)
    retained=[r for r in rows if r['ts']<=bar][-pending['lookback_bars']:]
    if (len(inputs)!=pending['lookback_bars'] or not inputs or inputs[-1]['ts']!=bar
            or any(b['ts']-a['ts']!=policy.step_ms for a,b in zip(inputs,inputs[1:]))
            or any(any(k not in r for k in P.META) or r['quality']!='VALID' or r['bar_state']!='FINAL'
                   or r['ts']!=r['event_time_ms']
                   or r['available_at_ms']>pending['observed_at_ms'] or r['observed_at_ms']>pending['observed_at_ms']
                   for r in inputs)
            or pending['observed_at_ms']<bar+policy.step_ms):
        raise ValueError('signal_original_source_unavailable')
    if (retained!=inputs or inputs[-1]['instrument_id']!=root['execution_binding']['instrument_id']
            or any(getattr(observation,k)!=inputs[-1][k] for k in ('open','high','low','close'))):
        raise ValueError('signal_original_source_differs')
    if committed_read_ms>bar+policy.step_ms+P.freshness_ms(spec.timeframe):
        raise ValueError('signal_execution_stale')
    if quote_reader is None:
        from .coordinated_trials import quote as quote_reader
    quote=quote_reader(SimpleNamespace(ex=executor.ex),trade['symbol'],'linear')
    now=int(time.time()*1000)
    if (quote['instrument_id']!=root['execution_binding']['instrument_id']
            or quote['symbol']!=trade['symbol'] or quote['contract']!='linear'
            or not quote.get('source')
            or not all(type(quote[k]) is int for k in ('started_ms','received_ms'))
            or not committed_read_ms<=quote['started_ms']<=quote['received_ms']<=now
            or quote['received_ms']>bar+policy.step_ms+P.freshness_ms(spec.timeframe)):
        raise ValueError('signal_execution_quote_identity_or_clock')
    bid,ask=quote['bid'],quote['ask']
    if any(isinstance(v,bool) or not isinstance(v,(float,int)) or not math.isfinite(v) or v<=0 for v in (bid,ask)) or bid>ask:
        raise ValueError('signal_execution_quote_invalid')
    price=bid if trade['side']=='long' else ask
    quote_bar=quote['received_ms']//policy.step_ms*policy.step_ms
    result=E.execute_open(policy,state,quote_bar,price,float(trade['amount']))
    reference=dict(price=price,basis='received_executable_bid_ask_not_fill',
        observed_at=datetime.fromtimestamp(quote['received_ms']/1000,timezone.utc).isoformat())
    evidence=dict(authority='STRATEGY',version_id=root['version_id'],install_id=root['install_id'],
        spec_hash=root['spec_sha256'],exit_semantics_id=E.EXIT_SEMANTICS_ID,intent=result.reason,
        intended_quantity=result.quantity,state=json.loads(E.encode(policy,result.state)),decision_reference=reference)
    if group:evidence.update(group_id=group,group_exit_semantics_id=root['exit_semantics_id'],scope='WHOLE_POSITION')
    proof=dict(schema='live-signal-eligibility.v1',trade_id=trade['id'],decision_sha256=row['sha256'],
        decision_id=pending['decision_id'],execution_policy=LIVE_POLICY_ID,benchmark=benchmark,sources=sources,committed_read_ms=committed_read_ms,
        quote=quote,decision=evidence,fill_claim=False,financial_result=None)
    if len(A.canonical(proof).encode())>S.MAX_BYTES:raise ValueError('signal_eligibility_bound')
    ensure(executor.journal)
    if group:U.ensure(executor.journal)
    else:V.ensure(executor.journal)
    with executor.journal._tx() as db:
        old=db.execute('SELECT * FROM '+TABLE+' WHERE trade_id=?',(trade['id'],)).fetchone()
        if old:
            proof=json.loads(old['payload'])
            if A.digest(proof)!=old['sha256'] or proof['decision_sha256']!=row['sha256']:
                raise ValueError('signal_eligibility_corrupt')
            evidence=proof['decision']
        else:
            db.execute('INSERT INTO '+TABLE+' VALUES(?,?,?)',(trade['id'],A.canonical(proof),A.digest(proof)))
        evidence=dict(evidence,signal_eligibility_sha256=A.digest(proof))
        # Commit eligibility and the recoverable close plan together. A crash
        # before the order must not leave an orphan proof needing new candles.
        if group:
            registered=db.execute('SELECT * FROM '+U.TABLE+' WHERE group_id=?',(group,)).fetchone()
            if registered is None:
                decision=U._exit_decision(trade,evidence,group,evidence['intent'])
                plan=U.new_plan(executor,group,A.digest(['coordinated-position-close',group]),evidence['intent'],decision)
                db.execute('INSERT INTO '+U.TABLE+' VALUES(?,?,?,?,?)',
                    (group,plan['logical_id'],A.canonical(plan),A.digest(plan),'UNWINDING'))
            elif not any(m['trade_id']==trade['id'] and m['entry_identity_sha256']==A.digest(root)
                         for m in U._plan(registered)['members']):
                raise ValueError('coordinated_unwind_members_changed')
        else:
            registered=db.execute('SELECT * FROM '+V.TABLE+' WHERE trade_id=?',(trade['id'],)).fetchone()
            if registered is None:
                plan=V.new_plan(trade,root,evidence['intent'],evidence)
                db.execute('INSERT INTO '+V.TABLE+' VALUES(?,?,?,?)',(trade['id'],A.canonical(plan),A.digest(plan),'CLOSING'))
            elif V.load(registered)['entry_identity_sha256']!=A.digest(root):
                raise ValueError('versioned_close_entry_differs')
    if group:return U.request_close(executor,dict(trade,_strategy_exit_evidence=evidence),evidence['intent'])
    return V.request_close(executor,trade,evidence['intent'],evidence=evidence)
