"""Versioned expected economics, detached from execution and decision authority.

Model registries are code-owned and deliberately EMPTY in production. Registered
adapters must validate calibration/authority, not merely a payload's claims.
Hashes establish integrity, not authenticity; verify with reread current inputs.
Historical cost receipts require a calibrated forward-scope bridge, never an
implicit conversion of realized costs into a forecast. Cost arithmetic/replay
belongs exclusively to paper_cost_evidence.
"""
from dataclasses import asdict, dataclass
from decimal import Decimal, localcontext
import json

from .allocator import Source, canonical, digest, number, _immutable
from ..core.instrument_registry import is_canonical_instrument_id
from ..engine import paper_cost_evidence as paper_cost

VERSION = 'LUFFY-EXPECTED-NET-ECONOMICS-R1'
GROSS_MODELS = {}
RESERVE_MODELS = {}
COST_SCOPE_MODELS = {}


@dataclass(frozen=True)
class Binding:
    opportunity_id: str
    strategy_id: str
    version_id: str
    spec_hash: str
    instrument: str
    market_type: str
    direction: str
    horizon: str
    as_of_ms: int
    context_json: str
    units: str
    quantity_basis: str
    capital_basis: str
    horizon_interpretation: str
    cost_treatment: str
    uncertainty_treatment: str
    freshness_semantics: str
    context_required: bool = False

    def __post_init__(self):
        _immutable(self)
        if not all(isinstance(v, str) and v for k, v in asdict(self).items() if k not in ('as_of_ms', 'context_required')):
            raise ValueError('EXACT_ECONOMIC_BINDING_REQUIRED')
        if not is_canonical_instrument_id(self.instrument) or self.instrument.split(':')[1] != self.market_type:
            raise ValueError('CANONICAL_INSTRUMENT_REQUIRED')
        if self.direction not in ('LONG', 'SHORT') or type(self.as_of_ms) is not int or self.as_of_ms < 0:
            raise ValueError('ECONOMIC_BINDING_INVALID')
        if type(self.context_required) is not bool:
            raise ValueError('CONTEXT_REQUIREMENT_INVALID')
        if not isinstance(json.loads(self.context_json), dict):
            raise ValueError('CONTEXT_OBJECT_REQUIRED')

    def comparison_key(self):
        return (self.units, self.horizon, self.horizon_interpretation,
                self.quantity_basis, self.capital_basis, self.cost_treatment,
                self.uncertainty_treatment, self.freshness_semantics, self.as_of_ms)


@dataclass(frozen=True)
class Inputs:
    binding: Binding
    gross: Source | None = None
    costs: Source | None = None
    uncertainty: Source | None = None
    # Historical validation/quality/reliability remain descriptive context.
    context: tuple[Source, ...] = ()

    def __post_init__(self):
        object.__setattr__(self, 'context', tuple(self.context))
        _immutable(self)
        sources = [s for s in (self.gross, self.costs, self.uncertainty, *self.context) if s]
        if len({s.source_id for s in sources}) != len(sources):
            raise ValueError('DUPLICATE_SOURCE_ID')
        for s in sources:
            Source(s.source_id, s.sha256, s.payload_json)


@dataclass(frozen=True)
class Receipt:
    receipt_id: str
    inputs_json: str
    result_json: str
    schema: str = VERSION

    def payload(self):
        return dict(schema=self.schema, receipt_id=self.receipt_id,
                    inputs=json.loads(self.inputs_json), result=json.loads(self.result_json))


def from_inputs(body):
    return Inputs(Binding(**body['binding']),
                  *(Source(**body[k]) if body[k] else None for k in ('gross', 'costs', 'uncertainty')),
                  tuple(Source(**s) for s in body['context']))


def from_payload(body):
    return Receipt(body['receipt_id'], canonical(body['inputs']), canonical(body['result']), body['schema'])


def _component(binding, source, reason, value=None, status='UNAVAILABLE', raw=None):
    raw = raw or (json.loads(source.payload_json) if source else {})
    return dict(status=status, value=value, units=binding.units,
                provenance=raw.get('provenance'), source_ids=[source.source_id] if source else [],
                source_hashes=[source.sha256] if source else [], method=raw.get('method'),
                method_version=raw.get('version'), timestamp_ms=raw.get('captured_ms'),
                valid_until_ms=raw.get('valid_until_ms'), freshness_semantics=binding.freshness_semantics,
                limitations=raw.get('limitations', []) + [reason], reason=reason)


def _model(binding, source, registry, missing):
    if source is None:
        return None, 'UNAVAILABLE', missing
    raw = json.loads(source.payload_json)
    validator = registry.get((raw.get('method'), raw.get('version')))
    if validator is None:
        return raw, 'UNAVAILABLE', missing
    if raw.get('binding') != asdict(binding) or raw.get('units') != binding.units:
        return raw, 'INCOMPATIBLE_CONTEXT', 'EXACT_MODEL_CONTEXT_DIFFERS'
    if not raw.get('provenance') or not isinstance(raw.get('limitations'), list):
        return raw, 'UNAVAILABLE', 'MODEL_PROVENANCE_OR_LIMITATIONS_MISSING'
    captured, expires = raw.get('captured_ms'), raw.get('valid_until_ms')
    if type(captured) is not int or type(expires) is not int:
        return raw, 'UNAVAILABLE', 'AUTHORITATIVE_FRESHNESS_MISSING'
    if not captured <= binding.as_of_ms <= expires:
        return raw, 'STALE', 'MODEL_EVIDENCE_OUTSIDE_FRESHNESS'
    if validator(raw) is not True:
        return raw, 'UNAVAILABLE', 'CALIBRATED_MODEL_AUTHORITY_UNPROVEN'
    return raw, 'ESTABLISHED', 'CALIBRATED_REGISTERED_EVIDENCE'


class _FrozenCostReader:
    """Read protocol over frozen paper records; no Journal/schema/DB mutation."""
    def __init__(self, raw):
        self.receipt = raw['paper_receipt_record']
        self.sources = raw['paper_source_records']

    def query(self, sql, params):
        if 'sqlite_master' in sql:
            return [{'present': 1}] if params[0] in (paper_cost.TABLE, paper_cost.SOURCES) else []
        if sql == f'SELECT * FROM {paper_cost.TABLE} WHERE trade_id=?':
            return [self.receipt] if self.receipt['trade_id'] == params[0] else []
        if sql == f'SELECT * FROM {paper_cost.SOURCES} WHERE source_id=?':
            return [s for s in self.sources if s['source_id'] == params[0]]
        raise ValueError('UNREGISTERED_COST_READ')


def _costs(binding, source):
    raw, status, reason = _model(binding, source, COST_SCOPE_MODELS,
                                'CALIBRATED_FORWARD_COST_SCOPE_BRIDGE_MISSING')
    names = {'COMMISSION': 'commission', 'SLIPPAGE': 'slippage', 'FUNDING/BORROW': 'funding'}
    if status != 'ESTABLISHED':
        return {name: _component(binding, source, reason, status=status, raw=raw) for name in names}
    # The scope validator must prove that the existing receipt's units, quantity,
    # baseline, environment and cost dimensions apply to this forward context.
    # No cost calculation is replicated here, including zero-event funding proof.
    body = paper_cost.verify(_FrozenCostReader(raw), raw['trade'], raw['strategy_version'], raw['install'])
    expected_identity = dict(strategy_id=binding.strategy_id, version_id=binding.version_id, spec_hash=binding.spec_hash)
    if (any(body['binding'][k] != v for k, v in expected_identity.items())
            or body['binding']['instrument'] != binding.instrument
            or body['binding']['market_type'] != binding.market_type
            or body['binding']['side'].upper() != binding.direction
            or raw.get('cost_baseline') != binding.cost_treatment):
        raise ValueError('FORWARD_COST_IDENTITY_OR_BASELINE_DIFFERS')
    components = {}
    for name, dimension in names.items():
        item = body['dimensions'][dimension]
        state = item['status']
        value = str(item['amount']) if state == 'ESTABLISHED' else None
        if state not in ('ESTABLISHED', 'NOT_APPLICABLE'):
            state, value = 'UNAVAILABLE', None
        components[name] = _component(binding, source, item['reason'], value, state, raw)
        components[name]['provenance'] = dict(scope_bridge=raw['provenance'], cost=item['provenance'])
        components[name]['cost_receipt_sha256'] = body['receipt_sha256']
        if name == 'FUNDING/BORROW' and (raw.get('borrow', {}).get('status') != 'NOT_APPLICABLE'
                                        or not raw.get('borrow', {}).get('provenance')):
            components[name].update(status='UNAVAILABLE', value=None, reason='BORROW_APPLICABILITY_UNPROVEN')
        if body['currency'] != binding.units:
            components[name].update(status='INCOMPATIBLE_CONTEXT', value=None, reason='COST_UNITS_DIFFER')
    return components


def _context_binding(inputs):
    """Reuse v1; current-context methods cannot substitute descriptive fields."""
    b = inputs.binding
    raw = json.loads(b.context_json)
    required = b.context_required or raw.get('schema_version') == 'opportunity-context.v1'
    for s, registry in ((inputs.gross, GROSS_MODELS), (inputs.costs, COST_SCOPE_MODELS),
                        (inputs.uncertainty, RESERVE_MODELS)):
        if s:
            model = json.loads(s.payload_json)
            validator = registry.get((model.get('method'), model.get('version')))
            required = required or getattr(validator, 'requires_opportunity_context', False)
    if not required:
        return None, None, None
    try:
        from trader.cognition.opportunity_context import OpportunityContext
        from .opportunity_live import receipt_from_source, economic_binding
        ctx = OpportunityContext.from_json(b.context_json)
        d = ctx.to_dict()
        if d['as_of_ms'] != b.as_of_ms or d['instrument']['canonical_id'] != b.instrument:
            raise ValueError('CONTEXT_CUT_OR_INSTRUMENT_DIFFERS')
        sources = [s for s in inputs.context if s.source_id.startswith('live-context:')]
        if len(sources) != 1:
            raise ValueError('EXACT_LIVE_CONTEXT_SOURCE_REQUIRED')
        live = receipt_from_source(sources[0])
        dims = {k: getattr(b, k) for k in ('units', 'quantity_basis', 'capital_basis',
                'horizon_interpretation', 'cost_treatment', 'uncertainty_treatment', 'freshness_semantics')}
        if live.context != ctx or b != economic_binding(live, **dims):
            raise ValueError('EXACT_ECONOMIC_CONTEXT_DIFFERS')
        p = json.loads(live.payload_json)
        expiry = min((json.loads(s['payload_json'])['valid_until_ms'] for s in p['sources']
                      if s['source_id'] in p['required_roles'] and
                      json.loads(s['payload_json'])['valid_until_ms'] is not None), default=None)
        return ctx.context_id, expiry, None
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        return None, None, 'REQUIRED_OPPORTUNITY_CONTEXT_REFUSED:' + str(exc)


def build(inputs: Inputs) -> Receipt:
    # Reconstruct to refuse forged/mutated frozen source objects.
    inputs = from_inputs(asdict(inputs))
    b = inputs.binding
    context_id, context_expiry, context_reason = _context_binding(inputs)
    components = {}
    for name, source, registry, missing in (
        ('EXPECTED_GROSS_VALUE', inputs.gross, GROSS_MODELS, 'EXACT_VERSION_FORWARD_CONDITIONAL_DISTRIBUTION_AND_CALIBRATION_MISSING'),
        ('UNCERTAINTY_RESERVE', inputs.uncertainty, RESERVE_MODELS, 'CALIBRATED_ECONOMIC_RESERVE_METHOD_AND_COVERAGE_MISSING')):
        raw, status, reason = _model(b, source, registry, missing)
        value = raw.get('value') if raw else None
        if status == 'ESTABLISHED':
            period = raw.get('evidence_period_ms')
            valid_period = (isinstance(period, list) and len(period) == 2
                            and all(type(x) is int for x in period)
                            and 0 <= period[0] < period[1] <= raw['captured_ms'] <= b.as_of_ms)
            if (number(value) is None or not valid_period or not raw.get('calibration_evidence')
                    or (name == 'EXPECTED_GROSS_VALUE' and (raw.get('costs_excluded') is not True or raw.get('cost_baseline') != b.cost_treatment))
                    or (name == 'UNCERTAINTY_RESERVE' and number(value) < 0)):
                status, reason = 'UNAVAILABLE', 'CALIBRATED_EXPECTATION_CONTRACT_INCOMPLETE'
        if name == 'UNCERTAINTY_RESERVE' and status == 'ESTABLISHED':
            if (raw.get('gross_source_sha256') != (inputs.gross.sha256 if inputs.gross else None)
                    or raw.get('cost_source_sha256') != (inputs.costs.sha256 if inputs.costs else None)):
                status, reason = 'UNAVAILABLE', 'RESERVE_GROSS_AND_COST_BINDING_MISSING'
        components[name] = _component(b, source, reason, value if status == 'ESTABLISHED' else None, status, raw)
    components.update(_costs(b, inputs.costs))
    if context_reason:
        for item in components.values():
            item.update(status='UNAVAILABLE', value=None, reason=context_reason)
    states = {v['status'] for v in components.values()}
    complete = states.issubset({'ESTABLISHED', 'NOT_APPLICABLE'})
    net = None
    if complete:
        values = {k: number(v['value']) if v['status'] == 'ESTABLISHED' else Decimal(0)
                  for k, v in components.items()}
        if any(v is None for v in values.values()):
            raise ValueError('ESTABLISHED_COMPONENT_NUMBER_INVALID')
        # Enough digits for exact subtraction, independent of process precision.
        ns = list(values.values())
        precision = max(n.adjusted() for n in ns) - min(n.as_tuple().exponent for n in ns) + 10
        with localcontext() as ctx:
            ctx.prec = max(28, precision)
            net = str(values['EXPECTED_GROSS_VALUE'] - sum(
                (values[k] for k in ('COMMISSION', 'SLIPPAGE', 'FUNDING/BORROW', 'UNCERTAINTY_RESERVE')), Decimal(0)))
    status = ('INCOMPATIBLE_CONTEXT' if 'INCOMPATIBLE_CONTEXT' in states else
              'STALE' if 'STALE' in states else 'UNAVAILABLE' if not complete else
              'POSITIVE' if number(net) > 0 else 'NON_POSITIVE')
    expiry = min((v['valid_until_ms'] for v in components.values()
                  if type(v['valid_until_ms']) is int), default=None)
    if context_expiry is not None:
        expiry = min(expiry, context_expiry) if expiry is not None else context_expiry
    components['EXPECTED_NET_VALUE'] = dict(status='ESTABLISHED' if complete else 'UNAVAILABLE',
        value=net, units=b.units, provenance='GROSS_MINUS_COSTS_MINUS_CALIBRATED_RESERVE',
        source_ids=[s.source_id for s in (inputs.gross, inputs.costs, inputs.uncertainty) if s],
        source_hashes=[s.sha256 for s in (inputs.gross, inputs.costs, inputs.uncertainty) if s],
        method=VERSION, method_version='1', timestamp_ms=b.as_of_ms, valid_until_ms=expiry,
        freshness_semantics=b.freshness_semantics,
        limitations=[v['reason'] for v in components.values() if v['status'] not in ('ESTABLISHED', 'NOT_APPLICABLE')])
    result = dict(economic_status=status, components=components, valid_until_ms=expiry,
                  economic_model_status='READY' if complete else 'INSUFFICIENT_EVIDENCE',
                  context_sources=[asdict(s) for s in inputs.context], context_id=context_id,
                  context_reason=context_reason, side_effects='NONE')
    frozen = asdict(inputs)
    return Receipt(digest(dict(schema=VERSION, inputs=frozen, result=result)), canonical(frozen), canonical(result))


def verify(receipt: Receipt, current_inputs: Inputs, now_ms: int) -> bool:
    """Current inputs must be reread authorities, not merely embedded replay."""
    try:
        if receipt != build(current_inputs) or type(now_ms) is not int or now_ms < current_inputs.binding.as_of_ms:
            return False
        result = json.loads(receipt.result_json)
        if result.get('context_reason'):
            return False
        if result['economic_status'] in ('STALE', 'INCOMPATIBLE_CONTEXT'):
            return False
        expiry = result['valid_until_ms']
        return expiry is None or now_ms <= expiry
    except (ValueError, TypeError, KeyError, AttributeError, ArithmeticError):
        return False


def comparison_key(receipt):
    return from_inputs(json.loads(receipt.inputs_json)).binding.comparison_key()


def compare(a: Receipt, b: Receipt, now_ms: int):
    """Completeness/freshness/integrity precede exact comparison."""
    try:
        for r in (a, b):
            if not verify(r, from_inputs(json.loads(r.inputs_json)), now_ms):
                return 'INCOMPARABLE'
            if json.loads(r.result_json)['components']['EXPECTED_NET_VALUE']['status'] != 'ESTABLISHED':
                return 'INCOMPARABLE'
        return 'COMPARABLE' if comparison_key(a) == comparison_key(b) else 'INCOMPARABLE'
    except (ValueError, KeyError, TypeError, AttributeError):
        return 'INCOMPARABLE'


def to_allocator(receipt):
    from .allocator import Economics, Evidence, Status
    b = from_inputs(json.loads(receipt.inputs_json)).binding
    result = json.loads(receipt.result_json)
    net = result['components']['EXPECTED_NET_VALUE']
    source = Source.freeze('economics:' + receipt.receipt_id, receipt.payload())
    e = Economics(Evidence(Status.ESTABLISHED if net['status'] == 'ESTABLISHED' else Status.UNAVAILABLE,
                           (source.source_id,)), net['value'], b.units, b.horizon,
                  b.quantity_basis, b.capital_basis, VERSION, b.cost_treatment,
                  canonical(receipt.payload()))
    return e, source


def persist(receipt, directory):
    """Append-only content-addressed publication, no production storage."""
    import os
    import tempfile
    if receipt != build(from_inputs(json.loads(receipt.inputs_json))):
        raise ValueError('RECEIPT_INTEGRITY_REFUSED')
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / (receipt.receipt_id + '.json')
    data = canonical(receipt.payload()) + '\n'
    fd, temporary = tempfile.mkstemp(dir=directory, prefix='.economics-')
    try:
        with os.fdopen(fd, 'w') as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        try:
            os.link(temporary, target)
        except FileExistsError:
            if target.read_text() != data:
                raise ValueError('IMMUTABLE_RECEIPT_COLLISION')
    finally:
        os.unlink(temporary)
    return target
