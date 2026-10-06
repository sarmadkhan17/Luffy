"""DATA-02: a journaled decision lineage must rebuild exact retained receipts."""
import json

import numpy as np
import pytest

from trader.core.journal import Journal
from trader.data import market_provenance as P
from trader.data.feed import DataFeed
from tests.test_stage1_data_provenance_pit import Clock, Venue, frame, T, Q


def seed(tmp_path, *, mutate=None, df=None):
    at = T + Q + 100
    feed = DataFeed(exchange=Venue([[T, 10, 11, 9, 10, 5]]), db_path=tmp_path / 'candles.db', clock_ms=Clock(at))
    iid, source = feed._identity('BTC/USDT')
    kept = P.annotate(frame() if df is None else df, instrument_id=iid, source=source, kind='candle',
                      received_ms=at, timeframe='15m')
    if mutate:
        kept = P.seal(mutate(kept))
    feed._store_save('BTC/USDT', '15m', kept, now_ms=at)
    feed.db.commit()
    lineage = dict(schema_version=P.SCHEMA, as_of_ms=at, instrument_id=iid,
                   frames={'15m': kept['revision_id'].tolist()}, derivs={}, references={}, universe={})
    return at, lineage


def reopened(tmp_path):
    return DataFeed(exchange=Venue([]), db_path=tmp_path / 'candles.db', clock_ms=Clock(0)).db


def test_restart_reconstructs_receipt_and_preserves_att04_admission(tmp_path):
    at, lineage = seed(tmp_path)
    lineage['attention_admission'] = dict(receipt_id='adm-1', policy_version='p1', source_cut_ms=at - 5)
    Journal(tmp_path / 'j.db').log_brain_event('market_provenance', 'market_data',
                                                dict(decision_id='d1', cycle_id='c1', receipt=lineage))
    row = Journal(tmp_path / 'j.db').query("SELECT detail FROM brain_events WHERE kind='market_provenance'")[0]
    stored = json.loads(row['detail'])['receipt']
    assert stored['attention_admission']['receipt_id'] == 'adm-1'
    got = P.resolve_lineage([reopened(tmp_path)], stored, transforms={'candle': None})
    meta = got[stored['frames']['15m'][0]]
    assert meta['event_time_ms'] == T and meta['available_at_ms'] == at and meta['quality'] == 'VALID'
    assert meta['source'] and meta['instrument_id'] == lineage['instrument_id'] and meta['kind'] == 'candle'


def test_missing_receipt_refused(tmp_path):
    at, lineage = seed(tmp_path)
    lineage['frames']['15m'].append('0' * 64)
    with pytest.raises(ValueError, match='lineage_receipt_missing'):
        P.resolve_lineage([reopened(tmp_path)], lineage)


def test_future_receipt_refused(tmp_path):
    at, lineage = seed(tmp_path)
    lineage['as_of_ms'] = at - 1
    with pytest.raises(ValueError, match='not_yet_available'):
        P.resolve_lineage([reopened(tmp_path)], lineage)


def test_nan_input_is_invalid_not_zero(tmp_path):
    df = frame()
    df['close'] = np.nan
    at, lineage = seed(tmp_path, df=df)
    with pytest.raises(ValueError, match='quality_not_valid'):
        P.resolve_lineage([reopened(tmp_path)], lineage)


@pytest.mark.parametrize('quality', ['STALE', 'SUSPECT', 'UNSUPPORTED', 'MISSING', 'INCOMPLETE', 'UNKNOWN'])
def test_non_valid_states_never_current(tmp_path, quality):
    at, lineage = seed(tmp_path, mutate=lambda d: d.assign(quality=quality))
    with pytest.raises(ValueError, match='quality_not_valid'):
        P.resolve_lineage([reopened(tmp_path)], lineage)


def test_transform_and_schema_mismatch_refused(tmp_path):
    at, lineage = seed(tmp_path)
    with pytest.raises(ValueError, match='transform_mismatch'):
        P.resolve_lineage([reopened(tmp_path)], lineage, transforms={'candle': 'candle.v2'})
    bad = dict(lineage, btc_context=dict(as_of_ms=at, transform_version='btc_context.v0', source_revisions=[]))
    with pytest.raises(ValueError, match='transform_mismatch'):
        P.resolve_lineage([reopened(tmp_path)], bad)
    with pytest.raises(ValueError, match='schema_incompatible'):
        P.resolve_lineage([reopened(tmp_path)], dict(lineage, schema_version='market.receipt.v0'))


def test_foreign_instrument_and_tamper_refused(tmp_path):
    at, lineage = seed(tmp_path)
    with pytest.raises(ValueError, match='instrument_mismatch'):
        P.resolve_lineage([reopened(tmp_path)], dict(lineage, instrument_id='binance_usdm:futures:ETHUSDT'))
    db = reopened(tmp_path)
    db.execute("UPDATE market_revisions SET record_json=replace(record_json,'\"quality\":\"VALID\"','\"quality\":\"STALE\"')")
    with pytest.raises(ValueError, match='content_corrupt'):
        P.resolve_lineage([db], lineage)


# ── end to end: journal -> reopen -> owner decision read -> durable receipts ──
def journaled(tmp_path, lineage, decision='d1', event=True):
    j = Journal(tmp_path / 'j.db')
    with j._tx() as c:
        c.execute("INSERT INTO cycles(id,ts,symbol,price) VALUES(?,?,?,?)",
                  ('c-' + decision, '2026-01-01T00:00:00+00:00', 'BTC/USDT', 10))
        c.execute("INSERT INTO decisions(id,cycle_id,ts,symbol,action,score,threshold,confidence,executed) "
                  "VALUES(?,?,'2026-01-01T00:00:00+00:00','BTC/USDT','HOLD',0,1,0,0)", (decision, 'c-' + decision))
    if event:
        j.log_brain_event('market_provenance', 'market_data',
                          dict(decision_id=decision, cycle_id='c-' + decision, receipt=lineage))
    return Journal(tmp_path / 'j.db')  # reopened: nothing held in memory


def read(tmp_path, lineage, **kw):
    from trader.dashboard.owner_reads import decision_detail
    return decision_detail(journaled(tmp_path, lineage, **kw), 'd1', [tmp_path / 'candles.db', tmp_path / 'derivs.db'])


def reason(d):
    assert 'market_inputs' not in d
    return next(u['reason'] for u in d['unavailable'] if u['field'] == 'market_inputs')


def test_owner_decision_read_reconstructs_exact_receipts(tmp_path):
    at, lineage = seed(tmp_path)
    lineage['attention_admission'] = dict(receipt_id='adm-1', policy_version='p1', source_cut_ms=at - 5)
    d = read(tmp_path, lineage)
    mi = d['market_inputs']
    assert mi['verified'] is True and mi['as_of_ms'] == at
    assert mi['attention_admission']['receipt_id'] == 'adm-1'
    (rid, m), = mi['receipts'].items()
    assert rid == lineage['frames']['15m'][0]
    assert (m['event_time_ms'], m['available_at_ms'], m['quality'], m['kind']) == (T, at, 'VALID', 'candle')
    assert m['source'] and m['instrument_id'] == lineage['instrument_id']
    assert not [u for u in d['unavailable'] if u['field'] == 'market_inputs']


@pytest.mark.parametrize('case,expect', [
    ('missing', 'lineage_receipt_missing'), ('future', 'lineage_receipt_not_yet_available'),
    ('stale', 'quality_not_valid'), ('unknown', 'quality_not_valid'),
    ('schema', 'schema_incompatible'), ('transform', 'transform_mismatch'), ('tamper', 'content_corrupt')])
def test_owner_read_negative_controls(tmp_path, case, expect):
    q = {'stale': 'STALE', 'unknown': 'UNKNOWN'}.get(case)
    at, lineage = seed(tmp_path, mutate=(lambda d: d.assign(quality=q)) if q else None)
    if case == 'missing':
        lineage['frames'] = {'15m': ['0' * 64]}
    if case == 'future':
        lineage['as_of_ms'] = at - 1
    if case == 'schema':
        lineage['schema_version'] = 'market.receipt.v0'
    if case == 'transform':
        lineage['btc_context'] = dict(as_of_ms=at, transform_version='btc_context.v0', source_revisions=[])
    if case == 'tamper':
        db = reopened(tmp_path)
        db.execute("UPDATE market_revisions SET record_json=replace(record_json,'\"quality\":\"VALID\"','\"quality\":\"STALE\"')")
        db.commit()
    assert expect in reason(read(tmp_path, lineage))


def test_owner_read_absent_event_and_legacy_callers(tmp_path):
    from trader.dashboard.owner_reads import decision_detail
    at, lineage = seed(tmp_path)
    assert 'no market_provenance event' in reason(read(tmp_path, lineage, event=False))
    assert 'market_inputs' not in decision_detail(Journal(tmp_path / 'j.db'), 'd1')  # no stores -> unchanged


def test_owner_read_missing_stores_never_verified(tmp_path):
    from trader.dashboard.owner_reads import decision_detail
    at, lineage = seed(tmp_path)
    j = journaled(tmp_path, lineage)
    assert 'market_stores_unavailable' in reason(decision_detail(j, 'd1', [tmp_path / 'nope.db']))
