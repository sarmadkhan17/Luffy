"""Stage 7 normal learning loop closure.

TEST-ONLY rules exist exclusively in this file. The production registries
(``targets.PRODUCTION_RULES``, ``foundation.REGISTERED_RULES``) stay empty /
lifecycle-only and nothing here can reach them from normal runtime.
"""
from copy import deepcopy
from dataclasses import replace
import json
import socket
import sqlite3
from pathlib import Path

import pandas as pd
import pytest

from trader.core.journal import Journal
from trader.core.types import Side
from trader.learning import (foundation as L, targets as T, application as A, runtime as R,
                             consumers as K, dispatch as D, capture as C, capture_runtime as CR,
                             producers as P)
from trader.strategy import factory_handoff as F
# Fixtures shared with the existing Stage 6/7 suites (real producers, no mocks).
from tests.test_real_learning_integration import isolated, consumer_case  # noqa: F401
from tests.test_learning_foundation import chain  # noqa: F401
from tests.test_decision_sources import producer, exact  # noqa: F401
from tests.test_scout_upgrades import _snap


@pytest.fixture(autouse=True)
def no_venue(monkeypatch):
    monkeypatch.setattr(socket.socket, 'connect', lambda *a: pytest.fail('venue/network forbidden'))


# ------------------------------------------------------------------ helpers
def make_rule(target, context, value, *, kinds=(L.Kind.CASH,), version='test-v1',
              required_sources=(), suffix=''):
    """A TEST-ONLY deterministic rule that satisfies the full dispatch contract."""
    def evaluate(ev, current):
        return L.Status.APPLICABLE, value, 'TEST_ONLY_DETERMINISTIC_ACTION'
    rule = T.Rule('test-only.' + target.value + suffix, target, version, evaluate, kinds=tuple(kinds),
                  required_sources=tuple(required_sources), context=lambda view: context,
                  sufficient=lambda view: (True, 'TEST_ONLY_SUFFICIENT'),
                  requirements=('TEST_ONLY',))
    return {rule.rule_id: rule}


def forward_outcome(j, d, ai, *, ret=.1):
    """The real automatic forward adapter on a real journalled decision."""
    cut = ai.as_of_ms
    declaration = dict(decision_id=d.id, cycle_id=d.cycle_id, symbol=d.symbol, ts=d.ts, action='BUY', entry_price=100)
    with j._tx() as db:
        C.record_action(db, 'decision:' + d.id, dict(prediction=declaration), cut)
        targets = {'1h': dict(ts=pd.Timestamp(cut + 3600000, unit='ms', tz='UTC').isoformat(), close=100 * (1 + ret))}
        return CR.forward(db, declaration, {'fwd_ret_1h': ret}, targets, cut + 3900000)


def evidence_of(j, outcome_id):
    raw = P.materialize(j._conn(), outcome_id)
    return raw, R.evidence_from_body(raw['evidence'])


def analyst_orchestrator(journal, timeframe='15m'):
    from trader.agents.base import Analyst
    from trader.engine.orchestrator import Orchestrator

    class Stub(Analyst):
        name = 'stub'
        evidence_timeframe = timeframe
        frame_inputs = (('15m', ('close',), 1),)
        measurement_limitations = ('Synthetic test-only measurement.',)

        def evaluate(self, snap):
            return self._vote(self.name, snap, 0.8, 0.8, 'stub')
    return Orchestrator([Stub()], journal)


def regime_of(snap):
    from trader.agents.regime import classify
    return classify(snap.df('15m'), snap.df('1h'))['regime']


@pytest.fixture
def loop(tmp_path):
    """Like test_decision_sources.producer, but the live candidate's origin IS the
    journalled Kernel decision (candidate_id = decision_id:version_id), as in the
    real Portfolio freeze. Everything is produced by the real adapters."""
    from datetime import datetime, timezone
    from trader.core.types import Snapshot, Decision, Action
    from trader.portfolio import opportunity_live as OL, candidate_bridge as B, allocator as AL
    from tests.test_opportunity_live_integration import snapshot, SYMBOL
    from tests.test_strategy_factory_handoff import INPUTS
    from tests.test_attention_telemetry import frames
    from scripts.opportunity_context_shadow import DIMENSIONS
    from trader.core.config import load_config
    from trader.strategy.signal_occurrence import spec_fingerprint
    from trader.strategy.spec import StrategySpec
    from tests.test_strategy_factory_handoff import _candidate, T0
    from tests.test_opportunity_live_integration import IID
    j = Journal(tmp_path / 'luffy.db')   # the file name Kernel-side readers (research order) expect
    h = _candidate(j)
    cfg = load_config()
    v = F.create_version(j, cfg, {'kind': 'research_candidate', 'hash': h}, at_ms=T0)
    v = F.load_version(j, v['version_id'])
    row = dict(j.query('SELECT * FROM strategy_versions WHERE version_id=?', (v['version_id'],))[0])
    sig = dict(symbol=SYMBOL, action='BUY', params=dict(spec_id=v['strategy_id'],
        spec_fingerprint=spec_fingerprint(StrategySpec.from_dict(v['spec'])),
        signal_timeframe=v['spec']['timeframe'], signal_bar_close_ms=T0))
    args = dict(as_of_ms=T0 + 100, symbol=SYMBOL, instrument_id=IID, cycle_id='test-cycle', candidate_id='test-setup',
        sources=(OL.source('strategy_version', row, T0, T0 + 1000), OL.source('signals', [sig], T0, T0 + 1000)),
        required_roles=('strategy_version', 'signals'))
    cut = args['as_of_ms']
    book = snapshot(None, cut)
    from trader.observability import attention, world_producer
    market = {SYMBOL: frames(1, cut)['S0/USDT']}
    event = attention.capture(market, [SYMBOL], 'prospective-market-cut', attention.settings({'world_model': True}), cut)
    model, world_receipt = world_producer.produce(event)
    assert model is not None and world_receipt['status'] == 'ok'
    args = dict(args, candidate_id='prospective:' + v['version_id'],
                sources=(*args['sources'], OL.source('world_model', world_receipt['record_json'], cut, cut + 1000),
                         OL.source('portfolio', book, cut, cut + 1000)))
    receipt = OL.produce(**args)
    c, er, sources = B.build(j, receipt, cfg, available_inputs=INPUTS, dimensions=DIMENSIONS)
    known = AL.Evidence(AL.Status.ESTABLISHED, (sources[0].source_id,))
    portfolio = AL.Portfolio(book['snapshot_id'], cut, cut + 1000, known, (), (sources[0].source_id,))
    ai = AL.Inputs(cut, (c,), portfolio, known, known, 'ACTIVE', sources)
    proposal = AL.allocate(ai)
    ts = datetime.fromtimestamp(cut / 1000, timezone.utc).isoformat()
    snap = Snapshot(SYMBOL, ts, 100, market[SYMBOL])
    d = Decision('prospective', 'test-cycle', SYMBOL, Action.HOLD, 0, .2, .5, [], [], ts=ts)
    intent = CR.journalize_allocation(j, snap, d, cfg, receipt, proposal, ai)
    assert not j.query('SELECT * FROM learning_capture_failures')
    j.learning_test_only = True
    return j, d, snap, cfg, receipt, proposal, ai, intent, v


# ------------------------------------------------------------ 1. sources
def test_scan_decision_names_later_stages_and_ignores_injected_learning_sources(tmp_path):
    from tests.test_historical_outcome_capture import CUT
    from trader.core.types import Snapshot, Decision, Action
    j = Journal(tmp_path / 'scan.db')
    frame = _snap().df('15m')
    ts = '2026-01-01T00:00:00+00:00'
    snap = Snapshot('BTCUSDT', ts, 100, {'1h': frame})
    snap.learning_sources = dict(world={'record_json': 'INJECTED'}, context={'context_json': 'INJECTED'})
    cfg = {'risk': {'limit': .01}, 'strategies': {}}
    inputs = CR.runtime_inputs(j, snap, cfg, cut_ms=CUT, control_state='ACTIVE')
    assert 'world' not in inputs and 'context' not in inputs   # no optional-attribute injection
    d = Decision('scan-d', 'scan-c', 'BTCUSDT', Action.HOLD, 0, .2, .5, [], [], ts=ts)
    j.log_cycle(snap, 'scan-c', 'TEST_ONLY')
    j.log_decision(d, capture_inputs=inputs)
    assert not j.query('SELECT * FROM learning_capture_failures')
    _, reg = C.registration(j._conn(), 'decision:scan-d')
    assert set(reg['not_consulted']) == set(CR.LATER_STAGE_ROLES)
    manifest = json.loads(j.query('SELECT payload FROM learning_decision_source_manifests WHERE manifest_id=?',
                                  (reg['decision_source_manifest_id'],))[0]['payload'])
    rows = {r['role']: r for r in manifest['dependencies']}
    for role in CR.LATER_STAGE_ROLES:
        assert rows[role]['status'] in ('UNAVAILABLE', 'NOT_APPLICABLE')
        assert rows[role]['reason'] == 'STAGE_AFTER_SCAN_DECISION_SEE_DECISION_CHAIN'
    for role in ('data', 'risk_config', 'control', 'decision', 'reasons', 'cycle'):
        assert rows[role]['status'] == 'AVAILABLE', role


def test_no_producer_attribute_can_inject_learning_sources():
    root = Path(__file__).resolve().parents[1] / 'trader'
    offenders = [str(p) for p in root.rglob('*.py') if 'learning_sources' in p.read_text()]
    assert not offenders


# ------------------------------------------------ 2. one decision identity
def checkpoint_stage(producer, monkeypatch, tmp_path):
    from trader.portfolio import current
    j, d, snap, cfg, receipt, proposal, ai, intent, v = producer
    monkeypatch.setattr(current, 'freeze', lambda *a, **kw: (ai, {}))
    origin = json.loads(receipt.payload_json)['candidate_id'].removesuffix(':' + v['version_id'])
    result, detail = current.checkpoint(j.db_path, cfg, ledger=tmp_path / 'portfolio.db',
                                        market_snapshots={origin: snap})
    return result, detail, origin


def test_portfolio_stage_is_the_same_decision_not_a_new_identity(loop, monkeypatch, tmp_path):
    j, d, snap, cfg, receipt, proposal, ai, intent, v = loop
    decisions_before = j.query('SELECT id,action FROM decisions ORDER BY id')
    result, detail, origin = checkpoint_stage(loop, monkeypatch, tmp_path)
    assert origin == d.id
    event, = detail['learning_source_delivery']
    assert event.startswith('decision:%s#allocation:%s' % (d.id, proposal.proposal_id))
    db = j._conn()
    _, reg = C.registration(db, event)
    parent_id, parent = C.registration(db, 'decision:' + d.id)
    # same decision, same cycle, linked parent; never an 'allocation:' identity or fabricated HOLD
    assert reg['lineage']['decision_id'] == d.id == parent['lineage']['decision_id']
    assert reg['lineage']['cycle_id'] == parent['lineage']['cycle_id']
    assert reg['chain'] == dict(decision_id=d.id, stage='ALLOCATION', parent_event_key='decision:' + d.id,
                                parent_registration_id=parent_id)
    assert reg['lineage']['proposal_id'] == proposal.proposal_id and reg['lineage']['intent_id']
    assert not db.execute("SELECT 1 FROM learning_registrations WHERE event_key LIKE 'decision:allocation:%'").fetchall()
    assert j.query('SELECT id,action FROM decisions ORDER BY id') == decisions_before
    assert not j.query('SELECT * FROM learning_capture_failures')
    # exact original sources, replayable; the stage action is the allocator's, not a placeholder HOLD
    manifest = C.manifest(db, next(r[0] for r in db.execute(
        'SELECT outcome_id FROM learning_outcome_captures WHERE outcome_key=?', (event + ':pending',))))
    assert manifest['decision_source_faults'] == []
    action = [json.loads(r[0])['dependency'] for r in db.execute(
        'SELECT payload FROM learning_actions WHERE event_key=?', (event,))]
    assert C.resolve(db, next(a for a in action if a['role'] == 'action'))['action'].startswith('ALLOCATION_')
    # idempotent: a second checkpoint of the same cut creates nothing new
    count = db.execute('SELECT COUNT(*) FROM learning_registrations').fetchone()[0]
    result2, detail2, _ = checkpoint_stage(loop, monkeypatch, tmp_path)
    assert detail2['learning_source_delivery'] == [event]
    assert db.execute('SELECT COUNT(*) FROM learning_registrations').fetchone()[0] == count


def test_risk_decision_and_refusal_resolve_the_same_chain(loop, monkeypatch, tmp_path):
    j, d, snap, cfg, receipt, proposal, ai, intent, v = loop
    result, detail, _ = checkpoint_stage(loop, monkeypatch, tmp_path)
    event, = detail['learning_source_delivery']
    db = j._conn()
    risks = [r for r in result['risk_decisions']]
    assert risks, 'the normal runtime evaluates Risk for every intent'
    actions = [json.loads(r[0])['dependency'] for r in db.execute('SELECT payload FROM learning_actions WHERE event_key=?', (event,))]
    risk_dep = next(a for a in actions if a['role'] == 'risk_decision')
    assert risk_dep['source_id'] == risks[0]['decision_id']
    value = C.resolve(db, risk_dep)
    assert value['trade_intent_id'] == C.registration(db, event)[1]['lineage']['intent_id']
    # A requested open that Risk does not approve is Risk-blocked ON THIS CHAIN.
    oid = CR.record_risk(db, event, cfg, value['trade_intent_id'], risks[0], ai.as_of_ms, True)
    assert oid
    j._conn().commit()
    manifest = C.manifest(db, oid)
    cap = manifest['capture']
    assert cap['kind'] == 'RISK_BLOCKED_SIGNAL' and manifest['decision_source_faults'] == []
    lin = cap['lineage']
    assert lin['decision_id'] == d.id and lin['proposal_id'] == proposal.proposal_id
    assert lin['intent_id'] == value['trade_intent_id'] and lin['risk_decision_id'] == risks[0]['decision_id']
    assert cap['registration']['chain']['parent_event_key'] == 'decision:' + d.id
    o, retained = C.learning_outcome(db, oid)
    replay = L.replay(o, retained)
    # only the honest "no registered future measurement" fault remains: identity chain verified
    assert replay.faults == ('manifest_not_complete',)
    assert o.kind == L.Kind.RISK_BLOCKED and o.boundary == L.Boundary.UNASSESSABLE
    # a non-refusal can never be turned into a Risk block
    with pytest.raises(ValueError, match='risk_decision_is_not_a_refusal'):
        with j._tx() as c2:
            dep = C.record_action(c2, event, dict(value, ok=True, risk_decision_id='x'), ai.as_of_ms, risk=True, source_id='x')
            P.risk_blocked(c2, event, dep, ai.as_of_ms)


def test_execution_resolves_the_exact_intent_stage_and_legacy_trade_stays_on_original(loop, monkeypatch, tmp_path):
    j, d, snap, cfg, receipt, proposal, ai, intent, v = loop
    result, detail, _ = checkpoint_stage(loop, monkeypatch, tmp_path)
    event, = detail['learning_source_delivery']
    db = j._conn()
    _, reg = C.registration(db, event)
    intent_id = reg['lineage']['intent_id']
    assert C.chain_event(db, d.id, intent_id) == event
    assert C.chain_event(db, d.id) == 'decision:' + d.id
    with pytest.raises(ValueError, match='decision_chain_stage_unavailable_or_ambiguous'):
        C.chain_event(db, d.id, 'intent-that-was-never-allocated')
    for identity, expected in ((dict(trade_intent_id=intent_id), event), ({}, 'decision:' + d.id)):
        tid = 't-' + str(len(identity))
        with j._tx() as c:
            c.execute("INSERT INTO trades(id,decision_id,symbol,side,amount,entry_price,opened_at,status,exec_mode,entry_identity_json)"
                      " VALUES(?,?,?,?,?,?,?,?,?,?)", (tid, d.id, d.symbol, 'long', 1, 100, d.ts, 'open', 'paper', json.dumps(identity)))
        assert C.trade_event(db, d.id, tid) == expected


# ------------------------------------------------------ 3. exit semantics
def test_exit_semantics_are_bound_per_version_not_per_allocation(loop):
    from trader.portfolio import economics as E
    j, d, snap, cfg, receipt, proposal, ai, intent, v = loop
    c = ai.candidates[0]
    er = E.from_payload(json.loads(c.economics.receipt_json))
    exact_binding = CR.exit_binding(c, er)
    assert exact_binding['exit_semantics_id'] and exact_binding['version_id'] == c.version_id
    assert exact_binding['reason'] == 'EXACT_VERSION_AUTHORITY'
    # another version never inherits this version's semantics; nothing is guessed
    other = replace(c, version_id='another-version') if hasattr(c, '__dataclass_fields__') else c
    unbound = CR.exit_binding(other, er)
    assert unbound['exit_semantics_id'] is None and unbound['reason'] == 'CANDIDATE_BRIDGE_AUTHORITY_VERSION_DIFFERS'
    # a cut with several candidates (even with a foreign authority) still yields this candidate's binding
    two = replace(ai, candidates=(c, other))
    inputs = CR.allocation_sources(None, snap, cfg, receipt, proposal, two, c, [intent])
    binding = inputs['exit_semantics']
    assert binding['exit_semantics_id'] == exact_binding['exit_semantics_id']
    assert {b['version_id']: b['exit_semantics_id'] for b in binding['cut_bindings']} == {
        c.version_id: exact_binding['exit_semantics_id'], 'another-version': None}


# --------------------------------------------- 4/5/12/13. rule dispatch
def test_empty_production_registry_yields_no_proposal_and_zero_mutation(isolated):
    j, cfg, ev, o, v = isolated
    assert not T.PRODUCTION_RULES and D.production_registry() is T.PRODUCTION_RULES
    assert set(L.REGISTERED_RULES) == {L.DECAY_RULE}
    before = '\n'.join(j._conn().iterdump())
    receipt = D.dispatch_evidence(j, cfg, ev, at_ms=o.observed_ms, shadow=True)
    assert [r['result'] for r in receipt['results']] == ['NO_PROPOSAL'] and receipt['results'][0]['reason'] == 'UNREGISTERED'
    assert '\n'.join(j._conn().iterdump()) == before
    written = D.dispatch_evidence(j, cfg, ev, at_ms=o.observed_ms)
    assert written['written'] and written['results'][0]['reason'] == 'UNREGISTERED'
    assert not j.query('SELECT * FROM learning_runtime_proposals')
    assert D.dispatch_evidence(j, cfg, ev, at_ms=o.observed_ms + 5)['duplicate']
    for target in T.ADAPTIVE:
        assert T.read(j, target, T.Context('A', 'h', 'r', 'e', 'd', 'f', 's'))['revision'] == 0


def test_target_category_existence_is_not_permission(isolated):
    j, cfg, ev, o, v = isolated
    ctx = T.Context('A', '4h', 'regime', 'signal', 'LONG', 'family', 'exact')
    # An old-style rule (no dispatch contract) can be applied explicitly but is never dispatched.
    legacy = T.Rule('legacy-explicit', L.Target.CONFIDENCE, 'v1', lambda e, s: (L.Status.APPLICABLE, {'reliability': .5}, 'x'))
    receipt = D.dispatch_evidence(j, cfg, ev, at_ms=o.observed_ms, registry={'legacy-explicit': legacy})
    assert receipt['results'][0]['reason'] == 'UNREGISTERED'
    mismatched = make_rule(L.Target.CONFIDENCE, ctx, {'reliability': .5}, kinds=(L.Kind.EXECUTED,))
    receipt = D.dispatch_evidence(j, cfg, ev, at_ms=o.observed_ms, registry=mismatched)
    assert [(r['result'], r['reason']) for r in receipt['results']] == [('NO_PROPOSAL', 'EVIDENCE_TYPE_INCOMPATIBLE')]
    missing_version = make_rule(L.Target.CONFIDENCE, ctx, {'reliability': .5}, kinds=(L.Kind.RESEARCH,),
                                required_sources=(('data', 'a-version-that-was-not-used'),), suffix='.v')
    receipt = D.dispatch_evidence(j, cfg, ev, at_ms=o.observed_ms, registry=missing_version)
    assert receipt['results'][0]['reason'].startswith('REQUIRED_SOURCE_VERSION_UNAVAILABLE')
    assert not j.query('SELECT * FROM learning_runtime_proposals')


def view(kind, **kw):
    return D.View(kind, L.Boundary.COUNTERFACTUAL, {}, 1, 2, {}, kw.get('roles', {}), kw.get('sources', {}))


@pytest.mark.parametrize('kind', list(L.Kind))
def test_every_outcome_class_is_acceptable_only_to_a_rule_that_declares_it(kind):
    ctx = T.Context('A', '4h', 'regime', 'signal', 'LONG', 'family', 'exact')
    rule = list(make_rule(L.Target.WEIGHTS, ctx, {'reliability': .5}, kinds=(kind,)).values())[0]
    assert D.screen(rule.rule_id, rule, view(kind)) == (None, ctx)
    other = next(k for k in L.Kind if k != kind)
    assert D.screen(rule.rule_id, rule, view(other))[0] == 'EVIDENCE_TYPE_INCOMPATIBLE'


def test_dispatch_binds_context_sufficiency_and_exact_source_versions():
    ctx = T.Context('A', '4h', 'regime', 'signal', 'LONG', 'family', 'exact')
    base = list(make_rule(L.Target.WEIGHTS, ctx, {'reliability': .5}, required_sources=(('data', 'v1'),)).values())[0]
    row = dict(status='AVAILABLE', version='v1')
    v = view(L.Kind.CASH, roles={'data': row}, sources={'data': {}})
    assert D.screen(base.rule_id, base, v) == (None, ctx)
    assert D.screen(base.rule_id, base, view(L.Kind.CASH, roles={'data': dict(row, version='v2')}, sources={'data': {}}))[0].startswith('REQUIRED_SOURCE')
    assert D.screen(base.rule_id, base, view(L.Kind.CASH, roles={'data': dict(row, status='UNAVAILABLE')}, sources={'data': {}}))[0].startswith('REQUIRED_SOURCE')
    assert D.screen(base.rule_id, replace(base, context=lambda view: None), v)[0] == 'TARGET_CONTEXT_UNAVAILABLE'
    assert D.screen(base.rule_id, replace(base, sufficient=lambda view: (False, 'too few')), v)[0] == 'INSUFFICIENT:too few'
    assert D.screen('another-id', base, v)[0] == 'RULE_NOT_DISPATCHABLE'
    # no generic win/loss -> up/down: the dispatcher has no value logic of its own; the value
    # always comes from the registered rule, never from the outcome's sign
    import ast
    names = {n.id for n in ast.walk(ast.parse(Path(D.__file__).read_text())) if isinstance(n, ast.Name)}
    assert not names & {'reliability', 'confidence', 'pnl', 'net_pnl', 'win', 'loss', 'returns'}


@pytest.fixture
def every_kind(loop, monkeypatch, tmp_path):
    """One real adapter-produced outcome per class the producers can currently emit."""
    from trader.engine.booking import assess
    from trader.engine.accounting import digest
    j, d, snap, cfg, receipt, proposal, ai, intent, v = loop
    found = {'CASH': forward_outcome(j, d, ai)}
    result, detail, _ = checkpoint_stage(loop, monkeypatch, tmp_path)
    event, = detail['learning_source_delivery']
    db = j._conn()
    risk = result['risk_decisions'][0]
    with j._tx() as c:
        found['RISK_BLOCKED'] = CR.record_risk(c, event, cfg, C.registration(c, event)[1]['lineage']['intent_id'], risk, ai.as_of_ms, True)
    found['MISSED'] = db.execute("SELECT outcome_id FROM learning_outcome_captures WHERE outcome_key LIKE 'missed:%' "
                                 "AND outcome_id IN (SELECT outcome_id FROM learning_outcome_captures)").fetchall()[-1][0]
    j.log_brain_event('data_quality_incident', 'stale feed', {'required_input': 'UNAVAILABLE'})
    found['DATA'] = j.query("SELECT outcome_id FROM learning_outcome_captures WHERE outcome_key LIKE 'incident:%'")[0]['outcome_id']
    trade = dict(id='t', decision_id=d.id, status='closed')
    cut = ai.as_of_ms
    fill = dict(id='fill', order='123', symbol='BTCUSDT', side='sell', timestamp=cut + 100, amount=1, price=101,
                commission=.1, commission_asset='USDT', realized_pnl=-1)
    booking = dict(schema_version='trade-booking.v1', trade_id='t', kind='close:test', observed_ms=cut + 100, before=None,
                   after=trade, evidence=dict(basis='venue_order_fills', order_id='123', quantity=1, slippage=5, symbol='BTCUSDT',
                                              side='sell', since_ms=cut, observed_ms=cut + 100, fills=[fill]))
    booking['assessment'] = assess(booking['evidence'])
    booking['sha256'] = digest(booking)
    with j._tx() as c:
        found['EXECUTION'] = P.execution_quality(c, trade, booking)
    return j, cfg, found


def test_generic_dispatch_accepts_real_replay_complete_evidence_of_each_producible_class(every_kind):
    j, cfg, found = every_kind
    catch_all = T.Context('BTC', '4h', 'regime', 'any', 'long', 'analyst', 'any')
    rules = make_rule(L.Target.WEIGHTS, catch_all, {'reliability': .5}, kinds=tuple(L.Kind))
    outcome = {}
    for name, oid in found.items():
        raw, ev = evidence_of(j, oid)
        rec = D.dispatch_evidence(j, cfg, ev, at_ms=raw['outcome']['observed_ms'] + 10, registry=rules, shadow=True)
        outcome[name] = (raw['outcome']['kind'], raw['replay_gate'], rec['results'][0]['result'], rec['results'][0]['reason'])
    # replay-complete evidence is dispatched; evidence whose boundary is honestly UNASSESSABLE
    # (no registered future measurement) is NO_PROPOSAL, never promoted into a proposal.
    assert outcome['CASH'][:3] == ('SKIPPED_CASH', 'REPLAY_COMPLETE', 'PROPOSED')
    assert outcome['EXECUTION'][:3] == ('EXECUTION_QUALITY', 'REPLAY_COMPLETE', 'PROPOSED')
    for name, kind in (('RISK_BLOCKED', 'RISK_BLOCKED_SIGNAL'), ('MISSED', 'MISSED_OPPORTUNITY'), ('DATA', 'DATA_QUALITY_INCIDENT')):
        assert outcome[name] == (kind, 'REPLAY_INCOMPLETE', 'NO_PROPOSAL', 'REPLAY_INCOMPLETE'), outcome[name]
    # with the production registry nothing is proposed for any of them
    for oid in found.values():
        raw, ev = evidence_of(j, oid)
        assert D.dispatch_evidence(j, cfg, ev, at_ms=1, shadow=True)['results'][0]['reason'] == 'UNREGISTERED'


# -------------------------------------- 10/12. the normal prospective loop
TARGETS = [L.Target.CONFIDENCE, L.Target.WEIGHTS, L.Target.ALLOCATION, L.Target.ATTENTION,
           L.Target.RESEARCH, L.Target.WORLD]


def evidence_consumer(target, j):
    """CONFIDENCE / WEIGHTS reach net_score through the real Orchestrator.decide path."""
    snap = _snap()
    regime = regime_of(snap)
    context = K.evidence_context(snap.symbol, '15m', regime, 'stub', Side.LONG.value, 'analyst', 'stub')

    def consume():
        return analyst_orchestrator(j).decide(_snap(), population=[], entry_allowed=True).score
    return context, ({'reliability': .5}), consume


@pytest.mark.parametrize('target', TARGETS)
def test_normal_loop_from_real_decision_to_changed_consumer(loop, target, monkeypatch, tmp_path):
    j, d, snap, cfg, receipt, proposal, ai, intent, v = loop
    if target in (L.Target.CONFIDENCE, L.Target.WEIGHTS):
        context, value, consume = evidence_consumer(target, j)
    else:
        context, value, consume = consumer_case(target, j, monkeypatch, tmp_path)
    # decision -> exact source manifest -> resolved outcome -> replay-complete evidence
    oid = forward_outcome(j, d, ai)
    raw, ev = evidence_of(j, oid)
    assert raw['replay_gate'] == 'REPLAY_COMPLETE' and raw['authoritative']
    assert raw['outcome']['kind'] == 'SKIPPED_CASH' and raw['outcome']['boundary'] == 'COUNTERFACTUAL'
    assert L.verified_history(ev)
    rules = make_rule(target, context, value, kinds=(L.Kind.CASH,),
                      required_sources=(('risk_config', 'prospective-source.v1'),))
    rid, = rules
    assert rid not in T.PRODUCTION_RULES and rid not in L.REGISTERED_RULES and rid not in D.production_registry()
    at = raw['outcome']['observed_ms'] + 10
    before = consume()
    # registered rule dispatch -> proposal queue
    receipts = D.dispatch_pending(j, cfg, at_ms=at, test_registry=rules)
    matched = [r for rec in receipts for r in rec['results'] if r['result'] == 'PROPOSED']
    assert len(matched) == 1 and matched[0]['rule_id'] == rid and matched[0]['target'] == target.value
    queued = j.query('SELECT proposal_id FROM learning_runtime_proposals')
    assert [q['proposal_id'] for q in queued] == [matched[0]['proposal_id']]
    # LearningApplication checkpoint -> owner (no handcrafted target write)
    applied = D.checkpoint_isolated(j, cfg, rules, at_ms=at, max_work=8)
    assert [a['result'] for a in applied] == ['APPLIED']
    assert T.read(j, target, context)['revision'] == 1
    # changed future consumer behaviour
    assert consume() != before
    # exact-context only: any other context is untouched
    for field in context.__dataclass_fields__:
        assert T.read(j, target, replace(context, **{field: 'different-exact-context'}))['value'] is None
    # nothing else moved
    assert not j.query('SELECT * FROM trades')


def test_normal_runtime_cannot_load_test_rules_or_apply_them(loop):
    j, d, snap, cfg, receipt, proposal, ai, intent, v = loop
    ctx = T.Context('A', '4h', 'regime', 'signal', 'LONG', 'family', 'exact')
    rules = make_rule(L.Target.WEIGHTS, ctx, {'reliability': .5})
    # a production-marked journal refuses any supplied registry
    prod = Journal(j.db_path)
    for call in (lambda: D.dispatch_pending(prod, cfg, at_ms=1, test_registry=rules),
                 lambda: D.checkpoint_isolated(prod, cfg, rules, at_ms=1)):
        with pytest.raises(ValueError, match='isolated_test_registry_required'):
            call()
    # a journal holding live trades refuses it even if marked test-only
    with j._tx() as c:
        c.execute("INSERT INTO trades(id,decision_id,symbol,side,amount,entry_price,opened_at,status,exec_mode)"
                  " VALUES('live','x','S','long',1,1,'t','open','live')")
    with pytest.raises(ValueError, match='live_test_registry_forbidden'):
        D.dispatch_pending(j, cfg, at_ms=1, test_registry=rules)
    # the Kernel path takes no registry argument at all
    import inspect
    from trader import kernel
    assert 'test_registry' not in inspect.getsource(kernel)
    assert inspect.signature(D.production_registry).parameters == {}


# ----------------------------------------------------- 14. idempotency
@pytest.fixture
def queued(loop):
    j, d, snap, cfg, receipt, proposal, ai, intent, v = loop
    ctx = K.evidence_context('BTC', '4h', 'regime', 'stub', Side.LONG.value, 'analyst', 'stub')
    rules = make_rule(L.Target.WEIGHTS, ctx, {'reliability': .5}, required_sources=(('risk_config', 'prospective-source.v1'),))
    oid = forward_outcome(j, d, ai)
    raw, ev = evidence_of(j, oid)
    at = raw['outcome']['observed_ms'] + 10
    D.dispatch_pending(j, cfg, at_ms=at, test_registry=rules)
    return j, cfg, ev, rules, ctx, at, oid, (d, ai)


def test_duplicate_outcome_and_proposal_never_duplicate_application(queued):
    j, cfg, ev, rules, ctx, at, oid, (d, ai) = queued
    # same outcome again: idempotent attach, idempotent dispatch, one proposal
    assert forward_outcome(j, d, ai) == oid
    again = D.dispatch_pending(j, cfg, at_ms=at + 1, test_registry=rules)
    assert again == []                                             # already receipted for this registry
    receipt = D.dispatch_evidence(j, cfg, ev, at_ms=at + 2, registry=rules)
    assert receipt['duplicate'] and len(j.query('SELECT * FROM learning_runtime_proposals')) == 1
    first = D.checkpoint_isolated(j, cfg, rules, at_ms=at)
    assert [r['result'] for r in first] == ['APPLIED']
    # duplicate proposal enqueue -> same row; duplicate application -> same receipt
    body = j.query('SELECT * FROM learning_runtime_proposals')[0]
    e, p, req = R.decode(body)
    assert R.enqueue(j, e, p, req) == p.proposal_id and len(j.query('SELECT * FROM learning_runtime_proposals')) == 1
    assert A.apply(j, cfg, e, p, req, at_ms=at + 3, test_registry=rules) == first[0]
    assert D.checkpoint_isolated(j, cfg, rules, at_ms=at + 4) == []
    assert len(j.query('SELECT * FROM learning_application_receipts')) == 1
    assert T.read(j, L.Target.WEIGHTS, ctx)['revision'] == 1


def test_restart_resumes_the_queue_exactly_once(queued):
    j, cfg, ev, rules, ctx, at, oid, _ = queued
    reopened = Journal(j.db_path)
    reopened.learning_test_only = True
    assert [r['result'] for r in D.checkpoint_isolated(reopened, cfg, rules, at_ms=at)] == ['APPLIED']
    again = Journal(j.db_path)
    again.learning_test_only = True
    assert D.checkpoint_isolated(again, cfg, rules, at_ms=at + 1) == []
    assert D.dispatch_pending(again, cfg, at_ms=at + 1, test_registry=rules) == []
    assert T.read(again, L.Target.WEIGHTS, ctx)['revision'] == 1


def test_stale_target_refuses(queued):
    j, cfg, ev, rules, ctx, at, oid, _ = queued
    other = make_rule(L.Target.WEIGHTS, ctx, {'reliability': .9}, suffix='.other')
    state = T.read(j, L.Target.WEIGHTS, ctx)
    p = T.propose(ev, L.Target.WEIGHTS, state, 'test-only.EVIDENCE_WEIGHTS.other', other)
    assert A.apply(j, cfg, ev, p, A.request(ev, p, state, A.source_versions(cfg)), at_ms=at, test_registry=other)['result'] == 'APPLIED'
    results = D.checkpoint_isolated(j, cfg, {**rules, **other}, at_ms=at)
    assert [r['result'] for r in results] == ['CONFLICT']
    assert T.read(j, L.Target.WEIGHTS, ctx)['value'] == {'reliability': .9}      # the queued proposal did not mutate


def test_changed_rule_version_invalidates_the_queued_proposal(queued):
    j, cfg, ev, rules, ctx, at, oid, _ = queued
    rid, rule = next(iter(rules.items()))
    revised = {rid: replace(rule, version='test-v2')}
    results = D.checkpoint_isolated(j, cfg, revised, at_ms=at)
    assert [r['result'] for r in results] == ['STALE']
    assert T.read(j, L.Target.WEIGHTS, ctx)['revision'] == 0
    # and the revised registry is dispatched afresh: a new registry digest, not a duplicate proposal
    assert D.registry_digest(revised) != D.registry_digest(rules)
    new = D.dispatch_pending(j, cfg, at_ms=at + 1, test_registry=revised)
    assert new and new[0]['results'][0]['rule_version'] == 'test-v2'


def test_source_hash_and_policy_mismatch_refuse(queued):
    j, cfg, ev, rules, ctx, at, oid, _ = queued
    row = j.query('SELECT * FROM learning_runtime_proposals')[0]
    e, p, req = R.decode(row)
    # tampered retained evidence -> replay refuses
    tampered_history = json.loads(e.historical_json)
    tampered_history['capture_manifest']['capture']['observation']['returns'] = {'fwd_ret_1h': 99}
    bad = replace(e, historical_json=json.dumps(tampered_history, sort_keys=True, separators=(',', ':')))
    refused = A.apply(j, cfg, bad, p, req, at_ms=at, shadow=True, test_registry=rules)
    assert refused['result'] in ('INCOMPLETE_REPLAY', 'CONFLICT')
    # changed governing policy -> source version mismatch
    changed = deepcopy(cfg)
    changed['risk']['__changed__'] = 1
    stale = D.checkpoint_isolated(j, changed, rules, at_ms=at)
    assert [r['result'] for r in stale] == ['STALE']
    assert T.read(j, L.Target.WEIGHTS, ctx)['revision'] == 0


# -------------------------------------------- 11. lifecycle regression
def test_recent_decay_still_retires_through_the_governor(isolated):
    j, cfg, ev, o, v = isolated
    data = json.loads(ev.historical_json)['data']
    frames = {s: pd.DataFrame(rows) for s, rows in data['frames'].items()}
    for frame in frames.values():
        frame['ts'] = pd.to_datetime(frame['ts'], utc=True)
    evidence, proposal, req = R.recent_decay(j, cfg, v['version_id'], frames, cutoff_ms=o.decision_ms, observed_ms=o.observed_ms)
    assert proposal.rule == L.DECAY_RULE and proposal.status == L.Status.APPLICABLE
    # the new dispatcher does not touch lifecycle: production adaptive registry is empty
    assert D.dispatch_evidence(j, cfg, evidence, at_ms=o.observed_ms, shadow=True)['results'][0]['reason'] == 'UNREGISTERED'
    results = R.checkpoint(j, cfg, at_ms=o.observed_ms, max_work=1)
    assert [r['result'] for r in results] == ['APPLIED']
    assert F.state_of(j, v['version_id']) == F.RETIRED
    assert R.checkpoint(Journal(j.db_path), cfg, at_ms=o.observed_ms + 1) == []
    assert len(F.governor_events(j, v['version_id'])) == 1


# ------------------------------- 6/7. contextual metadata & consumption
def test_normal_analyst_votes_carry_only_truthful_context(tmp_path):
    from trader.agents.momentum import MomentumAnalyst, ValueAnalyst, RotationAnalyst
    from trader.agents.flow import FlowAnalyst
    from trader.agents.structure import StructureAnalyst
    from trader.agents.positioning import PositioningAnalyst
    from trader.agents.orderbook_depth import DepthScout
    declared = {a.name: a.evidence_timeframe for a in (MomentumAnalyst(), ValueAnalyst(), RotationAnalyst(),
                FlowAnalyst(), StructureAnalyst(), PositioningAnalyst(), DepthScout())}
    assert declared == {'momentum': '15m', 'value': '15m', 'rotation': '1h', 'flow': '15m',
                        'structure': '15m', 'positioning': '1h', 'depth': None}
    j = Journal(tmp_path / 'o.db')
    d = analyst_orchestrator(j).decide(_snap(), population=[], entry_allowed=True)
    meta = d.votes[0]['meta']
    regime = regime_of(_snap())
    assert meta['horizon'] == '15m'
    ctx = meta['learning_context']
    assert ctx['status'] == 'EXACT' and (ctx['instrument'], ctx['horizon'], ctx['regime'], ctx['evidence_type'],
        ctx['direction'], ctx['strategy_family'], ctx['subject_id']) == ('ETH/USDT', '15m', regime, 'stub', 'long', 'analyst', 'stub')
    # genuinely unknown horizon: nothing fabricated, nothing governed
    unknown = analyst_orchestrator(j, timeframe=None).decide(_snap(), population=[], entry_allowed=True)
    assert 'horizon' not in unknown.votes[0]['meta']
    assert unknown.votes[0]['meta']['learning_context'] == dict(status='UNAVAILABLE', reason='UNAVAILABLE:horizon')


def test_governed_state_reaches_net_score_through_the_normal_path_and_baseline_is_identical(isolated):
    j, cfg, ev, o, v = isolated
    baseline = analyst_orchestrator(j).decide(_snap(), population=[], entry_allowed=True)
    twin = analyst_orchestrator(Journal(j.db_path.parent / 'untouched.db')).decide(_snap(), population=[], entry_allowed=True)
    assert baseline.score == twin.score and baseline.confidence == twin.confidence   # no learned state: identical
    assert 'governed_confidence' not in baseline.votes[0]['meta'] and 'governed_evidence' not in baseline.votes[0]['meta']
    regime = regime_of(_snap())
    context = K.evidence_context('ETH/USDT', '15m', regime, 'stub', Side.LONG.value, 'analyst', 'stub')
    scores = {}
    for target, value in ((L.Target.CONFIDENCE, {'reliability': .5}), (L.Target.WEIGHTS, {'reliability': .25})):
        rules = make_rule(target, context, value, kinds=(L.Kind.RESEARCH,))
        rid, = rules
        state = T.read(j, target, context)
        p = T.propose(ev, target, state, rid, rules)
        assert A.apply(j, cfg, ev, p, A.request(ev, p, state, A.source_versions(cfg)), at_ms=o.observed_ms,
                       test_registry=rules)['result'] == 'APPLIED'
        d = analyst_orchestrator(j).decide(_snap(), population=[], entry_allowed=True)
        scores[target] = d.score
        meta = d.votes[0]['meta']
        key = 'governed_confidence' if target == L.Target.CONFIDENCE else 'governed_evidence'
        assert meta[key]['value'] == value and meta[key]['revision'] == 1
        # raw evidence preserved; only the projection differs
        assert d.votes[0]['confidence'] == .8 and d.votes[0]['conviction'] == .8
    assert baseline.score > scores[L.Target.CONFIDENCE] > scores[L.Target.WEIGHTS]
    # a different horizon/agent never inherits it
    other = analyst_orchestrator(j, timeframe='1h').decide(_snap(), population=[], entry_allowed=True)
    assert other.score == baseline.score
    assert 'governed_confidence' not in other.votes[0]['meta']
    unknown = analyst_orchestrator(j, timeframe=None).decide(_snap(), population=[], entry_allowed=True)
    assert unknown.score == baseline.score


# -------------------------------------- 8/9. WorldModel learned confidence
def world_fixture(as_of=200):
    from trader.world import (ClaimCollection, ClaimCoordinate, ClaimEvidenceRef, HierarchyNode, Horizon,
                              Observation, Quality, Scope, ScopeLevel, WorldClaim, WorldModel, WorldState)
    glob, asset = Scope(ScopeLevel.GLOBAL, 'world'), Scope(ScopeLevel.ASSET_CLASS, 'digital-assets')
    btc, eth = Scope(ScopeLevel.INSTRUMENT, 'BTC'), Scope(ScopeLevel.INSTRUMENT, 'ETH')
    nodes = (HierarchyNode(glob), HierarchyNode(asset, glob), HierarchyNode(btc, asset), HierarchyNode(eth, asset))
    obs = Observation('BTC', 100, 150, '1h', 'price', 1, 'test', 'bar-1', Quality.VALID, available_at_ms=120)
    claims = tuple(WorldClaim(ClaimCoordinate(scope, Horizon.INTRADAY, dim), as_of, 'weak', Quality.SUSPECT, conf,
                              {'reason': 'tentative'}, (ClaimEvidenceRef.from_observation(obs),), (), 'test', 'claim-v1')
                   for scope, dim, conf in ((btc, 'trend', .6), (btc, 'stress', None)))
    model = WorldModel(as_of, nodes, (WorldState('BTC', as_of, (obs,)),), tuple(Horizon), claims=ClaimCollection(as_of, claims))
    return model, btc, eth, Horizon.INTRADAY


def test_world_model_learned_confidence_is_a_separate_exact_overlay(isolated):
    j, cfg, ev, o, v = isolated
    model, btc, eth, horizon = world_fixture(as_of=o.observed_ms)
    frozen = (model.to_json(), model.model_id, [c.to_json() for c in model.claims.claims])
    context = lambda claim: K.claim_context(claim, regime='ranging', direction='LONG', family='family')
    base, = model.get_claims(btc, horizon, dimension='trend')
    # no learned state: the base claim, unchanged
    view = K.world_claims(j, model, btc, horizon, regime='ranging', direction='LONG', family='family', dimension='trend')[0]
    assert (view.effective_confidence, view.base_confidence, view.applied, view.reason) == (.6, .6, False, 'NO_LEARNED_STATE')
    assert view.claim is base
    rules = make_rule(L.Target.WORLD, context(base), {'confidence': .9}, kinds=(L.Kind.RESEARCH,))
    rid, = rules
    state = T.read(j, L.Target.WORLD, context(base))
    p = T.propose(ev, L.Target.WORLD, state, rid, rules)
    assert A.apply(j, cfg, ev, p, A.request(ev, p, state, A.source_versions(cfg)), at_ms=o.observed_ms,
                   test_registry=rules)['result'] == 'APPLIED'
    got = K.world_claims(j, model, btc, horizon, regime='ranging', direction='LONG', family='family', dimension='trend')[0]
    assert (got.base_confidence, got.effective_confidence, got.applied) == (.6, .9, True)
    assert got.learned['revision'] == 1 and got.reason == 'LEARNED_OVERLAY_APPLIED'
    # snapshot and base claims are immutable; the stored model never changed
    assert (model.to_json(), model.model_id, [c.to_json() for c in model.claims.claims]) == frozen
    assert model.get_claims(btc, horizon, dimension='trend')[0].confidence == .6
    # exact context only: no fallback across regime / direction / family / horizon / dimension / scope
    for kw in (dict(regime='trending'), dict(direction='SHORT'), dict(family='other')):
        args = dict(regime='ranging', direction='LONG', family='family', dimension='trend')
        args.update(kw)
        view = K.world_claims(j, model, btc, horizon, **args)[0]
        assert view.effective_confidence == .6 and not view.applied
    stress = K.world_claims(j, model, btc, horizon, regime='ranging', direction='LONG', family='family', dimension='stress')[0]
    assert stress.base_confidence is None and stress.effective_confidence is None and not stress.applied
    # a caller cannot point a context at a claim it does not describe
    wrong = model.effective_claims(btc, horizon, dimension='trend', learning_journal=j,
                                   context_for=lambda c: replace(context(c), horizon='structural'))[0]
    assert not wrong.applied and wrong.reason == 'CONTEXT_DOES_NOT_MATCH_CLAIM'
    unknown_regime = model.effective_claims(btc, horizon, dimension='trend', learning_journal=j,
                                            context_for=lambda c: K.claim_context(c, regime='UNKNOWN', direction='LONG', family='f'))[0]
    assert not unknown_regime.applied and unknown_regime.reason == 'CONTEXT_UNAVAILABLE'
    assert model.effective_claims(eth, horizon, learning_journal=j, context_for=context) == ()


# ------------------------------------------ 15/16. legacy + safety
def test_legacy_adaptation_stays_explicitly_isolated(isolated):
    j, cfg, ev, o, v = isolated
    from trader.strategy.blend import strategy_weights
    from trader.engine.orchestrator import Orchestrator
    orch = Orchestrator([], j)
    assert orch._accuracy_multipliers() == {}
    assert orch._adaptive_base('ranging') == orch.base_threshold
    assert strategy_weights(j, [], 'ranging') == {}
    # recorded outcomes never trigger a recalculation: the reads only see explicitly frozen legacy state
    import inspect
    for fn in (Orchestrator._accuracy_multipliers, Orchestrator._adaptive_base):
        src = inspect.getsource(fn)
        assert 'legacy_frozen_' in src and '.query(' not in src and 'SELECT' not in src
    j.kv_set('legacy_frozen_adaptive_base', json.dumps({'ranging': .31}))
    assert Orchestrator([], j)._adaptive_base('ranging') == .31   # explicit frozen snapshot only
    for consumer in ('strategy_weights', 'adaptive_base', 'accuracy_multipliers'):
        with pytest.raises(ValueError, match='replay_complete_learning_authority_required:' + consumer):
            L.refuse_legacy_learning(consumer)
    # no new bypass: nothing in the learning package imports a legacy adaptive writer
    import ast
    for path in sorted((Path(D.__file__).parent).glob('*.py')):
        for node in ast.walk(ast.parse(path.read_text())):
            mods = ([a.name for a in node.names] if isinstance(node, ast.Import)
                    else [node.module or ''] + [a.name for a in node.names] if isinstance(node, ast.ImportFrom) else [])
            for mod in mods:
                assert not any(w in mod for w in ('weights_online', 'agents.calibration', 'meta_label', 'strategy.blend')), (path.name, mod)


def test_learning_modules_cannot_order_size_activate_or_touch_a_venue():
    import ast
    root = Path(__file__).resolve().parents[1] / 'trader' / 'learning'
    banned_modules = ('executor', 'ccxt', 'requests', 'urllib', 'websocket', 'binance', 'engine.risk', 'engine.exits')
    banned_calls = {'create_order', 'place_order', 'submit_order', 'cancel_order', 'check_entry', 'open', 'upsert_spec',
                    'set_state', 'activate', 'install_version', 'approve_version'}
    for path in sorted(root.glob('*.py')):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [a.name for a in node.names] + ([node.module] if isinstance(node, ast.ImportFrom) and node.module else [])
                for name in names:
                    assert not any(b in name.lower() for b in banned_modules), (path.name, name)
            if isinstance(node, ast.Call):
                fn = node.func
                called = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, 'id', '')
                if called == 'open':      # reading retained files is fine; writing is not
                    continue
                assert called not in banned_calls, (path.name, called)
                # the only lifecycle mutation is the existing Strategy Governor, from the application authority
                if called == 'govern_version':
                    assert path.name in ('application.py', 'foundation.py'), path.name
    # the Kernel reaches dispatch only through the proposal queue; dispatch never applies or sizes
    dispatch = (root / 'dispatch.py').read_text()
    for forbidden in ('A.apply', 'compare_and_apply', 'govern_version', 'risk', 'size_usdt'):
        assert forbidden not in dispatch, forbidden
