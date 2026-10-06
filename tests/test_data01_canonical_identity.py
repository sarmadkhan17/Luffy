"""DATA-01: a digest-valid capability whose revision or market/settlement/contract
facts are not the exact supported identity must never authorize exposure."""
import copy
import json
import time
import pytest
from tests.test_entry_recovery import setup
from tests.entry_authority_fixtures import permission
from trader.engine import entry_authority as A


def _rewrite(j, symbol, mutate):
    raw = json.loads(j.kv_get(A.CAP_KEY + symbol))
    mutate(raw)
    # keep the registry snapshot self-consistent so only the named fact differs
    raw['snapshot_id'] = A.digest(raw['registry'])
    raw['receipt_id'] = A.digest({k: v for k, v in raw.items() if k != 'receipt_id'})
    j.kv_set(A.CAP_KEY + symbol, A.canonical(raw))


def _both(raw, fn):
    fn(raw['record'])
    fn(raw['registry']['records'][0])


def _iid(raw, **kw):
    def f(rec):
        rec['instrument_id'].update(kw)
        rec['eligibility_basis']['instrument_id'].update(kw)
    _both(raw, f)
    i = raw['record']['instrument_id']
    raw['instrument_id'] = ':'.join((i['venue'], i['market_type'], i['venue_symbol']))


CASES = {
    'capability_revision_incompatible': [
        lambda r: r.update(schema='entry-capability.v2'),
        lambda r: r['registry'].update(schema_version=2),
        lambda r: _both(r, lambda rec: rec.update(schema_version=2)),
    ],
    'entry_exposure_model_unsupported': [
        lambda r: _iid(r, market_type='spot'),
        lambda r: _both(r, lambda rec: rec.update(settlement_asset='BUSD')),
        lambda r: _iid(r, venue='other_venue'),
    ],
    'contract_multiplier_unsupported': [
        lambda r: _both(r, lambda rec: rec.update(contract_multiplier='10')),
    ],
    'instrument_capability_unverified': [
        lambda r: _both(r, lambda rec: rec.update(contract_type=None)),
    ],
    'canonical_identity_mismatch': [
        lambda r: r.update(instrument_id='binance_usdm:futures:ETHUSDT'),
    ],
}


@pytest.mark.parametrize('reason,i', [(k, i) for k, v in CASES.items() for i in range(len(v))])
def test_incompatible_identity_fails_closed(setup, reason, i):
    ex, j, e, d = setup
    _rewrite(j, d.symbol, CASES[reason][i])
    with pytest.raises(ValueError, match=reason):
        A.capability(j, d.symbol, int(time.time() * 1000), e.leverage, 'LONG')


def test_unmutated_capability_still_valid(setup):
    ex, j, e, d = setup
    cap = A.capability(j, d.symbol, int(time.time() * 1000), e.leverage, 'LONG')
    assert cap['instrument_id'] == 'binance_usdm:futures:BTCUSDT'
