"""Pure Stage-6 producer around unchanged opportunity-context.v1.

The outer receipt freezes existing source payloads and their availability and
reader freshness policies. It is NOT another Opportunity Context schema or
trace ledger. Source hashes prove integrity, not venue authenticity. Callers
must use trusted read-only source readers; current verification rereads them.
"""
from dataclasses import asdict, dataclass
import json
from pathlib import Path

from trader.cognition import opportunity_context as oc
from trader.world.replay import WorldModelRecord
from trader.strategy.signal_occurrence import signal_occurrence
from .allocator import Source, canonical, digest, Evidence, Status, Candidate
from .opportunity_registry import identity

SCHEMA = 'opportunity-live-inputs.v1'
ROLES = frozenset(('attention_scan', 'allocation', 'registry_selection', 'signals',
                   'investigation', 'investigation_update', 'world_model', 'portfolio',
                   'strategy_version', 'strategy_authority_inventory', 'economics_receipts',
                   'cost_status', 'capacity_status'))


def source(role, data, known_at_ms, valid_until_ms=None):
    if role not in ROLES:
        raise ValueError('UNKNOWN_SOURCE_ROLE')
    return Source.freeze(role, dict(data=data, known_at_ms=known_at_ms, valid_until_ms=valid_until_ms))


@dataclass(frozen=True)
class LiveReceipt:
    receipt_id: str
    payload_json: str

    @property
    def context(self):
        return oc.OpportunityContext.from_json(json.loads(self.payload_json)['context_json'])

    def as_source(self):
        return Source.freeze('live-context:' + self.receipt_id, json.loads(self.payload_json))


def _selection(raw):
    from trader.observability.registry_selector import Selection, Exclusion
    raw = dict(raw)
    raw['exclusions'] = tuple(Exclusion(*x) for x in raw['exclusions'])
    for name in ('candidate_ids', 'unmatched_scan_symbols'):
        raw[name] = tuple(raw[name])
    return Selection(**raw)


def produce(*, as_of_ms, symbol, instrument_id, cycle_id, candidate_id,
            sources=(), required_roles=()):
    if type(as_of_ms) is not int or as_of_ms < 0:
        raise ValueError('CONTEXT_CUT_INVALID')
    if not set(required_roles) <= ROLES:
        raise ValueError('UNKNOWN_REQUIRED_ROLE')
    frozen, available, statuses = {}, {}, {}
    for s in sources:
        s = Source(**asdict(s))  # refuse object.__setattr__ and changed bytes
        role = s.source_id
        if role not in ROLES or role in frozen:
            raise ValueError('SOURCE_ROLE_DUPLICATE_OR_UNSUPPORTED')
        raw = json.loads(s.payload_json)
        if set(raw) != {'data', 'known_at_ms', 'valid_until_ms'}:
            raise ValueError('LIVE_SOURCE_FIELDS_INVALID')
        known, expires = raw['known_at_ms'], raw['valid_until_ms']
        if type(known) is not int or not 0 <= known <= as_of_ms:
            raise ValueError('FUTURE_SOURCE_OR_INVALID_AVAILABILITY')
        if expires is not None and (type(expires) is not int or expires < known):
            raise ValueError('SOURCE_FRESHNESS_INVALID')
        frozen[role] = s
        stale = expires is not None and as_of_ms > expires
        statuses[role] = dict(status='UNKNOWN' if stale else 'AVAILABLE',
                              reason='STALE_SOURCE' if stale else 'SOURCE_BOUND',
                              source_id=role, sha256=s.sha256, known_at_ms=known,
                              valid_until_ms=expires, freshness='UNKNOWN' if expires is None else
                              'STALE' if stale else 'CURRENT')
        # Verify every supplied object, even a stale optional source, before
        # excluding it from current context. Staleness never hides corruption.
        available[role] = raw['data']
    for role in ROLES - frozen.keys():
        statuses[role] = dict(status='UNKNOWN', reason='NOT_SUPPLIED')
    for role in required_roles:
        if statuses[role]['status'] != 'AVAILABLE':
            raise ValueError('REQUIRED_CONTEXT_EVIDENCE_' + role.upper() + '_UNAVAILABLE')

    world = WorldModelRecord.from_json(available['world_model']) if 'world_model' in available else None
    scan = available.get('attention_scan')
    if world:
        cut = scan['as_of_ms'] if scan else as_of_ms
        if world.reconstruct().as_of_ms != cut:
            raise ValueError('WORLD_MODEL_CUT_MISMATCH')
        # World models may be market-wide; an instrument state must exist for
        # this candidate. Other states remain descriptive model context.
        if not any(oc.venue_key(s.instrument) == oc.venue_key(symbol) for s in world.reconstruct().states):
            raise ValueError('WORLD_MODEL_INSTRUMENT_MISMATCH')
    # Underlying source event time cannot be hidden by an availability wrapper.
    for role, data in available.items():
        known = json.loads(frozen[role].payload_json)['known_at_ms']
        times = []
        if role == 'attention_scan':
            times = [data['persisted_at_ms'], data['as_of_ms']]
        elif role == 'world_model':
            times = [world.reconstruct().as_of_ms]
        elif role == 'portfolio':
            from trader.engine.evidence_capture import verify_snapshot
            if verify_snapshot(data) is None or data['completeness'] != 'COMPLETE':
                raise ValueError('VENUE_PORTFOLIO_UNVERIFIED_OR_INCOMPLETE')
            obs = data['observation']
            if ([(p['instrument_id'], p['side'], p['quantity']) for p in data['positions']]
                    != [(iid, side, qty) for iid, present, side, qty in obs['positions'] if present]
                    or any(data[k] != obs[j] for k, j in
                           (('venue', 'venue'), ('market_type', 'market_type'), ('environment', 'environment'),
                            ('observed_at_ms', 'as_of_ms'), ('received_at_ms', 'response_received_ms'),
                            ('request_start_ms', 'request_start_ms')))
                    or obs['complete'] is not True):
                raise ValueError('VENUE_PORTFOLIO_PROJECTION_DIFFERS')
            times = [data['observed_at_ms']]
        elif role == 'strategy_version':
            from trader.strategy.factory_handoff import load_version
            class Reader:
                def query(self, sql, params):
                    if sql == 'SELECT * FROM strategy_versions WHERE version_id=?':
                        return [data] if data['version_id'] == params[0] else []
                    raise ValueError('VERSION_READ_UNREGISTERED')
            load_version(Reader(), data['version_id'])
            times = [data['recorded_at_ms']]
        elif role in ('cost_status', 'capacity_status'):
            # These are descriptive inventory receipts, not model authority.
            if data.get('instrument') != instrument_id or data.get('status') not in ('UNKNOWN', 'UNAVAILABLE'):
                raise ValueError('UNVERIFIED_COST_CAPACITY_AUTHORITY')
            times = [data['as_of_ms']]
        elif role == 'registry_selection':
            times = [data['cycle_as_of_ms'], data['snapshot_as_of_ms']]
        elif role == 'allocation':
            times = [data['decision_cut_ms']]
        elif role == 'investigation':
            times = [data['registered_ms'], *(data['state'][k] for k in ('as_of_ms', 'available_ms', 'observed_ms'))]
        elif role == 'investigation_update':
            times = [data['as_of_ms'], data['observed_ms']]
        elif role == 'strategy_authority_inventory':
            if not isinstance(data, dict) or not set(data) <= {'validation', 'probation', 'installs', 'capacity', 'events', 'governor'}:
                raise ValueError('STRATEGY_AUTHORITY_INVENTORY_INVALID')
            version_id = available.get('strategy_version', {}).get('version_id')
            for rows in data.values():
                if not isinstance(rows, list) or len(rows) > 64:
                    raise ValueError('STRATEGY_AUTHORITY_INVENTORY_BOUND')
                for row in rows:
                    if row.get('version_id') != version_id:
                        raise ValueError('STRATEGY_AUTHORITY_VERSION_DIFFERS')
                    if 'canonical_json' in row and digest(json.loads(row['canonical_json'])) != row.get('canonical_sha256'):
                        raise ValueError('STRATEGY_AUTHORITY_SOURCE_CORRUPT')
                    times.append(row['recorded_at_ms'])
        elif role == 'economics_receipts':
            from . import economics as econ
            if not isinstance(data, list) or len(data) > 16:
                raise ValueError('ECONOMICS_REFERENCE_BOUND')
            for payload in data:
                receipt = econ.from_payload(payload)
                ei = econ.from_inputs(json.loads(receipt.inputs_json))
                if ei.binding.instrument != instrument_id or not econ.verify(receipt, ei, as_of_ms):
                    raise ValueError('ECONOMICS_REFERENCE_REFUSED')
                times.append(ei.binding.as_of_ms)
        elif role == 'signals':
            for sig in data:
                key, _ = signal_occurrence(sig)
                if key:
                    times.append(key[-1])
        if any(type(t) is not int or t > known or t < 0 for t in times):
            raise ValueError('SOURCE_EVENT_AFTER_AVAILABILITY')
    def core(data):
        return oc.build(as_of_ms=as_of_ms, symbol=symbol, instrument_id=instrument_id,
                        attention_scan=data.get('attention_scan'), allocation=data.get('allocation'),
                        registry_selection=_selection(data['registry_selection']) if 'registry_selection' in data else None,
                        signals=data.get('signals', ()), investigation=data.get('investigation'),
                        investigation_update=data.get('investigation_update'),
                        world_model=world if 'world_model' in data else None)
    core(available)  # validates optional stale payloads too
    usable = {k: v for k, v in available.items() if statuses[k]['status'] == 'AVAILABLE'}
    if 'attention_scan' not in usable:
        usable.pop('allocation', None)
    if 'investigation' not in usable:
        usable.pop('investigation_update', None)
    ctx = core(usable)
    version = None
    if 'strategy_version' in usable:
        version = json.loads(usable['strategy_version']['canonical_json'])
        occurrences = ctx.to_dict()['signal_occurrences']['items']
        from trader.strategy.spec import StrategySpec
        from trader.strategy.signal_occurrence import spec_fingerprint
        fp = spec_fingerprint(StrategySpec.from_dict(version['spec']))
        if (len(occurrences) != 1 or occurrences[0]['status'] != 'AVAILABLE'
                or occurrences[0]['key']['spec_id'] != version['strategy_id']
                or occurrences[0]['key']['spec_fingerprint'] != fp):
            raise ValueError('EXACT_VERSION_SIGNAL_LINEAGE_UNPROVEN')
        key = occurrences[0]['key']
        spec = version['spec']
        if (key['signal_timeframe'] != spec['timeframe']
                or key['action'] not in ('BUY', 'SELL')
                or spec['direction'] not in ('both', 'long' if key['action'] == 'BUY' else 'short')
                or 'futures' not in spec['markets']):
            raise ValueError('VERSION_SIGNAL_HORIZON_DIRECTION_OR_MARKET_DIFFERS')
        universe = spec.get('universe') or {}
        include = {oc.venue_key(s) for s in universe.get('include', ())}
        exclude = {oc.venue_key(s) for s in universe.get('exclude', ())}
        if (include and oc.venue_key(symbol) not in include) or oc.venue_key(symbol) in exclude:
            raise ValueError('VERSION_INSTRUMENT_UNIVERSE_DIFFERS')
    oid, lineage = identity(ctx, cycle_id, candidate_id, version['version_id'] if version else None)
    portfolio = usable.get('portfolio')
    direction = {'BUY': 'LONG', 'SELL': 'SHORT'}.get((lineage['occurrence'] or {}).get('action'))
    interaction = 'NO_ACTION'
    if portfolio and direction:
        held = [p for p in portfolio['positions'] if p['instrument_id'] == instrument_id]
        if held:
            interaction = 'SUPPORTS_EXISTING' if all(p['side'].upper() == direction for p in held) else 'CONFLICTS_EXISTING'
    payload = dict(schema=SCHEMA, as_of_ms=as_of_ms, symbol=symbol, instrument_id=instrument_id,
                   cycle_id=cycle_id, candidate_id=candidate_id, opportunity_id=oid,
                   context_id=ctx.context_id, context_json=ctx.canonical_json,
                   required_roles=sorted(set(required_roles)),
                   sources=[asdict(frozen[k]) for k in sorted(frozen)], source_status=statuses,
                   strategy_version_id=version['version_id'] if version else None,
                   strategy_eligibility='UNKNOWN', existing_position_interaction=interaction,
                   portfolio_status=statuses['portfolio']['status'], authority='NONE')
    return LiveReceipt(digest(payload), canonical(payload))


def replay(receipt, current_sources, now_ms=None):
    p = json.loads(receipt.payload_json)
    if p['schema'] != SCHEMA or digest(p) != receipt.receipt_id:
        raise ValueError('LIVE_CONTEXT_INTEGRITY_REFUSED')
    kwargs = {k: p[k] for k in ('as_of_ms', 'symbol', 'instrument_id', 'cycle_id', 'candidate_id', 'required_roles')}
    again = produce(**kwargs, sources=current_sources)
    if again != receipt:
        raise ValueError('LIVE_CONTEXT_SOURCE_SET_DIFFERS')
    if now_ms is not None:
        if type(now_ms) is not int or now_ms < p['as_of_ms']:
            raise ValueError('LIVE_CONTEXT_CLOCK_INVALID')
        for s in current_sources:
            raw = json.loads(s.payload_json)
            if s.source_id in p['required_roles'] and raw['valid_until_ms'] is not None and now_ms > raw['valid_until_ms']:
                raise ValueError('REQUIRED_CONTEXT_EVIDENCE_STALE')
    return again


def receipt_from_source(s):
    s = Source(**asdict(s))
    p = json.loads(s.payload_json)
    receipt = LiveReceipt(digest(p), canonical(p))
    if s.source_id != 'live-context:' + receipt.receipt_id:
        raise ValueError('LIVE_CONTEXT_SOURCE_ID_DIFFERS')
    return replay(receipt, tuple(Source(**v) for v in p['sources']))


def economic_binding(receipt, **dimensions):
    from .economics import Binding
    p = json.loads(receipt.payload_json)
    replay(receipt, tuple(Source(**v) for v in p['sources']))
    if not p['strategy_version_id']:
        raise ValueError('EXACT_STRATEGY_VERSION_UNAVAILABLE')
    version = json.loads(json.loads(next(s['payload_json'] for s in p['sources'] if s['source_id'] == 'strategy_version'))['data']['canonical_json'])
    key = p['context_json'] and receipt.context.to_dict()['signal_occurrences']['items'][0]['key']
    return Binding(opportunity_id=p['opportunity_id'], strategy_id=version['strategy_id'],
                   version_id=version['version_id'], spec_hash=version['spec_hash'],
                   instrument=p['instrument_id'], market_type='futures',
                   direction={'BUY': 'LONG', 'SELL': 'SHORT'}[key['action']],
                   horizon=key['signal_timeframe'], as_of_ms=p['as_of_ms'],
                   context_json=p['context_json'], context_required=True, **dimensions)


def candidate(receipt, economics_receipt):
    """Only frozen producer evidence is read; never resample mutable sources."""
    from .economics import from_inputs, to_allocator, verify
    p = json.loads(receipt.payload_json)
    b = from_inputs(json.loads(economics_receipt.inputs_json)).binding
    dims = {k: getattr(b, k) for k in ('units', 'quantity_basis', 'capital_basis', 'horizon_interpretation',
                                     'cost_treatment', 'uncertainty_treatment', 'freshness_semantics')}
    if b != economic_binding(receipt, **dims) or not verify(economics_receipt, from_inputs(json.loads(economics_receipt.inputs_json)), p['as_of_ms']):
        raise ValueError('ECONOMICS_CONTEXT_BINDING_REFUSED')
    live_source = receipt.as_source()
    frozen = from_inputs(json.loads(economics_receipt.inputs_json))
    if live_source not in frozen.context:
        raise ValueError('LIVE_CONTEXT_ECONOMICS_SOURCE_MISSING')
    e, es = to_allocator(economics_receipt)
    unknown = Evidence(Status.UNKNOWN, (live_source.source_id,))
    # Eligibility/cost/capacity/account/Risk authorities are independent. No
    # scalar/status inventory is promoted into permission or sizing evidence.
    return Candidate(b.opportunity_id, b.instrument, b.market_type, b.direction,
                     b.strategy_id, b.version_id, b.spec_hash, b.as_of_ms, b.horizon,
                     unknown, unknown, e, unknown, unknown, unknown, unknown,
                     unknown, unknown, Evidence(Status.UNKNOWN, (live_source.source_id,),
                       canonical({'interaction': p['existing_position_interaction']})),
                     unknown, unknown, None, (), (live_source.source_id, es.source_id),
                     p['context_json']), (live_source, es, *frozen.context)


def verify_candidate(c, receipt, economics_receipt):
    from .economics import from_inputs
    frozen = from_inputs(json.loads(economics_receipt.inputs_json))
    if any(s.source_id.startswith("candidate-bridge:") for s in frozen.context):
        from .candidate_bridge import verify_candidate as verify_bridge
        return verify_bridge(c, receipt, economics_receipt)
    expected, _ = candidate(receipt, economics_receipt)
    return c == expected


def persist(receipt, directory):
    import os
    import tempfile
    p = json.loads(receipt.payload_json)
    replay(receipt, tuple(Source(**s) for s in p['sources']))
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / (receipt.receipt_id + '.json')
    fd, tmp = tempfile.mkstemp(dir=directory, prefix='.context-')
    try:
        with os.fdopen(fd, 'w') as f:
            f.write(receipt.payload_json + '\n')
            f.flush()
            os.fsync(f.fileno())
        try:
            os.link(tmp, target)
        except FileExistsError:
            if target.read_text() != receipt.payload_json + '\n':
                raise ValueError('IMMUTABLE_CONTEXT_COLLISION')
    finally:
        os.unlink(tmp)
    return target


def load(path):
    """Archive replay checks the publication ID, not a freshly invented hash."""
    path = Path(path)
    text = path.read_text()
    payload = json.loads(text)
    if text != canonical(payload) + '\n' or path.stem != digest(payload):
        raise ValueError('PERSISTED_CONTEXT_ID_OR_BYTES_DIFFERS')
    receipt = LiveReceipt(path.stem, canonical(payload))
    return replay(receipt, tuple(Source(**s) for s in payload['sources']))
