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


#: Sources a scan-time decision never consults. They belong to the later
#: Portfolio stage of the SAME decision (see allocation_stage), so the original
#: registration says so explicitly instead of leaving them silently absent.
LATER_STAGE_ROLES = ('world', 'context', 'portfolio', 'economics', 'proposal', 'intent')


def decision(db, row, cycle, inputs=None, *, stage=None):
    """No lookup of latest context/config/strategy after original registration.

    ``stage`` registers a later stage of the same decision (same decision_id and
    cycle) at the stage's own cut; without it this is the original decision.
    """
    cut = stage['cut_ms'] if stage else timestamp(row['ts'])
    event = stage['event_key'] if stage else 'decision:'+row['id']
    votes=[dict(v) for v in db.execute('SELECT * FROM votes WHERE cycle_id=? AND symbol=? ORDER BY rowid',
                                     (row['cycle_id'],row['symbol']))]
    reasons=dict(skip_reason=row.get('skip_reason'), reason_codes=row.get('reason_codes'),
                 signals=row.get('signals_json'),votes=votes)
    if stage: reasons['allocation_stage']=stage['reason']
    deps = [freeze(db,'decision',row,cut,'Journal.log_decision'),
            freeze(db,'reasons',reasons,cut,'original decision and recorded votes')]
    lineage = asdict(L.Lineage(row['cycle_id'],row['id'],None))
    if cycle:
        deps.append(freeze(db,'cycle',cycle,cut,'Journal.log_cycle'))
    if inputs:
        # A stage already carries its exact candidate version; only the
        # original decision infers it from the signal that proposed the action.
        if not stage: inputs=bind_signal_version(db,inputs,row)
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
    present={d['role'] for d in deps}
    not_consulted={} if stage else {role:'STAGE_AFTER_SCAN_DECISION_SEE_DECISION_CHAIN'
                                   for role in LATER_STAGE_ROLES if role not in present}
    kind = stage['kind'] if stage else (L.Kind.CASH if row['action']=='HOLD' else L.Kind.REJECTED)
    rid=C.register(db,event,kind.value,lineage,cut,deps,chain=stage['chain'] if stage else None,
                   not_consulted=not_consulted, decision_stage='ALLOCATION' if stage else 'SCAN')
    C.attach(db,event,event+':pending',kind.value,L.Boundary.UNRESOLVED.value,dict(reason='future_outcome_pending'),cut)
    C.record_action(db,event,stage['action'] if stage else
        dict(action=row['action'],executed=row['executed'],skip_reason=row.get('skip_reason')),cut)
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
    event=C.trade_event(db,trade['decision_id'],trade_id)
    C.registration(db,event)
    at_ms=receipt['observed_ms']
    dep=freeze(db,'execution',receipt,at_ms,'existing trade-booking.v1')
    action=C.record_action(db,event,dict(action='EXECUTED',trade_id=trade_id,exec_mode=trade['exec_mode']),at_ms)
    closed=trade['status']=='closed'
    observation={'reason':'booking_not_complete_realized_accounting' if closed else 'awaiting_trade_outcome',
                 'monetary_status':'UNAVAILABLE','gross_pnl':None,'net_pnl':None,'fees':None,'funding':None}
    deps=[dep,action,freeze(db,'trade',trade,at_ms,'exact journal booking state; money unverified'),
          freeze(db,'outcome',dict(observation=observation,booking=receipt),at_ms,'original booking event')]
    order_id=receipt['evidence'].get('order_id')
    from .producers import execution_quality
    execution_quality(db,trade,receipt)
    return C.attach(db,event,'booking:'+receipt['sha256'],L.Kind.EXECUTED.value,
        L.Boundary.UNASSESSABLE.value if closed else L.Boundary.UNRESOLVED.value,
        observation,at_ms,deps,
        post_lineage=dict(trade_ids=(trade_id,),execution_ids=(str(order_id),) if order_id is not None else ()))


def forward(db, original, updates, targets, now_ms):
    from trader.engine.outcomes import target_bar
    targets = {label:target_bar(bar) for label,bar in targets.items()}
    root_event='decision:'+original['decision_id']
    event=C.forward_event(db,original['decision_id'],now_ms)
    _,reg=C.registration(db,event)
    # Registered reasons and initial action are frozen; no text-based Risk inference.
    kind=reg['kind']
    if kind in (L.Kind.CASH.value,L.Kind.MISSED.value) and (reg.get('chain') or reg['lineage'].get('opportunity_id') or reg['lineage'].get('context_id')):
        from .producers import opportunity
        opportunity(reg,{d['role']:C.resolve(db,d) for d in reg['dependencies'] if d['status']=='AVAILABLE'})
    action_rows=db.execute('SELECT payload FROM learning_actions WHERE event_key=?',(event,)).fetchall()
    actions=sorted([json.loads(r[0])['dependency'] for r in action_rows if json.loads(r[0])['dependency']['available_ms']<=now_ms],
                   key=lambda d:(d['available_ms'],d['sha256']))
    risk=[d for d in actions if d['role']=='risk_decision']
    intent_dep=next((d for d in reg['dependencies'] if d['role']=='intent' and d['status']=='AVAILABLE'),None)
    requested_open = (json.loads(C.resolve(db,intent_dep)['payload_json'])['requested_action']=='OPEN'
                      if intent_dep else reg['kind'] != L.Kind.CASH.value)
    if requested_open and risk and C.resolve(db,risk[-1]).get('ok') is False:
        kind=L.Kind.RISK_BLOCKED.value
    deps=[]
    action_dep=next((d for d in reversed(actions) if d['role']=='action' and 'prediction' not in C.resolve(db,d)),None)
    if action_dep: deps.append(action_dep)
    if risk: deps.append(risk[-1])
    observation=dict(measurement='counterfactual_price_return_not_money', returns=updates,
        original_rejection=reg['kind'], original_reasons=next((C.resolve(db,d) for d in reg['dependencies'] if d['role']=='reasons' and d['status']=='AVAILABLE'),None))
    # A scheduled measurement belongs to the root decision, retained verbatim.
    # The terminal stage owns the action/outcome; its parent owns the declaration.
    prediction_actions = actions if event == root_event else [json.loads(r[0])['dependency']
        for r in db.execute('SELECT payload FROM learning_actions WHERE event_key=?',(root_event,))]
    prediction_rows=[d for d in prediction_actions if d['role']=='action'
        and d['available_ms']<=now_ms and 'prediction' in C.resolve(db,d)]
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


def incident(db,event,at_ms,subject,detail,*,incident_kind='data_quality_incident'):
    key='incident:'+str(event)
    lineage=asdict(L.Lineage(None,None,None))
    deps=[freeze(db,'data',dict(subject=subject,detail=detail),at_ms,'original incident producer')]
    kind=L.Kind.INCIDENT if incident_kind in ('execution_incident','execution_error') else L.Kind.DATA
    C.register(db,key,kind.value,lineage,at_ms,deps,profile='INCIDENT')
    observation=dict(subject=subject,detail=detail,reason='incident causal linkage unavailable',market_conclusion='NOT_APPLICABLE')
    out=freeze(db,'outcome',dict(protocol='data-quality.v1' if kind==L.Kind.DATA else 'operational-incident.v1',observation=observation),at_ms,'existing operational incident')
    return C.attach(db,key,key,kind.value,L.Boundary.UNASSESSABLE.value,observation,at_ms,[out])


def runtime_inputs(journal, snap, config, *, cut_ms, additional_original_inputs=None, control_state=None):
    """Called before deciding, never when replaying historical rows."""
    safe = {k:config[k] for k in CONFIG_KEYS if k in config}
    result=dict(snapshot=deepcopy(snap),config=json.loads(L.canonical(safe)),source_cut_ms=cut_ms,
        additional_original_inputs=deepcopy(additional_original_inputs))
    # Only what the decision path itself used is carried (snapshot, config,
    # control; the signal-bound StrategyVersion is bound in decision()). Later
    # stages (WorldModel, Opportunity Context, portfolio cut, proposal, intent)
    # are registered explicitly UNAVAILABLE on the scan decision and bound as a
    # stage of the same decision by deliver_allocation. Nothing is looked up
    # from 'latest', and no optional producer attribute can inject sources.
    if control_state is not None:
        record={'state':getattr(control_state,'value',control_state)}
        result['control']=dict(state=record['state'],record=record,version=L.digest(record))
    return result


def missed_snapshot(db,scan_id,symbol,at_ms):
    """Capture an actually observed skip, without inventing an opportunity."""
    key='missed-snapshot:'+L.digest([scan_id,symbol,at_ms])
    lineage=asdict(L.Lineage(scan_id,None,None))
    deps=[C.unavailable('data','SNAPSHOT_PRODUCER_RETURNED_NONE'),
          freeze(db,'reasons',{'symbol':symbol,'reason':'missing_snapshot'},at_ms,'Kernel scan skip')]
    C.register(db,key,L.Kind.DATA.value,lineage,at_ms,deps)
    action=C.record_action(db,key,{'action':'SKIPPED','symbol':symbol},at_ms)
    observation={'reason':'no_original_snapshot_or_registered_future_measurement','market_conclusion':'NOT_APPLICABLE'}
    out=freeze(db,'outcome',{'protocol':'data-quality.v1','observation':observation},at_ms,'observed snapshot source failure')
    return C.attach(db,key,key,L.Kind.DATA.value,L.Boundary.UNASSESSABLE.value,
        observation,at_ms,[action,out])


def _signal_version(db, strategy_id, spec_hash, cut_ms):
    """(version, install) of one signal's own exact StrategyVersion, else (None, None)."""
    rows=db.execute('SELECT canonical_json,recorded_at_ms FROM strategy_versions WHERE strategy_id=? AND spec_hash=?',
                    (strategy_id,spec_hash)).fetchall()
    if len(rows)!=1 or rows[0][1]>cut_ms: return None,None
    version=json.loads(rows[0][0]); install=None
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='strategy_version_installs'").fetchone():
        matches=db.execute('SELECT canonical_json FROM strategy_version_installs WHERE version_id=?',(version['version_id'],)).fetchall()
        if len(matches)==1: install=json.loads(matches[0][0])
    return version,install


def bind_signal_version(db, inputs, row):
    signals=json.loads(row.get('signals_json') or '[]')
    agreeing=[s for s in signals if s.get('action')==row['action']]
    # A gated HOLD with one consulted signal still used that exact version.
    # Multiple signals without a selected direction remain ambiguous; never
    # infer a governing version from current state or mutable rejection text.
    if row['action']=='HOLD' and len(signals)==1:
        agreeing=signals
    if not agreeing or not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='strategy_versions'").fetchone():
        return inputs
    top=max(agreeing,key=lambda s:s.get('confidence',0))
    sh=(top.get('params') or {}).get('spec_sha256')
    if not sh: return inputs
    cut=timestamp(row['ts'])
    version,install=_signal_version(db,top['strategy_id'],sh,cut)
    if version is None: return inputs
    result=dict(inputs,strategy=version)
    # Every agreeing signal binds ITS OWN version's exit semantics; none inherits another's.
    bindings=[]
    for s in agreeing:
        h=(s.get('params') or {}).get('spec_sha256')
        v,i=_signal_version(db,s['strategy_id'],h,cut) if h else (None,None)
        bindings.append(dict(strategy_id=s['strategy_id'],spec_hash=h,version_id=v['version_id'] if v else None,
            exit_semantics_id=i.get('exit_semantics_id') if i else None,
            reason='EXACT_VERSION_INSTALL' if i else 'EXACT_EXIT_BINDING_UNAVAILABLE'))
    if install:
        result['exit_semantics']=dict(exit_semantics_id=install.get('exit_semantics_id'),install=install,
                                      signal_bindings=bindings)
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
    event=C.trade_event(db,trade['decision_id'],trade['id'])
    _,reg=C.registration(db,event)
    identity_row=db.execute('SELECT entry_identity_json FROM trades WHERE id=?',(trade['id'],)).fetchone()
    identity=json.loads(identity_row[0]) if identity_row and identity_row[0] else None
    from .producers import verify_trade_binding
    verify_trade_binding(reg,identity,trade)
    key='verified-trade:'+whole['sha256']
    previous=C.existing_outcome(db,key)
    if previous: return previous
    cut=receipt['available_ms']
    fills=accounting['fills']
    observation=dict(gross_pnl=sum(f['realized_pnl'] for f in fills),fees=sum(f['commission'] for f in fills),
                     funding=accounting['funding_net'], monetary_status='VERIFIED',
                     holding_duration_ms=max(f['event_ms'] for f in fills)-min(f['event_ms'] for f in fills),
                     exit_reason=whole['bookings']['receipts'][-1]['kind'],
                     intervention=whole['bookings']['receipts'][-1]['evidence'].get('exit_authority','UNAVAILABLE'),
                     slippage='UNAVAILABLE', net_pnl=receipt['actual_execution']['net_pnl'],
                     currency=receipt['actual_execution']['currency'],environment=whole['environment'])
    deps=[freeze(db,'outcome',dict(observation=observation,typed_receipt=receipt,trade_identity=identity),cut,'verified whole-trade outcome'),
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
    event=C.trade_event(db,trade['decision_id'],trade['id'])
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
    inputs = allocation_sources(journal, snap, config, receipt, proposal, allocation_inputs, c, intents)
    journal.log_cycle(snap, decision.cycle_id, 'prospective-allocation-capture')
    journal.log_decision(decision, capture_inputs=inputs)
    from trader.portfolio.opportunity_registry import Registry
    registry=Registry(journal.db_path,learning_journal=journal)
    try:
        cached=registry.db.execute('SELECT payload FROM opportunities WHERE opportunity_id=?',(c.opportunity_id,)).fetchone()
        registered=json.loads(cached[0]) if cached else registry.observe(receipt.context,decision.cycle_id,p['candidate_id'],c.version_id)
        if receipt.context.canonical_json not in registered.get('context_jsons',[]):
            raise ValueError('allocator_registry_context_differs')
        if registered['opportunity_id']!=c.opportunity_id:
            raise ValueError('allocator_registry_identity_differs')
        if proposal.payload()['result']['decision'] in ('CASH','NO_ALLOCATION'):
            registry.resolve(c.opportunity_id,'SKIPPED',proposal.proposal_id,allocation_inputs.as_of_ms)
    finally:
        registry.close()
    return intents[0]


def exit_binding(c, er):
    """Exact (version -> exit semantics) provenance of one candidate, or UNAVAILABLE."""
    from trader.portfolio import economics as E
    base = dict(candidate_id=c.candidate_id, version_id=c.version_id, spec_hash=c.spec_hash)
    try:
        authorities = [json.loads(s.payload_json)['result'] for s in E.from_inputs(json.loads(er.inputs_json)).context
                       if s.source_id.startswith('candidate-bridge:')]
        if len(authorities) != 1:
            return dict(base, exit_semantics_id=None, reason='CANDIDATE_BRIDGE_AUTHORITY_NOT_UNIQUE')
        a = authorities[0]
        if a.get('version_id') != c.version_id or a.get('spec_hash') != c.spec_hash or not a.get('exit_semantics_id'):
            return dict(base, exit_semantics_id=None, reason='CANDIDATE_BRIDGE_AUTHORITY_VERSION_DIFFERS')
        return dict(base, exit_semantics_id=a['exit_semantics_id'], reason='EXACT_VERSION_AUTHORITY')
    except (ValueError, KeyError, TypeError, AttributeError):
        return dict(base, exit_semantics_id=None, reason='CANDIDATE_BRIDGE_AUTHORITY_UNREADABLE')


def allocation_sources(journal, snap, config, receipt, proposal, allocation_inputs, c, intents):
    from trader.portfolio import economics as E
    er = E.from_payload(json.loads(c.economics.receipt_json))
    p = json.loads(receipt.payload_json)
    original = {s['source_id']: json.loads(s['payload_json']) for s in p['sources']}
    carried = dict(context={'context_json': p['context_json'], 'live_receipt': json.loads(receipt.payload_json)},
        opportunity_id=c.opportunity_id, economics=er.payload(), proposal=proposal.payload(),
        intent=dict(intent_id=intents[0].intent_id, payload_json=intents[0].payload_json) if intents else None,
        control=dict(state=allocation_inputs.control_state, record={'state':allocation_inputs.control_state},
                     version=L.digest({'state':allocation_inputs.control_state})))
    if 'world_model' in original: carried['world'] = {'record_json':original['world_model']['data']}
    if 'portfolio' in original:
        carried['portfolio'] = original['portfolio']['data']
        carried['derivative_identity'] = {k:carried['portfolio'][k] for k in ('venue','market_type','environment')}
    if 'strategy_version' in original:
        v = json.loads(original['strategy_version']['data']['canonical_json'])
        carried['strategy'] = v
        # Each candidate binds ITS OWN StrategyVersion authority: the single
        # bridge source inside this candidate's economics receipt. A cut with
        # many candidates therefore carries one binding per version; a missing
        # or mismatching authority leaves exit semantics UNAVAILABLE.
        binding = exit_binding(c, er)
        if binding.get('exit_semantics_id'):
            carried['exit_semantics'] = dict(binding, cut_bindings=[
                exit_binding(x, E.from_payload(json.loads(x.economics.receipt_json)))
                for x in sorted(allocation_inputs.candidates, key=lambda x: x.identity)])
    carried['source_evidence'] = {role:{k:v for k,v in raw.items() if k != 'data'} for role,raw in original.items()}
    inputs = (runtime_inputs(journal, snap, config, cut_ms=allocation_inputs.as_of_ms) if snap is not None
              else dict(config=config,source_cut_ms=allocation_inputs.as_of_ms))
    inputs.update(carried)
    return inputs


def deliver_allocation(db, config, current, result, market_snapshots):
    """Allocation stage of each REAL decision, on that decision's own identity.

    The candidate's origin is a Kernel decision already in the journal. The
    Portfolio cut, AllocationProposal, TradeIntent and Risk decision are bound
    as a stage of THAT decision (same decision_id and cycle, parent registration
    linked), never a fresh identity and never a fabricated HOLD. A candidate
    whose origin decision or original registration is absent is recorded as a
    capture failure; nothing is reconstructed later. Only learning tables are
    written, no Risk/Executor/venue call is made.
    """
    from trader.portfolio.allocator import Source, Proposal
    from trader.portfolio.opportunity_live import receipt_from_source
    from trader.portfolio.trade_intent import build, Action
    proposal = result.get('proposal')
    if proposal is None:
        return []
    p = Proposal(proposal['proposal_id'],L.canonical(proposal['inputs']),L.canonical(proposal['result']),proposal['allocator_version'])
    all_intents = build(p,current)
    risk_by_intent = {}
    for d in result.get('risk_decisions', ()):
        risk_by_intent[json.loads(d['payload_json'])['trade_intent_id']] = d
    delivered=[]
    def refuse(key, reason):
        # One explicit row per refusal, not one per Kernel cycle.
        if not db.execute('SELECT 1 FROM learning_capture_failures WHERE event_key=? AND reason=?', (key, reason)).fetchone():
            db.execute('INSERT INTO learning_capture_failures VALUES(?,?)', (key, reason))
    for source in current.sources:
        if not source.source_id.startswith('live-context:'):
            continue
        receipt = receipt_from_source(source)
        raw=json.loads(receipt.payload_json)
        candidates=[c for c in current.candidates if c.opportunity_context_json == raw['context_json']]
        if len(candidates)!=1:
            continue
        c=candidates[0]
        intents=[i for i in all_intents if i.payload()['opportunity_id']==c.opportunity_id]
        origin=raw['candidate_id'].removesuffix(':'+c.version_id)
        event=C.stage_event(origin,p.proposal_id,c.version_id)
        if db.execute('SELECT 1 FROM learning_registrations WHERE event_key=?',(event,)).fetchone():
            delivered.append(event)
            continue
        row=db.execute('SELECT * FROM decisions WHERE id=?',(origin,)).fetchone()
        parent=db.execute('SELECT registration_id FROM learning_registrations WHERE event_key=?',('decision:'+origin,)).fetchone()
        if row is None or parent is None or len(intents)!=1:
            refuse(event,'allocation_origin_decision_or_registration_or_intent_unavailable_no_backfill')
            continue
        row=dict(row)
        cycle=db.execute('SELECT * FROM cycles WHERE id=?',(row['cycle_id'],)).fetchone()
        if row['cycle_id']!=raw['cycle_id'] or cycle is None:
            refuse(event,'allocation_origin_cycle_differs')
            continue
        intent=intents[0]
        snap=(market_snapshots or {}).get(origin)
        inputs=allocation_sources(None,snap,config,receipt,p,current,c,intents)
        opens=intent.requested_action==Action.OPEN
        stage=dict(cut_ms=current.as_of_ms,event_key=event,
            kind=L.Kind.REJECTED if opens else L.Kind.CASH,
            reason=dict(proposal_id=p.proposal_id,allocation_decision=proposal['result']['decision'],
                        reason=proposal['result']['reason'],intent_id=intent.intent_id,
                        intent_action=intent.requested_action.value,intent_status=intent.status.value),
            action=dict(action='ALLOCATION_'+intent.requested_action.value,stage=C.STAGE_ALLOCATION,
                        proposal_id=p.proposal_id,intent_id=intent.intent_id,executed=False,
                        allocation_decision=proposal['result']['decision']),
            chain=dict(decision_id=origin,stage=C.STAGE_ALLOCATION,parent_event_key='decision:'+origin,
                       parent_registration_id=parent[0]))
        decision(db,row,dict(cycle),inputs,stage=stage)
        risk=risk_by_intent.get(intent.intent_id)
        if risk is not None:
            record_risk(db,event,config,intent.intent_id,risk,current.as_of_ms,opens)
        if proposal['result']['decision'] in ('CASH','NO_ALLOCATION'):
            resolve_nontrade(db,c.opportunity_id,p.proposal_id,current.as_of_ms,event)
        delivered.append(event)
    return delivered


def record_risk(db, event, config, intent_id, risk, at_ms, opens):
    """Bind the portfolio Risk decision to the chain it evaluated.

    Only a REQUESTED open that Risk did not approve is Risk-blocked; KEEP /
    NO_ACTION intents were never trade requests, so their refusal is recorded
    as the Risk answer but is not an outcome of its own.
    """
    body = json.loads(risk['payload_json'])
    value = dict(risk_decision_id=risk['decision_id'], result=body['result'], ok=body['result'] == 'APPROVE',
                 trade_intent_id=intent_id, config_risk_sha256=L.digest(config['risk']), payload=body)
    dep = C.record_action(db, event, value, at_ms, risk=True, source_id=risk['decision_id'])
    if opens and not value['ok']:
        from .producers import risk_blocked
        return risk_blocked(db, event, dep, at_ms)
    return None


def resolve_nontrade(db, opportunity_id, proposal_id, at_ms, event):
    """The allocator's own CASH/NO_ALLOCATION answer, on this chain's opportunity."""
    from trader.portfolio.opportunity_registry import resolve_row
    from .producers import missed
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='opportunities'").fetchone():
        return None
    if db.execute('SELECT 1 FROM opportunities WHERE opportunity_id=?',(opportunity_id,)).fetchone() is None:
        return None
    body=resolve_row(db,opportunity_id,'SKIPPED',proposal_id,at_ms)
    return missed(db,event,body)


def failed_allocation_delivery(db, current, result):
    """Record attempted terminal identities at the normal caller's own cut.

    If a supplied exact object cannot be retained, an earlier scan cannot later
    masquerade as the governing portfolio decision. No source is repaired.
    """
    proposal = result['proposal']
    for source in current.sources:
        if not source.source_id.startswith('live-context:'):
            continue
        raw = json.loads(source.payload_json)
        for candidate in current.candidates:
            if candidate.opportunity_context_json != raw['context_json']:
                continue
            origin = raw['candidate_id'].removesuffix(':' + candidate.version_id)
            event = C.stage_event(origin,proposal['proposal_id'],candidate.version_id)
            reason = 'terminal_source_delivery_failed_no_scan_fallback'
            if not db.execute('SELECT 1 FROM learning_capture_failures WHERE event_key=? AND reason=?',(event,reason)).fetchone():
                db.execute('INSERT INTO learning_capture_failures VALUES (?,?)',(event,reason))
