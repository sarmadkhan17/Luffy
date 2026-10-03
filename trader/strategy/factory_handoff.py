"""Stage-5 handoff: referee-passed candidate -> first-live eligibility.

SDD v3.2 §14.2 names one path:

    research -> spec -> validation/referee -> freeze immutable spec + evidence
    receipt -> shadow/paper -> approval required -> owner approval for the
    exact version/hash -> active

Factory proposers can install exact immutable versions for paper probation.
Unversioned proposals remain research/paper only. Eligibility is read-only;
Governor alone can record approved first-live activation, pause, degrade,
reactivate or retire. Activation re-verifies evidence, exact owner approval,
current capacity/inputs and held control/Risk prerequisites. The separate
real-entry fence remains closed for Factory versions in this package.
Historical exceptions require explicit immutable grandfather authority.
"""
from __future__ import annotations

import copy
from contextlib import nullcontext
import hashlib
import json
import math
import time
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import datetime, timezone

from ..engine.paper_exit_evidence import canonical
from ..engine.state import OWNER_ACTORS
from . import capacity as cap
from .compile import compile_spec
from .promotion import stats_of
from .spec import StrategySpec

VERSION_SCHEMA = "strategy-version.v1"
RECEIPT_SCHEMA = "strategy-validation-receipt.v1"
PROBATION_SCHEMA = "strategy-probation-receipt.v1"
REQUEST_SCHEMA = "strategy-first-live-approval-request.v1"
DECISION_SCHEMA = "strategy-first-live-owner-decision.v1"

# research_candidates states that carry a registered gate1 rejection and a
# passing gate3 (runner._examine writes referee_passed; reason_passed and
# admitted are its later states). Every other state is refused.
SOURCE_STATES = ("referee_passed", "reason_passed", "admitted")

# lifecycle vocabulary: SDD §14.3, stopping before ACTIVE
PROPOSED = "PROPOSED"                  # derived edit, no validation of its own
VALIDATED = "VALIDATED"
SHADOW = "SHADOW"                      # shadow/paper probation
APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
APPROVED_FIRST_LIVE = "APPROVED_FIRST_LIVE"
REJECTED = "REJECTED"
DEGRADED = "DEGRADED"
RETIRED = "RETIRED"
_ALLOWED = {
    None: {VALIDATED, PROPOSED},
    PROPOSED: set(),
    VALIDATED: {SHADOW},
    SHADOW: {APPROVAL_REQUIRED},
    APPROVAL_REQUIRED: {APPROVED_FIRST_LIVE, REJECTED},
    APPROVED_FIRST_LIVE: set(),
    DEGRADED: set(),
    REJECTED: set(),
    RETIRED: set(),
}

# probation receipt statuses
P_SATISFIED = "SATISFIED"
P_NO_POLICY = "NO_REGISTERED_POLICY"
P_NOT_INSTALLED = "NOT_INSTALLED"
P_INSTALLED_DIFFERS = "INSTALLED_SPEC_DIFFERS"
P_INSUFFICIENT = "INSUFFICIENT_TRADES"
P_NOT_SATISFIED = "NOT_SATISFIED"
P_IDENTITY_UNAVAILABLE = "TRADE_IDENTITY_UNAVAILABLE"   # no per-trade identity
P_INCOMPLETE = "INCOMPLETE_IDENTITY"   # a trade's version is unproven

P_COST_INCOMPLETE = "INCOMPLETE_COST_EVIDENCE"
P_EXIT_INCOMPLETE = "INCOMPLETE_EXIT_SEMANTICS_EVIDENCE"

# Per-trade version identity is Trade Provenance's (branch
# trade-provenance-r1, engine/trade_provenance.py): `trades.entry_identity_json`,
# schema trade-entry-identity.v1, whose `spec_sha256` hashes the compiled spec
# the evaluator ran with the same canonical JSON as `spec_hash`. Read only;
# where the column is absent no trade is attributable and probation says so.
IDENTITY_COLUMN = "entry_identity_json"
IDENTITY_SCHEMA = "trade-entry-identity.v1"
INSTALL_SCHEMA = "strategy-version-install.v1"
INSTALL_MODES = ("paper",)
# `trades.exec_mode` values that prove paper/shadow execution. The real
# order path refuses anything but "live" (it has no paper execution), so a
# paper-labelled trade cannot be a venue order; "live" or a missing/unknown
# mode never counts toward probation.
PAPER_EXEC_MODES = ("paper",)
POLICY_KEYS = ("paper_probation_trades", "paper_min_winrate",
               "paper_min_profit_factor")

# what a version / approval request records about capacity: a reference to
# the re-evaluated receipt contract, never a number
CAPACITY = cap.CONTRACT_REF

_TABLES = ("strategy_versions", "strategy_validation_receipts",
           "strategy_version_installs", "strategy_probation_receipts", "strategy_approval_requests",
           "strategy_approval_decisions", "strategy_version_events", "strategy_governor_events")

SCHEMA = """
CREATE TABLE IF NOT EXISTS strategy_versions (
    version_id TEXT PRIMARY KEY,
    strategy_id TEXT NOT NULL,
    spec_hash TEXT NOT NULL,
    parent_version_id TEXT,
    source_kind TEXT NOT NULL,
    source_id TEXT NOT NULL,
    canonical_sha256 TEXT NOT NULL,
    canonical_json TEXT NOT NULL,
    recorded_at_ms INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS strategy_validation_receipts (
    receipt_id TEXT PRIMARY KEY,
    version_id TEXT NOT NULL UNIQUE REFERENCES strategy_versions(version_id),
    canonical_sha256 TEXT NOT NULL,
    canonical_json TEXT NOT NULL,
    recorded_at_ms INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS strategy_version_installs (
    install_id TEXT PRIMARY KEY,
    version_id TEXT NOT NULL UNIQUE REFERENCES strategy_versions(version_id),
    strategy_id TEXT NOT NULL,
    spec_hash TEXT NOT NULL,
    mode TEXT NOT NULL,
    canonical_sha256 TEXT NOT NULL,
    canonical_json TEXT NOT NULL,
    recorded_at_ms INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS strategy_probation_receipts (
    receipt_id TEXT PRIMARY KEY,
    version_id TEXT NOT NULL REFERENCES strategy_versions(version_id),
    status TEXT NOT NULL,
    canonical_sha256 TEXT NOT NULL,
    canonical_json TEXT NOT NULL,
    recorded_at_ms INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS strategy_approval_requests (
    request_id TEXT PRIMARY KEY,
    version_id TEXT NOT NULL UNIQUE REFERENCES strategy_versions(version_id),
    canonical_sha256 TEXT NOT NULL,
    canonical_json TEXT NOT NULL,
    recorded_at_ms INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS strategy_approval_decisions (
    decision_id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL UNIQUE
        REFERENCES strategy_approval_requests(request_id),
    decision TEXT NOT NULL,
    actor TEXT NOT NULL,
    canonical_sha256 TEXT NOT NULL,
    canonical_json TEXT NOT NULL,
    recorded_at_ms INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS strategy_version_events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    version_id TEXT NOT NULL REFERENCES strategy_versions(version_id),
    from_state TEXT,
    to_state TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    ref_id TEXT,
    actor TEXT NOT NULL,
    at_ms INTEGER NOT NULL,
    UNIQUE (version_id, to_state)
);
CREATE TABLE IF NOT EXISTS strategy_governor_events(
 seq INTEGER PRIMARY KEY AUTOINCREMENT, version_id TEXT NOT NULL,
 to_state TEXT NOT NULL, canonical_json TEXT NOT NULL, canonical_sha256 TEXT NOT NULL);
""" + "".join(
    f"""CREATE TRIGGER IF NOT EXISTS {t}_no_update BEFORE UPDATE ON {t}
BEGIN SELECT RAISE(ABORT, '{t} is immutable'); END;
CREATE TRIGGER IF NOT EXISTS {t}_no_delete BEFORE DELETE ON {t}
BEGIN SELECT RAISE(ABORT, '{t} is immutable'); END;
""" for t in _TABLES)


AUTHORITY_GUARDS = """
CREATE TRIGGER IF NOT EXISTS governor_insert_authority
BEFORE INSERT ON strategy_governor_events WHEN strategy_governor_write() != 1
BEGIN SELECT RAISE(ABORT, 'STRATEGY_GOVERNOR_REQUIRED'); END;
CREATE TRIGGER IF NOT EXISTS version_lifecycle_authority
BEFORE INSERT ON strategy_version_events
WHEN NEW.to_state IN ('ACTIVE','PAUSED','REACTIVATED','DEGRADED','RETIRED')
BEGIN SELECT RAISE(ABORT, 'STRATEGY_GOVERNOR_REQUIRED'); END;
CREATE TRIGGER IF NOT EXISTS version_strategy_state_authority
BEFORE UPDATE OF state ON strategies
WHEN NEW.state != OLD.state AND strategy_governor_write() != 1
AND EXISTS (SELECT 1 FROM strategy_versions WHERE strategy_id=OLD.id)
BEGIN SELECT RAISE(ABORT, 'STRATEGY_GOVERNOR_REQUIRED'); END;
CREATE TRIGGER IF NOT EXISTS version_strategy_insert_authority
BEFORE INSERT ON strategies
WHEN NEW.state != 'paper' AND strategy_governor_write() != 1
AND EXISTS (SELECT 1 FROM strategy_versions WHERE strategy_id=NEW.id)
BEGIN SELECT RAISE(ABORT, 'STRATEGY_GOVERNOR_REQUIRED'); END;
CREATE TRIGGER IF NOT EXISTS version_strategy_delete_authority
BEFORE DELETE ON strategies
WHEN strategy_governor_write() != 1
AND EXISTS (SELECT 1 FROM strategy_versions WHERE strategy_id=OLD.id)
BEGIN SELECT RAISE(ABORT, 'STRATEGY_GOVERNOR_REQUIRED'); END;
CREATE TRIGGER IF NOT EXISTS version_strategy_replace_authority
BEFORE INSERT ON strategies
WHEN EXISTS (SELECT 1 FROM strategies WHERE id=NEW.id AND state != NEW.state)
AND EXISTS (SELECT 1 FROM strategy_versions WHERE strategy_id=NEW.id)
AND strategy_governor_write() != 1
BEGIN SELECT RAISE(ABORT, 'STRATEGY_GOVERNOR_REQUIRED'); END;
"""


class HandoffRefused(ValueError):
    """The step is refused; `.code` is the stable reason code."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _refuse(code: str):
    raise HandoffRefused(code)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _jsha(obj) -> str:
    return _sha(canonical(obj))


def ensure(journal) -> None:
    with journal._tx() as c:
        c.executescript(SCHEMA + AUTHORITY_GUARDS)


# ── stored-record helpers ────────────────────────────────────────────────
def _load(row) -> dict:
    """A stored record, its canonical hash re-verified."""
    if _sha(row["canonical_json"]) != row["canonical_sha256"]:
        _refuse("record_corrupt")
    return json.loads(row["canonical_json"])


def _one(journal, sql: str, args: tuple):
    rows = journal.query(sql, args)
    return rows[0] if rows else None


def _insert(c, table: str, key: str, row: dict, ignore=("recorded_at_ms",)):
    """insert / duplicate / conflict on the primary key; never overwrites.
    A duplicate is identical in every column except those in `ignore`."""
    cur = c.execute(f"SELECT * FROM {table} WHERE {key}=?", (row[key],))
    old = cur.fetchone()
    if old is not None:
        same = all(old[k] == v for k, v in row.items() if k not in ignore)
        return ("duplicate" if same else "conflict"), dict(old)
    cols = ",".join(row)
    c.execute(f"INSERT INTO {table}({cols}) VALUES "
              f"({','.join('?' * len(row))})", tuple(row.values()))
    return "inserted", row


def _begin(c) -> None:
    if not c.in_transaction:
        c.execute("BEGIN IMMEDIATE")


def events(journal, version_id: str) -> list:
    return journal.query("SELECT * FROM strategy_version_events "
                         "WHERE version_id=? ORDER BY seq", (version_id,))


def state_of(journal, version_id: str) -> str | None:
    if journal.query("SELECT 1 FROM sqlite_master WHERE type='table' AND name='strategy_governor_events'"):
        rows = journal.query("SELECT to_state FROM strategy_governor_events WHERE version_id=? ORDER BY seq DESC LIMIT 1", (version_id,))
        if rows:
            ev = governor_events(journal,version_id)
            if rows[0]['to_state'] != ev[-1]['to_state']:
                _refuse('governor_index_differs')
            return ev[-1]['to_state']
    ev = events(journal, version_id)
    return ev[-1]["to_state"] if ev else None


def _transition(c, version_id: str, to_state: str, reason_code: str,
                ref_id: str | None, actor: str, at_ms: int) -> str:
    """Append one lifecycle event inside the caller's transaction."""
    rows = c.execute("SELECT to_state FROM strategy_version_events WHERE "
                     "version_id=? ORDER BY seq", (version_id,)).fetchall()
    reached = [r[0] for r in rows]
    if to_state in reached:
        return "duplicate"
    cur = reached[-1] if reached else None
    if to_state not in _ALLOWED[cur]:
        _refuse(f"transition_not_allowed:{cur}->{to_state}")
    c.execute("INSERT INTO strategy_version_events(version_id, from_state, "
              "to_state, reason_code, ref_id, actor, at_ms) "
              "VALUES (?,?,?,?,?,?,?)",
              (version_id, cur, to_state, reason_code, ref_id, actor,
               int(at_ms)))
    return "inserted"


# ── the spec a candidate compiles to ─────────────────────────────────────
def candidate_spec(journal, cfg: dict, candidate: dict,
                   evaluated: dict | None = None) -> StrategySpec:
    """The spec `kernel._research_handoff` hands to `analyst.admit` for this
    candidate: `Combination.to_spec()`, its universe declared as the symbols
    it was examined on, and `provenance.research_hash`. The rebuilt
    combination must reproduce the candidate's hash.

    With `evaluated` (the referee look's research-evaluated-candidate.v1)
    the combination is the one the referee actually evaluated, rendered
    thresholds included; without it, the ledger vocabulary as measured NOW
    (the current gauges) renders it."""
    from ..research.combo import Combination
    from ..research.runner import ResearchRunner
    from ..research.universe import DISCOVERY, HELDOUT
    h, tf, geo = candidate["hash"], candidate["tf"], candidate["geo"]
    if evaluated is not None:
        try:
            c = Combination.from_dict(evaluated["combo"])
        except (KeyError, TypeError, ValueError):
            c = None
        if c is None or (c.tf, c.geo) != (tf, geo):
            _refuse("candidate_cannot_be_rebuilt")
    else:
        rows = journal.query("SELECT * FROM research_combos WHERE hash=?",
                             (h,))
        runner = ResearchRunner(journal, cfg, run=lambda *a, **k: None)
        c = runner._combo(rows[0], tf, geo) if rows else None
    if c is None or c.hash != h:
        _refuse("candidate_cannot_be_rebuilt")
    spec = c.to_spec()
    spec.universe = {"include": list(DISCOVERY) + list(HELDOUT),
                     "exclude": []}
    spec.provenance = {**(spec.provenance or {}), "research_hash": h}
    return spec


def _frozen_spec(spec: StrategySpec) -> tuple[dict, str]:
    """(spec dict, spec_hash) after `compile_spec`, which derives
    data_requires from the expressions actually used."""
    s = copy.deepcopy(spec)
    try:
        compile_spec(s)
    except Exception as e:                              # noqa: BLE001
        _refuse(f"spec_does_not_compile:{type(e).__name__}")
    d = s.to_dict()
    return d, _jsha(d)


# ── the referee evidence ─────────────────────────────────────────────────
def gate_evidence(journal, h: str) -> dict:
    """The registered referee evidence for one candidate, re-read from the
    ledger. Refuses unless the candidate is in a source state, has exactly
    one gate1 look in the error budget, that look rejected at its own
    alpha, the candidate's gate1 copies that look, and gate3 passed."""
    cand = _one(journal, "SELECT * FROM research_candidates WHERE hash=?",
                (h,))
    if cand is None:
        _refuse("candidate_not_found")
    if cand["state"] not in SOURCE_STATES:
        _refuse(f"candidate_not_referee_passed:{cand['state']}")
    tests = journal.query("SELECT * FROM research_tests WHERE hash=? AND "
                          "gate='gate1' ORDER BY seq", (h,))
    if len(tests) != 1:
        _refuse("gate1_look_missing" if not tests else "gate1_look_ambiguous")
    t = dict(tests[0])
    if t["p"] is None or not t["rejected"] \
            or float(t["p"]) > float(t["alpha_t"]):
        _refuse("gate1_not_rejected")
    try:
        g1 = json.loads(cand["gate1"] or "null")
        g3 = json.loads(cand["gate3"] or "null")
    except (TypeError, ValueError):
        _refuse("candidate_gate_evidence_malformed")
    if not isinstance(g1, dict) or g1.get("p") != t["p"] \
            or g1.get("alpha") != t["alpha_t"]:
        _refuse("candidate_gate1_does_not_match_look")
    if not isinstance(g3, dict) or g3.get("passed") is not True:
        _refuse("gate3_not_passed")
    combo = _one(journal, "SELECT hash, tf, geo, parts FROM research_combos "
                 "WHERE hash=?", (h,))
    if combo is None or (combo["tf"], combo["geo"]) != (cand["tf"],
                                                        cand["geo"]):
        _refuse("candidate_combo_missing")
    look = {k: t[k] for k in ("seq", "hash", "tf", "geo", "gate", "p",
                              "alpha_t", "rejected", "braked", "detail",
                              "at")}
    evaluated = _evaluated(t, h, cand["tf"], cand["geo"])
    return {"candidate": {"hash": h, "tf": cand["tf"], "geo": cand["geo"],
                          "state": cand["state"]},
            "gate1_look": look, "gate1_look_sha256": _jsha(look),
            "gate1": g1, "gate3": g3,
            "candidate_gates_sha256": _jsha({"gate1": g1, "gate3": g3}),
            "combo_sha256": _jsha(dict(combo)),
            "evaluated": evaluated, "evaluated_sha256": _jsha(evaluated)}


def _evaluated(look: dict, h: str, tf: str, geo: str) -> dict:
    """The rule the registered gate1 look evaluated (gate3 ran in the same
    referee job on the same rule), from the look's own append-only row.
    A look that did not record it cannot bind any parameters: refused."""
    from ..research.combo import EVALUATED_SCHEMA, Combination
    try:
        rec = (json.loads(look["detail"] or "null") or {}).get("evaluated")
    except (TypeError, ValueError, AttributeError):
        rec = None
    if not isinstance(rec, dict) or rec.get("schema") != EVALUATED_SCHEMA:
        _refuse("referee_evaluated_candidate_unbound")
    try:
        c = Combination.from_dict(rec["combo"])
    except (KeyError, TypeError, ValueError):
        _refuse("referee_evaluated_candidate_unbound")
    if (c.hash, c.tf, c.geo) != (h, tf, geo) or rec.get("hash") != h \
            or (c.long, c.short) != (rec.get("entry_long"),
                                     rec.get("entry_short")):
        _refuse("referee_evaluated_candidate_mismatch")
    from dataclasses import asdict
    from .exit_policy import EXIT_SEMANTICS_ID, unsupported
    bad = unsupported(c.to_spec().exit)
    if bad:
        _refuse('unsupported_versioned_exit:' + bad)
    if rec.get('exit_semantics_id') != EXIT_SEMANTICS_ID:
        _refuse('referee_exit_semantics_mismatch')
    if rec.get('exit_spec') != asdict(c.to_spec().exit):
        _refuse('referee_exit_spec_mismatch')
    return rec


def _receipt_evidence(ev: dict) -> dict:
    """What a receipt binds: every id/hash, the candidate state excluded
    (referee_passed -> reason_passed -> admitted keeps the evidence)."""
    return {"exit_semantics_id": ev["evaluated"]["exit_semantics_id"],
            "candidate_hash": ev["candidate"]["hash"],
            "tf": ev["candidate"]["tf"], "geo": ev["candidate"]["geo"],
            "gate1_test_seq": ev["gate1_look"]["seq"],
            "gate1_look_sha256": ev["gate1_look_sha256"],
            "candidate_gates_sha256": ev["candidate_gates_sha256"],
            "combo_sha256": ev["combo_sha256"],
            "evaluated_sha256": ev["evaluated_sha256"]}


# ── 1. candidate -> immutable version + validation receipt ──────────────
def _version_record(strategy_id, spec_d, spec_hash, parent, source,
                    evidence_ids) -> dict:
    ident = {"schema": VERSION_SCHEMA, "strategy_id": strategy_id,
             "spec_hash": spec_hash, "parent_version_id": parent,
             "source": source}
    # no capacity here: the version is the strategy itself; capacity is
    # evaluated at use against its own receipts (records written before
    # this carried a capacity reference and still load and dedupe)
    return {**ident, "version_id": _jsha(ident), "spec": spec_d,
            "evidence_ids": evidence_ids}


def _insert_version(c, rec: dict, at_ms: int) -> str:
    """insert / duplicate / conflict for one version record. A stored record
    that differs only in its (non-semantic) capacity reference is the same
    version: a retry stays idempotent across the capacity contract change."""
    row = _version_row(rec, at_ms)
    old = c.execute("SELECT * FROM strategy_versions WHERE version_id=?",
                    (row["version_id"],)).fetchone()
    if old is None:
        return _insert(c, "strategy_versions", "version_id", row)[0]
    try:
        stored = json.loads(old["canonical_json"])
    except (TypeError, ValueError):
        return "conflict"
    stored.pop("capacity", None)
    same = _sha(old["canonical_json"]) == old["canonical_sha256"] and all(
        old[k] == row[k] for k in row
        if k not in ("recorded_at_ms", "canonical_json", "canonical_sha256"))
    return "duplicate" if same and canonical(stored) == canonical(rec) \
        else "conflict"


def _version_row(rec: dict, at_ms: int) -> dict:
    text = canonical(rec)
    return {"version_id": rec["version_id"],
            "strategy_id": rec["strategy_id"], "spec_hash": rec["spec_hash"],
            "parent_version_id": rec["parent_version_id"],
            "source_kind": rec["source"]["kind"],
            "source_id": rec["source"]["id"],
            "canonical_sha256": _sha(text), "canonical_json": text,
            "recorded_at_ms": int(at_ms)}


def create_version(journal, cfg: dict, source: dict, *, at_ms: int) -> dict:
    """One immutable strategy version + its validation receipt, VALIDATED.

    `source` is {"kind": "research_candidate", "hash": <candidate hash>}.
    Retrying is idempotent (same version_id, "duplicate"); nothing stored is
    ever rewritten."""
    ensure(journal)
    kind = (source or {}).get("kind")
    if kind == "investigation_research" or str(
            ((source or {}).get("record") or {}).get("schema", "")
    ).startswith("investigation-"):
        rec = (source or {}).get("record") or {}
        _refuse("predictive_edge_not_established"
                if rec.get("predictive_edge_established") is not True
                else "not_a_referee_candidate")
    if kind != "research_candidate" or not source.get("hash"):
        _refuse("not_a_referee_candidate")
    h = source["hash"]
    ev = gate_evidence(journal, h)
    cand = _one(journal, "SELECT * FROM research_candidates WHERE hash=?",
                (h,))
    # the version is what the referee evaluated, never a re-rendering from
    # the gauges as measured now; if those would render anything else the
    # evidence does not describe that strategy and nothing is issued
    spec_d, spec_hash = _frozen_spec(candidate_spec(
        journal, cfg, dict(cand), evaluated=ev["evaluated"]))
    now_d, now_hash = _frozen_spec(candidate_spec(journal, cfg, dict(cand)))
    if now_hash != spec_hash or canonical(now_d) != canonical(spec_d):
        _refuse("validation_reconstruction_drift")
    ids = _receipt_evidence(ev)
    rec = _version_record(spec_d["id"], spec_d, spec_hash, None,
                          {"kind": "research_candidate", "id": h}, ids)
    vid = rec["version_id"]
    r_ident = {"schema": RECEIPT_SCHEMA, "version_id": vid,
               "strategy_id": rec["strategy_id"], "spec_hash": spec_hash,
               "evidence": ids}
    receipt = {**r_ident, "receipt_id": _jsha(r_ident),
               "gate1_look": ev["gate1_look"], "gate1": ev["gate1"],
               "gate3": ev["gate3"], "evaluated": ev["evaluated"],
               "candidate_state_at_receipt": ev["candidate"]["state"],
               "semantics": ("this exact version may enter shadow/paper "
                             "probation because its candidate's registered "
                             "gate1 look rejected at its alpha and gate3 "
                             "passed; no confidence score; no live, "
                             "allocation or order authority")}
    r_text = canonical(receipt)
    with journal._tx() as c:
        _begin(c)
        v_out = _insert_version(c, rec, at_ms)
        if v_out == "conflict":
            _refuse("version_conflict")
        r_out, _ = _insert(c, "strategy_validation_receipts", "receipt_id",
                           {"receipt_id": receipt["receipt_id"],
                            "version_id": vid,
                            "canonical_sha256": _sha(r_text),
                            "canonical_json": r_text,
                            "recorded_at_ms": int(at_ms)})
        if r_out == "conflict":
            _refuse("validation_receipt_conflict")
        _transition(c, vid, VALIDATED, "referee_evidence_bound",
                    receipt["receipt_id"], "factory", at_ms)
    return {"status": v_out, "version_id": vid, "spec_hash": spec_hash,
            "strategy_id": rec["strategy_id"],
            "validation_receipt_id": receipt["receipt_id"]}


def derive_version(journal, parent_version_id: str, spec: StrategySpec, *,
                   at_ms: int) -> dict:
    """A materially edited spec is a NEW version with parent lineage. It
    inherits no validation receipt, probation or approval (SDD §14.6)."""
    ensure(journal)
    parent = load_version(journal, parent_version_id)
    spec_d, spec_hash = _frozen_spec(spec)
    if spec_hash == parent["spec_hash"]:
        _refuse("spec_unchanged")
    if spec_d["id"] != parent["strategy_id"]:
        _refuse("strategy_id_changed")
    rec = _version_record(parent["strategy_id"], spec_d, spec_hash,
                          parent_version_id,
                          {"kind": "derived", "id": parent_version_id}, {})
    with journal._tx() as c:
        _begin(c)
        out = _insert_version(c, rec, at_ms)
        if out == "conflict":
            _refuse("version_conflict")
        _transition(c, rec["version_id"], PROPOSED, "derived_from_parent",
                    parent_version_id, "factory", at_ms)
    return {"status": out, "version_id": rec["version_id"],
            "spec_hash": spec_hash, "parent_version_id": parent_version_id}


def load_version(journal, version_id: str) -> dict:
    """The stored version, every identity re-derived; refuses on drift."""
    row = _one(journal, "SELECT * FROM strategy_versions WHERE version_id=?",
               (version_id,))
    if row is None:
        _refuse("version_missing")
    rec = _load(row)
    ident = {k: rec[k] for k in ("schema", "strategy_id", "spec_hash",
                                 "parent_version_id", "source")}
    if rec.get("version_id") != version_id or _jsha(ident) != version_id:
        _refuse("version_id_mismatch")
    if _jsha(rec["spec"]) != rec["spec_hash"] \
            or (row["spec_hash"], row["strategy_id"]) != (
                rec["spec_hash"], rec["strategy_id"]):
        _refuse("spec_hash_mismatch")
    spec_d, spec_hash = _frozen_spec(StrategySpec.from_dict(rec["spec"]))
    if spec_hash != rec["spec_hash"]:
        _refuse("spec_recompile_drift")
    return rec


def bind_installed_spec(journal, spec):
    """Opt in an exact installed Factory version for research consumers.

    No provenance heuristic and no serialized StrategySpec/identity change.
    Non-versioned specs retain their legacy simulator, including research
    specs created before Factory version receipts existed.
    """
    if not versioned(journal, spec.id):
        return spec
    rows = journal.query('SELECT version_id FROM strategy_version_installs WHERE strategy_id=?', (spec.id,))
    if len(rows) != 1:
        _refuse('install_missing_or_ambiguous')
    v = load_version(journal, rows[0]['version_id'])
    verify_exit_binding(journal, v)
    verify_install(journal, v, current=False)
    _, h = _frozen_spec(spec)
    if h != v['spec_hash']:
        _refuse('installed_version_differs')
    from .exit_policy import bind_research, EXIT_SEMANTICS_ID
    bind_research(spec)
    spec.exit._exit_binding = {'exit_semantics_id': EXIT_SEMANTICS_ID,
        'version_id': v['version_id'], 'spec_hash': v['spec_hash'],
        'install_id': _install_record(journal, v['version_id'])['install_id']}
    return spec


def verify_exit_binding(journal, version: dict) -> dict:
    """Immutable validation binding for managing an already-open position."""
    row = _one(journal, "SELECT * FROM strategy_validation_receipts "
               "WHERE version_id=?", (version["version_id"],))
    if row is None:
        _refuse("validation_receipt_missing")
    rec = _load(row)
    ident = {k: rec[k] for k in ("schema", "version_id", "strategy_id",
                                 "spec_hash", "evidence")}
    if _jsha(ident) != row["receipt_id"] \
            or rec.get("receipt_id") != row["receipt_id"]:
        _refuse("validation_receipt_id_mismatch")
    if (rec["version_id"], rec["spec_hash"]) != (version["version_id"],
                                                 version["spec_hash"]):
        _refuse("validation_receipt_wrong_version")
    from .exit_policy import EXIT_SEMANTICS_ID
    if rec.get('evidence', {}).get('exit_semantics_id') != EXIT_SEMANTICS_ID:
        _refuse('validation_exit_semantics_mismatch')
    if rec['evidence'] != version['evidence_ids']:
        _refuse('validation_evidence_changed')
    return rec


def verify_validation(journal, version: dict) -> dict:
    """Admission/approval also revalidate the current research ledger."""
    rec = verify_exit_binding(journal, version)
    now = gate_evidence(journal, version["source"]["id"])
    if _receipt_evidence(now) != rec["evidence"] \
            or rec["evidence"] != version["evidence_ids"]:
        _refuse("validation_evidence_changed")
    spec = version["spec"]
    if (spec.get("entry_long"), spec.get("entry_short")) != (
            now["evaluated"]["entry_long"], now["evaluated"]["entry_short"]):
        _refuse("version_spec_not_evaluated_candidate")
    return rec


# ── 2. exact paper install -> probation ──────────────────────────────────
def _installed(journal, strategy_id: str):
    """(spec dict, spec_hash) of the spec the running population loads for
    this id (kind='spec', paper/active), or None when nothing is installed."""
    row = _one(journal, "SELECT spec_json FROM strategies WHERE id=? AND "
               "kind='spec' AND state IN ('paper','active')", (strategy_id,))
    if row is None or not row["spec_json"]:
        return None
    try:
        return _frozen_spec(StrategySpec.from_json(row["spec_json"]))
    except (HandoffRefused, TypeError, ValueError):
        return {}, "uncompilable"


def _install_record(journal, version_id: str) -> dict | None:
    row = _one(journal, "SELECT * FROM strategy_version_installs WHERE "
               "version_id=?", (version_id,))
    return _load(row) if row else None


def record_exact_install(journal, version_id: str, *, mode: str = "paper",
                         at_ms: int) -> dict:
    """Bind the paper install of a VALIDATED version and start its probation
    (VALIDATED -> SHADOW, probation_started_at = at_ms).

    The installed row is read back from `strategies`, exactly as the running
    population loads it; its compiled spec must be byte-identical to the
    frozen spec, or the install is refused (INSTALLED_SPEC_DIFFERS) and
    probation does not start. This never writes `strategies`: the Kernel's
    exact-version handoff does, through the existing paper install."""
    ensure(journal)
    if mode not in INSTALL_MODES:
        _refuse("unknown_install_mode")
    v = load_version(journal, version_id)
    val = verify_validation(journal, v)
    old = _install_record(journal, version_id)
    if old is not None:
        verify_install(journal, v)
        return {"status": "duplicate", "install_id": old["install_id"],
                "version_id": version_id}
    cur = state_of(journal, version_id)
    if cur != VALIDATED:
        _refuse(f"not_validated:{cur}")
    from ..engine.exits import SpecExit
    bad = SpecExit.frozen_unsupported(StrategySpec.from_dict(v["spec"]))
    if bad:
        _refuse(f"unsupported_exit_geometry:{bad}")
    from ..engine.paper import unsupported
    bad = unsupported(StrategySpec.from_dict(v["spec"]))
    if bad:
        _refuse(f"unsupported_paper_exit:{bad}")
    inst = _installed(journal, v["strategy_id"])
    if inst is None:
        _refuse(P_NOT_INSTALLED)
    spec_d, spec_hash = inst
    if spec_hash != v["spec_hash"] or canonical(spec_d) != canonical(
            v["spec"]):
        _refuse(P_INSTALLED_DIFFERS)
    ident = {"schema": INSTALL_SCHEMA, "version_id": version_id,
             "strategy_id": v["strategy_id"], "spec_hash": v["spec_hash"],
             "validation_receipt_id": val["receipt_id"], "mode": mode,
             "exit_semantics_id": val["evidence"]["exit_semantics_id"]}
    rec = {**ident, "install_id": _jsha(ident), "installed_at_ms": int(at_ms),
           "installed_spec": spec_d, "installed_spec_hash": spec_hash}
    text = canonical(rec)
    with journal._tx() as c:
        _begin(c)
        out, _ = _insert(c, "strategy_version_installs", "install_id",
                         {"install_id": rec["install_id"],
                          "version_id": version_id,
                          "strategy_id": v["strategy_id"],
                          "spec_hash": v["spec_hash"], "mode": mode,
                          "canonical_sha256": _sha(text),
                          "canonical_json": text,
                          "recorded_at_ms": int(at_ms)})
        if out == "conflict":
            _refuse("install_conflict")
        _transition(c, version_id, SHADOW, "exact_version_installed",
                    rec["install_id"], "factory", at_ms)
    return {"status": out, "install_id": rec["install_id"],
            "version_id": version_id}


def verify_install(journal, version: dict, *, current: bool = True) -> dict:
    """The version's install record, re-verified; with `current` the spec
    installed now must still be the exact version."""
    from .exit_policy import EXIT_SEMANTICS_ID
    rec = _install_record(journal, version["version_id"])
    if rec is None:
        _refuse("install_missing")
    if rec.get("exit_semantics_id") != EXIT_SEMANTICS_ID:
        _refuse("install_exit_semantics_mismatch")
    ident = {k: rec[k] for k in ("schema", "version_id", "strategy_id",
                                 "spec_hash", "validation_receipt_id",
                                 "mode", "exit_semantics_id")}
    if _jsha(ident) != rec["install_id"] or (
            rec["version_id"], rec["spec_hash"], rec["installed_spec_hash"]
    ) != (version["version_id"], version["spec_hash"],
          version["spec_hash"]):
        _refuse("install_wrong_version")
    if current:
        inst = _installed(journal, version["strategy_id"])
        if inst is None:
            _refuse("installed_version_missing")
        if inst[1] != version["spec_hash"]:
            _refuse("installed_version_differs")
    return rec


def _ms(iso: str | None) -> int | None:
    if not iso:
        return None
    d = datetime.fromisoformat(iso)
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return int(d.timestamp() * 1000)


def _policy(cfg: dict) -> dict | None:
    from ..engine.paper_cost_evidence import WIN_RATE_POLICY
    s = (cfg or {}).get("strategies") or {}
    if any(s.get(k) is None for k in POLICY_KEYS):
        return None
    return {"source": "config.yaml strategies.*",
            "min_trades": int(s["paper_probation_trades"]),
            "min_winrate": float(s["paper_min_winrate"]),
            "min_profit_factor": float(s["paper_min_profit_factor"]),
            "winrate_policy": dict(WIN_RATE_POLICY)}


_TRADE_KEYS = ("id", "strategy_id", "symbol", "side", "exec_mode", "status",
               "opened_at", "closed_at", "realized_pnl")


def trade_identity_available(journal) -> bool:
    """Whether trades carry Trade Provenance's per-trade entry identity."""
    return any(r["name"] == IDENTITY_COLUMN for r in
               journal.query("PRAGMA table_info(trades)"))


def _classify(t: dict, v: dict) -> str:
    """counted | other_version | unattributed, from the trade's own
    Trade Provenance entry identity (never from the registry)."""
    try:
        idn = json.loads(t.get(IDENTITY_COLUMN) or "null")
    except (TypeError, ValueError):
        return "unattributed"
    if not isinstance(idn, dict) or idn.get("schema_version") != \
            IDENTITY_SCHEMA or idn.get("status") != "VERIFIED" \
            or idn.get("kind") != "spec" or not idn.get("spec_sha256"):
        return "unattributed"
    if idn.get("strategy_id") != v["strategy_id"]:
        return "unattributed"
    return "counted" if idn["spec_sha256"] == v["spec_hash"] \
        else "other_version"


def _paper_cost_evidence(journal, trade: dict, version: dict) -> dict:
    from ..engine import paper_cost_evidence as C
    try:
        return C.verify(journal, trade, version,
                        verify_install(journal, version, current=False))
    except (ValueError, TypeError, KeyError, OverflowError, AttributeError):
        return {"net_pnl_status": "UNAVAILABLE", "reason": "COST_RECEIPT_MISSING_OR_INVALID"}


def _economic_costs(trade: dict, version: dict, evidence: dict):
    if evidence.get('net_pnl_status') != 'ESTABLISHED' or not evidence.get('receipt_sha256'):
        dimensions = evidence.get('dimensions', {})
        missing = [k for k in ('commission','slippage','funding')
                   if dimensions.get(k, {}).get('status') not in ('ESTABLISHED','NOT_APPLICABLE')]
        return missing or ['invalid_cost_receipt'], None
    return [], evidence['known_costs']


def _probation_trades(journal, v: dict, since_ms: int, until_ms: int):
    counted, excluded = [], []
    inst = verify_install(journal, v, current=False)
    rows = journal.query("SELECT * FROM trades WHERE strategy_id=? AND "
                         "status='closed' ORDER BY opened_at, id", (v["strategy_id"],))
    if journal.query("SELECT 1 FROM sqlite_master WHERE type='table' AND name='versioned_paper_trades'"):
        rows += journal.query("SELECT * FROM versioned_paper_trades WHERE strategy_id=? AND status='closed' ORDER BY opened_at,id", (v["strategy_id"],))
    for t in rows:
        exit_digest = None
        try:
            o, cl = _ms(t["opened_at"]), _ms(t["closed_at"])
        except (TypeError, ValueError):
            o = cl = None
        if o is not None and cl is not None and (o < since_ms or cl > until_ms):
            continue
        kind = _classify(t, v)
        if t["exec_mode"] == "live":
            kind = "not_paper_execution"
        elif t["exec_mode"] != "paper":
            kind = "execution_mode_unproven"
        elif o is None or cl is None or cl < o:
            kind = "unattributed"
        elif kind == "counted":
            identity = json.loads(t[IDENTITY_COLUMN])
            # Legacy provenance must prove these fields itself; registry or
            # timestamps never supply missing per-trade identity.
            exact = {"strategy_id": v["strategy_id"], "version_id": v["version_id"],
                     "install_id": inst["install_id"], "exec_mode": "paper"}
            if any(identity.get(k) != value for k, value in exact.items()):
                kind = "unattributed"
            elif any(k in t and t[k] != value for k, value in exact.items()):
                kind = "unattributed"
            elif "spec_hash" in t and t["spec_hash"] != v["spec_hash"]:
                kind = "unattributed"
            if kind == 'counted':
                from ..engine.paper_exit_evidence import verify
                try:
                    exit_digest = verify(journal, t, v, inst)
                except (ValueError, TypeError, KeyError, OverflowError, AttributeError):
                    kind = 'exit_semantics_evidence_unproven'
        row = {k: t[k] for k in _TRADE_KEYS}
        if kind == "counted":
            counted.append({**row, "version_id": v["version_id"],
                            "install_id": inst["install_id"], "spec_hash": v["spec_hash"],
                            "entry_identity_sha256": _sha(t[IDENTITY_COLUMN]),
                            "exit_semantics_id": inst['exit_semantics_id'],
                            "canonical_exit_evidence_sha256": exit_digest,
                            "cost_evidence": _paper_cost_evidence(journal, t, v)})
        else:
            excluded.append({"id": t["id"], "reason": kind})
    return counted, excluded


def _assess(journal, cfg, v: dict, since_ms: int, at_ms: int, *,
            check_install: bool = True) -> dict:
    from ..engine.paper import unsupported
    bad = unsupported(StrategySpec.from_dict(v["spec"]))
    policy = _policy(cfg)
    base = {"policy": policy, "trades": [], "trades_sha256": _jsha([]),
            "excluded_trades": [], "stats": None,
            "trade_identity": "trade-provenance " + IDENTITY_SCHEMA}
    if bad:
        return {**base, "status": "UNSUPPORTED_PAPER_EXIT", "reason": bad}
    if policy is None:
        return {**base, "status": P_NO_POLICY}
    if check_install:
        inst = _installed(journal, v["strategy_id"])
        if inst is None:
            return {**base, "status": P_NOT_INSTALLED}
        if inst[1] != v["spec_hash"]:
            return {**base, "status": P_INSTALLED_DIFFERS}
    if not trade_identity_available(journal):
        return {**base, "status": P_IDENTITY_UNAVAILABLE}
    trades, excluded = _probation_trades(journal, v, since_ms, at_ms)
    base.update(excluded_trades=excluded, trades=trades, trades_sha256=_jsha(trades))
    if any(e['reason'] == 'exit_semantics_evidence_unproven' for e in excluded):
        return {**base, 'status': P_EXIT_INCOMPLETE}
    # Identity insufficiency takes precedence over unavailable economics.
    if any(e["reason"] in ("unattributed", "other_version", "execution_mode_unproven")
           for e in excluded):
        return {**base, "status": P_INCOMPLETE}
    gross = stats_of([t for t in trades if isinstance(t['realized_pnl'], (int, float))
                      and not isinstance(t['realized_pnl'], bool) and math.isfinite(t['realized_pnl'])])
    missing, net_trades = [], []
    for trade in trades:
        dimensions, costs = _economic_costs(trade, v, trade["cost_evidence"])
        pnl = trade["realized_pnl"]
        if isinstance(pnl, bool) or not isinstance(pnl, (int, float)) or not math.isfinite(pnl):
            dimensions.append("gross_pnl")
        if dimensions:
            missing.append({"trade_id": trade["id"], "dimensions": dimensions})
        else:
            net_trades.append({**trade, "realized_pnl": pnl - costs})
    gross_stats = {"trades": gross["trades"], "wins": gross["wins"],
                   "winrate": gross["winrate"], "profit_factor": gross["pf"],
                   "pnl": gross["pnl"], "basis": "GROSS_ONLY",
                   "economic_validation": "NOT_ECONOMICALLY_VALIDATED"}
    if missing:
        return {**base, "status": P_COST_INCOMPLETE, "trades": trades,
                "trades_sha256": _jsha(trades), "stats": None,
                "gross_stats": gross_stats, "missing_cost_evidence": missing,
                "economic_validation": "NOT_ECONOMICALLY_VALIDATED"}
    if policy["winrate_policy"].get("basis") not in ("NET", "GROSS"):
        return {**base, "status": "AMBIGUOUS_WIN_RATE_POLICY", "gross_stats": gross_stats}
    st = stats_of(net_trades)
    win_stats = st if policy["winrate_policy"]["basis"] == "NET" else gross
    stats = {"trades": st["trades"], "wins": st["wins"],
             "winrate": win_stats["winrate"], "winrate_basis": policy["winrate_policy"]["basis"], "profit_factor": st["pf"],
             "pnl": st["pnl"], "pnl_source": "gross paper P&L less evidenced costs",
             "paper_cost_basis": "NET_WITH_COMPLETE_COST_EVIDENCE"}
    if st["trades"] < policy["min_trades"]:
        status = P_INSUFFICIENT
    elif win_stats["winrate"] >= policy["min_winrate"] \
            and st["pf"] >= policy["min_profit_factor"]:
        status = P_SATISFIED
    else:
        status = P_NOT_SATISFIED
    return {**base, "status": status, "trades": trades,
            "trades_sha256": _jsha(trades), "stats": stats,
            "gross_stats": gross_stats, "missing_cost_evidence": []}


def evaluate_probation(journal, cfg: dict, version_id: str, *,
                       at_ms: int) -> dict:
    """Record one probation receipt for a SHADOW version under the existing
    config policy, counting only closed trades whose Trade Provenance entry
    identity names this exact version/install, opened after its exact install.
    Qualifying WR/PF use net P&L only with complete cost evidence; gross
    diagnostics never satisfy the profitability gate.
    SATISFIED moves it to APPROVAL_REQUIRED and files the first-live
    approval request in the same transaction; any other status leaves it in
    SHADOW. No threshold is invented: a missing policy key is
    NO_REGISTERED_POLICY."""
    ensure(journal)
    v = load_version(journal, version_id)
    val = verify_validation(journal, v)
    cur = state_of(journal, version_id)
    if cur != SHADOW:
        req = approval_request(journal, version_id)
        if cur == APPROVAL_REQUIRED and req is not None:   # a retry
            _verify_probation(journal, cfg, v, req["probation_receipt_id"])
            return {"status": P_SATISFIED, "result": "duplicate",
                    "probation_receipt_id": req["probation_receipt_id"],
                    "request_id": req["request_id"]}
        _refuse(f"not_in_probation:{cur}")
    inst = verify_install(journal, v, current=False)
    since = inst["installed_at_ms"]
    a = _assess(journal, cfg, v, since, at_ms)
    ident = {"schema": PROBATION_SCHEMA, "version_id": version_id,
             "spec_hash": v["spec_hash"],
             "validation_receipt_id": val["receipt_id"],
             "install_id": inst["install_id"],
             "since_ms": since, "evaluated_at_ms": int(at_ms),
             "exit_semantics_id": inst["exit_semantics_id"]}
    rec = {**ident, "receipt_id": _jsha(ident), **a}
    text = canonical(rec)
    out = {"status": a["status"], "probation_receipt_id": rec["receipt_id"],
           "request_id": None}
    with journal._tx() as c:
        _begin(c)
        res, _ = _insert(c, "strategy_probation_receipts", "receipt_id",
                         {"receipt_id": rec["receipt_id"],
                          "version_id": version_id, "status": a["status"],
                          "canonical_sha256": _sha(text),
                          "canonical_json": text,
                          "recorded_at_ms": int(at_ms)})
        if res == "conflict":
            _refuse("probation_receipt_conflict")
        if a["status"] == P_SATISFIED:
            q_ident = {"schema": REQUEST_SCHEMA, "version_id": version_id,
                       "strategy_id": v["strategy_id"],
                       "spec_hash": v["spec_hash"],
                       "validation_receipt_id": val["receipt_id"],
                       "probation_receipt_id": rec["receipt_id"]}
            req = {**q_ident, "request_id": _jsha(q_ident),
                   "requested_at_ms": int(at_ms), "capacity": CAPACITY,
                   "asks": "first real-money deployment of this exact "
                           "version (SDD §25.4 item 1)"}
            q_text = canonical(req)
            q_out, _ = _insert(c, "strategy_approval_requests", "request_id",
                               {"request_id": req["request_id"],
                                "version_id": version_id,
                                "canonical_sha256": _sha(q_text),
                                "canonical_json": q_text,
                                "recorded_at_ms": int(at_ms)})
            if q_out == "conflict":
                _refuse("approval_request_conflict")
            _transition(c, version_id, APPROVAL_REQUIRED,
                        "probation_satisfied", req["request_id"], "factory",
                        at_ms)
            out["request_id"] = req["request_id"]
    return out


def _verify_probation(journal, cfg, version: dict, receipt_id: str) -> dict:
    """The receipt, re-verified: exact version, exact install, and the same
    counted trades under the same policy. Whether the version is STILL the
    installed one is the eligibility check's concern, not the receipt's."""
    row = _one(journal, "SELECT * FROM strategy_probation_receipts WHERE "
               "receipt_id=?", (receipt_id,))
    if row is None:
        _refuse("probation_receipt_missing")
    rec = _load(row)
    from .exit_policy import EXIT_SEMANTICS_ID
    if rec.get("exit_semantics_id") != EXIT_SEMANTICS_ID:
        _refuse("probation_exit_semantics_mismatch")
    ident = {k: rec[k] for k in ("schema", "version_id", "spec_hash",
                                 "validation_receipt_id", "install_id",
                                 "since_ms", "evaluated_at_ms", "exit_semantics_id")}
    if _jsha(ident) != receipt_id or rec["version_id"] != \
            version["version_id"] or rec["spec_hash"] != version["spec_hash"]:
        _refuse("probation_receipt_wrong_version")
    inst = verify_install(journal, version, current=False)
    if (rec["install_id"], rec["since_ms"]) != (inst["install_id"],
                                                inst["installed_at_ms"]):
        _refuse("probation_receipt_wrong_install")
    if rec["status"] != P_SATISFIED:
        _refuse(f"probation_not_satisfied:{rec['status']}")
    again = _assess(journal, cfg, version, rec["since_ms"],
                    rec["evaluated_at_ms"], check_install=False)
    if again["policy"] != rec["policy"]:
        _refuse("probation_policy_changed")
    if again["status"] != P_SATISFIED \
            or again["trades_sha256"] != rec["trades_sha256"]:
        _refuse("probation_evidence_changed")
    return rec


# ── 3. owner decision ────────────────────────────────────────────────────
def approval_request(journal, version_id: str) -> dict | None:
    row = _one(journal, "SELECT * FROM strategy_approval_requests WHERE "
               "version_id=?", (version_id,))
    return _load(row) if row else None


def record_owner_decision(journal, cfg: dict, request_id: str,
                          decision: str, *, actor: str,
                          decided_at_ms: int) -> dict:
    """The owner's decision on one exact-version request. One decision per
    request: an identical retry is "duplicate", a different one refused."""
    ensure(journal)
    if decision not in ("APPROVED", "REJECTED"):
        _refuse("unknown_decision")
    if actor not in OWNER_ACTORS:
        _refuse("actor_not_owner")
    row = _one(journal, "SELECT * FROM strategy_approval_requests WHERE "
               "request_id=?", (request_id,))
    if row is None:
        _refuse("approval_request_missing")
    req = _load(row)
    v = load_version(journal, req["version_id"])
    if req["spec_hash"] != v["spec_hash"]:
        _refuse("approval_request_wrong_version")
    val = verify_validation(journal, v)
    if req["validation_receipt_id"] != val["receipt_id"]:
        _refuse("approval_request_stale_validation")
    if decision == "APPROVED":
        _verify_probation(journal, cfg, v, req["probation_receipt_id"])
    if int(decided_at_ms) < int(req["requested_at_ms"]):
        _refuse("decision_before_request")
    ident = {"schema": DECISION_SCHEMA, "request_id": request_id,
             "version_id": req["version_id"], "spec_hash": req["spec_hash"],
             "validation_receipt_id": req["validation_receipt_id"],
             "probation_receipt_id": req["probation_receipt_id"],
             "requested_at_ms": req["requested_at_ms"],
             "decision": decision, "actor": actor,
             "decided_at_ms": int(decided_at_ms), "config_sha256": _jsha(cfg)}
    rec = {**ident, "decision_id": _jsha(ident),
           "grants": ("first-live eligibility of this exact version only; "
                      "no activation, allocation or order")
           if decision == "APPROVED" else "nothing"}
    text = canonical(rec)
    with journal._tx() as c:
        _begin(c)
        old = c.execute("SELECT decision_id FROM strategy_approval_decisions "
                        "WHERE request_id=?", (request_id,)).fetchone()
        if old is not None and old[0] != rec["decision_id"]:
            _refuse("decision_already_recorded")
        out, _ = _insert(c, "strategy_approval_decisions", "decision_id",
                         {"decision_id": rec["decision_id"],
                          "request_id": request_id, "decision": decision,
                          "actor": actor, "canonical_sha256": _sha(text),
                          "canonical_json": text,
                          "recorded_at_ms": int(decided_at_ms)})
        _transition(c, req["version_id"],
                    APPROVED_FIRST_LIVE if decision == "APPROVED"
                    else REJECTED, f"owner_{decision.lower()}",
                    rec["decision_id"], actor, decided_at_ms)
    return {"status": out, "decision_id": rec["decision_id"],
            "version_id": req["version_id"]}


def retire_version(journal, version_id: str, to_state: str, *,
                   reason_code: str, actor: str, at_ms: int) -> dict:
    """DEGRADED or RETIRED; terminal for eligibility."""
    ensure(journal)
    if to_state not in (DEGRADED, RETIRED):
        _refuse("not_a_retirement_state")
    if state_of(journal, version_id) == to_state:
        return {"status": "duplicate", "version_id": version_id}
    return govern_version(journal, {}, version_id, to_state,
                          reason_code=reason_code, actor=actor, at_ms=at_ms)


# ── 4. capacity: one receipt for the exact version, re-evaluated ─────────
def evaluate_capacity(journal, cfg: dict, version_id: str, *,
                      instrument_id: str, market_type: str, as_of_ms: int,
                      registry=None, positions=None, market=None,
                      liquidity_evidence=(), at_ms: int) -> dict:
    """Evaluate and record one strategy-capacity-receipt.v1 for this exact,
    re-verified version. Writes only the receipt; grants nothing."""
    v = load_version(journal, version_id)
    try:
        inputs = cap.gather(journal, cfg, v, instrument_id=instrument_id,
                            market_type=market_type, as_of_ms=as_of_ms,
                            registry=registry, positions=positions,
                            market=market,
                            liquidity_evidence=liquidity_evidence)
        receipt = cap.build(inputs)
        out = cap.record(journal, receipt, at_ms=at_ms)
    except cap.CapacityRefused as e:
        _refuse(e.code)
    return {**out, "version_id": version_id, "status_capacity":
            receipt["status"], "effective": receipt["result"]["effective"]}


def verify_owner_configuration(journal, cfg, request, decision):
    """Consume exact configuration binding; absent historical proof fails closed.

    Gateway envelopes and Factory-native decisions use the same current config.
    This strengthens eligibility only; the execution fences remain unchanged.
    """
    from trader.owner.approvals import digest, exists, receipt_from
    native = decision.get('config_sha256')
    envelopes = journal.query('SELECT payload FROM owner_approval_receipts WHERE item_id=?',
                              (request['request_id'],)) if exists(journal, 'owner_approval_receipts') else []
    if native is None and not envelopes:
        _refuse('owner_approval_configuration_not_recorded')
    if native is not None and native != _jsha(cfg):
        _refuse('approved_configuration_changed')
    if envelopes:
        try:
            envelope = receipt_from(envelopes[0]['payload'], request['request_id'])
            expected = digest(dict(request=request, config_sha256=digest(cfg)))
            if envelope['binding_hash'] != expected:
                _refuse('approved_configuration_changed')
            if (envelope['decision'], envelope['actor']) != (decision['decision'], decision['actor']):
                _refuse('owner_approval_envelope_mismatch')
        except (ValueError, KeyError, TypeError):
            _refuse('owner_approval_envelope_invalid')


# ── 5. the eligibility predicate ─────────────────────────────────────────
@dataclass(frozen=True)
class FirstLiveEligibility:
    version_id: str
    eligible: bool
    reasons: tuple
    capacity: dict
    decision_id: str | None = None


def eligible_for_first_live(journal, version_id: str, *, cfg: dict,
                            available_inputs=None, capacity_receipt_id=None,
                            now_ms: int | None = None,
                            _governor_resume=False) -> FirstLiveEligibility:
    """True only when the immutable version, its validation evidence, its
    exact paper install (still the installed spec), its probation receipt
    and an exact-version owner approval all re-verify now, it is not degraded/retired/rejected, and every input the compiled
    spec requires is in `available_inputs` (None = not asserted = refused),
    and `capacity_receipt_id` names a strategy-capacity-receipt.v1 for this
    exact version that is current at `now_ms` with ESTABLISHED effective
    capacity (None = not asserted = refused).
    Read-only; grants eligibility only."""
    reasons: list[str] = []
    decision_id = None

    def check(fn, *a):
        try:
            return fn(*a)
        except HandoffRefused as e:
            reasons.append(e.code)
            return None

    names = {r["name"] for r in journal.query(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    if not set(_TABLES) <= names:
        return FirstLiveEligibility(version_id, False, ("version_missing",),
                                    CAPACITY)
    v = check(load_version, journal, version_id)
    if v is None:
        return FirstLiveEligibility(version_id, False, tuple(reasons),
                                    CAPACITY)
    reached = [e["to_state"] for e in events(journal, version_id)]
    for bad in (DEGRADED, RETIRED, REJECTED):
        if bad in reached:
            reasons.append(f"version_{bad.lower()}")
    cur = reached[-1] if reached else None
    governed = state_of(journal, version_id)
    if governed != cur and not _governor_resume:
        reasons.append(f"governor_state:{governed}")
    if governed == RETIRED or (governed == DEGRADED and not _governor_resume):
        reasons.append('version_' + governed.lower())
    if cur != APPROVED_FIRST_LIVE:
        reasons.append(f"state_not_approved:{cur}")
    val = check(verify_validation, journal, v)
    check(verify_install, journal, v)          # the paper version IS this one
    req = check(lambda: approval_request(journal, version_id)
                or _refuse("approval_request_missing"))
    if req is not None:
        if (req["version_id"], req["spec_hash"]) != (version_id,
                                                     v["spec_hash"]):
            reasons.append("approval_request_wrong_version")
        if val is not None and req["validation_receipt_id"] != \
                val["receipt_id"]:
            reasons.append("approval_request_stale_validation")
        check(_verify_probation, journal, cfg, v,
              req["probation_receipt_id"])
        drow = _one(journal, "SELECT * FROM strategy_approval_decisions "
                    "WHERE request_id=?", (req["request_id"],))
        d = check(_load, drow) if drow else None
        if d is None:
            reasons.append("owner_approval_missing")
        else:
            ident = {k: d[k] for k in (
                "schema", "request_id", "version_id", "spec_hash",
                "validation_receipt_id", "probation_receipt_id",
                "requested_at_ms", "decision", "actor", "decided_at_ms")}
            bound = {k: req[k] for k in (
                "request_id", "version_id", "spec_hash",
                "validation_receipt_id", "probation_receipt_id",
                "requested_at_ms")}
            if "config_sha256" in d:
                ident["config_sha256"] = d["config_sha256"]
            check(verify_owner_configuration, journal, cfg, req, d)
            if _jsha(ident) != d["decision_id"] or any(
                    d[k] != bound[k] for k in bound):
                reasons.append("owner_approval_wrong_version")
            if d["decision"] != "APPROVED":
                reasons.append("owner_approval_rejected")
            if d["actor"] not in OWNER_ACTORS:
                reasons.append("actor_not_owner")
            decision_id = d["decision_id"]
    need = set(v["spec"].get("data_requires") or [])
    if available_inputs is None:
        reasons.append("inputs_not_asserted")
    elif not need <= set(available_inputs):
        reasons.append("inputs_unavailable:" + ",".join(
            sorted(need - set(available_inputs))))
    capacity = {**CAPACITY, "receipt_id": None, "current": False}
    if capacity_receipt_id is None:
        reasons.append("capacity_receipt_not_asserted")
    else:
        import time
        now = int(time.time() * 1000) if now_ms is None else int(now_ms)
        c = cap.check_current(journal, cfg, v, capacity_receipt_id,
                              now_ms=now)
        capacity = {**CAPACITY, **c}
        reasons.extend(c["reasons"])
    reasons = list(dict.fromkeys(reasons))
    return FirstLiveEligibility(version_id, not reasons, tuple(reasons),
                                capacity, decision_id)


# ── 6. the paper/live authority fence ────────────────────────────────────
def versioned(journal, strategy_id: str) -> bool:
    """Whether `strategy_id` is a factory StrategyVersion's strategy.
    Raises when the version table cannot be read (callers fail closed)."""
    if not strategy_id:
        return False
    names = {r["name"] for r in journal.query(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    if "strategy_versions" not in names:
        return False
    return bool(journal.query("SELECT 1 FROM strategy_versions WHERE "
                              "strategy_id=? LIMIT 1", (strategy_id,)))


def live_entry_block(journal, strategy_id: str) -> str | None:
    """Fail closed for every entry without explicit immutable authority.

    Factory activation is lifecycle authority only in this package. Real
    first-live execution remains disabled, including for ACTIVE versions.
    Historical exceptions require an exact explicit grandfather receipt.
    """
    try:
        if not versioned(journal, strategy_id):
            from .legacy_authority import grandfathered
            return None if grandfathered(journal, strategy_id) else "VERSIONED_AUTHORITY_REQUIRED"
        inst = _installed(journal, strategy_id)
        if inst is None:
            return "version_install_unbound"
        rows = journal.query("SELECT version_id FROM strategy_versions WHERE "
                             "strategy_id=? AND spec_hash=?",
                             (strategy_id, inst[1]))
        if len(rows) != 1:
            return "version_install_unbound"
        vid = rows[0]["version_id"]
        st = state_of(journal, vid)
        if st in ("ACTIVE", "REACTIVATED"):
            return "version_first_live_execution_not_enabled"
        if st != APPROVED_FIRST_LIVE:
            return f"version_not_live_authorized:{st}"
        names = {r["name"] for r in journal.query(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        est = cap.TABLE in names and journal.query(
            f"SELECT 1 FROM {cap.TABLE} WHERE version_id=? AND status=? "
            "LIMIT 1", (vid, cap.ESTABLISHED))
        if not est:
            return "version_capacity_not_established"
        return "version_first_live_activation_absent"
    except Exception as e:                              # noqa: BLE001
        return f"version_authority_unreadable:{type(e).__name__}"


# One post-validation lifecycle authority. These receipts govern lifecycle and
# allocation only; they do NOT change the separate live execution fence.
GOVERNOR_SCHEMA = "strategy-governor-event.v1"
GOVERNOR_ALLOWED = {
    PROPOSED: {DEGRADED, RETIRED},
    VALIDATED: {DEGRADED, RETIRED},
    SHADOW: {DEGRADED, RETIRED},
    APPROVAL_REQUIRED: {DEGRADED, RETIRED},
    APPROVED_FIRST_LIVE: {"ACTIVE", "PAUSED", DEGRADED, RETIRED},
    "ACTIVE": {"ACTIVE", "PAUSED", DEGRADED, RETIRED},
    "REACTIVATED": {"ACTIVE", "PAUSED", DEGRADED, RETIRED},
    "PAUSED": {"REACTIVATED", DEGRADED, RETIRED},
    DEGRADED: {"PAUSED", "REACTIVATED", RETIRED},
    RETIRED: set(),
}


def governor_events(journal, version_id):
    if not journal.query("SELECT 1 FROM sqlite_master WHERE type='table' AND name='strategy_governor_events'"):
        return []
    rows = journal.query("SELECT canonical_json,canonical_sha256 FROM strategy_governor_events WHERE version_id=? ORDER BY seq", (version_id,))
    result, previous = [], None
    for row in rows:
        event = json.loads(row['canonical_json'])
        if (event['version_id'] != version_id or event['spec_hash'] != load_version(journal,version_id)['spec_hash']
                or _sha(row['canonical_json']) != row['canonical_sha256']
                or canonical(event) != row['canonical_json']
                or event['previous_sha256'] != previous):
            _refuse('governor_replay_differs')
        previous = row['canonical_sha256']
        result.append(event)
    return result


def govern_version(journal, cfg, version_id, to_state, *, actor, reason_code,
                   at_ms, allocation=None, available_inputs=None,
                   capacity_receipt_id=None, risk_manager=None, risk_release=None,
                   _connection=None, expected_target=None):
    """The sole lifecycle authority; no caller automatically activates.

    Activation/resume holds control and Risk authority through the exact
    evidence recheck and append. It grants no real execution permission.
    """
    from ..engine.control_fence import control_fence
    from ..engine.risk import RiskManager, RiskRelease, policy_from_config
    from ..core.types import ControlState
    if _connection is not None:
        # Learning can only retire, atomically within its receipt transaction.
        if (to_state != RETIRED or actor != 'strategy_governor'
                or not _connection.in_transaction
                or getattr(journal, 'connection', None) is not _connection):
            _refuse('learning_governor_retirement_transaction_required')
        if expected_target is None or lifecycle_target(journal, version_id) != expected_target:
            _refuse('governor_state_changed')
        return _govern_version_locked(journal, cfg, version_id, to_state,
            actor=actor, reason_code=reason_code, at_ms=at_ms, conn=_connection)
    ensure(journal)
    load_version(journal, version_id)
    activating = to_state in ('ACTIVE', 'REACTIVATED')
    if activating:
        if type(at_ms) is not int or abs(at_ms - int(time.time() * 1000)) > 5000:
            _refuse('governor_clock_not_current')
        if (not isinstance(risk_manager, RiskManager)
                or risk_manager.journal is not journal
                or not isinstance(risk_release, RiskRelease)
                or not risk_release.allowed or not risk_release.authoritative):
            _refuse('governor_risk_prerequisite_required')
        if risk_manager.policy()['digest'] != policy_from_config(cfg)['digest']:
            _refuse('governor_risk_policy_differs')
    with control_fence(journal):
        # Risk's transaction is the commit boundary. Use its connection;
        # opening another journal transaction here would deadlock.
        hold = risk_manager.hold_release(risk_release) if activating else nullcontext((None, None))
        with hold as (refusal, conn):
            if refusal:
                _refuse('governor_risk_refused:' + refusal)
            if activating and risk_manager.policy()['digest'] != policy_from_config(cfg)['digest']:
                _refuse('governor_risk_policy_differs')
            if activating and journal.kv_get('control_state', None) != ControlState.ACTIVE.value:
                _refuse('governor_control_not_active')
            tx = nullcontext(conn) if conn is not None else journal._tx()
            with tx as c:
                _begin(c)
                return _govern_version_locked(journal, cfg, version_id, to_state,
                    actor=actor, reason_code=reason_code, at_ms=at_ms,
                    allocation=allocation, available_inputs=available_inputs,
                    capacity_receipt_id=capacity_receipt_id, conn=c,
                    risk_release=risk_release if activating else None)


def _govern_version_locked(journal, cfg, version_id, to_state, *, actor, reason_code,
                   at_ms, allocation=None, available_inputs=None,
                   capacity_receipt_id=None, conn, risk_release=None):
    v = load_version(journal, version_id)
    if actor not in OWNER_ACTORS and actor != 'strategy_governor' and not (actor == 'factory' and to_state in (DEGRADED,RETIRED)):
        _refuse('governor_actor_invalid')
    if not reason_code or type(at_ms) is not int:
        _refuse('governor_reason_or_clock_missing')
    history = governor_events(journal, version_id)
    cur = state_of(journal, version_id)
    if to_state not in GOVERNOR_ALLOWED.get(cur, set()):
        _refuse('governor_transition_not_allowed')
    if history and at_ms < history[-1]['at_ms']:
        _refuse('governor_clock_regressed')
    ceiling = history[-1]['allocation_ceiling'] if history else None
    risk_sha = history[-1]['risk_sha256'] if history else None
    owner_decision_id = history[-1].get('owner_decision_id') if history else None
    if to_state in ('ACTIVE', 'REACTIVATED'):
        if ceiling is None and actor not in OWNER_ACTORS:
            _refuse('governor_owner_boundaries_required')
        if isinstance(allocation, bool) or not isinstance(allocation, (int,float)) or not math.isfinite(allocation) or not 0 < allocation <= 1:
            _refuse('governor_allocation_invalid')
        if ceiling is None:
            ceiling, risk_sha = allocation, _jsha(cfg['risk'])
        if allocation > ceiling or _jsha(cfg['risk']) != risk_sha:
            _refuse('governor_owner_boundaries_changed')
        eligibility = eligible_for_first_live(journal, version_id, cfg=cfg,
            available_inputs=available_inputs, capacity_receipt_id=capacity_receipt_id, now_ms=int(time.time() * 1000),
            _governor_resume=True)
        if not eligibility.eligible:
            _refuse('governor_evidence_unavailable:' + ','.join(eligibility.reasons))
        owner_decision_id = eligibility.decision_id
    else:
        allocation = 0
    body = dict(schema=GOVERNOR_SCHEMA, version_id=version_id,
        spec_hash=v['spec_hash'], from_state=cur, to_state=to_state, actor=actor,
        reason_code=reason_code, at_ms=at_ms, allocation=allocation,
        allocation_ceiling=ceiling, risk_sha256=risk_sha,
        capacity_receipt_id=capacity_receipt_id, owner_decision_id=owner_decision_id,
        previous_sha256=_sha(canonical(history[-1])) if history else None,
        risk_release=risk_release.as_dict() if risk_release else None,
        available_inputs=sorted(available_inputs) if available_inputs is not None else None,
        grants='LIFECYCLE_ALLOCATION_ONLY; LIVE_EXECUTION_FENCE_PRESERVED')
    text = canonical(body)
    if state_of(journal, version_id) != cur or len(governor_events(journal, version_id)) != len(history):
        _refuse('governor_state_changed')
    local = getattr(journal, '_local', None)
    if local is not None:
        local.governor_write = True
    try:
        conn.execute('INSERT INTO strategy_governor_events(version_id,to_state,canonical_json,canonical_sha256) VALUES(?,?,?,?)',
                     (version_id,to_state,text,_sha(text)))
        if to_state == RETIRED and journal.query("SELECT 1 FROM sqlite_master WHERE name='strategies'"):
            conn.execute("UPDATE strategies SET state='retired', retire_reason=?, state_changed_at=? WHERE id=?",
                         (reason_code, datetime.fromtimestamp(at_ms/1000, timezone.utc).isoformat(), v['strategy_id']))
    finally:
        if local is not None:
            local.governor_write = False
    return dict(status='inserted', version_id=version_id, event=body)


def lifecycle_target(journal, version_id):
    """Exact state plus history token; detects changes even after an ABA resume."""
    version = load_version(journal, version_id)
    return dict(version_id=version_id, spec_hash=version['spec_hash'],
                state=state_of(journal, version_id),
                history_sha256=_jsha(dict(factory=events(journal, version_id),
                                         governor=governor_events(journal, version_id))))
