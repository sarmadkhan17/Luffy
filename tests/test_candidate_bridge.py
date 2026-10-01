"""Exact factory fixtures are synthetic; shadow never manufactures versions."""
from dataclasses import replace
import json
import sqlite3
import pytest

from trader.portfolio import candidate_bridge as B, opportunity_live as L, economics as E
from trader.portfolio.allocator import Source, Evidence, Status, Inputs, Portfolio, allocate, canonical, inputs_from_payload
from trader.strategy import factory_handoff as F
from trader.strategy.signal_occurrence import spec_fingerprint
from trader.strategy.spec import StrategySpec
from trader.core.config import load_config
from tests.test_strategy_factory_handoff import _journal, T0, INPUTS
from tests.test_opportunity_live_integration import snapshot, IID, SYMBOL
from scripts.opportunity_context_shadow import DIMENSIONS


@pytest.fixture
def exact(tmp_path):
    j, h = _journal(tmp_path)
    cfg = load_config()
    v = F.create_version(j, cfg, {'kind': 'research_candidate', 'hash': h}, at_ms=T0)
    v = F.load_version(j, v['version_id'])
    row = dict(j.query('SELECT * FROM strategy_versions WHERE version_id=?', (v['version_id'],))[0])
    sig = dict(symbol=SYMBOL, action='BUY', params=dict(spec_id=v['strategy_id'],
        spec_fingerprint=spec_fingerprint(StrategySpec.from_dict(v['spec'])),
        signal_timeframe=v['spec']['timeframe'], signal_bar_close_ms=T0))
    args = dict(as_of_ms=T0+100, symbol=SYMBOL, instrument_id=IID, cycle_id='test-cycle', candidate_id='test-setup',
        sources=(L.source('strategy_version', row, T0, T0+1000), L.source('signals', [sig], T0, T0+1000)),
        required_roles=('strategy_version', 'signals'))
    return j, cfg, v, args


def build(exact, args=None):
    j, cfg, v, original = exact
    return B.build(j, L.produce(**(args or original)), cfg, available_inputs=INPUTS, dimensions=DIMENSIONS)


def test_exact_comparison_without_first_live(exact):
    j, cfg, v, args = exact
    before = F.eligible_for_first_live(j, v['version_id'], cfg=cfg)
    c, er, sources = build(exact)
    assert c.validation.status == Status.ESTABLISHED
    assert c.probation.status == Status.UNKNOWN
    assert c.capacity.status == Status.UNKNOWN
    authority = next(s for s in sources if s.source_id.startswith('candidate-bridge:'))
    a = json.loads(authority.payload_json)['result']
    assert a['status'] == B.ELIGIBLE and a['exit_semantics_id']
    assert a['lifecycle_state'] == F.VALIDATED
    assert F.eligible_for_first_live(j, v['version_id'], cfg=cfg) == before
    assert not before.eligible
    assert json.loads(er.result_json)['economic_status'] == 'UNAVAILABLE'
    assert B.verify_current(c, L.produce(**args), er, j, cfg, INPUTS, T0+200, current_sources=args['sources'])


@pytest.mark.parametrize('kind', ['version', 'hash', 'instrument', 'direction', 'horizon'])
def test_exact_mismatch_refused(exact, kind):
    j, cfg, v, args = exact
    sources = list(args['sources'])
    if kind in ('version', 'hash'):
        raw = json.loads(sources[0].payload_json)['data']
        raw['version_id' if kind == 'version' else 'spec_hash'] = 'wrong'
        sources[0] = L.source('strategy_version', raw, T0, T0+1000)
    else:
        raw = json.loads(sources[1].payload_json)['data']
        if kind == 'instrument': raw[0]['symbol'] = 'ETH/USDT:USDT'
        if kind == 'direction': raw[0]['action'] = 'FLAT'
        if kind == 'horizon': raw[0]['params']['signal_timeframe'] = '1h'
        sources[1] = L.source('signals', raw, T0, T0+1000)
    with pytest.raises(ValueError):
        build(exact, {**args, 'sources': sources})


def test_absent_context_and_required_inputs(exact):
    j, cfg, _, args = exact
    assert B.build(j, None, cfg) is None
    with pytest.raises(ValueError, match='INPUTS_UNAVAILABLE'):
        B.build(j, L.produce(**args), cfg, dimensions=DIMENSIONS)


def test_retired_version_and_current_reread(exact):
    j, cfg, v, args = exact
    c, er, _ = build(exact)
    F.retire_version(j, v['version_id'], F.RETIRED, reason_code='test', actor='factory', at_ms=T0+110)
    with pytest.raises(ValueError, match='NOT_VALID_FOR_COMPARISON'):
        build(exact)
    with pytest.raises(ValueError):
        B.verify_current(c, L.produce(**args), er, j, cfg, INPUTS, T0+200, current_sources=args['sources'])


def test_existing_feasibility_and_replay(exact):
    c, er, sources = build(exact)
    known = Evidence(Status.ESTABLISHED, (sources[0].source_id,))
    portfolio = Portfolio('test-empty-book', T0, T0+1000, known, (), (sources[0].source_id,))
    ai = Inputs(T0+100, (c,), portfolio, known, known, 'ACTIVE', sources)
    proposal = allocate(ai)
    row = json.loads(proposal.result_json)['candidates'][0]
    assert row['feasibility'] == 'BLOCKED'
    assert 'PORTFOLIO_EXPOSURE_AUTHORITY_UNAVAILABLE' in row['refusal_reasons']
    assert 'EXPECTED_ECONOMICS_UNAVAILABLE' in row['refusal_reasons']
    assert 'CAPACITY_UNAVAILABLE' in row['refusal_reasons']
    assert 'EXPECTED_ECONOMICS_RECEIPT_REFUSED' not in row['refusal_reasons']
    assert allocate(inputs_from_payload(json.loads(proposal.inputs_json))) == proposal
    bad = replace(c, validation=Evidence(Status.UNKNOWN, (sources[0].source_id,)))
    assert not B.verify_candidate(bad, L.produce(**exact[3]), er)


def test_deterministic_id_and_changed_cut(exact):
    c, er, _ = build(exact)
    assert build(exact)[0].candidate_id == c.candidate_id
    assert build(exact, {**exact[3], 'as_of_ms': T0+200})[0].candidate_id != c.candidate_id
    changed = replace(c, version_id='other')
    assert changed.candidate_id != c.candidate_id
    changed_er = E.build(replace(E.from_inputs(json.loads(er.inputs_json)), context=(*E.from_inputs(json.loads(er.inputs_json)).context, Source.freeze('extra-evidence', {'test_only': True}))))
    changed, _ = B.candidate(L.produce(**exact[3]), changed_er, next(s for s in E.from_inputs(json.loads(er.inputs_json)).context if s.source_id.startswith('candidate-bridge:')))
    assert changed.candidate_id != c.candidate_id


@pytest.mark.parametrize('side,expected', [('long', 'SUPPORTS_EXISTING'), ('short', 'CONFLICTS_EXISTING'), (None, 'NO_ACTION')])
def test_position_binding(exact, side, expected):
    args = exact[3]
    args = {**args, 'sources': (*args['sources'], L.source('portfolio', snapshot(side, T0+100), T0+100, T0+1000))}
    c, er, _ = build(exact, args)
    assert json.loads(c.existing_position_interaction.detail_json)['interaction'] == expected
    assert c.bounds == ()


def test_read_only_authorities(exact):
    j, _, _, _ = exact
    before = {t: j.query('SELECT * FROM ' + t) for t in ('strategy_version_events', 'state_kv', 'trades')}
    build(exact)
    assert before == {t: j.query('SELECT * FROM ' + t) for t in before}
    with pytest.raises(ValueError, match='WRITE_REFUSED'):
        B.ReadEvidence(j).query('UPDATE state_kv SET value=1')


def test_factory_bridge_only_reads_existing_authorities():
    import ast
    from pathlib import Path
    tree = ast.parse(Path('trader/portfolio/candidate_bridge.py').read_text())
    calls = {n.func.attr for n in ast.walk(tree) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Attribute) and isinstance(n.func.value, ast.Name)
             and n.func.value.id == 'factory'}
    assert calls <= {'load_version', 'state_of', 'verify_validation', '_load',
                     '_verify_probation', 'approval_request'}
    assert not any(n.module in ('trader.kernel', 'trader.engine.executor', 'trader.engine.risk',
                               'trader.engine.control_fence') for n in ast.walk(tree) if isinstance(n, ast.ImportFrom))


def test_zero_inventory_read_only_shadow(tmp_path, monkeypatch):
    from scripts import opportunity_context_shadow as S
    from trader.core.journal import Journal
    from trader.engine.evidence_capture import record_snapshot
    j = Journal(tmp_path / 'inputs' / 'journal.db')
    record_snapshot(j, snapshot(cut=1000), at_ms=1000)
    j.kv_set('control_state', 'FROZEN')
    before = j.query('SELECT * FROM state_kv')
    monkeypatch.setattr(S.time, 'time_ns', lambda: 1000 * 1000000)
    original = S._read
    mutations = []
    def readonly(*args):
        db = original(*args)
        def authorize(action, a, b, c, d):
            if action in (sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE):
                mutations.append(action)
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK
        db.set_authorizer(authorize)
        return db
    monkeypatch.setattr(S, '_read', readonly)
    result = S.run(tmp_path / 'inputs' / 'journal.db', tmp_path / 'inputs' / 'attention.db',
                   tmp_path / 'inputs' / 'investigation.db', {}, tmp_path / 'artifacts', candidate_bridge=True)
    assert result['status'] == 'PASS' and result['version_count'] == 0
    assert result['candidate_count'] == 0 and result['decision'] == 'NO_ALLOCATION'
    assert not mutations and j.query('SELECT * FROM state_kv') == before


def test_context_without_factory_version_refused(exact):
    args = {**exact[3], 'sources': exact[3]['sources'][1:], 'required_roles': ('signals',)}
    with pytest.raises(ValueError, match='STRATEGY_VERSION_UNAVAILABLE'):
        build(exact, args)


def test_stale_context_and_changed_economics_refused(exact):
    c, er, _ = build(exact)
    j, cfg, _, args = exact
    with pytest.raises(ValueError, match='STALE'):
        B.verify_current(c, L.produce(**args), er, j, cfg, INPUTS, T0+1001, current_sources=args['sources'])
    bad = replace(E.from_inputs(json.loads(er.inputs_json)).binding, spec_hash='other')
    forged = E.build(E.Inputs(bad, context=E.from_inputs(json.loads(er.inputs_json)).context))
    authority = next(s for s in E.from_inputs(json.loads(er.inputs_json)).context if s.source_id.startswith('candidate-bridge:'))
    with pytest.raises(ValueError):
        B.candidate(L.produce(**args), forged, authority)


def test_capacity_receipt_unavailable_reuses_existing_gate(exact):
    j, cfg, v, _ = exact
    F.evaluate_capacity(j, cfg, v['version_id'], instrument_id=IID, market_type='futures',
                        as_of_ms=T0, at_ms=T0)
    c, er, sources = build(exact)
    assert c.capacity.status == Status.UNAVAILABLE
    detail = json.loads(c.capacity.detail_json)
    assert not detail['current'] and detail['dimensions']['account']['funding']['status'] == 'UNAVAILABLE'
    assert c.bounds == ()


def test_changed_frozen_version_requires_own_validation(exact):
    j, cfg, v, args = exact
    spec = StrategySpec.from_dict(v['spec'])
    spec.entry_long = 'close > ema(30)'
    derived = F.derive_version(j, v['version_id'], spec, at_ms=T0)
    assert derived['version_id'] != v['version_id']
    row = dict(j.query('SELECT * FROM strategy_versions WHERE version_id=?', (derived['version_id'],))[0])
    spec = StrategySpec.from_dict(F.load_version(j, derived['version_id'])['spec'])
    signals = json.loads(args['sources'][1].payload_json)['data']
    signals[0]['params']['spec_fingerprint'] = spec_fingerprint(spec)
    changed = {**args, 'sources': (L.source('strategy_version', row, T0, T0+1000), L.source('signals', signals, T0, T0+1000))}
    assert L.produce(**changed).receipt_id != L.produce(**args).receipt_id
    with pytest.raises(ValueError, match='NOT_VALID_FOR_COMPARISON'):
        build(exact, changed)


def test_restart_replay(exact, tmp_path):
    from dataclasses import asdict
    import subprocess
    import sys
    c, er, sources = build(exact)
    known = Evidence(Status.ESTABLISHED, (sources[0].source_id,))
    ai = Inputs(T0+100, (c,), Portfolio('test-book', T0, T0+1000, known, (), (sources[0].source_id,)), known, known, 'ACTIVE', sources)
    proposal = allocate(ai)
    path = tmp_path / 'proposal.json'
    path.write_text(canonical(asdict(proposal)))
    code = """import json,sys
from pathlib import Path
from trader.portfolio.allocator import allocate,inputs_from_payload
p=json.loads(Path(sys.argv[1]).read_text())
assert allocate(inputs_from_payload(json.loads(p['inputs_json']))).proposal_id == p['proposal_id']
print('PASS')
"""
    assert subprocess.run([sys.executable, '-c', code, str(path)], check=True, text=True, capture_output=True).stdout.strip() == 'PASS'


@pytest.mark.parametrize('field,value', [('instrument', 'binance_usdm:futures:ETHUSDT'), ('direction', 'SHORT'), ('horizon', '1h')])
def test_wrong_economic_match_refused(exact, field, value):
    _, er, _ = build(exact)
    ei = E.from_inputs(json.loads(er.inputs_json))
    changed = E.build(replace(ei, binding=replace(ei.binding, **{field: value})))
    authority = next(s for s in ei.context if s.source_id.startswith('candidate-bridge:'))
    with pytest.raises(ValueError):
        B.candidate(L.produce(**exact[3]), changed, authority)


def test_nonempty_shadow_uses_bridge_and_sql_write_denial(exact, tmp_path, monkeypatch):
    from scripts import opportunity_context_shadow as S
    j, cfg, v, args = exact
    inventory = Source.freeze('test-inventory', {'test_only': True})
    detail = dict(as_of_ms=T0+100, version_count=1, read_only=True, production_mutations=0)
    monkeypatch.setattr(S, 'capture', lambda *a: ([args], snapshot(cut=T0+100), 'FROZEN', inventory, detail))
    mutations = []
    original = S._read
    def readonly(*a):
        db = original(*a)
        def authorize(action, a, b, c, d):
            if action in (sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE):
                mutations.append(action)
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK
        db.set_authorizer(authorize)
        return db
    monkeypatch.setattr(S, '_read', readonly)
    result = S.run(tmp_path / 'j.db', tmp_path / 'absent-attention.db', tmp_path / 'absent-investigation.db',
                   cfg, tmp_path.parent / (tmp_path.name + '-artifacts'), candidate_bridge=True, available_inputs=INPUTS)
    assert result['status'] == 'PASS' and result['candidate_count'] == 1
    assert not mutations and result['decision'] == 'NO_ALLOCATION'
    assert result['contexts'][0]['candidate_created']


def test_unverified_probation_does_not_require_first_live(exact, monkeypatch):
    j, cfg, v, args = exact
    original = j.query
    rec = dict(version_id=v['version_id'], spec_hash=v['spec_hash'],
               status=F.P_SATISFIED, receipt_id='test-only-unverified-probation')
    row = dict(canonical_json=canonical(rec), canonical_sha256=F._sha(canonical(rec)))
    def query(sql, params=()):
        if sql.startswith('SELECT * FROM strategy_probation_receipts WHERE version_id='):
            return [row]
        return original(sql, params)
    monkeypatch.setattr(j, 'query', query)
    def unavailable(*a):
        raise F.HandoffRefused('probation_policy_changed')
    monkeypatch.setattr(F, '_verify_probation', unavailable)
    c, er, _ = build(exact)
    assert c.validation.status == Status.ESTABLISHED
    assert c.probation.status == Status.UNKNOWN
    assert json.loads(c.probation.detail_json)['reason'] == 'probation_policy_changed'
