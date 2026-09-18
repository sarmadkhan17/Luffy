"""Opt-in, bounded investigation consumer. Independent of trading and admission.

--once requires --enable. The CLI enforces a 20-second wall deadline, shorter
than the frozen publication guard. Not scheduled by the watchdog by default.
"""
from __future__ import annotations

import argparse
import fcntl
from contextlib import closing
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import signal
import sqlite3
import time

from trader.cognition.attention import CognitionConfig, evaluate
from trader.cognition.contracts import INPUT_SCHEMA, is_timestamp, load_input
from trader.cognition import investigation as I
from .attention import SCHEMA as ATTENTION_SCHEMA, digest
from . import population, collector_health as H
from . import memory as memory_store
from trader.cognition import memory as memory_core
from . import outcomes as outcome_store
from trader.cognition import outcomes as outcome_core
from trader.cognition import forecast_protocol

MAX_ACTIVE, MAX_UPDATES, MAX_CASES = 32, 32, 256
MAX_BYTES, RETENTION_MS, FRESH_MS = 32 * 1024**2, 30 * 86_400_000, 300_000

MAX_CASE_UPDATES = 64


def source_snapshot(path):
    """Bounded coherent read; no mutation or migration of the attention store."""
    started = time.monotonic()
    with closing(sqlite3.connect(Path(path).resolve().as_uri()+"?mode=ro", uri=True, timeout=.1)) as db:
        db.set_progress_handler(lambda: int(time.monotonic()-started > 1), 1000)
        db.execute("BEGIN")
        row = db.execute("SELECT scan_id,length(payload) FROM scans WHERE causes_complete=1 AND payload IS NOT NULL ORDER BY as_of_ms DESC,scan_id DESC LIMIT 1").fetchone()
        if row is None:
            return None
        sid, size = row
        if size > 2*1024**2:
            raise ValueError("source_payload_bound")
        scan = json.loads(db.execute("SELECT payload FROM scans WHERE scan_id=?", (sid,)).fetchone()[0])
        count, max_size = db.execute("SELECT COUNT(*),MAX(length(v.payload)) FROM versions v JOIN scan_versions s ON v.id=s.version_id WHERE s.scan_id=?", (sid,)).fetchone()
        if count > 4096 or (max_size or 0) > 4096:
            raise ValueError("input_bound_exceeded")
        bars = {}
        for vid, available, payload in db.execute("SELECT v.id,v.first_seen_ms,v.payload FROM versions v JOIN scan_versions s ON v.id=s.version_id WHERE s.scan_id=?", (sid,)):
            b = json.loads(payload)
            b.update(version_id=vid, available_ms=available)
            bars.setdefault(b["symbol"], []).append(b)
        return scan, bars, []


@dataclass(frozen=True)
class Snapshot:
    scan: dict
    result: dict
    bars: tuple[I.InputBar, ...]
    observed_ms: int

    def state(self, symbol):
        return I.make_state(self.scan, self.result, symbol, self.bars, self.observed_ms)


def adapt(source, observed_ms):
    """Validate exact joined versions before deriving state. Does not alter source."""
    scan, by_symbol, _causes = source
    if (scan.get("schema_version") != ATTENTION_SCHEMA or scan.get("timeframe") != "4h"
            or not is_timestamp(observed_ms) or not is_timestamp(scan.get("as_of_ms"))
            or scan["as_of_ms"] > observed_ms):
        raise ValueError("unsupported_or_future_scan")
    if len(scan.get("membership", [])) > 64 or sum(map(len, by_symbol.values())) > 4096:
        raise ValueError("input_bound_exceeded")
    cfg = CognitionConfig(**scan["config"])
    if cfg.window != I.N or cfg.short != 5 or cfg.horizon != I.H or digest(scan["config"]) != scan["config_id"]:
        raise ValueError("unsupported_config")
    refs = {r["version_id"]: r for r in scan["input_versions"]}
    if len(refs) != len(scan["input_versions"]):
        raise ValueError("duplicate_input_reference")
    candles, versions = [], {}
    for symbol, entries in sorted(by_symbol.items()):
        for b in entries:
            vid = b["version_id"]
            if vid not in refs or b["symbol"] != symbol or vid in versions:
                raise ValueError("invalid_version_join")
            ref = refs[vid]
            if any(ref[k] != b[k] for k in ("symbol", "open_ms")) or ref["first_seen_ms"] != b["available_ms"]:
                raise ValueError("invalid_version_join")
            if not is_timestamp(b["available_ms"]) or b["available_ms"] > scan["as_of_ms"]:
                raise ValueError("unavailable_input")
            key = (symbol, b["open_ms"])
            if key in versions.values():
                raise ValueError("ambiguous_scan_revision")
            versions[vid] = key
            candles.append(dict(b))
    if set(versions) != set(refs):
        raise ValueError("missing_version_join")
    for m in scan["membership"]:
        if not is_timestamp(m.get("available_ms")) or m["available_ms"] > scan["as_of_ms"]:
            raise ValueError("unavailable_membership")
    ds = load_input({"schema": INPUT_SCHEMA, "timeframe": "4h", "decision_times": [scan["as_of_ms"]],
                     "candles": candles, "membership": scan["membership"]})
    if ds.rejected:
        raise ValueError("malformed_inputs:" + ",".join(sorted({r["reason"] for r in ds.rejected})))
    bars = []
    for vid, (symbol, opened) in sorted(versions.items()):
        c = ds.bar_asof(symbol, opened, scan["as_of_ms"])
        if c is None:
            raise ValueError("future_or_unavailable_bar")
        bars.append(I.InputBar(vid, c))
    result = evaluate(ds, scan["as_of_ms"], cfg, scan["scan_id"], {})
    if (result["universe"] != scan["rows"] or result["market"] != scan["market"]
            or [asdict(o) for o in result["observations"]] != scan["observations"]):
        raise ValueError("source_evaluator_mismatch")
    manifest = dict(scan["code_manifest"])
    for name, path in (("cognition/investigation.py", Path(I.__file__)),
                       ("observability/investigation.py", Path(__file__)),
                       ("cognition/memory.py", Path(memory_core.__file__)),
                       ("observability/memory.py", Path(memory_store.__file__)),
                       ("cognition/outcomes.py", Path(outcome_core.__file__)),
                       ("cognition/forecast_protocol.py", Path(forecast_protocol.__file__)),
                       ("observability/outcomes.py", Path(outcome_store.__file__))):
        manifest[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return Snapshot(dict(scan, code_manifest=manifest), result, tuple(bars), observed_ms)


def ledger(path):
    db = sqlite3.connect(path, timeout=.1)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA auto_vacuum=FULL")
    page_size = db.execute("PRAGMA page_size").fetchone()[0]
    db.execute(f"PRAGMA max_page_count={MAX_BYTES // page_size}")
    db.executescript("""
      CREATE TABLE IF NOT EXISTS protocols(id TEXT PRIMARY KEY, activated_ms INTEGER, payload TEXT);
      CREATE TABLE IF NOT EXISTS cases(id TEXT PRIMARY KEY, episode_id TEXT UNIQUE, symbol TEXT,
        created_ms INTEGER, terminal_ms INTEGER, payload TEXT NOT NULL);
      CREATE UNIQUE INDEX IF NOT EXISTS one_open_symbol ON cases(symbol) WHERE terminal_ms IS NULL;
      CREATE TABLE IF NOT EXISTS updates(id TEXT PRIMARY KEY, case_id TEXT, observed_ms INTEGER, payload TEXT NOT NULL);
      CREATE INDEX IF NOT EXISTS update_case ON updates(case_id, observed_ms);
      CREATE TABLE IF NOT EXISTS inputs(id TEXT PRIMARY KEY, payload TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS case_inputs(case_id TEXT, input_id TEXT, PRIMARY KEY(case_id,input_id));
      CREATE TABLE IF NOT EXISTS diagnostics(ts_ms INTEGER, detail TEXT NOT NULL);
    """)
    memory_store.schema(db)
    outcome_store.schema(db)
    return db


def _save_inputs(db, iid, bars, wanted):
    for b in bars:
        if b.version_id in wanted:
            payload = I.encode(asdict(b))
            old = db.execute("SELECT payload FROM inputs WHERE id=?", (b.version_id,)).fetchone()
            if old and old[0] != payload:
                raise ValueError("conflicting_retained_version")
            db.execute("INSERT OR IGNORE INTO inputs VALUES (?,?)", (b.version_id, payload))
            db.execute("INSERT OR IGNORE INTO case_inputs VALUES (?,?)", (iid, b.version_id))


def _append(db, update):
    memory_store.append_reasoning(db, update)
    db.execute("INSERT INTO updates VALUES (?,?,?,?)", (update.event_id, update.investigation_id,
               update.observed_ms, I.encode(asdict(update))))
    if update.evidence.status != "unresolved":
        db.execute("UPDATE cases SET terminal_ms=? WHERE id=?", (update.observed_ms, update.investigation_id))


def step(source_path, dest_path, now_ms=None, population_config=None):
    """One transaction. CLI enforces the publication/runtime bound in production."""
    started = time.monotonic()
    now = int(time.time() * 1000) if now_ms is None else now_ms
    detail = {"updated_ms": now, "status": "waiting", "reason": "no_complete_scan",
              "registered": 0, "updated": 0, "skipped": {}, "source_scan_id": None}
    decisions = []
    current = None
    def skip(reason):
        if current is not None:
            current['registration_reason'] = reason
        detail["skipped"][reason] = detail["skipped"].get(reason, 0) + 1
    with closing(ledger(dest_path)) as db, db:
        if population_config:
            population.flush(db, population_config, 'investigation')
        pop = population.Producer(db, population_config, 'investigation', now) if population_config else None
        if pop:
            for saved in db.execute('SELECT id,symbol,created_ms,terminal_ms FROM cases').fetchall():
                if pop.eligible(saved['symbol'], saved['created_ms']):
                    raw = memory_store.source_archive(db, saved['id'])
                    pop.update(saved['id'], saved['symbol'], saved['created_ms'],
                               population.D.digest(raw), raw, terminal=saved['terminal_ms'] is not None)
        db.execute("INSERT OR IGNORE INTO protocols VALUES (?,?,?)", (I.CATALOG_ID, now, I.encode(I.CATALOG)))
        activated = db.execute("SELECT activated_ms FROM protocols WHERE id=?", (I.CATALOG_ID,)).fetchone()[0]
        db.execute("DELETE FROM cases WHERE terminal_ms IS NOT NULL AND terminal_ms<?", (now-RETENTION_MS,))
        db.execute("DELETE FROM updates WHERE case_id NOT IN (SELECT id FROM cases)")
        db.execute("DELETE FROM case_inputs WHERE case_id NOT IN (SELECT id FROM cases)")
        db.execute("DELETE FROM inputs WHERE id NOT IN (SELECT input_id FROM case_inputs)")
        memory_store.retain(db)
        detail["memory"] = memory_store.ingest(db, now)
        outcome_store.retain(db, now)
        snapshot = None
        try:
            source, detail['collector_evidence'] = H.bound_snapshot(source_path,now_ms=now_ms,fresh_ms=FRESH_MS)
            H.record(db,pop,detail['collector_evidence'],now)
            if source:
                scan = source[0]
                detail["source_scan_id"] = scan["scan_id"]
                if scan["as_of_ms"] > now:
                    raise ValueError("stale_or_future_scan")
                if scan["as_of_ms"] < activated:
                    detail["reason"] = "awaiting_post_activation_scan"
                else:
                    snapshot = adapt(source, now)
                    detail.update(status="ok", reason="forward_scan_processed")
        except H.Refused as exc:
            detail.update(status='degraded',reason=exc.reason,error_type='ValueError',
                          collector_evidence=dict(exc.evidence,accepted=False,reason=exc.reason))
            H.record(db,pop,detail['collector_evidence'],now)
        except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as exc:
            detail.update(status="degraded", reason="source_refused", error_type=type(exc).__name__)
            # Reason codes only; no arbitrary source text or credentials.
            if isinstance(exc, ValueError):
                detail["reason"] = str(exc)[:100]
        import_now = int(time.time()*1000) if now_ms is None else now
        detail["typed_outcomes"] = outcome_store.ingest(db, Path(source_path).parent, import_now)
        detail["counterfactuals"] = outcome_store.resolve_counterfactuals(db, snapshot, now)
        active = db.execute("SELECT * FROM cases WHERE terminal_ms IS NULL ORDER BY created_ms,id").fetchall()
        for row in active:
            if detail["updated"] >= MAX_UPDATES:
                skip("update_budget_exhausted"); break
            inv = I.investigation_from_dict(json.loads(row["payload"]))
            last = db.execute("SELECT payload FROM updates WHERE case_id=? ORDER BY observed_ms DESC,rowid DESC LIMIT 1", (row["id"],)).fetchone()
            previous = I.update_from_dict(json.loads(last[0])) if last else None
            # Expiry is observed even during a source outage, never backdated.
            if snapshot is None and now <= inv.measurement.expires_ms:
                continue
            if snapshot and snapshot.scan["as_of_ms"] < inv.registered_ms:
                continue
            retained = [I.InputBar(d["version_id"], I.Candle(**d["candle"])) for (payload,) in db.execute(
                "SELECT payload FROM inputs JOIN case_inputs ON inputs.id=case_inputs.input_id WHERE case_id=?", (row["id"],))
                for d in [json.loads(payload)]]
            incoming = snapshot.bars if snapshot else ()
            evidence = I.measure(inv, (*retained, *incoming), snapshot.scan["as_of_ms"] if snapshot else now, now)
            update = I.advance(inv, evidence, previous)
            if update:
                count = db.execute("SELECT COUNT(*) FROM updates WHERE case_id=?", (inv.investigation_id,)).fetchone()[0]
                if count >= MAX_CASE_UPDATES-1 and update.evidence.status == "unresolved":
                    skip("case_update_capacity"); continue
                _save_inputs(db, inv.investigation_id, incoming, {v for _, _, v in evidence.target_versions})
                _append(db, update)
                if pop:
                    pop.update(inv.investigation_id, inv.state.symbol, inv.registered_ms, update.event_id,
                               memory_store.source_archive(db, inv.investigation_id),
                               terminal=update.evidence.status != 'unresolved')
                detail["updated"] += 1
        if snapshot:
            decisions = [dict(row, registration_reason='not_selected_for_investigation')
                         for row in snapshot.scan['rows']]
            by_symbol = {row['symbol']: row for row in decisions}
            for symbol in snapshot.result["selected"]:
                current = by_symbol[symbol]
                if db.execute("SELECT 1 FROM cases WHERE symbol=? AND terminal_ms IS NULL", (symbol,)).fetchone():
                    skip("existing_episode"); continue
                if detail["updated"] + detail["registered"] >= MAX_UPDATES:
                    skip("update_budget_exhausted"); continue
                if db.execute("SELECT COUNT(*) FROM cases WHERE terminal_ms IS NULL").fetchone()[0] >= MAX_ACTIVE:
                    skip("active_capacity"); continue
                if db.execute("SELECT COUNT(*) FROM cases").fetchone()[0] >= MAX_CASES:
                    skip("retention_capacity"); continue
                try:
                    inv = I.open_investigation(snapshot.state(symbol), snapshot.result["rows"][symbol]["dominant"], snapshot.bars, now)
                except ValueError as exc:
                    skip(str(exc)); continue
                if db.execute("SELECT 1 FROM cases WHERE episode_id=?", (inv.episode_id,)).fetchone():
                    skip("existing_closed_episode"); continue
                db.execute("INSERT INTO cases VALUES (?,?,?,?,NULL,?)", (inv.investigation_id, inv.episode_id, symbol, now, I.encode(asdict(inv))))
                memory_store.register(db, inv)
                _save_inputs(db, inv.investigation_id, snapshot.bars, set(inv.state.input_versions))
                if pop:
                    pop.registration(inv.investigation_id, symbol, now,
                        dict(memory_store.source_archive(db, inv.investigation_id),
                             memory=memory_store.owner_context(db, inv.investigation_id),
                             collector_evidence=detail.get("collector_evidence")))
                _append(db, I.advance(inv, I.measure(inv, (), now, now)))
                current.update(registration_reason='registered', episode_id=inv.investigation_id)
                detail["registered"] += 1
            if pop:
                pop.scan(dict(snapshot.scan,collector_evidence=detail.get("collector_evidence")), decisions)
        if pop and snapshot is None and pop.declaration['start_ms'] <= now < pop.declaration['discovery_cut_ms']:
            pop.emit('gap:invocation:'+str(now), 'gap', {'reason': detail['reason'], 'observed_ms': now})
        if (detail["memory"]["refused"] or detail["typed_outcomes"]["refused"]
                or detail["typed_outcomes"]["retry"]):
            detail.update(status="degraded", reason="memory_source_refused")
        if any(k in detail["skipped"] for k in ("update_budget_exhausted", "active_capacity", "retention_capacity", "case_update_capacity")):
            detail.update(status="degraded", reason="capacity_exhausted")
        # Abort whole transaction before publication if the guard can no longer hold.
        if (time.monotonic()-started) * 1000 >= I.MAX_RUNTIME_MS:
            raise TimeoutError("worker_deadline")
        if pop:
            detail['population'] = {'enabled': True, 'activated_ms': pop.activated,
                                    'declaration_version': pop.version}
        detail.update(active=db.execute("SELECT COUNT(*) FROM cases WHERE terminal_ms IS NULL").fetchone()[0],
                      cases=db.execute("SELECT COUNT(*) FROM cases").fetchone()[0],
                      elapsed_ms=round((time.monotonic()-started)*1000, 3), catalog_id=I.CATALOG_ID,
                      activated_ms=activated)
        db.execute("INSERT INTO diagnostics VALUES (?,?)", (now, I.encode(detail)))
        db.execute("DELETE FROM diagnostics WHERE rowid NOT IN (SELECT rowid FROM diagnostics ORDER BY rowid DESC LIMIT 512)")
    if population_config:
        with closing(ledger(dest_path)) as db:
            population.flush(db, population_config, 'investigation')
    return detail


def dossiers(path, limit=16):
    """Read-only owner payload. A renderer must use text nodes, never HTML inputs."""
    with closing(sqlite3.connect(Path(path).resolve().as_uri()+"?mode=ro", uri=True, timeout=.1)) as db:
        db.execute("BEGIN")
        result = []
        for iid, payload in db.execute("SELECT id,payload FROM cases ORDER BY created_ms DESC,id LIMIT ?", (max(0, min(limit, 32)),)):
            updates = [json.loads(r[0]) for r in db.execute("SELECT payload FROM updates WHERE case_id=? ORDER BY observed_ms,rowid", (iid,))]
            result.append({"investigation": json.loads(payload), "updates": updates,
                           "memory": memory_store.owner_context(db, iid)})
        return result


def owner_view(path, now_ms=None):
    now = int(time.time()*1000) if now_ms is None else now_ms
    path = Path(path)
    try:
        health = json.loads(path.with_name("investigation_health.json").read_text())
        if not 0 <= now-health.get("updated_ms", 0) <= 600_000:
            health = dict(health, status="stale")
    except (OSError, ValueError, TypeError):
        health = {"status": "unavailable"}
    try:
        cases = dossiers(path)
    except (OSError, sqlite3.Error, ValueError, TypeError):
        return {"status": "unavailable", "health": health, "cases": []}
    return {"status": health["status"], "health": health, "cases": cases}


def publish_health(path, result):
    """Preserve last success and cumulative invocation failures across restarts."""
    path = Path(path)
    try:
        if path.stat().st_size > 16384:
            raise ValueError("oversized_health")
        prior = json.loads(path.read_text())
        if not isinstance(prior, dict):
            raise ValueError("invalid_health")
    except (OSError, ValueError):
        prior = {}
    result = dict(result)
    failed = result.get("status") in ("error", "degraded")
    result["failed_invocations"] = prior.get("failed_invocations", 0) + int(failed)
    result["deferred_updates"] = prior.get("deferred_updates", 0) + sum(
        value for key, value in result.get("skipped", {}).items()
        if key in ("update_budget_exhausted", "active_capacity", "retention_capacity", "case_update_capacity"))
    success = result.get("status") == "ok"
    result["last_successful_scan_id"] = result.get("source_scan_id") if success else prior.get("last_successful_scan_id")
    result["last_successful_ms"] = result.get("updated_ms") if success else prior.get("last_successful_ms")
    result["limits"] = {"active": MAX_ACTIVE, "cases": MAX_CASES, "updates_per_invocation": MAX_UPDATES,
                        "updates_per_case": MAX_CASE_UPDATES, "db_bytes": MAX_BYTES,
                        "terminal_retention_ms": RETENTION_MS, "runtime_ms": I.MAX_RUNTIME_MS,
                        "diagnostic_rows": 512, "alternatives": 3, "next_actions": 1}
    tmp = path.with_suffix(".tmp")
    tmp.write_text(I.encode(result)); tmp.replace(path)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--enable", action="store_true")
    args = parser.parse_args()
    if not args.once or not args.enable:
        print(I.encode({"status": "disabled"})); return 0
    from trader.core.config import ROOT
    data = ROOT / "data"
    lock = (data / "investigation.lock").open("a")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        print(I.encode({"status": "busy", "reason": "worker_already_running"})); return 0
    def timeout(*_args):
        raise TimeoutError("worker_deadline")
    old_handler = signal.signal(signal.SIGALRM, timeout)
    signal.setitimer(signal.ITIMER_REAL, I.MAX_RUNTIME_MS / 1000)
    try:
        result = step(data / "attention.db", data / "investigation.db", population_config=population.configured(data))
    except Exception as exc:
        result = {"status": "error", "error_type": type(exc).__name__, "updated_ms": int(time.time()*1000),
                  "reason": str(exc)[:100] if isinstance(exc, ValueError) and str(exc).startswith("population_") else "consumer_failed"}
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old_handler)
    result["consumer_code_hash"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    dest = data / "investigation_health.json"
    try:
        result = publish_health(dest, result)
    except (OSError, ValueError, TypeError) as exc:
        result = {"status": "error", "reason": "health_write_failed", "error_type": type(exc).__name__}
    finally:
        lock.close()
    print(I.encode(result))
    return int(result["status"] == "error")


if __name__ == "__main__":
    raise SystemExit(main())
