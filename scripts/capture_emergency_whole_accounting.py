"""Read-only demo whole-trade capture for a FLAT emergency exit, from the
immutable archived `emergency-accounting-intent.v1` record only.

Reads the journal ledger (mode=ro) and bounded demo venue history; never
writes the live journal, submits an order or imports into learning memory.
Output is DERIVED (see emergency_whole_accounting.py) and self-contained;
retries require a new output filename.

The overlap scan (journal trades, other archived emergency intents, and any
still-active recovery intent for the same symbol) runs inside one BEGIN
snapshot so all three reads see the same journal state, and covers all
same-symbol activity from this intent's own start onward — not merely
through its flat_verified_ms — since the whole-trade capture's own fill and
funding history queries extend through "now", not through flat_verified_ms.
Malformed archived rows, unparseable timestamps or a scan that would need
paging past MAX_OVERLAP_SCAN rows refuse explicitly rather than silently
reporting "no conflict found".
"""
import argparse
import json
from pathlib import Path
import sqlite3

from trader.engine import emergency_whole_accounting as EWA
from trader.engine.recovery import KEY as RECOVERY_KEY

MAX_OVERLAP_SCAN = 1000


def canonical_journal_symbol(value):
    """Journal/intent symbols are stored slash-form with no venue futures
    suffix (e.g. 'BTC/USDT'), never the bare venue form or a ccxt-suffixed
    one. Anything else is an unsupported/malformed form, flagged rather
    than silently accepted as a valid comparison key."""
    return isinstance(value, str) and bool(value) and value.endswith('/USDT') and ':' not in value


def scan_overlap(db, intent):
    """One coherent read of trades/execution_accounting/state_kv. Returns
    (overlap_trade_ids, overlap_intent_ids, scan_metadata)."""
    from trader.cognition.outcomes import timestamp

    trade_rows = db.execute(
        'SELECT id, opened_at, closed_at FROM trades WHERE symbol=? LIMIT ?',
        (intent['symbol'], MAX_OVERLAP_SCAN+1)).fetchall()
    trades_truncated = len(trade_rows) > MAX_OVERLAP_SCAN
    overlap_trades, malformed_trades = [], []
    for tid, opened_at, closed_at in trade_rows[:MAX_OVERLAP_SCAN]:
        try:
            opened_ms = timestamp(opened_at)
            closed_ms = timestamp(closed_at) if closed_at is not None else None
            # A row "closed" before it was "opened" is internally corrupt;
            # its true window is undecidable, so it must refuse rather than
            # fall through to a closed_ms-only comparison that could
            # silently read it as "no conflict".
            if opened_ms < 0 or (closed_ms is not None and not opened_ms <= closed_ms):
                raise ValueError('chronology')
        except Exception:
            malformed_trades.append(tid)
            continue
        # Still-open, or closed at/after this intent began: the whole-trade
        # capture's own history extends through "now", so a trade that only
        # started after this intent's flat_verified_ms is still a conflict.
        if closed_ms is None or closed_ms >= intent['created_ms']:
            overlap_trades.append(tid)

    intent_rows = db.execute('SELECT id, payload FROM execution_accounting WHERE id<>? LIMIT ?',
                             (intent['id'], MAX_OVERLAP_SCAN+1)).fetchall()
    intents_truncated = len(intent_rows) > MAX_OVERLAP_SCAN
    overlap_intents, malformed_intents = [], []
    for rid, payload in intent_rows[:MAX_OVERLAP_SCAN]:
        try:
            other = json.loads(payload)
            osym, oid = other['symbol'], other['id']
            ocreated, oflat = other['created_ms'], other['flat_verified_ms']
            if (not canonical_journal_symbol(osym) or not isinstance(oid, str) or not oid or oid != rid
                    or type(ocreated) is not int or type(oflat) is not int or not 0 <= ocreated <= oflat):
                raise ValueError('malformed')
        except Exception:
            malformed_intents.append(rid)
            continue
        if osym == intent['symbol'] and oflat >= intent['created_ms']:
            overlap_intents.append(rid)

    active_recovery_conflict = False
    row = db.execute("SELECT value FROM state_kv WHERE key=?", (RECOVERY_KEY,)).fetchone()
    if row is not None and row[0] not in (None, 'null'):
        try:
            pending = json.loads(row[0])
            if not isinstance(pending, dict) or not isinstance(pending.get('symbol'), str) or not pending['symbol']:
                raise ValueError('malformed')
        except Exception:
            active_recovery_conflict = True
        else:
            # Any active recovery for this symbol conflicts, including one
            # that shares this intent's own id: an archived, released-flat
            # intent and a still-active recovery entry cannot both be true.
            if pending['symbol'] == intent['symbol']:
                active_recovery_conflict = True

    scan = dict(trades_scanned=len(trade_rows), intents_scanned=len(intent_rows),
                truncated=trades_truncated or intents_truncated,
                malformed_intent_ids=malformed_intents,
                malformed_trade_ids=malformed_trades,
                active_recovery_conflict=active_recovery_conflict)
    return overlap_trades, overlap_intents, scan


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--journal', type=Path, default=Path('data/luffy.db'))
    p.add_argument('--intent-id')
    p.add_argument('--output', type=Path)
    p.add_argument('--replay', type=Path)
    args = p.parse_args()
    if args.replay:
        if args.intent_id or args.output:
            p.error('--replay cannot be combined with capture arguments')
        artifact = json.loads(args.replay.read_text())
    else:
        if not args.intent_id or not args.output:
            p.error('--intent-id and --output required for capture')
        if args.output.exists():
            p.error('output already exists; use a new filename')
        from trader.core.config import Env
        if Env.get('BINANCE_DEMO', 'true').lower() not in ('1', 'true', 'yes'):
            p.error('this capture command is limited to demo accounting')
        with sqlite3.connect(args.journal.resolve().as_uri()+'?mode=ro', uri=True) as db:
            db.execute('BEGIN')
            row = db.execute('SELECT payload FROM execution_accounting WHERE id=?', (args.intent_id,)).fetchone()
            if row is None:
                p.error('archived intent not found')
            intent = json.loads(row[0])
            if intent.get('id') != args.intent_id:
                p.error('archived intent id does not match the requested row; refusing')
            overlap_trades, overlap_intents, scan = scan_overlap(db, intent)
        from trader.data.feed import make_exchange
        ex = make_exchange('futures', demo=True)
        ex.timeout = 5000
        artifact = EWA.capture(intent, ex, overlap_trades, overlap_intents, scan)
        EWA.replay(artifact)
        with args.output.open('x') as out:
            json.dump(artifact, out, indent=2, sort_keys=True, allow_nan=False)
    result = EWA.replay(artifact)
    print(json.dumps({k: v for k, v in result.items() if k != 'accounting'}, sort_keys=True))


if __name__ == '__main__':
    main()
