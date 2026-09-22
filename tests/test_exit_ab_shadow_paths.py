"""The frozen exit A/B protocol's virtual path geometry.

``record`` writes an opening stub; without ``update`` the paired comparison has
no observations at all. These tests pin the two frozen geometries against the
engine's own trade definition, and pin the safeguards that keep the shadow
ledger research-only: exact closed declared bars, deterministic replay,
idempotent writes, refusal instead of backfill, and a frozen exit once closed.
"""
import argparse
import json
import sqlite3

import numpy as np
import pandas as pd
import pytest

from scripts import exit_ab_shadow as S
from trader.cognition import dataset as D
from trader.strategy.spec import ExitSpec
from trader.strategy.vector_backtest import _trade

ENTRY_MS = 1789819200000
SYMBOL = 'AVAX/USDT'
KEY = f'{SYMBOL}|BUY|{ENTRY_MS}'


def bars_from(ohlc, start_ms=ENTRY_MS - 20 * S.TF_MS):
    """Declared 4h versions, one per (open, high, low, close) row."""
    out = []
    for i, (o, h, l, c) in enumerate(ohlc):
        open_ms = start_ms + i * S.TF_MS
        out.append({'version_id': f'v{i}', 'symbol': SYMBOL, 'open_ms': open_ms,
                    'value_hash': f'h{i}', 'bar': {'open': o, 'high': h, 'low': l, 'close': c}})
    return out


def flat_then(moves, base=100.0, span=0.5):
    """21 quiet bars then ``moves``. The entry bar is the last quiet one, so
    ATR(14) at entry is exactly ``span`` and the initial 2 ATR risk is 1.0 --
    every R below is therefore a plain price distance."""
    rows = [(base, base + span, base, base) for _ in range(21)]
    return rows + list(moves)


@pytest.fixture
def protocol():
    return S.read_json(S.PROTOCOL)


def replay(path, bars, protocol, side='long'):
    atr = S.atr_of(bars)
    entry_i = next(i for i, b in enumerate(bars) if b['open_ms'] == ENTRY_MS)
    return S.replay(path, side, bars[entry_i]['bar']['close'], bars, entry_i, atr, protocol)


# --- geometry -------------------------------------------------------------

def test_initial_risk_is_two_atr_of_the_declared_entry_bar(protocol):
    bars = bars_from(flat_then([(100, 100.5, 100, 100)]))
    for path in S.PATHS:
        state = replay(path, bars, protocol)
        assert state['atr_at_entry'] == pytest.approx(0.5)
        assert state['initial_risk'] == pytest.approx(1.0)
        assert state['initial_stop'] == pytest.approx(99.0)


def test_stop_does_not_move_before_the_one_r_arm(protocol):
    # +0.9R favourable is not enough to arm either path.
    bars = bars_from(flat_then([(100, 100.9, 99.8, 100.5)]))
    for path in S.PATHS:
        state = replay(path, bars, protocol)
        assert state['armed'] is False
        assert state['virtual_stop'] == pytest.approx(99.0)
        assert state['locked_r'] == pytest.approx(-1.0)
        assert state['peak_r'] == pytest.approx(0.9)


def test_a_trails_four_atr_and_b_retains_75_percent_of_peak(protocol):
    bars = bars_from(flat_then([(100, 102.0, 101.6, 101.9)]))
    a = replay('A_control_4x_atr', bars, protocol)
    b = replay('B_25pct_giveback', bars, protocol)
    assert a['armed'] and b['armed']
    assert a['peak_price'] == pytest.approx(102.0) and a['peak_r'] == pytest.approx(2.0)
    # A: peak 102 minus 4 x ATR(that bar) = 102 - 4 x 8.5/14.
    assert a['atr_last'] == pytest.approx(8.5 / 14)
    assert a['virtual_stop'] == pytest.approx(102.0 - 4 * 8.5 / 14)
    # B: entry + 0.75 x (102 - 100) = 101.5, i.e. 1.5R locked of a 2.0R peak.
    assert b['virtual_stop'] == pytest.approx(101.5)
    assert b['peak_r'] == pytest.approx(2.0)
    assert b['locked_r'] == pytest.approx(1.5)
    assert a['state'] == 'open' and b['state'] == 'open'


def test_stops_ratchet_only_and_never_loosen(protocol):
    bars = bars_from(flat_then([(100, 108.0, 106.1, 107.0), (107, 107.2, 106.5, 106.8)]))
    b = replay('B_25pct_giveback', bars, protocol)
    # The second bar's lower high must not pull the threshold back down.
    assert b['peak_price'] == pytest.approx(108.0)
    assert b['virtual_stop'] == pytest.approx(106.0)
    assert b['state'] == 'open'


def test_b_exits_on_the_declared_bar_that_crosses_the_threshold(protocol):
    bars = bars_from(flat_then([(100, 108.0, 106.1, 107.0), (107, 107.2, 105.0, 105.5)]))
    b = replay('B_25pct_giveback', bars, protocol)
    assert b['state'] == 'closed'
    assert b['exit_reason'] == 'virtual_stop'
    assert b['exit_price'] == pytest.approx(106.0)
    assert b['exit_r'] == pytest.approx(6.0)
    trigger = bars[-1]
    assert b['trigger_bar_open_ms'] == trigger['open_ms']
    assert b['trigger_ts_ms'] == trigger['open_ms'] + S.TF_MS
    assert b['trigger_version_id'] == trigger['version_id']
    assert b['trigger_version_hash'] == trigger['value_hash']


def test_a_survives_the_pullback_that_stops_b_out(protocol):
    bars = bars_from(flat_then([(100, 108.0, 106.1, 107.0), (107, 107.2, 105.0, 105.5)]))
    a = replay('A_control_4x_atr', bars, protocol)
    # A's trail ratchets to 108 - 4 x ATR = 103.86 and holds; 105.0 never reaches it.
    assert a['state'] == 'open'
    assert a['virtual_stop'] == pytest.approx(108.0 - 4 * 14.5 / 14)
    assert replay('B_25pct_giveback', bars, protocol)['state'] == 'closed'


def test_short_side_mirrors_the_long_geometry(protocol):
    bars = bars_from(flat_then([(93.9, 93.9, 92.0, 93.0)]))
    b = replay('B_25pct_giveback', bars, protocol, side='short')
    assert b['state'] == 'open'
    assert b['peak_price'] == pytest.approx(92.0)
    assert b['peak_r'] == pytest.approx(8.0)
    # entry - 0.75 x (100 - 92) = 94.0, i.e. 6.0R locked of an 8.0R peak.
    assert b['virtual_stop'] == pytest.approx(94.0)
    assert b['locked_r'] == pytest.approx(6.0)


@pytest.mark.parametrize('side,moves', [
    ('long', [(100, 102.0, 101.0, 101.8), (101.8, 104.0, 101.5, 103.5), (103.5, 103.6, 99.0, 99.5)]),
    ('short', [(100, 100.0, 98.0, 98.2), (98.2, 98.5, 96.0, 96.5), (96.5, 101.0, 96.4, 100.5)]),
])
def test_control_matches_the_engine_trade_definition(protocol, side, moves):
    """A is the live geometry; it must agree bar-for-bar with ``_trade``, which
    is the one shared definition of a trade."""
    rows = flat_then(moves)
    bars = bars_from(rows)
    state = replay('A_control_4x_atr', bars, protocol, side=side)

    df = pd.DataFrame([{'open': o, 'high': h, 'low': l, 'close': c} for o, h, l, c in rows])
    atr = S.atr_series(df, S.ATR_PERIOD).to_numpy(float)
    entry_i = next(i for i, b in enumerate(bars) if b['open_ms'] == ENTRY_MS)
    spec = ExitSpec(stop={'kind': 'atr', 'mult': 2.0}, target={'kind': 'none'},
                    trail={'kind': 'atr', 'mult': 4.0, 'arm_at_r': 1.0},
                    time={'max_bars': 500}, signal_exit='')
    exit_i, sl_dist, _ = _trade(
        entry_i, side, df, df['close'].to_numpy(float), df['high'].to_numpy(float),
        df['low'].to_numpy(float), atr, spec, 0.0, 0.0, 500, 4.0, 1.0, None, None, 0.0, 240)

    assert state['initial_risk'] == pytest.approx(sl_dist)
    assert state['state'] == 'closed'
    assert bars.index(next(b for b in bars if b['open_ms'] == state['trigger_bar_open_ms'])) == exit_i


def test_max_bars_closes_the_path_at_the_declared_close(protocol, monkeypatch):
    small = json.loads(json.dumps(protocol))
    small['B_candidate']['max_bars'] = 2
    bars = bars_from(flat_then([(100, 100.4, 99.8, 100.1), (100, 100.3, 99.9, 100.2)]))
    state = replay('B_25pct_giveback', bars, small)
    assert state['state'] == 'closed'
    assert state['exit_reason'] == 'max_bars'
    assert state['exit_price'] == pytest.approx(100.2)


def test_entry_bar_alone_leaves_the_path_open_with_risk_known(protocol):
    bars = bars_from(flat_then([]))
    state = replay('A_control_4x_atr', bars, protocol)
    assert state['state'] == 'open'
    assert state['bars_evaluated'] == 0
    assert state['initial_risk'] == pytest.approx(1.0)
    assert state['peak_r'] == pytest.approx(0.0)


# --- ledger safeguards ----------------------------------------------------

def _ledger(tmp_path, entry_price=100.0):
    db = S.init_db(tmp_path / 'ab.db')
    db.execute("INSERT INTO meta(key,value) VALUES('activation',?)", (json.dumps({'decision_id': 'c'}),))
    db.execute('INSERT INTO opportunities(' + ','.join(S.OPPORTUNITY_COLUMNS) + ') VALUES(' +
               ','.join('?' * len(S.OPPORTUNITY_COLUMNS)) + ')',
               (KEY, 'dec', 'ts', SYMBOL, 'BUY', ENTRY_MS, entry_price, 'ds', 'declared-1', 'v20', 'h20', '{}'))
    for path in S.PATHS:
        db.execute('INSERT INTO paths VALUES(?,?,?)', (KEY, path,
                   json.dumps({'state': 'open', 'entry_price': entry_price, 'virtual_only': True}, sort_keys=True)))
    db.commit(); db.close()
    return tmp_path / 'ab.db'


def _receipts(tmp_path, bars_by_symbol):
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / 'r.db'
    db = sqlite3.connect(path)
    db.executescript('''
      CREATE TABLE scans (scan_id TEXT PRIMARY KEY, as_of_ms INTEGER, payload TEXT, causes_complete INTEGER DEFAULT 0);
      CREATE TABLE versions (id TEXT PRIMARY KEY, symbol TEXT, tf TEXT, open_ms INTEGER, value_hash TEXT, payload TEXT);
      CREATE TABLE scan_versions (scan_id TEXT, version_id TEXT, PRIMARY KEY(scan_id, version_id));
    ''')
    universe = S.read_json(S.DECLARATION)['universe']
    payload = {'scope': {'declaration_version': D.declaration_version(S.read_json(S.DECLARATION)),
                         'availability_receipts': [{'symbol': s, 'status': 'available', 'retry_required': False}
                                                   for s in universe]}}
    db.execute('INSERT INTO scans VALUES(?,?,?,1)', ('declared-1', 1, json.dumps(payload)))
    for symbol in universe:
        for b in bars_by_symbol.get(symbol, bars_by_symbol[SYMBOL]):
            vid = f'{symbol}-{b["open_ms"]}'
            db.execute('INSERT INTO versions VALUES(?,?,?,?,?,?)',
                       (vid, symbol, '4h', b['open_ms'], b['value_hash'], json.dumps(b['bar'])))
            db.execute('INSERT INTO scan_versions VALUES(?,?)', ('declared-1', vid))
    db.commit(); db.close()
    return path


def _run(ledger, receipt_db, capsys):
    S.update(argparse.Namespace(ledger=ledger, receipt_db=receipt_db))
    return json.loads(capsys.readouterr().out)


def _states(ledger):
    with sqlite3.connect(ledger) as db:
        return {p: json.loads(s) for p, s in db.execute(
            'SELECT path,state_json FROM paths WHERE opportunity_key=?', (KEY,))}


def test_update_advances_both_paths_and_is_idempotent(tmp_path, capsys):
    bars = bars_from(flat_then([(100, 108.0, 106.1, 107.0)]))
    ledger, receipts = _ledger(tmp_path), _receipts(tmp_path, {SYMBOL: bars})

    first = _run(ledger, receipts, capsys)
    assert first['paths_advanced'] == 2
    assert first['opportunities_updated'] == 1
    before = _states(ledger)
    assert before['B_25pct_giveback']['locked_r'] == pytest.approx(6.0)
    assert before['A_control_4x_atr']['last_version_hash'] == bars[-1]['value_hash']

    second = _run(ledger, receipts, capsys)
    assert second['paths_advanced'] == 0
    assert _states(ledger) == before


def test_update_does_not_touch_the_recorded_opportunity(tmp_path, capsys):
    bars = bars_from(flat_then([(100, 108.0, 106.1, 107.0)]))
    ledger, receipts = _ledger(tmp_path), _receipts(tmp_path, {SYMBOL: bars})
    with sqlite3.connect(ledger) as db:
        before = db.execute('SELECT * FROM opportunities').fetchall()
    _run(ledger, receipts, capsys)
    with sqlite3.connect(ledger) as db:
        assert db.execute('SELECT * FROM opportunities').fetchall() == before


def test_a_declared_sequence_gap_refuses_instead_of_bridging(tmp_path, capsys):
    bars = bars_from(flat_then([(100, 108.0, 106.1, 107.0)]))
    del bars[5]                                   # a hole in the declared series
    ledger, receipts = _ledger(tmp_path), _receipts(tmp_path, {SYMBOL: bars})

    out = _run(ledger, receipts, capsys)
    assert out['paths_advanced'] == 0 and out['gaps'] == 1
    assert _states(ledger)['A_control_4x_atr'] == {'state': 'open', 'entry_price': 100.0, 'virtual_only': True}
    with sqlite3.connect(ledger) as db:
        assert [r[0] for r in db.execute('SELECT reason FROM gaps')] == ['declared_bar_sequence_gap_retry']
        _run(ledger, receipts, capsys)
        assert db.execute('SELECT count(*) FROM gaps').fetchone()[0] == 1   # gaps are not duplicated


def test_missing_atr_history_is_refused_not_fabricated(tmp_path, capsys):
    bars = bars_from(flat_then([(100, 108.0, 106.1, 107.0)]))[-6:]   # ATR(14) undefined
    ledger, receipts = _ledger(tmp_path), _receipts(tmp_path, {SYMBOL: bars})
    out = _run(ledger, receipts, capsys)
    assert out['paths_advanced'] == 0
    with sqlite3.connect(ledger) as db:
        assert [r[0] for r in db.execute('SELECT reason FROM gaps')] == ['entry_atr_coverage_incomplete_retry']


def test_entry_bar_absent_from_the_declared_scan_is_a_gap(tmp_path, capsys):
    bars = [b for b in bars_from(flat_then([(100, 108.0, 106.1, 107.0)])) if b['open_ms'] != ENTRY_MS]
    ledger, receipts = _ledger(tmp_path), _receipts(tmp_path, {SYMBOL: bars})
    out = _run(ledger, receipts, capsys)
    assert out['paths_advanced'] == 0
    with sqlite3.connect(ledger) as db:
        reasons = [r[0] for r in db.execute('SELECT reason FROM gaps')]
    assert 'entry_bar_not_in_declared_scan_retry' in reasons or 'declared_bar_sequence_gap_retry' in reasons


def test_a_closed_exit_is_frozen_and_a_replay_mismatch_is_reported(tmp_path, capsys):
    bars = bars_from(flat_then([(100, 108.0, 106.1, 107.0), (107, 107.2, 105.0, 105.5)]))
    ledger, receipts = _ledger(tmp_path), _receipts(tmp_path, {SYMBOL: bars})
    _run(ledger, receipts, capsys)
    closed = _states(ledger)['B_25pct_giveback']
    assert closed['state'] == 'closed'

    # The declared history changes underneath an already-closed path.
    moved = json.loads(json.dumps(bars))
    moved[-1]['bar']['low'] = 106.5                       # no longer crosses
    other = _receipts(tmp_path / 'moved', {SYMBOL: moved})
    out = _run(ledger, other, capsys)

    assert _states(ledger)['B_25pct_giveback'] == closed  # exit not rewritten
    assert out['gaps'] >= 1
    with sqlite3.connect(ledger) as db:
        assert 'closed_path_replay_mismatch' in [r[0] for r in db.execute('SELECT reason FROM gaps')]


def test_update_requires_activation(tmp_path):
    db = S.init_db(tmp_path / 'ab.db'); db.close()
    bars = bars_from(flat_then([]))
    with pytest.raises(ValueError, match='shadow_not_activated'):
        S.update(argparse.Namespace(ledger=tmp_path / 'ab.db',
                                    receipt_db=_receipts(tmp_path, {SYMBOL: bars})))
