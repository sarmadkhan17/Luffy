"""Stage-5 handoff: referee-passed candidate -> first-live eligibility.

SDD v3.2 §14.2 names one path:

    research -> spec -> validation/referee -> freeze immutable spec + evidence
    receipt -> shadow/paper -> approval required -> owner approval for the
    exact version/hash -> active

This module closes the part of it between the referee and "active", and
stops short of activation. It reuses what already exists and adds only the
records that were missing:

- the candidate and its gate evidence: `research.ledger` (`research_candidates`
  state + gate JSON, and the error budget's `research_tests` row);
- the spec: the same `Combination.to_spec()` + declared universe that
  `kernel._research_handoff` builds, compiled by `compile_spec`;
- the paper install: `Journal.upsert_spec`, called by the Kernel's
  exact-version handoff with the frozen spec unchanged; the factory reads the
  installed row back and binds it (`strategy_version_installs`);
- the probation policy: `strategies.paper_probation_trades`,
  `paper_min_winrate`, `paper_min_profit_factor` in config.yaml ("trades a
  new strategy must pass in paper before live eligibility"), scored with
  `promotion.stats_of` over closed trades whose Trade Provenance entry
  identity names this exact version;
- owner identity: `engine.state.OWNER_ACTORS`, the only owner actor
  vocabulary the control plane has.

What it adds is append-only (UPDATE/DELETE raise in SQLite): one immutable
version per (strategy, spec hash, parent, source), one validation receipt per
version, probation receipts, one approval request and at most one owner
decision per request, and a lifecycle event log.

Boundaries. Nothing here writes `strategies`, `trades`, control state or any
Kernel input. The Kernel (exact install) and the executor read one predicate,
`live_entry_block`, which fences every versioned strategy away from the real
order path unless it is exact-version first-live eligible — and no activation
path exists, so today it always blocks. `eligible_for_first_live` is a
read-only predicate: it grants ELIGIBILITY only — no activation, allocation,
order or control-state change. Capacity is never frozen into a version: the
version is the strategy alone, and approvals carry a reference to the
strategy-capacity-receipt.v1 contract (`capacity.py`); eligibility requires
a CURRENT receipt for the exact version whose effective capacity is
ESTABLISHED (SDD §14.1 lists a capacity estimate among a strategy's minimum
fields; Stage 5 requires it). Stale or unestablished capacity fails closed.
An investigation research result (e.g. investigation_volume_anomaly, whose
`predictive_edge_established` is always False) is never a source.
"""
from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone

from ..cognition.research_question import canonical
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
    PROPOSED: {DEGRADED, RETIRED},
    VALIDATED: {SHADOW, DEGRADED, RETIRED},
    SHADOW: {APPROVAL_REQUIRED, DEGRADED, RETIRED},
    APPROVAL_REQUIRED: {APPROVED_FIRST_LIVE, REJECTED, DEGRADED, RETIRED},
    APPROVED_FIRST_LIVE: {DEGRADED, RETIRED},
    DEGRADED: {RETIRED},
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
P_INCOMPLETE = "INCOMPLETE_TRADE_IDENTITY"   # a trade's version is unproven

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
           "strategy_approval_decisions", "strategy_version_events")

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
""" + "".join(
    f"""CREATE TRIGGER IF NOT EXISTS {t}_no_update BEFORE UPDATE ON {t}
BEGIN SELECT RAISE(ABORT, '{t} is immutable'); END;
CREATE TRIGGER IF NOT EXISTS {t}_no_delete BEFORE DELETE ON {t}
BEGIN SELECT RAISE(ABORT, '{t} is immutable'); END;
""" for t in _TABLES)


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
        c.executescript(SCHEMA)


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
    return rec


def _receipt_evidence(ev: dict) -> dict:
    """What a receipt binds: every id/hash, the candidate state excluded
    (referee_passed -> reason_passed -> admitted keeps the evidence)."""
    return {"candidate_hash": ev["candidate"]["hash"],
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


def verify_validation(journal, version: dict) -> dict:
    """The version's receipt, re-verified against the ledger as it is now."""
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
        return {"status": "duplicate", "install_id": old["install_id"],
                "version_id": version_id}
    cur = state_of(journal, version_id)
    if cur != VALIDATED:
        _refuse(f"not_validated:{cur}")
    from ..engine.exits import SpecExit
    bad = SpecExit.frozen_unsupported(StrategySpec.from_dict(v["spec"]))
    if bad:
        _refuse(f"unsupported_exit_geometry:{bad}")
    inst = _installed(journal, v["strategy_id"])
    if inst is None:
        _refuse(P_NOT_INSTALLED)
    spec_d, spec_hash = inst
    if spec_hash != v["spec_hash"] or canonical(spec_d) != canonical(
            v["spec"]):
        _refuse(P_INSTALLED_DIFFERS)
    ident = {"schema": INSTALL_SCHEMA, "version_id": version_id,
             "strategy_id": v["strategy_id"], "spec_hash": v["spec_hash"],
             "validation_receipt_id": val["receipt_id"], "mode": mode}
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
    rec = _install_record(journal, version["version_id"])
    if rec is None:
        _refuse("install_missing")
    ident = {k: rec[k] for k in ("schema", "version_id", "strategy_id",
                                 "spec_hash", "validation_receipt_id",
                                 "mode")}
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
    s = (cfg or {}).get("strategies") or {}
    if any(s.get(k) is None for k in POLICY_KEYS):
        return None
    return {"source": "config.yaml strategies.*",
            "min_trades": int(s["paper_probation_trades"]),
            "min_winrate": float(s["paper_min_winrate"]),
            "min_profit_factor": float(s["paper_min_profit_factor"])}


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


def _probation_trades(journal, v: dict, since_ms: int, until_ms: int):
    counted, excluded = [], []
    for t in journal.query("SELECT * FROM trades WHERE strategy_id=? AND "
                           "status='closed' ORDER BY opened_at, id",
                           (v["strategy_id"],)):
        o, cl = _ms(t["opened_at"]), _ms(t["closed_at"])
        if o is None or cl is None or o < since_ms or cl > until_ms:
            continue
        kind = _classify(t, v)
        if kind == "counted" and t["exec_mode"] not in PAPER_EXEC_MODES:
            # this version's trade, but not proven paper execution
            kind = "not_paper_execution" if t["exec_mode"] == "live" \
                else "execution_mode_unproven"
        row = {k: t[k] for k in _TRADE_KEYS}
        if kind == "counted":
            counted.append({**row, "entry_identity_sha256":
                            _sha(t[IDENTITY_COLUMN])})
        else:
            excluded.append({"id": t["id"], "reason": kind})
    return counted, excluded


def _assess(journal, cfg, v: dict, since_ms: int, at_ms: int, *,
            check_install: bool = True) -> dict:
    policy = _policy(cfg)
    base = {"policy": policy, "trades": [], "trades_sha256": _jsha([]),
            "excluded_trades": [], "stats": None,
            "trade_identity": "trade-provenance " + IDENTITY_SCHEMA}
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
    base.update(excluded_trades=excluded)
    if any(e["reason"] in ("unattributed", "execution_mode_unproven")
           for e in excluded):
        # a closed trade of this strategy id whose version or execution mode
        # cannot be proven may be this version's paper trade: counting
        # around it would bias the record
        return {**base, "status": P_INCOMPLETE}
    st = stats_of(trades)
    stats = {"trades": st["trades"], "wins": st["wins"],
             "winrate": st["winrate"], "profit_factor": st["pf"],
             "pnl": st["pnl"], "pnl_source": "journal trades.realized_pnl"}
    if st["trades"] < policy["min_trades"]:
        status = P_INSUFFICIENT
    elif st["winrate"] >= policy["min_winrate"] \
            and st["pf"] >= policy["min_profit_factor"]:
        status = P_SATISFIED
    else:
        status = P_NOT_SATISFIED
    return {**base, "status": status, "trades": trades,
            "trades_sha256": _jsha(trades), "stats": stats}


def evaluate_probation(journal, cfg: dict, version_id: str, *,
                       at_ms: int) -> dict:
    """Record one probation receipt for a SHADOW version under the existing
    config policy, counting only closed trades whose Trade Provenance entry
    identity names this exact version, opened after its exact install.
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
             "since_ms": since, "evaluated_at_ms": int(at_ms)}
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
    ident = {k: rec[k] for k in ("schema", "version_id", "spec_hash",
                                 "validation_receipt_id", "install_id",
                                 "since_ms", "evaluated_at_ms")}
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
             "decided_at_ms": int(decided_at_ms)}
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
    load_version(journal, version_id)
    with journal._tx() as c:
        _begin(c)
        out = _transition(c, version_id, to_state, reason_code, None, actor,
                          at_ms)
    return {"status": out, "version_id": version_id}


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
                            now_ms: int | None = None) -> FirstLiveEligibility:
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
    """Why this strategy may not reach the real order path, or None.

    None only for a strategy that is not a factory version: legacy genomes
    and non-versioned specs keep their existing authority. A versioned
    strategy is blocked unless its exact installed version is first-live
    eligible, and nothing records first-live activation (eligibility is a
    read-only predicate; activation is outside Stage 5 here), so every
    versioned strategy is blocked: SHADOW / probation, awaiting approval,
    approved without ESTABLISHED capacity, and approved with it alike.
    Any read failure blocks."""
    try:
        if not versioned(journal, strategy_id):
            return None
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
