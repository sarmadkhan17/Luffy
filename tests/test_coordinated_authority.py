"""The coordinated scope removes no qualification, owner or Risk authority."""
from copy import deepcopy
from types import SimpleNamespace as NS
from unittest.mock import Mock
import pytest
from trader.engine import coordinated_authority as C
from trader.strategy import factory_handoff as F
from trader.research.compose import hedge as H
from tests.test_strategy_hedge_stops import current


def version():
    return dict(version_id='v',spec_hash='s',strategy_id='strategy',source=dict(kind=F.HEDGE_HQ_KIND),
        lineage=dict(research=dict(multi_leg=dict(hedge_spec=dict(account_mode='netting',purpose='protective_beta',
            primary=dict(symbol='A/USDT',contract='linear'),hedge=dict(symbol='B/USDT',contract='linear'),
            management=current())))))


def identity(leg='primary'):
    return dict(status='VERIFIED',version_id='v',strategy_id='strategy',spec_hash='s',spec_sha256='s',
        install_id='i',exit_semantics_id=H.BOOK_EXIT_SEMANTICS_ID,coordinated_action_id='a',
        coordinated=dict(group_id='g',leg=leg))


def scoped(tmp_path,monkeypatch):
    j=NS(db_path=str(tmp_path/'j.db'));v=version()
    load=Mock(return_value=v);validation=Mock();install=Mock(return_value={'install_id':'i'})
    live=Mock(return_value=None)
    monkeypatch.setattr(F,'load_version',load)
    monkeypatch.setattr(F,'verify_validation',validation)
    monkeypatch.setattr(F,'verify_install',install)
    monkeypatch.setattr(F,'live_entry_block',live)
    return j,v,validation,install,live


def test_integrated_runtime_still_requires_the_exact_coordinated_caller():
    assert C.RUNTIME_READY
    assert C.support_block(version()) is None
    assert F.multi_leg_block(version())=='multi_leg_requires_coordinated_execution'


def test_integrated_caller_checks_real_validation_before_install_or_entry(tmp_path,monkeypatch):
    from trader.core.journal import Journal
    j=Journal(tmp_path/'unqualified.db');F.ensure(j)
    v=version()
    monkeypatch.setattr(F,'load_version',lambda *a:v)
    # Keep the real receipt verifier: a supported contract is not admission.
    with pytest.raises(F.HandoffRefused,match='validation_receipt_missing'):
        with C.execution_scope(j,{},'v',group_id='g',leg='primary',action_id='a'):
            pytest.fail('missing validation cannot reach the caller')
    assert not C.matches(v)
    assert not j.query('SELECT * FROM trades')


def test_ordinary_derived_version_with_no_research_has_no_multileg_block():
    assert C.support_block(dict(lineage=dict(research=None))) is None


def test_exact_scope_reauthenticates_and_resets_after_exception(tmp_path,monkeypatch):
    j,v,validation,install,live=scoped(tmp_path,monkeypatch)
    assert not C.matches(v)
    with pytest.raises(RuntimeError,match='fixture'):
        with C.execution_scope(j,{},'v',group_id='g',leg='primary',action_id='a'):
            assert C.matches(v) and F.multi_leg_block(v) is None
            C.validate_identity(j,'strategy',identity(),'A/USDT')
            validation.assert_called_once_with(j,v)
            install.assert_called_once_with(j,v)
            live.assert_called_once_with(j,'strategy',cfg={})
            raise RuntimeError('fixture')
    assert not C.matches(v)
    with pytest.raises(ValueError,match='scope_mismatch'):
        C.validate_identity(j,'strategy',identity(),'A/USDT')


@pytest.mark.parametrize('change',['symbol','leg','group','action','version','install','hash','journal'])
def test_scope_is_exact_to_leg_action_version_and_journal(tmp_path,monkeypatch,change):
    j,v,*_=scoped(tmp_path,monkeypatch)
    value=identity();symbol='A/USDT';other=j
    if change=='symbol':symbol='B/USDT'
    elif change=='leg':value['coordinated']['leg']='hedge'
    elif change=='group':value['coordinated']['group_id']='other'
    elif change=='action':value['coordinated_action_id']='other'
    elif change=='version':value['version_id']='other'
    elif change=='install':value['install_id']='other'
    elif change=='hash':value['spec_hash']='other'
    elif change=='journal':other=NS(db_path=str(tmp_path/'different.db'))
    with C.execution_scope(j,{},'v',group_id='g',leg='primary',action_id='a'):
        with pytest.raises(ValueError,match='scope_mismatch'):
            C.validate_identity(other,'strategy',value,symbol)


def test_scope_cannot_open_owner_or_qualification_gate(tmp_path,monkeypatch):
    j,v,validation,install,live=scoped(tmp_path,monkeypatch)
    live.return_value='owner_approval_missing'
    with pytest.raises(ValueError,match='owner_approval_missing'):
        with C.execution_scope(j,{},'v',group_id='g',leg='hedge',action_id='a'):
            pytest.fail('owner gate must be rechecked')
    assert not C.matches(v)
    validation.side_effect=ValueError('calibration_not_approved')
    with pytest.raises(ValueError,match='calibration_not_approved'):
        with C.execution_scope(j,{},'v',group_id='g',leg='hedge',action_id='a'):
            pytest.fail('qualification must be rechecked')
    assert not C.matches(v)


def test_development_only_and_unsupported_modes_never_gain_scope(tmp_path,monkeypatch):
    j,v,*_=scoped(tmp_path,monkeypatch)
    v['source']['kind']='development-only'
    assert C.support_block(v)=='multi_leg_requires_coordinated_execution'
    v['source']['kind']=F.HEDGE_HQ_KIND
    for key in ('account_mode','inverse','same_symbol','missing_stop'):
        bad=deepcopy(v);h=bad['lineage']['research']['multi_leg']['hedge_spec']
        if key=='account_mode':h['account_mode']='hedge'
        elif key=='inverse':h['hedge']['contract']='inverse'
        elif key=='same_symbol':h['hedge']['symbol']=h['primary']['symbol']
        else:h['management'].pop('hedge_stop')
        assert C.support_block(bad) is not None
