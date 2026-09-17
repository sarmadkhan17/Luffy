"""Discovery-only dossier for one research survivor, with its thesis policed.

    ./venv/bin/python scripts/candidate_dossier.py --db data/luffy.db \\
        --hash 55243573515adc3b --output-dir /tmp/luffy-candidate-dossier-20260915 \\
        [--thesis thesis.json]

Writes dossier.json, dossier.md, manifest.json and either thesis.input.json
(the supplied bytes, verbatim) or thesis.scaffold.json into an EMPTY directory.

Research only. It reads discovery columns of the research ledger through an
explicit allowlist that SQLite itself enforces (an authorizer denies every
other table and column: research_tests, research_candidates, the combo's
`result` JSON, control `detail`, candles, refs). It writes nothing to the
database, spends no error budget, has no gate-writing path, and can never
emit `reason_passed` or `ready_for_admission_review`. No candidate enters
demo from this tool.

Spec: docs/superpowers/specs/2026-09-15-demo-first-intelligence.md,
"First deliverable".
"""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from trader.data.references import REFS                         # noqa: E402
from trader.research import thesis as th                        # noqa: E402
from trader.research.combo import SCHEMA_VERSION as COMBO_SCHEMA  # noqa: E402
from trader.research.vocab import BOOL_PARTS, _fmt, gauges_for   # noqa: E402
from trader.strategy import dsl                                 # noqa: E402

DOSSIER_SCHEMA = "luffy.candidate_dossier.v1"
TOOL_VERSION = "2"          # 2: premise prose is never promoted to supported
OUTPUT_FILES = ("dossier.json", "dossier.md", "manifest.json")
MAX_THESIS_BYTES = 256_000

#: the ONLY cells this tool may read. Enforced by sqlite's authorizer.
ALLOWED_COLUMNS = {
    "research_combos": {"hash", "tf", "geo", "k", "round", "parent", "trigger",
                        "window", "parts", "entry_long", "entry_short",
                        "status", "label", "verdict", "reason",
                        "consistency_p", "median_pf", "total_pct",
                        "max_dd_pct", "trades", "scored_symbols", "testable",
                        "ablation", "created_at"},
    "research_slices": {"tf", "cut_ms", "counts", "measured_at"},
    "research_gauges": {"tf", "expr", "p10", "p25", "p75", "p90", "min", "max",
                        "n", "finite_frac", "usable", "measured_at"},
    "research_controls": {"tf", "window", "consistency_p", "powered", "status",
                          "measured_at"},
}
ABLATION_FIELDS = ("p", "total_pct", "verdict", "drop_p", "drop_pct")
NOT_READ = ("research_tests", "research_candidates", "research_archive",
            "research_combos.result", "research_controls.detail",
            "research_slices.counts.heldout_*", "candles", "refs")
SCOPE_NOTICE = ("RESEARCH ONLY. Discovery evidence only; no held-out number, "
                "referee or gate record was read. This tool writes no gate, "
                "spends no budget, and no candidate enters demo from it.")

_HASH = re.compile(r"^[0-9a-f]{16}$")
_PART_KEY = re.compile(r"^[A-Za-z0-9_:]+([<>]p(10|25|75|90))?$")
_GAUGE_KEY = re.compile(r"^(?P<g>[a-z0-9_]+)(?P<op>[<>])p(?P<q>10|25|75|90)$")


class DossierError(Exception):
    """A user-facing refusal: bad arguments, unsafe paths, missing rows."""


# ── read-only access ─────────────────────────────────────────────────────
def _authorizer(action, arg1, arg2, dbname, source):
    if action == sqlite3.SQLITE_READ:
        ok = arg2 in ALLOWED_COLUMNS.get(arg1, ())
        return sqlite3.SQLITE_OK if ok else sqlite3.SQLITE_DENY
    if action in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_TRANSACTION):
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_FUNCTION and arg2 in ("json_extract", "count"):
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


def connect_readonly(db: Path) -> sqlite3.Connection:
    """mode=ro URI + query_only + allowlist authorizer. Never creates a file."""
    path = Path(db).resolve()
    if not path.is_file():
        raise DossierError(f"database not found: {path}")
    conn = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True,
                           isolation_level=None)
    conn.execute("PRAGMA query_only = ON")
    conn.set_authorizer(_authorizer)
    return conn


COMBO_SQL = ("SELECT hash, tf, geo, k, round, parent, trigger, window, parts, "
             "entry_long, entry_short, status, label, verdict, reason, "
             "consistency_p, median_pf, total_pct, max_dd_pct, trades, "
             "scored_symbols, testable, created_at "
             "FROM research_combos WHERE hash = ?")
ABLATION_SQL = ("SELECT json_extract(ablation, ?) FROM research_combos "
                "WHERE hash = ?")
LEDGER_SQL = ("SELECT geo, verdict, COUNT(*) FROM research_combos "
              "WHERE tf = ? GROUP BY geo, verdict ORDER BY geo, verdict")
SLICE_SQL = ("SELECT tf, cut_ms, json_extract(counts, '$.discovery'), "
             "measured_at FROM research_slices WHERE tf = ?")
GAUGE_SQL = ("SELECT expr, p10, p25, p75, p90, min, max, n, finite_frac, "
             "usable, measured_at FROM research_gauges WHERE tf = ? AND expr = ?")
CONTROL_SQL = ("SELECT window, consistency_p, powered, status, measured_at "
               "FROM research_controls WHERE tf = ? ORDER BY window")


def read_ledger(db: Path, cand: str) -> dict:
    """Every read in one transaction; the connection is closed on return."""
    conn = connect_readonly(db)
    queries = []
    try:
        conn.execute("BEGIN")

        def q(sql, args):
            queries.append(sql)
            return conn.execute(sql, args).fetchall()

        rows = q(COMBO_SQL, (cand,))
        if not rows:
            raise DossierError(f"no research_combos row for hash {cand}")
        cols = [c.strip() for c in COMBO_SQL[7:COMBO_SQL.index(" FROM")]
                .split(",")]
        combo = dict(zip(cols, rows[0]))
        parts = json.loads(combo["parts"] or "[]")
        if not isinstance(parts, list) or not parts or \
                not all(isinstance(p, str) and _PART_KEY.match(p) for p in parts):
            raise DossierError("parts column is not a list of canonical keys")
        combo["parts"] = parts
        ablation = {}
        for key in parts:
            ablation[key] = {}
            for f in ABLATION_FIELDS:
                v = q(ABLATION_SQL, (f'$."{key}".{f}', cand))[0][0]
                if v is not None and not isinstance(v, (int, float, str)):
                    raise DossierError(f"ablation {key}.{f} is not a scalar")
                ablation[key][f] = v
        tf = combo["tf"]
        ledger = [{"geo": g, "verdict": v, "n": int(n)}
                  for g, v, n in q(LEDGER_SQL, (tf,))]
        srow = q(SLICE_SQL, (tf,))
        if not srow:
            raise DossierError(f"no research_slices row for tf {tf}")
        disc = json.loads(srow[0][2] or "null")
        if not isinstance(disc, dict) or not disc or not all(
                isinstance(k, str) and isinstance(v, int) and
                not isinstance(v, bool) for k, v in disc.items()):
            raise DossierError("discovery slice counts are not {symbol: bars}")
        slices = {"cut_ms": int(srow[0][1]), "discovery": disc,
                  "measured_at": srow[0][3]}
        gauges = {}
        for expr in _gauge_exprs(parts, tf):
            r = q(GAUGE_SQL, (tf, expr))
            if r:
                gauges[expr] = dict(zip(
                    ("expr", "p10", "p25", "p75", "p90", "min", "max", "n",
                     "finite_frac", "usable", "measured_at"), r[0]))
        controls = {w: {"consistency_p": p, "powered": bool(pw),
                        "status": st, "measured_at": at}
                    for w, p, pw, st, at in q(CONTROL_SQL, (tf,))}
        conn.execute("ROLLBACK")
    finally:
        conn.close()
    return {"combo": combo, "ablation": ablation, "ledger": ledger,
            "slices": slices, "gauges": gauges, "controls": controls,
            "queries": queries}


def _gauge_for(key: str, tf: str):
    m = _GAUGE_KEY.match(key)
    if not m:
        return None, None, None
    g = next((g for g in gauges_for(tf) if g.key == m["g"]), None)
    return g, m["op"], f"p{m['q']}"


def _gauge_exprs(parts: list, tf: str) -> list:
    out = []
    for key in parts:
        g, _, _ = _gauge_for(key, tf)
        if g:
            out += [e for e in (g.long, g.short) if e not in out]
    return out


# ── the dossier ──────────────────────────────────────────────────────────
def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc) \
        .strftime("%Y-%m-%dT%H:%M:%SZ")


def _reconstruct(raw: dict) -> tuple[list, list]:
    """Rebuild each part's long/short text from the vocabulary and the stored
    thresholds, and check the stored rule is exactly those parts."""
    combo, tf = raw["combo"], raw["combo"]["tf"]
    problems, parts = [], []
    bools = {p.key: p for p in BOOL_PARTS}
    for key in combo["parts"]:
        if key in bools:
            p = bools[key]
            parts.append({"key": key, "kind": p.kind, "long": p.long,
                          "short": p.short})
            continue
        g, op, q = _gauge_for(key, tf)
        a = raw["gauges"].get(g.long) if g else None
        b = raw["gauges"].get(g.short) if g else None
        if not g or not a or not b or a[q] is None or b[q] is None:
            problems.append(f"part {key}: gauge or threshold not resolvable")
            continue
        parts.append({"key": key, "kind": "gauge", "gauge": g.key,
                      "long": f"{g.long} {op} {_fmt(a[q])}",
                      "short": f"{g.short} {op} {_fmt(b[q])}"})
    if len(parts) == len(combo["parts"]):
        for side in th.LEGS:
            texts = [p[side] for p in parts]
            if not any(" and ".join(perm) == combo[f"entry_{side}"]
                       for perm in itertools.permutations(texts)):
                problems.append(f"entry_{side} is not exactly the parts' "
                                "rendered thresholds")
    blob = json.dumps({"v": COMBO_SCHEMA, "tf": tf, "geo": combo["geo"],
                       "dir": "both", "parts": sorted(combo["parts"])},
                      sort_keys=True)
    if hashlib.sha256(blob.encode()).hexdigest()[:16] != combo["hash"]:
        problems.append("canonical hash does not recompute from parts")
    if combo["k"] != len(combo["parts"]):
        problems.append("k does not equal the number of parts")
    return parts, problems


def _evidence(raw: dict, parts: list, requires: list) -> list:
    c, tf, h = raw["combo"], raw["combo"]["tf"], raw["combo"]["hash"]
    ev = []

    def add(eid, source, value):
        ev.append({"id": eid, "source": source, "value": value})

    for col in ("verdict", "label", "status", "reason", "consistency_p",
                "median_pf", "total_pct", "max_dd_pct", "trades",
                "scored_symbols", "testable", "window", "round", "parent",
                "trigger", "created_at"):
        v = bool(c[col]) if col == "testable" else c[col]
        add(f"E.combo.{col}", f"research_combos.{col} WHERE hash={h}", v)
    for key, fields in raw["ablation"].items():
        for f, v in fields.items():
            add(f"E.ablation.{key}.{f}",
                f"research_combos.ablation[{key}].{f} WHERE hash={h}", v)
    for g in sorted({(r["geo"]) for r in raw["ledger"]}):
        for r in raw["ledger"]:
            if r["geo"] == g:
                add(f"E.ledger.{g}.{r['verdict']}",
                    f"COUNT research_combos WHERE tf={tf} geo={g} "
                    f"verdict={r['verdict']}", r["n"])
    s = raw["slices"]
    bars = list(s["discovery"].values())
    add("E.slice.cut_ms", f"research_slices.cut_ms WHERE tf={tf}", s["cut_ms"])
    add("E.slice.discovery.symbols",
        f"research_slices.counts.discovery WHERE tf={tf} (count)", len(bars))
    add("E.slice.discovery.min_bars", "same, min bars", min(bars))
    add("E.slice.discovery.max_bars", "same, max bars", max(bars))
    for p in parts:
        if p["kind"] != "gauge":
            continue
        g, op, q = _gauge_for(p["key"], tf)
        for side, expr in (("long", g.long), ("short", g.short)):
            row = raw["gauges"][expr]
            for f in (q, "n", "finite_frac", "usable"):
                add(f"E.gauge.{g.key}.{side}.{f}",
                    f"research_gauges.{f} WHERE tf={tf} expr={expr}",
                    bool(row[f]) if f == "usable" else row[f])
    window = th.window_key(requires)
    ctl = raw["controls"].get(window)
    if ctl:
        add(f"E.control.{window}.powered",
            f"research_controls.powered WHERE tf={tf} window={window}",
            ctl["powered"])
        add(f"E.control.{window}.consistency_p",
            f"research_controls.consistency_p WHERE tf={tf} window={window}",
            ctl["consistency_p"])
    for r in requires:
        if r.startswith("ref:") and r[4:] in REFS:
            ref = REFS[r[4:]]
            for f in ("source", "tf", "close_after_ms", "max_stale_ms",
                      "close_only"):
                add(f"E.code.ref.{ref.key}.{f}",
                    f"trader/data/references.py REFS[{ref.key!r}].{f}",
                    getattr(ref, f))
    return ev


def _rivals(raw: dict, parts: list, requires: list, ev: dict) -> list:
    c = raw["combo"]
    market_wide = bool(parts) and all(
        p.get("gauge", "").startswith("r_") for p in parts)
    full_p, full_pct = c["consistency_p"], c["total_pct"]
    abl = raw["ablation"].values()
    worse = full_p is not None and full_pct is not None and bool(abl) and all(
        isinstance(a.get("p"), (int, float)) and a["p"] > full_p and
        isinstance(a.get("total_pct"), (int, float)) and
        a["total_pct"] < full_pct for a in abl)
    total = sum(r["n"] for r in raw["ledger"])
    surv = sum(r["n"] for r in raw["ledger"] if r["verdict"] == "survivor")
    out = [{
        "kind": "shared_market_regime",
        "applies": "strongly" if market_wide else "yes",
        "text": ("Every part reads a market-wide reference, so all "
                 f"{c['scored_symbols']} discovery symbols get the same "
                 "condition on the same bar; one market-wide episode can lift "
                 "many correlated symbols and be counted once per symbol."
                 if market_wide else
                 "Correlated symbols can share one market episode and be "
                 "counted repeatedly."),
        "evidence_ids": ["E.combo.scored_symbols", "E.combo.trades",
                         "E.combo.consistency_p"],
        "evidence_against_candidate": (
            "consistency_p is a pooled across-symbol figure that does not "
            "correct for cross-symbol dependence; its size is not evidence "
            "against this rival."),
    }, {
        "kind": "selection_from_many_variants",
        "applies": "yes",
        "text": (f"{total} combinations were evaluated at {c['tf']} and "
                 f"{surv} survived; each part's threshold is one of four "
                 "measured quantiles. The best of many looks on one slice "
                 "can be lucky."),
        "evidence_ids": sorted(k for k in ev if k.startswith(
            ("E.ledger.", "E.ablation."))),
        "evidence_for_candidate_on_discovery_only": (
            ("the ablation records that removing any one part raised p and "
             "lowered total return; " if worse else
             "the ablation does not show every removal worsening both p and "
             "total return; ") +
            "the same slice that selected the rule measured this, so it does "
            "not answer selection."),
    }]
    for r in requires:
        if not r.startswith("ref:") or r[4:] not in REFS:
            continue
        ref = REFS[r[4:]]
        if ref.source == "computed":
            text = (f"ref {ref.key!r} is computed in-house (equal-weight alt "
                    "perp index, close-only). Its membership is not recorded "
                    "in the ledger, so whether it contains the traded "
                    "discovery symbols — making the rule partly condition on "
                    "its own markets — is unverified here.")
        else:
            text = (f"ref {ref.key!r} comes from {ref.source} at {ref.tf} bars, "
                    f"known {ref.close_after_ms // 3_600_000}h after its stamp "
                    f"and live for up to {ref.max_stale_ms // 3_600_000}h; at "
                    f"{c['tf']} one value spans several bars, so trades "
                    "cluster on few distinct reference states.")
        out.append({"kind": "data_provenance", "applies": "yes", "text": text,
                    "evidence_ids": sorted(k for k in ev
                                           if k.startswith(f"E.code.ref.{ref.key}."))})
    return out


def scaffold(ctx: dict) -> dict:
    """A deliberately INCOMPLETE thesis. Every placeholder fails validation,
    so it cannot be submitted as-is; nothing in it is a claim."""
    fill = "<fill: {}>"
    return {
        "schema": th.SCHEMA,
        "candidate_hash": ctx["candidate_hash"],
        "identity_sha256": ctx["identity_sha256"],
        "evidence_sha256": ctx["evidence_sha256"],
        "mechanism": {"tag": fill.format("snake_case tag"),
                      "statement": fill.format("falsifiable proposal; mark "
                                               "every unobserved premise"),
                      "premise_ids": ["P1"]},
        "premises": [
            {"id": "P1", "kind": "proposed",
             "text": fill.format("unobserved premise"), "evidence_ids": []},
            {"id": "P2", "kind": "observed",
             "text": fill.format("omit; or exactly the supported_statement "
                                 "the validator renders — other wording "
                                 "stays unverified"),
             "claim": {"evidence_id": fill.format("an id from dossier "
                                                  "evidence"),
                       "op": "==", "value": None}},
        ],
        "rivals": [{"id": f"R{i + 1}", "kind": k,
                    "text": fill.format(f"{k} rival, specific to this rule"),
                    "evidence_ids": []}
                   for i, k in enumerate(list(th.REQUIRED_RIVALS) + (
                       ["data_provenance"]
                       if ctx["provenance_rival_required"] else []))],
        "predictions": [
            {"id": f"H{i}", "condition": fill.format(
                "DSL boolean, or replace with subset {kind, value}"),
             "metric": fill.format("|".join(th.METRICS)),
             "relation": fill.format("> or <"),
             "baseline": fill.format("complement or all"),
             "premise_ids": ["P1"], "distinguishes": ["R1"]}
            for i in (1, 2)],
        "kill_condition": fill.format("forward result that retires it"),
    }


def _next_test(raw: dict, report: dict | None) -> dict:
    c, cut = raw["combo"], raw["slices"]["cut_ms"]
    parts = c["parts"]
    accepted = [p for p in (report or {}).get("predictions", [])
                if p["status"] == "accepted_for_testing"]
    return {
        "question": ("does the combination carry information beyond the "
                     "shared market state, and does that survive outside "
                     "discovery?"),
        "distinguishes": ["combination adds information",
                          "shared_market_regime", "selection_from_many_variants"],
        "already_seen": {
            "evidence_ids": sorted(f"E.ablation.{k}.{f}" for k in parts
                                   for f in ("p", "total_pct", "drop_p",
                                             "drop_pct")),
            "note": ("the discovery ablation already records the with/without "
                     "comparison for each part; restating it is not a novel "
                     "consequence"),
        },
        "required_test": (
            "predeclared incremental comparison, full rule vs each "
            f"single-part ablation ({', '.join(parts)} removed in turn), on "
            "held-out A (unseen markets) and held-out B (bars at/after "
            f"{_iso(cut)}), under the dependence-respecting common-rotation "
            "null of gate 1, each look charged to the LORD++ budget"),
        "thesis_predictions_to_test": [p["id"] for p in accepted],
        "data_needed": (f"{c['tf']} candles for held-out A/B symbols and the "
                        f"{c['window']} reference series over the same span; "
                        "none of it is read by this tool"),
        "evidence_status": {
            "discovery": "seen: every number in this dossier",
            "heldout_a": "not read; whether a look was already spent is "
                         "not consulted here",
            "heldout_b": "not read; whether a look was already spent is "
                         "not consulted here"},
        "gate": ("gate 1 (trader/research/referee.py:gate1), then gate 2 "
                 "prediction tests (not implemented), then gate 3; the kernel "
                 "handoff reads only reason_passed"),
        "blockers": [
            "referee stop: research.referee=false pending the gate decision "
            "(phase-3 plan, 2026-09-14 Task 9 and 2026-09-15 addendum)",
            "gate 2 (thesis prediction test) is not implemented",
        ] + ([] if accepted else ["no thesis prediction accepted for testing"]),
    }


def load_thesis(path: Path) -> tuple[bytes, object, str | None]:
    data = Path(path).read_bytes()
    if len(data) > MAX_THESIS_BYTES:
        return data, None, f"thesis file exceeds {MAX_THESIS_BYTES} bytes"

    def pairs(kv):
        keys = [k for k, _ in kv]
        if len(set(keys)) != len(keys):
            raise ValueError("duplicate JSON key")
        return dict(kv)

    def const(name):
        raise ValueError(f"non-finite number {name}")

    try:
        return data, json.loads(data.decode("utf-8"), object_pairs_hook=pairs,
                                parse_constant=const), None
    except (UnicodeDecodeError, ValueError) as e:
        return data, None, f"thesis is not strict JSON: {e}"


def build(raw: dict, thesis_obj=None, thesis_error: str | None = None,
          thesis_given: bool = False) -> dict:
    c = raw["combo"]
    parts, problems = _reconstruct(raw)
    try:
        requires = list(dsl.data_requires(
            *[dsl.parse(p[s]) for p in parts for s in th.LEGS]))
    except dsl.SpecError as e:
        requires, problems = [], problems + [f"rule does not parse: {e}"]
    if requires and th.window_key(requires) != c["window"]:
        problems.append("stored window differs from the rule's derived data")
    ev_list = _evidence(raw, parts, requires) if not problems else \
        _evidence(raw, [], requires)
    ev = {e["id"]: e["value"] for e in ev_list}
    identity = {"dossier_schema": DOSSIER_SCHEMA, "combo_schema": COMBO_SCHEMA,
                "hash": c["hash"], "tf": c["tf"], "geo": c["geo"], "k": c["k"],
                "direction": "both", "window": c["window"],
                "parts": c["parts"], "entry_long": c["entry_long"],
                "entry_short": c["entry_short"], "round": c["round"],
                "parent": c["parent"], "trigger": c["trigger"],
                "created_at": c["created_at"], "cut_ms": raw["slices"]["cut_ms"],
                "discovery_symbols": sorted(raw["slices"]["discovery"])}
    identity_sha = th.sha256_json(identity)
    evidence_sha = th.sha256_json(ev_list)
    ctx = {"candidate_hash": c["hash"], "identity_sha256": identity_sha,
           "evidence_sha256": evidence_sha, "evidence": ev,
           "rule": {"parts": parts, "entry_long": c["entry_long"],
                    "entry_short": c["entry_short"]},
           "requires": requires,
           "discovery_symbols": sorted(raw["slices"]["discovery"]),
           "cut_ms": raw["slices"]["cut_ms"],
           "controls": {w: v["powered"] for w, v in raw["controls"].items()},
           "provenance_rival_required": any(r.startswith("ref:")
                                            for r in requires)}

    report = None
    if thesis_given:
        if thesis_error or problems:
            report = {"schema": th.SCHEMA, "status": "rejected",
                      "errors": [{"code": "thesis_unparseable" if thesis_error
                                  else "dossier_integrity", "path": "$",
                                  "detail": thesis_error or "; ".join(problems)}],
                      "premises": [], "rivals": [], "predictions": [],
                      "review_items": [], "thesis_sha256": None,
                      "mechanism_status": "absent"}
        else:
            report = th.validate(thesis_obj, ctx)
    v = th.verdict(survivor=c["verdict"] == "survivor",
                   survivor_label=c["verdict"], thesis_report=report)
    if problems:
        v.update(state="research_only",
                 blocking_condition="dossier integrity: " + "; ".join(problems),
                 next_action="resolve the ledger/vocabulary mismatch before "
                             "any thesis")

    window = th.window_key(requires) if requires else c["window"]
    ctl = raw["controls"].get(window)
    gauges_cov = {}
    for p in parts:
        if p["kind"] == "gauge":
            g, _, q = _gauge_for(p["key"], c["tf"])
            gauges_cov[p["key"]] = {
                side: {f: raw["gauges"][e][f] for f in
                       ("n", "finite_frac", "usable", "measured_at")}
                for side, e in (("long", g.long), ("short", g.short))}
    dossier = {
        "schema": DOSSIER_SCHEMA, "tool_version": TOOL_VERSION,
        "scope": {"notice": SCOPE_NOTICE, "discovery_only": True,
                  "demo_eligible": False, "not_read": list(NOT_READ)},
        "identity": identity, "identity_sha256": identity_sha,
        "evidence_sha256": evidence_sha, "integrity_problems": problems,
        "evidence": ev_list,
        "observed_pattern": {
            "label": "OBSERVED — ledger facts, discovery slice only",
            "rule": {"parts": parts, "entry_long": c["entry_long"],
                     "entry_short": c["entry_short"], "geometry": c["geo"],
                     "timeframe": c["tf"]},
            "discovery_facts": sorted(k for k in ev if k.startswith(
                ("E.combo.", "E.ablation."))),
            "code_availability": {
                "parts_parse_in_dsl": not problems,
                "data_requires": requires,
                "rule_reconstructed_from_vocab_and_gauges": not problems},
            "data_coverage": {
                "discovery_cut": _iso(raw["slices"]["cut_ms"]),
                "discovery_symbols": len(raw["slices"]["discovery"]),
                "discovery_bars_min_max": [min(raw["slices"]["discovery"]
                                               .values()),
                                           max(raw["slices"]["discovery"]
                                               .values())],
                "gauges": gauges_cov},
            "statistical_testability": {
                "testable_flag": bool(c["testable"]),
                "trades": c["trades"], "scored_symbols": c["scored_symbols"],
                "window": window,
                "window_control": ctl and {"powered": ctl["powered"],
                                           "consistency_p": ctl["consistency_p"]},
                "caveat": "consistency_p here is the discovery pooled figure, "
                          "not the dependence-corrected gate-1 statistic"},
        },
        "proposed_mechanism": (
            {"label": "PROPOSED — unverified", "status": "absent",
             "note": "no thesis supplied; none is invented"}
            if report is None else
            {"label": "PROPOSED — unverified",
             "status": report.get("mechanism_status"),
             "mechanism": (thesis_obj or {}).get("mechanism")
             if isinstance(thesis_obj, dict) else None,
             "premises": report["premises"]}),
        "rivals": _rivals(raw, parts, requires, ev),
        "novel_consequences": (
            {"status": "absent", "grammar": th.__doc__.split(
                "Prediction grammar")[1].strip()}
            if report is None else
            {"status": report["status"], "predictions": report["predictions"],
             "novelty": report.get("novelty")}),
        "next_test": _next_test(raw, report),
        "verdict": v,
        "thesis": {"provided": thesis_given, "validation": report},
    }
    return {"dossier": dossier, "ctx": ctx}


# ── output ───────────────────────────────────────────────────────────────
def render_markdown(d: dict) -> str:
    o, v = d["observed_pattern"], d["verdict"]
    st = o["statistical_testability"]
    ev = {e["id"]: e["value"] for e in d["evidence"]}
    L = [f"# Candidate dossier — {d['identity']['hash']}", "",
         f"> {d['scope']['notice']}", "",
         f"- identity_sha256 `{d['identity_sha256']}`",
         f"- evidence_sha256 `{d['evidence_sha256']}`",
         f"- **verdict: {v['state']}** — {v['blocking_condition']}",
         f"- next action: {v['next_action']}",
         f"- demo eligible: {v['demo_eligible']}; gate writes: {v['gate_writes']}",
         "", "## 1. Observed pattern (ledger facts, discovery only)", "",
         f"- {o['rule']['timeframe']} / {o['rule']['geometry']}, parts "
         f"{', '.join(p['key'] for p in o['rule']['parts'])}",
         f"- long: `{o['rule']['entry_long']}`",
         f"- short: `{o['rule']['entry_short']}`",
         f"- code: parses={o['code_availability']['parts_parse_in_dsl']}, "
         f"requires {o['code_availability']['data_requires']}",
         f"- coverage: cut {o['data_coverage']['discovery_cut']}, "
         f"{o['data_coverage']['discovery_symbols']} symbols, bars "
         f"{o['data_coverage']['discovery_bars_min_max']}",
         f"- testability: testable={st['testable_flag']}, trades={st['trades']}, "
         f"symbols={st['scored_symbols']}, control {st['window']} = "
         f"{st['window_control']}",
         f"- discovery: consistency_p={ev.get('E.combo.consistency_p')}, "
         f"median_pf={ev.get('E.combo.median_pf')}, "
         f"total_pct={ev.get('E.combo.total_pct')}, "
         f"max_dd_pct={ev.get('E.combo.max_dd_pct')} ({st['caveat']})"]
    for k in d["identity"]["parts"]:
        L.append(f"- ablation, remove `{k}`: p={ev.get(f'E.ablation.{k}.p')}, "
                 f"total_pct={ev.get(f'E.ablation.{k}.total_pct')}, "
                 f"drop_pct={ev.get(f'E.ablation.{k}.drop_pct')}")
    if d["integrity_problems"]:
        L.append(f"- **integrity problems:** {d['integrity_problems']}")
    m = d["proposed_mechanism"]
    L += ["", "## 2. Proposed mechanism (unverified)", "",
          f"- status: {m['status']}" + (f" — {m['note']}" if "note" in m else "")]
    if m.get("mechanism"):
        L.append(f"- statement: {m['mechanism'].get('statement')}")
    for p in m.get("premises", []):
        L.append(f"- {p['id']} ({p['declared_kind']}) → **{p['status']}**: "
                 f"{'; '.join(p['reasons'])}")
        if p.get("supported_statement"):
            L.append(f"  - supported statement (validator-rendered): "
                     f"`{p['supported_statement']}`")
        nar = p.get("narrative") or {}
        if nar.get("text"):
            L.append(f"  - narrative ({nar['status']}, never verified as "
                     f"prose): {nar['text']}"
                     + (f" — flags: {'; '.join(nar['flags'])}"
                        if nar.get("flags") else ""))
    L += ["", "## 3. Rival explanations", ""]
    for r in d["rivals"]:
        L.append(f"- **{r['kind']}** ({r['applies']}): {r['text']}")
        for k in ("evidence_against_candidate",
                  "evidence_for_candidate_on_discovery_only"):
            if k in r:
                L.append(f"  - {k.replace('_', ' ')}: {r[k]}")
    n = d["novel_consequences"]
    L += ["", "## 4. Novel consequences", "", f"- status: {n['status']}"]
    for p in n.get("predictions", []):
        why = "; ".join([c["code"] + ": " + c["detail"] for c in p["codes"]]
                        + p["review"])
        L.append(f"- {p['id']} → **{p['status']}**" + (f": {why}" if why else ""))
    if d["thesis"]["validation"]:
        for e in d["thesis"]["validation"]["errors"]:
            L.append(f"- error `{e['code']}` at {e['path']}: {e['detail']}")
    t = d["next_test"]
    L += ["", "## 5. Next test", "", f"- question: {t['question']}",
          f"- already seen: {t['already_seen']['note']}",
          f"- required: {t['required_test']}",
          f"- data: {t['data_needed']}", f"- gate: {t['gate']}",
          f"- evidence status: {t['evidence_status']}",
          "- blockers:"] + [f"  - {b}" for b in t["blockers"]]
    L += ["", "## 6. Verdict", "", f"- state: **{v['state']}**",
          f"- blocking condition: {v['blocking_condition']}",
          f"- next action: {v['next_action']}",
          f"- external validation: {v['external_validation']['status']}", ""]
    return "\n".join(L)


def _inside(a: Path, b: Path) -> bool:
    return a == b or a.is_relative_to(b)


def prepare_output(out: Path, db: Path, thesis: Path | None) -> Path:
    out, db = Path(out).resolve(), Path(db).resolve()
    if _inside(db, out) or _inside(out, db.parent):
        raise DossierError("output dir must not overlap the source database "
                           "or its directory")
    if _inside(out, (ROOT / "data").resolve()):
        raise DossierError("output dir must not be under the repo data/ dir")
    if thesis is not None:
        tp = Path(thesis).resolve()
        if _inside(tp, out):
            raise DossierError("thesis input must not be inside the output dir")
        if tp == db:
            raise DossierError("thesis input must not be the database")
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        raise DossierError(f"output dir exists and is not empty: {out}")
    return out


def execute(db: Path, cand: str, output_dir: Path,
            thesis: Path | None = None) -> dict:
    if not isinstance(cand, str) or not _HASH.match(cand):
        raise DossierError("hash must be 16 lowercase hex characters")
    if thesis is not None and not Path(thesis).is_file():
        raise DossierError(f"thesis file not found: {thesis}")
    out = prepare_output(output_dir, db, thesis)
    raw = read_ledger(Path(db), cand)
    texts: dict = {}
    thesis_bytes, obj, terr = (None, None, None)
    if thesis is not None:
        thesis_bytes, obj, terr = load_thesis(Path(thesis))
    built = build(raw, obj, terr, thesis_given=thesis is not None)
    d = built["dossier"]
    texts["dossier.json"] = json.dumps(d, indent=1, sort_keys=True,
                                       allow_nan=False) + "\n"
    texts["dossier.md"] = render_markdown(d)
    extra = {}
    if thesis_bytes is not None:
        extra["thesis.input.json"] = thesis_bytes
    else:
        texts["thesis.scaffold.json"] = json.dumps(
            scaffold(built["ctx"]), indent=1, sort_keys=True) + "\n"
    files = {k: hashlib.sha256(t.encode()).hexdigest() for k, t in texts.items()}
    files.update({k: hashlib.sha256(b).hexdigest() for k, b in extra.items()})
    manifest = {"schema": "luffy.candidate_dossier.manifest.v1",
                "tool_version": TOOL_VERSION, "candidate_hash": cand,
                "identity_sha256": d["identity_sha256"],
                "evidence_sha256": d["evidence_sha256"],
                "verdict": d["verdict"]["state"],
                "source": {"database": str(Path(db).resolve()),
                           "access": "sqlite URI mode=ro, PRAGMA query_only=ON, "
                                     "allowlist authorizer, one read "
                                     "transaction rolled back",
                           "queries": raw["queries"],
                           "allowed_columns": {k: sorted(v) for k, v in
                                               ALLOWED_COLUMNS.items()},
                           "not_read": list(NOT_READ)},
                "files": dict(sorted(files.items()))}
    texts["manifest.json"] = json.dumps(manifest, indent=1,
                                        sort_keys=True) + "\n"
    out.mkdir(parents=True, exist_ok=True)
    for name, t in texts.items():
        with open(out / name, "x") as f:                   # never overwrite
            f.write(t)
    for name, b in extra.items():
        with open(out / name, "xb") as f:
            f.write(b)
    return {"output_dir": str(out), "dossier": d, "manifest": manifest}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--db", required=True, type=Path)
    ap.add_argument("--hash", required=True, dest="cand")
    ap.add_argument("--output-dir", required=True, type=Path)
    ap.add_argument("--thesis", type=Path, default=None)
    a = ap.parse_args(argv)
    try:
        res = execute(a.db, a.cand, a.output_dir, a.thesis)
    except DossierError as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2
    v = res["dossier"]["verdict"]
    print(f"{a.cand} verdict={v['state']} ({v['blocking_condition']}) "
          f"identity={res['dossier']['identity_sha256'][:12]} -> "
          f"{res['output_dir']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
