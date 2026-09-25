"""Opt-in, bounded investigation consumer. Independent of trading and admission.

--once requires --enable. The CLI enforces a 20-second wall deadline, shorter
than the frozen publication guard. Not scheduled by the watchdog by default.
"""
from __future__ import annotations

import argparse
import fcntl
from contextlib import closing
from dataclasses import asdict, dataclass, field
import hashlib
import json
from pathlib import Path
import signal
import sqlite3
import sys
import threading
import time
from uuid import uuid4
import zlib

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
from trader.cognition import opportunity_context as oc

MAX_ACTIVE, MAX_UPDATES, MAX_CASES = 32, 32, 256
MAX_BYTES, RETENTION_MS, FRESH_MS = 32 * 1024**2, 30 * 86_400_000, 300_000

MAX_CASE_UPDATES = 64

# Contextual investigation-slot allocation. The Attention scan is untouched; only
# which ranked candidates may open NEW investigations uses ledger state.
ALLOCATION_POLICY = "investigation-state-feedback.v1"
ALLOCATION_SCHEMA = "investigation-allocation-decision.v1"
LEGACY = "legacy-top-k.v0"
_CONTEXT_KEYS = ("reason", "selected", "open_episode_id")

# Per-case resource receipts: raw observational telemetry, never case identity.
# No bound or budget is declared here; compliance stays UNKNOWN until a separate,
# prospectively frozen policy exists. UNITS are pinned by RECEIPT_SCHEMA.
RECEIPT_SCHEMA = "investigation-resource-receipt.v1"
ACCOUNTING_SCOPE = "investigation_step_case_local"
# Receipts cover committed case-local work only. Rolled-back attempts leave no
# receipt, so their cost is UNKNOWN, never zero.
COVERAGE = "committed_case_local_investigation_work"
NOT_COVERED = ("total_research_cost", "total_attempted_cost",
               "failed_or_rolled_back_attempt_cost", "end_to_end_step_duration_attributable_to_cases")
UNITS = {
    "wall_ns": "time.monotonic_ns elapsed inside the per-case section",
    "cpu_ns": "time.thread_time_ns elapsed inside the per-case section",
    "evidence_rows_read": "input-bar rows supplied to the case's registration/measurement",
    "ledger_rows_written": "sqlite total_changes delta inside the section (receipt row excluded)",
    "payload_bytes": "UTF-8 bytes of case, update and newly inserted input payloads",
    "llm_calls": "0 when no egress audit event occurred in the section, else UNKNOWN",
    "venue_requests": "0 when no egress audit event occurred in the section, else UNKNOWN",
}
_CLOCKS = {"wall_ns": time.monotonic_ns, "cpu_ns": time.thread_time_ns}
_EGRESS = frozenset({"socket.connect", "socket.getaddrinfo", "socket.gethostbyname",
                     "socket.sendto", "subprocess.Popen", "os.system", "os.posix_spawn",
                     "os.exec", "os.spawn"})
_local = threading.local()
_audit_installed = False


def _audit(event, _args):
    counts = getattr(_local, "egress", None)
    if counts is not None and event in _EGRESS:
        counts[event] = counts.get(event, 0) + 1


def _install_audit():
    global _audit_installed
    if not _audit_installed:
        sys.addaudithook(_audit)
        _audit_installed = True


def _clock(name):
    try:
        value = _CLOCKS[name]()
    except Exception:
        return None
    return value if type(value) is int else None


class _Meter:
    """Measures one bounded section. Raw clock values are never persisted."""

    def __init__(self, db=None, egress=False):
        self.db, self.rows_read, self.payload_bytes = db, 0, 0
        self.egress = {} if egress else None
        if egress:
            _local.egress = self.egress
        self.changes = db.total_changes if db is not None else None
        self.start = {k: _clock(k) for k in _CLOCKS}

    def stop(self):
        """Elapsed per unit, or None with a reason. Never a fabricated 0."""
        end = {k: _clock(k) for k in _CLOCKS}
        if self.egress is not None:
            _local.egress = None
        values, unknown = {}, {}
        for k in _CLOCKS:
            if self.start[k] is None or end[k] is None:
                values[k], unknown[k] = None, "clock_read_failed"
            elif end[k] < self.start[k]:
                values[k], unknown[k] = None, "non_monotonic_reading"
            else:
                values[k] = end[k] - self.start[k]
        if self.db is not None:
            values.update(evidence_rows_read=self.rows_read, payload_bytes=self.payload_bytes,
                          ledger_rows_written=self.db.total_changes - self.changes)
            if sum(self.egress.values()):
                for k in ("llm_calls", "venue_requests"):
                    values[k], unknown[k] = None, "unclassified_egress"
            else:
                values.update(llm_calls=0, venue_requests=0)
        return values, unknown


def _receipt_body(meter, inv, stage, execution_id, work, outcome, now):
    """One measured execution of case-local work.

    `execution_id` is the step invocation: each real execution is its own receipt,
    even when its logical work context (`work`) is identical to an earlier one.
    """
    values, unknown = meter.stop()
    identity = {"schema_version": RECEIPT_SCHEMA, "execution_id": execution_id,
                "case_id": inv.investigation_id, "stage": stage, "work": work}
    wall = values["wall_ns"]
    return dict(identity, receipt_id=digest(identity), episode_id=inv.episode_id, symbol=inv.state.symbol,
                family=inv.measurement.family, protocol_id=inv.measurement.catalog_id,
                source_scan_id=work["source_scan_id"], observed_ms=now, outcome=outcome,
                accounting_scope=ACCOUNTING_SCOPE, coverage=COVERAGE, not_covered=list(NOT_COVERED),
                measurements=values, unknown_units=unknown,
                elapsed_ms=None if wall is None else wall / 1e6,
                measurement_status="UNKNOWN" if unknown else "MEASURED",
                egress_events=dict(sorted(meter.egress.items())),
                resource_bound=None, resource_compliance="UNKNOWN",
                compliance_reason="no_prospectively_frozen_resource_bound")


def _persist_receipt(db, body):
    """Idempotent only for the SAME execution; an existing receipt is never rewritten."""
    return db.execute("INSERT OR IGNORE INTO resource_receipts VALUES (?,?,?,?,?,?)",
                      (body["receipt_id"], body["case_id"], body["stage"], body["execution_id"],
                       body["observed_ms"], I.encode(body))).rowcount == 1


def _receipt(db, meter, inv, stage, execution_id, work, outcome, now):
    """Returns (written, measurements, unknown)."""
    body = _receipt_body(meter, inv, stage, execution_id, work, outcome, now)
    return _persist_receipt(db, body), body["measurements"], bool(body["unknown_units"])


def receipts(path, case_id=None):
    """Read-only receipt history, in append order."""
    with closing(sqlite3.connect(Path(path).resolve().as_uri()+"?mode=ro", uri=True, timeout=.1)) as db:
        sql, args = "SELECT payload FROM resource_receipts", ()
        if case_id is not None:
            sql, args = sql + " WHERE case_id=?", (case_id,)
        return [json.loads(p) for (p,) in db.execute(sql + " ORDER BY rowid", args)]


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
    dataset: object = field(default=None, repr=False, compare=False)

    def state(self, symbol, family=None):
        return I.make_state(self.scan, self.result, symbol, self.bars, self.observed_ms, family)


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
    raw = {"schema": INPUT_SCHEMA, "timeframe": "4h", "decision_times": [scan["as_of_ms"]],
           "candles": candles, "membership": scan["membership"]}
    if "positioning_input" in scan:
        # Replay the captured positioning exactly; never re-query derivs.db.
        # Its rejected records already fail closed per series in the rows.
        raw["positioning"] = scan["positioning_input"]
    if "correlation_input" in scan:
        # Replay the captured close history exactly; never query prices.
        # Its rejected records already fail closed per symbol in the rows.
        if not isinstance(scan["correlation_input"], list) or len(scan["correlation_input"]) > 64:
            raise ValueError("input_bound_exceeded")
        raw["correlation_history"] = scan["correlation_input"]
    ds = load_input(raw)
    per_symbol = ("positioning", "correlation_history")
    if any(r["section"] not in per_symbol for r in ds.rejected):
        raise ValueError("malformed_inputs:" + ",".join(sorted({r["reason"] for r in ds.rejected
                                                                 if r["section"] not in per_symbol})))
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
    return Snapshot(dict(scan, code_manifest=manifest), result, tuple(bars), observed_ms, ds)


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
      CREATE TABLE IF NOT EXISTS allocation_decisions(id TEXT PRIMARY KEY, scan_id TEXT NOT NULL,
        policy_version TEXT NOT NULL, decided_ms INTEGER NOT NULL, payload TEXT NOT NULL,
        UNIQUE(scan_id, policy_version));
      CREATE TABLE IF NOT EXISTS allocation_bindings(declaration_version TEXT PRIMARY KEY,
        policy TEXT NOT NULL, bound_ms INTEGER NOT NULL);
      CREATE TABLE IF NOT EXISTS resource_receipts(id TEXT PRIMARY KEY, case_id TEXT NOT NULL,
        stage TEXT NOT NULL, execution_id TEXT NOT NULL, recorded_ms INTEGER NOT NULL,
        payload TEXT NOT NULL);
      CREATE INDEX IF NOT EXISTS receipt_case ON resource_receipts(case_id);
      CREATE TABLE IF NOT EXISTS opportunity_contexts(id TEXT PRIMARY KEY,
        investigation_id TEXT NOT NULL UNIQUE, symbol TEXT NOT NULL, as_of_ms INTEGER NOT NULL,
        scan_sha256 TEXT NOT NULL, allocation_sha256 TEXT NOT NULL, payload TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS context_evidence(sha256 TEXT PRIMARY KEY, payload BLOB NOT NULL);
    """)
    memory_store.schema(db)
    outcome_store.schema(db)
    return db


class LedgerRefused(ValueError):
    """Ledger evidence cannot support a contextual decision: fail closed."""
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def ledger_state(rows, case_count, updates_used):
    """Exact active-investigation evidence at one decision cut.

    `rows` are the open `cases` rows read inside the deciding transaction. None
    means the evidence was not obtained; it is refused, never read as "none open".
    """
    if rows is None or type(case_count) is not int or type(updates_used) is not int:
        raise LedgerRefused("ledger_evidence_missing")
    active = []
    for r in rows:
        try:
            payload = r["payload"]
            inv = I.investigation_from_dict(json.loads(payload))
            key = (inv.investigation_id, inv.episode_id, inv.state.symbol, inv.registered_ms)
        except (TypeError, ValueError, KeyError, AttributeError) as exc:
            raise LedgerRefused("ledger_evidence_corrupt") from exc
        if key != (r["id"], r["episode_id"], r["symbol"], r["created_ms"]):
            raise LedgerRefused("ledger_evidence_inconsistent")
        active.append({"investigation_id": key[0], "episode_id": key[1], "symbol": key[2],
                       "registered_ms": key[3],
                       "payload_sha256": hashlib.sha256(payload.encode()).hexdigest()})
    active.sort(key=lambda a: a["symbol"])
    if len({a["symbol"] for a in active}) != len(active) or case_count < len(active):
        raise LedgerRefused("ledger_evidence_inconsistent")
    state = {"active": active, "active_count": len(active), "case_count": case_count,
             "updates_used": updates_used}
    return dict(state, sha256=digest(state))


def allocate(snapshot, state, scan_sha256, cut_ms, mode=ALLOCATION_POLICY, refusal=None):
    """Freeze which ranked candidates may open new investigations.

    Reuses Attention's own `open_episodes` rule by re-running the pure evaluator on
    the identical dataset: active symbols are skipped without taking a top-k slot.
    Everything except the context keys must equal the original scan.
    """
    if state is None or "sha256" not in state:
        raise LedgerRefused("ledger_evidence_missing")
    scan, legacy = snapshot.scan, snapshot.result
    open_episodes = {a["symbol"]: a["investigation_id"] for a in state["active"]}
    cfg = CognitionConfig(**scan["config"])
    if mode == ALLOCATION_POLICY:
        if snapshot.dataset is None:
            raise LedgerRefused("allocation_dataset_missing")
        ctx = evaluate(snapshot.dataset, scan["as_of_ms"], cfg, scan["scan_id"], open_episodes)
        strip = lambda rows: {s: {k: v for k, v in r.items() if k not in _CONTEXT_KEYS}
                              for s, r in rows.items()}
        if ctx["ranked"] != legacy["ranked"] or strip(ctx["rows"]) != strip(legacy["rows"]):
            raise LedgerRefused("allocation_scan_divergence")
        selected = list(ctx["selected"])
        skipped = [s for s in ctx["ranked"] if ctx["rows"][s]["reason"] == "open_episode"]
    else:
        selected, skipped = list(legacy["selected"]), []
    body = {"schema_version": ALLOCATION_SCHEMA, "policy_version": ALLOCATION_POLICY,
            "mode": mode, "refusal": refusal,
            "source_scan_id": scan["scan_id"], "source_scan_sha256": scan_sha256,
            "source_as_of_ms": scan["as_of_ms"], "decision_cut_ms": cut_ms,
            "ledger_state": state, "open_episodes": dict(sorted(open_episodes.items())),
            "capacity": {"k": cfg.k,
                         "active_available": max(0, MAX_ACTIVE - state["active_count"]),
                         "retention_available": max(0, MAX_CASES - state["case_count"]),
                         "update_budget_available": max(0, MAX_UPDATES - state["updates_used"])},
            "legacy_selected": list(legacy["selected"]), "active_skipped": skipped,
            "selected": selected}
    return dict(body, decision_id=digest(body))


def _frozen_allocation(db, scan, snapshot, pop, now, updates_used):
    """One allocation per (scan, policy), persisted in the registering transaction.

    A retry reuses the frozen record, so this scan's own registrations can never
    widen its selection. The record commits in the same transaction as its
    registrations, so a stored record proves that execution already happened:
    a reuse is `execution_closed` and registers nothing, even if capacity or
    ledger state has since changed (only a new scan may use freed capacity). Scans already processed before the policy, and declared
    cohorts that already observed legacy allocation, keep legacy selection.
    """
    scan_sha = digest(scan)
    try:
        row = db.execute("SELECT id,payload FROM allocation_decisions WHERE scan_id=? AND policy_version=?",
                         (scan["scan_id"], ALLOCATION_POLICY)).fetchone()
        if row is not None:
            try:
                body = json.loads(row["payload"])
                intact = digest({k: v for k, v in body.items() if k != "decision_id"}) == row["id"] == body["decision_id"]
            except (TypeError, ValueError, KeyError) as exc:
                raise LedgerRefused("allocation_decision_corrupt") from exc
            if not intact:
                raise LedgerRefused("allocation_decision_corrupt")
            if body["source_scan_sha256"] != scan_sha:
                raise LedgerRefused("allocation_decision_scan_conflict")
            return dict(body, reused=True)
        rows = db.execute("SELECT id,episode_id,symbol,created_ms,payload FROM cases "
                          "WHERE terminal_ms IS NULL ORDER BY symbol").fetchall()
        state = ledger_state(rows, db.execute("SELECT COUNT(*) FROM cases").fetchone()[0], updates_used)
        mode, refusal = ALLOCATION_POLICY, None
        if db.execute("SELECT 1 FROM cases WHERE json_extract(payload,'$.state.scan_id')=? LIMIT 1",
                      (scan["scan_id"],)).fetchone():
            mode, refusal = LEGACY, "scan_processed_before_policy"
        elif pop is not None:
            bound = db.execute("SELECT policy FROM allocation_bindings WHERE declaration_version=?",
                               (pop.version,)).fetchone()
            if bound is None:
                prefix = pop.version + ":" + pop.stream + ":"
                prior = db.execute("SELECT 1 FROM population_events WHERE id LIKE ? OR id LIKE ? LIMIT 1",
                                   (prefix + "scan:%", prefix + "registration:%")).fetchone()
                bound = (LEGACY if prior else ALLOCATION_POLICY,)
                db.execute("INSERT INTO allocation_bindings VALUES (?,?,?)", (pop.version, bound[0], now))
            if bound[0] != ALLOCATION_POLICY:
                mode, refusal = LEGACY, "declared_cohort_predates_policy"
    except sqlite3.Error as exc:
        raise LedgerRefused("ledger_evidence_unavailable") from exc
    decision = allocate(snapshot, state, scan_sha, now, mode, refusal)
    db.execute("INSERT INTO allocation_decisions VALUES (?,?,?,?,?)", (decision["decision_id"],
               scan["scan_id"], ALLOCATION_POLICY, now, I.encode(decision)))
    return dict(decision, reused=False)


class ContextConflict(ValueError):
    """A different context or retained evidence is already persisted."""


class ContextTransactionLost(Exception):
    """SQLite discarded the registering transaction during an optional context
    write; the step's own writes are gone and must be redone without it."""


def registration_context(scan, allocation, inv, initial):
    """opportunity-context.v1 for one registration, from evidence already in hand.

    The cut is the case's registration time. Only the persisted Attention scan,
    the frozen allocation decision, the case and its initial update are bound;
    signals, registry selection, instrument and world model stay UNKNOWN.
    """
    return oc.build(as_of_ms=inv.registered_ms, symbol=inv.state.symbol, attention_scan=scan,
                    allocation={k: v for k, v in allocation.items() if k != "reused"},
                    investigation=inv, investigation_update=initial)


def _retain(db, value):
    """Content-addressed, compressed copy of replay evidence whose upstream
    store prunes on its own schedule (Attention scans, allocation decisions)."""
    text = oc.canonical(value)
    sha = hashlib.sha256(text.encode()).hexdigest()
    old = db.execute("SELECT payload FROM context_evidence WHERE sha256=?", (sha,)).fetchone()
    if old is None:
        db.execute("INSERT INTO context_evidence VALUES (?,?)", (sha, zlib.compress(text.encode())))
    else:
        try:
            same = zlib.decompress(old[0]).decode() == text
        except (zlib.error, UnicodeDecodeError, TypeError):
            same = False        # unreadable retained evidence is a conflict, never a crash
        if not same:
            raise ContextConflict("opportunity_context_evidence_conflict")
    return sha


def _retained(db, sha):
    row = db.execute("SELECT payload FROM context_evidence WHERE sha256=?", (sha,)).fetchone()
    if row is None:
        raise LedgerRefused("replay_evidence_missing")
    try:
        text = zlib.decompress(row[0]).decode()
    except (zlib.error, UnicodeDecodeError) as exc:
        raise LedgerRefused("replay_evidence_corrupt") from exc
    if hashlib.sha256(text.encode()).hexdigest() != sha:
        raise LedgerRefused("replay_evidence_corrupt")
    return json.loads(text)


def persist_context(db, investigation_id, ctx, scan, allocation):
    """Append-only, with the scan and allocation needed to replay it. The
    identical context is an idempotent no-op (False); any other context for
    this case, other bytes under this ID, or other retained evidence under the
    same hash is refused."""
    allocation = {k: v for k, v in allocation.items() if k != "reused"}
    scan_sha, alloc_sha = _retain(db, scan), _retain(db, allocation)
    if scan_sha != ctx.to_dict()["attention"].get("scan_sha256"):
        raise ContextConflict("opportunity_context_evidence_conflict")
    key = (ctx.context_id, investigation_id, ctx.canonical_json, scan_sha, alloc_sha)
    rows = db.execute("SELECT id,investigation_id,payload,scan_sha256,allocation_sha256 "
                      "FROM opportunity_contexts WHERE id=? OR investigation_id=?",
                      (ctx.context_id, investigation_id)).fetchall()
    for r in rows:
        if tuple(r) != key:
            raise ContextConflict("opportunity_context_conflict")
    if rows:
        return False
    body = ctx.to_dict()
    db.execute("INSERT INTO opportunity_contexts VALUES (?,?,?,?,?,?,?)",
               (ctx.context_id, investigation_id, body["instrument"]["symbol_key"],
                body["as_of_ms"], scan_sha, alloc_sha, ctx.canonical_json))
    return True


def _record_context(db, scan, allocation, inv, initial, counts):
    """Best effort inside a savepoint: a refusal or recoverable write failure
    rolls back only the context rows and is reported by reason code.

    SQLite may instead discard the whole transaction (e.g. SQLITE_FULL at the
    ledger's page ceiling). Nothing written so far survives that, so it is
    raised as ContextTransactionLost and `step` redoes the pass without contexts.
    """
    db.execute("SAVEPOINT opportunity_context")
    try:
        written = persist_context(db, inv.investigation_id,
                                  registration_context(scan, allocation, inv, initial),
                                  scan, allocation)
    except (oc.OpportunityContextRefused, ContextConflict, sqlite3.Error) as exc:
        if not db.in_transaction:
            raise ContextTransactionLost("opportunity_context_transaction_lost") from exc
        try:
            db.execute("ROLLBACK TO opportunity_context")
            db.execute("RELEASE opportunity_context")
        except sqlite3.Error as lost:
            raise ContextTransactionLost("opportunity_context_transaction_lost") from lost
        reason = getattr(exc, "reason", None) or (str(exc) if isinstance(exc, ContextConflict)
                                                  else "opportunity_context_write_failed")
        counts["refused"][reason] = counts["refused"].get(reason, 0) + 1
        return
    db.execute("RELEASE opportunity_context")
    counts["written" if written else "duplicate"] += 1


def context_for(path, investigation_id):
    """Read-only persisted context for a case, re-verified; None for legacy
    cases registered before contexts were persisted."""
    with closing(sqlite3.connect(Path(path).resolve().as_uri()+"?mode=ro", uri=True, timeout=.1)) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='opportunity_contexts'").fetchone():
            return None
        row = db.execute("SELECT id,payload FROM opportunity_contexts WHERE investigation_id=?",
                         (investigation_id,)).fetchone()
    if row is None:
        return None
    ctx = oc.OpportunityContext.from_json(row[1])
    if ctx.context_id != row[0]:
        raise oc.OpportunityContextRefused(oc.CORRUPT_EVIDENCE, "context row id")
    return ctx


def replay_context(path, investigation_id, attention_path=None):
    """Rebuild a case's saved registration context from persisted evidence and
    return it only if it reproduces the saved canonical payload and ID.

    The case and its initial update come from the ledger; the scan and
    allocation from their retained copies, so upstream pruning does not end
    replay. Upstream copies still present (the allocation row, and the scan in
    `attention_path` when given) must equal the retained ones. Any missing,
    altered or non-reproducing evidence is refused, never substituted.
    """
    with closing(sqlite3.connect(Path(path).resolve().as_uri()+"?mode=ro", uri=True, timeout=.1)) as db:
        db.execute("BEGIN")
        row = db.execute("SELECT id,payload,scan_sha256,allocation_sha256 FROM opportunity_contexts "
                         "WHERE investigation_id=?", (investigation_id,)).fetchone()
        if row is None:
            raise LedgerRefused("replay_context_missing")
        try:
            saved = oc.OpportunityContext.from_json(row[1])
        except oc.OpportunityContextRefused as exc:
            raise LedgerRefused("replay_context_corrupt") from exc
        if saved.context_id != row[0]:
            raise LedgerRefused("replay_context_corrupt")
        update = saved.to_dict()["investigation"].get("latest_update", {})
        case = db.execute("SELECT payload FROM cases WHERE id=?", (investigation_id,)).fetchone()
        initial = db.execute("SELECT payload FROM updates WHERE id=? AND case_id=?",
                             (update.get("event_id"), investigation_id)).fetchone()
        if case is None or initial is None:
            raise LedgerRefused("replay_evidence_missing")
        scan, allocation = _retained(db, row[2]), _retained(db, row[3])
        upstream = db.execute("SELECT payload FROM allocation_decisions WHERE id=?",
                              (allocation.get("decision_id"),)).fetchone()
    if upstream is not None and json.loads(upstream[0]) != allocation:
        raise LedgerRefused("replay_evidence_conflict")
    if attention_path is not None:
        with closing(sqlite3.connect(Path(attention_path).resolve().as_uri()+"?mode=ro", uri=True,
                                     timeout=.1)) as db:
            live = db.execute("SELECT payload FROM scans WHERE scan_id=?", (scan.get("scan_id"),)).fetchone()
        if live is not None and json.loads(live[0]) != scan:
            raise LedgerRefused("replay_evidence_conflict")
    try:
        rebuilt = registration_context(scan, allocation, I.investigation_from_dict(json.loads(case[0])),
                                       I.update_from_dict(json.loads(initial[0])))
    except (oc.OpportunityContextRefused, TypeError, KeyError, ValueError, AttributeError) as exc:
        raise LedgerRefused("replay_evidence_refused") from exc
    if (rebuilt.context_id, rebuilt.canonical_json) != (saved.context_id, saved.canonical_json):
        raise LedgerRefused("replay_mismatch")
    return rebuilt


def _save_inputs(db, iid, bars, wanted):
    """Returns payload bytes newly inserted into `inputs`."""
    size = 0
    for b in bars:
        if b.version_id in wanted:
            payload = I.encode(asdict(b))
            old = db.execute("SELECT payload FROM inputs WHERE id=?", (b.version_id,)).fetchone()
            if old and old[0] != payload:
                raise ValueError("conflicting_retained_version")
            if db.execute("INSERT OR IGNORE INTO inputs VALUES (?,?)", (b.version_id, payload)).rowcount == 1:
                size += len(payload.encode())
            db.execute("INSERT OR IGNORE INTO case_inputs VALUES (?,?)", (iid, b.version_id))
    return size


def _append(db, update):
    """Returns the update payload bytes."""
    memory_store.append_reasoning(db, update)
    payload = I.encode(asdict(update))
    db.execute("INSERT INTO updates VALUES (?,?,?,?)", (update.event_id, update.investigation_id,
               update.observed_ms, payload))
    if update.evidence.status != "unresolved":
        db.execute("UPDATE cases SET terminal_ms=? WHERE id=?", (update.observed_ms, update.investigation_id))
    return len(payload.encode())


def step(source_path, dest_path, now_ms=None, population_config=None):
    """One transaction. CLI enforces the publication/runtime bound in production.

    Opportunity-context persistence is optional: if its write makes SQLite
    discard the transaction, the whole pass is redone once, from scratch, with
    context persistence off, so registration never depends on it.
    """
    try:
        return _step(source_path, dest_path, now_ms, population_config, contexts_enabled=True)
    except ContextTransactionLost:
        return _step(source_path, dest_path, now_ms, population_config, contexts_enabled=False)


def _step(source_path, dest_path, now_ms, population_config, contexts_enabled):
    _install_audit()
    pass_meter = _Meter()
    started = time.monotonic()
    now = int(time.time() * 1000) if now_ms is None else now_ms
    detail = {"updated_ms": now, "status": "waiting", "reason": "no_complete_scan",
              "registered": 0, "updated": 0, "skipped": {}, "source_scan_id": None}
    decisions = []
    current = None
    charged = {"written": 0, "duplicates_ignored": 0, "unknown": 0, "wall_ns": 0, "cpu_ns": 0}
    # One id per real step() invocation; wall-clock time is never identity.
    execution_id = uuid4().hex
    def charge(meter, inv, stage, outcome, previous_id, resulting_id):
        work = {"source_scan_id": detail["source_scan_id"], "previous_update_id": previous_id,
                "resulting_update_id": resulting_id}
        written, values, unknown = _receipt(db, meter, inv, stage, execution_id, work, outcome, now)
        if not written:
            # The SAME execution persisted twice: already counted once.
            charged["duplicates_ignored"] += 1
            return
        charged["written"] += 1
        charged["unknown"] += unknown
        for k in _CLOCKS:
            charged[k] = None if values[k] is None or charged[k] is None else charged[k] + values[k]
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
        db.execute("INSERT OR IGNORE INTO protocols VALUES (?,?,?)",
                   (I.POSITIONING_CATALOG_ID, now, I.encode(I.POSITIONING_CATALOG)))
        positioning_activated = db.execute("SELECT activated_ms FROM protocols WHERE id=?",
                                           (I.POSITIONING_CATALOG_ID,)).fetchone()[0]
        db.execute("INSERT OR IGNORE INTO protocols VALUES (?,?,?)",
                   (I.CORRELATION_CATALOG_ID, now, I.encode(I.CORRELATION_CATALOG)))
        correlation_activated = db.execute("SELECT activated_ms FROM protocols WHERE id=?",
                                           (I.CORRELATION_CATALOG_ID,)).fetchone()[0]
        db.execute("DELETE FROM cases WHERE terminal_ms IS NOT NULL AND terminal_ms<?", (now-RETENTION_MS,))
        db.execute("DELETE FROM updates WHERE case_id NOT IN (SELECT id FROM cases)")
        db.execute("DELETE FROM case_inputs WHERE case_id NOT IN (SELECT id FROM cases)")
        db.execute("DELETE FROM resource_receipts WHERE case_id NOT IN (SELECT id FROM cases)")
        db.execute("DELETE FROM opportunity_contexts WHERE investigation_id NOT IN (SELECT id FROM cases)")
        db.execute("DELETE FROM context_evidence WHERE sha256 NOT IN (SELECT scan_sha256 FROM opportunity_contexts "
                   "UNION SELECT allocation_sha256 FROM opportunity_contexts)")
        db.execute("DELETE FROM inputs WHERE id NOT IN (SELECT input_id FROM case_inputs)")
        db.execute("DELETE FROM allocation_decisions WHERE decided_ms<?", (now-RETENTION_MS,))
        memory_store.retain(db)
        detail["memory"] = memory_store.ingest(db, now)
        outcome_store.retain(db, now)
        snapshot = None
        try:
            source, detail['collector_evidence'] = H.bound_snapshot(source_path,now_ms=now_ms,fresh_ms=FRESH_MS)
            if (pop and source[0].get('scope',{}).get('kind')=='declared_population'
                    and source[0]['scope'].get('declaration_version')!=pop.version):
                raise H.Refused('declared_binding_mismatch')
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
        detail["typed_outcomes"] = outcome_store.ingest(db, Path(dest_path).parent, import_now)
        detail["counterfactuals"] = outcome_store.resolve_counterfactuals(db, snapshot, now)
        active = db.execute("SELECT * FROM cases WHERE terminal_ms IS NULL ORDER BY created_ms,id").fetchall()
        for row in active:
            if detail["updated"] >= MAX_UPDATES:
                skip("update_budget_exhausted"); break
            # Case-local section. Cases passed over without assessment get no
            # receipt; their small cost stays in the unattributed residual.
            meter = _Meter(db, egress=True)
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
            meter.rows_read = len(retained) + len(incoming)
            evidence = I.measure(inv, (*retained, *incoming), snapshot.scan["as_of_ms"] if snapshot else now, now)
            update = I.advance(inv, evidence, previous)
            prior = previous.event_id if previous else None
            if update:
                count = db.execute("SELECT COUNT(*) FROM updates WHERE case_id=?", (inv.investigation_id,)).fetchone()[0]
                if count >= MAX_CASE_UPDATES-1 and update.evidence.status == "unresolved":
                    skip("case_update_capacity")
                    charge(meter, inv, "assessment", "case_update_capacity", prior, None); continue
                meter.payload_bytes += _save_inputs(db, inv.investigation_id, incoming, {v for _, _, v in evidence.target_versions})
                meter.payload_bytes += _append(db, update)
                if pop:
                    pop.update(inv.investigation_id, inv.state.symbol, inv.registered_ms, update.event_id,
                               memory_store.source_archive(db, inv.investigation_id),
                               terminal=update.evidence.status != 'unresolved')
                detail["updated"] += 1
                charge(meter, inv, "update", "update:" + update.evidence.status, prior, update.event_id)
            else:
                charge(meter, inv, "assessment", "no_state_change", prior, None)
        if snapshot:
            contexts = detail["opportunity_context"] = {"written": 0, "duplicate": 0, "refused": {}}
            if not contexts_enabled:
                contexts["disabled"] = "opportunity_context_transaction_lost"
            decisions = [dict(row, registration_reason='not_selected_for_investigation')
                         for row in snapshot.scan['rows']]
            by_symbol = {row['symbol']: row for row in decisions}
            try:
                allocation = _frozen_allocation(db, source[0], snapshot, pop, now, detail["updated"])
            except LedgerRefused as exc:
                allocation = None
                detail.update(status="degraded", reason=exc.reason)
                for row in decisions:
                    row['registration_reason'] = exc.reason
            if allocation:
                detail["allocation"] = {k: allocation[k] for k in (
                    "decision_id", "mode", "refusal", "reused", "legacy_selected", "active_skipped", "selected")}
                detail["allocation"]["execution"] = "execution_closed" if allocation["reused"] else "executed"
                for symbol in allocation["active_skipped"]:
                    by_symbol[symbol]['registration_reason'] = 'active_investigation'
                if allocation["reused"]:
                    # The committed decision's registration side effect is closed.
                    for symbol in allocation["selected"]:
                        by_symbol[symbol]['registration_reason'] = 'allocation_execution_closed'
            for symbol in (allocation["selected"] if allocation and not allocation["reused"] else ()):
                current = by_symbol[symbol]
                # Case-local only once a case exists; refused candidates have no
                # case to charge and stay in the unattributed residual.
                meter = _Meter(db, egress=True)
                if db.execute("SELECT 1 FROM cases WHERE symbol=? AND terminal_ms IS NULL", (symbol,)).fetchone():
                    skip("existing_episode"); continue
                if detail["updated"] + detail["registered"] >= MAX_UPDATES:
                    skip("update_budget_exhausted"); continue
                if db.execute("SELECT COUNT(*) FROM cases WHERE terminal_ms IS NULL").fetchone()[0] >= MAX_ACTIVE:
                    skip("active_capacity"); continue
                if db.execute("SELECT COUNT(*) FROM cases").fetchone()[0] >= MAX_CASES:
                    skip("retention_capacity"); continue
                family = snapshot.result["rows"][symbol]["dominant"]
                if family not in (*I.FAMILIES, *I.REGISTRATION_FAMILIES):
                    skip("no_investigation_family"); continue
                activated_for = {I.POSITIONING_FAMILY: positioning_activated,
                                 I.CORRELATION_FAMILY: correlation_activated}
                if family in activated_for and snapshot.scan["as_of_ms"] < activated_for[family]:
                    skip("awaiting_post_activation_scan"); continue
                try:
                    inv = I.open_investigation(snapshot.state(symbol, family), family, snapshot.bars, now)
                except ValueError as exc:
                    skip(str(exc)); continue
                if db.execute("SELECT 1 FROM cases WHERE episode_id=?", (inv.episode_id,)).fetchone():
                    skip("existing_closed_episode"); continue
                case_payload = I.encode(asdict(inv))
                db.execute("INSERT INTO cases VALUES (?,?,?,?,NULL,?)", (inv.investigation_id, inv.episode_id, symbol, now, case_payload))
                meter.rows_read = len(snapshot.bars)
                meter.payload_bytes = len(case_payload.encode())
                memory_store.register(db, inv)
                meter.payload_bytes += _save_inputs(db, inv.investigation_id, snapshot.bars, set(inv.state.input_versions))
                if pop:
                    pop.registration(inv.investigation_id, symbol, now,
                        dict(memory_store.source_archive(db, inv.investigation_id),
                             memory=memory_store.owner_context(db, inv.investigation_id),
                             collector_evidence=detail.get("collector_evidence")))
                initial = I.advance(inv, I.measure(inv, (), now, now))
                meter.payload_bytes += _append(db, initial)
                current.update(registration_reason='registered', episode_id=inv.investigation_id)
                detail["registered"] += 1
                charge(meter, inv, "registration", "registered", None, initial.event_id)
                # After the receipt, so case-local measurements are unchanged.
                if contexts_enabled:
                    _record_context(db, source[0], allocation, inv, initial, contexts)
            if pop:
                pop.scan(dict(snapshot.scan,collector_evidence=detail.get("collector_evidence")), decisions)
        if pop and snapshot is None and pop.declaration['start_ms'] <= now < pop.declaration['discovery_cut_ms']:
            pop.emit('gap:invocation:'+str(now), 'gap', {'reason': detail['reason'], 'observed_ms': now})
        if (detail["memory"]["refused"] or detail["memory"]["unassessable"]["refused"]
                or detail["memory"]["assessed"]["refused"]
                or detail["typed_outcomes"]["refused"]
                or detail["typed_outcomes"]["retry"]):
            detail.update(status="degraded", reason="memory_source_refused")
        if any(k in detail["skipped"] for k in ("update_budget_exhausted", "active_capacity", "retention_capacity", "case_update_capacity")):
            detail.update(status="degraded", reason="capacity_exhausted")
        # Abort whole transaction before publication if the guard can no longer hold.
        if (time.monotonic()-started) * 1000 >= I.MAX_RUNTIME_MS:
            raise TimeoutError("worker_deadline")
        # Pass residual: everything outside case-local sections, reported once
        # and never apportioned to candidates.
        _local.egress = None
        pass_values, pass_unknown = pass_meter.stop()
        residual = {"schema_version": RECEIPT_SCHEMA, "accounting_scope": ACCOUNTING_SCOPE,
                    "execution_id": execution_id, "coverage": COVERAGE, "not_covered": list(NOT_COVERED),
                    "uncommitted_attempt_cost": "UNKNOWN",
                    "written": charged["written"], "duplicates_ignored": charged["duplicates_ignored"],
                    "unknown_receipts": charged["unknown"], "resource_compliance": "UNKNOWN",
                    "unknown_units": {}}
        for k, name in (("wall_ns", "wall"), ("cpu_ns", "cpu")):
            residual[f"pass_{name}_ns"] = pass_values[k]
            residual[f"attributed_{name}_ns"] = charged[k]
            if pass_values[k] is None or charged[k] is None:
                residual[f"unattributed_{name}_ns"] = None
                residual["unknown_units"][k] = pass_unknown.get(k, "case_measurement_unknown")
            else:
                residual[f"unattributed_{name}_ns"] = pass_values[k] - charged[k]
        detail["resource_receipts"] = residual
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
    from .declared import source_path as configured_source_path
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
        result = step(configured_source_path(data), data / "investigation.db", population_config=population.configured(data))
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
