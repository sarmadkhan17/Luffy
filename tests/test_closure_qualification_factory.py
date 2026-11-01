"""P3b/P6: frozen candidate -> independent protected look -> VALIDATED only.

FIXTURES (labelled in tests/closure_fixtures.py): the referee READING is scripted
and the calibration APPROVAL is a hand-made report; both drive the real qualify()
bookkeeping, cumulative LORD++ ledger, exposure registry, verifier and Factory
creation, and neither is evidence of an edge. Real-data qualification is
separately blocked until provenance, exposure, gates, book and approval exist."""
import copy
import json

import pytest

from tests.closure_fixtures import (approval, build, fixture_freeze, gated_cfg, incumbent_book,
                                    scripted_referee, source_journal)
from trader.core.config import load_config
from trader.core.journal import Journal
from trader.research import fdr
from trader.research.closure import ClosureRefused, digest, exposure, handoff, manifest, qualification as Q
from trader.strategy import factory_handoff as fh


def n(j, table):
    return len(j.query(f'SELECT * FROM {table}')) if j.query('SELECT 1 FROM sqlite_master WHERE name=?', (table,)) else 0


def authority(tmp, b):
    appr = approval(tmp, b.env)
    shared = str(source_journal(tmp))           # the ONE journal every runner spends in (shared reservation)
    return dict(cfg=gated_cfg(appr), book=incumbent_book(), source_journal=shared, budget_journal=shared)


@pytest.fixture(scope='module')
def good(tmp_path_factory):
    tmp = tmp_path_factory.mktemp('qual')
    b = build(tmp, name='q', klass='CONFIGURED', seal='FORWARD')
    assert b.env['protected_status'] == 'ELIGIBLE' and b.env['policy_class'] == 'CONFIGURED'
    b.freeze, b.calls, b.tmp, b.auth = fixture_freeze(b), [], tmp, authority(tmp, b)
    b.packet = Q.qualify(b.store, b.freeze, b.env, b.snap, tmp / 'work', now_ms=5, run=scripted_referee(True, b.calls), **b.auth)
    return b


def refusal(packet, cfg):
    try:
        fh._verified_historical(cfg, packet)
    except fh.HandoffRefused as e:
        return e.code
    return None


def test_qualified_packet_comes_from_the_real_registered_look(good):
    p = good.packet
    assert p['status'] == 'QUALIFIED' and len(good.calls) == 1 and p['gate3_book']['kind'] == 'DECLARED_INCUMBENT'
    assert p['source_sequence_id'] and Q.verify_qualification(p) is p
    look, = [t for t in p['ledger']['tests'] if t['gate'] == 'gate1']
    d = json.loads(look['detail'])
    assert look['rejected'] and look['p'] <= look['alpha_t'] and d['envelope_id'] == good.env['envelope_id']
    assert p['ledger']['budget']['alpha'] == good.env['policy']['fdr_target']


def test_recall_is_idempotent_and_one_look_per_protected_window(good):
    again = Q.qualify(good.store, good.freeze, good.env, good.snap, good.tmp / 'work', now_ms=6,
                      run=scripted_referee(True, good.calls), **good.auth)
    assert again == good.packet and len(good.calls) == 1
    other = fixture_freeze(good, 'trend_breakout.v1', dict(channel=20, trend='st:above_ema50', geo='trail'))
    p2 = Q.qualify(good.store, other, good.env, good.snap, good.tmp / 'work2', now_ms=7,
                   run=scripted_referee(True, good.calls), **good.auth)
    assert p2['status'] == 'BLOCKED' and 'already_looked' in p2['reason'] and len(good.calls) == 1
    assert any(e.get('state') == 'PROTECTED_EXPOSED' for e in exposure.events(good.store))


def test_closed_production_gates_block_configured_qualification_without_spending(tmp_path):
    b = build(tmp_path, name='gate', klass='CONFIGURED', seal='FORWARD')
    f = fixture_freeze(b)
    auth = authority(tmp_path, b)
    calls = []
    closed = load_config()
    closed['research'].update(referee=False,handoff=False)
    for cfg, want in ((closed, 'production_referee_gate_closed'),
                      (gated_cfg(None), 'calibration_not_approved'),
                      (gated_cfg(approval(tmp_path, b.env, calibration_status='INCONCLUSIVE')), 'calibration_not_approved'),
                      (gated_cfg(approval(tmp_path, b.env, numeric_id='other')), 'calibration_not_approved'),
                      (gated_cfg(approval(tmp_path, b.env, evaluator_id='stale')), 'calibration_not_approved')):
        p = Q.qualify(b.store, f, b.env, b.snap, tmp_path / 'w', now_ms=1, run=scripted_referee(True, calls),
                      **dict(auth, cfg=cfg))
        assert p['status'] == 'BLOCKED' and want in p['reason'], (want, p['reason'])
    separate = Q.qualify(b.store, f, b.env, b.snap, tmp_path / 'w', now_ms=1, run=scripted_referee(True, calls),
                         **dict(auth, budget_journal=str(tmp_path / 'independent-copy.db')))
    assert separate['status'] == 'BLOCKED' and 'shared_budget_reservation_unavailable' in separate['reason']
    for missing in ('book', 'source_journal', 'budget_journal'):
        p = Q.qualify(b.store, f, b.env, b.snap, tmp_path / 'w', now_ms=1, run=scripted_referee(True, calls),
                      **dict(auth, **{missing: None}))
        assert p['status'] == 'BLOCKED' and 'authority_blocked' in p['reason']
    assert not calls and b.store.get_once('looks', b.env['envelope_id']) is None
    assert b.store.all_once('qualifications') == []                      # not sticky: the gates may open later
    p = Q.qualify(b.store, f, b.env, b.snap, tmp_path / 'w', now_ms=2, run=scripted_referee(True, calls), **auth)
    assert p['status'] == 'QUALIFIED' and len(calls) == 1, p['reason']


def test_cumulative_budget_across_studies_and_an_independent_runner(good, tmp_path):
    """Study 2 (new data/window identity) and an unrelated runner spending in the SAME
    journal continue ONE sequence; the ledger's own BEGIN IMMEDIATE is the reservation."""
    from trader.research.ledger import Ledger
    shared = good.auth['budget_journal']
    other = Ledger(Journal(shared))                        # e.g. the legacy runner, a different process in reality
    t, alpha = other.next_alpha(.5, .4)
    other.record_test('legacy-candidate', '4h', 'fixed', 'gate1', 0.9, alpha, False,
                      dict(look_state='complete', cut_ms=good.env['split']['cut_ms']))
    b2 = build(tmp_path, name='q2', seed=11, klass='CONFIGURED', seal='FORWARD')
    assert b2.env['protected_fingerprint'] != good.env['protected_fingerprint']
    auth = dict(good.auth, cfg=gated_cfg(approval(tmp_path, b2.env)))
    p2 = Q.qualify(b2.store, fixture_freeze(b2), b2.env, b2.snap, tmp_path / 'w2', now_ms=1, run=scripted_referee(True), **auth)
    tests = p2['ledger']['tests']
    assert [t['seq'] for t in tests] == [1, 2, 3] and p2['status'] == 'QUALIFIED'
    pol = b2.env['policy']
    assert tests[2]['alpha_t'] == pytest.approx(fdr.alpha_at(3, [1], pol['fdr_target'], pol['lord_w0']))
    assert tests[2]['alpha_t'] != tests[0]['alpha_t']                       # alpha was NOT reset
    # a level above the registered ceiling for the NEXT index cannot be recorded (no silent over-spend)
    with pytest.raises(ValueError, match='unregistered_alpha'):
        other.record_test('late', '4h', 'fixed', 'gate1', 0.5, 0.9, False,
                          dict(look_state='complete', cut_ms=good.env['split']['cut_ms']))


def test_test_only_import_path_refuses_divergent_sources(good, tmp_path):
    """Independent (TEST_ONLY) copies: a source that disagrees is refused, never reset."""
    for name, edit, want in (('badbudget', 'budget', 'error_budget_differs'), ('diverged', 'prior', 'budget_sequence_diverged')):
        b = build(tmp_path, name=name, seed=13, seal='SYNTHETIC')
        canonical = tmp_path / f'{name}-canon.db'
        Q.qualify(b.store, fixture_freeze(b), b.env, b.snap, tmp_path / f'{name}-w0', now_ms=1,
                  run=scripted_referee(True), budget_journal=str(canonical))     # seq 1 in the independent canonical copy
        b2 = build(tmp_path, name=name + '2', seed=14, seal='SYNTHETIC')
        src = source_journal(tmp_path, f'{name}-src.db', prior=[(0.9, 0.0001, 1_000)] if edit == 'prior' else None)
        if edit == 'budget':
            with Journal(src)._tx() as c:
                c.execute('UPDATE research_budget SET alpha=0.3,w0=0.2 WHERE id=1')
        with pytest.raises(ClosureRefused, match=want):
            Q.qualify(b2.store, fixture_freeze(b2), b2.env, b2.snap, tmp_path / f'{name}-w', now_ms=1,
                      run=scripted_referee(True), source_journal=str(src), budget_journal=str(canonical))


def test_blocked_without_proven_exposure_takes_no_look(tmp_path):
    b = build(tmp_path, name='unk', seal=None)
    p = Q.qualify(b.store, fixture_freeze(b), b.env, b.snap, tmp_path / 'w', now_ms=1, run=scripted_referee(True))
    assert p['status'] == 'BLOCKED' and 'exposure' in p['reason'] and not (tmp_path / 'w' / 'qualification.db').exists()
    with pytest.raises(ClosureRefused):
        Q.verify_qualification(p)


def test_failed_reading_and_forged_status_label(tmp_path):
    b = build(tmp_path, name='fail', seal='SYNTHETIC')
    p = Q.qualify(b.store, fixture_freeze(b), b.env, b.snap, tmp_path / 'w', now_ms=1, run=scripted_referee(False))
    assert p['status'] == 'NOT_QUALIFIED'
    forged = dict(p, status='QUALIFIED')
    forged['evidence_id'] = digest({k: v for k, v in forged.items() if k != 'evidence_id'})
    with pytest.raises(ClosureRefused):
        Q.verify_qualification(forged)


def test_worker_failure_or_timeout_is_a_spent_failed_look_never_retaken(tmp_path):
    from types import SimpleNamespace
    b = build(tmp_path, name='to', seal='SYNTHETIC')
    f = fixture_freeze(b)
    calls = []
    def timed_out(fn, payload, **_):
        calls.append(1)
        return SimpleNamespace(ok=False, timed_out=True, error='timed out after 5s', value=None)
    p = Q.qualify(b.store, f, b.env, b.snap, tmp_path / 'w', now_ms=1, run=timed_out)
    assert p['status'] == 'INCONCLUSIVE' and 'failed look' in p['reason'] and len(calls) == 1
    assert Q.qualify(b.store, f, b.env, b.snap, tmp_path / 'w', now_ms=2, run=timed_out) == p and len(calls) == 1


def test_altered_evidence_is_refused(good):
    cfg = good.auth['cfg']
    assert refusal(good.packet, cfg) is None
    def tampered(edit):
        p = copy.deepcopy(good.packet)
        edit(p)
        p['evidence_id'] = digest({k: v for k, v in p.items() if k != 'evidence_id'})
        return p
    def gate(p):
        return next(t for t in p['ledger']['tests'] if t['gate'] == 'gate1')
    def lower_p(p): gate(p)['p'] = 1e-9
    def widen_alpha(p): gate(p)['alpha_t'] = 0.9
    def flip_state(p): p['ledger']['candidate']['state'] = 'gate1_fail'
    def other_window(p):
        d = json.loads(gate(p)['detail']); d['envelope_id'] = 'other'; gate(p)['detail'] = json.dumps(d)
    def drop_proof(p): p['envelope']['exposure_proofs'][p['envelope']['discovery_symbols'][0]] = None
    def empty_book(p): p['gate3_book'] = Q.BOOK; p['book'] = []
    def book_swap(p): p['book'] = p['book'][:0] + [{'id': 'other'}]
    def no_sequence(p): p['source_sequence_id'] = None
    for edit in (lower_p, widen_alpha, flip_state, other_window, drop_proof, empty_book, book_swap, no_sequence):
        assert refusal(tampered(edit), cfg), edit.__name__
    stale = copy.deepcopy(good.packet); stale['evidence_id'] = 'x'
    assert refusal(stale, cfg) == 'hq_qualification_integrity'
    assert refusal({'garbage': 1}, cfg) in ('hq_packet_malformed', 'hq_qualification_integrity')


def test_handoff_validated_only_idempotent_and_visible(good, tmp_path):
    cfg = good.auth['cfg']
    j = Journal(tmp_path / 'j.db')
    first = handoff.hand_off(j, cfg, good.packet, at_ms=1000)
    assert first['status'] == 'VALIDATED' and first['execution_authority'] == 'NONE' and first['transfer_status'] == 'inserted'
    second = handoff.hand_off(j, cfg, good.packet, at_ms=2000)
    assert second['outcome'] == 'duplicate' and second['version_id'] == first['version_id']
    assert fh.state_of(j, first['version_id']) == fh.VALIDATED
    v = fh.load_version(j, first['version_id'])
    assert v['source']['kind'] == fh.HQ_KIND and v['lineage']['research']['origin'] == 'historical_qualification'
    assert v['lineage']['research']['gate3_book']['kind'] == 'DECLARED_INCUMBENT'
    fh.verify_validation(j, v)
    assert len(j.query('SELECT * FROM strategy_versions')) == 1
    assert len(j.query("SELECT * FROM brain_events WHERE kind='research_factory_validated'")) == 1
    for table in ('orders', 'strategy_installs', 'strategy_install_receipts'):
        if j.query('SELECT 1 FROM sqlite_master WHERE name=?', (table,)):
            assert not j.query(f'SELECT 1 FROM {table}')
    from trader.dashboard.glacier_truth import versioned_strategies
    shown = versioned_strategies(j, cfg, 3.0)['versions']
    assert len(shown) == 1 and shown[0]['lifecycle'] == fh.VALIDATED and shown[0]['validation']
    assert shown[0]['install'] is None and shown[0]['errors'] == {'install': 'install_missing'}      # validated, NOT installed
    assert shown[0]['version']['lineage']['research']['origin'] == 'historical_qualification'


def test_interrupted_handoff_retries_without_duplicates(good, tmp_path, monkeypatch):
    cfg = good.auth['cfg']
    j = Journal(tmp_path / 'j.db')
    real = fh.create_version
    monkeypatch.setattr(fh, 'create_version', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('died after transfer')))
    with pytest.raises(RuntimeError):
        handoff.hand_off(j, cfg, good.packet, at_ms=1)
    assert n(j, 'research_tests') == 1 and n(j, 'strategy_versions') == 0
    monkeypatch.setattr(fh, 'create_version', real)
    out = handoff.hand_off(j, cfg, good.packet, at_ms=2)
    assert out['status'] == 'VALIDATED' and out['transfer_status'] == 'duplicate'
    assert n(j, 'research_tests') == 1 and n(j, 'strategy_versions') == 1


def test_production_gates_and_strictness_are_honoured_by_the_new_source(good, tmp_path):
    j = Journal(tmp_path / 'j.db')
    closed = load_config()
    closed['research'].update(referee=False,handoff=False)                      # explicit gate fixture
    real = handoff.hand_off(j, closed, good.packet)
    assert real['status'] == 'REFUSED' and real['reason_code'] == 'hq_research_handoff_gate_closed'
    open_cfg = copy.deepcopy(load_config())
    open_cfg['research'].update(referee=True, handoff=True, closure=dict(approved_calibration=good.auth['cfg']['research']['closure']['approved_calibration']))
    out = handoff.hand_off(j, open_cfg, good.packet)                              # gates open, real (stricter) numbers
    assert out['status'] == 'REFUSED' and out['reason_code'] == 'hq_policy_weaker_than_configured'
    assert n(j, 'research_tests') == 0 and n(j, 'strategy_versions') == 0
    closed = gated_cfg(good.auth['cfg']['research']['closure']['approved_calibration'], handoff=False)
    assert refusal(good.packet, closed) == 'hq_research_handoff_gate_closed'
    assert refusal(good.packet, gated_cfg(good.auth['cfg']['research']['closure']['approved_calibration'], referee=False)) == 'hq_research_referee_gate_closed'
    assert refusal(good.packet, gated_cfg(None)) == 'hq_calibration_not_approved'


def test_test_only_packet_has_no_factory_authority(tmp_path):
    t = build(tmp_path, name='t', seal='SYNTHETIC')
    p = Q.qualify(t.store, fixture_freeze(t), t.env, t.snap, tmp_path / 'w', now_ms=1, run=scripted_referee(True))
    assert p['status'] == 'QUALIFIED' and p['authority_class'] == 'TEST_ONLY'
    j = Journal(tmp_path / 'j.db')
    out = handoff.hand_off(j, gated_cfg(approval(tmp_path, t.env)), p)
    assert out['reason_code'] == 'hq_test_only_policy_has_no_factory_authority'
    assert n(j, 'research_tests') == 0 and n(j, 'strategy_versions') == 0


def test_changed_cost_model_or_evaluator_voids_the_packet(good, monkeypatch):
    cfg = copy.deepcopy(good.auth['cfg'])
    cfg['risk'] = dict(cfg['risk'], taker_fee_pct=0.9)
    assert refusal(good.packet, cfg) == 'hq_cost_model_differs_from_configured'
    monkeypatch.setattr(manifest, 'evaluator_id', lambda: 'changed')
    assert refusal(good.packet, good.auth['cfg']) in ('hq_evaluator_changed_since_qualification', 'hq_calibration_not_approved')


def test_interrupted_look_is_never_retaken(tmp_path):
    b = build(tmp_path, name='int', seal='SYNTHETIC')
    f = fixture_freeze(b)
    calls = []
    def dies(fn, payload, **_):
        calls.append(1)
        raise KeyboardInterrupt('killed mid-look')
    with pytest.raises(KeyboardInterrupt):
        Q.qualify(b.store, f, b.env, b.snap, tmp_path / 'w', now_ms=1, run=dies)
    p = Q.qualify(b.store, f, b.env, b.snap, tmp_path / 'w', now_ms=2, run=scripted_referee(True, calls))
    assert p['status'] == 'INCONCLUSIVE' and 'interrupted' in p['reason'] and len(calls) == 1
