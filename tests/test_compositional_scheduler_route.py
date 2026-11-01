"""Configured compositional research must actually own its scheduled slot."""
from types import SimpleNamespace
from trader.scheduling import kernel_runtime as K
from trader.research.closure import study
from trader.research import runner
from trader.observability import managed


def test_compositional_route_cannot_be_starved_by_legacy(monkeypatch):
    cfg={'research':{'enabled':True,'closure':{'enabled':True,'slot_budget_s':120}},'mechanism':{'enabled':False}}
    view=SimpleNamespace(cfg=cfg,journal=object())
    obj=SimpleNamespace(kernel=SimpleNamespace(_last_cycle_s=426,_stop=False,_runtime_revision='a'*40))
    monkeypatch.setattr(K,'analysis_context',lambda _:view)
    calls=[];launches=[];released=[];receipt={'unit':'research-fixture.service'}
    monkeypatch.setattr(managed,'launch_role',lambda role,root,**kw:launches.append((role,kw)) or receipt)
    monkeypatch.setattr(managed,'wait_role',lambda handle,root,revision,**kw:calls.append((handle,revision,kw)) or {'state':'SUCCEEDED'})
    monkeypatch.setattr(managed,'release_role',lambda handle:released.append(handle) or True)
    def forbidden(*a,**kw):raise AssertionError('legacy discovery consumed new engine slot')
    monkeypatch.setattr(runner,'ResearchRunner',forbidden)
    monkeypatch.setattr(study,'scheduled',forbidden)  # numerical work belongs to the managed worker
    K.KernelRuntime.research(obj)
    assert launches==[('research',{'revision':'a'*40})] and released==[receipt]
    assert len(calls)==1 and calls[0][:2]==(receipt,'a'*40) and calls[0][2]['timeout_s']==150
    assert not calls[0][2]['should_stop']()
    obj.kernel._stop=True
    assert calls[0][2]['should_stop']()
    assert obj._research_managed_status['mechanism_pending'] is False and obj._managed_research_receipt is None


def test_unconfigured_existing_route_is_preserved(monkeypatch):
    view=SimpleNamespace(cfg={'research':{'enabled':True},'mechanism':{'enabled':False}},journal=object())
    obj=SimpleNamespace(kernel=SimpleNamespace(_last_cycle_s=31))
    monkeypatch.setattr(K,'analysis_context',lambda _:view)
    calls=[]
    monkeypatch.setattr(runner,'ResearchRunner',lambda *a:SimpleNamespace(step=lambda **kw:calls.append(kw)))
    K.KernelRuntime.research(obj)
    assert calls==[{'cycle_seconds':31}]
