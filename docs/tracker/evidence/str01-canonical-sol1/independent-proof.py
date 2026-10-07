from copy import deepcopy
import json
import shutil
from pathlib import Path
import pytest
from tests.test_res08_governed_bridge import base, completed, copy_db, cycle, rows
from tests.test_predictive_research_bridge import NumericalFixture, CFG
from trader.core.journal import Journal
from trader.research import predictive_bridge as B, predictive_receipt as M
from trader.cognition.predictive_bank import predictive_extensions
from trader.strategy import factory_handoff as F

OUT=Path('/mnt/luffy-data/str01-closure-adjudication/final-independent-1508509.json')

def test_persisted_adversarial_and_revision(completed,tmp_path):
    case, _, h, ex, old=completed
    bank=case[1]
    M.validate(h,ex,old)
    old_src=B.factory_source(old,bank)
    old_j=Journal(old['referee_disposition']['factory_candidate']['quantitative_ledger'])
    old_version=F.create_version(old_j,CFG,old_src,at_ms=case[4])
    old_record=F.load_version(old_j,old_version['version_id'])
    assert F.state_of(old_j,old_version['version_id'])==F.VALIDATED
    revised=tmp_path/'revised'; revised.mkdir()
    copy_db(case[0],revised/'investigation.db')
    copy_db(case[0].parent/'luffy.db',revised/'luffy.db')
    later=case[4]+14_400_000
    second=(revised/'investigation.db',revised/'bridge.db',case[2],case[3],later,case[5])
    fake=NumericalFixture()
    cycle(second,fake,case[3])
    for _ in range(6): cycle(second,fake,later)
    current=next(r for r in rows(second[1],'bridge_bank_results') if r['classification']=='SUPPORTED')
    current_ex=next(e for e in rows(second[1],'bridge_experiments') if e['experiment_id']==current['experiment_id'])
    M.validate(h,current_ex,current)
    assert current_ex['split']['end_ms']>ex['split']['end_ms']
    assert current['hypothesis_id']==old['hypothesis_id']
    assert current['experiment_id']!=old['experiment_id']
    # Append genuine later experiment/result records to the SAME immutable Bank.
    for exp in rows(second[1],'bridge_experiments'):
        source_dir=second[1].parent/(second[1].stem+'-experiments')/exp['experiment_id']
        dest_dir=bank.parent/(bank.stem+'-experiments')/exp['experiment_id']
        if source_dir.exists(): shutil.copytree(source_dir,dest_dir)
    with B.store(bank) as db:
        for table,key in [('bridge_experiments','experiment_id'),('bridge_questions','question_id'),('bridge_bank_results','result_id')]:
            for record in rows(second[1],table): B.append(db,table,record[key],record)
        db.commit()
    att=predictive_extensions(bank,h.bank_id)
    assert old in att['results'] and current in att['results']
    src=B.factory_source(current,bank)
    ledger=current['referee_disposition']['factory_candidate']['quantitative_ledger']
    attacks={}
    forged=deepcopy(src)
    for i,k in enumerate(('measurement_id','quantitative_evidence_id','result_id'),1): forged['research'][k]=str(i)*64
    attacks['forged_well_formed_ids']=(forged,'research_bank_result_not_found')
    crossed=deepcopy(src); crossed['research']['result_id']=old['result_id']
    attacks['cross_result_id']=(crossed,'research_bank_lineage_mismatch')
    mismatch=deepcopy(src); mismatch['research']['measurement_id']=old['measurement_id']
    attacks['measurement_result_mismatch']=(mismatch,'research_bank_lineage_mismatch')
    stale=deepcopy(src)
    for k in ('measurement_id','quantitative_evidence_id','result_id'): stale['research'][k]=old[k]
    attacks['stale_ids_against_current_experiment']=(stale,'research_bank_lineage_mismatch')
    verdicts={}
    for name,(attack,code) in attacks.items():
        jp=tmp_path/(name+'.db'); copy_db(ledger,jp); j=Journal(jp)
        F.gate_evidence(j,src['hash'])
        with pytest.raises(F.HandoffRefused) as error: F.create_version(j,CFG,attack,at_ms=later)
        assert error.value.code==code
        assert not j.query('SELECT * FROM strategy_versions')
        verdicts[name]=dict(verdict='REFUSED',reason=code)
    with pytest.raises(F.HandoffRefused) as error: F.create_version(old_j,CFG,old_src,at_ms=later)
    assert error.value.code=='research_result_stale'
    verdicts['genuine_old_result_after_new_revision']=dict(verdict='REFUSED',reason=error.value.code)
    current_j=Journal(ledger)
    new_version=F.create_version(current_j,CFG,src,at_ms=later)
    new_record=F.load_version(current_j,new_version['version_id'])
    assert F.state_of(current_j,new_version['version_id'])==F.VALIDATED
    assert new_version['version_id']!=old_version['version_id']
    assert F.load_version(old_j,old_version['version_id'])==old_record
    again=F.create_version(Journal(ledger),CFG,src,at_ms=later+1)
    assert again['version_id']==new_version['version_id']
    assert len(current_j.query('SELECT * FROM strategy_versions'))==1
    # Bank unavailable after creation: immutable load and validation still work;
    # new creation/replay through the factory requires authentication again.
    moved=bank.with_suffix('.unavailable'); bank.rename(moved)
    try:
        assert F.load_version(current_j,new_version['version_id'])==new_record
        F.verify_validation(current_j,new_record)
        assert F.load_version(old_j,old_version['version_id'])==old_record
        with pytest.raises(F.HandoffRefused) as error: F.create_version(current_j,CFG,src,at_ms=later+2)
        assert error.value.code=='research_bank_unauthenticated'
    finally: moved.rename(bank)
    for j in (old_j,current_j):
        for table in ('strategies','strategy_version_installs','strategy_approval_decisions','strategy_governor_events','orders'):
            if j.query("SELECT name FROM sqlite_master WHERE name=?",(table,)): assert not j.query('SELECT * FROM '+table)
    OUT.write_text(json.dumps(dict(commit='1508509',synthetic_protocol_fixture=True,attacks=verdicts,old_version=old_version['version_id'],new_version=new_version['version_id'],old_experiment=old['experiment_id'],new_experiment=current['experiment_id'],old_end=ex['split']['end_ms'],new_end=current_ex['split']['end_ms'],exact_current_validated=True,restart_identity=True,material_bank_revision_changes_identity=True,old_version_immutable=True,load_and_verify_do_not_reopen_bank=True,no_install_or_order_authority=True),indent=2)+'\n')
