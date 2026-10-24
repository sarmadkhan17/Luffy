"""Configured compositional research must actually own its scheduled slot."""
from types import SimpleNamespace
from trader.scheduling import kernel_runtime as K
from trader.research.closure import study
from trader.research import runner


def test_compositional_route_cannot_be_starved_by_legacy(monkeypatch):
    cfg={'research':{'enabled':True,'closure':{'enabled':True,'slot_budget_s':120}},'mechanism':{'enabled':False}}
    view=SimpleNamespace(cfg=cfg,journal=object())
    obj=SimpleNamespace(kernel=SimpleNamespace(_last_cycle_s=426,_stop=False))
    monkeypatch.setattr(K,'analysis_context',lambda _:view)
    calls=[]
    monkeypatch.setattr(study,'scheduled',lambda cfg,**kw:calls.append(kw) or {'status':'IDLE'})
    def forbidden(*a,**kw):raise AssertionError('legacy discovery consumed new engine slot')
    monkeypatch.setattr(runner,'ResearchRunner',forbidden)
    K.KernelRuntime.research(obj)
    assert len(calls)==1 and calls[0]['slot_remaining_s']==120 and calls[0]['cycle_seconds'] is None
    assert not calls[0]['should_stop']()
    obj.kernel._stop=True
    assert calls[0]['should_stop']()


def test_unconfigured_existing_route_is_preserved(monkeypatch):
    view=SimpleNamespace(cfg={'research':{'enabled':True},'mechanism':{'enabled':False}},journal=object())
    obj=SimpleNamespace(kernel=SimpleNamespace(_last_cycle_s=31))
    monkeypatch.setattr(K,'analysis_context',lambda _:view)
    calls=[]
    monkeypatch.setattr(runner,'ResearchRunner',lambda *a:SimpleNamespace(step=lambda **kw:calls.append(kw)))
    K.KernelRuntime.research(obj)
    assert calls==[{'cycle_seconds':31}]
