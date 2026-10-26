"""Development leads are requalified numerically before any paper install.
Fixture development rows never grant admission by themselves."""
from types import SimpleNamespace
from unittest.mock import Mock
from tests.test_compose_core import spec
from trader.research.compose import graph
from trader.research.closure import paper_proposals as P
from trader.research.closure.store import Store
from trader.research.closure import digest
from trader.kernel import Kernel
from trader.strategy import factory_handoff as F


def store_lead(tmp_path,policy='CONFIGURED'):
    store=Store(tmp_path/'research.db')
    candidate=graph.make_candidate(spec(failure_conditions=['Net returns fail after costs','No improvement over the control'],long={'cmp':'gt','a':{'f':'ret24'},'b':{'lit':0}},
                                        short={'cmp':'lt','a':{'f':'ret24'},'b':{'lit':0}}))
    combo=graph.realize(candidate,'fixture-env')
    store.put_once('candidates',candidate['candidate_id'],candidate)
    store.put_once('envelopes','fixture-env',dict(policy_class=policy,discovery_symbols=['A','B']))
    result=dict(stage='DEVELOPMENT',outcome='DEVELOPMENT_POSITIVE',
        candidate_id=candidate['candidate_id'],result_id='fixture-result',envelope_id='fixture-env',
        combo=combo.as_dict(),combo_hash=combo.hash,data_mode='RETROSPECTIVE')
    result['result_id']=digest({k:v for k,v in result.items() if k!='result_id'})
    store.put_run('fixture-run',result)
    path=store.path;store.close()
    return path


def test_unqualified_proposal_reaches_gate_but_not_installer(tmp_path,monkeypatch):
    path=store_lead(tmp_path)
    proposed=P.proposals(path)
    assert len(proposed)==1
    assert proposed[0]['spec'].universe['include']==['A','B']
    assert proposed[0]['spec'].provenance['admission'].startswith('UNTESTED')
    monkeypatch.setattr(F,'ensure',lambda *a:None)
    monkeypatch.setattr(F,'versioned',lambda *a:False)
    installer=Mock()
    analyst=SimpleNamespace(qualify=Mock(return_value=(False,{'reason':'fixture missing evidence'},None)))
    kernel=SimpleNamespace(cfg={'research':{'closure':{'enabled':True,'paper_intake':True,'store_path':str(path)}}},
                           journal=SimpleNamespace(query=lambda *a: [],log_brain_event=Mock()),_qualified_install=installer)
    assert Kernel._compositional_paper_intake(kernel,analyst,[],max_book=8,limit=1)==[]
    assert analyst.qualify.call_args.kwargs=={'fixed_timeframe':True}
    installer.assert_not_called()


def test_test_only_results_cannot_enter_production_paper_gate(tmp_path):
    assert P.proposals(store_lead(tmp_path,'TEST_ONLY'))==[]


def test_existing_version_does_not_strand_newer_unregistered_lead(tmp_path,monkeypatch):
    path=store_lead(tmp_path)
    items=P.proposals(path)
    registered=items[0]['spec']
    from copy import deepcopy
    fresh=deepcopy(items[0]);fresh['spec'].id='fresh-proposal'
    monkeypatch.setattr(P,'proposals',lambda *a,**kw:[items[0],fresh])
    monkeypatch.setattr(F,'ensure',lambda *a:None)
    monkeypatch.setattr(F,'versioned',lambda j,sid:sid==registered.id)
    analyst=SimpleNamespace(qualify=Mock(return_value=(False,{},None)))
    kernel=SimpleNamespace(cfg={'research':{'closure':{'enabled':True,'paper_intake':True,'store_path':str(path)}}},
                           journal=SimpleNamespace(query=lambda *a: [],log_brain_event=Mock()),_qualified_install=Mock())
    Kernel._compositional_paper_intake(kernel,analyst,[],max_book=8,limit=1)
    assert analyst.qualify.call_args.args[0].id=='fresh-proposal'


def test_exact_qualifying_lead_installs_once_and_recovers_restart(tmp_path,monkeypatch):
    """Synthetic numerical PASS proves wiring only; no economic claim."""
    from trader.core.journal import Journal
    from tests.test_sol1_strategy_lifecycle import _qualification,_ev,SCORED
    from trader.kernel import Kernel
    from trader.core.config import load_config
    path=store_lead(tmp_path)
    proposal=P.proposals(path)[0]
    proposed=proposal['spec']
    proposed.universe={'include':list(SCORED),'exclude':[]}
    j=Journal(tmp_path/'paper.db')
    cfg=load_config();cfg['research']['closure'].update(enabled=True,paper_intake=True,store_path=str(path))
    k=Kernel.__new__(Kernel);k.cfg=cfg;k.journal=j;k.notifier=None
    q=_qualification(proposed,cfg)
    analyst=SimpleNamespace(qualify=Mock(return_value=(True,dict(_ev(),chosen_timeframe='4h'),q)),
                            set_measured_regimes=lambda _:[])
    monkeypatch.setattr(P,'proposals',lambda *a,**kw:[] if proposal['candidate_id'] in kw.get('exclude',()) else [proposal])
    # Simulate interruption after immutable validation, before paper installation.
    real_install=k._install_version
    k._install_version=lambda *a,**kw:False
    assert k._compositional_paper_intake(analyst,[],max_book=8,limit=1)==[]
    versions=j.query('SELECT version_id FROM strategy_versions');assert len(versions)==1
    assert F.state_of(j,versions[0]['version_id'])==F.VALIDATED
    assert not j.query('SELECT * FROM strategies')
    k._install_version=real_install
    book=[]
    assert k._compositional_paper_intake(analyst,book,max_book=8,limit=1)==[proposed.name]
    assert F.state_of(j,versions[0]['version_id'])==F.SHADOW
    assert j.query('SELECT state FROM strategies')[0]['state']=='paper'
    assert analyst.qualify.call_count==1  # restart reuses the exact authenticated qualification
    assert k._compositional_paper_intake(analyst,book,max_book=8,limit=1)==[]
    assert len(j.query('SELECT * FROM strategy_version_installs'))==1
    assert not j.query('SELECT * FROM trades')
