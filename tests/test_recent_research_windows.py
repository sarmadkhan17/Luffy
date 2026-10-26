"""Recent retrospective development cannot borrow independent-test authority."""
import pytest
from trader.research.closure import auto,manifest,study,synthetic as S
from trader.research.closure.store import Store
from trader.core.types import TF_MS


def test_recent_development_uses_latest_closed_data_and_unassigned_test_stays_unavailable(tmp_path):
    path=tmp_path/'recent-snapshot.db';S.write_snapshot(path,n_symbols=28,n_bars=900,seed=31)
    a=dict(auto.DEFAULTS,tf='4h',fit_bars=500,snapshot_dir=str(tmp_path),
        recent_retrospective_development=True,world=False)
    cfg={'risk':{}}
    out=auto.derive_spec(a,cfg,path,[])
    assert 'spec' in out,out
    spec=out['spec'];cut=out['prepared_manifest']['as_of_ms'];step=TF_MS['4h']
    assert spec['dev_end_ms']==cut and spec['fit_start_ms']==cut-500*step
    assert spec['independent_window_assigned'] is False
    store=Store(tmp_path/'closure.db')
    sid=study.create_study(store,spec,now_ms=cut,prepared_manifest=out['prepared_manifest'])
    import json
    saved=json.loads(store.study(sid)['payload'])
    env=store.get_once('envelopes',saved['envelope_id'])
    assert env['development_status']=='ELIGIBLE'
    assert env['protected_status']=='UNAVAILABLE' and env['protected_fingerprint'] is None
    assert any('independent_window_not_assigned' in r for r in env['protected_reasons'])
    assert not store.all_once('looks') and not store.all_once('qualifications')
    assert env['protected_window'][0]>=env['dev_end_ms']+spec['embargo_bars']*step
    store.close()


def test_recent_policy_cannot_relabel_replay_as_retrospective():
    with pytest.raises(ValueError,match='requires_retrospective'):
        auto.recent_development(dict(recent_retrospective_development=True,development_mode='REPLAY'))
    assert not auto.recent_development(dict(development_mode='REPLAY'))


def test_publication_has_no_old_final_test_reserve_in_explicit_recent_policy(tmp_path,monkeypatch):
    from trader.research.closure import publish
    captured={}
    def fake(src,dst,**kw):
        captured.update(kw)
        return dict(path=str(dst),bytes=0,rows=0,revisions=0,as_of_ms=123,consistency='FIXTURE',derivs=None,revision_scope='FIXTURE')
    monkeypatch.setattr(publish,'publish_exact_cut',fake)
    a=dict(auto.DEFAULTS,tf='4h',fit_bars=6570,recent_retrospective_development=True)
    auto.publish_job(dict(now_ms=123,snapshot_dir=str(tmp_path),auto=a,cfg={},deadline_s=30,market_db='FIXTURE'))
    assert captured['coverage_filter']['reserve_bars']==0
    assert captured['coverage_filter']['fit_bars']==6570 and captured['retrospective']
    assert captured['window_bars']<6570+1000
    a['recent_retrospective_development']=False
    auto.publish_job(dict(now_ms=123,snapshot_dir=str(tmp_path),auto=a,cfg={},deadline_s=30,market_db='FIXTURE'))
    assert captured['coverage_filter']['reserve_bars']>2000


def test_calibration_contract_distinguishes_history_length_and_native_timeframe():
    from trader.research.closure import calibration as C
    a=C.search_contract(search={},max_candidates=8,max_revisions=4,tf='4h',development_bars=1620)
    b=C.search_contract(search={},max_candidates=8,max_revisions=4,tf='4h',development_bars=6570)
    c=C.search_contract(search={},max_candidates=8,max_revisions=4,tf='1h',development_bars=6570)
    assert a!=b and b!=c


@pytest.mark.parametrize('tf',['15m','1h','4h'])
def test_generated_calibration_bars_and_receipts_use_native_timeframe(tmp_path,tf):
    import sqlite3,json
    path=tmp_path/(tf+'-synthetic.db');S.write_snapshot(path,n_symbols=1,n_bars=4,tf=tf)
    with sqlite3.connect(path) as db:
        rows=db.execute('SELECT tf,ts FROM candles ORDER BY ts').fetchall()
        assert {r[0] for r in rows}=={tf} and rows[1][1]-rows[0][1]==TF_MS[tf]
        receipt=json.loads(db.execute('SELECT record_json FROM market_revisions ORDER BY event_ms LIMIT 1').fetchone()[0])
        assert receipt['available_at_ms']==rows[0][1]+TF_MS[tf]
