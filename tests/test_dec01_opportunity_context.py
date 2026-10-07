"""DEC-01: one coherent, immutable, decision-grade Opportunity Context.

Synthetic fixtures only (no provider/venue/Kernel call). Every clause is
exercised through `opportunity_live.produce` (the normal producer), the
normal capture/checkpoint path, and the frozen-receipt consumers.
"""
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timezone
import json
import re
from pathlib import Path

import pytest

from trader.agents.measurement import Measurement
from trader.engine.trade_provenance import spec_version
from trader.cognition import decision_analysts as DA, opportunity_context as oc
from trader.portfolio import allocator as A, economics as E, opportunity_live as L
from trader.portfolio.allocator import Evidence, Inputs, Portfolio, Source, Status, allocate
from trader.strategy import compile as C, factory_handoff as F
from trader.strategy.signal_occurrence import spec_fingerprint
from trader.strategy.spec import StrategySpec
from trader.world.observation import Quality
from tests.test_compile import _spec
from tests.test_market_investigation import prefix  # noqa: F401
from tests.test_opportunity_context import evidence  # noqa: F401
from tests.test_opportunity_live_integration import snapshot, world as world_record  # noqa: F401
from tests.test_stage6_normal_sources import compiled_params
from scripts.opportunity_context_shadow import DIMENSIONS

STEP = 5000          # analyst cut A = market cut M + STEP
LATER = 20000        # decision cut D = A + LATER


def iso(ms):
    return datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat()


def packet(name, strength, symbol, cut_ms, *, required_input=True):
    """A real `analyst.measurement.v1` packet; `strength=None` is an unavailable measurement."""
    ok = strength is not None
    inputs = [dict(name='ohlcv', source='snapshot.dfs.4h', source_ref=('a' * 64) if ok else None,
                   quality='UNKNOWN' if ok else 'MISSING', required=required_input,
                   reason='fixture' if ok else 'frame absent')]
    return Measurement(name, symbol, iso(cut_ms), tuple(inputs), '4h',
                       Quality.SUSPECT if ok else Quality.MISSING, strength,
                       dict(confidence=.6 if ok else None, basis='heuristic reliability; not calibrated probability'),
                       ('fixture limitation',), 'fixture rationale').to_dict()


class Ctx:
    """Decision-grade producer inputs built around the shared Attention fixture."""

    def __init__(self, e, *, required=(), packets=None):
        self.e = e
        self.symbol = e['symbol']
        self.M = e['scan']['as_of_ms']
        self.A = self.M + STEP
        self.D = self.A + LATER
        spec, sh = F._frozen_spec(_spec(timeframe='4h', direction='both', entry_short='close < ema(20)'))
        self.spec = spec
        self.version = F._version_record(spec['id'], spec, sh, None, {'kind': 'TEST-ONLY', 'id': 'dec01'}, [])
        self.row = F._version_row(self.version, self.M - 1000)
        self.iid = 'binance_usdm:futures:' + oc.venue_key(self.symbol)
        self.decision_id = 'decision-1'
        self.cycle_id = 'cycle-1'
        self.required = tuple(required)
        names = ('structure', 'momentum', 'flow')
        self.packets = packets if packets is not None else [packet(n, .5, self.symbol, self.A) for n in names]

    def signal(self, **over):
        params = dict(spec_id=self.spec['id'],
                      spec_fingerprint=spec_fingerprint(StrategySpec.from_dict(self.spec)),
                      signal_timeframe='4h', signal_bar_close_ms=self.M,
                      **compiled_params(self.spec))
        params.update(over)
        return {'symbol': self.symbol, 'action': 'BUY', 'params': params}

    def bundle(self, **over):
        b = DA.bundle(decision_id=self.decision_id, cycle_id=self.cycle_id, symbol=self.symbol,
                      cut_ts=iso(self.A), roster=DA.roster({'decision': {'context': {
                          'required_analysts': list(self.required)}}}), packets=self.packets)
        b.update(over)
        return b

    def book(self, cut=None):
        return snapshot('long', self.D if cut is None else cut)

    def sources(self, **over):
        book = over.pop('book', None) or self.book()
        s = {'attention_scan': L.source('attention_scan', self.e['scan'], self.e['scan']['persisted_at_ms'],
                                        self.M + 300000),
             'signals': L.source('signals', [over.pop('signal', None) or self.signal()], self.D, self.D + 60000),
             'strategy_version': L.source('strategy_version', self.row, self.row['recorded_at_ms'], self.D + 60000),
             'portfolio': L.source('portfolio', book, book['received_at_ms'], book['observed_at_ms'] + 120000),
             'analysts': L.source('analysts', over.pop('bundle', None) or self.bundle(), self.D)}
        for role in over.pop('drop', ()):
            s.pop(role)
        s.update(over)
        return tuple(s.values())

    def kwargs(self, **over):
        sources = over.pop('sources', None) or self.sources()
        return {**dict(as_of_ms=self.D, symbol=self.symbol, instrument_id=self.iid, cycle_id=self.cycle_id,
                       candidate_id=self.decision_id + ':' + self.version['version_id'], sources=sources,
                       required_roles=(), decision_grade=True), **over}

    def produce(self, **over):
        return L.produce(**self.kwargs(**over))


@pytest.fixture
def ctx(evidence):  # noqa: F811
    return Ctx(evidence)


def refused(code, exc=ValueError):
    return pytest.raises(exc, match=re.escape(code))


# ── 1/2: canonical identity, exact spec + compiled StrategyVersion ───────────

def test_frozen_context_binds_identity_version_compiled_cuts_and_authority(ctx):
    r = ctx.produce()
    p = json.loads(r.payload_json)
    di = p['decision_inputs']
    assert p['decision_grade'] is True and p['authority'] == 'NONE'
    assert p['strategy_version_id'] == ctx.version['version_id']
    assert p['candidate_id'] == ctx.decision_id + ':' + ctx.version['version_id']
    assert set(p['required_roles']) >= L.DECISION_REQUIRED          # forced, not caller-optional
    exp = C.compile_identity(spec_version(StrategySpec.from_dict(ctx.spec))['spec_sha256'],
                             C.COMPILER_VERSION, C.FEATURE_VERSION, C.feature_contract_sha256())
    assert di['compiled']['compile_identity'] == exp
    assert di['cuts'] == dict(decision_cut_ms=ctx.D, market_cut_ms=ctx.M, world_cut_ms=None,
                              analyst_cut_ms=ctx.A, strategy_eval_cut_ms=ctx.M,
                              strategy_version_recorded_ms=ctx.row['recorded_at_ms'],
                              book_observed_ms=ctx.book()['observed_at_ms'])
    assert di['analysts']['decision_id'] == ctx.decision_id
    assert r.context.to_dict()['authority'] == 'NONE'
    assert L.replay(r, ctx.sources(), ctx.D) == r


@pytest.mark.parametrize('change,code', [
    ('other_version_fingerprint', 'EXACT_VERSION_SIGNAL_LINEAGE_UNPROVEN'),
    ('stale_compile_identity', 'COMPILED_IDENTITY_STALE_OR_MISMATCHED'),
    ('stale_feature_version', 'COMPILED_IDENTITY_STALE_OR_MISMATCHED'),
    ('stale_compiler_version', 'COMPILED_IDENTITY_STALE_OR_MISMATCHED'),
    ('other_spec_sha', 'COMPILED_IDENTITY_STALE_OR_MISMATCHED'),
    ('identity_absent', 'COMPILED_IDENTITY_UNPROVEN'),
    ('other_decision', 'DECISION_IDENTITY_MISMATCH'),
    ('other_cycle', 'DECISION_IDENTITY_MISMATCH'),
    ('other_version_id', 'DECISION_IDENTITY_MISMATCH'),
])
def test_wrong_version_compiled_identity_or_decision_refuses(ctx, change, code):
    sig, kwargs = ctx.signal(), {}
    if change == 'other_version_fingerprint':
        sig['params']['spec_fingerprint'] = 'e' * 64
    elif change == 'stale_compile_identity':
        sig['params']['compile_identity'] = '0' * 64
    elif change == 'stale_feature_version':
        sig['params']['feature_version'] = 'strategy-features.v0'
    elif change == 'stale_compiler_version':
        sig['params']['compiler_version'] = 'strategy-compiler.v0'
    elif change == 'other_spec_sha':
        sig['params']['spec_sha256'] = '1' * 64
    elif change == 'identity_absent':
        del sig['params']['compile_identity']
    elif change == 'other_decision':
        kwargs['candidate_id'] = 'decision-2:' + ctx.version['version_id']
    elif change == 'other_cycle':
        kwargs['cycle_id'] = 'cycle-2'
    elif change == 'other_version_id':
        kwargs['candidate_id'] = ctx.decision_id + ':someotherversion'
    with refused(code):
        ctx.produce(sources=ctx.sources(signal=sig), **kwargs)


def test_unvalidated_or_tampered_version_row_refuses(ctx):
    row = dict(ctx.row, spec_hash='f' * 64)
    with pytest.raises(ValueError):
        ctx.produce(sources=ctx.sources(strategy_version=L.source('strategy_version', row, ctx.row['recorded_at_ms'])))


def test_version_outside_declared_universe_or_direction_refuses(ctx):
    spec, sh = F._frozen_spec(_spec(timeframe='4h', direction='short', entry_short='close < ema(20)'))
    ctx.spec = spec
    ctx.version = F._version_record(spec['id'], spec, sh, None, {'kind': 'TEST-ONLY', 'id': 'dec01'}, [])
    ctx.row = F._version_row(ctx.version, ctx.M - 1000)
    with refused('VERSION_SIGNAL_HORIZON_DIRECTION_OR_MARKET_DIFFERS'):
        ctx.produce()                      # a short-only version cannot own a BUY occurrence


# ── 3: required vs optional ────────────────────────────────────────────────

NAMES = ('structure', 'momentum', 'flow', 'value')


@pytest.mark.parametrize('required,present,unavailable', [
    ((), (), ()),                                           # nothing required, nothing recorded
    ((), ('structure',), ()),
    (('structure',), ('structure',), ()),
    (('structure', 'flow'), ('structure', 'flow', 'value'), ()),
    ((), ('structure', 'momentum', 'flow'), ('flow',)),     # optional present-but-unusable
    (('structure',), ('structure', 'flow'), ('flow',)),
])
def test_valid_combinations_keep_optional_absence_explicit(ctx, required, present, unavailable):
    ctx.required = required
    ctx.packets = [packet(n, None if n in unavailable else .4, ctx.symbol, ctx.A) for n in present]
    a = json.loads(ctx.produce().payload_json)['decision_inputs']['analysts']
    absent = {x['analyst'] for x in a['absence']['absent']}
    unusable = {x['analyst'] for x in a['absence']['unavailable']}
    assert absent == set(DA.ENABLED) - set(present)
    assert unusable == set(unavailable)
    assert all(x['role'] == 'optional' for x in a['absence']['absent'] + a['absence']['unavailable']) \
        or set(required) & (absent | unusable) == set()
    used = {x['analyst'] for k in ('supporting', 'opposing', 'neutral') for x in a['evidence'][k]}
    assert used == set(present) - set(unavailable)          # absence never fabricated into evidence


@pytest.mark.parametrize('required,present,unavailable,blocked', [
    (('structure',), (), (), 'structure'),
    (('structure',), ('momentum',), (), 'structure'),
    (('structure', 'flow'), ('structure',), (), 'flow'),
    (('structure',), ('structure',), ('structure',), 'structure'),    # present but no measurement
    (('structure', 'flow'), ('momentum',), (), 'flow,structure'),
])
def test_missing_required_analyst_blocks(ctx, required, present, unavailable, blocked):
    ctx.required = required
    ctx.packets = [packet(n, None if n in unavailable else .4, ctx.symbol, ctx.A) for n in present]
    with refused('REQUIRED_ANALYST_UNAVAILABLE:' + blocked, DA.ContextBlocked):
        ctx.produce()


def test_optional_analyst_cannot_become_required_by_incidental_paths(ctx):
    # an optional analyst whose own required INPUT is missing is an unavailable optional measurement
    ctx.packets = [packet('structure', .4, ctx.symbol, ctx.A),
                   packet('value', None, ctx.symbol, ctx.A, required_input=True)]
    p = json.loads(ctx.produce().payload_json)
    assert 'value' not in p['required_roles'] and p['decision_inputs']['analysts']['roster']['required'] == []
    # the roster itself is the only way to require one, and it must name an enabled analyst
    with pytest.raises(DA.ContextRefused, match='ANALYST_ROSTER_INVALID'):
        DA.roster({'decision': {'context': {'required_analysts': ['made_up']}}})
    with pytest.raises(DA.ContextRefused, match='ANALYST_ROSTER_INVALID'):
        DA.roster({'decision': {'context': {'required_analysts': ['flow', 'flow']}}})
    assert DA.roster({}) == {'required': [], 'optional': sorted(DA.ENABLED)}


def test_enabled_roster_matches_kernel_instantiation():
    text = Path('trader/kernel.py').read_text()
    order = re.search(r'order = \(([^)]*)\)', text).group(1)
    assert tuple(re.findall(r'"(\w+)"', order)) == DA.ENABLED


@pytest.mark.parametrize('role', ['analysts', 'portfolio', 'signals', 'strategy_version', 'attention_scan'])
def test_missing_required_input_role_blocks_even_if_caller_omits_it(ctx, role):
    # decision_grade forces the required set; the caller declaring none cannot weaken it
    with refused('REQUIRED_CONTEXT_EVIDENCE_' + role.upper() + '_UNAVAILABLE', DA.ContextBlocked):
        ctx.produce(sources=ctx.sources(drop=(role,)))


def test_stale_required_input_blocks_but_stale_optional_does_not(ctx, evidence):  # noqa: F811
    old = ctx.book(cut=ctx.D - 400000)
    with refused('REQUIRED_CONTEXT_EVIDENCE_PORTFOLIO_UNAVAILABLE', DA.ContextBlocked):
        ctx.produce(sources=ctx.sources(book=old))
    # a stale OPTIONAL world model is excluded with an explicit status, not a block
    stale = L.source('world_model', world_record(ctx.M, 'S0/USDT'), ctx.M, ctx.M + 1)
    r = ctx.produce(sources=(*ctx.sources(), stale))
    assert json.loads(r.payload_json)['source_status']['world_model']['reason'] == 'STALE_SOURCE'


# ── 4: clock / cut coherence ───────────────────────────────────────────────

def test_analyst_from_future_or_other_cut_refuses(ctx):
    base = [packet(n, .5, ctx.symbol, ctx.A) for n in ('structure', 'momentum')]
    for bad_cut, code in ((ctx.D + 1, 'ANALYST_MIXED_CUTS:flow'), (ctx.A - 1, 'ANALYST_MIXED_CUTS:flow'),
                          (ctx.A + 1, 'ANALYST_MIXED_CUTS:flow')):
        ctx.packets = base + [packet('flow', .5, ctx.symbol, bad_cut)]
        with refused(code, DA.ContextRefused):
            ctx.produce()


def test_analyst_bundle_cut_after_decision_cut_or_availability_refuses(ctx):
    late = ctx.bundle(cut_ms=ctx.D + 1, cut_ts=iso(ctx.D + 1))
    with pytest.raises(ValueError):
        ctx.produce(sources=ctx.sources(bundle=late))
    # availability cannot pre-date the cut the bundle describes
    early = L.source('analysts', ctx.bundle(), ctx.A - 1)
    with refused('SOURCE_EVENT_AFTER_AVAILABILITY'):
        ctx.produce(sources=ctx.sources(analysts=early))
    # the bundle's declared cut must equal its own timestamp
    with refused('ANALYST_CUT_INVALID', DA.ContextRefused):
        ctx.produce(sources=ctx.sources(bundle=ctx.bundle(cut_ms=ctx.A + 1)))


@pytest.mark.parametrize('what', ['market_after_analysts', 'signal_after_analysts', 'version_after_analysts'])
def test_evidence_newer_than_the_analyst_cut_refuses(ctx, what):
    if what == 'market_after_analysts':
        ctx.A = ctx.M - 1                              # analysts evaluated before the scan they claim
        ctx.packets = [packet('structure', .5, ctx.symbol, ctx.A)]
        match = 'MIXED_CUTS_EVIDENCE_AFTER_ANALYST_CUT:market_cut_ms'
        kwargs = {}
    elif what == 'signal_after_analysts':
        match, kwargs = 'MIXED_CUTS_EVIDENCE_AFTER_ANALYST_CUT:strategy_eval_cut_ms', dict(
            sources=ctx.sources(signal=ctx.signal(signal_bar_close_ms=ctx.A + 1)))
    else:
        ctx.row = F._version_row(ctx.version, ctx.A + 1)
        match, kwargs = 'MIXED_CUTS_EVIDENCE_AFTER_ANALYST_CUT:strategy_version_recorded_ms', {}
    with refused(match, DA.ContextRefused):
        ctx.produce(**kwargs)


def test_world_model_from_another_or_future_cut_refuses(ctx):
    for cut in (ctx.M - 1, ctx.M + 1, ctx.D + 1):
        src = L.source('world_model', world_record(cut, ctx.symbol), ctx.D)
        with refused('WORLD_MODEL_CUT_MISMATCH'):
            ctx.produce(sources=(*ctx.sources(), src))
    ok = L.source('world_model', world_record(ctx.M, ctx.symbol), ctx.D)
    p = json.loads(ctx.produce(sources=(*ctx.sources(), ok)).payload_json)
    assert p['decision_inputs']['cuts']['world_cut_ms'] == ctx.M


def test_book_after_decision_cut_or_stale_against_current_evidence_refuses_or_blocks(ctx):
    with pytest.raises(ValueError):
        ctx.produce(sources=ctx.sources(book=ctx.book(cut=ctx.D + 1)))          # future book
    with pytest.raises(DA.ContextBlocked):
        ctx.produce(sources=ctx.sources(book=ctx.book(cut=ctx.D - 130000)))     # book older than its own freshness at D


# ── 5: costs and book binding ──────────────────────────────────────────────

@pytest.fixture
def costed(ctx, monkeypatch):
    from tests.economics_fixtures import binding as fixture_binding, frozen, install_models
    install_models(monkeypatch)
    receipt = ctx.produce()
    basis = fixture_binding()
    dims = {k: getattr(basis, k) for k in DIMENSIONS}

    def build(cut_shift=0, valid_for=60000, captured=None, drop_costs=False, other_receipt=None):
        r = other_receipt or receipt
        ei = frozen('2', L.economic_binding(r, **dims))
        raw = lambda s, **kw: Source.freeze(s.source_id, {**json.loads(s.payload_json), **kw})  # noqa: E731
        at = ctx.D if captured is None else captured
        gross = raw(ei.gross, captured_ms=at, valid_until_ms=ctx.D + valid_for)
        costs = raw(ei.costs, captured_ms=at, valid_until_ms=ctx.D + valid_for)
        reserve = {**json.loads(raw(ei.uncertainty, captured_ms=at, valid_until_ms=ctx.D + valid_for).payload_json),
                   'gross_source_sha256': gross.sha256, 'cost_source_sha256': costs.sha256}
        ei = replace(ei, gross=gross, costs=None if drop_costs else costs,
                     uncertainty=Source.freeze(ei.uncertainty.source_id, reserve),
                     context=(*ei.context, r.as_source()))
        return ei, E.build(ei)
    return receipt, build


def test_costs_bound_to_exact_cut_and_replay_identically(ctx, costed):
    receipt, build = costed
    ei, er = build()
    first = L.decision_cost_binding(receipt, er, ctx.D + 10)
    assert first['as_of_ms'] == ctx.D and first['captured_ms'] == ctx.D
    assert set(first['components']) == set(L.COST_COMPONENTS)
    again = L.decision_cost_binding(receipt, E.build(E.from_inputs(json.loads(er.inputs_json))), ctx.D + 10)
    assert again == first                                    # same historical values, nothing recomputed


def test_missing_stale_or_wrong_cut_costs_refuse(ctx, costed):
    receipt, build = costed
    _, no_costs = build(drop_costs=True)
    with refused('REQUIRED_COST_EVIDENCE_UNAVAILABLE', DA.ContextBlocked):
        L.decision_cost_binding(receipt, no_costs, ctx.D)
    _, after = build(captured=ctx.D + 1)                     # cost snapshot after the decision cut
    with refused('COST_CAPTURED_AFTER_DECISION_CUT', DA.ContextRefused):
        L.decision_cost_binding(receipt, after, ctx.D)
    _, er = build(valid_for=100)                             # expired at use time
    with pytest.raises(ValueError):
        L.decision_cost_binding(receipt, er, ctx.D + 101)
    # costs bound to a different decision cut can never be rebound to this one
    other = ctx.produce(as_of_ms=ctx.D + 1000, sources=ctx.sources(
        signal=ctx.signal(), bundle=ctx.bundle()))
    _, foreign = build(other_receipt=other)
    with refused('COST_BINDING_CUT_OR_IDENTITY_DIFFERS', DA.ContextRefused):
        L.decision_cost_binding(receipt, foreign, ctx.D)


def test_costs_cannot_be_decision_costs_for_an_undecided_context(ctx, costed):
    receipt, build = costed
    _, er = build()
    plain = L.produce(**{**ctx.kwargs(), 'decision_grade': False})   # not decision-grade
    with refused('DECISION_GRADE_RECEIPT_REQUIRED', DA.ContextRefused):
        L.decision_cost_binding(plain, er, ctx.D)


def test_book_binding_is_the_frozen_book_never_a_refresh(ctx):
    r = ctx.produce()
    book = ctx.book()
    assert L.decision_book_binding(r, book)['snapshot_id'] == book['snapshot_id']
    later = snapshot('short', ctx.D + 5000)
    with refused('BOOK_DIFFERS_FROM_FROZEN_CONTEXT_BOOK', DA.ContextRefused):
        L.decision_book_binding(r, later)
    cut_ok = json.loads(r.payload_json)['decision_inputs']['book']
    assert cut_ok['observed_at_ms'] == book['observed_at_ms'] and cut_ok['completeness'] == 'COMPLETE'


# ── 6: supporting and opposing evidence ───────────────────────────────────

def test_supporting_and_opposing_evidence_both_retained_with_provenance(ctx):
    ctx.packets = [packet('structure', .8, ctx.symbol, ctx.A), packet('momentum', -.7, ctx.symbol, ctx.A),
                   packet('flow', .01, ctx.symbol, ctx.A), packet('value', None, ctx.symbol, ctx.A)]
    ev = json.loads(ctx.produce().payload_json)['decision_inputs']['analysts']
    assert [x['analyst'] for x in ev['evidence']['supporting']] == ['structure']
    assert [x['analyst'] for x in ev['evidence']['opposing']] == ['momentum']
    assert [x['analyst'] for x in ev['evidence']['neutral']] == ['flow']
    assert ev['evidence']['conflict'] is True
    for x in ev['evidence']['supporting'] + ev['evidence']['opposing']:
        assert len(x['record_id']) == 64 and x['inputs'][0]['source_ref'] == 'a' * 64
        assert x['confidence'] == .6 and x['quality'] == 'SUSPECT' and x['limitations'] == ['fixture limitation']
    assert [x['analyst'] for x in ev['absence']['unavailable']] == ['value']      # absence explicit
    assert ev['cut_ms'] == ctx.A


def test_direction_decides_which_side_is_opposition(ctx):
    ctx.packets = [packet('structure', .8, ctx.symbol, ctx.A)]
    buy = DA.analyze(ctx.bundle(), symbol=ctx.symbol, as_of_ms=ctx.D, direction=1)
    sell = DA.analyze(ctx.bundle(), symbol=ctx.symbol, as_of_ms=ctx.D, direction=-1)
    none = DA.analyze(ctx.bundle(), symbol=ctx.symbol, as_of_ms=ctx.D, direction=None)
    assert [len(buy['evidence'][k]) for k in ('supporting', 'opposing')] == [1, 0]
    assert [len(sell['evidence'][k]) for k in ('supporting', 'opposing')] == [0, 1]
    assert len(none['evidence']['unclassified']) == 1 and not none['evidence']['conflict']


def test_forged_support_only_summary_is_refused_on_replay(ctx):
    ctx.packets = [packet('structure', .8, ctx.symbol, ctx.A), packet('momentum', -.7, ctx.symbol, ctx.A)]
    r = ctx.produce()
    p = json.loads(r.payload_json)
    p['decision_inputs']['analysts']['evidence']['opposing'] = []         # hide the contradiction
    p['decision_inputs']['analysts']['evidence']['conflict'] = False
    forged = L.LiveReceipt(A.digest(p), A.canonical(p))
    with pytest.raises(ValueError, match='LIVE_CONTEXT_SOURCE_SET_DIFFERS'):
        L.replay(forged, ctx.sources(), ctx.D)
    with pytest.raises(ValueError, match='LIVE_CONTEXT_INTEGRITY_REFUSED'):
        L.replay(L.LiveReceipt(r.receipt_id, A.canonical(p)), ctx.sources(), ctx.D)


def test_foreign_instrument_duplicate_or_unlisted_analyst_refuses(ctx):
    cases = [([packet('structure', .5, 'ETH/USDT:USDT', ctx.A)], 'ANALYST_INSTRUMENT_MISMATCH'),
             ([packet('structure', .5, ctx.symbol, ctx.A)] * 2, 'ANALYST_PACKET_DUPLICATE:structure'),
             ([packet('astrologer', .5, ctx.symbol, ctx.A)], 'ANALYST_NOT_IN_ROSTER:astrologer')]
    for packets, code in cases:
        ctx.packets = packets
        with refused(code, DA.ContextRefused):
            ctx.produce()
    ctx.packets = [packet('structure', .5, ctx.symbol, ctx.A)]
    bad = ctx.bundle(packets=[{**ctx.packets[0], 'strength': 3.0}])        # not a valid measurement
    with refused('ANALYST_PACKET_INVALID', DA.ContextRefused):
        ctx.produce(sources=ctx.sources(bundle=bad))


# ── 7: immutability and replay ─────────────────────────────────────────────

def test_identity_is_reproducible_and_changes_with_material_evidence_or_cut(ctx):
    a, b = ctx.produce(), ctx.produce()
    assert a == b and a.receipt_id == b.receipt_id
    ctx.packets = [packet('structure', .5001, ctx.symbol, ctx.A)] + ctx.packets[1:]
    assert ctx.produce().receipt_id != a.receipt_id                       # analyst evidence changed
    ctx.packets = [packet(n, .5, ctx.symbol, ctx.A) for n in ('structure', 'momentum', 'flow')]
    assert ctx.produce().receipt_id == a.receipt_id
    shifted = ctx.produce(as_of_ms=ctx.D + 1)
    assert shifted.receipt_id != a.receipt_id                             # a different cut is a different context


def test_frozen_receipt_is_immutable_and_persists_byte_equivalently(ctx, tmp_path):
    r = ctx.produce()
    with pytest.raises(FrozenInstanceError):
        r.payload_json = '{}'
    path = L.persist(r, tmp_path / 'contexts')
    assert L.persist(r, tmp_path / 'contexts') == path                    # idempotent
    first = path.read_bytes()
    restarted = L.load(path)                                              # "new process": bytes only
    assert restarted == r and path.read_bytes() == first
    path.write_text(path.read_text().replace('"authority":"NONE"', '"authority":"ALL"'))
    with pytest.raises(ValueError, match='PERSISTED_CONTEXT_ID_OR_BYTES_DIFFERS'):
        L.load(path)


def test_later_revision_cannot_rewrite_or_enter_the_earlier_context(ctx):
    r = ctx.produce()
    before = r.payload_json
    # a later revision of the analyst evidence (new cut, new bundle) is a NEW context
    later_cut = ctx.D + 60000
    revised = Ctx(ctx.e)
    revised.A, revised.D = later_cut, later_cut + LATER
    revised.packets = [packet('structure', -.9, ctx.symbol, revised.A)]
    newer = revised.produce(sources=revised.sources(book=revised.book(), bundle=revised.bundle()),
                            as_of_ms=revised.D)
    assert newer.receipt_id != r.receipt_id and r.payload_json == before
    assert L.replay(r, ctx.sources(), ctx.D) == r                         # original still replays to itself
    # replaying the ORIGINAL cut with the revised (later) bundle refuses; no latest fallback
    with pytest.raises(ValueError):
        L.replay(r, ctx.sources(bundle=revised.bundle()), ctx.D)
    with pytest.raises(ValueError):
        L.produce(**{**ctx.kwargs(), 'sources': ctx.sources(bundle=revised.bundle())})


# ── 9: authority boundary ──────────────────────────────────────────────────

def test_valid_context_grants_no_activation_governor_owner_capital_or_order_authority(ctx):
    from tests.test_opportunity_live_integration import economics
    r = ctx.produce()
    p = json.loads(r.payload_json)
    assert p['authority'] == 'NONE' and p['strategy_eligibility'] == 'UNKNOWN'
    assert p['decision_inputs']['authority_boundary'] == 'NO_ACTIVATION_GOVERNOR_OWNER_CAPITAL_OR_ORDER_AUTHORITY'
    assert p['decision_inputs']['costs']['status'] == 'UNKNOWN'
    er = E.build(economics(r))
    c, sources = L.candidate(r, er)
    sources = tuple({s.source_id: s for s in sources}.values())
    unknown = Evidence(Status.UNKNOWN, ())
    proposal = allocate(Inputs(ctx.D, (c,), Portfolio('missing', ctx.D, None, unknown, (), ()),
                               unknown, unknown, 'FROZEN', sources))
    result = json.loads(proposal.result_json)
    assert result['decision'] == 'NO_ALLOCATION' and result['cash_candidate']['expression'] == 'CASH'


def test_context_blocked_is_a_value_error_and_distinct_from_refusal():
    assert issubclass(DA.ContextBlocked, ValueError) and issubclass(DA.ContextRefused, ValueError)
    assert not issubclass(DA.ContextBlocked, DA.ContextRefused)


def test_non_decision_grade_receipts_keep_their_bytes(ctx):
    r = L.produce(**{**ctx.kwargs(), 'decision_grade': False})
    p = json.loads(r.payload_json)
    assert 'decision_inputs' not in p and 'decision_grade' not in p
    assert L.replay(r, ctx.sources(), ctx.D) == r


# ── normal path: Journal votes -> capture -> decision-grade produce -> bridge ──

from tests.test_stage6_normal_sources import publications  # noqa: E402,F401
from tests.authority_factory_fixtures import cfg  # noqa: E402,F401


def normal_checkpoint(publications, tmp_path, *, votes=(), required=()):
    from tests.test_attention_telemetry import frames, event
    from tests.admission_cycle_fixture import bind_market
    from tests.test_stage6_normal_sources import NOW, SYMBOL as SYM
    from trader.core.types import Action, Decision, Side, Vote
    from trader.engine.evidence_capture import record_snapshot
    from trader.observability.attention import settings
    from trader.observability.store import Store
    from trader.portfolio import current
    j, config, v, receipt, _, market, _ = publications
    for role in ('contexts', 'economics'):
        for p in (tmp_path / role).glob('*.json'):
            p.unlink()
    record_snapshot(j, snapshot('long', NOW), at_ms=NOW)
    data = {SYM: next(iter(frames(1, NOW).values()))}
    store = Store(tmp_path / 'attention.db', settings())
    ident = dict(schema='attention-scan-identity.v1', instance_id='1' * 32, seq=NOW)
    store.write(dict(event('journal-scan', NOW, data), identity=ident))
    store.write(dict(kind='causes', scan_id='journal-scan', as_of_ms=NOW, identity=ident,
                     items=[dict(symbol=SYM, decision_id='normal-decision')]))
    store.close()
    sig = next(json.loads(s['payload_json'])['data'][0] for s in json.loads(receipt.payload_json)['sources']
               if s['source_id'] == 'signals')
    j.log_cycle(market, 'journal-cycle', 'paper')
    j.log_decision(Decision('normal-decision', 'journal-cycle', SYM, Action.BUY, 1., .5, .8, [],
                            strategy_signals=[sig], ts=market.ts))
    if votes:
        j.log_votes('journal-cycle', SYM, [Vote(n, SYM, Side.LONG, .5, .6, 'r', {'measurement': m}, ts=market.ts)
                                           for n, m in votes])
    config = {**config, 'decision': {'context': {'required_analysts': list(required)}}}
    admission = bind_market(market, config, NOW)
    return current.freeze(j.db_path, tmp_path / 'attention.db', tmp_path / 'investigation.db', config,
                          None, {'normal-decision': market}, None, admission)


def frozen_context(inputs):
    live = [s for s in inputs.sources if s.source_id.startswith('live-context:')]
    assert len(live) == 1
    return json.loads(live[0].payload_json)


def test_normal_path_freezes_votes_as_supporting_and_opposing_evidence(publications, tmp_path):
    from tests.test_stage6_normal_sources import NOW, SYMBOL as SYM
    votes = [('structure', packet('structure', .8, SYM, NOW)), ('momentum', packet('momentum', -.8, SYM, NOW))]
    inputs, detail = normal_checkpoint(publications, tmp_path, votes=votes, required=('structure',))
    p = frozen_context(inputs)
    assert p['decision_grade'] is True and p['authority'] == 'NONE'
    a = p['decision_inputs']['analysts']
    assert [x['analyst'] for x in a['evidence']['supporting']] == ['structure']
    assert [x['analyst'] for x in a['evidence']['opposing']] == ['momentum']
    assert a['evidence']['conflict'] is True and a['decision_id'] == 'normal-decision'
    assert {x['analyst'] for x in a['absence']['absent']} == set(DA.ENABLED) - {'structure', 'momentum'}
    assert p['decision_inputs']['cuts']['analyst_cut_ms'] == NOW
    assert p['decision_inputs']['book']['sha256'] == DA.sha256(json.loads(
        next(s.payload_json for s in inputs.sources if s.source_id == 'venue_position_snapshot')))
    # the context is complete and analyst-valid; only the (real, empty-registry) cost state blocks
    assert [d['block_reason'] for d in detail['blocked_decisions']] == ['REQUIRED_COST_EVIDENCE_UNAVAILABLE']
    assert detail['blocked_decisions'][0]['live_receipt_id'] == next(
        s.source_id for s in inputs.sources if s.source_id.startswith('live-context:')).split(':', 1)[1]


def test_normal_path_missing_optional_analysts_does_not_block_the_context(publications, tmp_path):
    inputs, detail = normal_checkpoint(publications, tmp_path)
    assert not [m for m in detail['missing_sources'] if 'ANALYST' in m]
    a = frozen_context(inputs)['decision_inputs']['analysts']
    assert a['evidence']['supporting'] == [] and len(a['absence']['absent']) == len(DA.ENABLED)
    assert [d['block_reason'] for d in detail['blocked_decisions']] == ['REQUIRED_COST_EVIDENCE_UNAVAILABLE']


def test_normal_path_missing_required_analyst_blocks_that_candidate_explicitly(publications, tmp_path):
    inputs, detail = normal_checkpoint(publications, tmp_path, required=('structure',))
    assert detail['candidate_count'] == 0 and 'blocked_decisions' not in detail   # blocked before economics
    assert any(m.startswith('CONTEXT_BLOCKED:normal-decision:') and 'REQUIRED_ANALYST_UNAVAILABLE:structure' in m
               for m in detail['missing_sources'])
    assert not [s for s in inputs.sources if s.source_id.startswith('live-context:')]


def test_normal_path_mixed_analyst_cut_refuses_the_checkpoint(publications, tmp_path):
    from tests.test_stage6_normal_sources import NOW, SYMBOL as SYM
    votes = [('structure', packet('structure', .8, SYM, NOW - 1000))]
    with pytest.raises(DA.ContextRefused, match='ANALYST_MIXED_CUTS:structure'):
        normal_checkpoint(publications, tmp_path, votes=votes)


# ── cost state bound to the decision context (DEC-01), production empty registry ──

def production_economics(receipt):
    """The REAL economics path: registries are code-owned and empty, no fixture models."""
    assert E.GROSS_MODELS == {} and E.RESERVE_MODELS == {} and E.COST_SCOPE_MODELS == {}
    from tests.test_opportunity_live_integration import economics
    return E.build(economics(receipt))


def test_empty_registry_yields_real_unavailable_receipt_bound_and_blocked(ctx):
    r = ctx.produce()
    er = production_economics(r)
    result = json.loads(er.result_json)
    assert result['economic_status'] == 'UNAVAILABLE' and result['context_id'] == r.context.context_id
    d = L.finalize_decision(r, er, ctx.D + 10)
    p = json.loads(d.payload_json)
    assert d.status == L.BLOCKED and d.block_reason == 'REQUIRED_COST_EVIDENCE_UNAVAILABLE'
    assert (p['live_receipt_id'], p['context_id'], p['economics_receipt_id'], p['economics_status']) == (
        r.receipt_id, r.context.context_id, er.receipt_id, 'UNAVAILABLE')
    assert p['decision_cut_ms'] == ctx.D and p['book'] == json.loads(r.payload_json)['decision_inputs']['book']
    assert p['cost_binding'] is None and p['authority'] == 'NONE'
    # UNKNOWN is never converted to zero: no component carries any value
    assert set(p['cost_components']) == set(L.COST_COMPONENTS)
    assert all(c['value'] is None and c['status'] == 'UNAVAILABLE' for c in p['cost_components'].values())
    assert '"0"' not in json.dumps(p['cost_components']) and ': 0' not in json.dumps(p['cost_components'])
    assert L.verify_decision(d, r, er, ctx.D + 10) == d                    # replay identical


def test_decision_envelope_does_not_mutate_the_core_and_changes_with_economics(ctx):
    r = ctx.produce()
    before = r.payload_json
    er = production_economics(r)
    d = L.finalize_decision(r, er, ctx.D + 10)
    assert r.payload_json == before and d.receipt_id != r.receipt_id
    with pytest.raises(FrozenInstanceError):
        d.payload_json = '{}'
    assert L.finalize_decision(r, er, ctx.D + 10) == d
    assert L.finalize_decision(r, er, ctx.D + 11).receipt_id != d.receipt_id   # now_ms is part of what was bound
    forged = json.loads(d.payload_json)
    forged['status'] = L.BOUND
    with pytest.raises(ValueError, match='DECISION_CONTEXT_INTEGRITY_REFUSED'):
        L.verify_decision(L.DecisionReceipt(d.receipt_id, A.canonical(forged)), r, er, ctx.D + 10)
    with pytest.raises(ValueError, match='DECISION_CONTEXT_REPLAY_DIFFERS'):
        L.verify_decision(L.DecisionReceipt(A.digest(forged), A.canonical(forged)), r, er, ctx.D + 10)


def test_available_cost_same_cut_binds_and_replays(ctx, costed):
    receipt, build = costed
    _, er = build()
    d = L.finalize_decision(receipt, er, ctx.D + 10)
    p = json.loads(d.payload_json)
    assert d.status == L.BOUND and d.block_reason is None
    assert p['cost_binding'] == L.decision_cost_binding(receipt, er, ctx.D + 10)
    assert all(c['status'] in ('ESTABLISHED', 'NOT_APPLICABLE') for c in p['cost_components'].values())
    assert L.verify_decision(d, receipt, er, ctx.D + 10) == d


def test_stale_wrong_cut_or_late_cost_receipts(ctx, costed):
    receipt, build = costed
    _, stale = build(valid_for=100)
    assert L.finalize_decision(receipt, stale, ctx.D + 101).status == L.BLOCKED
    _, late = build(captured=ctx.D + 1)
    with refused('COST_CAPTURED_AFTER_DECISION_CUT', DA.ContextRefused):
        L.finalize_decision(receipt, late, ctx.D)
    other = ctx.produce(as_of_ms=ctx.D + 1000)
    _, foreign = build(other_receipt=other)
    with refused('COST_BINDING_CUT_OR_IDENTITY_DIFFERS', DA.ContextRefused):
        L.finalize_decision(receipt, foreign, ctx.D)
    plain = L.produce(**{**ctx.kwargs(), 'decision_grade': False})
    with refused('DECISION_GRADE_RECEIPT_REQUIRED', DA.ContextRefused):
        L.finalize_decision(plain, build()[1], ctx.D)


def test_only_a_bound_decision_reaches_the_allocator_inputs(publications, tmp_path):
    """Stage-6 fixture models (mechanics only): BOUND envelope + candidate; none under the real registry."""
    from tests.test_stage6_normal_sources import read
    inputs, detail = read(publications)
    envelopes = [json.loads(s.payload_json) for s in inputs.sources if s.source_id.startswith('decision-context:')]
    assert [e['status'] for e in envelopes] == ['BOUND'] and len(inputs.candidates) == 1
    assert envelopes[0]['opportunity_id'] == inputs.candidates[0].opportunity_id
    assert 'blocked_decisions' not in detail
