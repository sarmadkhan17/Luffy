"""Volume-first selection must not acquire broad history or grant execution."""
from copy import deepcopy
from pathlib import Path
import pytest
import yaml
from trader import attention_admission as A
from trader.data.broad_crypto import observe
from tests.test_hierarchical_admission import broad_kernel, CUT, observation


def config():
    return yaml.safe_load(Path('config.yaml').read_text())


def test_production_volume_policy():
    cfg=config();p=A.AdmissionPolicy.from_config(cfg)
    assert cfg['attention']['selection']=='volume'
    assert (p.max_deep_slots,p.event_reserve,p.exploration_reserve)==(30,0,0)


def test_real_broad_volume_does_not_read_history(tmp_path,monkeypatch):
    monkeypatch.setattr('trader.data.broad_crypto.time.time',lambda:CUT/1000)
    cfg=config();k=broad_kernel(tmp_path,cfg,45)
    tickers=k.universe.ex.fetch_tickers.return_value
    for i,t in enumerate(tickers.values()): t['quoteVolume']=1000+i
    o,frames,membership=observe(k,[])
    assert not frames and not membership
    k.feed.cached_ohlcv.assert_not_called();k.feed.fetch_ohlcv.assert_not_called()
    assert k.universe.ex.fetch_tickers.call_count==1
    r=A.persist(k.journal,o,A.AdmissionPolicy.from_config(cfg))
    assert r['admitted_symbols']==[f'S{i:02}/USDT' for i in range(44,14,-1)]
    assert len(A.candidates(r))==30
    assert A.latest(k.journal)['receipt_id']==r['receipt_id']
    assert all(c['authority']=='INVESTIGATE_ONLY' for c in A.candidates(r))
    broken=deepcopy(r);broken['observation']['rows']['S44/USDT']['quote_volume']=1e20
    broken['receipt_id']=A.digest({k:v for k,v in broken.items() if k!='receipt_id'})
    with pytest.raises(ValueError): A.verify_receipt(broken)


def test_volume_ties_missing_and_exposure():
    o=observation(35);o['selection']='volume'
    for row in o['rows'].values(): row['quote_volume']=10
    o['rows']['S00/USDT']['quote_volume']=0
    o['rows']['S01/USDT']['quote_volume']=None
    o['rows']['S02/USDT']['quality']='INVALID'
    p=A.AdmissionPolicy(30,0,0,0)
    r=A.admit(o,p,A.initial_state())
    assert r['admitted_symbols']==[f'S{i:02}/USDT' for i in range(3,33)]
    assert r['roles']['exposure']==('HELD/USDT',)
    assert not r['roles']['event'] and not r['roles']['exploration']
    assert not r['required_deferred']
    A.verify_receipt(r)


def test_historical_receipt_remains_replayable():
    old=A.admit(observation(),A.AdmissionPolicy(12,2,2,0),A.initial_state())
    assert A.verify_receipt(old)['policy_version']=='hierarchical-crypto.v1'


def test_volume_cycle_reaches_strategies_and_keeps_holdings(tmp_path,monkeypatch):
    from tests.test_hierarchical_admission import full_cycle_kernel
    k=full_cycle_kernel(tmp_path,config(),monkeypatch)
    stats=k.cycle()
    assert stats['admission']['deep_admitted']==30
    assert stats['decisions']==stats['deep_processed']==30
    assert k.orchestrator.decide.call_count==30
    assert stats['entries']==0
    assert k._manage_one.call_count==15
    assert k._history_preparation['status']=='NOT_REQUIRED'
