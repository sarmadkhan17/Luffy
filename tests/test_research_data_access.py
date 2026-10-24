"""Research data-access requests through the existing Needs You surface.

A study candidate missing a REQUIRED owner-suppliable reference (TradingView CRYPTOCAP TOTAL) is checkpointed
as WAITING_DATA and one durable alert is recorded in the closure Store; unrelated candidates proceed; the
alert resolves only when the candidate is actually re-evaluated on eligible data. Fixtures only."""
import hashlib
import json
import os
import sqlite3
from pathlib import Path

import numpy as np
import pytest

from tests import compose_fixtures as C
from tests.test_published_csv_context import CAPTURED, rule, walk, write_csv
from trader.core.config import load_config
from trader.core.journal import Journal
from trader.data import chart_exports, published_csv as P
from trader.data.references import DAY, RefStore
from trader.owner import approvals as A
from trader.research.closure import data_access, manifest, study as S, synthetic as SYN
from trader.research.closure.store import Store

TF = SYN.TF_MS


def rsi_rule():
    return dict(question='unrelated ohlcv only', rationale='r', expected='e', failure_conditions='f', structure='long_short',
                tf='4h', geo='fixed', long={'cmp': 'gt', 'a': {'f': 'rsi14'}, 'b': {'lit': 70}},
                short={'cmp': 'lt', 'a': {'f': 'rsi14'}, 'b': {'lit': 30}})


def factor_rule(factor):
    f = {'f': factor}
    return dict(question=f'does {factor} lead', rationale='r', expected='e', failure_conditions='f', structure='long_short',
                tf='4h', geo='fixed', long={'cmp': 'gt', 'a': f, 'b': {'lit': 0}}, short={'cmp': 'lt', 'a': f, 'b': {'lit': 0}})


FIRST = SYN.EPOCH_MS // DAY * DAY - 45 * DAY


def csv_days(b):
    return (b.protected_end - FIRST) // DAY + 3


@pytest.fixture()
def world(tmp_path):
    """Retrospective study over a context snapshot with a detached store that holds CoinGecko TOTAL only."""
    b = C.build_ctx(tmp_path, mode='null', n_bars=1500)
    st = SYN.EPOCH_MS
    env = manifest.make_envelope(
        b.manifest, b.snap, policy=b.env['policy'], discovery_symbols=b.names[:6], heldout_symbols=b.names[6:],
        fit_start_ms=st, dev_end_ms=st + C.DEV_BARS * TF, embargo_bars=10, protected_start_ms=st + 1100 * TF,
        protected_end_ms=st + (1500 - 1) * TF, risk=C.RISK, development_mode='RETROSPECTIVE')
    assert env['development_status'] == 'ELIGIBLE'
    b.protected_end = env['protected_window'][1]
    cg = write_csv(tmp_path / 'cg.csv', walk(csv_days(b)), FIRST, vols=np.full(csv_days(b), 1e10))
    refs = tmp_path / 'refs.db'
    P.import_csv(cg, refs, captured_ms=CAPTURED)
    spec = dict(snapshot_path=str(b.snap), tf='4h', discovery_symbols=b.names[:6], heldout_symbols=b.names[6:],
                fit_start_ms=st, dev_end_ms=st + C.DEV_BARS * TF, embargo_bars=10, protected_start_ms=st + 1100 * TF,
                protected_end_ms=st + (1500 - 1) * TF, policy=b.env['policy'], risk=C.RISK, development_mode='RETROSPECTIVE',
                references_path=str(refs), workdir=str(tmp_path / 'wk'),
                seed_graphs=[factor_rule('ctx_total'), rsi_rule(), factor_rule('ctx_cg_total_ret30')])
    sid = S.create_study(b.store, spec, now_ms=1)
    b.sid, b.refs, b.tmp, b.spec = sid, refs, tmp_path, spec
    b.cfg = dict(research=dict(closure=dict(store_path=str(b.store.path))))
    return b


def step(b, now, store=None):
    return S.step(store or b.store, b.sid, budget_s=900, now_ms=now, child=False, cfg=load_config(), allow_heavy=False)


def unit_by_question(b, store=None):
    out = {}
    for u in (store or b.store).units(b.sid):
        if u['kind'] == 'develop':
            cand = b.store.get_once('candidates', u['unit_id'].split(':', 1)[1])
            out[cand['question']] = u
    return out


def tv_export(b, name='tv.csv'):
    n = csv_days(b)
    close = walk(n, seed=21)
    rows = ['time,open,high,low,close,volume']
    for i, c in enumerate(close):
        rows.append(f'{FIRST // 1000 + i * 86400},{float(c * .99)!r},{float(c * 1.02)!r},{float(c * .97)!r},{float(c)!r},1000')
    f = b.tmp / name
    f.write_text('\n'.join(rows) + '\n')
    os.utime(f, (1_000_000_000, 1_000_000_000))
    return f


def test_missing_required_ref_makes_one_durable_alert_and_only_that_candidate_waits(world):
    b = world
    out = step(b, 10)
    assert out['stop_reason'] == 'WAITING_FOR_DATA' and out['process_status'] == 'IN_PROGRESS'
    assert out['research_outcome'] != 'QUALIFIED' and out['waiting_for_data']
    u = unit_by_question(b)
    assert u['does ctx_total lead']['status'] == 'WAITING_DATA'
    assert u['unrelated ohlcv only']['status'] == 'DONE' and u['does ctx_cg_total_ret30 lead']['status'] == 'DONE'
    for q in ('unrelated ohlcv only', 'does ctx_cg_total_ret30 lead'):
        assert json.loads(u[q]['result']).get('waiting_data') is None           # available dependency: no log-in request
        assert json.loads(u[q]['result'])['outcome'] not in ('BLOCKED_DATA', 'CODE_FAILURE')
    reqs = b.store.data_requests(study_id=b.sid)
    assert len(reqs) == 1                                                       # not one per unavailable catalogue factor
    r = reqs[0]
    env = b.store.get_once('envelopes', json.loads(b.store.study(b.sid)['payload'])['envelope_id'])
    assert (r['provider'], r['series'], r['tf'], r['reference_key']) == ('TradingView', 'CRYPTOCAP:TOTAL', '1d', 'tv_total')
    assert (r['span_start_ms'], r['span_end_ms']) == tuple(env['fit_window'])
    assert r['candidate_id'] == json.loads(u['does ctx_total lead']['result'])['candidate_id'] and r['question_id']
    assert 'sign in' in r['owner_action'] and r['status'] == 'OPEN' and 'password' not in json.dumps(r).lower()
    import re
    assert re.fullmatch(r'\d{4}-\d\d-\d\d to \d{4}-\d\d-\d\d \(UTC\)', r['span_utc']) and not re.search(r'\d{12,}', r['owner_action'])
    assert not any(x['unit_id'] == 'freeze' for x in b.store.units(b.sid))     # waiting is not completion
    blocked = [x for x in b.store.all_once('runs') if x['outcome'] == 'BLOCKED_DATA']
    assert len(blocked) == 1                                                    # original blocked lineage retained
    # repeated steps and a restart create no second alert and no extra run
    for k in range(3):
        step(b, 20 + k)
    b.store.close()
    again = Store(b.store.path)
    step(b, 30, store=again)
    assert len(again.data_requests(study_id=b.sid)) == 1 and again.data_requests()[0]['request_id'] == r['request_id']
    assert len([x for x in again.all_once('runs') if x['outcome'] == 'BLOCKED_DATA']) == 1
    again.close()


def test_needs_you_projects_pending_alert_read_only_and_is_not_approvable(world, tmp_path):
    b = world
    step(b, 10)
    b.store.close()
    j = Journal(tmp_path / 'j.db')
    before_file = hashlib.sha256(Path(b.store.path).read_bytes()).hexdigest()
    before_rows = sqlite3.connect(b.store.path).execute('SELECT request_id,status,updated_ms FROM data_requests').fetchall()
    changes = j._conn().total_changes
    view = A.items(j, b.cfg)
    assert hashlib.sha256(Path(b.store.path).read_bytes()).hexdigest() == before_file       # read side wrote nothing
    assert sqlite3.connect(b.store.path).execute('SELECT request_id,status,updated_ms FROM data_requests').fetchall() == before_rows
    assert j._conn().total_changes == changes and not A.exists(j, 'owner_approval_items')
    [item] = view['items']
    assert (item['action_type'], item['decision_operation'], item['status']) == ('research_data_access', 'provide_data', 'PENDING')
    assert item['bindings']['series'] == 'CRYPTOCAP:TOTAL' and 'sign in' in item['required_owner_action']
    assert view['pending_total'] == 1 and view['total'] == 1
    assert not [u for u in view['unavailable'] if 'closure' in u['reason']]      # a readable store is not 'unavailable'
    # approving it is refused before any ledger write
    with pytest.raises(ValueError, match='data_request_is_not_an_approval'):
        A.decide(j, b.cfg, dict(item_id=item['item_id'], binding_hash=item['binding_hash'], decision='APPROVED'),
                 actor='owner', request_id='r1', now_ms=99)
    assert not A.exists(j, 'owner_approval_items') and not A.exists(j, 'owner_approval_receipts')
    assert j._conn().total_changes == changes


def test_unconfigured_absent_or_corrupt_store_is_truthful(world, tmp_path):
    j = Journal(tmp_path / 'j.db')
    base = A.items(j, {})
    assert base['items'] == []                                                                # no fake alert when not configured
    assert A.items(j, {'research': {'closure': {'enabled': True}}})['unavailable'] == base['unavailable']
    missing = A.items(j, dict(research=dict(closure=dict(store_path=str(tmp_path / 'nope.db')))))
    key = lambda xs: sorted(x['reason'] for x in xs)
    assert missing['items'] == [] and key(missing['unavailable']) == key(base['unavailable'] + [dict(field='approvals', reason='closure_store_not_found')])
    assert not (tmp_path / 'nope.db').exists()                                              # GET never creates a store
    bad = tmp_path / 'bad.db'
    bad.write_bytes(b'not a sqlite database' * 50)
    corrupt = A.items(j, dict(research=dict(closure=dict(store_path=str(bad)))))
    assert corrupt['items'] == [] and dict(field='approvals', reason='closure_store_unreadable') in corrupt['unavailable']
    old = tmp_path / 'old.db'
    sqlite3.connect(old).execute('CREATE TABLE studies(x)').connection.commit()           # a store from before this feature
    assert A.items(j, dict(research=dict(closure=dict(store_path=str(old)))))['unavailable'] == base['unavailable']
    assert not [r for r in sqlite3.connect(old).execute("SELECT name FROM sqlite_master WHERE name='data_requests'")]


def test_unchanged_data_stays_pending_then_supplied_data_reevaluates_and_resolves(world, tmp_path):
    b = world
    step(b, 10)
    runs_before = len(b.store.all_once('runs'))
    step(b, 11)
    step(b, 12)
    assert len(b.store.all_once('runs')) == runs_before                         # no re-evaluation, no stale-cache reuse
    assert b.store.data_requests()[0]['status'] == 'OPEN'
    # the owner supplies an official export into the detached store the study reads
    store = RefStore(b.refs)
    res = chart_exports.ingest_exports([dict(path=tv_export(b), symbol='CRYPTOCAP:TOTAL', timeframe='1d')], store,
                                       captured_ms=CAPTURED)
    store.db.close()
    assert list(res.values())[0]['status'] == 'IMPORTED', res
    out = step(b, 40)
    u = unit_by_question(b)['does ctx_total lead']
    assert u['status'] == 'DONE' and json.loads(u['result'])['outcome'] not in ('BLOCKED_DATA', 'CODE_FAILURE')
    assert out['stop_reason'] != 'WAITING_FOR_DATA'
    [r] = b.store.data_requests()
    assert r['status'] == 'RESOLVED' and r['resolution']['result_id'] == json.loads(u['result'])['result_id']
    runs = [x for x in b.store.all_once('runs') if x['candidate_id'] == r['candidate_id']]
    assert sorted(x['outcome'] == 'BLOCKED_DATA' for x in runs) == [False, True] and not [x for x in runs if x['outcome'] == 'CODE_FAILURE']    # old blocked run kept, new run has its own cache key
    assert len({x['cache_key'] for x in runs}) == 2
    new = [x for x in runs if x['outcome'] != 'BLOCKED_DATA'][0]
    assert new['context_provenance']['ref:tv_total']['sources'][0]['csv_sha256'] == hashlib.sha256((b.tmp / 'tv.csv').read_bytes()).hexdigest()
    # resolved history stays visible but is no longer pending
    b.store.close()
    view = A.items(Journal(tmp_path / 'j.db'), b.cfg)
    assert view['pending_total'] == 0 and [i['status'] for i in view['items']] == ['RESOLVED']
    assert 'freeze' in {x['unit_id'] for x in Store(b.store.path).units(b.sid)}   # study now continues past the wait


def test_replay_study_gets_no_login_request_and_non_owner_refs_get_none(world):
    b = world
    cand = b.store.get_once('candidates', next(c for c in (u['unit_id'].split(':', 1)[1] for u in
                                                          (step(b, 10) and b.store.units(b.sid)) if u['kind'] == 'develop')))
    env = b.store.get_once('envelopes', json.loads(b.store.study(b.sid)['payload'])['envelope_id'])
    study = json.loads(b.store.study(b.sid)['payload'])
    gap = dict(requirement='ref:tv_total', reason='no data')
    assert data_access.build(b.sid, study, env, cand, gap) is not None
    assert data_access.build(b.sid, study, dict(env, development_mode='REPLAY'), cand, gap) is None   # a new capture cannot be replay history
    assert data_access.build(b.sid, study, env, cand, dict(requirement='ref:oil', reason='x')) is None  # nothing the owner supplies
    assert data_access.build(b.sid, study, env, cand, dict(requirement='ohlcv', reason='x')) is None
