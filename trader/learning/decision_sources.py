"""Immutable decision-time source inventory. No reads of current state at replay."""
from . import foundation as L

SCHEMA = 'decision-source-manifest.v1'
TABLE = 'learning_decision_source_manifests'
ROLES = ('cycle', 'decision', 'data', 'world', 'context', 'portfolio', 'risk_config',
         'control', 'strategy', 'exit_semantics', 'economics', 'proposal', 'intent',
         'reasons', 'derivative_identity')
MANDATORY = {'cycle', 'decision', 'data', 'world', 'context', 'portfolio', 'risk_config', 'control', 'reasons'}


def needs(reg):
    if reg['profile'] in ('RESEARCH', 'DECAY_EVALUATION'):
        return {d['role'] for d in reg['dependencies']}
    required = set(MANDATORY)
    for role, key in (('strategy', 'strategy_id'), ('proposal', 'proposal_id'), ('intent', 'intent_id')):
        if reg['lineage'].get(key): required.add(role)
    if 'strategy' in required: required.add('exit_semantics')
    if 'proposal' in required: required.add('economics')
    required.update(reg.get('additional_required', ()))
    required.update(d['role'] for d in reg['dependencies'])
    return required


def make(reg):
    """Only the original registration is permitted; absent stages stay explicit."""
    from .capture import unavailable
    deps = {d['role']: d for d in reg['dependencies']}
    required = needs(reg)
    rows = []
    for role in sorted(set(ROLES) | set(deps)):
        dep = deps.get(role)
        if dep is None:
            dep = unavailable(role, reg.get('not_consulted', {}).get(role) or
                              ('NOT_SUPPLIED_AT_DECISION' if role in required else 'STAGE_NOT_USED'))
            if role not in required: dep['status'] = 'NOT_APPLICABLE'
        rows.append(dict(dep, required=role in required))
    body = dict(schema=SCHEMA, event_key=reg['event_key'], profile=reg['profile'],
                decision_ms=reg['decision_ms'], lineage=reg['lineage'], dependencies=rows)
    return dict(manifest_id=L.digest(body), **body)


def verify(manifest, reg, sources):
    """Detached verification against original ids/hashes, clocks and producer joins."""
    from . import capture as C
    faults = []
    if reg.get('decision_source_manifest_id') and manifest and reg['decision_source_manifest_id']!=manifest.get('manifest_id'):
        return ('decision_manifest_reference_differs',)
    if manifest != make(reg): return ('decision_manifest_identity_differs',)
    for dep in manifest['dependencies']:
        role = dep['role']
        if dep['required'] and dep['status'] != 'AVAILABLE':
            faults.append('missing:' + role)
        if dep['status'] != 'AVAILABLE': continue
        value = sources.get(role)
        try:
            if value is None or L.digest(value) != dep['sha256']:
                raise ValueError('source_hash_differs')
            if not dep['source_id'] or not dep['version'] or dep['version'].lower() == 'latest':
                raise ValueError('exact_identity_required')
            if type(dep['available_ms']) is not int or not 0 <= dep['available_ms'] <= reg['decision_ms']:
                raise ValueError('source_clock_differs')
            if reg['profile'] not in ('RESEARCH', 'DECAY_EVALUATION'): C.semantic(role, value, reg)
        except (ValueError, KeyError, TypeError) as exc:
            faults.append(role + ':' + str(exc))
    if reg['profile'] not in ('RESEARCH', 'DECAY_EVALUATION'):
        data=sources.get('data', {})
        for role, clock in (data.get('source_evidence') or {}).items():
            if type(clock.get('known_at_ms')) is not int or clock['known_at_ms'] > reg['decision_ms']:
                faults.append('freshness_clock:'+role)
            expiry=clock.get('valid_until_ms')
            if expiry is not None and expiry < reg['decision_ms']:
                faults.append('stale_at_decision:'+role)
        if not reg['lineage'].get('cycle_id') or not reg['lineage'].get('decision_id'):
            faults.append('decision_cycle_identity_missing')
        try:
            if 'economics' in sources:
                from trader.portfolio.economics import from_payload, from_inputs
                import json
                er=from_payload(sources['economics'])
                binding=from_inputs(json.loads(er.inputs_json)).binding
                if binding.context_json!=sources['context']['context_json']:
                    raise ValueError('economics_context_differs')
                for key in ('opportunity_id','strategy_id','version_id','spec_hash'):
                    if getattr(binding,key)!=reg['lineage'].get(key):
                        raise ValueError('economics_'+key+'_differs')
                for source in json.loads(er.inputs_json)['context']:
                    if source['source_id'].startswith('candidate-bridge:'):
                        policy=json.loads(source['payload_json'])['config']
                        config=sources['risk_config']['config']
                        if policy!={k:config.get(k,{}) for k in ('risk','strategies')}:
                            raise ValueError('allocation_risk_config_differs')
            proposal = sources.get('proposal')
            if proposal:
                book = proposal['inputs']['portfolio']
                if book['snapshot_id'] != sources['portfolio']['snapshot_id']:
                    raise ValueError('allocation_portfolio_differs')
                if proposal['inputs']['control_state'] != sources['control']['state']:
                    raise ValueError('allocation_control_differs')
                candidates = proposal['inputs']['candidates']
                matching = [c for c in candidates if c['version_id'] == reg['lineage'].get('version_id')
                            and c['opportunity_id'] == reg['lineage'].get('opportunity_id')]
                if len(matching) != 1: raise ValueError('allocation_candidate_differs')
                c = matching[0]
                if c['opportunity_context_json'] != sources['context']['context_json'] or c['spec_hash'] != reg['lineage']['spec_hash']:
                    raise ValueError('allocation_context_strategy_differs')
                if c['economics']['receipt_json'] != L.canonical(sources['economics']):
                    raise ValueError('allocation_economics_differs')
                if 'intent' in sources:
                    from trader.portfolio.trade_intent import build
                    from trader.portfolio.allocator import Proposal, inputs_from_payload
                    p = Proposal(proposal['proposal_id'], L.canonical(proposal['inputs']), L.canonical(proposal['result']), proposal['allocator_version'])
                    if sources['intent'] not in [dict(intent_id=i.intent_id, payload_json=i.payload_json) for i in build(p, inputs_from_payload(proposal['inputs']))]:
                        raise ValueError('intent_proposal_differs')
        except (ValueError, KeyError, TypeError) as exc:
            faults.append('binding:' + str(exc))
    return tuple(sorted(set(faults)))


def load(db, identity):
    import json
    row = db.execute(f'SELECT payload FROM {TABLE} WHERE manifest_id=?', (identity,)).fetchone()
    if row is None: raise ValueError('decision_manifest_missing_no_backfill')
    m = json.loads(row[0])
    if L.digest({k:v for k,v in m.items() if k != 'manifest_id'}) != identity:
        raise ValueError('decision_manifest_hash_differs')
    return m
