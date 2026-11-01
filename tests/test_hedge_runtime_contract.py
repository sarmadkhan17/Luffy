"""Research capability reporting and the coordinated caller share one contract."""
from copy import deepcopy

import pytest

from trader.engine import coordinated_authority as C
from trader.research.compose import hedge as H
from tests.test_coordinated_authority import version


@pytest.mark.parametrize('change,want', [
    ('none', None),
    ('same_symbol', 'coordinated_account_or_contract_unsupported'),
    ('hedge_mode', None),
    ('inverse', 'coordinated_account_or_contract_unsupported'),
    ('stop', 'coordinated_strategy_stop_unavailable'),
])
def test_research_and_actual_caller_agree_on_the_exact_supported_contract(change, want):
    v = deepcopy(version())
    hs = v['lineage']['research']['multi_leg']['hedge_spec']
    if change == 'same_symbol':
        hs['hedge']['symbol'] = hs['primary']['symbol']
    elif change == 'hedge_mode':
        hs['account_mode'] = 'hedge'
    elif change == 'inverse':
        hs['hedge']['contract'] = 'inverse'
    elif change == 'stop':
        hs['management'].pop('hedge_stop')
    assert C.support_block(v) == H.execution_support_block(hs) == want
    report = H.deployment_contract(hs)
    assert (report['status'] == 'DEPLOYABLE') == (want is None)
    assert report['execution_authority'] == 'NONE' and report['readiness'] == 'NOT_ASSESSED'
    if want:
        assert want in report['reasons']


def test_external_record_cannot_invent_a_caller_for_an_unimplemented_mode(monkeypatch):
    monkeypatch.setitem(H.RUNTIME_SUPPORT, 'hedge_mode', False)
    hs = version()['lineage']['research']['multi_leg']['hedge_spec']
    hs['account_mode'] = 'hedge'
    report = H.deployment_contract(hs, verified=dict(multi_leg_orders=True,
        account_modes_in_executor=['hedge(positionSide=LONG/SHORT)']))
    assert report['status'] == 'REFUSED_UNSUPPORTED_VENUE_MODE'
    assert 'coordinated_account_or_contract_unsupported' in report['reasons']


def test_installed_record_can_narrow_the_available_caller():
    hs = version()['lineage']['research']['multi_leg']['hedge_spec']
    report = H.deployment_contract(hs, verified={'multi_leg_orders': False})
    assert report['status'] == 'REFUSED_UNSUPPORTED_VENUE_MODE'
    assert report['execution_authority'] == 'NONE'


def test_native_same_instrument_code_support_is_not_current_venue_or_entry_authority():
    hs = version()['lineage']['research']['multi_leg']['hedge_spec']
    hs['account_mode'] = 'hedge'
    hs['hedge']['symbol'] = hs['primary']['symbol']
    assert H.execution_support_block(hs) is None
    report = H.deployment_contract(hs)
    assert report['status'] == 'DEPLOYABLE'
    assert report['execution_authority'] == 'NONE' and report['readiness'] == 'NOT_ASSESSED'
    report = H.deployment_contract(hs, verified={'hedge_mode':False})
    assert report['status'] == 'REFUSED_UNSUPPORTED_VENUE_MODE'
