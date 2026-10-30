"""Held positions retain exact native inputs outside scan and paper admission."""
from types import SimpleNamespace as NS
from unittest.mock import Mock
import pytest
from tests.test_coordinated_trial_lifecycle import packet  # noqa: F401
from tests.test_strategy_hedge_stops import packet_with_stop
from trader.core.journal import Journal
from trader.core.types import Position,Side
from trader.engine import coordinated_inputs as I
from trader.strategy import factory_handoff as F
from trader.research.closure import coordinated_trial as T


def held(tmp_path,packet,*,bad_hedge=False):
    j=Journal(tmp_path/'held.db')
    p=packet_with_stop(packet,'close_group')
    issued=F.create_version(j,{},dict(kind=T.KIND,packet=p),at_ms=1)
    v=F.load_version(j,issued['version_id'])
    installed=F.record_coordinated_paper_install(j,v['version_id'],at_ms=1)
    hs=v['lineage']['research']['multi_leg']['hedge_spec']
    for tid,leg in [('p','primary'),('h','hedge')]:
        identity=dict(status='VERIFIED',strategy_id=v['strategy_id'],version_id=v['version_id'],
            spec_hash=v['spec_hash'],spec_sha256=v['spec_hash'],install_id=installed['install']['install_id'],
            exit_semantics_id=F._expected_exit_semantics(v),
            coordinated=dict(group_id='fixture',leg=leg,management_policy=hs['management']))
        if bad_hedge and leg=='hedge':identity['spec_hash']='wrong'
        j.add_trade(Position(tid,hs[leg]['symbol'],Side.LONG if leg=='primary' else Side.SHORT,
            1.,100.,100.,strategy_id=v['strategy_id'],entry_identity=identity))
    return j,v


def test_both_frozen_legs_keep_native_inputs_without_population_or_paper_worker(tmp_path,packet):
    j,v=held(tmp_path,packet)
    plans,errors=I.plans(j)
    assert errors=={}
    assert set(plans)=={'A/USDT','B/USDT'}
    assert all(v['spec']['timeframe'] in p.limits for p in plans.values())
    assert all(0<n<=4096 for p in plans.values() for n in p.limits.values())
    assert not plans['B/USDT'].requires  # own stop uses hedge candles, not primary entry factors
    candles,derivs=I.collector_needs(plans)
    assert candles['B/USDT']=={v['spec']['timeframe']}
    j.close_trade('h',100.,0.,'fixture')
    plans,errors=I.plans(j)
    assert set(plans)=={'A/USDT','B/USDT'} and not errors
    assert plans['B/USDT'].limits[v['spec']['timeframe']]>=v['lineage']['research']['multi_leg']['hedge_spec']['ratio']['lookback']+2


def test_bad_frozen_identity_is_unavailable_without_hiding_other_leg(tmp_path,packet):
    j,v=held(tmp_path,packet,bad_hedge=True)
    plans,errors=I.plans(j)
    assert set(plans)=={'A/USDT','B/USDT'}  # valid primary still needs the paired series
    assert errors=={'B/USDT':'coordinated_held_input_identity_differs'}


def test_failed_snapshot_still_publishes_collector_demand(tmp_path,monkeypatch):
    from tests.test_split_kernel_runtime import minimal
    from trader.scheduling.scan_plan import Inputs
    k,r=minimal(tmp_path)
    k._manages_exits=lambda:False
    plan=Inputs(limits={'2h':80})
    view=NS(_exposure_symbols=lambda:{'B/USDT'},_refresh_btc_context=Mock(),_peer_frames=lambda symbols:{},
        _service_exposure=lambda *a:0,_exposure_errors=['snapshot_unavailable'],
        _held_input_plans={'B/USDT':plan},_portfolio_holdings_market={})
    monkeypatch.setattr('trader.scheduling.kernel_runtime.analysis_context',lambda runtime:view)
    with pytest.raises(ValueError,match='scheduled_exposure_incomplete'):r.positions()
    assert r.held_coordinated_needs==({'B/USDT':{'2h'}},[])
    assert r.positions_at is None


def test_active_shortlisted_version_warms_paired_ratio_before_first_hedge(tmp_path,packet,monkeypatch):
    from trader.engine import coordinated_intake as intake
    from tests.test_coordinated_intake import receipt
    import time
    j,v=held(tmp_path,packet)
    monkeypatch.setattr(intake,'candidates',lambda journal:[v])
    now=int(time.time()*1000)
    plans,errors=I.active_plans(j,receipt(now,'A/USDT'),at_ms=now)
    assert not errors and set(plans)=={'A/USDT','B/USDT'}
    assert not plans['B/USDT'].requires
    tf=v['spec']['timeframe'];lookback=v['lineage']['research']['multi_leg']['hedge_spec']['ratio']['lookback']
    assert all(lookback+2<=p.limits[tf]<=4096 for p in plans.values())
    assert I.active_plans(j,receipt(now,'OTHER/USDT'),at_ms=now)==({},{})
    with pytest.raises(ValueError,match='shortlist_unavailable'):
        I.active_plans(j,receipt(now,'A/USDT'),at_ms=now+60001)
