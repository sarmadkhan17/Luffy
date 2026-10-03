"""Exact-source outcome adapters and proposal-only automatic materialization."""
from dataclasses import asdict
import json
from . import foundation as L, capture as C


def materialize(db, outcome_id):
    """Persist one replayed chain; never call any learning application API."""
    old = db.execute('SELECT payload FROM learning_produced_chains WHERE outcome_id=?', (outcome_id,)).fetchone()
    if old:
        return json.loads(old[0])
    o, retained = C.learning_outcome(db, outcome_id)
    a = L.attribute(o)
    r = L.replay(o, retained)
    e = L.evidence(o, a, r)
    # Original identity only. Application must later obtain Governor authority.
    current = dict(version_id=o.lineage.version_id, spec_hash=o.lineage.spec_hash, state='UNKNOWN')
    p = L.propose(e, L.Target.LIFECYCLE, current, rule=L.DECAY_RULE)
    body = dict(replay_gate='REPLAY_COMPLETE' if r.status=='COMPLETE' else 'REPLAY_INCOMPLETE',outcome=asdict(o), attribution=asdict(a), replay=asdict(r),
                evidence=asdict(e), proposal=asdict(p), authoritative=r.status=='COMPLETE')
    C.insert(db, 'learning_produced_chains', 'outcome_id', outcome_id, (outcome_id, L.canonical(body)))
    return body


def verify_trade_binding(reg, identity, trade):
    """Only the persisted entry identity proves which version placed the trade."""
    if not isinstance(identity, dict) or identity.get('status') != 'VERIFIED':
        raise ValueError('exact_trade_entry_identity_unavailable')
    line = reg['lineage']
    if (not line.get('version_id') or not line.get('spec_hash') or
            identity.get('version_id') != line['version_id'] or
            identity.get('spec_sha256') != line['spec_hash'] or
            identity.get('strategy_id') != line['strategy_id'] or
            trade['strategy_id'] != line['strategy_id'] or
            trade['decision_id'] != line['decision_id'] or
            identity.get('decision', {}).get('decision_id') != line['decision_id'] or
            identity.get('decision', {}).get('cycle_id') != line['cycle_id'] or
            trade.get('exec_mode') != 'live' or trade.get('status') != 'closed'):
        raise ValueError('exact_trade_decision_version_binding_differs')


def opportunity(reg, sources):
    """A frozen live candidate receipt is an exact registration, not a price guess."""
    line = reg['lineage']
    context = sources.get('context', {})
    raw = context.get('live_receipt')
    if not raw or not line.get('opportunity_id'):
        raise ValueError('exact_registered_opportunity_required')
    if (raw['opportunity_id'] != line['opportunity_id'] or raw['cycle_id'] != line['cycle_id'] or
            raw['context_json'] != context['context_json'] or raw['as_of_ms'] > reg['decision_ms']):
        raise ValueError('opportunity_context_lineage_differs')
    return raw


def validate(body, sources):
    out = sources['outcome']
    if out.get('protocol') == 'existing-forward-outcome.v1':
        if body['kind'] in (L.Kind.CASH.value, L.Kind.MISSED.value):
            opportunity(body['registration'], sources)
    if out.get('protocol') == 'execution-quality.v1':
        from trader.engine.booking import replay
        receipt = sources['execution']
        assessment=replay(receipt)
        if assessment['status']!='verified_leg_fills_only':
            raise ValueError('execution_fill_provenance_unverified')
        if (receipt['trade_id'] not in body['lineage']['trade_ids'] or
                receipt['after'] != sources['trade'] or
                receipt['after']['decision_id'] != body['lineage']['decision_id'] or
                out['observation'] != execution_observation(receipt, sources['decision'])):
            raise ValueError('execution_quality_provenance_differs')
    if out.get('protocol') == 'registered-opportunity-resolution.v1':
        raw = opportunity(body['registration'], sources)
        resolved = sources['opportunity']
        if (resolved['opportunity_id'] != raw['opportunity_id'] or
                raw['context_json'] not in resolved['context_jsons'] or
                resolved['resolution']['resolution'] not in ('SKIPPED','EXPIRED','INVALIDATED','BLOCKED')):
            raise ValueError('missed_opportunity_registration_differs')
        if out['observation'] != dict(status=resolved['resolution']['resolution'],
                evidence_id=resolved['resolution']['evidence_id'], measurement='registered_nontrade_status_not_missed_profit'):
            raise ValueError('missed_status_differs')
    if out.get('protocol') == 'portfolio-risk-block.v1':
        risk = sources.get('risk_decision') or {}
        line = body['lineage']
        if (body['kind'] != L.Kind.RISK_BLOCKED.value or risk.get('ok') is not False
                or out['observation'].get('risk_decision_id') != risk.get('risk_decision_id')
                or line.get('risk_decision_id') != risk.get('risk_decision_id')
                or line.get('intent_id') != risk.get('trade_intent_id')
                or not line.get('proposal_id') or not body['registration'].get('chain')):
            raise ValueError('risk_block_decision_chain_differs')
    if out.get('protocol') == 'data-quality.v1' and (
            body['kind'] != L.Kind.DATA.value or body['boundary'] != L.Boundary.UNASSESSABLE.value or
            out['observation'].get('market_conclusion') != 'NOT_APPLICABLE'):
        raise ValueError('data_incident_is_not_market_prediction')


def execution_observation(receipt, decision):
    e = receipt['evidence']
    # Preserve fields exactly as recorded; no nearby price or implied timestamps.
    fields = ('submission_ms','requested_quantity','spread','slippage','depth_id','rejection','partials')
    return dict(measurement='execution_quality_not_signal_quality',
        decision_price=decision.get('entry_price'), decision_time=decision.get('ts'),
        order_id=e.get('order_id'), filled_quantity=e.get('confirmed_quantity', e.get('quantity')),
        fills=e.get('fills'), execution_fields={k:e.get(k) for k in fields},
        unavailable=[k for k in fields if e.get(k) is None],
        predictive_quality='NOT_APPLICABLE', causal_effect='UNKNOWN')


def execution_quality(db, trade, receipt):
    from .capture_runtime import freeze
    from trader.engine.booking import replay
    replay(receipt)
    event = C.trade_event(db, trade['decision_id'], trade['id'])
    _, reg = C.registration(db, event)
    key = 'execution-quality:'+receipt['sha256']
    previous = C.existing_outcome(db, key)
    if previous: return previous
    decision = C.resolve(db, next(d for d in reg['dependencies'] if d['role']=='decision'))
    obs = execution_observation(receipt, decision)
    cut = receipt['observed_ms']
    deps = [freeze(db,'execution',receipt,cut,'exact booking execution provenance'),
            freeze(db,'trade',receipt['after'],cut,'exact booking trade snapshot'),
            freeze(db,'outcome',dict(protocol='execution-quality.v1',observation=obs),cut,'execution quality adapter'),
            C.record_action(db,event,dict(action='EXECUTION_OBSERVED',booking_id=receipt['sha256']),cut)]
    return C.attach(db,event,key,L.Kind.EXECUTION.value,L.Boundary.REALIZED.value,obs,cut,deps,
        post_lineage=dict(trade_ids=(trade['id'],),execution_ids=(receipt['sha256'],)))


def missed(db, event, resolved):
    from .capture_runtime import freeze
    _, reg = C.registration(db,event)
    sources={d['role']:C.resolve(db,d) for d in reg['dependencies'] if d['status']=='AVAILABLE'}
    opportunity(reg,sources)
    row=db.execute('SELECT payload FROM opportunities WHERE opportunity_id=?',(resolved['opportunity_id'],)).fetchone()
    if row is None or json.loads(row[0])!=resolved:
        raise ValueError('exact_registered_resolution_missing')
    status=resolved['resolution']['resolution']
    if status not in ('SKIPPED','EXPIRED','INVALIDATED','BLOCKED'):
        raise ValueError('not_a_registered_nontrade_resolution')
    cut=resolved['resolution']['at_ms']
    key='missed:'+L.digest(dict(event=event,resolution=resolved))
    previous=C.existing_outcome(db,key)
    if previous: return previous
    obs=dict(status=status,evidence_id=resolved['resolution']['evidence_id'],measurement='registered_nontrade_status_not_missed_profit')
    deps=[freeze(db,'opportunity',resolved,cut,'exact registered opportunity resolution'),
          freeze(db,'outcome',dict(protocol='registered-opportunity-resolution.v1',observation=obs),cut,'registered opportunity status'),
          C.record_action(db,event,dict(action=status),cut)]
    # The status is observed, but there is no registered future price measurement.
    return C.attach(db,event,key,L.Kind.MISSED.value,L.Boundary.UNASSESSABLE.value,obs,cut,deps)


def latest_action(db, event, role, at_ms):
    rows = [json.loads(r[0])['dependency'] for r in db.execute(
        'SELECT payload FROM learning_actions WHERE event_key=?', (event,))]
    found = [d for d in rows if d['role'] == role and d['available_ms'] <= at_ms]
    return max(found, key=lambda d: (d['available_ms'], d['sha256'])) if found else None


def risk_blocked(db, event, risk_dep, at_ms):
    """Portfolio Risk refusal resolved on the SAME decision chain that was refused.

    No future return is invented: the measurement is the refusal itself, so the
    boundary stays UNASSESSABLE until a registered future measurement exists.
    """
    from .capture_runtime import freeze
    C.registration(db, event)
    value = C.resolve(db, risk_dep)
    if value.get('ok') is not False:
        raise ValueError('risk_decision_is_not_a_refusal')
    obs = dict(status='RISK_' + value['result'], risk_decision_id=value['risk_decision_id'],
               trade_intent_id=value['trade_intent_id'],
               measurement='registered_nontrade_status_not_missed_profit')
    key = 'risk-blocked:' + value['risk_decision_id']
    previous = C.existing_outcome(db, key)
    if previous:
        return previous
    action = latest_action(db, event, 'action', at_ms)
    deps = [risk_dep, freeze(db, 'outcome', dict(protocol='portfolio-risk-block.v1', observation=obs),
                             at_ms, 'portfolio Risk refusal')]
    if action:
        deps.append(action)
    return C.attach(db, event, key, L.Kind.RISK_BLOCKED.value, L.Boundary.UNASSESSABLE.value, obs, at_ms, deps)


def deliver_accounting(journal_path, artifact):
    """Off-path worker delivery, no Journal constructor or trading-table writes."""
    from pathlib import Path
    import sqlite3
    from .capture_runtime import verified_trade
    from trader.engine.trade_accounting import verified_outcome
    receipt=verified_outcome(artifact,artifact['captured_ms'])
    with sqlite3.connect(Path(journal_path).resolve().as_uri()+'?mode=ro',uri=True,timeout=2) as src:
        if not src.execute("SELECT 1 FROM sqlite_master WHERE name='learning_registrations'").fetchone():
            raise ValueError('original_registration_missing_no_backfill')
        _,reg=C.registration(src,C.trade_event(src,artifact['bookings']['trade']['decision_id'],artifact['bookings']['trade']['id']))
        row=src.execute('SELECT entry_identity_json FROM trades WHERE id=?',(artifact['bookings']['trade']['id'],)).fetchone()
        verify_trade_binding(reg,json.loads(row[0]) if row and row[0] else None,artifact['bookings']['trade'])
    with sqlite3.connect(Path(journal_path).resolve().as_uri()+'?mode=rw',uri=True,timeout=2) as db:
        db.row_factory=sqlite3.Row
        C.ensure(db)
        return C.safely(db,'verified-trade:'+artifact['sha256'],verified_trade,receipt)
