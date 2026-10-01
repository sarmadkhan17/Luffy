"""Prospective, append-only historical capture; never applies learning updates.

Content-addressed snapshots deduplicate mutable inputs. Durable source references
are permitted only to explicitly immutable producer tables. There is no 'latest'
resolver and no retrospective registration. Missing dependencies stay unavailable.
"""
from dataclasses import asdict, dataclass
import json
import sqlite3
from . import foundation as L

SCHEMA = 'learning-replay-manifest.v1'
TABLES = ('learning_source_blobs', 'learning_registrations', 'learning_outcome_captures', 'learning_actions', 'learning_decision_source_manifests')
DURABLE = {'strategy_versions': ('version_id', 'canonical_json'),
           'research_bank_objects': ('bank_object_id', 'canonical_json'),
           'research_questions': ('question_id', 'canonical_json'),
           'research_plans': ('plan_id', 'canonical_json'),
           'research_evidence': ('evidence_id', 'canonical_json'),
           'research_results': ('result_id', 'canonical_json'),
           'versioned_paper_cost_sources': ('source_id', 'canonical_json'),
           'versioned_paper_cost_receipts': ('trade_id', 'canonical_json')}
ROLES = ('cycle', 'decision', 'data', 'world', 'context', 'strategy', 'exit_semantics',
         'portfolio', 'risk_config', 'proposal', 'intent', 'reasons', 'action',
         'control', 'economics', 'risk_decision', 'execution', 'trade', 'cost', 'funding', 'derivative_identity',
         'execution_reference', 'position_basis', 'question', 'plan', 'research_evidence',
         'falsifier_result', 'bank', 'outcome', 'prediction', 'research_run')
TRADING = {'cycle', 'decision', 'data', 'world', 'context', 'portfolio', 'risk_config', 'control', 'reasons', 'action', 'outcome'}
RESEARCH = {'question', 'plan', 'research_evidence', 'falsifier_result', 'bank', 'outcome', 'research_run'}
POST = {'action', 'risk_decision', 'execution', 'trade', 'cost', 'funding', 'execution_reference', 'position_basis', 'falsifier_result', 'bank', 'outcome', 'prediction', 'research_run'}


def ensure(db):
    definitions = {'learning_decision_source_manifests': 'manifest_id TEXT PRIMARY KEY,event_key TEXT UNIQUE NOT NULL,payload TEXT NOT NULL',
        'learning_source_blobs': 'sha256 TEXT PRIMARY KEY,payload TEXT NOT NULL',
        'learning_registrations': 'event_key TEXT PRIMARY KEY,registration_id TEXT UNIQUE NOT NULL,payload TEXT NOT NULL',
        'learning_outcome_captures': 'outcome_key TEXT PRIMARY KEY,outcome_id TEXT UNIQUE NOT NULL,payload TEXT NOT NULL',
        'learning_actions': 'action_id TEXT PRIMARY KEY,event_key TEXT NOT NULL,payload TEXT NOT NULL'}
    for table, columns in definitions.items():
        db.execute(f'CREATE TABLE IF NOT EXISTS {table}({columns})')
        key = columns.split()[0]
        db.execute(f"CREATE TRIGGER IF NOT EXISTS {table}_duplicate BEFORE INSERT ON {table} WHEN EXISTS(SELECT 1 FROM {table} WHERE {key}=NEW.{key}) BEGIN SELECT RAISE(ABORT,'learning capture immutable'); END")
        for action in ('UPDATE', 'DELETE'):
            db.execute(f"CREATE TRIGGER IF NOT EXISTS {table}_{action.lower()} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT,'learning capture immutable'); END")
    db.execute('CREATE TABLE IF NOT EXISTS learning_capture_failures(event_key TEXT,reason TEXT)')


def insert(db, table, key, identity, values):
    row = db.execute(f'SELECT * FROM {table} WHERE {key}=?', (identity,)).fetchone()
    if row is not None:
        if tuple(row) != tuple(values):
            raise ValueError('immutable_capture_conflict')
        return 'DUPLICATE'
    db.execute(f"INSERT INTO {table} VALUES({','.join('?' for _ in values)})", values)
    return 'INSERTED'


def unavailable(role, reason):
    return dict(role=role, status='UNAVAILABLE', reason=reason, source_id=None, version=None,
                sha256=None, available_ms=None, producer=None, storage=None, locator=None)


def snapshot(db, role, value, *, source_id, version, available_ms, producer):
    if role not in ROLES or not source_id or not version or version.lower() == 'latest' or not producer:
        raise ValueError('exact_dependency_identity_required')
    if type(available_ms) is not int or available_ms < 0:
        raise ValueError('exact_dependency_availability_required')
    # Detach mutable caller data and address it once, including large bar sets.
    text = L.canonical(value)
    sha = L.digest(value)
    insert(db, 'learning_source_blobs', 'sha256', sha, (sha, text))
    return dict(role=role, status='AVAILABLE', reason='EXACT_CAPTURE', source_id=source_id,
        version=version, sha256=sha, available_ms=available_ms, producer=producer,
        storage='SNAPSHOT', locator=dict(sha256=sha))


def reference(db, role, table, source_id, *, version, available_ms, producer):
    if table not in DURABLE:
        raise ValueError('mutable_or_unregistered_reference_table')
    key, col = DURABLE[table]
    # A producer table must actually protect against UPDATE and DELETE.
    triggers = [r[0].upper() for r in db.execute("SELECT sql FROM sqlite_master WHERE type='trigger' AND tbl_name=?", (table,))]
    if not all(any('BEFORE '+action in t and 'RAISE' in t for t in triggers) for action in ('UPDATE', 'DELETE')):
        raise ValueError('durable_source_not_immutable_snapshot_required')
    row = db.execute(f'SELECT {col} FROM {table} WHERE {key}=?', (source_id,)).fetchone()
    if row is None:
        return unavailable(role, 'EXACT_DURABLE_SOURCE_MISSING')
    raw = json.loads(row[0])
    if not version or version.lower() == 'latest' or type(available_ms) is not int or available_ms < 0 or not producer:
        raise ValueError('durable_source_identity_or_clock_missing')
    return dict(role=role, status='AVAILABLE', reason='EXACT_DURABLE_REFERENCE', source_id=source_id,
        version=version, sha256=L.digest(raw), available_ms=available_ms, producer=producer,
        storage='REFERENCE', locator=dict(table=table, key=source_id))


def resolve(db, dep):
    if dep['status'] != 'AVAILABLE':
        return None
    if dep['storage'] == 'SNAPSHOT':
        row = db.execute('SELECT payload FROM learning_source_blobs WHERE sha256=?', (dep['locator']['sha256'],)).fetchone()
    elif dep['storage'] == 'REFERENCE':
        table = dep['locator']['table']
        if table not in DURABLE:
            raise ValueError('reference_table_unregistered')
        key, col = DURABLE[table]
        row = db.execute(f'SELECT {col} FROM {table} WHERE {key}=?', (dep['locator']['key'],)).fetchone()
    else:
        raise ValueError('storage_unregistered')
    if row is None:
        raise ValueError('retained_exact_source_missing:' + dep['role'])
    raw = json.loads(row[0])
    if L.digest(raw) != dep['sha256']:
        raise ValueError('retained_source_hash_differs:' + dep['role'])
    return raw


def required(kind, lineage, *, profile='TRADING'):
    if profile == 'RESEARCH':
        return set(RESEARCH)
    if profile == 'INCIDENT':
        return {'decision', 'data', 'action', 'outcome'}
    roles = set(TRADING)
    if lineage.get('strategy_id'):
        roles |= {'strategy', 'exit_semantics'}
    if lineage.get('proposal_id'):
        roles |= {'proposal','economics'}
    if lineage.get('intent_id'):
        roles.add('intent')
    if kind == L.Kind.EXECUTED.value:
        roles |= {'strategy', 'exit_semantics', 'execution', 'trade', 'cost', 'funding',
                  'derivative_identity', 'execution_reference', 'position_basis', 'risk_decision'}
    if kind == L.Kind.RISK_BLOCKED.value:
        roles.add('risk_decision')
    return roles


def register(db, event_key, kind, lineage, decision_ms, dependencies, *, profile='TRADING'):
    ensure(db)
    if type(decision_ms) is not int or decision_ms < 0 or profile not in ('TRADING', 'RESEARCH', 'INCIDENT'):
        raise ValueError('capture_registration_invalid')
    L.Kind(kind)
    deps = list(dependencies)
    if len({d['role'] for d in deps}) != len(deps):
        raise ValueError('duplicate_capture_role')
    for dep in deps:
        if dep['role'] not in ROLES or dep['status'] not in ('AVAILABLE', 'UNAVAILABLE'):
            raise ValueError('registration_dependency_invalid')
        if dep['status'] == 'AVAILABLE':
            resolve(db, dep)
            if dep['available_ms'] > decision_ms:
                raise ValueError('future_source_at_registration')
    additional=[]
    for dep in deps:
        if dep['role']=='data' and dep['status']=='AVAILABLE':
            data=resolve(db,dep)
            if isinstance(data,dict) and (data.get('market_type')=='futures' or data.get('chunks',{}).get('derivs')):
                additional.append('derivative_identity')
    body = dict(schema=SCHEMA, additional_required=additional, event_key=event_key, kind=kind, lineage=lineage,
                decision_ms=decision_ms, profile=profile, dependencies=sorted(deps, key=lambda d:d['role']))
    from . import decision_sources as D
    m = D.make(body)
    insert(db, D.TABLE, 'manifest_id', m['manifest_id'], (m['manifest_id'], event_key, L.canonical(m)))
    body['decision_source_manifest_id'] = m['manifest_id']
    rid = L.digest(body)
    insert(db, 'learning_registrations', 'event_key', event_key, (event_key, rid, L.canonical(body)))
    return rid


def registration(db, event_key):
    row = db.execute('SELECT registration_id,payload FROM learning_registrations WHERE event_key=?', (event_key,)).fetchone()
    if row is None:
        raise ValueError('original_registration_missing_no_backfill')
    raw = json.loads(row[1])
    if L.digest(raw) != row[0] or raw['event_key'] != event_key:
        raise ValueError('registration_hash_differs')
    return row[0], raw


def existing_outcome(db,outcome_key):
    row=db.execute('SELECT outcome_id,payload FROM learning_outcome_captures WHERE outcome_key=?',(outcome_key,)).fetchone()
    if row is None: return None
    body=json.loads(row[1])
    if L.digest(body)!=row[0]: raise ValueError('outcome_capture_hash_differs')
    rid,reg=registration(db,body['registration']['event_key'])
    if rid!=body['registration_id'] or reg!=body['registration']:
        raise ValueError('retry_original_registration_differs')
    for dep in body['dependencies']:
        if dep['status']=='AVAILABLE': resolve(db,dep)
    return row[0]


def record_action(db, event_key, value, at_ms, *, risk=False):
    rid, reg = registration(db, event_key)
    if at_ms < reg['decision_ms']:
        raise ValueError('action_precedes_decision')
    dep = snapshot(db, 'risk_decision' if risk else 'action', value,
                   source_id=L.digest(value), version='captured-action.v1', available_ms=at_ms,
                   producer='original decision/action producer')
    body = dict(registration_id=rid, event_key=event_key, dependency=dep)
    aid = L.digest(body)
    insert(db, 'learning_actions', 'action_id', aid, (aid, event_key, L.canonical(body)))
    return dep


def attach(db, event_key, outcome_key, kind, boundary, observation, observed_ms, dependencies=(), *, post_lineage=None):
    rid, reg = registration(db, event_key)
    kind, boundary = L.Kind(kind), L.Boundary(boundary)
    if observed_ms < reg['decision_ms']:
        raise ValueError('outcome_precedes_decision')
    if kind in (L.Kind.REJECTED, L.Kind.MISSED, L.Kind.RISK_BLOCKED, L.Kind.CASH) and boundary == L.Boundary.REALIZED:
        raise ValueError('nontrade_is_unrealized')
    if boundary == L.Boundary.COUNTERFACTUAL and (observed_ms <= reg['decision_ms'] or 'net_pnl' in observation):
        raise ValueError('counterfactual_clock_or_money_invalid')
    initial = {d['role']:d for d in reg['dependencies']}
    for dep in dependencies:
        if dep['role'] not in POST:
            raise ValueError('future_outcome_cannot_rewrite_original_context')
        if dep['role'] in initial:
            raise ValueError('original_dependency_is_frozen')
        if dep['status'] == 'AVAILABLE' and not reg['decision_ms'] <= dep['available_ms'] <= observed_ms:
            raise ValueError('outcome_source_clock_invalid')
        initial[dep['role']] = dep
    extra = post_lineage or {}
    if set(extra) - {'trade_ids','execution_ids','risk_decision_id'}:
        raise ValueError('outcome_cannot_rewrite_original_lineage')
    effective_lineage = dict(reg['lineage'], **extra)
    if 'risk_decision' in initial and initial['risk_decision']['status']=='AVAILABLE':
        risk_id=initial['risk_decision']['source_id']
        if extra.get('risk_decision_id') not in (None,risk_id):
            raise ValueError('risk_decision_identity_differs')
        effective_lineage['risk_decision_id']=risk_id
    body = dict(schema=SCHEMA, registration_id=rid, registration=reg, lineage=effective_lineage, outcome_key=outcome_key,
        kind=kind.value, boundary=boundary.value, observation=observation, observed_ms=observed_ms,
        label='SIMULATED / UNREALIZED' if boundary == L.Boundary.COUNTERFACTUAL else boundary.value,
        dependencies=sorted(initial.values(), key=lambda d:d['role']))
    if reg.get('decision_source_manifest_id'):
        from . import decision_sources as D
        body['decision_source_manifest'] = D.load(db, reg['decision_source_manifest_id'])
    oid = L.digest(body)
    insert(db, 'learning_outcome_captures', 'outcome_key', outcome_key, (outcome_key, oid, L.canonical(body)))
    return oid


def semantic(role, value, reg):
    """Verify existing producer objects, not just newly calculated hashes."""
    if role == 'world':
        from trader.world.replay import WorldModelRecord
        record = WorldModelRecord.from_json(value['record_json'])
        if record.model_id != reg['lineage'].get('world_id') or record.reconstruct().as_of_ms > reg['decision_ms']:
            raise ValueError('world_binding_or_cut_differs')
    elif role == 'context':
        from trader.cognition.opportunity_context import OpportunityContext
        context = OpportunityContext.from_json(value['context_json'])
        if value.get('live_receipt'):
            from trader.portfolio.opportunity_live import LiveReceipt, replay
            from trader.portfolio.allocator import Source
            raw=value['live_receipt']
            receipt=LiveReceipt(L.digest(raw),L.canonical(raw))
            replay(receipt,tuple(Source(**s) for s in raw['sources']),reg['decision_ms'])
            if raw['context_json']!=value['context_json'] or raw['cycle_id']!=reg['lineage']['cycle_id']:
                raise ValueError('original_live_context_receipt_differs')
        if context.context_id != reg['lineage'].get('context_id') or context.to_dict()['as_of_ms'] > reg['decision_ms']:
            raise ValueError('context_binding_or_cut_differs')
        world_id = context.to_dict()['world_model'].get('model_id')
        if reg['lineage'].get('world_id') and world_id != reg['lineage']['world_id']:
            raise ValueError('context_world_differs')
    elif role == 'strategy':
        from trader.strategy.factory_handoff import _jsha,VERSION_SCHEMA
        if value.get('schema')!=VERSION_SCHEMA or value['spec'].get('id')!=value['strategy_id']:
            raise ValueError('strategy_contract_or_spec_identity_differs')
        if value['version_id'] != reg['lineage'].get('version_id') or _jsha(value['spec']) != value['spec_hash'] or value['spec_hash'] != reg['lineage'].get('spec_hash'):
            raise ValueError('historical_strategy_differs')
        ident={k:value[k] for k in ('schema','strategy_id','spec_hash','parent_version_id','source')}
        if _jsha(ident)!=value['version_id']:
            raise ValueError('strategy_version_identity_differs')
    elif role == 'exit_semantics':
        from trader.strategy.exit_policy import EXIT_SEMANTICS_ID
        if value.get('exit_semantics_id')!=EXIT_SEMANTICS_ID:
            raise ValueError('historical_exit_semantics_implementation_unavailable')
        install=value.get('install')
        if install and (install['version_id']!=reg['lineage']['version_id'] or install['spec_hash']!=reg['lineage']['spec_hash'] or install['exit_semantics_id']!=value['exit_semantics_id']):
            raise ValueError('exit_binding_differs')
    elif role == 'derivative_identity':
        if not all(value.get(k) for k in ('venue','market_type','environment')):
            raise ValueError('derivative_market_environment_unavailable')
    elif role in ('execution_reference','position_basis'):
        if not value.get('trade_id') or not value.get('timestamp_ms') or not value.get('basis'):
            raise ValueError('exact_execution_reference_or_position_basis_unavailable')
    elif role == 'proposal':
        from trader.portfolio.allocator import Proposal,canonical,verify,inputs_from_payload
        proposal=Proposal(value['proposal_id'],canonical(value['inputs']),canonical(value['result']),value['allocator_version'])
        if proposal.proposal_id!=reg['lineage']['proposal_id'] or not verify(proposal,inputs_from_payload(value['inputs'])):
            raise ValueError('allocation_proposal_replay_differs')
    elif role == 'intent':
        from trader.portfolio.trade_intent import TradeIntent
        intent=TradeIntent(value['intent_id'],value['payload_json'])
        if intent.intent_id!=reg['lineage']['intent_id']:
            raise ValueError('trade_intent_binding_differs')
    elif role == 'decision':
        if value.get('decision_id', value.get('id')) != reg['lineage'].get('decision_id'):
            raise ValueError('decision_binding_differs')
    elif role == 'reasons':
        from trader.cognition.outcomes import timestamp
        for vote in value.get('votes',()):
            if (vote['cycle_id']!=reg['lineage']['cycle_id'] or timestamp(vote['ts'])>reg['decision_ms']):
                raise ValueError('original_vote_binding_or_clock_differs')
    elif role == 'cycle':
        if value.get('cycle_id', value.get('id')) != reg['lineage'].get('cycle_id'):
            raise ValueError('cycle_binding_differs')
    elif role == 'cost':
        if value.get('schema_version')=='execution-accounting.v1':
            if not value.get('complete') or not value.get('fills'):
                raise ValueError('cost_accounting_incomplete')
        elif value.get('schema')=='versioned-paper-cost-receipt.v1':
            if not value.get('known_costs_complete'):
                raise ValueError('paper_cost_source_unavailable')
        else:
            raise ValueError('cost_receipt_unregistered')
    elif role == 'funding':
        from trader.observability.funding_events import replay
        if value.get('schema') == 'binance-funding-interval.v1':
            replay(value)
        elif value.get('schema_version') != 'execution-accounting.v1' or not value.get('funding_complete'):
            raise ValueError('funding_evidence_not_verified')
    elif role == 'portfolio':
        from trader.engine.evidence_capture import verify_snapshot
        if verify_snapshot(value) is None or value['completeness'] != 'COMPLETE':
            raise ValueError('exact_venue_portfolio_unavailable_journal_not_truth')
        obs = value['observation']
        if obs['complete'] is not True or any(value[k]!=obs[j] for k,j in (('venue','venue'),('market_type','market_type'),('environment','environment'),('observed_at_ms','as_of_ms'),('received_at_ms','response_received_ms'),('request_start_ms','request_start_ms'))):
            raise ValueError('venue_portfolio_projection_differs')
        if value['observed_at_ms'] > reg['decision_ms'] or value['received_at_ms'] > reg['decision_ms']:
            raise ValueError('future_portfolio')
        if [(p['instrument_id'], p['side'], p['quantity']) for p in value['positions']] != [(iid, side, qty) for iid, present, side, qty in obs['positions'] if present]:
            raise ValueError('venue_portfolio_projection_differs')
    elif role == 'control':
        if value.get('state') not in ('ACTIVE','FROZEN','HALTED') or value.get('version') != L.digest(value['record']) or value.get('state') != value['record'].get('state'):
            raise ValueError('exact_control_state_unavailable')
    elif role == 'economics':
        from trader.portfolio.economics import from_payload, from_inputs, verify
        receipt = from_payload(value)
        if not verify(receipt, from_inputs(json.loads(receipt.inputs_json)), reg['decision_ms']):
            raise ValueError('economics_receipt_unverified')
    elif role == 'risk_config':
        if not value.get('risk') or value.get('config_version') != L.digest(value['config']):
            raise ValueError('risk_config_version_unverified')
        if value.get('risk_version') != L.digest(value['risk']) or value['risk'] != value['config'].get('risk'):
            raise ValueError('risk_policy_binding_differs')


def manifest(db, outcome_id):
    row = db.execute('SELECT payload FROM learning_outcome_captures WHERE outcome_id=?', (outcome_id,)).fetchone()
    if row is None:
        raise ValueError('exact_outcome_missing')
    body = json.loads(row[0])
    if L.digest(body) != outcome_id:
        raise ValueError('outcome_capture_hash_differs')
    reg = body['registration']
    rid, stored = registration(db, reg['event_key'])
    if rid != body['registration_id'] or stored != reg:
        raise ValueError('outcome_registration_differs')
    needs = required(body['kind'], reg['lineage'], profile=reg['profile']) | set(reg.get('additional_required',()))
    deps = {d['role']:d for d in body['dependencies']}
    rows, sources, blobs = [], {}, {}
    for role in ROLES:
        dep = deps.get(role)
        if dep is None:
            dep = unavailable(role, 'REQUIRED_SOURCE_NOT_CAPTURED') if role in needs else dict(unavailable(role, 'NOT_APPLICABLE_TO_REGISTERED_EVENT'), status='NOT_APPLICABLE')
        elif dep['status'] == 'AVAILABLE':
            try:
                raw = resolve(db, dep)
                semantic(role, raw, reg)
                if role=='data':
                    for sha in chunk_hashes(raw):
                        entry=db.execute('SELECT payload FROM learning_source_blobs WHERE sha256=?',(sha,)).fetchone()
                        if entry is None or L.digest(json.loads(entry[0]))!=sha:
                            raise ValueError('data_chunk_missing_or_tampered')
                        blobs[sha]=json.loads(entry[0])
                if role not in POST and dep['available_ms'] > reg['decision_ms']:
                    raise ValueError('future_original_source')
                if dep['available_ms'] > body['observed_ms']:
                    raise ValueError('future_outcome_source')
                sources[role] = raw
            except (ValueError, KeyError, TypeError) as exc:
                dep = dict(dep, status='UNAVAILABLE', reason=str(exc))
        rows.append(dict(dep, required=role in needs))
    try:
        validate_chain(body,sources,blobs)
    except (ValueError,KeyError,TypeError) as exc:
        for dep in rows:
            if dep['role']=='outcome':
                dep.update(status='UNAVAILABLE',reason=str(exc))
        sources.pop('outcome',None)
    from . import decision_sources as D
    dm = body.get('decision_source_manifest')
    decision_faults = D.verify(dm, reg, sources) if dm else ('decision_manifest_missing_no_backfill',)
    complete = not decision_faults and all(r['status']=='AVAILABLE' for r in rows if r['required'])
    if body['boundary'] in ('UNRESOLVED', 'UNASSESSABLE'):
        status = 'UNASSESSABLE' if body['boundary']=='UNASSESSABLE' else 'REPLAY_INCOMPLETE'
    else:
        status = 'REPLAY_COMPLETE' if complete else 'REPLAY_PARTIAL' if sources else 'REPLAY_INCOMPLETE'
    result = dict(schema=SCHEMA, outcome_id=outcome_id, registration_id=rid, status=status,
                  dependencies=rows, sources=sources, data_blobs=blobs, capture=body,
                  decision_source_faults=list(decision_faults))
    return dict(manifest_id=L.digest(result), **result)


def learning_outcome(db, outcome_id):
    m = manifest(db, outcome_id)
    b, r = m['capture'], m['capture']['registration']
    lineage = L.Lineage(**dict(b['lineage'], execution_ids=tuple(b['lineage'].get('execution_ids',())), trade_ids=tuple(b['lineage'].get('trade_ids',()))))
    src = L.Source('capture_manifest', m['manifest_id'], SCHEMA, L.digest(m), b['observed_ms'])
    o = L.Outcome(L.Kind(b['kind']), L.Boundary(b['boundary']), lineage, r['decision_ms'], b['observed_ms'],
                  (src,), L.canonical(b['observation']), b['label'])
    return o, {(src.source_id, src.version): m}


def replay_manifest(outcome, retained):
    ref, = outcome.sources
    m = retained.get((ref.source_id, ref.version))
    faults = []
    if not m or L.digest(m) != ref.sha256 or L.digest({k:v for k,v in m.items() if k!='manifest_id'}) != m['manifest_id']:
        return L.Replay(outcome.outcome_id, 'INCOMPLETE', ('manifest_hash_differs',), L.canonical({}))
    b, reg = m['capture'], m['capture']['registration']
    if L.digest(b) != m['outcome_id'] or L.digest(reg) != m['registration_id']:
        faults.append('capture_identity_differs')
    from . import decision_sources as D
    dm = b.get('decision_source_manifest')
    faults.extend(D.verify(dm, reg, m['sources']) if dm else ('decision_manifest_missing_no_backfill',))
    needs = required(b['kind'], reg['lineage'], profile=reg['profile']) | set(reg.get('additional_required',()))
    rows = {d['role']:d for d in m['dependencies']}
    declared = {d['role']:d for d in b['dependencies']}
    original = {d['role']:d for d in reg['dependencies']}
    if len(rows) != len(m['dependencies']) or len(declared) != len(b['dependencies']):
        faults.append('duplicate_dependency')
    for role, dep in original.items():
        if declared.get(role) != dep:
            faults.append('original_dependency_changed:'+role)
    for role, dep in declared.items():
        row = rows.get(role)
        if row is None or any(row.get(k)!=dep.get(k) for k in ('sha256','source_id','version','available_ms','producer','storage','locator')):
            faults.append('dependency_declaration_differs:'+role)
    for role in needs:
        dep = rows.get(role)
        if not dep or dep['status'] != 'AVAILABLE' or role not in m['sources']:
            faults.append('missing:' + role)
    for role, raw in m['sources'].items():
        dep = rows.get(role)
        try:
            if not dep or dep['status']!='AVAILABLE' or L.digest(raw)!=dep['sha256']:
                raise ValueError('source_hash_differs')
            semantic(role, raw, reg)
            if dep['available_ms'] > b['observed_ms'] or role not in POST and dep['available_ms'] > reg['decision_ms']:
                raise ValueError('source_clock_differs')
        except (ValueError, KeyError, TypeError) as exc:
            faults.append(role+':'+str(exc))
    try:
        validate_chain(b,m['sources'],m.get('data_blobs',{}))
    except (ValueError,KeyError,TypeError) as exc:
        faults.append('measurement:'+str(exc))
    if (asdict(outcome.lineage) != dict(b['lineage'], execution_ids=tuple(b['lineage'].get('execution_ids',())), trade_ids=tuple(b['lineage'].get('trade_ids',())))
            or json.loads(outcome.observation_json)!=b['observation'] or outcome.kind.value!=b['kind'] or outcome.boundary.value!=b['boundary']):
        faults.append('canonical_outcome_differs')
    if m['status'] != 'REPLAY_COMPLETE' or b['boundary'] in ('UNRESOLVED','UNASSESSABLE'):
        faults.append('manifest_not_complete')
    return L.Replay(outcome.outcome_id, 'INCOMPLETE' if faults else 'COMPLETE', tuple(sorted(faults)), L.canonical({'capture_manifest':m}))


def safely(db, key, operation, *args, **kwargs):
    """Telemetry failure never changes existing trading/research transactions."""
    try:
        db.execute('SAVEPOINT learning_capture')
        ensure(db)
        value = operation(db, *args, **kwargs)
        db.execute('RELEASE learning_capture')
        return value
    except Exception as exc:
        db.execute('ROLLBACK TO learning_capture')
        db.execute('RELEASE learning_capture')
        ensure(db)
        db.execute('INSERT INTO learning_capture_failures VALUES(?,?)', (key, type(exc).__name__ + ':' + str(exc)[:160]))
        return None


def chunk_hashes(raw):
    if not isinstance(raw,dict) or raw.get('format')!='snapshot-frame-chunks.v1':
        return ()
    def leaves(value):
        if isinstance(value,dict):
            return [s for v in value.values() for s in leaves(v)]
        return [value] if value else []
    return tuple(sorted(set(leaves(raw['chunks']))))


def validate_chain(body,sources,blobs):
    from trader.cognition.opportunity_context import venue_key
    decision=sources.get('decision',{})
    data=sources.get('data',{})
    symbol=decision.get('symbol')
    if symbol and 'context' in sources:
        context=json.loads(sources['context']['context_json'])
        if venue_key(symbol)!=context['instrument']['symbol_key']:
            raise ValueError('original_context_decision_instrument_differs')
    if symbol and data.get('format')=='snapshot-frame-chunks.v1':
        if venue_key(symbol)!=venue_key(data['symbol']) or data['cutoff_ms']>body['registration']['decision_ms']:
            raise ValueError('original_data_decision_binding_or_clock_differs')
        identity=sources.get('derivative_identity')
        if identity and identity['market_type']!=data['market_type']:
            raise ValueError('original_derivative_market_type_differs')
    cycle=sources.get('cycle',{})
    if symbol and cycle.get('symbol') and venue_key(symbol)!=venue_key(cycle['symbol']):
        raise ValueError('original_cycle_decision_instrument_differs')
    for sha in chunk_hashes(sources.get('data')):
        if sha not in blobs or L.digest(blobs[sha])!=sha:
            raise ValueError('data_chunk_unverified')
        from .capture_runtime import restore_frame
        restore_frame(blobs[sha])
    out=sources.get('outcome')
    if out is None: raise ValueError('outcome_evidence_missing')
    if out.get('observation')!=body['observation']:
        raise ValueError('outcome_measurement_differs')
    if body['kind']=='EXECUTED_TRADE' and body['boundary']=='REALIZED':
        from trader.cognition.outcomes import replay
        receipt=replay(out['typed_receipt'])
        actual=receipt['actual_execution']
        if actual['status']!='verified_actual' or actual['net_pnl']!=body['observation'].get('net_pnl'):
            raise ValueError('realized_receipt_unverified')
        trade_id=receipt['source'].get('registration',{}).get('trade_id')
        if 'whole_trade_capture' in receipt['source']:
            trade_id=receipt['source']['whole_trade_capture']['bookings']['trade']['id']
        if trade_id not in body['lineage']['trade_ids']:
            raise ValueError('realized_trade_binding_differs')
        whole=receipt['source'].get('whole_trade_capture')
        if whole:
            from trader.engine.trade_accounting import replay as accounting_replay
            accounting=accounting_replay(whole)['accounting']
            trade=whole['bookings']['trade']
            if trade['decision_id']!=body['lineage']['decision_id'] or trade['strategy_id']!=body['lineage'].get('strategy_id'):
                raise ValueError('realized_decision_strategy_binding_differs')
            for role in ('cost','funding'):
                if role in sources and sources[role]!=accounting:
                    raise ValueError('realized_'+role+'_binding_differs')
            if 'execution' in sources and sources['execution']!=whole:
                raise ValueError('realized_execution_binding_differs')
            if 'trade' in sources and sources['trade']!=whole['bookings']['trade']:
                raise ValueError('realized_trade_source_differs')
            ref=sources.get('execution_reference')
            if ref and (ref.get('orders')!=whole['orders'] or ref['timestamp_ms']!=whole['start_ms'] or ref['trade_id']!=trade_id):
                raise ValueError('realized_execution_reference_differs')
            basis=sources.get('position_basis')
            if basis and (basis.get('position')!=whole['position'] or basis.get('fills')!=whole['fill_history']
                          or basis['timestamp_ms']!=whole['observed_ms'] or basis['trade_id']!=trade_id):
                raise ValueError('realized_position_basis_differs')
            identity=sources.get('derivative_identity')
            if identity and (identity['environment']!=whole['environment'] or identity['venue']!=whole['venue']):
                raise ValueError('realized_derivative_environment_differs')
    if 'risk_decision' in sources and 'risk_config' in sources:
        if sources['risk_decision'].get('config_risk_sha256')!=L.digest(sources['risk_config']['risk']):
            raise ValueError('risk_decision_config_binding_differs')
    cost=sources.get('cost',{})
    if cost.get('schema')=='versioned-paper-cost-receipt.v1':
        from trader.engine import paper_cost_evidence as P
        bundle=sources.get('execution',{})
        if bundle.get('receipt')!=cost:
            raise ValueError('paper_cost_source_bundle_unavailable')
        with sqlite3.connect(':memory:') as db:
            db.row_factory=sqlite3.Row
            P.ensure(db)
            for raw in bundle['sources'].values():
                P.freeze_source(db,raw)
            if P.build(db,cost['binding'],cost['source_ids'])!=cost:
                raise ValueError('paper_cost_replay_differs')
    if out.get('protocol')=='existing-forward-outcome.v1':
        import pandas as pd
        from trader.cognition.outcomes import timestamp
        declaration=out['declaration']
        frozen=sources.get('prediction')
        if frozen is None or frozen['declaration']!=declaration:
            raise ValueError('original_prediction_not_frozen')
        if declaration['decision_id']!=body['lineage']['decision_id']:
            raise ValueError('forward_decision_binding_differs')
        direction=1 if declaration['action']=='BUY' else -1
        horizons={'1h':3600000,'4h':14400000,'24h':86400000}
        for label,bar in out['target_bars'].items():
            opened=int(pd.Timestamp(bar['ts']).timestamp()*1000)
            if opened < timestamp(declaration['ts'])+horizons[label] or opened+300000>out['measured_ms']:
                raise ValueError('target_clock_not_closed_or_horizon_differs')
            expected=round((float(bar['close'])-declaration['entry_price'])/declaration['entry_price']*direction,6)
            if out['observation']['returns'].get('fwd_ret_'+label)!=expected:
                raise ValueError('forward_arithmetic_differs')
        if not out['target_bars']: raise ValueError('forward_target_missing')
    if body['registration']['profile']=='RESEARCH' and 'bank' in sources:
        chain=sources['research_run']['chain']
        bank=sources['bank']
        if bank.get('bank_kind')=='strategy_health_unreadable':
            from trader.cognition import research_unreadable_bank as B
            rebuilt=B.build(chain)
        elif bank.get('bank_kind')=='investigation_volume_anomaly':
            from trader.observability import investigation_research as I
            I.verify_evidence(chain['evidence'])
            if I.build_plan(chain['question'])!=chain['plan'] or I.build_result(chain['evidence'])!=chain['result'] or I.build_run(chain['question'],chain['plan'],chain['evidence'],chain['result'])!=chain['receipt']:
                raise ValueError('registered_research_family_replay_differs')
            rebuilt=I.build_bank(chain['question'],chain['plan'],chain['evidence'],chain['result'],chain['receipt'])
        else:
            from trader.cognition import research_bank as B
            rebuilt=B.build(chain)
        if rebuilt!=bank:
            raise ValueError('research_bank_chain_differs')
        for role,key in (('question','question'),('plan','plan'),('research_evidence','evidence'),('falsifier_result','result')):
            if sources[role]!=chain[key]: raise ValueError('research_chain_link_differs')
