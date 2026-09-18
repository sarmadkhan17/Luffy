"""Forward-only shadow forecasts. Reads attention snapshots; never trades.

Run --once from the watchdog. Independent SQLite ledger and atomic health file;
failures cannot reach the trading thread. No network or strategy admission.
"""
from __future__ import annotations

from contextlib import closing
import argparse
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import time

from . import population

from trader.cognition.forecast_protocol import TF, PROTOCOL, PROTOCOL_ID, encode, usable


def source_snapshot(path):
    """One coherent read of a completed scan and its exact retained versions."""
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro",
                                 uri=True, timeout=.1)) as db:
        db.execute("BEGIN")
        row = db.execute("SELECT payload FROM scans WHERE causes_complete=1 "
                         "AND payload IS NOT NULL ORDER BY as_of_ms DESC LIMIT 1").fetchone()
        if not row:
            return None
        scan = json.loads(row[0])
        bars = {}
        for vid, first_seen, payload in db.execute(
                "SELECT v.id,v.first_seen_ms,v.payload FROM versions v "
                "JOIN scan_versions s ON s.version_id=v.id WHERE s.scan_id=?",
                (scan["scan_id"],)):
            bar = json.loads(payload)
            bar.update(version_id=vid, available_ms=first_seen)
            bars.setdefault(bar["symbol"], []).append(bar)
        causes = [json.loads(r[0]) for r in db.execute(
            "SELECT payload FROM causes WHERE scan_id=?", (scan["scan_id"],))]
        return scan, bars, causes


def ledger(path):
    db = sqlite3.connect(path, timeout=.1)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA auto_vacuum=FULL")
    db.execute("PRAGMA max_page_count=16384")  # 64MiB with the default 4KiB pages
    db.executescript("""
      CREATE TABLE IF NOT EXISTS protocols (id TEXT PRIMARY KEY, activated_ms INTEGER, payload TEXT);
      CREATE TABLE IF NOT EXISTS episodes (
        id TEXT PRIMARY KEY, protocol_id TEXT, symbol TEXT, created_ms INTEGER,
        deadline_ms INTEGER, status TEXT, prediction TEXT, outcome TEXT);
      CREATE INDEX IF NOT EXISTS episode_symbol ON episodes(symbol,deadline_ms);
      CREATE TABLE IF NOT EXISTS diagnostics (ts_ms INTEGER, status TEXT, detail TEXT);
      CREATE TABLE IF NOT EXISTS forecast_sources (episode_id TEXT, phase TEXT, payload TEXT NOT NULL, PRIMARY KEY(episode_id,phase));
    """)
    return db



def summary(db):
    counts = dict(db.execute("SELECT status,COUNT(*) FROM episodes GROUP BY status").fetchall())
    learning = {}
    for row in db.execute("SELECT prediction,outcome FROM episodes WHERE status='resolved'"):
        prediction, outcome = json.loads(row[0]), json.loads(row[1])
        group = "selected" if prediction["selected"] else "ignored"
        key = group + ":" + outcome["supports"]
        learning[key] = learning.get(key, 0) + 1
    recent = [dict(id=r["id"], symbol=r["symbol"], status=r["status"],
                   deadline_ms=r["deadline_ms"], prediction=json.loads(r["prediction"]),
                   outcome=json.loads(r["outcome"]) if r["outcome"] else None)
              for r in db.execute("SELECT * FROM episodes ORDER BY created_ms DESC,id LIMIT 16")]
    return {"counts": counts, "descriptive_outcomes": learning, "recent": recent,
            "limitations": ["Uncalibrated shadow price forecasts, not P&L or trading authority.",
                            "Selected and ignored samples are correlated; counts are not significance.",
                            "No automatic ranking update or strategy admission.",
                            "Ledger retains at most 4096 episodes and 30 days of terminal episodes."]}


def step(attention_path, ledger_path, now_ms=None, population_config=None):
    now = int(time.time() * 1000) if now_ms is None else now_ms
    with closing(ledger(ledger_path)) as db, db:
        if population_config:
            population.flush(db, population_config, 'forecast')
        pop = population.Producer(db, population_config, 'forecast', now) if population_config else None
        # Capture unavailable results before retention, including legacy gaps.
        db.execute("INSERT OR IGNORE INTO protocols VALUES (?,?,?)", (PROTOCOL_ID, now, encode(PROTOCOL)))
        activated = db.execute("SELECT activated_ms FROM protocols WHERE id=?", (PROTOCOL_ID,)).fetchone()[0]
        if pop:
            for eid, symbol, registered_ms in db.execute("SELECT id,symbol,created_ms FROM episodes WHERE status='pending'").fetchall():
                identity = pop.version+':forecast:registration:'+eid
                if pop.eligible(symbol, registered_ms) and not db.execute('SELECT 1 FROM population_events WHERE id=?', (identity,)).fetchone():
                    pop.emit('gap:'+eid, 'gap', {'episode_id': eid, 'reason': 'missed_registration',
                                                'registered_ms': registered_ms})
        # Missing target data never becomes a zero return or successful forecast.
        db.execute("UPDATE episodes SET status='unavailable',outcome=? "
                   "WHERE status='pending' AND deadline_ms+? < ?",
                   (encode({"reason": "target_not_observed_within_grace", "recorded_ms": now}), PROTOCOL["grace_ms"], now))
        if pop:
            for saved in db.execute("SELECT * FROM episodes WHERE status!='pending'").fetchall():
                raw = dict(saved)
                raw['prediction'] = json.loads(raw['prediction'])
                raw['outcome'] = json.loads(raw['outcome']) if raw['outcome'] else None
                raw['source_receipts'] = {phase: json.loads(payload) for phase, payload in db.execute(
                    'SELECT phase,payload FROM forecast_sources WHERE episode_id=?', (raw['id'],))}
                pop.update(raw['id'], raw['symbol'], raw['created_ms'], '', raw, terminal=True)
        db.execute("DELETE FROM episodes WHERE status!='pending' AND created_ms < ?",
                   (now-PROTOCOL["retention_ms"],))
        db.execute("DELETE FROM forecast_sources WHERE episode_id NOT IN (SELECT id FROM episodes)")
        status, reason, registered, resolved = "waiting", "no_complete_scan", 0, 0
        skipped = {}
        retries = []
        decisions = []
        current = None
        def skip(reason):
            if current is not None:
                current['registration_reason'] = reason
            skipped[reason] = skipped.get(reason, 0)+1
        source = None
        try:
            health = json.loads(Path(attention_path).with_name("attention_health.json").read_text())
            if (not 0 <= now-health.get("updated_ms", 0) <= PROTOCOL["fresh_ms"]
                    or health.get("last_error") or health.get("errors")
                    or health.get("status") != "ok" or not health.get("worker_alive")):
                status, reason = "degraded", "collector_unhealthy"
            else:
                source = source_snapshot(attention_path)
        except (OSError, ValueError, sqlite3.Error):
            status, reason = "degraded", "source_unavailable"
        if source:
            scan, bars, causes = source
            as_of = scan["as_of_ms"]
            if (scan["timeframe"] != "4h" or not 0 <= now-as_of <= PROTOCOL["fresh_ms"]):
                status, reason = "degraded", "stale_future_or_wrong_timeframe"
            elif as_of < activated:
                reason = "awaiting_post_activation_scan"
            else:
                status, reason = "ok", "forward_scan_processed"
                # Resolve only predictions that were already committed before this scan.
                for row in db.execute("SELECT * FROM episodes WHERE status='pending' AND deadline_ms<=?",
                                      (as_of,)).fetchall():
                    prediction = json.loads(row["prediction"])
                    target = next((b for b in bars.get(row["symbol"], [])
                                   if b["open_ms"] == row["deadline_ms"]-TF and usable(b, as_of)), None)
                    if target is None or as_of <= row["created_ms"]:
                        retries.append({"episode_id": row["id"], "reason": "exact_target_missing_retry",
                                        "target_key": [row["symbol"], row["deadline_ms"]-TF], "attempt_ms": now})
                        if pop and pop.eligible(row['symbol'], row['created_ms']):
                            pop.emit('retry:'+row['id']+':'+str(now), 'missing', retries[-1])
                        continue
                    move = (target["close"]/prediction["baseline_close"]-1)*10_000
                    signed = move * prediction["direction"]
                    supports = ("persistence" if signed > prediction["neutral_bps"] else
                                "reversal" if signed < -prediction["neutral_bps"] else "unresolved")
                    outcome = {"supports": supports, "price_change_bps": move, "observed_ms": as_of,
                               "recorded_ms": now, "scan_id": scan["scan_id"],
                               "target": target, "measurement": "observed_price_change_not_pnl"}
                    db.execute("UPDATE episodes SET status='resolved',outcome=? WHERE id=? AND status='pending'",
                               (encode(outcome), row["id"]))
                    db.execute("INSERT OR IGNORE INTO forecast_sources VALUES (?,'outcome',?)",
                               (row["id"],encode([{'scan_id':scan['scan_id'],'bar':target}])))
                    if pop:
                        raw = dict(db.execute('SELECT * FROM episodes WHERE id=?', (row['id'],)).fetchone())
                        raw['prediction'], raw['outcome'] = json.loads(raw['prediction']), json.loads(raw['outcome'])
                        raw['source_receipts'] = {phase: json.loads(payload) for phase, payload in db.execute(
                            'SELECT phase,payload FROM forecast_sources WHERE episode_id=?', (raw['id'],))}
                        pop.update(raw['id'], raw['symbol'], raw['created_ms'], '', raw, terminal=True)
                    resolved += 1
                available = PROTOCOL["max_episodes"]-db.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
                for row in scan["rows"]:
                    symbol = row["symbol"]
                    current = dict(row, registration_reason='not_registered')
                    decisions.append(current)
                    if not row.get("eligible") or row.get("status") != "ok" or available <= 0:
                        skip("ineligible_or_capacity")
                        continue
                    if db.execute("SELECT 1 FROM episodes WHERE symbol=? AND "
                                  "(status='pending' OR deadline_ms>?) LIMIT 1", (symbol, now)).fetchone():
                        skip("existing_episode")
                        continue
                    window = sorted((b for b in bars.get(symbol, []) if usable(b, as_of)),
                                    key=lambda b: b["open_ms"])[-6:]
                    if len(window) != 6 or window[-1]["open_ms"] != as_of//TF*TF-TF:
                        skip("missing_recent_window")
                        continue
                    if any(b["open_ms"]-a["open_ms"] != TF for a, b in zip(window, window[1:])):
                        skip("window_gap")
                        continue
                    move = window[-1]["close"]/window[0]["close"]-1
                    if move == 0:
                        skip("flat_recent_move")
                        continue
                    target_open = (now//TF+1)*TF
                    # The scheduled invocation has a 20s wall-time limit.
                    # Skip the boundary margin so commit precedes target open.
                    if target_open-now < PROTOCOL["registration_guard_ms"]:
                        skip("registration_boundary_guard")
                        continue
                    prediction = {"protocol_id": PROTOCOL_ID, "scan_id": scan["scan_id"],
                        "observed_ms": as_of, "registered_ms": now, "evidence": row["evidence"],
                        "selected": bool(row.get("selected")), "attention_reason": row["reason"],
                        "recent_move_bps": move*10_000, "direction": 1 if move > 0 else -1,
                        "baseline_close": window[-1]["close"], "input_window": window,
                        "source_code_manifest": scan["code_manifest"],
                        "consumer_code_hash": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                        "decisions": [c.get("decision_id") for c in causes if c["symbol"]==symbol],
                        "hypotheses": PROTOCOL["hypotheses"], "probability": None,
                        "neutral_bps": PROTOCOL["neutral_bps"], "target_open_ms": target_open,
                        "invalidation": "opposite move beyond neutral band supports competitor; neutral is unresolved"}
                    eid = hashlib.sha256(encode([PROTOCOL_ID,symbol,target_open]).encode()).hexdigest()
                    cur = db.execute("INSERT OR IGNORE INTO episodes VALUES (?,?,?,?,?,'pending',?,NULL)",
                               (eid,PROTOCOL_ID,symbol,now,target_open+TF,encode(prediction)))
                    if cur.rowcount:
                        db.execute("INSERT INTO forecast_sources VALUES (?,'registration',?)",
                                   (eid,encode([{'scan_id':scan['scan_id'],'bar':b} for b in window])))
                    current['registration_reason'] = 'registered' if cur.rowcount else 'duplicate_episode'
                    current['episode_id'] = eid
                    if pop and cur.rowcount:
                        pop.registration(eid, symbol, now, {'episode_id': eid, 'symbol': symbol,
                            'registered_ms': now, 'status': 'pending', 'prediction': prediction,
                            'row': row, 'membership': scan.get('membership'),
                            'source_receipt': [{'scan_id': scan['scan_id'], 'bar': b} for b in window]})
                    registered += cur.rowcount
                    available -= cur.rowcount
                if pop:
                    pop.scan(scan, decisions)
                if available <= 0:
                    status, reason = "degraded", "ledger_capacity_reached"
        if pop and not decisions and pop.declaration['start_ms'] <= now < pop.declaration['discovery_cut_ms']:
            pop.emit('gap:invocation:'+str(now), 'gap', {'reason': reason, 'observed_ms': now})
        detail = {"reason": reason, "registered": registered, "resolved": resolved,
                  "skipped": skipped, "outcome_retries": retries, "source_scan_id": source[0]["scan_id"] if source else None}
        db.execute("INSERT INTO diagnostics VALUES (?,?,?)", (now,status,encode(detail)))
        db.execute("DELETE FROM diagnostics WHERE rowid NOT IN (SELECT rowid FROM diagnostics ORDER BY rowid DESC LIMIT 512)")
        if pop:
            detail['population'] = {'enabled': True, 'activated_ms': pop.activated,
                                    'declaration_version': pop.version}
        result = {"status": status, "updated_ms": now, "protocol_id": PROTOCOL_ID,
                "activated_ms": activated, **detail, **summary(db)}
    if population_config:
        with closing(ledger(ledger_path)) as db:
            population.flush(db, population_config, 'forecast')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="consume one completed forward scan")
    args = parser.parse_args()
    from trader.core.config import ROOT, load_config
    cfg = load_config()
    if (not args.once or not (cfg.get("attention_learning") or {}).get("enabled")
            or not (cfg.get("attention") or {}).get("enabled")):
        print(encode({"status": "disabled"})); return 0
    data = ROOT/"data"
    try:
        result = step(data/"attention.db", data/"attention_learning.db", population_config=population.configured(data))
    except Exception as exc:
        result = {"status": "error", "updated_ms": int(time.time()*1000), "error_type": type(exc).__name__,
                  "reason": str(exc)[:100] if isinstance(exc, ValueError) and str(exc).startswith("population_") else "consumer_failed"}
    dest = data/"attention_learning_health.json"
    tmp = dest.with_suffix(".tmp")
    tmp.write_text(encode(result)); tmp.replace(dest)
    print(encode({k:v for k,v in result.items() if k not in ("recent", "limitations")}))
    return 1 if result["status"] == "error" else 0


def read_health(path, now_ms=None):
    """Compact owner view; no database or venue work in the dashboard."""
    now = int(time.time()*1000) if now_ms is None else now_ms
    try:
        data = json.loads(Path(path).read_text())
        result = {k:data[k] for k in ("status", "updated_ms", "reason", "counts",
                  "descriptive_outcomes", "limitations", "error_type") if k in data}
        if not 0 <= now-data.get("updated_ms", 0) <= 600_000:
            result["status"] = "stale"
        result["recent"] = [{"symbol":r["symbol"], "status":r["status"],
            "deadline_ms":r["deadline_ms"], "direction":r["prediction"]["direction"],
            "selected":r["prediction"]["selected"],
            "scan_id":r["prediction"]["scan_id"],
            "supports":(r.get("outcome") or {}).get("supports")}
            for r in data.get("recent", [])[:16]]
        return result
    except (OSError, ValueError, KeyError, TypeError):
        return {"status": "unavailable"}


if __name__ == "__main__":
    raise SystemExit(main())
