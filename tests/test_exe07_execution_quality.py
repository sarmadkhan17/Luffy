"""EXE-07: execution-quality timing and actual venue costs bind the exact action.

Offline only: hand-built journal rows, no venue/provider call.
"""
import hashlib
import json
import sqlite3

import pytest

from trader.engine import execution_evidence as E
from trader.engine import trade_provenance as TP
from trader.engine.entry_authority import SCHEMA as REQUESTS
from tests.test_entry_recovery import setup, Venue  # noqa: F401

LOGICAL = 'logical-1'
CID = 'lr_' + hashlib.sha256(LOGICAL.encode()).hexdigest()[:28]
REF = {'price': 100.0, 'basis': 'decision_snapshot_price', 'snapshot_at': '2026-10-06T00:00:00+00:00',
       'bar_ts': '2026-10-05T20:00:00', 'submitted_ms': 1_000}


@pytest.fixture
def db():
    c = sqlite3.connect(':memory:')
    c.row_factory = sqlite3.Row
    c.executescript(TP.TABLES + REQUESTS + 'CREATE TABLE trades(id TEXT, decision_id TEXT, '
                    'strategy_id TEXT, entry_identity_json TEXT);')
    return c


def q(c):
    return lambda sql, p=(): [dict(r) for r in c.execute(sql, p).fetchall()]


def trade(c, tid='t1', decision='d1', logical=LOGICAL, decided='2026-10-06T00:00:00+00:00'):
    ident = {'status': 'VERIFIED', 'strategy_id': 's', 'decision': {'decision_id': decision, 'decided_at': decided}}
    if logical:
        ident['execution_binding'] = {'logical_id': logical}
    c.execute('INSERT INTO trades VALUES (?,?,?,?)', (tid, decision, 's', json.dumps(ident)))


def leg(c, tid='t1', oid='o1', cid=CID, purpose='entry', side='buy', req=2.0, booked=2.0,
        ref=REF, fee_basis=None):
    cur = c.execute(
        'INSERT INTO trade_legs(trade_id,booking_id,kind,purpose,origin,symbol,side,venue_order_id,'
        'client_order_id,order_identity,requested_qty,booked_qty,reference_json,exit_attribution,fee_basis,'
        "source,recorded_ms,market_type) VALUES (?,1,'entry',?,'luffy_order','BTC/USDT:USDT',?,?,?,'VERIFIED',"
        "?,?,?,'NOT_APPLICABLE',?,'live_booking',5,'futures')",
        (tid, purpose, side, oid, cid, req, booked, json.dumps(ref) if ref is not None else None, fee_basis))
    return cur.lastrowid


def fill(c, lid, fid, qty=1.0, price=100.1, fee='0.04', asset='USDT', ts=1_500, oid='o1', tid='t1'):
    c.execute('INSERT INTO trade_fills(symbol,fill_key,venue_fill_id,venue_order_id,trade_id,leg_id,leg,side,qty,'
              'price,commission,commission_asset,venue_ts_ms,observed_ms,attribution,attribution_reason,source,'
              "fill_json,market_type) VALUES ('BTC/USDT:USDT',?,?,?,?,?,'entry','buy',?,?,?,?,?,9,'ATTRIBUTED',"
              "'x','t','{}','futures')", (f'futures|id:{fid}', fid, oid, tid, lid, qty, price, fee, asset, ts))


def one(c, **kw):
    return E.executed_orders(q(c), **kw)[0]


def reserve(c, logical=LOGICAL, decision='d1', cid=CID, state='REFUSED', result=None):
    c.execute('INSERT INTO execution_requests VALUES (?,?,?,?,?,?,NULL,NULL,?)',
              (logical, decision, 'h', '{}', cid, state, json.dumps(result) if result else None))


def test_exact_action_lineage_and_complete_timing(db):
    trade(db); lid = leg(db); fill(db, lid, 'f1'); fill(db, lid, 'f2', ts=1_700); reserve(db, state='TERMINAL')
    o = one(db)
    assert o['action'] == dict(o['action'], status='VERIFIED', logical_id=LOGICAL, decision_id='d1',
                               client_order_id=CID, venue_order_id='o1', reservation='RECORDED')
    t = o['timing']
    assert t['signal_bar_ts'] == REF['bar_ts'] and t['decision_at'] == '2026-10-06T00:00:00+00:00'
    assert t['submitted_ms'] == 1_000 and t['first_fill_ms'] == 1_500 and t['last_fill_ms'] == 1_700
    assert t['unavailable'] == ['acknowledged_ms']          # never invented
    assert o['reference']['observed_at'] == REF['snapshot_at'] and o['reference']['observed_at_source'] == 'snapshot_at'
    assert o['commission_status'] == 'VERIFIED' and o['commissions_by_asset'] == {'USDT': '0.08'}
    assert o['market_context']['spread']['status'] == 'UNAVAILABLE' == o['market_context']['depth']['status']
    assert o['slippage']['status'] == 'MEASURED'


def test_missing_fee_is_never_zero_or_verified(db):
    trade(db); lid = leg(db); fill(db, lid, 'f1', fee=None, asset=None); fill(db, lid, 'f2')
    o = one(db)
    assert o['commission_status'] == 'PARTIAL' and o['commissions_by_asset'] == {'USDT': '0.04'}
    db.execute('UPDATE trade_fills SET commission=NULL, commission_asset=NULL')
    o = one(db)
    assert o['commission_status'] == 'UNAVAILABLE' and o['commissions_by_asset'] == {}


def test_estimated_fee_without_venue_figure_is_estimated_not_verified(db):
    trade(db); lid = leg(db, fee_basis='estimated_order_booking'); fill(db, lid, 'f1', fee=None, asset=None)
    fill(db, lid, 'f2', fee='', asset='')
    assert one(db)['commission_status'] == 'ESTIMATED'
    db.execute('DELETE FROM trade_fills')                    # no fills at all
    assert one(db)['commission_status'] == 'ESTIMATED'
    db.execute('UPDATE trade_legs SET fee_basis=NULL')
    assert one(db)['commission_status'] == 'UNAVAILABLE'


def test_actual_fee_beats_estimate(db):
    trade(db); lid = leg(db, fee_basis='estimated_order_booking'); fill(db, lid, 'f1'); fill(db, lid, 'f2')
    assert one(db)['commission_status'] == 'VERIFIED'


def test_partial_fill_cannot_be_verified_money(db):
    trade(db); lid = leg(db, req=2.0, booked=2.0); fill(db, lid, 'f1', qty=1.0)
    o = one(db)
    assert o['fill_coverage'] == 'PARTIAL' and o['commission_status'] == 'PARTIAL'
    assert o['slippage']['status'] == 'MEASURED_ON_PARTIAL_FILLS'
    assert o['fill_qty'] == 1.0 and o['requested_qty'] == 2.0


def test_multiple_fills_vwap_and_commission_by_asset(db):
    trade(db); lid = leg(db); fill(db, lid, 'f1', price=100.0, fee='0.01', asset='USDT')
    fill(db, lid, 'f2', price=102.0, fee='0.00001', asset='BNB')
    o = one(db)
    assert o['vwap'] == pytest.approx(101.0) and o['commissions_by_asset'] == {'BNB': '0.00001', 'USDT': '0.01'}


def test_rejection_is_recorded_with_identity_and_has_no_fill(db):
    reserve(db, state='REFUSED', result={'reason': 'InvalidOrder', 'submitted': True, 'submitted_ms': 1_000})
    reserve(db, logical='l2', decision='d2', cid='lr_other', state='REFUSED')       # reason never recorded
    reserve(db, logical='l3', decision='d3', cid='lr_ok', state='TERMINAL')
    r = E.rejections(q(db))
    assert [x['logical_id'] for x in r] == [LOGICAL, 'l2']
    assert r[0]['reason'] == 'InvalidOrder' and r[0]['client_order_id'] == CID and r[0]['fills'] == 0
    assert r[1]['reason'] == 'UNAVAILABLE' and r[1]['submitted'] is None
    assert E.executed_orders(q(db)) == []                      # a rejection is not an executed order


@pytest.mark.parametrize('mutate', ['client', 'decision', 'request_logical', 'request_decision'])
def test_wrong_action_or_order_binding_is_mismatch(db, mutate):
    trade(db, decision='d1')
    lid = leg(db, cid='lr_forged' if mutate == 'client' else CID); fill(db, lid, 'f1')
    if mutate == 'decision':
        db.execute("UPDATE trades SET decision_id='dX'")
    if mutate == 'request_logical':
        reserve(db, logical=LOGICAL, cid=CID, state='TERMINAL'); db.execute("UPDATE execution_requests SET decision_id='dZ'")
        db.execute("UPDATE trades SET decision_id='d1'")
    if mutate == 'request_decision':
        reserve(db, decision='dY', state='TERMINAL')
    a = one(db)['action']
    assert a['status'] == 'MISMATCH' and a['status'] != 'VERIFIED'


def test_unbound_entry_and_exit_never_claim_logical_identity(db):
    trade(db, logical=None); leg(db)
    assert one(db)['action']['status'] == 'UNKNOWN' and one(db)['action']['logical_id'] is None
    db.execute("UPDATE trade_legs SET client_order_id=NULL")
    trade(db, tid='t2', logical=LOGICAL); leg(db, tid='t2', oid='o2', cid=None)
    assert [o['action']['status'] for o in E.executed_orders(q(db))] == ['UNKNOWN', 'UNKNOWN']
    db.execute("UPDATE trade_legs SET purpose='final_exit', client_order_id='lx_1' WHERE trade_id='t2'")
    a = E.executed_orders(q(db), trade_id='t2')[0]['action']
    assert a['status'] == 'TRADE_BOUND_ONLY' and a['logical_id'] is None and a['trade_id'] == 't2'


def test_missing_timing_fields_are_listed_not_filled(db):
    trade(db); lid = leg(db, ref={'price': None, 'basis': 'unavailable', 'submitted_ms': 1_000})
    fill(db, lid, 'f1', ts=None)
    o = one(db)
    t = o['timing']
    assert t['submitted_ms'] == 1_000 and t['first_fill_ms'] is None and t['signal_bar_ts'] is None
    assert {'acknowledged_ms', 'first_fill_ms', 'signal_bar_ts', 'reference_observed_at'} <= set(t['unavailable'])
    assert o['slippage'] == {'status': 'UNAVAILABLE', 'reason': 'NO_REFERENCE_PRICE'}
    db.execute('UPDATE trade_legs SET reference_json=NULL')
    assert one(db)['timing']['submitted_ms'] is None


def test_restart_replay_is_idempotent_and_measurement_id_stable(db, tmp_path):
    path = tmp_path / 'j.db'
    disk = sqlite3.connect(path); disk.row_factory = sqlite3.Row
    disk.executescript(TP.TABLES + REQUESTS + 'CREATE TABLE trades(id TEXT, decision_id TEXT, '
                       'strategy_id TEXT, entry_identity_json TEXT);')
    trade(disk); lid = leg(disk); fill(disk, lid, 'f1'); fill(disk, lid, 'f2'); disk.commit()
    first = E.executed_orders(q(disk))
    disk.close()
    again = sqlite3.connect(path); again.row_factory = sqlite3.Row
    assert E.executed_orders(q(again)) == first and E.executed_orders(q(again)) == first
    with pytest.raises(sqlite3.IntegrityError):                   # a replayed venue fill cannot double-count
        fill(again, lid, 'f1')


def test_real_venue_rejection_binds_reason_and_exact_ids(setup):
    from ccxt import InsufficientFunds
    from tests.entry_authority_fixtures import permission
    ex, j, e, d = setup
    kw = permission(e, d)
    ex.entry_error = InsufficientFunds('rejected')
    assert e.open(d, 2, 2, 95, 110, 'strategy', 'strategy', **kw) is None
    (r,) = E.rejections(j.query)
    row = j.query('SELECT logical_id,decision_id,client_order_id FROM execution_requests')[0]
    assert (r['logical_id'], r['decision_id'], r['client_order_id']) == (
        row['logical_id'], row['decision_id'], row['client_order_id'])
    assert r['reason'] == 'InsufficientFunds' and r['submitted'] is True and r['submitted_ms'] > 0
    assert 'rejected' not in json.dumps(r)                       # venue text is never stored
    assert E.executed_orders(j.query) == []
