"""Economics boundary tests: no venue or Kernel execution."""
from copy import deepcopy

from trader.dashboard.economics import read
from tests.test_owner_reads import live, H, _only_receipt, _fills_evidence


def trade():
    return dict(id="t", realized_pnl=999, mfe_r=0, mae_r=None,
                excursion=dict(status="measured", coverage=.8, bars=8,
                               expected_bars=10, exit_r=1.5,
                               partial_edge_bars_excluded=True))


def provenance():
    return dict(schema_version="trade-provenance-read.v2", missing=["funding"],
                legs=[dict(leg_id=1, fill_coverage="PARTIAL",
                           exit_attribution=dict(status="AMBIGUOUS"), fills=[
                    dict(venue_fill_id="f", venue_order_id="o", market_type="futures",
                         attribution="ATTRIBUTED", realized_pnl="10", commission="1",
                         commission_asset="BNB", source="live_booking", observed_ms=123)])],
                slippage_basis=dict(price=100, status="RECORDED"))


def test_estimates_cashflow_and_derived_coverage_stay_separate():
    r = read(trade(), provenance())
    fields = {x['field']: x for x in r['venue_monetary']}
    assert fields['realized_pnl']['value'] == '10'
    assert fields['commission']['coverage']['currency'] == 'BNB'
    assert fields['realized_pnl']['coverage']['fill_coverage'] == 'PARTIAL'
    assert fields['funding']['value'] is fields['net_economic_pnl']['value'] is None
    assert not r['whole_trade_complete']
    assert r['journal_booked'][0]['value'] == 999
    assert r['journal_booked'][0]['status'] == 'JOURNAL_BOOKED'
    derived = {x['field']: x for x in r['derived']}
    assert derived['MFE']['value'] == 0  # measured zero is not missing
    assert derived['MAE']['status'] == 'UNAVAILABLE'
    assert derived['R']['value'] == 1.5
    assert derived['MFE']['coverage']['coverage'] == .8
    assert derived['MFE']['calculation_version'] is None
    assert derived['slippage']['value'] is None
    assert derived['attribution']['value'][0]['status'] == 'AMBIGUOUS'
    assert all(x['source'] and x['version'] and isinstance(x['coverage'], dict)
               for group in ('venue_monetary', 'journal_booked', 'derived') for x in r[group])


def test_missing_fills_and_excursion_cannot_borrow_journal_numbers():
    t = trade(); t.update(mfe_r=None, excursion=None)
    r = read(t, {})
    assert all(x['value'] is None for x in r['venue_monetary'])
    assert all(x['value'] is None for x in r['derived'])


def test_snapshot_versions_change_independently_and_reads_do_not_mutate():
    t, p = trade(), provenance(); before = deepcopy((t, p))
    a = read(t, p)
    assert (t, p) == before
    p['legs'][0]['fills'][0]['commission'] = '2'
    b = read(t, p)
    assert a['venue_monetary'][1]['version'] != b['venue_monetary'][1]['version']
    assert a['derived'][0]['version'] == b['derived'][0]['version']
    t['mfe_r'] = 2
    assert read(t, p)['derived'][1]['version'] != b['derived'][1]['version']


def test_api_replayed_leg_does_not_promote_whole_trade_accounting(live):
    c, j, *_ = live
    _only_receipt(j, evidence=_fills_evidence())
    d = c.get('/owner-api/v1/trades/t-btc/lineage', headers=H).json()
    assert d['accounting']['fills_verified']
    assert d['economics']['whole_trade_complete'] is False
    monetary = {x['field']: x for x in d['economics']['venue_monetary']}
    assert monetary['funding']['value'] is None
    assert monetary['net_economic_pnl']['status'] == 'UNAVAILABLE'


def test_nonfinite_derived_value_is_unknown():
    t = trade(); t['mfe_r'] = float('nan')
    assert read(t, {})['derived'][1]['value'] is None


def test_nonfinite_monetary_evidence_is_not_a_venue_number():
    t, p = trade(), provenance()
    t['realized_pnl'] = float('inf')
    p['legs'][0]['fills'][0]['commission'] = 'NaN'
    r = read(t, p)
    assert r['journal_booked'][0]['status'] == 'UNKNOWN'
    assert r['venue_monetary'][1]['value'] is None


def test_unknown_journal_booking_keeps_recorded_venue_fields_distinct():
    t = trade(); t.update(realized_pnl=0, pnl_status="UNKNOWN", pnl_value_class="UNKNOWN")
    r = read(t, provenance())
    assert r['journal_booked'][0]['value'] is None
    assert r['journal_booked'][0]['status'] == 'UNKNOWN'
    assert r['venue_monetary'][0]['value'] == '10'
    assert r['venue_monetary'][0]['status'] == 'RECORDED_VENUE_FIELD'
    t.update(realized_pnl=5, pnl_status="ESTIMATE", pnl_value_class="DERIVED_ESTIMATE")
    r = read(t, provenance())
    assert r['journal_booked'][0]['status'] == 'DERIVED_ESTIMATE'
    assert r['venue_monetary'][0]['value'] == '10'
    assert not r['whole_trade_complete']
