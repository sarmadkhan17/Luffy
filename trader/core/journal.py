"""Decision Journal — the memory of the Trading OS.

Every cycle, vote, decision, trade and outcome lands here. This is what
makes learning possible: rejected setups are tracked with the same rigor
as executed trades, so we can later answer "which agent was right?".

SQLite + WAL; one writer (the OS process), many readers (dashboard, brain).
"""
from __future__ import annotations

import json
import os
import time
import traceback
import logging
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Optional

from .types import Decision, Snapshot, now_utc
from . import reason_codes as _rc

SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS cycles (
    id TEXT PRIMARY KEY,
    ts TEXT NOT NULL,
    symbol TEXT NOT NULL,
    price REAL,
    regime TEXT,
    adx REAL,
    btc_trend TEXT,
    market_type TEXT,
    mode TEXT
);
CREATE INDEX IF NOT EXISTS idx_cycles_ts ON cycles(ts);

CREATE TABLE IF NOT EXISTS votes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cycle_id TEXT NOT NULL REFERENCES cycles(id),
    ts TEXT NOT NULL,
    symbol TEXT NOT NULL,
    agent TEXT NOT NULL,
    side TEXT NOT NULL,
    conviction REAL NOT NULL,
    confidence REAL NOT NULL,
    rationale TEXT,
    meta TEXT
);
CREATE INDEX IF NOT EXISTS idx_votes_agent ON votes(agent, ts);
CREATE INDEX IF NOT EXISTS idx_votes_symbol ON votes(symbol, ts);
-- Outcome/owner accuracy joins use the exact cycle and symbol together.
-- A symbol/time lookup rescanned millions of unrelated votes per outcome.
CREATE INDEX IF NOT EXISTS idx_votes_cycle_symbol ON votes(cycle_id, symbol);

CREATE TABLE IF NOT EXISTS decisions (
    id TEXT PRIMARY KEY,
    cycle_id TEXT NOT NULL REFERENCES cycles(id),
    ts TEXT NOT NULL,
    symbol TEXT NOT NULL,
    action TEXT NOT NULL,
    score REAL NOT NULL,
    threshold REAL NOT NULL,
    confidence REAL NOT NULL,
    executed INTEGER NOT NULL DEFAULT 0,
    skip_reason TEXT,
    size_usdt REAL DEFAULT 0,
    entry_price REAL,
    strategy_ids TEXT
);
CREATE INDEX IF NOT EXISTS idx_decisions_symbol ON decisions(symbol, ts);
CREATE INDEX IF NOT EXISTS idx_decisions_exec ON decisions(executed);
-- Portfolio reads the bounded latest decisions across all symbols. Descending
-- keys retain ascending rowid order for equal timestamps, as the table sorter.
CREATE INDEX IF NOT EXISTS idx_decisions_current_ts ON decisions(ts DESC);
-- exact latest-match reads (owner surfaces): partial indexes, newest by ts
CREATE INDEX IF NOT EXISTS idx_decisions_executed_ts ON decisions(ts) WHERE executed=1;
CREATE INDEX IF NOT EXISTS idx_decisions_directional_ts ON decisions(ts)
    WHERE action!='HOLD';
CREATE INDEX IF NOT EXISTS idx_decisions_skipped_ts ON decisions(ts)
    WHERE action!='HOLD' AND executed=0;
CREATE INDEX IF NOT EXISTS idx_decisions_rejected_ts ON decisions(ts)
    WHERE action!='HOLD' AND executed=0 AND skip_reason IS NOT NULL AND skip_reason!='';

-- Outcome resolution: what actually happened after a non-HOLD decision.
CREATE TABLE IF NOT EXISTS outcomes (
    decision_id TEXT PRIMARY KEY REFERENCES decisions(id),
    cycle_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    ts TEXT NOT NULL,                -- decision time
    action TEXT NOT NULL,
    entry_price REAL NOT NULL,
    resolved_at TEXT,
    fwd_ret_1h REAL,
    fwd_ret_4h REAL,
    fwd_ret_24h REAL,
    correct_1h INTEGER,              -- 1/0/NULL: did direction win at horizon?
    correct_4h INTEGER,
    correct_24h INTEGER
);

CREATE TABLE IF NOT EXISTS trades (
    id TEXT PRIMARY KEY,
    decision_id TEXT,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    amount REAL NOT NULL,
    entry_price REAL NOT NULL,
    exit_price REAL,
    notional_usdt REAL,
    leverage INTEGER DEFAULT 1,
    stop_loss REAL, take_profit REAL, sl_order_id TEXT DEFAULT '',
    strategy_id TEXT, strategy_name TEXT,
    market_type TEXT, exec_mode TEXT,
    opened_at TEXT NOT NULL,
    closed_at TEXT,
    realized_pnl REAL DEFAULT 0,
    close_reason TEXT,
    status TEXT NOT NULL DEFAULT 'open'   -- open | closed
);
CREATE INDEX IF NOT EXISTS idx_trades_strategy ON trades(strategy_id);
CREATE INDEX IF NOT EXISTS idx_trades_status ON trades(status);
-- keyset trade history (owner frontend): newest first by immutable identity
CREATE INDEX IF NOT EXISTS idx_trades_opened ON trades(opened_at, id);
CREATE INDEX IF NOT EXISTS idx_trades_status_opened ON trades(status, opened_at, id);

CREATE TABLE IF NOT EXISTS equity (
    ts TEXT PRIMARY KEY,
    equity REAL NOT NULL,
    balance REAL NOT NULL,
    open_positions INTEGER
);
-- Where each equity row's value came from (kernel account observation):
-- written in the same transaction as the row, keyed by the row's ts. The
-- row's ts is its WRITE time; provenance.observed_at is the venue read that
-- produced the value (a reused fallback keeps the original read time). A
-- row without provenance has unknown origin (legacy / other writers).
CREATE TABLE IF NOT EXISTS equity_provenance (
    ts TEXT PRIMARY KEY,
    provenance TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS brain_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    kind TEXT NOT NULL,              -- review / promote / demote / retire / propose / mutate / note
    subject TEXT,                    -- strategy id or scope
    detail TEXT                      -- free text / JSON
);

CREATE TABLE IF NOT EXISTS control_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    event TEXT NOT NULL,             -- state_change / panic / mode_switch / budget_stop
    from_state TEXT,
    to_state TEXT,
    actor TEXT NOT NULL,             -- operator / luffy / watchdog / risk_engine
    detail TEXT
);

CREATE TABLE IF NOT EXISTS trade_accounting_bookings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS trade_accounting_bookings_trade ON trade_accounting_bookings(trade_id,id);

CREATE TABLE IF NOT EXISTS execution_accounting (
    id TEXT PRIMARY KEY,
    payload TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS state_kv (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL              -- control_state, market_type, proving_trades, ...
);

CREATE TABLE IF NOT EXISTS strategies (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    params TEXT NOT NULL,            -- JSON genes
    state TEXT NOT NULL,
    description TEXT,
    origin TEXT,
    hypothesis TEXT,
    invalidation TEXT,
    regime_filter TEXT,              -- JSON list
    markets TEXT,                    -- JSON list
    generation INTEGER DEFAULT 0,
    parent_id TEXT DEFAULT '',
    created_at TEXT,
    stats_json TEXT DEFAULT '{}'
);
-- Attention supplemental selection: one row per pure-selector Selection,
-- written in the same transaction as the rotating cursor it implies. The
-- canonical JSON is authoritative; the other columns are its projection.
CREATE TABLE IF NOT EXISTS attention_selections (
    selection_id TEXT PRIMARY KEY,   -- sha256(canonical_json)
    rule_version TEXT NOT NULL,
    cycle_as_of_ms INTEGER NOT NULL,
    outcome TEXT NOT NULL,
    snapshot_id TEXT NOT NULL,
    snapshot_as_of_ms INTEGER NOT NULL,
    registry_age_ms INTEGER NOT NULL,
    max_snapshot_age_ms INTEGER NOT NULL,
    selected_id TEXT,
    selected_symbol TEXT,
    cursor_before TEXT,
    cursor_after TEXT,
    canonical_json TEXT NOT NULL,
    recorded_at_ms INTEGER NOT NULL
);

-- durable rotating cursors; an absent row is "no cursor"
CREATE TABLE IF NOT EXISTS attention_cursor (
    name TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- what a later supplemental fetch did; never touches attention_cursor
CREATE TABLE IF NOT EXISTS attention_fetch_outcomes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    selection_id TEXT NOT NULL REFERENCES attention_selections(selection_id),
    status TEXT NOT NULL,
    reason TEXT NOT NULL,
    started_ms INTEGER,
    ended_ms INTEGER,
    recorded_at_ms INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS attention_fetch_outcomes_sel ON attention_fetch_outcomes(selection_id,id);

-- RETIRED (RP-04): the strategy-decay and strategy-health-unreadable
-- research chains (question/plan/evidence/result/run/bank, next questions,
-- registrations) were removed. No writer or reader remains; these empty
-- tables are kept only so existing journal files and backups stay
-- schema-compatible and open without migration. Do not add consumers.
-- research-question.v1 (retired RP-04): one row per
-- registered question. The canonical JSON is authoritative; the other
-- columns are its projection. Append-only: never updated or deleted here.
CREATE TABLE IF NOT EXISTS research_questions (
    question_id TEXT PRIMARY KEY,    -- sha256 of the question identity
    schema TEXT NOT NULL,
    question_kind TEXT NOT NULL,
    trigger TEXT NOT NULL,
    scope_kind TEXT NOT NULL,
    scope_id TEXT NOT NULL,
    source_kind TEXT NOT NULL,
    source_event_id INTEGER NOT NULL,
    canonical_json TEXT NOT NULL,
    recorded_at_ms INTEGER NOT NULL,
    UNIQUE(question_kind, source_kind, source_event_id)
);

-- research-plan.v1 (retired RP-04): one evidence-routing plan
-- per verified research question. The canonical JSON is authoritative; the
-- other columns are its projection. Append-only: never updated or deleted.
CREATE TABLE IF NOT EXISTS research_plans (
    plan_id TEXT PRIMARY KEY,        -- sha256 of the plan identity
    schema TEXT NOT NULL,
    plan_kind TEXT NOT NULL,
    planner_id TEXT NOT NULL,
    question_id TEXT NOT NULL,
    question_sha256 TEXT NOT NULL,   -- sha256 of the question canonical JSON
    scope_kind TEXT NOT NULL,
    scope_id TEXT NOT NULL,
    canonical_json TEXT NOT NULL,
    recorded_at_ms INTEGER NOT NULL,
    UNIQUE(schema, plan_kind, question_id)
);

-- research-evidence.v1 (retired RP-04): frozen evidence
-- collected for one verified research plan. The canonical JSON is
-- authoritative; the other columns are its projection. Append-only: never
-- updated or deleted.
CREATE TABLE IF NOT EXISTS research_evidence (
    evidence_id TEXT PRIMARY KEY,    -- sha256 of the evidence identity
    schema TEXT NOT NULL,
    evidence_kind TEXT NOT NULL,
    collector_id TEXT NOT NULL,
    plan_id TEXT NOT NULL,
    plan_sha256 TEXT NOT NULL,       -- sha256 of the plan canonical JSON
    question_id TEXT NOT NULL,
    scope_kind TEXT NOT NULL,
    scope_id TEXT NOT NULL,
    canonical_sha256 TEXT NOT NULL,  -- sha256 of canonical_json
    canonical_json TEXT NOT NULL,
    recorded_at_ms INTEGER NOT NULL,
    UNIQUE(schema, evidence_kind, plan_id)
);

-- research-result.v1 (retired RP-04): the structural result
-- closing one verified research-evidence record. The canonical JSON is
-- authoritative; the other columns are its projection. Append-only: never
-- updated or deleted.
CREATE TABLE IF NOT EXISTS research_results (
    result_id TEXT PRIMARY KEY,      -- sha256 of the result identity
    schema TEXT NOT NULL,
    result_kind TEXT NOT NULL,
    resolver_id TEXT NOT NULL,
    evidence_id TEXT NOT NULL UNIQUE,
    evidence_sha256 TEXT NOT NULL,   -- sha256 of the evidence canonical JSON
    plan_id TEXT NOT NULL,
    scope_kind TEXT NOT NULL,
    scope_id TEXT NOT NULL,
    status TEXT NOT NULL,
    canonical_sha256 TEXT NOT NULL,  -- sha256 of canonical_json
    canonical_json TEXT NOT NULL,
    recorded_at_ms INTEGER NOT NULL
);

-- research-run.v1 (retired RP-04): one audit receipt per
-- explicit offline run of the strategy-decay chain. The canonical JSON is
-- authoritative; telemetry_json (digest telemetry_sha256) is MEASURED
-- telemetry kept outside the receipt hash and duplicate comparison. Append-only: never updated or
-- deleted.
CREATE TABLE IF NOT EXISTS research_runs (
    run_id TEXT PRIMARY KEY,         -- sha256 of the explicit run inputs
    schema TEXT NOT NULL,
    run_kind TEXT NOT NULL,
    runner_id TEXT NOT NULL,
    run_key TEXT NOT NULL,
    run_recorded_at_ms INTEGER NOT NULL,
    canonical_sha256 TEXT NOT NULL,  -- sha256 of canonical_json
    canonical_json TEXT NOT NULL,
    telemetry_sha256 TEXT NOT NULL,  -- sha256 of telemetry_json
    telemetry_json TEXT NOT NULL,
    recorded_at_ms INTEGER NOT NULL
);

-- research-bank-object.v1 (retired RP-04): one immutable
-- Research Bank object per verified result a completed research run
-- reached. The canonical JSON is authoritative; the other columns are its
-- projection. Append-only: never updated or deleted.
CREATE TABLE IF NOT EXISTS research_bank_objects (
    bank_object_id TEXT PRIMARY KEY, -- sha256 of the bank object identity
    schema TEXT NOT NULL,
    bank_kind TEXT NOT NULL,
    builder_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    result_id TEXT NOT NULL,
    evidence_id TEXT NOT NULL,
    plan_id TEXT NOT NULL,
    question_id TEXT NOT NULL,
    scope_kind TEXT NOT NULL,
    scope_id TEXT NOT NULL,
    canonical_sha256 TEXT NOT NULL,  -- sha256 of canonical_json
    canonical_json TEXT NOT NULL,
    recorded_at_ms INTEGER NOT NULL,
    UNIQUE(run_id, result_id)
);

-- research-next-question.v1 (retired RP-04): one
-- immutable context_only record per structural gap of a verified
-- Research Bank object's result hypothesis entry. The canonical JSON is
-- authoritative; the other columns are its projection. Append-only: never
-- updated or deleted.
CREATE TABLE IF NOT EXISTS research_next_questions (
    next_question_id TEXT PRIMARY KEY, -- sha256 of the record identity
    schema TEXT NOT NULL,
    question_kind TEXT NOT NULL,
    generator_id TEXT NOT NULL,
    bank_object_id TEXT NOT NULL,
    result_id TEXT NOT NULL,
    hypothesis TEXT NOT NULL,
    scope_kind TEXT NOT NULL,
    scope_id TEXT NOT NULL,
    canonical_sha256 TEXT NOT NULL,    -- sha256 of canonical_json
    canonical_json TEXT NOT NULL,
    recorded_at_ms INTEGER NOT NULL,
    UNIQUE(bank_object_id, hypothesis)
);

-- strategy-health-unreadable-question.v1
-- (retired RP-04): one context_only question
-- per unreadable strategy-health observation. Deliberately separate from
-- research_questions so the strategy-decay question/plan chain never reads
-- it. A first insert writes a research-registration.v1 receipt (record_type
-- = this schema) in the same transaction; rows filed before receipts
-- existed have none and are never backfilled. The canonical JSON is
-- authoritative; the other columns are its projection. Append-only: never
-- updated or deleted.
CREATE TABLE IF NOT EXISTS research_unreadable_questions (
    question_id TEXT PRIMARY KEY,    -- sha256 of the question identity
    schema TEXT NOT NULL,
    question_kind TEXT NOT NULL,
    scope_kind TEXT NOT NULL,
    scope_id TEXT NOT NULL,
    source_kind TEXT NOT NULL,
    source_event_id INTEGER NOT NULL,
    canonical_sha256 TEXT NOT NULL,  -- sha256 of canonical_json
    canonical_json TEXT NOT NULL,
    recorded_at_ms INTEGER NOT NULL,
    UNIQUE(source_kind, source_event_id)
);

-- strategy-health-unreadable-plan.v1
-- (retired RP-04): one context_only evidence-routing
-- plan per verified research_unreadable_questions row. Deliberately separate
-- from research_plans so the strategy-decay plan/evidence chain never reads
-- it; no registration receipt. The canonical JSON is authoritative; the
-- other columns are its projection. Append-only: never updated or deleted.
CREATE TABLE IF NOT EXISTS research_unreadable_plans (
    plan_id TEXT PRIMARY KEY,        -- sha256 of the plan identity
    schema TEXT NOT NULL,
    plan_kind TEXT NOT NULL,
    planner_id TEXT NOT NULL,
    question_id TEXT NOT NULL,
    question_sha256 TEXT NOT NULL,   -- sha256 of the question canonical JSON
    scope_kind TEXT NOT NULL,
    scope_id TEXT NOT NULL,
    source_event_id INTEGER NOT NULL,
    canonical_sha256 TEXT NOT NULL,  -- sha256 of canonical_json
    canonical_json TEXT NOT NULL,
    recorded_at_ms INTEGER NOT NULL,
    UNIQUE(schema, plan_kind, question_id)
);

-- strategy-health-unreadable-evidence.v1
-- (retired RP-04): one context_only frozen
-- evidence record per verified research_unreadable_plans row. Deliberately
-- separate from research_evidence so the strategy-decay evidence/result/run
-- chain never reads it; no registration receipt. The canonical JSON is
-- authoritative; the other columns are its projection. Append-only: never
-- updated or deleted.
CREATE TABLE IF NOT EXISTS research_unreadable_evidence (
    evidence_id TEXT PRIMARY KEY,    -- sha256 of the evidence identity
    schema TEXT NOT NULL,
    evidence_kind TEXT NOT NULL,
    collector_id TEXT NOT NULL,
    plan_id TEXT NOT NULL,
    plan_sha256 TEXT NOT NULL,       -- sha256 of the plan canonical JSON
    question_id TEXT NOT NULL,
    scope_kind TEXT NOT NULL,
    scope_id TEXT NOT NULL,
    source_event_id INTEGER NOT NULL,
    canonical_sha256 TEXT NOT NULL,  -- sha256 of canonical_json
    canonical_json TEXT NOT NULL,
    recorded_at_ms INTEGER NOT NULL,
    UNIQUE(schema, evidence_kind, plan_id)
);

-- strategy-health-unreadable-result.v1
-- (retired RP-04): one context_only structural
-- result per verified research_unreadable_evidence row, always INCONCLUSIVE
-- / NOT_ASSESSED. Deliberately separate from research_results so the
-- strategy-decay result/run/bank chain never reads it; no registration
-- receipt. The canonical JSON is authoritative; the other columns are its
-- projection. Append-only: never updated or deleted.
CREATE TABLE IF NOT EXISTS research_unreadable_results (
    result_id TEXT PRIMARY KEY,      -- sha256 of the result identity
    schema TEXT NOT NULL,
    result_kind TEXT NOT NULL,
    resolver_id TEXT NOT NULL,
    evidence_id TEXT NOT NULL,
    evidence_sha256 TEXT NOT NULL,   -- sha256 of the evidence canonical JSON
    plan_id TEXT NOT NULL,
    question_id TEXT NOT NULL,
    scope_kind TEXT NOT NULL,
    scope_id TEXT NOT NULL,
    status TEXT NOT NULL,
    canonical_sha256 TEXT NOT NULL,  -- sha256 of canonical_json
    canonical_json TEXT NOT NULL,
    recorded_at_ms INTEGER NOT NULL,
    UNIQUE(schema, result_kind, evidence_id)
);

-- strategy-health-unreadable-run.v1 (retired RP-04):
-- one audit receipt per explicit offline run of the unreadable
-- strategy-health chain. Deliberately separate from research_runs so the
-- strategy-decay runner, bank, recall and cost ledger never read it. The
-- canonical JSON is authoritative; telemetry_json (digest telemetry_sha256)
-- is MEASURED telemetry kept outside the receipt hash and duplicate
-- comparison. Append-only: never updated or deleted.
CREATE TABLE IF NOT EXISTS research_unreadable_runs (
    run_id TEXT PRIMARY KEY,         -- sha256 of the explicit run inputs
    schema TEXT NOT NULL,
    run_kind TEXT NOT NULL,
    runner_id TEXT NOT NULL,
    run_key TEXT NOT NULL,
    run_recorded_at_ms INTEGER NOT NULL,
    canonical_sha256 TEXT NOT NULL,  -- sha256 of canonical_json
    canonical_json TEXT NOT NULL,
    telemetry_sha256 TEXT NOT NULL,  -- sha256 of telemetry_json
    telemetry_json TEXT NOT NULL,
    recorded_at_ms INTEGER NOT NULL
);

-- strategy-health-unreadable-bank-object.v1
-- (retired RP-04): one immutable context_only
-- Research Bank object per verified result a completed unreadable run
-- reached. Deliberately separate from research_bank_objects so the
-- strategy-decay bank, view, recall and next questions never read it. A
-- first insert writes a research-registration.v1 receipt (record_type =
-- this schema) in the same transaction; rows filed before receipts existed
-- have none and are never backfilled. The canonical JSON is authoritative;
-- the other columns are its projection. Append-only: never updated or
-- deleted.
CREATE TABLE IF NOT EXISTS research_unreadable_bank_objects (
    bank_object_id TEXT PRIMARY KEY, -- sha256 of the bank object identity
    schema TEXT NOT NULL,
    bank_kind TEXT NOT NULL,
    builder_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    result_id TEXT NOT NULL,
    evidence_id TEXT NOT NULL,
    plan_id TEXT NOT NULL,
    question_id TEXT NOT NULL,
    scope_kind TEXT NOT NULL,
    scope_id TEXT NOT NULL,
    canonical_sha256 TEXT NOT NULL,  -- sha256 of canonical_json
    canonical_json TEXT NOT NULL,
    recorded_at_ms INTEGER NOT NULL,
    UNIQUE(run_id, result_id)
);

-- research-registration.v1: the first-registration receipt of one
-- research-question.v1 / research-bank-object.v1 /
-- strategy-health-unreadable-question.v1 /
-- strategy-health-unreadable-bank-object.v1 row, written in the same
-- transaction as that row's first insert. envelope_json is the canonical
-- {schema, record_type, record_id, canonical_sha256, recorded_at_ms}; the
-- other columns are its projection. Immutable: never updated or deleted.
CREATE TABLE IF NOT EXISTS research_registrations (
    record_type TEXT NOT NULL,       -- the record's schema
    record_id TEXT NOT NULL,
    canonical_sha256 TEXT NOT NULL,  -- sha256 of the record canonical JSON
    recorded_at_ms INTEGER NOT NULL, -- first registration
    envelope_sha256 TEXT NOT NULL UNIQUE,  -- sha256 of envelope_json
    envelope_json TEXT NOT NULL,
    PRIMARY KEY (record_type, record_id)
);
CREATE TRIGGER IF NOT EXISTS research_registrations_no_update
BEFORE UPDATE ON research_registrations
BEGIN SELECT RAISE(ABORT, 'research_registrations is immutable'); END;
CREATE TRIGGER IF NOT EXISTS research_registrations_no_delete
BEFORE DELETE ON research_registrations
BEGIN SELECT RAISE(ABORT, 'research_registrations is immutable'); END;
-- INSERT OR REPLACE deletes the conflicting row without firing the DELETE
-- trigger (recursive_triggers is off): refuse any insert that collides with
-- an existing receipt on either unique key
CREATE TRIGGER IF NOT EXISTS research_registrations_no_replace
BEFORE INSERT ON research_registrations
WHEN EXISTS (SELECT 1 FROM research_registrations
             WHERE (record_type=NEW.record_type AND record_id=NEW.record_id)
                OR envelope_sha256=NEW.envelope_sha256)
BEGIN SELECT RAISE(ABORT, 'research_registrations is immutable'); END;
"""


def _identity_json(p) -> str | None:
    """The position's entry identity as stored JSON; None when absent. An
    unserializable identity is recorded as such rather than failing the
    booking of a position the venue already holds."""
    identity = getattr(p, "entry_identity", None)
    if not identity:
        return None
    try:
        return json.dumps(identity, sort_keys=True, allow_nan=False)
    except (TypeError, ValueError):
        return json.dumps({"status": "UNKNOWN", "reason": "identity_not_serializable"})


SLOW_TX_S = 1.0           # a traced transaction is reported once any phase total reaches this
_TRACE_LABELS = 6         # caller labels kept per report
_TRACE_ACTIVE = threading.local()


class _TracedConnection(sqlite3.Connection):
    """Times only explicit BEGIN statements (e.g. Risk._durable_hold's
    BEGIN IMMEDIATE): that is where another process's SQLite writer is waited
    on. Never inspects or records statement arguments."""
    _begin_armed = False       # set by the active trace; reset each transaction
    _begin_s = None            # None = no explicit BEGIN observed (never a fabricated 0)

    def execute(self, sql, *args):
        if not self._begin_armed or not isinstance(sql, str) or sql.lstrip()[:5].upper() != 'BEGIN':
            return super().execute(sql, *args)
        started = time.monotonic()
        try:
            return super().execute(sql, *args)
        finally:
            self._begin_s = (self._begin_s or 0.0) + time.monotonic() - started


class _TxTrace:
    """Phase timing for one Journal._tx, started BEFORE the Python writer mutex.

    mutex  = wait for this Journal's in-process lock
    begin  = explicit BEGIN time (SQLite writer ownership); None when the
             statement was implicit and is not separately observable
    body   = caller code between mutex acquisition and its end, minus begin
    commit / cleanup = COMMIT, and rollback-or-discard plus failure publication
    """

    def __init__(self):
        self.started = self.last = time.monotonic()
        self.phase = {}
        self.conn = None

    def mark(self, name):
        if name in self.phase:
            return
        now = time.monotonic()
        self.phase[name] = now - self.last
        self.last = now

    def emit(self):
        try:
            if isinstance(self.conn, _TracedConnection):
                self.conn._begin_armed = False
            total = time.monotonic() - self.started
            if total < SLOW_TX_S or getattr(_TRACE_ACTIVE, 'on', False):
                return
            _TRACE_ACTIVE.on = True       # a logging handler must not re-enter this report
            try:
                begin = getattr(self.conn, '_begin_s', None)
                body = self.phase.get('body')
                if begin is not None and body is not None:
                    body = max(0.0, body - begin)
                labels = [f'{Path(f.filename).name}:{f.lineno}:{f.name}'
                          for f in traceback.extract_stack(limit=24)
                          if Path(f.filename).name not in ('journal.py', 'contextlib.py')][-_TRACE_LABELS:]
                logging.getLogger(__name__).warning(
                    'slow journal transaction %s', json.dumps({
                        'total_s': round(total, 3),
                        'mutex_wait_s': _r(self.phase.get('mutex')),
                        'begin_s': _r(begin),
                        'body_s': _r(body),
                        'commit_s': _r(self.phase.get('commit')),
                        'cleanup_s': _r(self.phase.get('cleanup')),
                        'pid': os.getpid(), 'thread': threading.current_thread().name[:48],
                        'connection': None if self.conn is None else f'{id(self.conn) & 0xffffff:06x}',
                        'callers': labels}))
            finally:
                _TRACE_ACTIVE.on = False
        except Exception:
            pass  # Diagnostic failure cannot alter commit/rollback.


def _r(value):
    return None if value is None else round(value, 3)


class Journal:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._write_lock = threading.Lock()
        with self._conn() as c:
            if c.execute("SELECT 1 FROM sqlite_master WHERE name='journal_representation_v1'").fetchone():
                representation = c.execute("SELECT status FROM journal_representation_v1 WHERE version='detail.v1'").fetchone()
                if representation and representation[0] != 'VERIFIED':
                    from .journal_evidence import EvidenceError
                    raise EvidenceError('journal_candidate_not_verified')
            c.executescript(SCHEMA)
            from . import journal_evidence
            c.executescript(journal_evidence.SCHEMA)
            from ..engine.entry_authority import SCHEMA as entry_request_schema
            c.executescript(entry_request_schema)
            for stmt in (
                "ALTER TABLE decisions ADD COLUMN strategy_selection_json TEXT DEFAULT NULL",
                "ALTER TABLE decisions ADD COLUMN instrument_binding_json TEXT DEFAULT NULL",
                "ALTER TABLE decisions ADD COLUMN reason_codes TEXT DEFAULT NULL",
                "ALTER TABLE decisions ADD COLUMN reason_codes_version TEXT DEFAULT NULL",
                "ALTER TABLE decisions ADD COLUMN scan_id TEXT DEFAULT NULL",
                "ALTER TABLE decisions ADD COLUMN signals_json TEXT DEFAULT '[]'",
                "ALTER TABLE trades ADD COLUMN tp1_done INTEGER DEFAULT 0",
                "ALTER TABLE strategies ADD COLUMN retire_reason TEXT DEFAULT ''",
                # when the strategy last changed state — the retirement clock
                # reads this, not created_at
                "ALTER TABLE strategies ADD COLUMN state_changed_at TEXT DEFAULT ''",
                # meta-labeling: the secondary model's live judgment
                "ALTER TABLE decisions ADD COLUMN meta_p REAL DEFAULT NULL",
                # the stop distance the trade was SIZED on. Every R multiple
                # was computed against the CURRENT stop, so the first trail
                # ratchet shrank the denominator and R exploded — a live
                # trail logged R=83 on a +2% position. Everything gated on R
                # (the time stop, the flip exit, TP1) then read nonsense.
                "ALTER TABLE trades ADD COLUMN initial_risk REAL DEFAULT NULL",
                # the research search's StrategySpec population, alongside
                # the legacy genome rows (see upsert_spec/list_specs) — added
                # here too so a fresh database has it from the first query,
                # not only after the first spec write.
                "ALTER TABLE strategies ADD COLUMN spec_json TEXT",
                # intrabar maximum favourable/adverse excursion in R, and the
                # provenance that says which frame measured it and how well
                # the candles covered the hold. Observation only — nothing
                # reads these to make a decision. NULL means "not measured",
                # never "no excursion"; see engine/excursion.py.
                "ALTER TABLE trades ADD COLUMN mfe_r REAL DEFAULT NULL",
                "ALTER TABLE trades ADD COLUMN mae_r REAL DEFAULT NULL",
                "ALTER TABLE trades ADD COLUMN excursion_json TEXT DEFAULT NULL",
                # the exact strategy version an entry was taken on, frozen at
                # insert (engine/trade_provenance.py). NULL means UNKNOWN —
                # the trigger below refuses to fill it in later.
                "ALTER TABLE trades ADD COLUMN entry_identity_json TEXT DEFAULT NULL",
            ):
                try:
                    c.execute(stmt)
                except Exception:
                    pass
            from ..engine import trade_provenance
            trade_provenance.migrate(c)
            from ..strategy.legacy_authority import SCHEMA as legacy_authority_schema
            c.executescript(legacy_authority_schema)
            if c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='strategy_versions'").fetchone():
                # the guards name tables a database created by an earlier release
                # may lack; the Factory's own idempotent schema comes first
                from ..strategy.factory_handoff import AUTHORITY_GUARDS, SCHEMA as factory_schema
                c.executescript(factory_schema + AUTHORITY_GUARDS)
            # backfill: existing rows get their birth time so the demotion
            # clock starts now rather than firing retroactively
            try:
                c.execute("UPDATE strategies SET state_changed_at=created_at "
                          "WHERE state_changed_at IS NULL OR state_changed_at=''")
            except Exception:
                pass

    # -- connection -----------------------------------------------------
    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            # The timing subclass exists only while the opt-in trace is on, so
            # an untraced connection carries no per-statement overhead.
            extra = ({"factory": _TracedConnection}
                     if os.environ.get('LUFFY_TRACE_SLOW_TRANSACTIONS') == '1' else {})
            conn = sqlite3.connect(str(self.db_path), timeout=30, **extra)
            conn.row_factory = sqlite3.Row
            from . import journal_evidence
            journal_evidence.install(conn)
            conn.create_function("learning_target_write", 0,
                                 lambda: int(getattr(self._local, "learning_target_write", False)))
            conn.create_function("strategy_governor_write", 0,
                                 lambda: int(getattr(self._local, "governor_write", False)))
            conn.create_function("strategy_approval_write", 0,
                                 lambda: int(getattr(self._local, "approval_write", False)))
            conn.execute("PRAGMA foreign_keys=ON")
            self._local.conn = conn
        return conn

    @contextmanager
    def _tx(self):
        """Serialized write transaction (single-writer discipline)."""
        trace = _TxTrace() if os.environ.get('LUFFY_TRACE_SLOW_TRANSACTIONS') == '1' else None
        try:
            with self._write_lock:
                if trace:
                    trace.mark('mutex')
                conn = None
                try:
                    conn = self._conn()
                    if trace:
                        trace.conn = conn
                        if isinstance(conn, _TracedConnection):
                            conn._begin_armed, conn._begin_s = True, None
                    yield conn
                    if trace:
                        trace.mark('body')
                    conn.commit()
                    if trace:
                        trace.mark('commit')
                except BaseException as exc:
                    if trace:
                        if 'body' in trace.phase:
                            trace.mark('commit')  # the body had ended: COMMIT itself failed
                        trace.mark('body')        # body raised
                    # Reuse DATA's existing rollback-or-discard cleanup. A failed
                    # rollback or process interrupt must not strand this thread's
                    # connection with SQLite writer ownership, or mask the cause.
                    if conn is not None:
                        from ..data.sqlite_tx import abort
                        abort(self._local, conn, 'journal')
                    from ..observability.safety import journal_failure, is_storage_error
                    if is_storage_error(exc):
                        journal_failure(self, 'write', exc)
                    if trace:
                        trace.mark('cleanup')
                    raise
        finally:
            if trace:
                trace.emit()

    # -- writes ----------------------------------------------------------
    def log_cycle(self, snap: Snapshot, cycle_id: str, mode: str) -> None:
        with self._tx() as c:
            c.execute(
                "INSERT OR REPLACE INTO cycles VALUES (?,?,?,?,?,?,?,?,?)",
                (cycle_id, snap.ts, snap.symbol, snap.price, snap.regime,
                 snap.adx, snap.btc_trend, snap.market_type, mode))

    def log_votes(self, cycle_id: str, symbol: str, votes: list) -> None:
        rows = [(cycle_id, v.ts, symbol, v.agent, v.side.value,
                 v.conviction, v.confidence, v.rationale,
                 json.dumps(v.meta)) for v in votes]
        if not rows:
            return
        with self._tx() as c:
            c.executemany(
                "INSERT INTO votes(cycle_id,ts,symbol,agent,side,conviction,"
                "confidence,rationale,meta) VALUES (?,?,?,?,?,?,?,?,?)", rows)

    def log_decision(self, d: Decision, *, capture_inputs=None) -> None:
        if getattr(d, 'strategy_selection', None) is not None:
            from ..strategy.plan_owner import verified_selection
            verified_selection(d.strategy_signals, d.strategy_selection)
        strat_ids = ",".join(s.get("strategy_id", "")
                             for s in d.strategy_signals or [])
        signals = json.dumps(d.strategy_signals or [])
        with self._tx() as c:
            c.execute(
                "INSERT OR REPLACE INTO decisions "
                "(id,cycle_id,ts,symbol,action,score,threshold,confidence,"
                "executed,skip_reason,size_usdt,entry_price,strategy_ids,"
                "signals_json,meta_p,scan_id,reason_codes,reason_codes_version,instrument_binding_json,strategy_selection_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (d.id, d.cycle_id, d.ts, d.symbol, d.action.value, d.score,
                 d.threshold, d.confidence, int(d.executed), d.skip_reason,
                 d.size_usdt, None, strat_ids, signals,
                 d.meta_p if d.meta_p else None, getattr(d, "scan_id", None),
                 *self._reason_codes_cols(getattr(d, "reason_codes", None)), getattr(d, "instrument_binding_json", None),
                 json.dumps(d.strategy_selection, sort_keys=True, allow_nan=False) if getattr(d, "strategy_selection", None) is not None else None))

            from ..learning import capture as lc, capture_runtime as lr
            captured_row = dict(c.execute('SELECT * FROM decisions WHERE id=?', (d.id,)).fetchone())
            cycle = c.execute('SELECT * FROM cycles WHERE id=?', (d.cycle_id,)).fetchone()
            lc.safely(c, 'decision:'+d.id, lr.decision, captured_row, dict(cycle) if cycle else None,
                      capture_inputs or getattr(d, 'learning_inputs', None))

    def update_decision_outcome(self, decision_id: str, executed: bool,
                                size_usdt: float = 0.0,
                                skip_reason: str = "", reason_codes: list | None = None) -> None:
        with self._tx() as c:
            c.execute("UPDATE decisions SET executed=?, size_usdt=?, "
                      "skip_reason=?, reason_codes=?, reason_codes_version=? WHERE id=?",
                      (int(executed), size_usdt, skip_reason, *self._reason_codes_cols(reason_codes), decision_id))

            from ..learning import capture as lc, capture_runtime as lr
            row = c.execute('SELECT * FROM decisions WHERE id=?', (decision_id,)).fetchone()
            if row is not None:
                from ..cognition.outcomes import timestamp
                lc.safely(c, 'decision:'+decision_id, lr.action, dict(row), timestamp(now_utc().isoformat()))


    def set_decision_entry_price(self, decision_id: str, price: float) -> None:
        with self._tx() as c:
            c.execute("UPDATE decisions SET entry_price=? WHERE id=?",
                      (price, decision_id))

    def add_trade(self, p, accounting: dict | None = None) -> None:
        with self._tx() as c:
            c.execute(
                "INSERT INTO trades (id,decision_id,symbol,side,amount,"
                "entry_price,notional_usdt,leverage,stop_loss,take_profit,"
                "sl_order_id,strategy_id,strategy_name,market_type,exec_mode,"
                "opened_at,realized_pnl,status,tp1_done,initial_risk,"
                "entry_identity_json) "
                "VALUES (:id,:decision_id,:symbol,:side,:amount,:entry_price,"
                ":notional_usdt,:leverage,:stop_loss,:take_profit,:sl_order_id,"
                ":strategy_id,:strategy_name,:market_type,:exec_mode,"
                ":opened_at,0,'open',0,:initial_risk,:entry_identity_json)",
                {"id": p.id, "decision_id": p.decision_id or "",
                 "symbol": p.symbol, "side": p.side.value, "amount": p.amount,
                 "entry_price": p.entry_price,
                 "notional_usdt": p.notional_usdt, "leverage": p.leverage,
                 "stop_loss": p.stop_loss, "take_profit": p.take_profit,
                 # frozen at entry: the risk the position was sized on
                 "initial_risk": (((getattr(p, 'entry_identity', None) or {}).get('initial_risk')
                                   if (getattr(p, 'entry_identity', None) or {}).get('version_id') else None)
                                  or round(abs(p.entry_price - p.stop_loss), 10))
                 if p.stop_loss else None,
                 "sl_order_id": getattr(p, "sl_order_id", ""),
                 "strategy_id": p.strategy_id,
                 "strategy_name": p.strategy_name,
                 "market_type": p.market_type, "exec_mode": p.exec_mode,
                 "opened_at": p.opened_at,
                 "entry_identity_json": _identity_json(p)})
            from ..engine.booking import persist
            persist(c, p.id, "entry", None, accounting)

    def close_trade(self, trade_id: str, exit_price: float, pnl: float,
                    reason: str, closed_at: str | None = None,
                    accounting: dict | None = None) -> None:
        closed_at = closed_at or now_utc().isoformat()
        with self._tx() as c:
            from ..engine.booking import persist, snapshot
            before = snapshot(c.execute('SELECT * FROM trades WHERE id=?', (trade_id,)).fetchone())
            if before is None or before['status'] == 'closed':
                return  # duplicate close callbacks must not double-book P&L
            # `pnl` is this leg, not the trade. It used to be written over
            # realized_pnl, discarding whatever close_partial had already
            # banked — so every trade that took its 1.5R partial recorded
            # only the final leg, and the pooled PF, the decay gate and the
            # judge all graded the book on a fraction of what it earned.
            updated = c.execute(
                "UPDATE trades SET exit_price=?, "
                "realized_pnl=COALESCE(realized_pnl,0)+?, close_reason=?, "
                "closed_at=?, status='closed' WHERE id=? AND status='open'",
                (exit_price, pnl, reason, closed_at, trade_id))
            if not updated.rowcount:
                return
            persist(c, trade_id, "close:"+reason, before, accounting)
        # Event delivery follows commit. A notification failure cannot undo
        # booking; the maintenance timer also catches missed notifications.
        callback = getattr(self, 'on_trade_closed', None)
        if callback is not None:
            try:
                callback()
            except Exception:
                pass

    def align_trade_amount(self, trade_id: str, amount: float,
                           notional: float, pnl_delta: float = 0.0,
                           pnl_total: float | None = None,
                           accounting: dict | None = None, tp1_done: bool = False,
                           _connection=None) -> None:
        """Pull an open trade's size back to what the venue actually holds.

        Used when the venue closed part of a position without us — a stop or
        TP that filled short of the whole line. `notional` shrinks with the
        amount so the heat cap charges the position that exists.

        `pnl_total` sets realized P&L outright and is what a caller passes
        when it has the venue's own figure; `pnl_delta` adds an estimate to
        whatever the journal already banked. Never both — the total already
        contains every leg, so adding to it double-counts the partial.
        """
        from contextlib import nullcontext
        with (nullcontext(_connection) if _connection is not None else self._tx()) as c:
            from ..engine.booking import persist, snapshot
            before = snapshot(c.execute('SELECT * FROM trades WHERE id=?', (trade_id,)).fetchone())
            if before is None or before['status'] == 'closed':
                return
            if pnl_total is not None:
                c.execute("UPDATE trades SET amount=?, notional_usdt=?, "
                          "realized_pnl=? WHERE id=?",
                          (round(amount, 8), round(notional, 2),
                           round(pnl_total, 8), trade_id))
            else:
                c.execute("UPDATE trades SET amount=?, notional_usdt=?, "
                          "realized_pnl=COALESCE(realized_pnl,0)+? WHERE id=?",
                          (round(amount, 8), round(notional, 2),
                           round(pnl_delta, 8), trade_id))
            if tp1_done:
                c.execute("UPDATE trades SET tp1_done=1 WHERE id=?", (trade_id,))
            persist(c, trade_id, "align_total" if pnl_total is not None else "align_delta", before, accounting)

    def record_stop_order(self, trade_id: str, order_id: str,
                          stop_price: float) -> None:
        """Bind a live protective order to its trade, committed.

        Both columns in ONE committed transaction, for the reason
        `exits._move_stop` documents: writing a stop id through
        `Journal.query()` leaves it uncommitted and invisible to every other
        connection while holding a write lock.
        """
        with self._tx() as c:
            c.execute("UPDATE trades SET stop_loss=?, sl_order_id=? "
                      "WHERE id=?",
                      (round(float(stop_price), 6), str(order_id), trade_id))

    def update_position_protection(self, trade_id: str, sl: float, tp: float) -> None:
        with self._tx() as c:
            c.execute("UPDATE trades SET stop_loss=?, take_profit=? WHERE id=?",
                      (sl, tp, trade_id))

    def log_equity(self, equity: float, balance: float, n_open: int,
                   provenance: dict | None = None) -> None:
        """One equity row; `provenance` (the value's source) is written
        atomically beside it. Without provenance any stale provenance for this
        ts is removed, so a row never inherits another value's origin."""
        ts = now_utc().isoformat(timespec="seconds")
        with self._tx() as c:
            c.execute("INSERT OR REPLACE INTO equity VALUES (?,?,?,?)",
                      (ts, equity, balance, n_open))
            if provenance is None:
                c.execute("DELETE FROM equity_provenance WHERE ts=?", (ts,))
            else:
                c.execute("INSERT OR REPLACE INTO equity_provenance(ts, provenance) "
                          "VALUES (?,?)", (ts, json.dumps(provenance, allow_nan=False)))

    def log_brain_event(self, kind: str, subject: str, detail: Any) -> None:
        with self._tx() as c:
            stored = self._brain_detail(c, kind, detail)
            if kind == "market_provenance" and self._provenance_retry(c, subject, stored):
                return
            cursor = c.execute("INSERT INTO brain_events(ts,kind,subject,detail) VALUES (?,?,?,?)",
                      (now_utc().isoformat(), kind, subject, stored))
            if kind in ('execution_incident', 'data_quality_incident', 'execution_error', 'data_error', 'stale_data',
                        'malformed_evidence', 'required_input_unavailable', 'source_failure'):
                from ..learning import capture as lc, capture_runtime as lr
                from ..cognition.outcomes import timestamp
                lc.safely(c, 'incident:'+str(cursor.lastrowid), lr.incident, cursor.lastrowid,
                          timestamp(now_utc().isoformat()), subject, detail, incident_kind=kind)

    PROVENANCE_RETRY_WINDOW = 256

    @staticmethod
    def _provenance_retry(conn, subject, stored) -> bool:
        """True when this exact event was already appended (a logical retry).

        Identity is the stored manifest, which hashes the full detail including
        the caller's decision_id/cycle_id. A distinct observation carries its
        own decision_id, so identical receipt bytes alone are never a retry.
        Only the newest rows are checked: a retry follows its original at once.
        """
        return conn.execute(
            "SELECT 1 FROM (SELECT subject,detail FROM brain_events WHERE kind='market_provenance' "
            "ORDER BY id DESC LIMIT ?) WHERE subject IS ? AND detail=? LIMIT 1",
            (Journal.PROVENANCE_RETRY_WINDOW, subject, stored)).fetchone() is not None

    @staticmethod
    def _brain_detail(conn, kind, detail):
        if kind == "market_provenance":
            from .journal_evidence import store
            return store(conn, detail)
        return detail if isinstance(detail, str) else json.dumps(detail)

    # -- control state ----------------------------------------------------
    def kv_get(self, key: str, default: str | None = None) -> str | None:
        try:
            row = self._conn().execute(
                "SELECT value FROM state_kv WHERE key=?", (key,)).fetchone()
            return row["value"] if row else default
        except Exception as exc:
            from ..observability.safety import journal_failure, is_storage_error
            if is_storage_error(exc):
                journal_failure(self, 'read', exc)
            raise

    def kv_set(self, key: str, value: str) -> None:
        with self._tx() as c:
            c.execute("INSERT OR REPLACE INTO state_kv(key,value) VALUES (?,?)",
                      (key, value))

    def log_control_event(self, event: str, actor: str,
                          from_state: str = "", to_state: str = "",
                          detail: Any = "") -> int:
        with self._tx() as c:
            cursor = c.execute(
                "INSERT INTO control_events(ts,event,from_state,to_state,actor,detail) "
                "VALUES (?,?,?,?,?,?)",
                (now_utc().isoformat(), event, from_state, to_state, actor,
                 detail if isinstance(detail, str) else json.dumps(detail)))
            return int(cursor.lastrowid)

    def schedule_outcome(self, decision_id: str, cycle_id: str, symbol: str,
                         ts: str, action: str, entry_price: float) -> None:
        with self._tx() as c:
            c.execute(
                "INSERT OR REPLACE INTO outcomes"
                "(decision_id,cycle_id,symbol,ts,action,entry_price) "
                "VALUES (?,?,?,?,?,?)",
                (decision_id, cycle_id, symbol, ts, action, entry_price))

            from ..learning import capture as lc
            from ..cognition.outcomes import timestamp
            declared=dict(decision_id=decision_id,cycle_id=cycle_id,symbol=symbol,ts=ts,action=action,entry_price=entry_price)
            lc.safely(c,'decision:'+decision_id,lc.record_action,'decision:'+decision_id,dict(prediction=declared),timestamp(now_utc().isoformat()))

    # -- Attention selection: primitives only; the Selection adapter is
    # observability/selection_persistence.py --------------------------------
    _SELECTION_COLUMNS = ("selection_id", "rule_version", "cycle_as_of_ms", "outcome",
                          "snapshot_id", "snapshot_as_of_ms", "registry_age_ms",
                          "max_snapshot_age_ms", "selected_id", "selected_symbol",
                          "cursor_before", "cursor_after", "canonical_json")

    def record_attention_selection(self, row: dict, *, cursor_name: str,
                                   recorded_at_ms: int) -> str:
        """Insert one selection row and set cursor `cursor_name` to its
        cursor_after in ONE transaction; a None cursor_after deletes the row.

        "inserted" on a write. "duplicate" when an identical row (ignoring
        recorded_at_ms) exists: nothing is written, so a late replay cannot
        rewind the cursor. "conflict" when the ID exists with other content:
        nothing is written. "cursor_conflict" when a new row's cursor_before
        is not exactly the durable cursor (None = no cursor row), e.g. another
        writer advanced it first: nothing is written. The cursor is read under
        BEGIN IMMEDIATE so no other connection can move it before the write."""
        values = tuple(row[k] for k in self._SELECTION_COLUMNS)
        with self._tx() as c:
            if not c.in_transaction:
                c.execute("BEGIN IMMEDIATE")
            old = c.execute(
                f"SELECT {','.join(self._SELECTION_COLUMNS)} FROM attention_selections "
                "WHERE selection_id=?", (row["selection_id"],)).fetchone()
            if old is not None:
                return "duplicate" if tuple(old) == values else "conflict"
            cur = c.execute("SELECT value FROM attention_cursor WHERE name=?",
                            (cursor_name,)).fetchone()
            if (cur[0] if cur else None) != row["cursor_before"]:
                return "cursor_conflict"
            c.execute(
                f"INSERT INTO attention_selections({','.join(self._SELECTION_COLUMNS)},"
                f"recorded_at_ms) VALUES ({','.join('?' * (len(values) + 1))})",
                values + (recorded_at_ms,))
            if row["cursor_after"] is None:
                c.execute("DELETE FROM attention_cursor WHERE name=?", (cursor_name,))
            else:
                c.execute("INSERT OR REPLACE INTO attention_cursor(name,value) VALUES (?,?)",
                          (cursor_name, row["cursor_after"]))
        return "inserted"

    def attention_cursor(self, name: str) -> str | None:
        """The raw stored cursor, None when no row exists. Unvalidated."""
        row = self._conn().execute(
            "SELECT value FROM attention_cursor WHERE name=?", (name,)).fetchone()
        return row["value"] if row else None

    def attention_selection(self, selection_id: str) -> dict | None:
        row = self._conn().execute(
            "SELECT * FROM attention_selections WHERE selection_id=?",
            (selection_id,)).fetchone()
        return dict(row) if row else None

    def record_attention_fetch_outcome(self, selection_id: str, status: str, reason: str,
                                       started_ms: int | None, ended_ms: int | None,
                                       recorded_at_ms: int) -> int:
        """Its own transaction; never touches attention_cursor. The selection
        must already exist (foreign key)."""
        with self._tx() as c:
            cur = c.execute(
                "INSERT INTO attention_fetch_outcomes(selection_id,status,reason,"
                "started_ms,ended_ms,recorded_at_ms) VALUES (?,?,?,?,?,?)",
                (selection_id, status, reason, started_ms, ended_ms, recorded_at_ms))
            return int(cur.lastrowid)

    def attention_fetch_outcomes(self, selection_id: str) -> list[dict]:
        return self.query("SELECT * FROM attention_fetch_outcomes WHERE selection_id=? "
                          "ORDER BY id", (selection_id,))

    # -- strategy population ------------------------------------------------
    def upsert_strategy(self, st) -> None:
        from .types import Strategy as _S   # typing only; avoid cycle at import
        with self._tx() as c:
            # named columns: migrations (e.g. retire_reason) must not break
            # positional inserts
            c.execute(
                "INSERT OR REPLACE INTO strategies "
                "(id,name,kind,params,state,description,origin,hypothesis,"
                "invalidation,regime_filter,markets,generation,parent_id,"
                "created_at,stats_json) VALUES "
                "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (st.id, st.name, st.kind, json.dumps(st.params), st.state.value,
                 st.description, st.origin,
                 getattr(st, "hypothesis", ""), getattr(st, "invalidation", ""),
                 json.dumps(sorted(getattr(st, "regime_filter", []) or [])),
                 json.dumps(sorted(getattr(st, "markets", []) or [])),
                 int(getattr(st, "generation", 0)), getattr(st, "parent_id", ""),
                 st.created_at, json.dumps(st.stats)))
            # INSERT OR REPLACE rewrites the whole row, so state_changed_at
            # would fall back to its DEFAULT. Seed it for fresh rows.
            c.execute("UPDATE strategies SET state_changed_at=created_at "
                      "WHERE id=? AND (state_changed_at IS NULL "
                      "OR state_changed_at='')", (st.id,))

    # ── specs (the StrategySpec population) ──────────────────────────
    def _ensure_spec_column(self) -> None:
        cols = [r[1] for r in self._conn().execute(
            "PRAGMA table_info(strategies)")]
        if "spec_json" not in cols:
            c = self._conn()
            c.execute("ALTER TABLE strategies ADD COLUMN spec_json TEXT")
            c.commit()

    def upsert_spec(self, spec, state: str = "paper",
                    origin: str = "strategist") -> None:
        """Store a StrategySpec alongside the legacy genome rows.

        kind='spec' routes the row to the compiler at load; everything else
        keeps loading as a Genome, so the two populations coexist and the
        cutover needs no migration.
        """
        import json as _j
        from datetime import datetime, timezone
        self._ensure_spec_column()
        c = self._conn()
        c.execute(
            "INSERT OR REPLACE INTO strategies "
            "(id, name, kind, params, state, description, origin, hypothesis,"
            " invalidation, regime_filter, markets, generation, parent_id,"
            " created_at, stats_json, spec_json) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,"
            "  COALESCE((SELECT created_at FROM strategies WHERE id=?), ?),"
            "  COALESCE((SELECT stats_json FROM strategies WHERE id=?), '{}'),"
            "  ?)",
            (spec.id, spec.name, "spec", "{}", state,
             (spec.thesis or "")[:400], origin, spec.thesis,
             spec.invalidation, _j.dumps(list(spec.regime_filter)),
             _j.dumps(list(spec.markets)), spec.generation, spec.parent_id,
             spec.id, datetime.now(timezone.utc).isoformat(),
             spec.id, spec.to_json()))
        c.commit()

    def list_specs(self, states=None) -> list:
        """[(row, StrategySpec)] for every stored spec."""
        import json as _j
        from ..strategy.spec import StrategySpec
        self._ensure_spec_column()
        q = "SELECT * FROM strategies WHERE kind='spec' AND spec_json IS NOT NULL"
        args = ()
        if states:
            q += f" AND state IN ({','.join('?' * len(states))})"
            args = tuple(states)
        out = []
        for r in self.query(q, args):
            try:
                from ..strategy.factory_handoff import bind_installed_spec
                spec = bind_installed_spec(self, StrategySpec.from_json(r['spec_json']))
                out.append((r, spec))
            except Exception:
                continue
        return out

    def list_strategies(self, states: list[str] | None = None) -> list[dict]:
        if states:
            q = f"SELECT * FROM strategies WHERE state IN " \
                f"({','.join('?' * len(states))}) ORDER BY created_at"
            return self.query(q, tuple(states))
        return self.query("SELECT * FROM strategies ORDER BY created_at")

    # -- reads (used by brain, dashboard, learning) -----------------------
    def query(self, sql: str, params: tuple = ()) -> list[dict]:
        try:
            from .journal_evidence import read_sql
            conn = self._conn()
            rows = conn.execute(read_sql(conn, sql), params).fetchall()
            return [dict(r) for r in rows]
        except Exception as exc:
            from ..observability.safety import journal_failure, is_storage_error
            if is_storage_error(exc):
                journal_failure(self, 'read', exc)
            raise

    def open_trades(self) -> list[dict]:
        return self.query("SELECT * FROM trades WHERE status='open'")

    def trades_for_strategy(self, strategy_id: str) -> list[dict]:
        return self.query(
            "SELECT * FROM trades WHERE strategy_id=? ORDER BY opened_at",
            (strategy_id,))

    def unresolved_outcomes(self, older_than_hours: float = 1.0) -> list[dict]:
        return self.query(
            "SELECT * FROM outcomes WHERE resolved_at IS NULL "
            "AND ts <= datetime('now', ?) ",
            (f"-{older_than_hours} hours",))

    def agent_accuracy(self, since_hours: float = 168.0) -> list[dict]:
        """Directional accuracy of each agent's OWN vote vs the 4h forward
        return. A vote is correct when its direction matched the raw move:
        a vote agreeing with the decision inherits the decision's outcome,
        a dissenting vote gets the flipped label. (Previously every agent
        inherited the decision's correctness — inverting truth for
        dissenters and poisoning accuracy + calibration simultaneously.)"""
        return self.query("""
            SELECT v.agent,
                   COUNT(*) AS n,
                   AVG(CASE WHEN (v.conviction > 0) = (o.action = 'BUY')
                            THEN o.correct_4h ELSE 1 - o.correct_4h END
                       ) AS accuracy
            FROM votes v INDEXED BY idx_votes_cycle_symbol
            JOIN outcomes o ON o.symbol=v.symbol AND o.cycle_id=v.cycle_id
             AND o.resolved_at IS NOT NULL
            WHERE v.conviction != 0 AND o.correct_4h IS NOT NULL
              AND v.ts >= datetime('now', ?)
            GROUP BY v.agent
        """, (f"-{since_hours} hours",))

    def strategy_health_rows_for_spec(self, spec_id: str) -> list[dict]:
        """The subset of `strategy_health_rows` that research-question and
        research-plan verification of one spec can consult: every sweep
        record, the spec's own observation rows, and observation rows with
        no usable subject (NULL, empty or non-text), which derivation must
        still see in order to refuse them. Other specs' observation rows
        are excluded. Id order, as `strategy_health_rows` returns them
        (brain_events has no kind index, so that scan is in rowid order).
        SELECT only."""
        return self.query(
            "SELECT id, ts, kind, subject, detail FROM brain_events "
            "WHERE kind='strategy_health_sweep' "
            "OR (kind='strategy_health_observed' AND (subject=? "
            "OR subject IS NULL OR typeof(subject)<>'text' OR subject='')) "
            "ORDER BY id", (spec_id,))


    def strategy_health_rows_by_id(self, event_ids) -> list[dict]:
        """The `strategy_health_rows` rows with the given event ids, in id
        order. SELECT only."""
        ids = sorted(set(event_ids))
        if not ids:
            return []
        return self.query(
            "SELECT id, ts, kind, subject, detail FROM brain_events "
            "WHERE kind IN ('strategy_health_observed', "
            "'strategy_health_sweep') "
            f"AND id IN ({','.join('?' * len(ids))}) ORDER BY id",
            tuple(ids))


    @staticmethod
    def _reason_codes_cols(codes) -> tuple:
        if codes is None:
            return None, None
        return _rc.encode(codes), _rc.VERSION


    def decision_observation_rows(self) -> list[dict]:
        """Raw decision columns for strategy/signal_occurrence_observation.
        SELECT only — decoding and ordering belong to the summariser."""
        return self.query(
            "SELECT id, ts, scan_id, symbol, action, executed, reason_codes, "
            "reason_codes_version, signals_json FROM decisions")

    def strategy_health_rows(self) -> list[dict]:
        """Raw strategy-health-observation.v1 brain_events for
        strategy/health_observation.history. SELECT only."""
        return self.query(
            "SELECT id, ts, kind, subject, detail FROM brain_events "
            "WHERE kind IN ('strategy_health_observed', "
            "'strategy_health_sweep')")
