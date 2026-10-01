"""Adapters for existing decision/booking/research producers; no new data reads.

Invocations run inside the producer's journal transaction. Capture failures are
contained and recorded. No acquisition, strategy activation or learning apply.
"""
import json
from copy import deepcopy
from dataclasses import asdict
from . import capture as C, foundation as L
from trader.cognition.outcomes import timestamp

CONFIG_KEYS=('mode','universe','derivatives','timeframes','risk','mechanism',
             'research','strategies','scouts','references','attention','attention_learning')


def freeze(db, role, raw, cut, producer):
    identity=L.digest(raw)
    if isinstance(raw,dict):
        key={'portfolio':'snapshot_id','strategy':'version_id','risk_config':'config_version',
             'economics':'receipt_id','proposal':'proposal_id','intent':'intent_id','control':'version'}.get(role)
        if key: identity=raw.get(key) or identity
        if role=='world':
            from trader.world.replay import WorldModelRecord
            identity=WorldModelRecord.from_json(raw['record_json']).model_id
        if role=='context':
            from trader.cognition.opportunity_context import OpportunityContext
            identity=OpportunityContext.from_json(raw['context_json']).context_id
    return C.snapshot(db, role, raw, source_id=identity, version='prospective-source.v1',
                      available_ms=cut, producer=producer)


def frame_chunks(db, snap, cut):
    chunks = {}
    def frame(value):
        if value is None:
            return None
        raw = exact_frame(value)
        dep = freeze(db, 'data', raw, cut, 'existing Snapshot DataFrame; raw observed values')
        return dep['sha256']
    chunks['dfs'] = {k: frame(v) for k,v in snap.dfs.items()}
    chunks['derivs'] = {k:frame(v) for k,v in (snap.derivs or {}).items()}
    chunks['market'] = {k:frame(v) for k,v in (snap.market or {}).items()}
    chunks['universe'] = {symbol:{k:frame(v) for k,v in frames.items()}
                          for symbol,frames in (snap.universe or {}).items()}
    return dict(format='snapshot-frame-chunks.v1', chunks=chunks, cutoff_ms=cut,
                original_snapshot_ts=snap.ts,
                symbol=snap.symbol, market_type=snap.market_type, price=snap.price,
                btc_context=snap.btc_ctx,regime=snap.regime,adx=snap.adx,btc_trend=snap.btc_trend,
                macro_note=snap.macro_note,semantics='raw values available at snapshot cut; no final-bar claim')


def exact_frame(frame):
    """Preserve binary float values and nanosecond timestamps, not JSON rounding."""
    import math
    import pandas as pd
    from datetime import datetime,date
    def scalar(v):
        if v is pd.NA: return {'missing':'pd.NA'}
        if v is pd.NaT: return {'missing':'pd.NaT'}
        if hasattr(v,'item'): v=v.item()
        if isinstance(v,(pd.Timestamp,datetime,date)):
            return {'timestamp':v.isoformat(),'type':type(v).__name__}
        if isinstance(v,float) and not math.isfinite(v):
            return {'float_hex':v.hex()}
        if v is None or isinstance(v,(str,int,float,bool)): return v
        if isinstance(v,tuple): return {'tuple':[scalar(x) for x in v]}
        raise ValueError('unsupported_original_frame_scalar:'+type(v).__name__)
    return dict(format='exact-dataframe.v1',columns=[scalar(v) for v in frame.columns],
        index=[scalar(v) for v in frame.index],index_names=list(frame.index.names),
        column_names=list(frame.columns.names),dtypes=[str(d) for d in frame.dtypes],
        data=[[scalar(v) for v in row] for row in frame.itertuples(index=False,name=None)])


def restore_frame(raw):
    """Restore retained data without reading a feed, registry or latest config."""
    import pandas as pd
    from datetime import datetime,date
    if raw['format']!='exact-dataframe.v1': raise ValueError('dataframe_format_unregistered')
    def scalar(v):
        if not isinstance(v,dict): return v
        if 'float_hex' in v: return float.fromhex(v['float_hex'])
        if 'missing' in v: return pd.NA if v['missing']=='pd.NA' else pd.NaT
        if 'tuple' in v: return tuple(scalar(x) for x in v['tuple'])
        if 'timestamp' in v:
            types={'Timestamp':pd.Timestamp,'datetime':datetime.fromisoformat,'date':date.fromisoformat}
            return types[v['type']](v['timestamp'])
        raise ValueError('dataframe_scalar_unregistered')
    columns=[scalar(v) for v in raw['columns']]
    index=[scalar(v) for v in raw['index']]
    def axis(values,names):
        return pd.MultiIndex.from_tuples(values,names=names) if len(names)>1 else pd.Index(values,name=names[0])
    frame=pd.DataFrame([[scalar(v) for v in row] for row in raw['data']],columns=axis(columns,raw['column_names']),
                       index=axis(index,raw['index_names']))
    for i,dtype in enumerate(raw['dtypes']):
        frame.isetitem(i,frame.iloc[:,i].astype(dtype))
    if L.digest(exact_frame(frame))!=L.digest(raw):
        raise ValueError('retained_dataframe_roundtrip_differs')
    return frame


def decision(db, row, cycle, inputs=None):
    """No lookup of latest context/config/strategy after original registration."""
    cut = timestamp(row['ts'])
    votes=[dict(v) for v in db.execute('SELECT * FROM votes WHERE cycle_id=? AND symbol=? ORDER BY rowid',
                                     (row['cycle_id'],row['symbol']))]
    deps = [freeze(db,'decision',row,cut,'Journal.log_decision'),
            freeze(db,'reasons',dict(skip_reason=row.get('skip_reason'), reason_codes=row.get('reason_codes'),
                                    signals=row.get('signals_json'),votes=votes),cut,'original decision and recorded votes')]
    lineage = asdict(L.Lineage(row['cycle_id'],row['id'],None))
    if cycle:
        deps.append(freeze(db,'cycle',cycle,cut,'Journal.log_cycle'))
    if inputs:
        inputs=bind_signal_version(db,inputs,row)
        source_cut=inputs.get('source_cut_ms',cut)
        if source_cut>cut: raise ValueError('source_cut_after_decision')
        snap, config = inputs.get('snapshot'), inputs.get('config')
        if snap is not None:
            lineage['regime']=snap.regime
            snapshot_cut = timestamp(snap.ts)
            if snapshot_cut > cut:
                raise ValueError('snapshot_after_decision')
            data=frame_chunks(db,snap,source_cut)
            data['additional_original_inputs']=inputs.get('additional_original_inputs')
            data['source_evidence']=inputs.get('source_evidence')
            deps.append(freeze(db,'data',data,source_cut,'original Snapshot and supplied market inputs observed before decision'))
        if config is not None:
            # Code-owned allowlist: no credentials, transport endpoints or .env.
            safe = {k:config[k] for k in CONFIG_KEYS if k in config}
            cfg = dict(config=safe, risk=safe.get('risk'), config_version=L.digest(safe), risk_version=L.digest(safe.get('risk')))
            deps.append(freeze(db,'risk_config',cfg,source_cut,'configuration used by decision producer'))
        for role in ('world','context','portfolio','control','economics','proposal','intent','derivative_identity'):
            value = inputs.get(role)
            if value is not None:
                if role=='portfolio':
                    from trader.engine.evidence_capture import verify_snapshot
                    if verify_snapshot(value) is None or value.get('completeness')!='COMPLETE':
                        deps.append(C.unavailable('portfolio','EXACT_VENUE_STATE_UNAVAILABLE_JOURNAL_NOT_TRUTH'))
                        continue
                clock_role={'world':'world_model','strategy':'strategy_version'}.get(role,role)
                clock=(inputs.get('source_evidence') or {}).get(clock_role,{})
                deps.append(freeze(db,role,value,clock.get('known_at_ms',source_cut),'explicit original producer input'))
        if inputs.get('proposal'):
            lineage['proposal_id']=inputs['proposal']['proposal_id']
        if inputs.get('intent'):
            lineage['intent_id']=inputs['intent']['intent_id']
        lineage['opportunity_id']=inputs.get('opportunity_id')
        if inputs.get('world'):
            from trader.world.replay import WorldModelRecord
            lineage['world_id']=WorldModelRecord.from_json(inputs['world']['record_json']).model_id
        if inputs.get('context'):
            from trader.cognition.opportunity_context import OpportunityContext
            lineage['context_id']=OpportunityContext.from_json(inputs['context']['context_json']).context_id
        if inputs.get('strategy'):
            v=inputs['strategy']
            lineage.update(strategy_id=v['strategy_id'],version_id=v['version_id'],spec_hash=v['spec_hash'])
            try:
                version_dep=C.reference(db,'strategy','strategy_versions',v['version_id'],version=v['schema'],available_ms=cut,producer='exact signal-bound StrategyVersion')
                if version_dep['status']!='AVAILABLE': raise ValueError('version_source_absent')
                if C.resolve(db,version_dep)!=v: raise ValueError('supplied_exact_strategy_bytes_differ')
            except (ValueError,__import__('sqlite3').OperationalError):
                version_dep=freeze(db,'strategy',v,cut,'exact strategy version used')
            deps.append(version_dep)
            if inputs.get('exit_semantics') and inputs['exit_semantics'].get('exit_semantics_id'):
                deps.append(freeze(db,'exit_semantics',inputs['exit_semantics'],cut,'exact version exit binding'))
            else:
                deps.append(C.unavailable('exit_semantics','EXACT_EXIT_BINDING_UNAVAILABLE'))
        elif row.get('strategy_ids'):
            # Do not pick today's version for an old/unversioned signal.
            lineage['strategy_id']=row['strategy_ids']
            deps.append(C.unavailable('strategy','EXACT_SIGNAL_VERSION_NOT_SUPPLIED'))
    kind = L.Kind.CASH if row['action']=='HOLD' else L.Kind.REJECTED
    rid=C.register(db,'decision:'+row['id'],kind.value,lineage,cut,deps)
    C.attach(db,'decision:'+row['id'],'decision:'+row['id']+':pending',kind.value,L.Boundary.UNRESOLVED.value,dict(reason='future_outcome_pending'),cut)
    C.record_action(db,'decision:'+row['id'],dict(action=row['action'],executed=row['executed'],skip_reason=row.get('skip_reason')),cut)
    return rid


def action(db, row, at_ms, risk_value=None):
    event = 'decision:'+row['id']
    # If a historical row has no prospective registration, refusal is explicit.
    return C.record_action(db,event,dict(action=row['action'],executed=row['executed'],
        size_usdt=row.get('size_usdt'),skip_reason=row.get('skip_reason'),reason_codes=row.get('reason_codes')),
        at_ms) if risk_value is None else C.record_action(db,event,risk_value,at_ms,risk=True)


def booking(db, trade_id, receipt):
    row=db.execute('SELECT * FROM trades WHERE id=?',(trade_id,)).fetchone()
    trade=dict(row)
    event='decision:'+trade['decision_id']
    C.registration(db,event)
    at_ms=receipt['observed_ms']
    dep=freeze(db,'execution',receipt,at_ms,'existing trade-booking.v1')
    action=C.record_action(db,event,dict(action='EXECUTED',trade_id=trade_id,exec_mode=trade['exec_mode']),at_ms)
    closed=trade['status']=='closed'
    observation={'reason':'booking_not_complete_realized_accounting' if closed else 'awaiting_trade_outcome'}
    deps=[dep,action,freeze(db,'trade',trade,at_ms,'exact journal booking state; money unverified'),
          freeze(db,'outcome',dict(observation=observation,booking=receipt),at_ms,'original booking event')]
    order_id=receipt['evidence'].get('order_id')
    return C.attach(db,event,'booking:'+receipt['sha256'],L.Kind.EXECUTED.value,
        L.Boundary.UNASSESSABLE.value if closed else L.Boundary.UNRESOLVED.value,
        observation,at_ms,deps,
        post_lineage=dict(trade_ids=(trade_id,),execution_ids=(str(order_id),) if order_id is not None else ()))


def forward(db, original, updates, targets, now_ms):
    event='decision:'+original['decision_id']
    _,reg=C.registration(db,event)
    # Registered reasons and initial action are frozen; no text-based Risk inference.
    latest=db.execute('SELECT * FROM decisions WHERE id=?',(original['decision_id'],)).fetchone()
    if latest and latest['executed']:
        # A price path is still unrealized; it cannot replace the trade's monetary result.
        kind=L.Kind.EXECUTED.value
    else:
        kind=reg['kind']
    action_rows=db.execute('SELECT payload FROM learning_actions WHERE event_key=?',(event,)).fetchall()
    actions=sorted([json.loads(r[0])['dependency'] for r in action_rows],
                   key=lambda d:(d['available_ms'],d['sha256']))
    risk=[d for d in actions if d['role']=='risk_decision']
    if risk and C.resolve(db,risk[-1]).get('ok') is False:
        kind=L.Kind.RISK_BLOCKED.value
    deps=[]
    action_dep=next((d for d in reversed(actions) if d['role']=='action' and 'prediction' not in C.resolve(db,d)),None)
    if action_dep: deps.append(action_dep)
    if risk: deps.append(risk[-1])
    observation=dict(measurement='counterfactual_price_return_not_money', returns=updates)
    prediction_rows=[d for d in actions if d['role']=='action' and 'prediction' in C.resolve(db,d)]
    declaration=C.resolve(db,prediction_rows[0])['prediction'] if prediction_rows else {k:original[k] for k in ('decision_id','cycle_id','symbol','ts','action','entry_price')}
    if prediction_rows:
        deps.append(freeze(db,'prediction',dict(declaration=declaration),prediction_rows[0]['available_ms'],'original schedule_outcome'))
    measurement=dict(observation=observation, protocol='existing-forward-outcome.v1',
        declaration=declaration, target_bars=targets, measured_ms=now_ms)
    key='forward:'+L.digest(dict(event=event,observation=observation,declaration=declaration,
        target_bars=targets,action_hashes=[d['sha256'] for d in deps]))
    previous=C.existing_outcome(db,key)
    if previous: return previous
    deps.append(freeze(db,'outcome',measurement,now_ms,'engine.outcomes.resolve_pending'))
    return C.attach(db,event,key,kind,L.Boundary.COUNTERFACTUAL.value,
                    observation,now_ms,deps)


def research_bank(db, row, recorded_ms, journal, *, unreadable=False):
    if unreadable:
        from trader.cognition import research_unreadable_bank as B
    else:
        from trader.cognition import research_bank as B
    rec, chain=B.verify_row(journal, dict(row,recorded_at_ms=recorded_ms))
    q=chain['question']
    # These exact registered protocol objects do not assert trading context.
    initial=[]
    for role,key in (('question','question'),('plan','plan'),('research_evidence','evidence')):
        initial.append(freeze(db,role,chain[key],recorded_ms,'verified Research Bank linked chain'))
    lineage=asdict(L.Lineage(None,None,None))
    key='research-bank:'+rec['bank_object_id']
    C.register(db,key,L.Kind.RESEARCH.value,lineage,recorded_ms,initial,profile='RESEARCH')
    observation=dict(result_status=rec.get('result_status',rec.get('result',{}).get('status')),
        result_reason=rec.get('result_reason',rec.get('result',{}).get('reason')),
        bank_object_id=rec['bank_object_id'],question_id=q['question_id'],authority='recorded_result_not_claim_equivalence')
    deps=[freeze(db,'research_run',dict(chain=chain),recorded_ms,'verified research run/telemetry chain'),freeze(db,'falsifier_result',chain['result'],recorded_ms,'verified research result; predicates may be absent'),
          freeze(db,'bank',rec,recorded_ms,'verified Research Bank object'),
          freeze(db,'outcome',dict(observation=observation),recorded_ms,'verified Research Bank result')]
    return C.attach(db,key,key,L.Kind.RESEARCH.value,L.Boundary.REALIZED.value,observation,recorded_ms,deps)


def investigation_research(db,chain,bank,recorded_ms):
    """The producer supplies its just-built, registered family chain."""
    key='research-bank:'+bank['bank_object_id']
    deps=[freeze(db,role,chain[name],recorded_ms,'investigation research producer')
          for role,name in (('question','question'),('plan','plan'),('research_evidence','evidence'))]
    C.register(db,key,L.Kind.RESEARCH.value,asdict(L.Lineage(None,None,None)),recorded_ms,deps,profile='RESEARCH')
    observation=dict(result_status=bank['result_status'],result_reason=bank['result_reason'],
        bank_object_id=bank['bank_object_id'],question_id=chain['question']['question_id'],
        authority='registered_observable_research_result_not_predictive_edge')
    deps=[freeze(db,'research_run',dict(chain=chain),recorded_ms,'investigation research run'),
          freeze(db,'falsifier_result',chain['result'],recorded_ms,'registered family result'),
          freeze(db,'bank',bank,recorded_ms,'exact Research Bank family object'),
          freeze(db,'outcome',dict(observation=observation),recorded_ms,'registered research result')]
    return C.attach(db,key,key,L.Kind.RESEARCH.value,L.Boundary.REALIZED.value,observation,recorded_ms,deps)


def incident(db,event,at_ms,subject,detail):
    key='incident:'+str(event)
    lineage=asdict(L.Lineage(None,None,None))
    deps=[freeze(db,'data',dict(subject=subject,detail=detail),at_ms,'original incident producer')]
    C.register(db,key,L.Kind.INCIDENT.value,lineage,at_ms,deps,profile='INCIDENT')
    return C.attach(db,key,key,L.Kind.INCIDENT.value,L.Boundary.UNASSESSABLE.value,
                    dict(subject=subject,detail=detail,reason='incident causal linkage unavailable'),at_ms)


def runtime_inputs(journal, snap, config, *, cut_ms, additional_original_inputs=None, control_state=None):
    """Called before deciding, never when replaying historical rows."""
    safe = {k:config[k] for k in CONFIG_KEYS if k in config}
    result=dict(snapshot=deepcopy(snap),config=json.loads(L.canonical(safe)),source_cut_ms=cut_ms,
        additional_original_inputs=deepcopy(additional_original_inputs))
    # Exact objects must actually be carried by the decision producer. No
    # lookup of latest external context/WorldModel using a timestamp shortcut.
    if control_state is not None:
        record={'state':getattr(control_state,'value',control_state)}
        result['control']=dict(state=record['state'],record=record,version=L.digest(record))
    result.update(deepcopy(getattr(snap,'learning_sources',{}) or {}))
    return result


def missed_snapshot(db,scan_id,symbol,at_ms):
    """Capture an actually observed skip, without inventing an opportunity."""
    key='missed-snapshot:'+L.digest([scan_id,symbol,at_ms])
    lineage=asdict(L.Lineage(scan_id,None,None))
    deps=[C.unavailable('data','SNAPSHOT_PRODUCER_RETURNED_NONE'),
          freeze(db,'reasons',{'symbol':symbol,'reason':'missing_snapshot'},at_ms,'Kernel scan skip')]
    C.register(db,key,L.Kind.MISSED.value,lineage,at_ms,deps)
    action=C.record_action(db,key,{'action':'SKIPPED','symbol':symbol},at_ms)
    out=freeze(db,'outcome',{'observation':{'reason':'no_original_snapshot_or_registered_future_measurement'}},at_ms,'observed scan skip')
    return C.attach(db,key,key,L.Kind.MISSED.value,L.Boundary.UNASSESSABLE.value,
        {'reason':'no_original_snapshot_or_registered_future_measurement'},at_ms,[action,out])


def bind_signal_version(db, inputs, row):
    signals=json.loads(row.get('signals_json') or '[]')
    agreeing=[s for s in signals if s.get('action')==row['action']]
    if not agreeing or not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='strategy_versions'").fetchone():
        return inputs
    top=max(agreeing,key=lambda s:s.get('confidence',0))
    params=top.get('params') or {}
    sh=params.get('spec_sha256')
    if not sh: return inputs
    rows=db.execute('SELECT canonical_json,recorded_at_ms FROM strategy_versions WHERE strategy_id=? AND spec_hash=?',
                    (top['strategy_id'],sh)).fetchall()
    if len(rows)!=1 or rows[0][1]>timestamp(row['ts']): return inputs
    version=json.loads(rows[0][0])
    # A durable immutable reference when the upstream actually protects it;
    # otherwise a content snapshot of the exact signal-bound version.
    result=dict(inputs,strategy=version)
    installs=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='strategy_version_installs'").fetchone()
    if installs:
        matches=db.execute('SELECT canonical_json FROM strategy_version_installs WHERE version_id=?',(version['version_id'],)).fetchall()
        if len(matches)==1:
            install=json.loads(matches[0][0])
            result['exit_semantics']=dict(exit_semantics_id=install.get('exit_semantics_id'),install=install)
    return result


def forecast_registration(db, episode_id, prediction, symbol):
    cut=prediction['registered_ms']
    event='forecast:'+episode_id
    deps=[freeze(db,'question',dict(episode_id=episode_id,symbol=symbol,prediction=prediction),cut,'registered forecast protocol'),
          freeze(db,'data',prediction['input_window'],cut,'forecast baseline versions')]
    C.register(db,event,L.Kind.RESEARCH.value,asdict(L.Lineage(None,None,None)),cut,deps,profile='RESEARCH')
    C.attach(db,event,event+':pending',L.Kind.RESEARCH.value,L.Boundary.UNRESOLVED.value,
             dict(reason='forecast_target_pending'),cut)


def forecast_result(db, episode):
    from trader.cognition.outcomes import forecast
    cut=episode['outcome']['recorded_ms']
    verified=forecast(episode,cut)
    observation=verified['observation']
    dep=freeze(db,'outcome',dict(observation=observation,typed_receipt=verified),cut,'verified forecast.v1')
    return C.attach(db,'forecast:'+episode['id'],'forecast:'+episode['id']+':resolved',L.Kind.RESEARCH.value,
        L.Boundary.REALIZED.value,observation,cut,[dep])


def verified_trade(db, receipt):
    """Retain a producer's already verified receipt; never collect venue data.

    Requires an original registration in this ledger. Whole-trade accounting is
    currently an offline producer, so its absence in the automatic booking path
    stays UNASSESSABLE rather than claiming money from journal price arithmetic.
    """
    from trader.cognition.outcomes import replay
    from trader.engine.trade_accounting import replay as accounting_replay
    receipt=replay(receipt)
    whole=receipt['source']['whole_trade_capture']
    accounting=accounting_replay(whole)['accounting']
    trade=whole['bookings']['trade']
    event='decision:'+trade['decision_id']
    C.registration(db,event)
    key='verified-trade:'+whole['sha256']
    previous=C.existing_outcome(db,key)
    if previous: return previous
    cut=receipt['imported_ms']
    observation=dict(net_pnl=receipt['actual_execution']['net_pnl'],
                     currency=receipt['actual_execution']['currency'],environment=whole['environment'])
    deps=[freeze(db,'outcome',dict(observation=observation,typed_receipt=receipt),cut,'verified whole-trade outcome'),
          freeze(db,'execution',whole,cut,'existing whole-trade-accounting.v1'),
          freeze(db,'trade',trade,cut,'verified whole-trade booking export'),
          freeze(db,'cost',accounting,cut,'exact fill/commission accounting'),
          freeze(db,'funding',accounting,cut,'exact owned funding cashflows/publication frontier'),
          freeze(db,'execution_reference',dict(trade_id=trade['id'],timestamp_ms=whole['start_ms'],
              basis='exact venue order and fill references',orders=whole['orders']),cut,'existing venue receipt'),
          freeze(db,'position_basis',dict(trade_id=trade['id'],timestamp_ms=whole['observed_ms'],
              basis='verified one-way balanced fill stream and terminal position',position=whole['position'],
              fills=whole['fill_history']),cut,'existing whole-trade ownership verification')]
    actions=[json.loads(r[0])['dependency'] for r in db.execute(
        'SELECT payload FROM learning_actions WHERE event_key=?',(event,))]
    for role in ('action','risk_decision'):
        candidates=[d for d in actions if d['role']==role and d['available_ms']<=cut]
        if candidates: deps.append(max(candidates,key=lambda d:(d['available_ms'],d['sha256'])))
    return C.attach(db,event,key,L.Kind.EXECUTED.value,
        L.Boundary.REALIZED.value,observation,cut,deps,
        post_lineage=dict(trade_ids=(trade['id'],),execution_ids=tuple(str(o['orderId']) for o in whole['orders'])))


def paper_cost(db, trade, receipt, *, observed_ms):
    """Automatic immutable references at the existing cost-finalization producer.

    Paper fills remain simulated. Missing calibrated cost authorities remain
    unavailable, even when a public funding interval was exactly captured.
    """
    event='decision:'+trade['decision_id']
    C.registration(db,event)
    at_ms=observed_ms
    sources={}
    for kind,sid in receipt['source_ids'].items():
        row=db.execute('SELECT canonical_json FROM versioned_paper_cost_sources WHERE source_id=?',(sid,)).fetchone()
        if row:
            raw=json.loads(row[0]);sources[kind]=raw
            if raw['captured_ms']>at_ms:
                raise ValueError('paper_cost_source_not_available_at_capture')
    cost=C.reference(db,'cost','versioned_paper_cost_receipts',trade['id'],version=receipt['schema'],
                     available_ms=at_ms,producer='existing paper cost finalization')
    # Retain exact adapters/raw response bodies without duplicating journal data.
    bundle=dict(receipt=receipt,sources=sources)
    observation=dict(reason='paper_execution_simulated; complete calibrated costs required',
                     known_costs_complete=receipt['known_costs_complete'])
    deps=[cost,freeze(db,'trade',trade,at_ms,'paper cost producer'),
          freeze(db,'execution',bundle,at_ms,'exact paper cost sources'),
          freeze(db,'outcome',dict(observation=observation),at_ms,'paper cost finalization')]
    bound=receipt['binding']
    if bound['entry']['reference'] is not None:
        deps.append(freeze(db,'execution_reference',dict(trade_id=trade['id'],
            timestamp_ms=bound['entry']['time_ms'],basis=trade.get('fill_basis'),
            entry=bound['entry'],exit=bound['exit'],label='SIMULATED / UNREALIZED'),at_ms,'exact paper reference receipt'))
    identity=json.loads(trade['entry_identity_json'])
    exposure=identity.get('paper_exposure')
    if exposure:
        deps.append(freeze(db,'position_basis',dict(trade_id=trade['id'],timestamp_ms=exposure['opened_ms'],
            basis=exposure['basis'],exposure=exposure,label='SIMULATED / UNREALIZED'),at_ms,'immutable isolated paper exposure'))
    source=sources.get('funding',{})
    funding=source.get('provenance',{}).get('receipt')
    if funding: deps.append(freeze(db,'funding',funding,at_ms,'existing public funding interval producer'))
    return C.attach(db,event,'paper-cost:'+L.digest(receipt),L.Kind.EXECUTED.value,
        L.Boundary.UNASSESSABLE.value,observation,at_ms,deps,post_lineage=dict(trade_ids=(trade['id'],)))


def journalize_allocation(journal, snap, decision, config, receipt, proposal, allocation_inputs):
    """Prospective producer adapter: carry the exact Stage-6 objects into capture.

    The existing allocator/intent constructors own their decisions. This adapter
    only records what they consumed; it never invokes Risk or routes an intent.
    """
    from trader.portfolio import opportunity_live as live, allocator as A, trade_intent as T, economics as E
    p = json.loads(receipt.payload_json)
    live.replay(receipt, tuple(A.Source(**s) for s in p['sources']), allocation_inputs.as_of_ms)
    if not A.verify(proposal, allocation_inputs): raise ValueError('exact_allocation_refused')
    if decision.cycle_id != p['cycle_id'] or timestamp(decision.ts) != allocation_inputs.as_of_ms:
        raise ValueError('decision_allocation_cut_or_cycle_differs')
    candidates = [c for c in allocation_inputs.candidates if c.opportunity_context_json == p['context_json']]
    if len(candidates) != 1: raise ValueError('exact_decision_candidate_unavailable')
    c = candidates[0]
    er = E.from_payload(json.loads(c.economics.receipt_json))
    intents = [i for i in T.build(proposal, allocation_inputs) if i.payload()['opportunity_id'] == c.opportunity_id]
    if len(intents) != 1: raise ValueError('exact_candidate_intent_unavailable')
    original = {s['source_id']: json.loads(s['payload_json']) for s in p['sources']}
    carried = dict(context={'context_json': p['context_json'], 'live_receipt': json.loads(receipt.payload_json)},
        opportunity_id=c.opportunity_id, economics=er.payload(), proposal=proposal.payload(),
        intent=dict(intent_id=intents[0].intent_id, payload_json=intents[0].payload_json),
        control=dict(state=allocation_inputs.control_state, record={'state':allocation_inputs.control_state},
                     version=L.digest({'state':allocation_inputs.control_state})))
    if 'world_model' in original: carried['world'] = {'record_json':original['world_model']['data']}
    if 'portfolio' in original:
        carried['portfolio'] = original['portfolio']['data']
        carried['derivative_identity'] = {k:carried['portfolio'][k] for k in ('venue','market_type','environment')}
    if 'strategy_version' in original:
        v = json.loads(original['strategy_version']['data']['canonical_json'])
        carried['strategy'] = v
        authorities = [json.loads(s.payload_json)['result'] for s in allocation_inputs.sources
                       if s.source_id.startswith('candidate-bridge:')]
        if len(authorities) == 1:
            carried['exit_semantics'] = {'exit_semantics_id':authorities[0]['exit_semantics_id']}
    carried['source_evidence'] = {role:{k:v for k,v in raw.items() if k != 'data'} for role,raw in original.items()}
    inputs = runtime_inputs(journal, snap, config, cut_ms=allocation_inputs.as_of_ms)
    inputs.update(carried)
    journal.log_cycle(snap, decision.cycle_id, 'prospective-allocation-capture')
    journal.log_decision(decision, capture_inputs=inputs)
    return intents[0]
