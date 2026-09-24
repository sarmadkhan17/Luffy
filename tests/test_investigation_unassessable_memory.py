"""SDD-STAGE-3-INVESTIGATION-UNASSESSABLE-MEMORY-V1: terminal not_testable closures
are remembered with their exact reason as context, never as usefulness,
falsification, priority or registration authority. Synthetic; no network, LLM or trading."""
import ast
import json
import shutil
import sqlite3
import time
from dataclasses import asdict, replace
from pathlib import Path

import pytest

from trader.cognition import investigation as I, memory as M
from trader.cognition.contracts import stable_id
from trader.observability import investigation as C, memory as S
from tests.test_market_investigation import prefix, targets  # noqa: F401 (fixture)
from tests.test_investigation_memory import resolved
from tests.test_attention_learning import publish
from tests import test_positioning_investigation_family as PF
from tests import test_correlation_investigation_family as CF


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    import socket

    def denied(*a, **kw):
        raise AssertionError("network forbidden")
    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket, "create_connection", denied)


FORBIDDEN = ("useful", "falsif", "not_established", "established", "direction", "long", "short",
             "probab", "suppress", "salience", "rank", "allocat", "winner", "score")


def consistent(state):
    """A self-consistent synthetic state: its id matches its content."""
    fields = {k: getattr(state, k) for k in M._STATE_FIELDS}
    return replace(state, state_id=stable_id('state', fields))


def expired(inv):
    at = inv.measurement.expires_ms + 1
    return I.advance(inv, I.measure(inv, (), at, at))


def reopen(inv, bars, at):
    """A later registration of the same family/protocol/config/symbol."""
    at = at if (at // I.TF + 1) * I.TF - at >= I.GUARD_MS else (at // I.TF + 1) * I.TF + 1
    return I.open_investigation(replace(inv.state, observed_ms=at), inv.primary_trigger, bars, at)


def positioning_closure(tmp_path):
    snap, state = PF._state(tmp_path)
    bare = consistent(PF._with(state, lambda d: None if d.name in I.FAMILIES else d))
    inv = I.open_investigation(bare, I.POSITIONING_FAMILY, snap.bars, PF.NOW)
    return snap, inv, I.advance(inv, I.measure(inv, (), PF.NOW, PF.NOW))


def v2_closure(prefix):
    _, snap, inv = prefix
    return snap, inv, expired(inv)


def store(db, inv, bars, updates):
    db.execute('INSERT INTO cases VALUES (?,?,?,?,NULL,?)', (inv.investigation_id, inv.episode_id,
               inv.state.symbol, inv.registered_ms, I.encode(asdict(inv))))
    C._save_inputs(db, inv.investigation_id, bars, {b.version_id for b in bars})
    for u in updates:
        C._append(db, u)


# 1, 5, 6 ── one record, exact reason, full provenance ─────────────────────

def test_v2_not_testable_closure_is_one_record_with_exact_reason_and_provenance(prefix):
    snap, inv, update = v2_closure(prefix)
    assert (update.evidence.status, update.next_action.kind) == ('not_testable', 'UNASSESSABLE')
    now = update.observed_ms + 1
    rec = M.verified_unassessable(inv, update, snap.bars, now)
    assert rec.outcome_type == 'unassessable_closure' and rec.schema_version == M.UNASSESSABLE_SCHEMA
    assert rec.reason == 'missing_data_expired' and rec.reason_codes == update.reason_codes
    assert (rec.source_id, rec.source_event_id, rec.source_evidence_id, rec.source_episode_id) == (
        inv.investigation_id, update.event_id, update.evidence.evidence_id, inv.episode_id)
    assert (rec.source_scan_id, rec.source_state_id, rec.catalog_id, rec.config_id, rec.family, rec.symbol) == (
        inv.state.scan_id, inv.state.state_id, I.CATALOG_ID, inv.state.config_id, inv.primary_trigger, inv.state.symbol)
    assert (rec.registered_ms, rec.resolved_ms, rec.recorded_ms) == (inv.registered_ms, update.observed_ms, now)
    assert rec.evidence == json.loads(I.encode(asdict(update.evidence)))
    for absent in ('winner', 'score', 'sign', 'probability', 'direction', 'measurement'):
        assert absent not in asdict(rec)
    assert M.unassessable_from_dict(json.loads(I.encode(asdict(rec)))) == rec
    assert M.verified_unassessable(inv, update, snap.bars, now) == rec          # 11: deterministic


def test_positioning_not_testable_closure_keeps_its_reason(tmp_path):
    snap, inv, update = positioning_closure(tmp_path)
    rec = M.verified_unassessable(inv, update, snap.bars, PF.NOW + 1)
    assert (rec.reason, rec.reason_codes) == ('concurrent_evidence_missing', ('concurrent_evidence_missing',))
    assert (rec.family, rec.catalog_id) == (I.POSITIONING_FAMILY, I.POSITIONING_CATALOG_ID)


def test_correlation_family_is_verified_and_fails_closed_when_not_replayable(tmp_path):
    # The frozen correlation protocol validates the same evidence at registration,
    # so a correlation not_testable closure cannot be replayed from a genuine case.
    inv = CF._inv(tmp_path)
    broken = replace(inv, state=consistent(CF._with(inv.state, lambda d: None if d.name == CF.SIGNED else d)))
    update = I.advance(broken, I.measure(broken, (), CF.NOW, CF.NOW))
    assert update.evidence.reason == 'correlation_evidence_missing'
    snap, _ = CF._state(tmp_path)
    with pytest.raises(ValueError, match='unassessable_frozen_registration_mismatch'):
        M.verified_unassessable(broken, update, snap.bars, CF.NOW + 1)


# 2, 3 ── unresolved and measured closures are not this type ───────────────

def test_unresolved_and_measured_are_refused_as_this_type(prefix):
    _, snap, inv = prefix
    pending = I.advance(inv, I.measure(inv, (), inv.registered_ms, inv.registered_ms))
    assert pending.evidence.status == 'unresolved'
    with pytest.raises(ValueError, match='unassessable_requires_not_testable_closure'):
        M.verified_unassessable(inv, pending, snap.bars, inv.registered_ms + 1)
    bars, measured = resolved(inv)
    with pytest.raises(ValueError, match='unassessable_requires_not_testable_closure'):
        M.verified_unassessable(inv, measured, (*snap.bars, *bars), measured.observed_ms)


def test_ledger_ingests_only_terminal_not_testable_and_leaves_measured_path_alone(prefix, tmp_path):
    snap, inv, update = v2_closure(prefix)
    open_inv = replace(inv, investigation_id='open', episode_id='open_ep',
                       state=replace(inv.state, symbol='OPEN/USDT'))
    _, snap2, inv2 = prefix
    bars, measured = resolved(inv2)
    measured_inv = inv2
    with C.ledger(tmp_path / 'm.db') as db:
        store(db, inv, snap.bars, [update])
        db.execute('INSERT INTO cases VALUES (?,?,?,?,NULL,?)', ('open', 'open_ep', 'OPEN/USDT', 1,
                   I.encode(asdict(open_inv))))
        now = update.observed_ms + 1
        report = S.ingest(db, now)
        assert report['unassessable'] == {'added': 1, 'refused': []}
        assert report['added'] == 0 and report['refused'] == []
        assert db.execute('SELECT COUNT(*) FROM memory_cases').fetchone()[0] == 0
        [(sid,)] = db.execute('SELECT source_id FROM memory_unassessable').fetchall()
        assert sid == inv.investigation_id
    with C.ledger(tmp_path / 'measured.db') as db:
        store(db, measured_inv, (*snap2.bars, *bars), [measured])
        report = S.ingest(db, measured.observed_ms + 1)
        assert report['added'] == 1 and report['unassessable'] == {'added': 0, 'refused': []}
        assert db.execute('SELECT COUNT(*) FROM memory_unassessable').fetchone()[0] == 0
        case = json.loads(db.execute('SELECT payload FROM memory_cases').fetchone()[0])
        assert case['outcome_type'] == 'non_economic_observation'
        assert case == json.loads(I.encode(asdict(M.verified_case(
            measured_inv, measured, (*snap2.bars, *bars), measured.observed_ms + 1))))


# 4 ── retry and restart never duplicate ────────────────────────────────────

def test_retry_and_restart_do_not_duplicate(prefix, tmp_path):
    snap, inv, update = v2_closure(prefix)
    now = update.observed_ms + 1
    with C.ledger(tmp_path / 'm.db') as db:
        store(db, inv, snap.bars, [update])
        assert S.ingest(db, now)['unassessable']['added'] == 1
        first = db.execute('SELECT payload FROM memory_unassessable').fetchall()
        assert S.ingest(db, now + 5)['unassessable']['added'] == 0
    with C.ledger(tmp_path / 'm.db') as db:                                    # restart
        assert S.ingest(db, now + 9)['unassessable']['added'] == 0
        assert db.execute('SELECT payload FROM memory_unassessable').fetchall() == first


@pytest.mark.parametrize('change', ['reason', 'action', 'case', 'chronology', 'state', 'catalog', 'baseline', 'assessment'])
def test_forged_or_mismatched_closures_are_refused(prefix, change):
    snap, inv, update = v2_closure(prefix)
    bars = snap.bars
    e = update.evidence
    if change == 'reason': update = replace(update, evidence=replace(e, reason='insufficient_history'))
    if change == 'action': update = replace(update, next_action=replace(update.next_action, kind='WAIT'))
    if change == 'case': update = replace(update, investigation_id='other')
    if change == 'chronology': update = replace(update, evidence=replace(e, as_of_ms=inv.registered_ms - 1))
    if change == 'state': inv = replace(inv, state=replace(inv.state, symbol='X/USDT'))
    if change == 'catalog': inv = replace(inv, measurement=replace(inv.measurement, catalog_id=I.POSITIONING_CATALOG_ID))
    if change == 'baseline': bars = bars[1:]
    if change == 'assessment': update = replace(update, assessment=(('same_direction', 'compatible'),))
    with pytest.raises(ValueError, match='^unassessable_'):
        M.verified_unassessable(inv, update, bars, update.observed_ms + 1)


# 7, 10 ── exact-key recall, no conflation ──────────────────────────────────

def test_later_matching_investigation_retrieves_caution(prefix):
    snap, inv, update = v2_closure(prefix)
    rec = M.verified_unassessable(inv, update, snap.bars, update.observed_ms + 1)
    new = reopen(inv, snap.bars, update.observed_ms + 2)
    ctx = M.retrieve_unassessable(new, [rec])
    assert ctx['records'] == [asdict(rec)] and ctx['audit'] == [
        {'case_id': rec.case_id, 'reason': 'compatible_prior_unassessable_closure'}]
    [caution] = ctx['cautions']
    assert caution['kind'] == 'prior_unassessable_closure' and caution['authority'] == 'context_only'
    assert (caution['family'], caution['catalog_id'], caution['reason'], caution['reason_codes']) == (
        inv.primary_trigger, I.CATALOG_ID, 'missing_data_expired', list(update.reason_codes))
    text = (caution['text'] + ' ' + ctx['limitation']).lower()
    assert 'missing_data_expired' in text
    assert not [w for w in FORBIDDEN if w in caution['text'].lower()]
    assert 'counter_tests' not in ctx


@pytest.mark.parametrize('change,reason', [
    ('self', 'self_excluded'), ('later_known', 'not_known_before_registration'),
    ('late_import', 'not_known_before_registration'), ('family', 'incompatible_family'),
    ('catalog', 'incompatible_protocol_or_config'), ('config', 'incompatible_protocol_or_config'),
    ('symbol', 'incompatible_symbol'), ('type', 'incompatible_version_or_outcome_type'),
    ('schema', 'incompatible_version_or_outcome_type')])
def test_retrieval_exclusions(prefix, change, reason):
    snap, inv, update = v2_closure(prefix)
    rec = M.verified_unassessable(inv, update, snap.bars, update.observed_ms + 1)
    new = reopen(inv, snap.bars, update.observed_ms + 2)
    edits = {'self': dict(source_id=new.investigation_id), 'later_known': dict(available_ms=new.registered_ms),
             'late_import': dict(recorded_ms=new.registered_ms + 1), 'family': dict(family='volatility_transition'),
             'catalog': dict(catalog_id=I.POSITIONING_CATALOG_ID), 'config': dict(config_id='other'),
             'symbol': dict(symbol='OTHER/USDT'), 'type': dict(outcome_type='non_economic_observation'),
             'schema': dict(schema_version='investigation-memory.v1')}
    ctx = M.retrieve_unassessable(new, [replace(rec, **edits[change])])
    assert ctx['records'] == [] and ctx['cautions'] == [] and ctx['audit'][0]['reason'] == reason


def test_distinct_reasons_stay_distinct_and_capacity_is_deterministic(prefix):
    snap, inv, update = v2_closure(prefix)
    rec = M.verified_unassessable(inv, update, snap.bars, update.observed_ms + 1)
    new = reopen(inv, snap.bars, update.observed_ms + 2)
    reasons = ['missing_data_expired', 'missing_baseline_window', 'unusable_future_scale',
               'zero_or_missing_initial_component', 'unusable_baseline_scale']
    recs = [replace(rec, case_id=f'c{i}', source_id=f's{i}', reason=r, reason_codes=(r,), available_ms=rec.available_ms - i)
            for i, r in enumerate(reasons)]
    ctx = M.retrieve_unassessable(new, recs)
    assert ctx == M.retrieve_unassessable(new, list(reversed(recs)))
    assert [c['reason'] for c in ctx['cautions']] == reasons[:M.MAX_RETRIEVED]
    assert [c['reason_codes'] for c in ctx['cautions']] == [[r] for r in reasons[:M.MAX_RETRIEVED]]
    assert [a['reason'] for a in ctx['audit']].count('retrieval_capacity') == len(reasons) - M.MAX_RETRIEVED
    assert len(ctx['audit']) == len(reasons)


def test_positioning_recall_matches_only_positioning(tmp_path, prefix):
    (tmp_path / 'p').mkdir()
    snap, inv, update = positioning_closure(tmp_path / 'p')
    rec = M.verified_unassessable(inv, update, snap.bars, PF.NOW + 1)
    new = reopen(inv, snap.bars, PF.NOW + I.TF)
    assert M.retrieve_unassessable(new, [rec])['cautions'][0]['reason'] == 'concurrent_evidence_missing'
    _, _, v2 = prefix
    other = replace(v2, state=replace(v2.state, symbol=inv.state.symbol, config_id=inv.state.config_id),
                    registered_ms=new.registered_ms)
    assert M.retrieve_unassessable(other, [rec])['audit'][0]['reason'] == 'incompatible_family'


# 8, 9, 11, retention ── consumer: no suppression, no priority effect ───────

def _ledger_with_closures(tmp_path):
    now = int(time.time() * 1000) // I.TF * I.TF + 60_000
    src, dst = tmp_path / 'attention.db', tmp_path / 'investigation.db'
    publish(src, now); C.step(src, dst, now)
    with sqlite3.connect(dst) as db:
        expires = max(json.loads(p)['measurement']['expires_ms'] for (p,) in db.execute('SELECT payload FROM cases'))
    C.step(src, dst, expires + 1)                      # outage: expiry observed, never backdated
    detail = C.step(src, dst, expires + 2)             # first later consumer invocation ingests
    return src, dst, expires, detail


def test_consumer_ingests_registers_and_does_not_suppress_or_reprioritize(tmp_path, monkeypatch):
    src, dst, expires, detail = _ledger_with_closures(tmp_path)
    with sqlite3.connect(dst) as db:
        closed = db.execute("SELECT COUNT(*) FROM updates WHERE json_extract(payload,'$.evidence.status')='not_testable'").fetchone()[0]
        stored = [json.loads(p) for (p,) in db.execute('SELECT payload FROM memory_unassessable ORDER BY source_id')]
    assert closed and len(stored) == closed
    assert all(r['reason_codes'] == ['missing_data_expired'] for r in stored)
    assert C.step(src, dst, expires + 3)['memory']['unassessable'] == {'added': 0, 'refused': []}
    # Identical later scan on a copy with no unassessable memory: the comparator.
    bare = tmp_path / 'bare'; bare.mkdir()
    shutil.copy(dst, bare / 'investigation.db')
    with C.ledger(bare / 'investigation.db') as db:
        db.execute('DELETE FROM memory_unassessable')
    later = expires + I.TF
    publish(src, later, 'next')
    shutil.copy(src, bare / 'attention.db')
    shutil.copy(src.with_name('attention_health.json'), bare / 'attention_health.json')
    with_mem = C.step(src, dst, later)
    with monkeypatch.context() as mp:                  # comparator never re-ingests
        mp.setattr(S, 'ingest_unassessable', lambda db, now: {'added': 0, 'refused': []})
        without = C.step(bare / 'attention.db', bare / 'investigation.db', later)
    assert with_mem['registered'] == without['registered'] > 0                  # 8: not suppressed
    assert with_mem['allocation'] == without['allocation']                      # 9: allocation unchanged
    assert with_mem['skipped'] == without['skipped']
    q = 'SELECT id,episode_id,symbol FROM cases ORDER BY id'
    rq = "SELECT case_id,stage,json_extract(payload,'$.work') FROM resource_receipts"
    aq = 'SELECT id,payload FROM allocation_decisions ORDER BY id'
    with sqlite3.connect(dst) as a, sqlite3.connect(bare / 'investigation.db') as b:
        for sql in (q, rq, aq):
            assert sorted(a.execute(sql).fetchall()) == sorted(b.execute(sql).fetchall())
    new = [d for d in C.dossiers(dst, limit=64) if d['investigation']['registered_ms'] == later]
    assert new
    for d in new:
        ctx = d['memory']['context']
        closures = ctx['unassessable_closures']
        assert closures['cautions'] and not ctx['counter_tests']
        assert all(r['recorded_ms'] < ctx['cutoff_ms'] and r['symbol'] == d['investigation']['state']['symbol']
                   for r in closures['records'])
        assert all(not r['changed'] for r in d['memory']['reasoning'])
        assert len(closures['audit']) == len(stored)
    plain = {d['investigation']['investigation_id']: d for d in C.dossiers(bare / 'investigation.db', limit=64)}
    for d in new:
        other = plain[d['investigation']['investigation_id']]['memory']['context']
        assert 'unassessable_closures' not in other
        assert {k: v for k, v in d['memory']['context'].items() if k not in ('unassessable_closures', 'context_id')} == \
               {k: v for k, v in other.items() if k != 'context_id'}
    # 11: export and replay reproduce the prior closure from self-contained evidence.
    iid = new[0]['investigation']['investigation_id']
    with C.ledger(dst) as db:
        archive = S.export_case(db, iid)
    S.replay_export(json.loads(I.encode(archive)))
    forged = json.loads(I.encode(archive))
    forged['memory']['context']['unassessable_closures']['records'][0]['reason'] = 'insufficient_history'
    forged['sha256'] = S.typed.O.digest({k: forged[k] for k in ('schema_version', 'source', 'memory')})
    with pytest.raises(ValueError, match='memory_archive_prior_unassessable_mismatch'):
        S.replay_export(forged)


def test_retention_removes_records_with_their_case(tmp_path):
    _, dst, _, _ = _ledger_with_closures(tmp_path)
    with C.ledger(dst) as db:
        assert db.execute('SELECT COUNT(*) FROM memory_unassessable').fetchone()[0] > 0
        db.execute('DELETE FROM cases'); S.retain(db)
        assert db.execute('SELECT COUNT(*) FROM memory_unassessable').fetchone()[0] == 0


def test_contexts_without_records_are_byte_identical(prefix, tmp_path):
    _, snap, inv = prefix
    with C.ledger(tmp_path / 'm.db') as db:
        ctx = S.register(db, inv)
    assert 'unassessable_closures' not in ctx
    base = M.retrieve(inv, [])
    assert ctx['cases'] == base['cases'] and ctx['cautions'] == base['cautions']


# 14 ── no LLM, network or trading dependency ───────────────────────────────

@pytest.mark.parametrize('module', [M, S])
def test_no_llm_network_or_trading_imports(module):
    tree = ast.parse(Path(module.__file__).read_text())
    names = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    names |= {n.module or '' for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    bad = ('socket', 'requests', 'http', 'urllib', 'ccxt', 'anthropic', 'openai', 'brain', 'engine', 'executor', 'risk')
    assert not [n for n in names if any(b in n for b in bad)]
