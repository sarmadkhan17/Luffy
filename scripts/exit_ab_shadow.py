"""Research-only prospective paired exit recorder.

This module never opens, changes, or reconciles a venue position. ``collect``
uses a separate declared-universe receipt directory; ``activate`` establishes a
forward cursor; ``record`` reads the journal and declared receipts read-only and
writes only the separate shadow ledger; ``update`` replays the paired virtual
exit paths over exact closed declared bars. Incomplete PIT coverage is a retry/gap,
never a zero or a backfilled observation.
"""
import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import time

import pandas as pd

from trader.agents.indicators import atr_series
from trader.cognition import dataset as D
from trader.observability import declared

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT / 'docs/superpowers/artifacts/exit-geometry/2026-09-19-prospective-ab-protocol.json'
DECLARATION = ROOT / 'docs/superpowers/artifacts/exit-geometry/2026-09-19-prospective-ab-declaration.json'
FREEZE = ROOT / 'docs/superpowers/artifacts/exit-geometry/2026-09-19-prospective-ab-freeze.json'
JOURNAL = ROOT / 'data/luffy.db'
DEFAULT_RECEIPT_DB = ROOT / 'data/exit-ab-shadow/declared-population/attention.db'
DEFAULT_LEDGER = ROOT / 'data/exit-ab-shadow/ab-shadow.db'
TF_MS = 4 * 60 * 60 * 1000
ATR_PERIOD = 14
PATHS = ('A_control_4x_atr', 'B_25pct_giveback')
# ``update`` reads the same contract blocks ``validate_contract`` pins, so the
# geometry can never drift from the frozen protocol without the contract check
# failing first.
PATH_CONTRACT = {'A_control_4x_atr': 'A_control', 'B_25pct_giveback': 'B_candidate'}

# Written explicitly so ``status`` takes its schema default and column order
# changes cannot silently misalign the recorded values.
OPPORTUNITY_COLUMNS = (
    'key', 'decision_id', 'decision_ts', 'symbol', 'side', 'signal_bar_open_ms',
    'entry_price', 'decision_scan_id', 'declared_scan_id', 'version_id',
    'version_hash', 'receipt_json',
)


def read_json(path):
    return json.loads(Path(path).read_text())


def validate_contract():
    p, d, f = read_json(PROTOCOL), read_json(DECLARATION), read_json(FREEZE)
    D.validate_freeze(f, d)
    if p['status'] != 'prepared_not_activated' and p['status'] != 'active':
        raise ValueError('protocol_status_invalid')
    if p['strategy_id'] != 'auth_donchian_breakout_trail' or p['A_control']['trail_atr'] != 4.0:
        raise ValueError('control_contract_invalid')
    if p['B_candidate']['peak_profit_giveback'] != 0.25:
        raise ValueError('candidate_contract_invalid')
    return p, d, f


def ro(path):
    db = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True, timeout=.5)
    db.execute('BEGIN')
    return db


def init_db(path):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.executescript('''
      CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS opportunities (
        key TEXT PRIMARY KEY, decision_id TEXT NOT NULL, decision_ts TEXT NOT NULL,
        symbol TEXT NOT NULL, side TEXT NOT NULL, signal_bar_open_ms INTEGER NOT NULL,
        entry_price REAL NOT NULL, decision_scan_id TEXT, declared_scan_id TEXT NOT NULL,
        version_id TEXT NOT NULL, version_hash TEXT NOT NULL, receipt_json TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'recorded');
      CREATE TABLE IF NOT EXISTS paths (
        opportunity_key TEXT NOT NULL, path TEXT NOT NULL,
        state_json TEXT NOT NULL, PRIMARY KEY(opportunity_key, path));
      CREATE TABLE IF NOT EXISTS gaps (
        id INTEGER PRIMARY KEY AUTOINCREMENT, observed_ms INTEGER NOT NULL,
        reason TEXT NOT NULL, detail_json TEXT NOT NULL);
    ''')
    return db


def latest_complete(db, declaration_version, universe):
    row = db.execute('SELECT scan_id,as_of_ms,payload FROM scans WHERE causes_complete=1 ORDER BY as_of_ms DESC LIMIT 1').fetchone()
    if not row:
        raise ValueError('declared_scan_missing_retry')
    scan_id, as_of, payload = row
    body = json.loads(payload)
    scope = body.get('scope', {})
    if scope.get('declaration_version') != declaration_version:
        raise ValueError('declared_version_mismatch_retry')
    receipts = scope.get('availability_receipts', [])
    if (len(receipts) != len(universe) or {r.get('symbol') for r in receipts} != set(universe) or
            any(r.get('status') != 'available' or r.get('retry_required') for r in receipts)):
        raise ValueError('declared_universe_incomplete_retry')
    ids = [r[0] for r in db.execute('SELECT version_id FROM scan_versions WHERE scan_id=?', (scan_id,))]
    versions = {}
    for vid in ids:
        x = db.execute('SELECT id,symbol,tf,open_ms,value_hash,payload FROM versions WHERE id=?', (vid,)).fetchone()
        if x and x[2] == '4h':
            versions.setdefault(x[1], []).append(dict(version_id=x[0], symbol=x[1], open_ms=x[3], value_hash=x[4], bar=json.loads(x[5])))
    if set(versions) != set(universe):
        raise ValueError('declared_versions_incomplete_retry')
    return scan_id, as_of, versions


def activate(args):
    validate_contract()
    with closing(ro(JOURNAL)) as src:
        row = src.execute('SELECT id,ts FROM decisions ORDER BY rowid DESC LIMIT 1').fetchone()
    if not row:
        raise ValueError('journal_cursor_missing')
    db = init_db(args.ledger)
    db.execute('INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)', ('activation', json.dumps({'decision_id': row[0], 'activated_ms': int(time.time()*1000)})))
    db.commit(); db.close()
    print(json.dumps({'state': 'activated_after_cursor', 'decision_id': row[0], 'decision_ts': row[1], 'ledger': str(args.ledger)}, sort_keys=True))


def record(args):
    protocol, declaration, freeze = validate_contract()
    db = init_db(args.ledger)
    activation = db.execute("SELECT value FROM meta WHERE key='activation'").fetchone()
    if not activation:
        raise ValueError('shadow_not_activated')
    cursor = json.loads(activation[0])['decision_id']
    with closing(ro(JOURNAL)) as journal, closing(ro(args.receipt_db)) as receipts:
        scan_id, as_of, versions = latest_complete(receipts, D.declaration_version(declaration), declaration['universe'])
        rows = journal.execute('SELECT id,ts,symbol,signals_json,scan_id FROM decisions WHERE rowid>(SELECT rowid FROM decisions WHERE id=?) ORDER BY rowid', (cursor,)).fetchall()
        added = 0
        for did, ts, symbol, signals, decision_scan in rows:
            if symbol not in versions or not (protocol['window']['start_ms'] <= int(__import__('datetime').datetime.fromisoformat(ts).timestamp()*1000) < protocol['window']['discovery_cut_ms']):
                continue
            for sig in json.loads(signals or '[]'):
                if sig.get('strategy_id') != protocol['strategy_id'] or sig.get('action') not in ('BUY','SELL'):
                    continue
                side = sig['action']; decision_ms = int(__import__('datetime').datetime.fromisoformat(ts).timestamp()*1000)
                eligible = [v for v in versions[symbol] if v['open_ms'] + TF_MS <= decision_ms]
                if not eligible:
                    db.execute('INSERT INTO gaps(observed_ms,reason,detail_json) VALUES(?,?,?)', (int(time.time()*1000), 'signal_bar_missing_retry', json.dumps({'decision_id': did, 'symbol': symbol})))
                    continue
                v = max(eligible, key=lambda x:x['open_ms']); key = f'{symbol}|{side}|{v["open_ms"]}'
                if db.execute('SELECT 1 FROM opportunities WHERE key=?', (key,)).fetchone(): continue
                receipt = {'protocol': protocol['schema_version'], 'decision_id': did, 'decision_ts': ts, 'decision_scan_id': decision_scan, 'declared_scan_id': scan_id, 'declared_as_of_ms': as_of, 'version_id': v['version_id'], 'version_hash': v['value_hash'], 'signal_bar': v['bar'], 'binding': 'exact_closed_declared_4h_version'}
                db.execute(
                    'INSERT INTO opportunities(' + ','.join(OPPORTUNITY_COLUMNS) + ')'
                    ' VALUES(' + ','.join('?' * len(OPPORTUNITY_COLUMNS)) + ')',
                    (key,did,ts,symbol,side,v['open_ms'],v['bar']['close'],decision_scan,scan_id,v['version_id'],v['value_hash'],json.dumps(receipt,sort_keys=True)))
                for path in ('A_control_4x_atr','B_25pct_giveback'):
                    db.execute('INSERT INTO paths VALUES(?,?,?)', (key,path,json.dumps({'state':'open','entry_price':v['bar']['close'],'virtual_only':True},sort_keys=True)))
                added += 1
    db.commit(); db.close()
    print(json.dumps({'state':'recorded','opportunities_added':added,'ledger':str(args.ledger)}, sort_keys=True))


def _gap(db, reason, detail):
    """Gaps are observations, not events: an identical unresolved gap is
    recorded once so repeated updates leave the ledger unchanged."""
    detail_json = json.dumps(detail, sort_keys=True)
    if db.execute('SELECT 1 FROM gaps WHERE reason=? AND detail_json=?', (reason, detail_json)).fetchone():
        return
    db.execute('INSERT INTO gaps(observed_ms,reason,detail_json) VALUES(?,?,?)',
               (int(time.time() * 1000), reason, detail_json))


def contiguous_series(versions_for_symbol):
    """Declared 4h versions in bar order. A sequence break is a coverage gap,
    never a bridged or backfilled bar."""
    bars = sorted(versions_for_symbol, key=lambda v: v['open_ms'])
    for a, b in zip(bars, bars[1:]):
        if b['open_ms'] - a['open_ms'] != TF_MS:
            return bars, {'prev_open_ms': a['open_ms'], 'next_open_ms': b['open_ms']}
    return bars, None


def atr_of(bars):
    """The engine's own ATR definition (rolling mean true range, period 14) so
    the shadow geometry and ``vector_backtest`` cannot diverge. Insufficient
    history stays NaN and is refused upstream rather than filled."""
    df = pd.DataFrame([{'high': b['bar']['high'], 'low': b['bar']['low'],
                        'close': b['bar']['close']} for b in bars])
    return atr_series(df, ATR_PERIOD).to_numpy(float)


def chain_hash(bars):
    h = hashlib.sha256()
    for b in bars:
        h.update(f"{b['open_ms']}:{b['value_hash']}".encode())
    return h.hexdigest()


def replay(path, side, entry_price, bars, entry_i, atr, protocol):
    """Deterministic full replay from entry over exact closed declared bars.

    Mirrors ``vector_backtest._trade`` bar order exactly: extend the favorable
    extreme from this bar, ratchet the stop, then test the cross on the same
    bar. Both paths share the initial 2 ATR risk and the +1R arm; A trails 4
    ATR off the extreme, B retains 75% of peak profit. Ratchet only, so the
    stop never loosens. Returns the state dict; no state is carried between
    runs, which is what makes the updater idempotent.
    """
    cfg = protocol[PATH_CONTRACT[path]]
    sign = 1.0 if side == 'long' else -1.0
    risk = atr[entry_i] * float(cfg['initial_stop_atr'])
    arm_r = float(cfg['arm_r'])
    max_bars = int(cfg['max_bars'])
    stop = entry_price - sign * risk
    initial_stop = stop
    best = entry_price
    armed = False
    armed_bar = None
    exit_at = None
    truncated = None
    last_i = entry_i
    end = min(entry_i + max_bars, len(bars) - 1)
    for j in range(entry_i + 1, end + 1):
        bar = bars[j]['bar']
        best = max(best, bar['high']) if sign > 0 else min(best, bar['low'])
        if abs(best - entry_price) >= arm_r * risk:
            if path == 'A_control_4x_atr':
                if not (atr[j] == atr[j] and atr[j] > 0):
                    truncated = {'reason': 'trail_atr_unavailable', 'open_ms': bars[j]['open_ms']}
                    break
                candidate = best - sign * float(cfg['trail_atr']) * atr[j]
            else:
                # retain 75% of the peak excursion; (best - entry) already
                # carries the sign, so both sides use one expression
                candidate = entry_price + float(cfg['peak_profit_retained']) * (best - entry_price)
            stop = max(stop, candidate) if sign > 0 else min(stop, candidate)
            if not armed:
                armed, armed_bar = True, bars[j]['open_ms']
        last_i = j
        crossed = bar['low'] <= stop if sign > 0 else bar['high'] >= stop
        if crossed:
            exit_at = (j, stop, 'virtual_stop')
            break
    else:
        if end - entry_i == max_bars and end > entry_i:
            exit_at = (end, bars[end]['bar']['close'], 'max_bars')

    evaluated = bars[entry_i:last_i + 1]
    last = bars[last_i]
    current_price = last['bar']['close']
    state = {
        'path': path, 'virtual_only': True, 'side': side,
        'entry_price': entry_price, 'entry_bar_open_ms': bars[entry_i]['open_ms'],
        'atr_period': ATR_PERIOD, 'atr_at_entry': atr[entry_i],
        'atr_last': atr[last_i] if path == 'A_control_4x_atr' else None,
        'initial_risk': risk, 'initial_stop': initial_stop,
        'armed': armed, 'armed_at_bar_open_ms': armed_bar,
        'peak_price': best, 'peak_r': (best - entry_price) * sign / risk,
        'current_price': current_price,
        'current_r': (current_price - entry_price) * sign / risk,
        'virtual_stop': stop, 'locked_r': (stop - entry_price) * sign / risk,
        'bars_evaluated': last_i - entry_i,
        'last_bar_open_ms': last['open_ms'], 'last_version_id': last['version_id'],
        'last_version_hash': last['value_hash'],
        'evaluated_chain_hash': chain_hash(evaluated),
        'costs_applied': False,
        'state': 'open', 'exit_reason': None, 'exit_price': None, 'exit_r': None,
        'trigger_bar_open_ms': None, 'trigger_ts_ms': None,
        'trigger_version_id': None, 'trigger_version_hash': None,
        'truncated': truncated,
    }
    if exit_at:
        j, px, reason = exit_at
        state.update({
            'state': 'closed', 'exit_reason': reason, 'exit_price': px,
            'exit_r': (px - entry_price) * sign / risk,
            'trigger_bar_open_ms': bars[j]['open_ms'],
            'trigger_ts_ms': bars[j]['open_ms'] + TF_MS,
            'trigger_version_id': bars[j]['version_id'],
            'trigger_version_hash': bars[j]['value_hash'],
        })
    return state


# Fields whose disagreement on replay means the declared history behind a
# already-closed path changed; the recorded exit is frozen and the mismatch is
# reported instead of being overwritten.
FROZEN_FIELDS = ('state', 'exit_reason', 'exit_price', 'exit_r', 'trigger_bar_open_ms')


def update(args):
    """Advance every eligible virtual path once. Research only: reads the
    declared receipts and writes the shadow ledger, never a venue, spec, or
    live exit."""
    protocol, declaration, _ = validate_contract()
    db = init_db(args.ledger)
    if not db.execute("SELECT value FROM meta WHERE key='activation'").fetchone():
        raise ValueError('shadow_not_activated')
    summary = {'opportunities_updated': 0, 'paths_advanced': 0, 'exits': {p: 0 for p in PATHS},
               'already_closed': 0, 'gaps': 0, 'open_paths': []}
    with closing(ro(args.receipt_db)) as receipts:
        scan_id, as_of, versions = latest_complete(
            receipts, D.declaration_version(declaration), declaration['universe'])
    series = {}
    for opp in db.execute('SELECT key,symbol,side,signal_bar_open_ms,entry_price FROM opportunities ORDER BY key').fetchall():
        key, symbol, action, entry_ms, entry_price = opp
        side = 'long' if action == 'BUY' else 'short'
        if symbol not in series:
            bars, broken = contiguous_series(versions.get(symbol, []))
            if broken:
                _gap(db, 'declared_bar_sequence_gap_retry', dict(symbol=symbol, **broken))
                summary['gaps'] += 1
            series[symbol] = (None if broken else bars)
        bars = series[symbol]
        if bars is None:
            continue
        index = {b['open_ms']: i for i, b in enumerate(bars)}
        if entry_ms not in index:
            _gap(db, 'entry_bar_not_in_declared_scan_retry', {'key': key, 'signal_bar_open_ms': entry_ms})
            summary['gaps'] += 1
            continue
        entry_i = index[entry_ms]
        atr = atr_of(bars)
        if not (atr[entry_i] == atr[entry_i] and atr[entry_i] > 0):
            _gap(db, 'entry_atr_coverage_incomplete_retry', {'key': key, 'signal_bar_open_ms': entry_ms})
            summary['gaps'] += 1
            continue
        touched = False
        for path in PATHS:
            row = db.execute('SELECT state_json FROM paths WHERE opportunity_key=? AND path=?', (key, path)).fetchone()
            if not row:
                continue
            before = json.loads(row[0])
            fresh = replay(path, side, entry_price, bars, entry_i, atr, protocol)
            fresh['declared_scan_id'] = scan_id
            fresh['declared_as_of_ms'] = as_of
            if before.get('state') == 'closed':
                summary['already_closed'] += 1
                if any(before.get(f) != fresh.get(f) for f in FROZEN_FIELDS):
                    _gap(db, 'closed_path_replay_mismatch', {'key': key, 'path': path,
                         'recorded': {f: before.get(f) for f in FROZEN_FIELDS},
                         'replayed': {f: fresh.get(f) for f in FROZEN_FIELDS}})
                    summary['gaps'] += 1
                continue
            if fresh['truncated']:
                _gap(db, 'path_truncated_' + fresh['truncated']['reason'], {'key': key, 'path': path, **fresh['truncated']})
                summary['gaps'] += 1
            after = json.dumps(fresh, sort_keys=True)
            if after != row[0]:
                db.execute('UPDATE paths SET state_json=? WHERE opportunity_key=? AND path=?', (after, key, path))
                summary['paths_advanced'] += 1
                touched = True
            if fresh['state'] == 'closed':
                summary['exits'][path] += 1
            else:
                summary['open_paths'].append({'key': key, 'path': path, 'peak_r': round(fresh['peak_r'], 4),
                                              'locked_r': round(fresh['locked_r'], 4),
                                              'current_r': round(fresh['current_r'], 4),
                                              'armed': fresh['armed'],
                                              'bars_evaluated': fresh['bars_evaluated']})
        summary['opportunities_updated'] += int(touched)
    db.execute('INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)',
               ('last_update', json.dumps({'declared_scan_id': scan_id, 'declared_as_of_ms': as_of}, sort_keys=True)))
    db.commit(); db.close()
    print(json.dumps({'state': 'updated_research_only', 'declared_scan_id': scan_id, **summary}, sort_keys=True))


def collect(args):
    validate_contract()
    config = {'declaration': str(DECLARATION), 'receipt': str(FREEZE)}
    result = declared.run(config, Path(args.receipt_db).parent)
    print(json.dumps({'state':'collected_research_only','result':result}, sort_keys=True))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--ledger', type=Path, default=DEFAULT_LEDGER)
    ap.add_argument('--receipt-db', type=Path, default=DEFAULT_RECEIPT_DB)
    sub = ap.add_subparsers(dest='command', required=True)
    sub.add_parser('activate'); sub.add_parser('record')
    sub.add_parser('update'); sub.add_parser('collect')
    args = ap.parse_args()
    if args.command == 'activate': activate(args)
    elif args.command == 'record': record(args)
    elif args.command == 'update': update(args)
    else: collect(args)


if __name__ == '__main__': main()
