"""Cheap regressions for data-access request bounds, projection truthfulness and dependency preflight
(no candle fixtures; synthetic stores only)."""
import hashlib
import os
import re
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from trader.core.journal import Journal
from trader.data import chart_exports
from trader.data.references import DAY, RefStore
from trader.owner import approvals as A
from trader.research.closure import data_access as D, development
from trader.research.closure.store import Store
from trader.research.compose import data as cdata

LO, HI = 1_600_000_000_000 // DAY * DAY, 1_600_000_000_000 // DAY * DAY + 120 * DAY
ENV = dict(development_mode='RETROSPECTIVE', fit_window=[LO, HI], dev_end_ms=HI, discovery_symbols=['A/USDT'])
STUDY = dict(snapshot_path='/x/snap.db')
TV = ('ref:tv_total', 'ref:tv_total2', 'ref:tv_total3', 'ref:tv_btc_d', 'ref:tv_usdt_d')


def cand(n):
    return dict(candidate_id=f'{n:064x}', question=f'question {n}')


def gaps(*reqs):
    return [dict(requirement=r, reason='no retained observations closed by development end') for r in reqs]


def blocked(*reqs):
    return dict(outcome='BLOCKED_DATA', context_gaps=gaps(*reqs), result_id='r', data_id='d', reason='x')


def test_active_requests_are_never_hidden_behind_resolved_history(tmp_path):
    st = Store(tmp_path / 'c.closure')
    old = D.build('s0', STUDY, ENV, cand(0), gaps('ref:tv_total')[0])
    st.open_data_request(old, 1)
    for i in range(1, 251):                                   # 250 newer, already resolved
        r = D.build(f's{i}', STUDY, ENV, cand(i), gaps('ref:tv_total')[0])
        st.open_data_request(r, i + 1)
        st.resolve_data_request(r['request_id'], i + 2, dict(outcome='NEGATIVE_ECONOMICS'))
    st.close()
    path = tmp_path / 'c.closure'
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    cfg = dict(research=dict(closure=dict(store_path=str(path))))
    j = Journal(tmp_path / 'j.db')
    view = A.items(j, cfg)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before                       # read side wrote nothing
    assert view['pending_total'] == 1 and view['items'][0]['item_id'] == old['request_id']   # active first, found
    assert view['research_data'] == dict(source='configured closure store data_requests', open_total=1, history_total=250,
                                         history_shown=D.MAX_HISTORY_PROJECTED, history_hidden=150)
    assert not [u for u in view['unavailable'] if 'closure' in u['reason']]
    # an exact identity is found however much history exists, without the global cap
    oldest_resolved = D.build('s1', STUDY, ENV, cand(1), gaps('ref:tv_total')[0])['request_id']
    one = A.items(j, cfg, identity=oldest_resolved)['items']
    assert [i['item_id'] for i in one] == [oldest_resolved] and one[0]['status'] == 'RESOLVED'


def test_open_projection_overflow_is_reported_not_silent(tmp_path, monkeypatch):
    st = Store(tmp_path / 'c.closure')
    for i in range(5):
        st.open_data_request(D.build(f's{i}', STUDY, ENV, cand(i), gaps('ref:tv_total')[0]), i)
    st.close()
    monkeypatch.setattr(D, 'MAX_OPEN_PROJECTED', 3)
    proj = D.project(tmp_path / 'c.closure')
    assert proj['open_total'] == 5 and len(proj['requests']) == 3 and proj['reason'] == 'closure_data_requests_open_truncated'


def test_cap_overflow_is_aggregated_with_every_requirement_and_blocks_resume(tmp_path, monkeypatch):
    monkeypatch.setattr(D, 'MAX_OPEN_PER_STUDY', 3)
    st = Store(tmp_path / 'c.closure')
    ids, required = D.record(st, 's', STUDY, ENV, cand(1), blocked(*TV), 10)
    assert required == sorted(TV) and len(ids) == 3                                      # nothing dropped, nothing phantom
    stored = {r['request_id']: r for r in st.data_requests(study_id='s')}
    assert set(ids) == set(stored) and all(stored[i]['status'] == 'OPEN' for i in ids)
    named = [q for r in stored.values() for q in D.required_of(r)]
    assert sorted(named) == sorted(TV)                                                   # exact affected requirements
    agg = [r for r in stored.values() if r['requirement'] == 'multiple'][0]
    assert len(agg['requirements']) == 3 and 'cap reached' in agg['missing_reason']
    assert D.record(st, 's', STUDY, ENV, cand(1), blocked(*TV), 11)[0] == ids            # idempotent: no growth
    assert len(st.data_requests(study_id='s')) == 3
    # resume only when EVERY awaited requirement is served, including the aggregated ones
    served = lambda missing: (lambda *a, **k: dict(gaps=gaps(*missing)))                  # noqa: E731
    monkeypatch.setattr(development, '_external', served(['ref:tv_usdt_d']))
    assert not D.eligible_now(st, STUDY, ENV, cand(1), required)
    monkeypatch.setattr(development, '_external', served([]))
    assert D.eligible_now(st, STUDY, ENV, cand(1), required)
    # resolution only after a re-evaluation that no longer needs them
    D.record(st, 's', STUDY, ENV, cand(1), dict(outcome='NEGATIVE_ECONOMICS', result_id='new'), 20)
    assert {r['status'] for r in st.data_requests(study_id='s')} == {'RESOLVED'}
    st.close()


def test_request_text_is_human_readable_and_structured_evidence_keeps_stamps():
    r = D.build('s', STUDY, ENV, cand(1), gaps('ref:tv_total')[0])
    assert re.fullmatch(r'\d{4}-\d\d-\d\d to \d{4}-\d\d-\d\d \(UTC\)', r['span_utc'])
    assert r['span_utc'] in r['owner_action'] and not re.search(r'\d{12,}', r['owner_action'])
    assert (r['span_start_ms'], r['span_end_ms']) == (LO, HI)
    assert D.build('s', STUDY, ENV, cand(1), dict(requirement='ref:oil', reason='x')) is None


def tv_db(tmp_path, first_day, days, name='refs.db'):
    rows = ['time,open,high,low,close,volume']
    for i in range(days):
        c = 1e12 * (1 + 0.001 * i)
        rows.append(f'{first_day // 1000 + i * 86400},{c * .99!r},{c * 1.02!r},{c * .97!r},{c!r},1')
    f = tmp_path / (name + '.csv')
    f.write_text('\n'.join(rows) + '\n')
    os.utime(f, (1_000_000_000, 1_000_000_000))
    store = RefStore(tmp_path / name)
    out = chart_exports.ingest_exports([dict(path=f, symbol='CRYPTOCAP:TOTAL', timeframe='1d')], store, captured_ms=1_790_000_000_000)
    store.db.close()
    assert list(out.values())[0]['status'] == 'IMPORTED', out
    return tmp_path / name


def external(db, **kw):
    retro = dict(dataset_id='closure:t', cut_ms=HI, start_ms=LO)
    return cdata.load_external(['ref:tv_total'], db, None, [], HI, references_path=db, retrospective=retro,
                               window_start_ms=LO, **kw)


def test_required_reference_stale_or_late_coverage_is_a_structured_gap(tmp_path):
    ok = external(tv_db(tmp_path, LO - 10 * DAY, 140, 'ok'))
    assert ok['gaps'] == [] and 'tv_total' in ok['market']
    stale = external(tv_db(tmp_path, LO - 10 * DAY, 60, 'stale'))                        # ends ~50 days before the cut
    assert [g['requirement'] for g in stale['gaps']] == ['ref:tv_total'] and 'stale' in stale['gaps'][0]['reason']
    assert re.search(r'\d{4}-\d\d-\d\d', stale['gaps'][0]['reason']) and not stale['market']
    late = external(tv_db(tmp_path, LO + 40 * DAY, 120, 'late'))
    assert 'coverage' in late['gaps'][0]['reason']
    # a non-empty frame of invalid rows is not coverage either
    frame = pd.DataFrame(dict(ts=pd.to_datetime([LO], unit='ms', utc=True), close=[np.nan]))
    assert 'no finite' in cdata.coverage_gap('tv_total', frame, HI, LO)
    # and the request that follows names the stale reason
    req = D.build('s', STUDY, ENV, cand(1), stale['gaps'][0])
    assert 'stale' in req['missing_reason'] and req['requirement'] == 'ref:tv_total'


def test_evaluator_reference_gaps_stay_structured_for_the_request_route(tmp_path):
    out = development._structured_gaps({'A/USDT': ['ref:tv_total: retrospective dependency missing'],
                                        'B/USDT': ['ref:tv_total: retrospective dependency missing']})
    assert out == [dict(requirement='ref:tv_total', reason='retrospective dependency missing', symbols=['A/USDT', 'B/USDT'])]
    assert development._structured_gaps(gaps('ref:tv_total')) == gaps('ref:tv_total')
    st = Store(tmp_path / 'c.closure')
    assert D.record(st, 's', STUDY, ENV, cand(1), dict(outcome='BLOCKED_DATA', context_gaps=out, result_id='r'), 1)[1] == ['ref:tv_total']
    st.close()


def test_context_warmup_may_precede_the_window_but_never_cross_the_cut_or_change_dataset():
    from trader.strategy.features import FeatureCtx
    cut, start = 1_600_000_000_000 // DAY * DAY + 200 * DAY, 1_600_000_000_000 // DAY * DAY + 100 * DAY
    desc = dict(dataset_id='d', cut_ms=cut, start_ms=start)

    def frame(first_day, n, d):
        f = pd.DataFrame(dict(ts=pd.to_datetime([first_day + i * DAY for i in range(n)], unit='ms', utc=True),
                              close=np.arange(1.0, n + 1)))
        f.attrs.update(retrospective=d, read_mode='retrospective', timeframe='1d')
        return f

    base = frame(start, 50, desc)
    base.attrs['timeframe'] = '1d'
    ok = frame(start - 60 * DAY, 100, dict(desc, start_ms=start - 60 * DAY))        # warmup before the window
    FeatureCtx(frames={'1d': base}, tf='1d', market={'x': ok})
    for bad, why in ((frame(start + DAY, 10, dict(desc, start_ms=start + DAY)), 'later start'),
                     (frame(start - 60 * DAY, 100, dict(desc, dataset_id='other', start_ms=start - 60 * DAY)), 'other dataset'),
                     (frame(start - 60 * DAY, 200, dict(desc, start_ms=start - 60 * DAY)), 'rows past the cut')):
        with pytest.raises(ValueError, match='mismatch|crosses'):
            FeatureCtx(frames={'1d': base}, tf='1d', market={'x': bad})


def test_derivative_inputs_carry_source_lineage_into_the_external_context(tmp_path):
    from tests import compose_fixtures as C
    from trader.research.closure import synthetic as S
    path = tmp_path / 'derivs-copy.db'
    names = ['S0/USDT', 'S1/USDT']
    C.write_funding(path, names, n_bars=300)
    cut = S.EPOCH_MS + 200 * S.TF_MS
    ext = cdata.load_external(['funding'], None, path, names, cut)
    assert not ext['gaps']
    for s in names:
        lin = ext['provenance'][f'funding:{s}']
        assert lin['instrument_ids'] == [f'offline:deriv:{s}'] and lin['sources'] == ['offline:synthetic-context']
        assert lin['rows'] > 0 and lin['revision_digest'] and lin['last_ms'] < cut and lin['read_mode'] != 'retrospective'
    from trader.research.closure import auto
    copy = auto.copy_database(path, tmp_path / 'derivs-copy2.db')['path']      # a settled byte-identical copy (the read left a -wal)
    again = cdata.load_external(['funding'], None, copy, names, cut)
    assert again['provenance'] == ext['provenance'] and again['data_id'] == ext['data_id']      # reproducible identity


def test_hedge_deployment_contract_refuses_unverified_venue_modes_explicitly():
    from trader.research.compose import hedge as H
    s = H.make_spec(primary=dict(symbol='ALT/USDT'), hedge=dict(symbol='BTC/USDT'), purpose='protective_beta', account_mode='hedge')
    d = H.deployment_contract(s)
    assert d['status'] == 'REFUSED_UNSUPPORTED_VENUE_MODE' and d['replay_only'] and 'no live multi-leg support' in d['claim']
    assert any('coordinated_account_or_contract_unsupported' in r for r in d['reasons']) and any('account_mode_unverified' in r for r in d['reasons'])
    inv = H.make_spec(primary=dict(symbol='ALT/USD_PERP', contract='inverse', multiplier=10.0), hedge=dict(symbol='BTC/USDT'),
                      purpose='protective_beta', account_mode='netting')
    assert any('inverse COIN-M' in r for r in H.deployment_contract(inv)['reasons'])
    # Report only the implemented caller with the same exact strategy policy.
    from tests.test_strategy_hedge_stops import current
    ok = H.deployment_contract(H.make_spec(primary=dict(symbol='ALT/USDT'), hedge=dict(symbol='BTC/USDT'),
                                           purpose='protective_beta', account_mode='netting', management=current()))
    assert ok['status'] == 'DEPLOYABLE' and not ok['reasons']
