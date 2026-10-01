"""Deterministic exposure context; no statistical classification or trade authority.

All source hashes prove integrity only. Current use requires trusted rereads.
Entry basis notionals are not current marked exposure. Distinct lineage does
not prove statistical independence. Missing mark/stop/account authority stays
unavailable; no proxy or default threshold is substituted.
"""
from dataclasses import asdict, dataclass
from decimal import Decimal, localcontext
from enum import Enum
from itertools import combinations
import json

from .allocator import Source, Status, canonical, digest, number
from trader.core.instrument_registry import is_canonical_instrument_id
from trader.engine.evidence_capture import verify_snapshot
from trader.engine.protection_snapshot import STALE_AFTER_S

SCHEMA = 'portfolio-common-factor-context.v1'


class Relationship(str, Enum):
    EXACT_SHARED_EXPOSURE = 'EXACT_SHARED_EXPOSURE'
    MEASURED_RELATIONSHIP = 'MEASURED_RELATIONSHIP'
    NO_ESTABLISHED_RELATIONSHIP = 'NO_ESTABLISHED_RELATIONSHIP'
    UNKNOWN = 'UNKNOWN'


@dataclass(frozen=True)
class Receipt:
    receipt_id: str
    inputs_json: str
    result_json: str

    def payload(self):
        return dict(schema=SCHEMA, receipt_id=self.receipt_id,
                    inputs=json.loads(self.inputs_json), result=json.loads(self.result_json))


def _checked(source):
    Source(**asdict(source))
    return json.loads(source.payload_json)


def snapshot(source, now_ms):
    """Project the existing verified venue contract, checking its projection too."""
    raw = _checked(source)
    if verify_snapshot(raw) is None or raw['completeness'] != 'COMPLETE':
        raise ValueError('VENUE_PORTFOLIO_TRUTH_UNAVAILABLE')
    obs = raw['observation']
    # Replay the established producer's structural identity rules. A rehashed
    # malformed observation cannot establish a complete one-way venue book.
    from trader.observability.portfolio_observation import observe_positions
    from trader.core.types import MarketType
    rebuilt = observe_positions([
        dict(info={'symbol':iid.split(':')[-1]},
             symbol=iid.split(':')[-1][:-4] + '/USDT:USDT', contracts=qty, side=side)
        for iid,present,side,qty in obs['positions']],
        exchange_id='binanceusdm', market_type=MarketType.FUTURES,
        environment=obs['environment'], source_ref=obs['source_ref'],
        request_start_ms=obs['request_start_ms'], response_received_ms=obs['response_received_ms'])
    if rebuilt.observation_id != obs['observation_id']:
        raise ValueError('VENUE_OBSERVATION_REPLAY_DIFFERS')
    if (obs['complete'] is not True or raw['observed_at_ms'] != obs['as_of_ms']
            or raw['market_type'] != obs['market_type'] or raw['environment'] != obs['environment']
            or raw['received_at_ms'] != obs['response_received_ms']):
        raise ValueError('VENUE_SNAPSHOT_PROJECTION_DIFFERS')
    if not raw['observed_at_ms'] <= now_ms <= raw['observed_at_ms'] + int(STALE_AFTER_S * 1000):
        raise ValueError('VENUE_PORTFOLIO_TRUTH_STALE')
    expected = sorted((iid, side, str(qty)) for iid, present, side, qty in obs['positions'] if present)
    actual = sorted((p['instrument_id'], p['side'], str(p['quantity'])) for p in raw['positions'])
    if expected != actual or len({p[0] for p in actual}) != len(actual):
        raise ValueError('VENUE_SNAPSHOT_PROJECTION_DIFFERS')
    rows = []
    with localcontext() as ctx:
        ctx.prec = 100
        for p in sorted(raw['positions'], key=lambda p: p['instrument_id']):
            iid, qty = p['instrument_id'], number(str(p['quantity']))
            price = number(p.get('entry_price_text'))
            if (not is_canonical_instrument_id(iid) or iid.split(':')[1] != raw['market_type']
                    or p['side'] not in ('long', 'short') or qty is None or qty <= 0):
                raise ValueError('VENUE_POSITION_INVALID')
            # Verified producer supports linear Binance USDT contracts only.
            entry_notional = str(qty * price) if price is not None and price > 0 else None
            rows.append(dict(instrument=iid, market_type=raw['market_type'], direction=p['side'].upper(),
                quantity=str(qty), notional=None, notional_status='CURRENT_MARK_UNAVAILABLE',
                entry_basis_notional=entry_notional, notional_unit='USDT',
                strategy_id=None, version_id=None, attribution='UNKNOWN',
                observed_at_ms=raw['observed_at_ms'], provenance=source.source_id,
                freshness='CURRENT', completeness='COMPLETE'))
        complete_entry = all(p['entry_basis_notional'] is not None for p in rows)
        totals = {direction: str(sum((Decimal(p['entry_basis_notional']) for p in rows
                  if p['direction'] == direction), Decimal(0))) if complete_entry else None
                  for direction in ('LONG', 'SHORT')}
        gross = str(sum((Decimal(p['entry_basis_notional']) for p in rows), Decimal(0))) if complete_entry else None
    return dict(snapshot_id=raw['snapshot_id'], observed_at_ms=raw['observed_at_ms'],
                environment=raw['environment'], positions=rows, freshness='CURRENT', completeness='COMPLETE',
                current_gross_notional=None, entry_basis_gross_notional=gross,
                entry_basis_directional_notional=totals, current_risk_heat=None, margin_usage=None,
                limitations=['NO_CURRENT_MARK', 'NO_VENUE_BOUND_STOP_RISK', 'NO_POSITION_STRATEGY_ATTRIBUTION'])


def world_measurements(source, now_ms):
    """Replay the existing WorldModel, preserving exact raw measurement context."""
    from trader.world.replay import WorldModelRecord
    from trader.world.observation import Quality
    from trader.world.model import ScopeLevel
    raw = _checked(source)
    expiry = raw['valid_until_ms']
    record = WorldModelRecord.from_json(raw['record_json'])
    model = record.reconstruct()
    if type(expiry) is not int or not model.as_of_ms <= now_ms <= expiry:
        raise ValueError('RELATIONSHIP_EVIDENCE_STALE_OR_UNBOUNDED')
    rows = []
    for r in model.relationships.relationships if model.relationships else ():
        # Scope edges with non-instrument endpoints remain context, never
        # converted to numerical exposure on an invented instrument mapping.
        item = r.to_dict()
        if r.quality is Quality.STALE:
            raise ValueError('RELATIONSHIP_EVIDENCE_STALE')
        valid = r.quality is Quality.VALID
        for o in r.evidence:
            if o.quality is Quality.STALE:
                raise ValueError('RELATIONSHIP_EVIDENCE_STALE')
            if o.quality is not Quality.VALID or o.available_at_ms is None:
                valid = False
            if o.max_age_ms is None:
                valid = False
            elif now_ms > o.timestamp_ms + o.max_age_ms:
                raise ValueError('RELATIONSHIP_EVIDENCE_STALE')
        windows = [o.to_dict() for o in r.evidence]
        rows.append(dict(evidence_id=r.relationship_id, source_id=source.source_id,
            instruments=[r.coordinate.source.identifier, r.coordinate.target.identifier]
            if (r.coordinate.source.level is ScopeLevel.INSTRUMENT and r.coordinate.target.level is ScopeLevel.INSTRUMENT
                and is_canonical_instrument_id(r.coordinate.source.identifier)
                and is_canonical_instrument_id(r.coordinate.target.identifier)) else None,
            scopes=item['coordinate'], horizon=r.coordinate.horizon.value, as_of_ms=r.as_of_ms,
            value=item['value'], method=r.source, method_source_ref=r.source_ref,
            window_evidence=windows, window='AS_RECORDED_IN_OBSERVATIONS',
            valid_until_ms=expiry, freshness='CURRENT' if valid else 'UNKNOWN',
            authority='CONTEXT_ONLY', record=item))
    return sorted(rows, key=lambda r: r['evidence_id'])


def attention_measurements(source, now_ms, book):
    """Retain the existing leave-one-out basket observation, never a pairwise rho."""
    from trader.observability.investigation import adapt
    raw = _checked(source)
    scan = raw['scan']
    if type(raw['valid_until_ms']) is not int or not scan['as_of_ms'] <= now_ms <= raw['valid_until_ms']:
        raise ValueError('ATTENTION_RELATIONSHIP_EVIDENCE_STALE')
    adapted = adapt((scan, raw['bars'], []), now_ms)
    own_instruments = {p['instrument'].split(':')[-1][:-4] + '/USDT': p['instrument']
                       for p in book['positions']}
    rows = []
    for o in adapted.scan['observations']:
        if o['kind'] != 'correlation_change':
            continue
        instrument = own_instruments.get(o['symbol'])
        if instrument is None:
            continue
        detail = o['detail']
        rows.append(dict(evidence_id=o['obs_id'], source_id=source.source_id,
            instruments=[instrument], reference_symbols=detail.get('reference_peers', []),
            reference_membership_id=detail.get('reference_membership_id'),
            relationship_scope='INSTRUMENT_TO_LEAVE_ONE_OUT_PEER_BASKET',
            horizon=adapted.scan['timeframe'], as_of_ms=scan['as_of_ms'],
            value={k:detail.get(k) for k in ('r_baseline','r_recent','signed_fisher_scaled_change')},
            method=detail['method'], window_evidence=detail,
            window=detail['windows'], valid_until_ms=raw['valid_until_ms'],
            freshness='CURRENT' if o['status'] == 'ok' else 'UNKNOWN',
            authority='CONTEXT_ONLY', record=o))
    return sorted(rows, key=lambda r:r['evidence_id'])


def _lineages(sources, now_ms):
    from .candidate_bridge import replay_lineage
    by_version = {}
    for source in sources:
        result = replay_lineage(source, now_ms)
        if result['version_id'] in by_version:
            raise ValueError('DUPLICATE_LINEAGE_AUTHORITY')
        by_version[result['version_id']] = result
    return by_version


def evidence_groups(candidates, lineages):
    """One representative per proven root; unknown provenance never votes."""
    groups, unknown = {}, []
    for c in sorted(candidates, key=lambda c: c.identity):
        proof = lineages.get(c.version_id)
        if proof is None:
            unknown.append(list(c.identity))
            continue
        if (proof['strategy_id'], proof['spec_hash']) != (c.strategy_id, c.spec_hash):
            raise ValueError('LINEAGE_CANDIDATE_IDENTITY_DIFFERS')
        groups.setdefault(proof['root_version_id'], []).append(list(c.identity))
    return dict(lineage_groups=[dict(root_version_id=k, members=v) for k, v in sorted(groups.items())],
        evidence_group_count=len(groups), shared_lineage_count=sum(len(v)-1 for v in groups.values()),
        independent_evidence_count=None, independence_status='NOT_ESTABLISHED',
        unknown_lineage=unknown, representatives=[v[0] for _, v in sorted(groups.items())],
        confidence_aggregation='NONE')


def build(inputs, venue_source, policy_source, lineage_sources=(), relationship_sources=()):
    now = inputs.as_of_ms
    if type(now) is not int or now < 0:
        raise ValueError('AS_OF_INVALID')
    book = snapshot(venue_source, now)
    p = inputs.portfolio
    projection = sorted((v.instrument, v.direction, Decimal(v.quantity)) for v in p.positions)
    projected = sorted((v['instrument'], v['direction'], Decimal(v['quantity'])) for v in book['positions'])
    if (p.snapshot_id != book['snapshot_id'] or p.as_of_ms != book['observed_at_ms']
            or projection != projected or p.evidence.status != Status.ESTABLISHED
            or p.valid_until_ms is None or not p.as_of_ms <= now <= p.valid_until_ms):
        raise ValueError('ALLOCATOR_PORTFOLIO_PROJECTION_DIFFERS')
    policy = _checked(policy_source)
    limits = policy.get('risk', policy)
    cap = limits.get('max_open_positions')
    if cap is not None and (type(cap) is not int or cap < 1):
        raise ValueError('OWNER_POLICY_INVALID')
    count = len(book['positions'])
    checks = [dict(metric='open_position_count', value=count, limit=cap,
                   policy_source_id=policy_source.source_id,
                   status='UNAVAILABLE' if cap is None else 'EXCEEDS_EXISTING_LIMIT' if count > cap else 'WITHIN_EXISTING_LIMITS')]
    for metric, key in [('risk_heat_pct', 'portfolio_heat_cap_pct'), ('per_symbol_risk_pct', 'per_symbol_risk_cap_pct'),
                        ('position_margin_pct', 'max_position_margin_pct'), ('total_margin_pct', 'max_total_margin_pct')]:
        if key not in limits:
            continue
        limit = limits.get(key)
        if limit is not None and (isinstance(limit, bool) or number(str(limit)) is None or number(str(limit)) < 0):
            raise ValueError('OWNER_POLICY_INVALID')
        checks.append(dict(metric=metric, value=None, limit=limit, policy_source_id=policy_source.source_id,
                           status='UNAVAILABLE', reason='VENUE_BOUND_METRIC_AUTHORITY_UNAVAILABLE'))
    concentration = ('EXCEEDS_EXISTING_LIMIT' if any(c['status'] == 'EXCEEDS_EXISTING_LIMIT' for c in checks)
                     else 'UNAVAILABLE' if any(c['status'] == 'UNAVAILABLE' for c in checks) else 'WITHIN_EXISTING_LIMITS')
    lineage_sources = tuple(sorted(lineage_sources, key=lambda s: s.source_id))
    relationship_sources = tuple(sorted(relationship_sources, key=lambda s: s.source_id))
    lineages = _lineages(lineage_sources, now)
    measurements = [r for s in relationship_sources for r in (
        attention_measurements(s, now, book) if _checked(s).get('schema') == 'attention-relationship-input.v1'
        else world_measurements(s, now))]
    if len({r['evidence_id'] for r in measurements}) != len(measurements):
        raise ValueError('DUPLICATE_RELATIONSHIP_EVIDENCE')
    groups = evidence_groups(inputs.candidates, lineages)
    exposures = [dict(key='candidate:' + canonical(c.identity), instrument=c.instrument, direction=c.direction,
                     version_id=c.version_id, origin='CANDIDATE') for c in sorted(inputs.candidates, key=lambda c: c.identity)]
    exposures += [dict(key='holding:' + p['instrument'], instrument=p['instrument'], direction=p['direction'],
                      version_id=None, origin='VENUE_POSITION') for p in book['positions']]
    pairs = []
    for a, b in combinations(exposures, 2):
        same = a['instrument'] == b['instrument'] and a['direction'] == b['direction']
        conflict = a['instrument'] == b['instrument'] and a['direction'] != b['direction']
        la, lb = lineages.get(a['version_id']), lineages.get(b['version_id'])
        shared = la is not None and lb is not None and la['root_version_id'] == lb['root_version_id']
        distinct = la is not None and lb is not None and not shared
        measured = [r['evidence_id'] for r in measurements if r['freshness'] == 'CURRENT' and
                    r['instruments'] and set(r['instruments']) == {a['instrument'], b['instrument']}]
        state = (Relationship.EXACT_SHARED_EXPOSURE if same or shared else Relationship.MEASURED_RELATIONSHIP if measured
                 else Relationship.NO_ESTABLISHED_RELATIONSHIP if distinct else Relationship.UNKNOWN)
        pairs.append(dict(left=a['key'], right=b['key'], redundancy='EXACT_REDUNDANT' if same else 'SHARED_LINEAGE' if shared else 'DISTINCT',
             lineage_status='SHARED_LINEAGE' if shared else 'DISTINCT' if distinct else 'UNKNOWN',
             relationship=state.value, conflict=conflict, measurement_ids=measured,
             effect='ONE_EXPRESSION_NO_STACKING' if same else 'CONFLICT' if conflict else 'DEDUPLICATE_EVIDENCE' if shared else 'CONTEXT_ONLY'))
    result = dict(portfolio=book, pairs=pairs, measured_relationships=measurements,
        duplicate_confidence=groups, concentration=dict(status=concentration, checks=checks),
        constraints=dict(new_position_count_permitted=cap is not None and count < cap,
                         maximum_open_positions=cap, existing_positions='KEEP_EXISTING',
                         missing_concentration_authority=concentration == 'UNAVAILABLE'),
        factor_model='NOT_JUSTIFIED', factor_model_reason='NO_REGISTERED_CALIBRATED_FACTOR_AUTHORITY',
        numeric_adjustments='NONE', risk_final_authority=True, side_effects='NONE')
    frozen = dict(as_of_ms=now, portfolio=asdict(inputs.portfolio),
        candidates=[asdict(c) for c in sorted(inputs.candidates, key=lambda c: c.identity)],
        venue_source=asdict(venue_source), policy_source=asdict(policy_source),
        lineage_sources=[asdict(s) for s in lineage_sources], relationship_sources=[asdict(s) for s in relationship_sources])
    return Receipt(digest(dict(schema=SCHEMA, inputs=frozen, result=result)), canonical(frozen), canonical(result))


def verify(receipt, current_inputs, venue_source, policy_source, lineage_sources=(), relationship_sources=()):
    try:
        return receipt == build(current_inputs, venue_source, policy_source, lineage_sources, relationship_sources)
    except (ValueError, TypeError, KeyError, AttributeError):
        return False


def attach(inputs, venue_source, policy_source, lineage_sources=(), relationship_sources=()):
    from dataclasses import replace
    receipt = build(inputs, venue_source, policy_source, lineage_sources, relationship_sources)
    source = Source.freeze('portfolio-common-factor:' + receipt.receipt_id, receipt.payload())
    sources = {s.source_id: s for s in inputs.sources}
    for s in (venue_source, policy_source, *lineage_sources, *relationship_sources, source):
        if s.source_id in sources and sources[s.source_id] != s:
            raise ValueError('SOURCE_ID_COLLISION')
        sources[s.source_id] = s
    return replace(inputs, sources=tuple(sources.values()), exposure_source_id=source.source_id), receipt


def from_allocator(inputs):
    source = next(s for s in inputs.sources if s.source_id == inputs.exposure_source_id)
    body = _checked(source)
    frozen = body['inputs']
    current_sources = {s.source_id: s for s in inputs.sources}
    def reread(raw):
        s = Source(**raw)
        if current_sources.get(s.source_id) != s:
            raise ValueError('CURRENT_EXPOSURE_SOURCE_DIFFERS')
        return s
    receipt = build(inputs, reread(frozen['venue_source']), reread(frozen['policy_source']),
                    tuple(reread(s) for s in frozen['lineage_sources']),
                    tuple(reread(s) for s in frozen['relationship_sources']))
    if source.source_id != 'portfolio-common-factor:' + receipt.receipt_id or receipt.payload() != body:
        raise ValueError('EXPOSURE_AUTHORITY_REPLAY_REFUSED')
    return json.loads(receipt.result_json)
