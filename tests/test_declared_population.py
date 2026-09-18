import json
from pathlib import Path

import pytest

from trader.observability import declared as C, collector_health as H, learning, investigation
from trader.observability.attention import capture, settings
from trader.observability.store import Store
from trader.cognition import dataset as D
from trader.cognition.contracts import TF_MS

TF=TF_MS['4h']
NOW=200000*TF+10000


def bars(symbol, end=None):
    end=(NOW//TF-1)*TF if end is None else end
    return [[end-(25-i)*TF,100+i,102+i,99+i,101+i,1000+i*i] for i in range(26)]


def decl(n=20):
    return dict(schema_version='pit-dataset-declaration.v1',dataset_id='test',collection_mode='forward',
                start_ms=NOW+TF,discovery_cut_ms=NOW+TF*7,
                universe=[f'S{i}/USDT' for i in range(n)],
                coverage=dict(expected_symbols=[f'S{i}/USDT' for i in range(n)],expected_kinds={},min_rows=1,max_sequence_gap_ms=TF),
                dependence=dict(symbol_groups={'market':[f'S{i}/USDT' for i in range(n)]},window_pad_ms=TF),
                limits=dict(max_records=256,max_rows=512))


def config(tmp_path,d):
    dp=tmp_path/'d.json'; fp=tmp_path/'f.json'
    dp.write_text(json.dumps(d));fp.write_text(json.dumps(D.freeze(d,NOW-1000,code_manifest={'test':'abc'})))
    return dict(declaration=str(dp),receipt=str(fp))


def test_membership_independent_of_selection_and_cap():
    d=decl()
    seen=[]
    def fetch(s): seen.append(s); return bars(s)
    for trading in [d['universe'][::-1], ['OTHER/USDT']*30, []]:
        kernel=capture({},trading,'kernel',settings(),NOW)
        e=C.collect(d,fetch,lambda:NOW)
        assert len(kernel['input']['membership'])<=16
        assert [m['symbol'] for m in e['input']['membership']]==d['universe']
        assert len(e['scope']['availability_receipts'])==20
        assert e['scope']['excluded_count']==0
    assert set(seen)==set(d['universe'])


def test_capacity_never_truncates():
    e=C.collect(decl(64),bars,lambda:NOW)
    assert len(e['input']['membership'])==64
    with pytest.raises(ValueError,match='capacity'): C.collect(decl(65),bars,lambda:NOW)


def test_errors_stale_missing_and_retry():
    d=decl(4)
    def fetch(s):
        if s=='S0/USDT': raise TimeoutError()
        if s=='S1/USDT': return []
        if s=='S2/USDT': return bars(s,(NOW//TF-2)*TF)
        return bars(s)
    e=C.collect(d,fetch,lambda:NOW)
    assert [r['status'] for r in e['scope']['availability_receipts']]==['error','unavailable','stale','available']
    assert len(e['input']['membership'])==4
    retry=C.collect(d,bars,lambda:NOW+100)
    assert all(r['status']=='available' for r in retry['scope']['availability_receipts'])
    assert all(b['available_ms']==NOW+100 for b in retry['input']['candles'])


def test_malformed_no_partial_or_fabricated_data():
    bad=bars('S0/USDT'); bad[-1][4]='nan'
    e=C.collect(decl(1),lambda _:bad,lambda:NOW)
    assert e['input']['candles']==[]
    assert e['scope']['availability_receipts'][0]['status']=='error'


def test_persistence_provenance_revisions_and_bound_read(tmp_path,monkeypatch):
    monkeypatch.setattr(H,'clock_ms',lambda:NOW+1000)
    d=decl(20); cfg=config(tmp_path,d); dest=tmp_path/'collector'
    h=C.run(cfg,dest,bars,lambda:NOW)
    source,ev=H.bound_snapshot(dest/'attention.db',now_ms=NOW)
    assert ev['accepted'] and len(source[0]['rows'])==20
    assert len(source[1])==20 and source[2]==[]
    old={v['version_id'] for vs in source[1].values() for v in vs}
    C.run(cfg,dest,bars,lambda:NOW+100)
    again,_=H.bound_snapshot(dest/'attention.db',now_ms=NOW+100)
    assert old=={v['version_id'] for vs in again[1].values() for v in vs}
    assert {v['available_ms'] for vs in again[1].values() for v in vs}=={NOW}
    def revised(s):
        b=bars(s); b[-1][5]+=1; return b
    C.run(cfg,dest,revised,lambda:NOW+200)
    changed,_=H.bound_snapshot(dest/'attention.db',now_ms=NOW+200)
    assert sum(v['available_ms']==NOW+200 for vs in changed[1].values() for v in vs)==20
    with pytest.raises(H.Refused): H.bound_snapshot(dest/'attention.db',now_ms=NOW+400000)
    health=json.loads((dest/'attention_health.json').read_text());health['status']='collecting'
    C.publish(dest/'attention_health.json',health)
    with pytest.raises(H.Refused): H.bound_snapshot(dest/'attention.db',now_ms=NOW+200)


def test_failure_invalidates_previous_success(tmp_path,monkeypatch):
    monkeypatch.setattr(H,'clock_ms',lambda:NOW)
    cfg=config(tmp_path,decl(2)); dest=tmp_path/'collector'
    C.run(cfg,dest,bars,lambda:NOW)
    Path(cfg['receipt']).write_text('{}')
    with pytest.raises(Exception): C.run(cfg,dest,bars,lambda:NOW+10)
    with pytest.raises(H.Refused): H.bound_snapshot(dest/'attention.db',now_ms=NOW+10)


def test_opt_in_no_silent_fallback(tmp_path):
    assert C.source_path(tmp_path)==tmp_path/'attention.db'
    (tmp_path/'declared_population.enabled').touch()
    assert C.source_path(tmp_path)==tmp_path/'declared-population/attention.db'


def test_consumers_see_whole_universe(tmp_path,monkeypatch):
    monkeypatch.setattr(H,'clock_ms',lambda:NOW)
    cfg=config(tmp_path,decl(20));dest=tmp_path/'collector'
    C.run(cfg,dest,bars,lambda:NOW)
    learning.step(dest/'attention.db',tmp_path/'learning.db',now_ms=NOW)
    # New scan after protocol activation.
    C.run(cfg,dest,bars,lambda:NOW+100)
    result=learning.step(dest/'attention.db',tmp_path/'learning.db',now_ms=NOW+100)
    assert result['reason']=='forward_scan_processed'
    import sqlite3
    with sqlite3.connect(tmp_path/'learning.db') as db:
        assert db.execute('select count(distinct symbol) from episodes').fetchone()[0]==20


def test_per_symbol_first_seen_is_fetch_completion(tmp_path):
    times=iter([NOW,NOW+1,NOW+2,NOW+3,NOW+4,NOW+5])
    e=C.collect(decl(2),bars,lambda:next(times))
    receipts=e['scope']['availability_receipts']
    assert e['as_of_ms']==NOW+5
    store=Store(tmp_path/'attention.db',e['capture_settings'])
    try:
        store.write(e)
        seen=dict(store.db.execute('select symbol,min(first_seen_ms) from versions group by symbol'))
        assert seen=={r['symbol']:r['observed_ms'] for r in receipts}
    finally: store.close()


def test_snapshot_crossing_bar_boundary_is_stale():
    ticks=iter([NOW,NOW,NOW,(NOW//TF+1)*TF])
    e=C.collect(decl(1),bars,lambda:next(ticks))
    assert e['scope']['availability_receipts'][0]['status']=='stale'


def test_timeout_is_bounded(monkeypatch):
    import subprocess
    def timeout(*args,**kwargs):
        assert kwargs['timeout']==6
        raise subprocess.TimeoutExpired(args[0],6)
    monkeypatch.setattr(C.subprocess,'run',timeout)
    b,r=C.observe('S0/USDT',C.fetch,lambda:NOW)
    assert not b and r['error']=='TimeoutExpired' and r['retry_required']


def test_population_receipts_include_availability_and_refuse_wrong_binding(tmp_path,monkeypatch):
    monkeypatch.setattr(H,'clock_ms',lambda:NOW)
    d=decl(20); d['start_ms']=NOW-500; cfg=config(tmp_path,d)
    cfg.update(export_directory=str(tmp_path/'exports'),local_ledgers={})
    dest=tmp_path/'collector'; ledger=tmp_path/'learning.db'
    C.run(cfg,dest,bars,lambda:NOW)
    learning.step(dest/'attention.db',ledger,now_ms=NOW,population_config=cfg)
    C.run(cfg,dest,bars,lambda:NOW+100)
    result=learning.step(dest/'attention.db',ledger,now_ms=NOW+100,population_config=cfg)
    assert result['reason']=='forward_scan_processed'
    events=[json.loads(p.read_text()) for p in (tmp_path/'exports/forecast').glob('*.json')]
    scans=[e for e in events if e['kind']=='population']
    assert scans
    payload=scans[-1]['source']
    assert payload['missing_symbols']==[]
    assert len(payload['collection_scope']['availability_receipts'])==20
    # Persist a fresh scan for a different declaration; the consumer must NOT process it.
    other=dict(d,dataset_id='other'); othercfg=config(tmp_path,other)
    C.run(othercfg,dest,bars,lambda:NOW+200)
    config(tmp_path,d)
    result=learning.step(dest/'attention.db',ledger,now_ms=NOW+200,population_config=cfg)
    assert result['reason']=='declared_binding_mismatch'
