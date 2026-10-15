"""RP-03: authenticated transfer of qualifying research evidence to a Journal.

A predictive experiment runs against its OWN quantitative ledger
(`<bank>-experiments/<experiment_id>/quantitative.db`), seeded from, but never
written back to, the Journal the Factory reads. When that experiment ends
SUPPORTED, its spent look, registered gate evidence and candidate row exist
only there and in the Research Bank. The Factory re-derives its validation
from the Journal it is given (candidate row, the whole error-budget sequence,
the registered budget, the combination), so against the production Journal it
can only refuse `candidate_not_found` — and a later discovery look priced
against that Journal would not know the look was spent.

This module is the research side of that handoff and nothing else:

  authenticate(bank, result_id)   read-only. Replays the Bank result through the
                                  existing predictive authority, then proves the
                                  experiment ledger holds exactly the rows the
                                  Bank retained. Anything not a genuine, current,
                                  SUPPORTED result is an explicit refusal.
  transfer(journal, packet)       one transaction: the registered budget, every
                                  held-out look up to and including the
                                  candidate's, its candidate row and its
                                  combination, copied verbatim (original
                                  sequence numbers and timestamps). Refuses —
                                  writing nothing — if the target Journal's
                                  sequence has diverged, its frozen cut
                                  differs, or the rule is already spent under
                                  other evidence.

It creates no StrategyVersion, installs nothing, grants no authority and
spends no alpha: the looks it copies were spent when they were taken. The
Factory then validates the result with its own, unchanged contract
(`create_version(journal, cfg, predictive_bridge.factory_source(...))`).
"""
from __future__ import annotations

import hashlib
import json
from contextlib import closing
from pathlib import Path

SCHEMA = "research-factory-transfer.v1"
#: the only candidate state predictive evidence qualifies in. `reason_passed`
#: is the discovery-search state behind a gate nothing writes yet; a predictive
#: result never claims it and is never promoted to it here.
SOURCE_STATE = "referee_passed"
_COLUMNS = {
    "research_tests": ("seq", "hash", "tf", "geo", "gate", "p", "alpha_t",
                       "rejected", "braked", "detail", "at"),
    "research_budget": ("id", "alpha", "w0", "registered_at"),
}


class TransferRefused(ValueError):
    """The transfer is refused; `.code` is the stable reason code and the
    target Journal was not written."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _refuse(code: str):
    raise TransferRefused(code)


def _sha(obj) -> str:
    return hashlib.sha256(json.dumps(
        obj, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def experiment_ledger_path(bank_path, experiment_id: str) -> Path:
    """Where the bridge keeps an experiment's quantitative ledger (derived,
    never read from the retained record)."""
    bank = Path(bank_path).resolve()
    return bank.parent / (bank.stem + "-experiments") / experiment_id \
        / "quantitative.db"


# ── authentication ───────────────────────────────────────────────────────
def _bank_result(bank_path, result_id: str):
    from ..cognition.predictive_bank import predictive_extensions
    from . import predictive_receipt as M
    from .predictive_experiment import readonly
    if not isinstance(result_id, str) or not result_id:
        _refuse("research_result_required")
    if not isinstance(bank_path, (str, Path)) or not Path(bank_path).is_file():
        _refuse("research_bank_required")
    try:
        with closing(readonly(bank_path)) as db:
            held = [json.loads(p) for (p,) in db.execute(
                "SELECT payload FROM bridge_bank_results ORDER BY rowid")]
        first = [r for r in held if r.get("result_id") == result_id]
        if len(first) != 1:
            _refuse("research_result_not_found")
        att = predictive_extensions(bank_path, first[0]["bank_id"])
    except TransferRefused:
        raise
    except Exception:                                   # noqa: BLE001
        _refuse("research_bank_unauthenticated")
    res = [r for r in att["results"] if r["result_id"] == result_id]
    if len(res) != 1:
        _refuse("research_result_not_found")
    r = res[0]
    if r.get("classification") != "SUPPORTED":
        # research-only, unsupported, inconclusive, data-insufficient: no
        # candidate qualifies; nothing to transfer
        _refuse("research_only_not_supported:" + str(r.get("classification")))
    if (r.get("quantitative_outcome") != "PASS"
            or r.get("predictive_strategy_validation") is not True
            or r.get("schema") != M.SCHEMA
            or r.get("authority") != "RESEARCH_ONLY"):
        _refuse("research_result_not_supported")
    exps = [e for e in att["experiments"]
            if e["source_hypothesis_id"] == r["hypothesis_id"]]
    ex = next((e for e in exps if e["experiment_id"] == r["experiment_id"]),
              None)
    if ex is None:
        _refuse("research_bank_experiment_not_found")
    if exps[-1]["experiment_id"] != r["experiment_id"]:
        _refuse("research_result_stale")
    return r, ex


def _rows(db, sql: str, args=()):
    cur = db.execute(sql, args)
    names = [d[0] for d in cur.description]
    return [dict(zip(names, row)) for row in cur.fetchall()]


def authenticate(bank_path, result_id: str) -> dict:
    """The transferable packet for one genuine SUPPORTED Bank result, or a
    `TransferRefused`. Read-only: neither the Bank nor any ledger is written."""
    from .predictive_experiment import readonly
    r, ex = _bank_result(bank_path, result_id)
    fc = (r.get("referee_disposition") or {}).get("factory_candidate") or {}
    ev = (r.get("referee_disposition") or {}).get("evidence") or {}
    h = fc.get("hash")
    if not h or fc.get("experiment_id") != r["experiment_id"] \
            or fc.get("hypothesis_id") != r["hypothesis_id"] \
            or (ev.get("candidate") or {}).get("hash") != h:
        _refuse("research_candidate_missing")
    if (ev.get("candidate") or {}).get("state") != SOURCE_STATE:
        _refuse("candidate_state_not_qualifying:"
                + str((ev.get("candidate") or {}).get("state")))
    # The Bank result retains the experiment ledger's registered evidence
    # (budget, every spent look, every candidate row), hash-identified and
    # replayed by the predictive authority above. That, not the ledger file,
    # is what is transferred.
    qe = r.get("quantitative_evidence") or {}
    tests = [dict(t) for t in qe.get("spent_looks") or []]
    budget = [dict(b) for b in qe.get("registered_budget") or []]
    looks = [t for t in tests if t["hash"] == h and t["gate"] == "gate1"]
    if len(looks) != 1:
        _refuse("gate1_look_missing" if not looks else "gate1_look_ambiguous")
    look = looks[0]
    upto = [t for t in tests if t["seq"] <= look["seq"]]
    if [t["seq"] for t in tests] != sorted({t["seq"] for t in tests}):
        _refuse("bank_sequence_malformed")
    if len(budget) != 1 or budget[0]["id"] != 1:
        _refuse("error_budget_unregistered")
    cand = [dict(c) for c in qe.get("candidates") or [] if c["hash"] == h]
    if len(cand) != 1 or cand[0]["state"] != SOURCE_STATE \
            or (cand[0]["tf"], cand[0]["geo"]) != (look["tf"], look["geo"]):
        _refuse("bank_candidate_differs_from_evidence")
    try:
        same = (json.loads(cand[0]["gate1"]) == ev["gate1"]
                and json.loads(cand[0]["gate3"]) == ev["gate3"]
                and all(look[k] == ev["gate1_look"][k]
                        for k in ev["gate1_look"]))
    except (KeyError, TypeError, ValueError):
        same = False
    if not same:
        _refuse("bank_candidate_differs_from_evidence")
    # the one row the Bank does not retain whole: the combination's ledger row,
    # accepted only if its result is exactly the retained, replayed one
    retained = [x for x in qe.get("results") or [] if x["hash"] == h]
    path = experiment_ledger_path(bank_path, r["experiment_id"])
    if path.is_symlink() or not path.is_file():
        _refuse("experiment_ledger_missing")
    try:
        with closing(readonly(path)) as led:
            bound = led.execute(
                "SELECT id FROM predictive_experiment_binding").fetchall()
            combo = _rows(led, "SELECT * FROM research_combos WHERE hash=?",
                          (h,))
    except Exception:                                   # noqa: BLE001
        _refuse("experiment_ledger_unreadable")
    if bound != [(r["experiment_id"],)]:
        _refuse("experiment_ledger_binding")
    if len(combo) != 1 or len(retained) != 1 or {
            "hash": combo[0]["hash"], "result": combo[0]["result"]} \
            != {"hash": retained[0]["hash"], "result": retained[0]["result"]}:
        _refuse("experiment_combo_differs_from_bank")
    packet = {"schema": SCHEMA, "result_id": r["result_id"],
              "hypothesis_id": r["hypothesis_id"],
              "experiment_id": r["experiment_id"], "hash": h,
              "tf": look["tf"], "geo": look["geo"],
              "look_seq": look["seq"], "tests": upto, "budget": budget[0],
              "candidate": cand[0], "combo": combo[0]}
    packet["transfer_id"] = _sha({k: v for k, v in packet.items()})
    return packet


# ── transfer ─────────────────────────────────────────────────────────────
def _cut(detail) -> int | None:
    try:
        c = (json.loads(detail or "{}") or {}).get("cut_ms")
    except (TypeError, ValueError, AttributeError):
        return None
    return int(c) if c else None


def transfer(journal, packet: dict) -> dict:
    """Copy the packet's evidence into `journal` in one transaction.

    `inserted` or `duplicate` (everything already there, byte for byte);
    every other outcome is a `TransferRefused` raised before any write."""
    from .ledger import Ledger
    if not isinstance(packet, dict) or packet.get("schema") != SCHEMA \
            or packet.get("transfer_id") != _sha(
                {k: v for k, v in packet.items() if k != "transfer_id"}):
        _refuse("transfer_packet_identity")
    Ledger(journal).ensure()
    h, tf, seq = packet["hash"], packet["tf"], packet["look_seq"]
    with journal._tx() as c:
        if not c.in_transaction:
            c.execute("BEGIN IMMEDIATE")

        def fetch(sql, args=()):
            cur = c.execute(sql, args)
            names = [d[0] for d in cur.description]
            return [dict(zip(names, row)) for row in cur.fetchall()]

        # 1. one registered budget
        have = fetch("SELECT * FROM research_budget WHERE id=1")
        if have and have[0] != packet["budget"]:
            _refuse("error_budget_differs")
        # 2. the sequence up to the look is identical, or absent: a fork here
        #    would price one LORD index twice
        mine = {t["seq"]: t for t in fetch(
            "SELECT * FROM research_tests WHERE seq<=? ORDER BY seq", (seq,))}
        want = {t["seq"]: t for t in packet["tests"]}
        for s, row in mine.items():
            if want.get(s) != row:
                _refuse("budget_sequence_diverged")
        # 3. the rule is spent exactly once, under this evidence
        spent = fetch("SELECT seq FROM research_tests WHERE hash=? AND "
                      "gate='gate1'", (h,))
        if any(r["seq"] != seq for r in spent):
            _refuse("holdout_already_spent")
        # 4. the frozen cut is the one this look was taken at
        target_cuts = [x for x in (_cut(r["detail"]) for r in fetch(
            "SELECT detail FROM research_tests WHERE tf=?", (tf,))) if x]
        packet_cuts = [x for x in (_cut(t["detail"]) for t in packet["tests"]
                                   if t["tf"] == tf) if x]
        if target_cuts and packet_cuts and min(target_cuts) != min(packet_cuts):
            _refuse("frozen_cut_differs")
        # 5. candidate and combination: identical or absent, never overwritten
        cand = fetch("SELECT * FROM research_candidates WHERE hash=?", (h,))
        if cand:
            keys = ("tf", "geo", "gate1", "gate3")
            if any(cand[0][k] != packet["candidate"][k] for k in keys):
                _refuse("candidate_conflict")
        combo = fetch("SELECT * FROM research_combos WHERE hash=?", (h,))
        if combo and combo[0] != packet["combo"]:
            _refuse("combo_conflict")

        inserted = 0
        if not have:
            c.execute("INSERT INTO research_budget VALUES (?,?,?,?)",
                      tuple(packet["budget"][k]
                            for k in _COLUMNS["research_budget"]))
            inserted += 1
        cols = _COLUMNS["research_tests"]
        for s in sorted(want):
            if s not in mine:
                c.execute(f"INSERT INTO research_tests({','.join(cols)}) "
                          f"VALUES ({','.join('?' * len(cols))})",
                          tuple(want[s][k] for k in cols))
                inserted += 1
        for table, row, present in (("research_combos", packet["combo"], combo),
                                    ("research_candidates", packet["candidate"],
                                     cand)):
            if not present:
                names = list(row)
                c.execute(f"INSERT INTO {table}({','.join(names)}) VALUES "
                          f"({','.join('?' * len(names))})",
                          tuple(row[k] for k in names))
                inserted += 1
    return {"status": "inserted" if inserted else "duplicate",
            "transfer_id": packet["transfer_id"], "hash": h, "look_seq": seq,
            "rows_written": inserted}
