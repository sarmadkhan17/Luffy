"""Bounded current-cut selection must preserve the exhaustive revision oracle."""
import json
import random
import sqlite3

import pandas as pd
import pytest

from trader.data import market_provenance as P

TF = 14_400_000
BASE = 1_700_006_400_000 // TF * TF
KEY = 'candle:BTC/USDT:4h'
IID = 'binance_usdm:futures:BTCUSDT'
SOURCE = 'https://offline.example/' + 'retained-source/' * 30
CUT = BASE + 100 * TF


@pytest.fixture(scope='module')
def retained():
    db = sqlite3.connect(':memory:')
    P.init(db)
    rng = random.Random(17)
    for version in range(4):
        frame = pd.DataFrame(dict(ts=pd.to_datetime([BASE+i*TF for i in range(80)], unit='ms', utc=True),
                                  close=[rng.randrange(1, 100) for _ in range(80)]))
        frame = frame.assign(open=frame.close, high=frame.close+1, low=frame.close-.5, volume=5.)
        df = P.annotate(frame, instrument_id=IID, source=SOURCE, kind='candle',
                        received_ms=CUT-100+version//2, timeframe='4h', request_id=str(version))
        # Equal-clock revisions exercise rowid ties; newer foreign, partial,
        # and unavailable revisions must not displace an eligible predecessor.
        if version:
            df.loc[df.index % 5 == 0, 'instrument_id'] = 'binance_usdm:futures:ETHUSDT'
            df.loc[df.index % 7 == 0, 'source'] = 'https://other.example'
            df.loc[df.index % 3 == 0, 'bar_state'] = 'PARTIAL'
            df.loc[df.index % 11 == 0, 'available_at_ms'] = CUT + 10
        P.append(db, KEY, P.seal(df))
    db.commit()
    db.execute('PRAGMA query_only=ON')
    yield db
    db.close()


def exhaustive(db, cut, limit, iid, source, partial):
    records = [(rowid, P.read_record(db, raw)) for rowid, raw in db.execute(
        'SELECT rowid,record_json FROM market_revisions WHERE series_key=?', (KEY,))]
    known = [(rowid,r) for rowid,r in records if
             r['available_at_ms'] is not None and r['available_at_ms'] <= cut and r['observed_at_ms'] <= cut]
    if iid is None and len({r['instrument_id'] for _,r in known if r['instrument_id'] is not None}) > 1:
        return []
    winners = {}
    for rowid, r in known:
        if iid is not None and r['instrument_id'] != iid:
            continue
        if source is not None and r['source'] != source:
            continue
        if not partial and r['bar_state'] == 'PARTIAL':
            continue
        event = r['event_time_ms']
        order = (r['observed_at_ms'], rowid)
        if event not in winners or order > winners[event][0]:
            winners[event] = (order, r)
    return [winners[event][1] for event in sorted(winners)[-limit:]]


@pytest.mark.parametrize('limit', [1, 3, 20, 200])
@pytest.mark.parametrize('cut', [CUT-100, CUT, CUT+100])
@pytest.mark.parametrize('partial', [False, True])
@pytest.mark.parametrize('identity', [(IID, SOURCE), (IID, None), (None, None)])
def test_matches_exhaustive_oracle(retained, limit, cut, partial, identity):
    iid, source = identity
    expected = exhaustive(retained, cut, limit, iid, source, partial)
    changes = retained.total_changes
    actual = P.load(retained, KEY, as_of_ms=cut, limit=limit,
                    instrument_id=iid, source=source, include_partial=partial)
    assert retained.total_changes == changes
    if not expected:
        assert actual is None
    else:
        # Every numeric field and provenance field, not merely bar timestamps.
        assert json.loads(actual.drop(columns='ts').to_json(orient='records')) == expected


def test_staleness_and_missing_history_remain_explicit(retained):
    assert P.load(retained, KEY, as_of_ms=BASE-1, limit=3, instrument_id=IID, source=SOURCE) is None
    at = CUT + 100 * TF
    actual = P.load(retained, KEY, as_of_ms=at, limit=3, instrument_id=IID, source=SOURCE)
    expected = exhaustive(retained, at, 3, IID, SOURCE, False)
    safe = P.usable_current(actual, '4h', at, P.DEFAULT_TTL_S['4h'])
    assert safe['revision_id'].tolist() == [r['revision_id'] for r in expected]
    assert safe['quality'].eq('STALE').all()
    assert safe['close'].isna().all()


def test_selected_corruption_is_still_refused(retained):
    db = sqlite3.connect(':memory:')
    retained.backup(db)
    expected = exhaustive(db, CUT, 1, IID, SOURCE, False)[0]
    raw = db.execute('SELECT record_json FROM market_revisions WHERE revision_id=?',
                     (expected['revision_id'],)).fetchone()[0]
    altered = json.loads(raw)
    altered['close'] += 1
    db.execute('UPDATE market_revisions SET record_json=? WHERE revision_id=?',
               (json.dumps(altered), expected['revision_id']))
    with pytest.raises(ValueError, match='market_revision_content_corrupt'):
        P.load(db, KEY, as_of_ms=CUT, limit=1, instrument_id=IID, source=SOURCE)
    db.close()
