"""Offline full-cohort repair, queue fairness and provenance proofs.

The requested 439/20/412/7 distribution is an explicitly synthetic frozen
fixture. Event 2338 is separately checked with its actual retained counts.
"""
from collections import Counter
from copy import deepcopy
import gzip
import json
import math
import os
import resource
import time
from pathlib import Path
from types import SimpleNamespace as NS
import socket
from unittest.mock import Mock

import pandas as pd
import pytest

from trader import attention_admission as A
from trader.cognition.attention import CognitionConfig
from trader.core.journal import Journal
from trader.data import market_provenance as mp
from trader.data.feed import DataFeed
from trader.data.attention_history import coverage, prepare, decision_state, STATE_KEY
from trader.observability.attention import capture, evaluate_snapshot, settings

TF = 14_400_000
CUT = 125000 * TF + 60000
SYMBOLS = tuple(f'S{i:03}/USDT' for i in range(439))


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(socket.socket, 'connect', Mock(side_effect=AssertionError('offline fixture')))


class Venue:
    id = 'binanceusdm'
    urls = {'api': {'fapiPublic': 'https://offline.invalid'}}

    def __init__(self, now=CUT, unavailable=()):
        self.now = now
        self.unavailable = set(unavailable)
        self.calls = []

    def market(self, symbol):
        return dict(id=symbol.split(':')[0].replace('/', ''), contract=True)

    def rows(self, symbol, count=154, stale=0):
        end = (self.now//TF-stale)*TF
        i = int(symbol[1:4]) if symbol.startswith('S') and symbol[1:4].isdigit() else 0
        out = []
        for j in range(count):
            price = 100 + i + .3*j + math.sin(j*.8+i*.17)*2
            out.append([end-(count-j)*TF, price, price+2, price-2, price+.2,
                        1000 + j*3 + (i % 13)*math.cos(j)*10])
        return out

    def fetch_ohlcv(self, symbol, tf, since=None, limit=1000):
        assert tf == '4h'
        self.calls.append((symbol, tf, since, limit))
        if symbol in self.unavailable:
            raise TimeoutError('frozen source unavailable')
        rows = self.rows(symbol)
        return ([r for r in rows if r[0] >= since] if since is not None else rows)[-limit:]


def feed_at(path, venue=None):
    venue = venue or Venue()
    return DataFeed(exchange=venue, db_path=path, clock_ms=lambda:venue.now)


def seed(feed):
    for i,s in enumerate(SYMBOLS[:27]):
        frame = feed._received_frame(feed.ex.rows(s, stale=0 if i < 20 else 1), s, '4h')
        feed._merge_save(s, '4h', None, frame)


def scan(feed):
    frames = {}
    for s in SYMBOLS:
        df = feed.cached_ohlcv(s, '4h', limit=153, as_of_ms=feed._clock_ms())
        if df is not None: frames[s] = {'4h':df}
    cfg = settings(); cfg['max_symbols'] = 439
    ev = capture(frames, SYMBOLS, 'da05-frozen', cfg, feed._clock_ms())
    result = evaluate_snapshot(ev)
    o = dict(schema='broad-crypto.v1', source_cut_ms=feed._clock_ms(), source_identity='frozen-439',
        rows={s:dict(symbol=s, asset_class='CRYPTO', quality='VALID', available_at_ms=feed._clock_ms(), missing_reasons=[]) for s in SYMBOLS},
        peer_cohort=list(SYMBOLS[:30]), strategy_relevance={s:['fixture-strategy'] for s in SYMBOLS[:60]},
        required_by_strategy={SYMBOLS[438]:['fixture-strategy']}, required_symbols=[SYMBOLS[438]],
        context_anchors=list(SYMBOLS[:2]), exposure_required=[SYMBOLS[437]],
        attention_event=ev, salience_rows={r['symbol']:r for r in result['rows']},
        salience_config=result['config'], salience_anchor_ms=feed._clock_ms()//TF*TF,
        status='VALID' if all(r['eligible'] for r in result['rows']) else 'DEGRADED_CACHED_HISTORY')
    if hasattr(feed, 'prepare_history'):
        o['history_coverage'] = coverage(result, SYMBOLS, result['config']['min_salience'])
    return o, result


def drain(feed, tasks):
    # Deterministic execution of the actual single-worker body.
    feed._acquisition_lock.release()
    feed._start_background_worker()
    while tasks:
        tasks.pop(0)['target']()
    assert not feed._acquisition_lock.locked()
    feed._acquisition_lock.acquire()


def scheduler(monkeypatch):
    tasks = []
    monkeypatch.setattr('trader.data.feed.threading.Thread', lambda **kw: NS(start=lambda:tasks.append(kw)))
    return tasks


def test_frozen_requested_failure_and_exact_historical_event(tmp_path):
    f = feed_at(tmp_path/'frozen.db'); seed(f)
    obs, result = scan(f)
    assert Counter(r['status'] for r in result['rows']) == {'ok':20,'missing':412,'stale':7}
    r = A.admit(obs, A.AdmissionPolicy(12,2,2,0), A.initial_state())
    assert r['degraded_state'] == 'DEGRADED_CACHED_HISTORY'
    assert len(r['admitted_symbols']) <= 12
    retained = json.loads(gzip.decompress(Path('tests/fixtures/da01-event2338.json.gz').read_bytes()))
    assert len(retained['observation']['rows']) == 439
    assert Counter(r['status'] for r in retained['observation']['salience_rows'].values()) == {'ok':5,'missing':407,'stale':27}
    assert not f.ex.calls


def test_full_cohort_progress_restart_and_bounded_resources(tmp_path, monkeypatch):
    tasks = scheduler(monkeypatch)
    path = tmp_path/'candles.db'; f = feed_at(path); seed(f)
    obs, result = scan(f)
    receipt = A.admit(obs, A.AdmissionPolicy(12,2,2,0), A.initial_state())
    frozen = A.canonical(receipt)
    j = Journal(tmp_path/'journal.db'); k = NS(feed=f, journal=j)
    f.background_acquisition = True; f._acquisition_lock.acquire()
    maximum = 0
    submission_ms = []
    t0, cpu0 = time.perf_counter(), time.process_time()
    checkpoint = None
    for cycle in range(112):
        started = time.perf_counter()
        report = prepare(k, receipt)
        submission_ms.append((time.perf_counter()-started)*1000)
        assert report['submitted'] <= 16
        maximum = max(maximum, report['queue']['pending'])
        drain(f, tasks)
        if cycle == 55:
            interim, interim_result = scan(f)
            checkpoint = sum(r['eligible'] for r in interim_result['rows'])
            # Interrupted submissions remain missing until a later sweep.
            assert checkpoint == 431
        if cycle == 20:
            # Simulate process loss with a real pending queue. Reconstruct
            # demand from retained candles and persisted cursor, not RAM.
            prepare(k, receipt)
            assert f.background_acquisition_health()['pending'] > 0
            f.cancel_background_acquisition(); f._acquisition_lock.release()
            f.db.close(); del f._local.conn
            f = feed_at(path, f.ex); f.background_acquisition = True
            f._acquisition_lock.acquire(); k = NS(feed=f, journal=Journal(j.db_path))
    after, after_result = scan(f)
    assert sum(r['eligible'] for r in after_result['rows']) == 439
    assert maximum <= 16 and len(f._cache) <= 128
    assert f.background_acquisition_health()['outcomes_retained'] <= 128
    assert max(c[3] for c in f.ex.calls) <= 1000
    assert len(f.ex.calls) == 419  # 412 missing + seven stale, not all 439
    assert A.canonical(A.verify_receipt(json.loads(frozen))) == frozen
    replay = evaluate_snapshot(obs['attention_event'])
    assert replay['rows'] == result['rows'] and replay['observations'] == result['observations']
    assert after['history_coverage']['counts']['decision_ready'] == 0
    assert after['history_coverage']['counts']['ranking_eligible'] == 439
    assert json.loads(k.journal.kv_get(STATE_KEY))['generation'] == 113
    if output := os.environ.get('DA05_METRICS_PATH'):
        path = Path(output).resolve()
        assert str(path).startswith('/mnt/luffy-recovery/')
        path.write_text(json.dumps(dict(cohort=439, cycles=112,
            ranking_eligible_before=sum(r['eligible'] for r in result['rows']),
            ranking_eligible_after_56=checkpoint, ranking_eligible_after=439,
            before_status_counts=dict(Counter(r['status'] for r in result['rows'])),
            after_status_counts=dict(Counter(r['status'] for r in after_result['rows'])),
            source_requests=len(f.ex.calls), symbols_acquired=len({c[0] for c in f.ex.calls}),
            max_requested_bars=max(c[3] for c in f.ex.calls), queue_max_pending=maximum,
            queue_health=f.background_acquisition_health(), cache_series=len(f._cache),
            wall_seconds=time.perf_counter()-t0, cpu_seconds=time.process_time()-cpu0,
            peak_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            candle_db_bytes=(tmp_path/'candles.db').stat().st_size,
            submission_ms_max=max(submission_ms), historical_attention_exact=True,
            historical_receipt_exact=True),indent=2,sort_keys=True))
    f._acquisition_lock.release()


def test_queue_reserve_priority_and_exploration_no_starvation(tmp_path, monkeypatch):
    tasks = scheduler(monkeypatch); f = feed_at(tmp_path/'q.db')
    f.background_acquisition = True; f._acquisition_lock.acquire()
    for s in SYMBOLS[:110]: f.prepare_history(s)
    assert f.background_acquisition_health()['pending'] == 96
    assert f._queue_background_request('HELD/USDT','4h',154,False,26)
    assert f._queue_background_request('REQUIRED/USDT','4h',154,False,26)
    drain(f,tasks)
    assert [c[0] for c in f.ex.calls[:2]] == ['HELD/USDT','REQUIRED/USDT']
    assert len(f.ex.calls) == 98
    assert f.background_acquisition_health()['refused_full'] == 14
    f._acquisition_lock.release()


def test_inflight_duplicates_coalesce_but_larger_requirement_survives(tmp_path, monkeypatch):
    tasks=scheduler(monkeypatch); f=feed_at(tmp_path/'q.db')
    f.background_acquisition=True; f._acquisition_lock.acquire()
    original=f.ex.fetch_ohlcv
    def fetch(symbol,tf,since=None,limit=1000):
        assert f.prepare_history(symbol) == 'queued_or_coalesced'
        assert not f._acquisition_queue
        assert f._queue_background_request(symbol,tf,400,False,30)
        assert len(f._acquisition_queue)==1
        f.ex.fetch_ohlcv=original
        return original(symbol,tf,since,limit)
    f.ex.fetch_ohlcv=fetch
    f.prepare_history(SYMBOLS[30]); drain(f,tasks)
    assert f.background_acquisition_health()['coalesced'] == 1
    assert f.background_acquisition_health()['completed'] == 2
    f._acquisition_lock.release()


def test_unavailable_source_refuses_and_does_not_strand_other_history(tmp_path,monkeypatch):
    tasks=scheduler(monkeypatch); f=feed_at(tmp_path/'q.db',Venue(unavailable=[SYMBOLS[30]]))
    f.background_acquisition=True; f._acquisition_lock.acquire()
    f.prepare_history(SYMBOLS[30]); f.prepare_history(SYMBOLS[31]); drain(f,tasks)
    assert f.cached_ohlcv(SYMBOLS[30],'4h',as_of_ms=CUT) is None
    assert f.cached_ohlcv(SYMBOLS[31],'4h',as_of_ms=CUT) is not None
    assert f.prepare_history(SYMBOLS[30]) == 'retry_cooldown'
    assert f.background_acquisition_health()['failed'] == 1
    f._acquisition_lock.release()


def test_legacy_inventory_gap_late_acquisition_and_multiyear_reuse(tmp_path,monkeypatch):
    tasks=scheduler(monkeypatch); venue=Venue(); f=feed_at(tmp_path/'q.db',venue)
    s=SYMBOLS[30]
    raw=venue.rows(s, count=5000)
    inventory=f._frame(raw)
    # Multi-year old projection is not a qualified decision source.
    f._merge_save(s,'4h',None,inventory)
    assert f.cached_ohlcv(s,'4h',as_of_ms=CUT) is None
    f.background_acquisition=True; f._acquisition_lock.acquire()
    f.prepare_history(s); drain(f,tasks)
    assert f.cached_ohlcv(s,'4h',as_of_ms=CUT-1) is None
    current=f.cached_ohlcv(s,'4h',as_of_ms=CUT)
    assert len(current)==154 and current['available_at_ms'].eq(CUT).all()
    replay=f.replay_ohlcv(s,'4h')
    # the newest two 4h bars arrived inside interval + TTL of their close; 152 older backfills stay retrospective
    assert len(replay)==2 and replay['available_at_ms'].eq(CUT).all()
    # Old inventory remains retained; only a bounded current window acquired.
    assert f.db.execute('SELECT count(*) FROM candles WHERE symbol=?',(s,)).fetchone()[0]==5000
    assert len(venue.calls)==1 and venue.calls[0][3]==154
    # Fresh qualified store survives restart with zero acquisition.
    f2=feed_at(tmp_path/'q.db',venue)
    assert len(f2.fetch_ohlcv(s,'4h',limit=154,min_bars=26))==154
    assert len(venue.calls)==1
    f._acquisition_lock.release()


def test_missing_grid_bar_is_reacquired_not_silently_skipped(tmp_path,monkeypatch):
    tasks=scheduler(monkeypatch); f=feed_at(tmp_path/'q.db')
    raw=f.ex.rows(SYMBOLS[30],count=180)
    gap_ts=raw[-5][0]; del raw[-5]
    f._merge_save(SYMBOLS[30],'4h',None,f._received_frame(raw,SYMBOLS[30],'4h'))
    f.background_acquisition=True;f._acquisition_lock.acquire()
    f.prepare_history(SYMBOLS[30]);drain(f,tasks)
    obs,_=scan(f)
    assert obs['salience_rows'][SYMBOLS[30]]['eligible']
    assert len(f.ex.calls)==1
    _, _, since, limit=f.ex.calls[0]
    assert since is not None and since <= gap_ts
    assert limit == f.HISTORY_REQUEST_BARS == 154
    f._acquisition_lock.release()


def test_states_do_not_promote_tickers_counts_or_admission_to_decision_ready(tmp_path):
    f=feed_at(tmp_path/'q.db');seed(f);obs,result=scan(f)
    c=obs['history_coverage']; assert c['counts']['observed']==439
    assert c['counts']['ranking_eligible']==20 and c['counts']['decision_ready']==0
    assert c['rows'][SYMBOLS[30]]['state']=='OBSERVED'
    assert c['rows'][SYMBOLS[20]]['state']=='WARMING'
    assert all(r['state']=='SALIENT' for r in c['rows'].values() if r['salient'])
    d=NS(id='decision',strategy_signals=[]); snap=NS(market_provenance_parts={'frames':{'4h':['rev']},'as_of_ms':CUT})
    assert decision_state(snap,d)['state']=='WARMING'
    d.strategy_signals=[{'strategy_id':'actual-evaluated-output'}]
    assert decision_state(snap,d)['state']=='DECISION_READY'
    assert decision_state(NS(),d)['state']=='WARMING'


def test_short_history_is_retained_without_decision_authority(tmp_path,monkeypatch):
    tasks=scheduler(monkeypatch); f=feed_at(tmp_path/'young.db')
    rows=f.ex.rows(SYMBOLS[30],count=12)
    f.ex.fetch_ohlcv=lambda *a,**kw:rows
    f.background_acquisition=True;f._acquisition_lock.acquire()
    f.prepare_history(SYMBOLS[30]);drain(f,tasks)
    frame=f.cached_ohlcv(SYMBOLS[30],'4h',as_of_ms=CUT)
    assert len(frame)==12
    assert f._acquisition_outcomes[(SYMBOLS[30],'4h')]['outcome']=='completed_unqualified_history'
    obs,_=scan(f)
    assert obs['salience_rows'][SYMBOLS[30]]['status']=='warmup'
    assert obs['history_coverage']['counts']['ranking_eligible']==0
    f._acquisition_lock.release()


def test_invalid_future_partial_and_degenerate_windows_refuse(tmp_path):
    f=feed_at(tmp_path/'bad.db')
    for i,mode in enumerate(('invalid','future','partial','degenerate')):
        s=SYMBOLS[i];rows=f.ex.rows(s)
        if mode=='invalid': rows[-1][4]=-10.0
        if mode=='partial': rows[-1][0]=CUT//TF*TF
        if mode=='degenerate':
            for row in rows: row[1:5]=[100,101,99,100]
        df=f._received_frame(rows,s,'4h')
        if mode=='future':
            df=mp.annotate(f._frame(rows), instrument_id='binance_usdm:futures:'+s.replace('/',''),
                           source=mp.venue_source(f.ex),kind='candle',received_ms=CUT+1,timeframe='4h',raw=rows)
        f._merge_save(s,'4h',None,df)
    obs,result=scan(f)
    assert not any(r['eligible'] for r in result['rows'])
    assert obs['salience_rows'][SYMBOLS[3]]['status']=='degenerate'
    assert obs['history_coverage']['counts']['decision_ready']==0


def test_receipt_rehash_cannot_forge_history_qualification(tmp_path):
    f=feed_at(tmp_path/'q.db');seed(f);obs,_=scan(f)
    bad=deepcopy(obs)
    bad['history_coverage']['counts']['ranking_eligible']=439
    with pytest.raises(ValueError,match='admission_history_coverage_mismatch'):
        A.admit(bad,A.AdmissionPolicy(12,2,2,0),A.initial_state())
    bad=deepcopy(obs)
    bad['salience_rows'][SYMBOLS[30]].update(eligible=True,salience=10)
    with pytest.raises(ValueError,match='admission_salience_history_mismatch'):
        A.admit(bad,A.AdmissionPolicy(12,2,2,0),A.initial_state())


def test_history_survives_continuous_trading_demand(tmp_path,monkeypatch):
    tasks=scheduler(monkeypatch);f=feed_at(tmp_path/'q.db')
    f.background_acquisition=True;f._acquisition_lock.acquire()
    f.prepare_history(SYMBOLS[30])
    for i in range(20): f._queue_background_request(f'T{i}/USDT','4h',154,False,26)
    drain(f,tasks)
    assert [c[0] for c in f.ex.calls].index(SYMBOLS[30])==8
    assert f.background_acquisition_health()['pending']==0
    f._acquisition_lock.release()


def test_disabled_shutdown_and_corrupt_rotation_refuse(tmp_path):
    f=feed_at(tmp_path/'q.db')
    assert f.prepare_history(SYMBOLS[30])=='refused_background_disabled'
    f.background_acquisition=True;f.cancel_background_acquisition()
    assert f.prepare_history(SYMBOLS[30])=='refused_shutdown'
    obs,_=scan(f);r=A.admit(obs,A.AdmissionPolicy(12,2,2,0),A.initial_state())
    j=Journal(tmp_path/'j.db');j.kv_set(STATE_KEY,'{"schema":"corrupt"}')
    with pytest.raises(ValueError,match='history_rotation_corrupt'):
        prepare(NS(feed=f,journal=j),r)
    assert not f.ex.calls


def test_kernel_prepares_only_after_receipt_commit_and_preserves_admission(tmp_path,monkeypatch):
    from tests.test_hierarchical_admission import broad_kernel, CUT as K_CUT
    import yaml
    cfg=yaml.safe_load(Path('config.yaml').read_text())
    # Historical history-ranking contract; production now uses volume selection.
    cfg['attention'].pop('selection', None)
    cfg['attention']['admission'] = dict(max_deep_slots=12,event_reserve=2,exploration_reserve=2,minimum_slots=0)
    monkeypatch.setattr('trader.data.broad_crypto.time.time',lambda:K_CUT/1000)
    k=broad_kernel(tmp_path,cfg,n=30)
    submitted=[]
    def submit(s,tf):
        current=A.latest(k.journal)
        assert current and current['receipt_id']==k._admission_receipt['receipt_id']
        submitted.append((s,tf));return 'queued_or_coalesced'
    k.feed.prepare_history=submit;k.feed.HISTORY_BATCH=16
    k.feed.background_acquisition_health=lambda:dict(pending=len(submitted),capacity=128)
    r,_=k._admission_plan()
    assert r is not None and submitted
    assert len(submitted)<=16 and r['capacity_used']<=12
    assert r['history_coverage']['counts']['decision_ready']==0
    assert r['degraded_state']=='DEGRADED_CACHED_HISTORY'
    assert not k.feed.fetch_ohlcv.called
    assert k._history_preparation['status']=='SCHEDULED'


def test_qualified_multiyear_store_is_reused_and_only_tail_refreshed(tmp_path):
    f=feed_at(tmp_path/'years.db');s=SYMBOLS[30]
    # 5,000 4h bars: over two years of retained, qualified receipts.
    raw=f.ex.rows(s,count=5000)
    f._merge_save(s,'4h',None,f._received_frame(raw,s,'4h'))
    assert len(f.fetch_ohlcv(s,'4h',limit=154,min_bars=26))==154
    assert not f.ex.calls
    f.ex.now += TF
    f2=feed_at(tmp_path/'years.db',f.ex)
    frame=f2.fetch_ohlcv(s,'4h',limit=154,min_bars=26)
    assert frame is not None and f2._current_tail(frame,'4h',26)
    assert len(f.ex.calls)==1
    assert f.ex.calls[0][2] == raw[-1][0]-f.STORE_OVERLAP_BARS*TF
    assert f.db.execute('SELECT count(*) FROM candles WHERE symbol=?',(s,)).fetchone()[0]==5001
    assert len(f2.cached_ohlcv(s,'4h',as_of_ms=CUT,limit=6000))==5000


def test_worker_start_failure_retains_bounded_demand_for_retry(tmp_path,monkeypatch):
    f=feed_at(tmp_path/'worker.db');f.background_acquisition=True
    def unavailable(): raise RuntimeError('thread unavailable')
    monkeypatch.setattr('trader.data.feed.threading.Thread',lambda **kw:NS(start=unavailable))
    assert f.prepare_history(SYMBOLS[30])=='queued_or_coalesced'
    assert len(f._acquisition_queue)==1 and not f._acquisition_lock.locked()
    assert f.background_acquisition_health()['last_outcome']=='worker_start_failed:RuntimeError'
    tasks=scheduler(monkeypatch)
    f._start_background_worker();tasks.pop(0)['target']()
    assert not f._acquisition_queue
    assert len(f.ex.calls)==1


def test_invalid_retained_prefix_reacquires_window_without_tail_certification(tmp_path,monkeypatch):
    tasks=scheduler(monkeypatch);f=feed_at(tmp_path/'invalid-prefix.db');s=SYMBOLS[30]
    rows=f.ex.rows(s); invalid_ts=rows[-40][0]; rows[-40][5]=-1
    f._merge_save(s,'4h',None,f._received_frame(rows,s,'4h'))
    f.background_acquisition=True;f._acquisition_lock.acquire()
    f.prepare_history(s);drain(f,tasks)
    current=f.cached_ohlcv(s,'4h',as_of_ms=CUT,limit=154)
    assert len(current)==154 and current['quality'].eq('VALID').all()
    assert len(f.ex.calls)==1
    _, _, since, limit=f.ex.calls[0]
    assert since is not None and since <= invalid_ts
    assert limit == f.HISTORY_REQUEST_BARS == 154
    f._acquisition_lock.release()
