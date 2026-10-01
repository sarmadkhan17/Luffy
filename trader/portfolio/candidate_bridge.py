"""Read/verify/transform/bind only. Factory and allocator retain authority.

Recorded query answers allow detached replay, not authentication. Current
verification must reread the trusted reader under a consistent read snapshot.
"""
from dataclasses import asdict, replace
import json

from trader.strategy import factory_handoff as factory, capacity
from . import opportunity_live as live, economics
from .allocator import Source, Evidence, Status, canonical, digest

SCHEMA = 'portfolio-candidate-bridge.v1'
ELIGIBLE = 'ELIGIBLE_FOR_PORTFOLIO_COMPARISON'


class ReadEvidence:
    def __init__(self, reader=None, answers=None):
        self.reader = reader
        self.answers = {} if answers is None else dict(answers)

    def query(self, sql, params=()):
        if not sql.lstrip().upper().startswith(('SELECT ', 'PRAGMA TABLE_INFO(')):
            raise ValueError('BRIDGE_WRITE_REFUSED')
        key = canonical([sql, list(params)])
        if self.reader is not None:
            bounded = sql + ' LIMIT 1001' if sql.lstrip().upper().startswith('SELECT ') and 'LIMIT' not in sql.upper() else sql
            rows = [dict(r) for r in self.reader.query(bounded, params)]
            if len(rows) > 1000 or len(canonical(rows)) > 2 * 1024**2:
                raise ValueError('BRIDGE_READ_BOUND_EXCEEDED')
            if key in self.answers and self.answers[key] != rows:
                raise ValueError('BRIDGE_SOURCE_CHANGED_DURING_READ')
            self.answers[key] = rows
        if key not in self.answers:
            raise ValueError('BRIDGE_REPLAY_READ_MISSING')
        return self.answers[key]

    def kv_get(self, key, default=None):
        rows = self.query('SELECT value FROM state_kv WHERE key=?', (key,))
        return rows[0]['value'] if rows else default


def eligible_for_portfolio_comparison(reader, receipt, config, available_inputs=None):
    p = json.loads(receipt.payload_json)
    live.replay(receipt, tuple(Source(**s) for s in p['sources']), p['as_of_ms'])
    if not p['strategy_version_id']:
        raise ValueError('EXACT_STRATEGY_VERSION_UNAVAILABLE')
    v = factory.load_version(reader, p['strategy_version_id'])
    bound = next(s for s in p['sources'] if s['source_id'] == 'strategy_version')
    row = json.loads(bound['payload_json'])['data']
    if reader.query('SELECT * FROM strategy_versions WHERE version_id=?', (v['version_id'],)) != [row]:
        raise ValueError('CURRENT_FACTORY_VERSION_DIFFERS')
    state = factory.state_of(reader, v['version_id'])
    valid_states = {factory.VALIDATED, factory.SHADOW, factory.APPROVAL_REQUIRED,
                    factory.APPROVED_FIRST_LIVE, 'ACTIVE', 'PAUSED', 'REACTIVATED'}
    if state not in valid_states:
        raise ValueError('VERSION_NOT_VALID_FOR_COMPARISON:' + str(state))
    validation = factory.verify_validation(reader, v)
    need = sorted(v['spec'].get('data_requires') or [])
    if available_inputs is None or not set(need) <= set(available_inputs):
        raise ValueError('REQUIRED_STRATEGY_INPUTS_UNAVAILABLE')
    # Existing live producer verifies exact instrument, direction, timeframe,
    # signal fingerprint and universe. No matching heuristics here.
    probation = dict(status='UNKNOWN', reason='PROBATION_RECEIPT_MISSING')
    rows = reader.query('SELECT * FROM strategy_probation_receipts WHERE version_id=? ORDER BY recorded_at_ms DESC LIMIT 1', (v['version_id'],))
    if rows:
        rec = factory._load(rows[0])
        if (rec.get('version_id'), rec.get('spec_hash')) != (v['version_id'], v['spec_hash']):
            raise ValueError('PROBATION_IDENTITY_DIFFERS')
        probation = dict(status=rec['status'], receipt_id=rec['receipt_id'], verified=False)
        if rec['status'] == factory.P_SATISFIED:
            try:
                factory._verify_probation(reader, config, v, rec['receipt_id'])
            except factory.HandoffRefused as exc:
                # Probation maturity is allocator evidence, not comparison
                # admission. Its own authority supplies the exact refusal.
                probation['reason'] = exc.code
            else:
                probation['verified'] = True
    request = factory.approval_request(reader, v['version_id'])
    approval = dict(status='UNKNOWN', request=request, decisions=[])
    if request:
        approval['decisions'] = [factory._load(r) for r in reader.query('SELECT * FROM strategy_approval_decisions WHERE request_id=?', (request['request_id'],))]
    cap = dict(status='UNKNOWN', current=False, reason='CAPACITY_RECEIPT_MISSING')
    names = {r['name'] for r in reader.query("SELECT name FROM sqlite_master WHERE type='table'")}
    if capacity.TABLE in names:
        rows = reader.query('SELECT * FROM strategy_capacity_receipts WHERE version_id=? AND instrument_id=? ORDER BY as_of_ms DESC LIMIT 1', (v['version_id'], p['instrument_id']))
        if rows:
            cap = capacity.check_current(reader, config, v, rows[0]['receipt_id'], now_ms=p['as_of_ms'])
            rec = capacity.load(reader, rows[0]['receipt_id'])
            cap['dimensions'] = rec['result']['dimensions']
    observations = {k: reader.kv_get(k) for k in ('account_observation', 'account_margin_observation')}
    observations['status'] = 'UNKNOWN'
    observations['venue_and_account_dimensions'] = cap.get('dimensions', {}).get('venue', {})
    return dict(observations=observations, status=ELIGIBLE, strategy_id=v['strategy_id'], version_id=v['version_id'],
                spec_hash=v['spec_hash'], lifecycle_state=state, validation=validation,
                probation=probation, approval=approval, capacity=cap,
                exit_semantics_id=validation['evidence']['exit_semantics_id'],
                required_data=need, required_features={k: v['spec'].get(k) for k in ('entry_long', 'entry_short', 'filters')},
                available_inputs=sorted(available_inputs), authority='NONE')


def freeze_authority(reader, receipt, config, available_inputs):
    safe_config = {k: config.get(k, {}) for k in ('risk', 'strategies')}
    recording = ReadEvidence(reader)
    result = eligible_for_portfolio_comparison(recording, receipt, safe_config, available_inputs)
    body = dict(schema=SCHEMA, live_receipt_id=receipt.receipt_id, config=safe_config,
                available_inputs=sorted(available_inputs), answers=recording.answers, result=result)
    if len(canonical(body)) > 8 * 1024**2:
        raise ValueError('BRIDGE_EVIDENCE_BOUND_EXCEEDED')
    return Source.freeze('candidate-bridge:' + digest(body), body)


def _verified(source, receipt):
    Source(**asdict(source))
    body = json.loads(source.payload_json)
    if (body['schema'] != SCHEMA or source.source_id != 'candidate-bridge:' + digest(body)
            or body['live_receipt_id'] != receipt.receipt_id):
        raise ValueError('BRIDGE_IDENTITY_DIFFERS')
    result = eligible_for_portfolio_comparison(ReadEvidence(answers=body['answers']), receipt, body['config'], body['available_inputs'])
    if result != body['result']:
        raise ValueError('BRIDGE_AUTHORITY_REPLAY_DIFFERS')
    return result


def candidate(receipt, economic_receipt, authority):
    result = _verified(authority, receipt)
    inputs = economics.from_inputs(json.loads(economic_receipt.inputs_json))
    if authority not in inputs.context:
        raise ValueError('BRIDGE_ECONOMICS_SOURCE_MISSING')
    c, sources = live.candidate(receipt, economic_receipt)
    known = Evidence(Status.ESTABLISHED, (authority.source_id,))
    unknown = Evidence(Status.UNKNOWN, (authority.source_id,))
    p = json.loads(receipt.payload_json)
    expiries = [s['valid_until_ms'] for role, s in p['source_status'].items()
                if role in set(p['required_roles']) | {'signals', 'strategy_version'} and s.get('valid_until_ms') is not None]
    required = set(p['required_roles']) | {'signals', 'strategy_version'}
    fresh = all(p['source_status'][role].get('freshness') == 'CURRENT' for role in required)
    components = json.loads(economic_receipt.result_json)['components']
    costs_ready = all(components[k]['status'] in ('ESTABLISHED', 'NOT_APPLICABLE') for k in ('COMMISSION', 'SLIPPAGE', 'FUNDING/BORROW'))
    c = replace(c, validation=Evidence(Status.ESTABLISHED, (authority.source_id,),
                    canonical({'status': ELIGIBLE, 'lifecycle_state': result['lifecycle_state']})),
                probation=known if result['probation'].get('verified') else Evidence(Status.UNKNOWN, (authority.source_id,), canonical(result['probation'])),
                costs=Evidence(Status.ESTABLISHED if costs_ready else Status.UNAVAILABLE, c.economics.evidence.source_ids),
                account_instrument=Evidence(Status.UNKNOWN, (authority.source_id,), canonical(result['observations'])),
                capacity=known if result['capacity'].get('current') else Evidence(Status.UNKNOWN if result['capacity']['status'] == 'UNKNOWN' else Status.UNAVAILABLE, (authority.source_id,), canonical(result['capacity'])),
                freshness=known if fresh else unknown, valid_until_ms=min(expiries) if fresh else None,
                source_ids=tuple(sorted(set((*c.source_ids, authority.source_id)))))
    return c, tuple({s.source_id: s for s in (*sources, authority)}.values())


def build(reader, receipt, config, *, available_inputs=None, dimensions=None):
    if receipt is None:
        return None
    authority = freeze_authority(reader, receipt, config, available_inputs)
    binding = live.economic_binding(receipt, **dimensions)
    er = economics.build(economics.Inputs(binding, context=(receipt.as_source(), authority)))
    c, sources = candidate(receipt, er, authority)
    return c, er, sources


def verify_candidate(c, receipt, er):
    inputs = economics.from_inputs(json.loads(er.inputs_json))
    authorities = [s for s in inputs.context if s.source_id.startswith('candidate-bridge:')]
    if len(authorities) != 1:
        return False
    expected, _ = candidate(receipt, er, authorities[0])
    return c == expected


def verify_current(c, receipt, er, reader, config, available_inputs, now_ms, *, current_sources):
    inputs = economics.from_inputs(json.loads(er.inputs_json))
    live.replay(receipt, current_sources, now_ms)
    authority = freeze_authority(reader, receipt, config, available_inputs)
    result = json.loads(authority.payload_json)['result']
    if result['capacity'].get('current') and not capacity.check_current(reader, config,
            factory.load_version(reader, c.version_id), result['capacity']['receipt_id'], now_ms=now_ms)['current']:
        return False
    return authority in inputs.context and verify_candidate(c, receipt, er) and economics.verify(er, inputs, now_ms)


def _lineage(reader, version_id):
    chain, seen = [], set()
    current = version_id
    while current:
        if current in seen or len(chain) >= 64:
            raise ValueError('LINEAGE_CYCLE_OR_READ_BOUND')
        seen.add(current)
        v = factory.load_version(reader, current)
        chain.append(v)
        current = v['parent_version_id']
    if len({v['strategy_id'] for v in chain}) != 1:
        raise ValueError('LINEAGE_STRATEGY_ID_DIFFERS')
    return dict(version_id=version_id, strategy_id=chain[0]['strategy_id'],
                spec_hash=chain[0]['spec_hash'], root_version_id=chain[-1]['version_id'],
                chain=[v['version_id'] for v in chain])


def freeze_lineage(reader, version_id, *, as_of_ms, valid_until_ms):
    if type(as_of_ms) is not int or type(valid_until_ms) is not int or not 0 <= as_of_ms <= valid_until_ms:
        raise ValueError('LINEAGE_FRESHNESS_INVALID')
    recorded = ReadEvidence(reader)
    result = _lineage(recorded, version_id)
    # Every version must already exist at this cut.
    for rows in recorded.answers.values():
        if any(r.get('recorded_at_ms', as_of_ms + 1) > as_of_ms for r in rows):
            raise ValueError('FUTURE_LINEAGE_EVIDENCE')
    body = dict(schema='strategy-lineage-context.v1', as_of_ms=as_of_ms,
                valid_until_ms=valid_until_ms, result=result, answers=recorded.answers)
    return Source.freeze('strategy-lineage:' + digest(body), body)


def replay_lineage(source, now_ms):
    Source(**asdict(source))
    body = json.loads(source.payload_json)
    if (body['schema'] != 'strategy-lineage-context.v1'
            or source.source_id != 'strategy-lineage:' + digest(body)
            or not body['as_of_ms'] <= now_ms <= body['valid_until_ms']):
        raise ValueError('LINEAGE_SOURCE_STALE_OR_INVALID')
    result = _lineage(ReadEvidence(answers=body['answers']), body['result']['version_id'])
    if result != body['result'] or any(r.get('recorded_at_ms', body['as_of_ms']+1) > body['as_of_ms']
                                      for rows in body['answers'].values() for r in rows):
        raise ValueError('LINEAGE_REPLAY_DIFFERS')
    return result
