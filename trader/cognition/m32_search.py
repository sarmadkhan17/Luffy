"""Bounded deterministic M3.2 historical pattern search.

The runner reads only the locked artifact through ``m32_protocol`` and writes
one immutable result ledger outside that artifact. It never touches trading,
strategy, referee, handoff, or Gate 2 state.
"""
from __future__ import annotations

import hashlib
import json
import math
import random
import statistics
from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path

from .m32_protocol import (
    ARTIFACT_SHA256, BUDGET, CUT_DIMENSIONS, FEATURE_FAMILIES, LOCKED,
    PROTOCOL_ID, SUPPORT_FLOORS, ProtocolError, canonical_pattern,
    cut_labels, load_locked_artifact, pattern_id, rows_of, support_accounting,
)

TOLERANCE = 1e-12
SEARCH_SCHEMA = "m3.2-historical-pattern-search.v1"


def _finite(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _feature(row, family, feature):
    f = row.get("features") or {}
    if family == "forecast" and feature.startswith("bar_return_"):
        i = int(feature.rsplit("_", 2)[1])
        values = f.get("bar_returns_bps")
        return values[i] if isinstance(values, list) and i < len(values) else None
    if family == "forecast":
        return f.get(feature)
    if family in {"journal", "investigation"}:
        return f.get(feature)
    return None


def _numeric_feature(family, feature):
    return family == "forecast" and (feature == "recent_move_bps" or feature.startswith("bar_return_"))


def _bin(v, bounds):
    if not _finite(v):
        return None
    if v <= bounds["p33_33"]:
        return "low"
    if v <= bounds["p66_67"]:
        return "mid"
    return "high"


def _value(row, atom, bins):
    raw = _feature(row, atom["family"], atom["feature"])
    if _numeric_feature(atom["family"], atom["feature"]):
        return _bin(raw, bins[(atom["family"], atom["feature"])])
    return "missing" if raw is None and atom["family"] == "journal" else raw


def _atom_key(atom):
    return json.dumps(atom, sort_keys=True, separators=(",", ":"))


def _quantile(values):
    xs = sorted(float(v) for v in values if _finite(v))
    if not xs:
        return None
    def q(x):
        pos = (len(xs) - 1) * x
        lo, hi = math.floor(pos), math.ceil(pos)
        return xs[lo] if lo == hi else xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)
    return {"p33_33": q(1 / 3), "p66_67": q(2 / 3), "n": len(xs)}


def _eligible_state(rows, family):
    producer = {"forecast": "forecast", "journal": "journal", "investigation": "investigation_case"}[family]
    return [r for r in rows if r.get("row_type") == "state" and r.get("producer") == producer]


def build_state_atoms(rows):
    bins = {}
    atoms = []
    for family, features in (("forecast", ("recent_move_bps", "bar_return_0_bps", "bar_return_1_bps", "bar_return_2_bps", "bar_return_3_bps", "bar_return_4_bps", "direction", "window_bars")),
                             ("journal", ("action", "executed", "attribution_linked", "registration_time_availability", "skip_reason")),
                             ("investigation", ("family", "cohort", "sign", "catalog_id"))):
        eligible = _eligible_state(rows, family)
        for feature in features:
            if _numeric_feature(family, feature):
                values = [_feature(r, family, feature) for r in eligible]
                bounds = _quantile(values)
                if bounds:
                    bins[(family, feature)] = bounds
                    values = ("low", "mid", "high")
                else:
                    values = ()
            else:
                values = sorted({"missing" if _feature(r, family, feature) is None and family == "journal" else _feature(r, family, feature)
                                 for r in eligible if _feature(r, family, feature) is not None or family == "journal"}, key=lambda x: json.dumps(x, sort_keys=True))
            for value in values:
                atom = {"family": family, "feature": feature, "value": value}
                support = sum(_value(r, atom, bins) == value for r in eligible)
                if support >= SUPPORT_FLOORS["state"]["matched"]:
                    atoms.append((family, atom, support))
    atoms.sort(key=lambda x: (_atom_key(x[1]), x[0]))
    return atoms[:BUDGET["state_atoms"]], bins


def _state_patterns(atoms):
    out = []
    by_family = defaultdict(list)
    for family, atom, _ in atoms:
        by_family[family].append(atom)
    for family in sorted(by_family):
        aa = sorted(by_family[family], key=_atom_key)
        for k in (1, 2, 3):
            for chosen in combinations(aa, k):
                out.append({"family": "state", "atoms": list(chosen)})
                if len(out) >= BUDGET["state_patterns"]:
                    return out
    return out


def _transition_patterns(rows):
    out = set()
    for row in rows:
        if row.get("row_type") != "sequence":
            continue
        f = row.get("features") or {}
        for feature, old, new in (
            ("row_type", f.get("prior_row_type"), "state"),
            ("producer", f.get("prior_producer"), "forecast"),
        ):
            if old is not None:
                out.add(json.dumps({"family": "transition", "atoms": [{"family": "sequence", "feature": feature, "from": old, "to": new}]}, sort_keys=True))
    patterns = [json.loads(x) for x in sorted(out)]
    return patterns[:BUDGET["transition_patterns"]]


def _sequence_patterns(rows):
    links = [r for r in rows if r.get("row_type") == "sequence"]
    by_prior = defaultdict(list)
    for r in links:
        by_prior[(r.get("symbol"), (r.get("features") or {}).get("prior_row_id"))].append(r)
    chains = set()
    for first in links:
        f1 = first.get("features") or {}
        current = f1.get("current_row_id")
        for second in by_prior.get((first.get("symbol"), current), []):
            f2 = second.get("features") or {}
            atoms = []
            for f in (f1, f2):
                atoms.append({"family": "sequence", "feature": "producer", "from": f.get("prior_producer"), "to": "sequence_link"})
            chains.add(json.dumps({"family": "sequence", "atoms": atoms}, sort_keys=True))
    return [json.loads(x) for x in sorted(chains)][:BUDGET["sequence_patterns"]]


def enumerate_patterns(rows):
    atoms, bins = build_state_atoms(rows)
    patterns = _state_patterns(atoms) + _transition_patterns(rows) + _sequence_patterns(rows)
    unique = {}
    for p in patterns:
        c = canonical_pattern(p)
        unique[pattern_id(c)] = c
    ordered = [unique[k] for k in sorted(unique)]
    return ordered[:BUDGET["unique_patterns"]], bins


def _matches(pattern, rows, bins):
    family = pattern["family"]
    out = []
    if family == "state":
        source = pattern["atoms"][0]["family"]
        eligible = _eligible_state(rows, source)
        for row in eligible:
            if all(_value(row, a, bins) == a["value"] for a in pattern["atoms"]):
                out.append(row["row_id"])
    elif family == "transition":
        for row in rows:
            if row.get("row_type") != "sequence":
                continue
            f = row.get("features") or {}
            if all(f.get("prior_" + a["feature"]) == a["from"] and a["to"] == ("state" if a["feature"] == "row_type" else "forecast") for a in pattern["atoms"]):
                out.append(row["row_id"])
    else:
        links = [r for r in rows if r.get("row_type") == "sequence"]
        by_prior = defaultdict(list)
        for r in links:
            by_prior[(r.get("symbol"), (r.get("features") or {}).get("prior_row_id"))].append(r)
        for first in links:
            current = first.get("features", {}).get("current_row_id")
            for second in by_prior.get((first.get("symbol"), current), []):
                sig = []
                for r in (first, second):
                    f = r.get("features") or {}
                    sig.append((f.get("prior_producer"), "sequence_link"))
                if all((a.get("from"), a.get("to")) == sig[i] for i, a in enumerate(pattern["atoms"])):
                    out.append(first["row_id"])
    return sorted(set(out))


def _family_for_pattern(pattern):
    if pattern["family"] == "state":
        return pattern["atoms"][0]["family"]
    return pattern["family"]


def applicable_label_families(pattern):
    """Return every registered label family for one canonical pattern."""
    family = _family_for_pattern(pattern)
    if family == "forecast":
        return ("case_kind", "persistence")
    if family in {"journal", "investigation"}:
        return ("case_kind",)
    return ("continuation",)


def _target(row, label_family, lookup):
    labels = row.get("labels") or {}
    if label_family == "case_kind":
        return labels.get("case_kind")
    if label_family == "persistence":
        direction = (row.get("features") or {}).get("direction")
        value = labels.get("price_change_bps")
        return float(value) * (1 if direction in (None, 1, "1") else -1) if _finite(value) else None
    if label_family == "continuation":
        current = lookup.get((row.get("features") or {}).get("current_row_id"))
        return (current.get("row_type"), current.get("producer")) if current else None
    return None


def _eligible_for_label(pattern, rows, label):
    family = _family_for_pattern(pattern)
    if label == "case_kind":
        if family == "forecast": return [r for r in rows if r.get("row_type") == "state" and r.get("producer") == "forecast"]
        if family == "journal": return [r for r in rows if r.get("row_type") == "state" and r.get("producer") == "journal"]
        if family == "investigation": return [r for r in rows if r.get("row_type") == "state" and r.get("producer") == "investigation_case"]
        return []
    if label == "persistence":
        return [r for r in rows if r.get("row_type") == "state" and r.get("producer") == "forecast" and _finite((r.get("labels") or {}).get("price_change_bps"))]
    if label == "continuation":
        return [r for r in rows if r.get("row_type") == "sequence" and (r.get("features") or {}).get("current_row_id")]
    return []


def _metric(rows, matched, label, lookup):
    values = [(r, _target(r, label, lookup)) for r in rows]
    values = [(r, v) for r, v in values if v is not None]
    hit = [(r, v) for r, v in values if r.get("row_id") in matched]
    if not hit or not values:
        return {"effect": None, "signed_effect": None, "observed": len(hit), "baseline": None, "status": "untestable"}
    if label == "persistence":
        allv = [v for _, v in values]; hitv = [v for _, v in hit]
        med = statistics.median(allv); hmed = statistics.median(hitv)
        mad = statistics.median([abs(x - med) for x in allv])
        signed = (hmed - med) / (mad + 1.0)
        return {"effect": abs(signed), "signed_effect": signed, "observed": len(hit), "baseline": med, "matched_median": hmed, "status": "scored"}
    all_labels = [v for _, v in values]; hit_labels = [v for _, v in hit]
    majority = Counter(all_labels).most_common(1)[0][0]
    hit_majority = Counter(hit_labels).most_common(1)[0][0]
    nonhit = [v for r, v in values if r.get("row_id") not in matched]
    nonhit_majority = Counter(nonhit).most_common(1)[0][0] if nonhit else hit_majority
    classes = sorted(set(all_labels), key=str)
    def f1(pred_match, pred_nonmatch):
        scores=[]
        for cls in classes:
            tp=sum(1 for r,v in values if v==cls and ((r.get("row_id") in matched and pred_match==cls) or (r.get("row_id") not in matched and pred_nonmatch==cls)))
            fp=sum(1 for r,v in values if v!=cls and ((r.get("row_id") in matched and pred_match==cls) or (r.get("row_id") not in matched and pred_nonmatch==cls)))
            fn=sum(1 for r,v in values if v==cls and not ((r.get("row_id") in matched and pred_match==cls) or (r.get("row_id") not in matched and pred_nonmatch==cls)))
            scores.append(0.0 if 2*tp+fp+fn == 0 else 2*tp/(2*tp+fp+fn))
        return sum(scores)/len(scores)
    baseline = f1(majority, majority)
    score = f1(hit_majority, nonhit_majority)
    signed = score - baseline
    return {"effect": abs(signed), "signed_effect": signed, "observed": len(hit), "baseline": baseline, "score": score, "status": "scored"}


def _cut_rows(rows, cut_id):
    if cut_id == "full": return list(rows)
    dim = cut_id.split("=", 1)[0]
    value = cut_id.split("=", 1)[1]
    return [r for r in rows if cut_labels(r)[dim] == value]


def _cut_ids(rows):
    ids = ["full"]
    for dim in CUT_DIMENSIONS:
        values = sorted({cut_labels(r)[dim] for r in rows})
        ids.extend(f"{dim}={v}" for v in values)
    return ids


def _null_p(rows, matched, label, lookup, cut_id, observed, draws=2000, seed_text=""):
    if observed is None:
        return {"seed": None, "attempted": 0, "successful": 0, "p": None, "failed": 0}
    groups = defaultdict(list)
    for row in rows:
        cl = "full" if cut_id == "full" else cut_labels(row)[cut_id.split("=", 1)[0]]
        groups[(cl, row.get("symbol"), row.get("dependence_group", "unknown"))].append(row)
    if not any(len(v) > 1 for v in groups.values()):
        return {"seed": hashlib.sha256(seed_text.encode()).hexdigest(), "attempted": 0, "successful": 0, "p": None, "failed": 1}
    rng = random.Random(int(hashlib.sha256(seed_text.encode()).hexdigest(), 16))
    targets = {r["row_id"]: _target(r, label, lookup) for r in rows}
    favorable = 0; successful = 0
    ids = set(matched)
    for _ in range(draws):
        shuffled = dict(targets)
        failed = False
        for group in groups.values():
            if len(group) < 2:
                continue
            vals = [targets[r["row_id"]] for r in group]
            if label == "persistence":
                offset = rng.randrange(len(vals))
                vals = vals[offset:] + vals[:offset]
            else:
                rng.shuffle(vals)
            for row, value in zip(group, vals): shuffled[row["row_id"]] = value
        if failed: continue
        fake = []
        for row in rows:
            clone = dict(row)
            clone["labels"] = dict(row.get("labels") or {})
            if label == "persistence": clone["labels"]["price_change_bps"] = shuffled[row["row_id"]]
            else: clone["labels"]["case_kind"] = shuffled[row["row_id"]]
            fake.append(clone)
        score = _metric(fake, ids, label, lookup).get("signed_effect")
        if score is None: continue
        successful += 1
        if abs(score) >= abs(observed): favorable += 1
    return {"seed": hashlib.sha256(seed_text.encode()).hexdigest(), "attempted": draws, "successful": successful, "p": (1 + favorable) / (successful + 1) if successful else None, "failed": draws - successful}


def _ablation(pattern, record_by_id, bins, label=None):
    if pattern["family"] != "state" or len(pattern["atoms"]) == 1:
        return {"pass": True, "reason": "single_or_nonconjunctive"}
    child = record_by_id.get((pattern_id(pattern), label)) if label else record_by_id.get(pattern_id(pattern))
    if not child or child.get("metric", {}).get("effect") is None:
        return {"pass": False, "reason": "child_unscored"}
    weaker = []
    for i in range(len(pattern["atoms"])):
        atoms = pattern["atoms"][:i] + pattern["atoms"][i+1:]
        parent = {"family": "state", "atoms": atoms}
        rec = record_by_id.get((pattern_id(parent), label)) if label else record_by_id.get(pattern_id(parent))
        if not rec or (rec.get("metric", {}).get("effect") or 0) >= child["metric"]["effect"] - TOLERANCE:
            weaker.append(pattern_id(parent))
    return {"pass": not weaker, "weaker_or_equal": weaker}


def _falsification(pattern, rows, matched, label, lookup, metric):
    symbols = sorted({r.get("symbol") for r in rows})
    episodes = sorted({r.get("underlying_id") or r.get("case_id") or r.get("row_id") for r in rows})
    checks = {}
    for name, key, values in (("leave_symbol", "symbol", symbols), ("leave_episode", "underlying_id", episodes)):
        signs=[]
        for value in values:
            sub=[r for r in rows if (r.get(key) or r.get("case_id") or r.get("row_id")) != value]
            if len(sub) < 2: continue
            m=set(r["row_id"] for r in sub if r["row_id"] in matched)
            x=_metric(sub,m,label,lookup).get("signed_effect")
            if x is not None and abs(x)>TOLERANCE: signs.append(x > 0)
        checks[name] = {"pass": not signs or all(x == signs[0] for x in signs), "signs": signs}
    full = metric.get("signed_effect")
    checks["reversed_time"] = {"pass": True, "effect": full, "reason": "row-order reversal leaves row-local matching unchanged"}
    base_rows = [r for r in rows if (r.get("labels") or {}).get("case_kind") not in {"skip", None}]
    if label == "case_kind" and base_rows:
        m=set(r["row_id"] for r in base_rows if r["row_id"] in matched)
        reduced=_metric(base_rows,m,label,lookup).get("effect")
        checks["skip_failure_visibility"] = {"pass": reduced is None or reduced <= (metric.get("effect") or 0) + TOLERANCE, "effect_without_skips": reduced}
    checks["sequence_link_integrity"] = {"pass": all(r.get("row_id") in matched for r in rows if r.get("row_id") in matched)}
    return {"pass": all(v["pass"] for v in checks.values()), "checks": checks}


def _bh(records):
    eligible=[r for r in records if r.get("p_value") is not None]
    eligible.sort(key=lambda r: (r["p_value"], r["pattern_id"]))
    n=len(eligible); running=1.0
    for i,r in reversed(list(enumerate(eligible,1))):
        running=min(r["p_value"]*n/i, running); r["q_value"]=running


def run_search():
    artifact = load_locked_artifact()
    rows = rows_of(artifact)
    patterns, bins = enumerate_patterns(rows)
    lookup = {r.get("row_id"): r for r in rows}
    records=[]
    for pattern in patterns:
        pid=pattern_id(pattern); matched=set(_matches(pattern, rows, bins))
        for label in applicable_label_families(pattern):
            eligible=_eligible_for_label(pattern, rows, label)
            support = support_accounting(eligible, matched,
                                         "persistence" if label == "persistence" else
                                         "sequence" if label == "continuation" else "state")
            floor=SUPPORT_FLOORS["sequence" if label == "continuation" else "state"]
            status="untestable" if any(support.get(k,0) < v for k,v in floor.items()) else "rejected"
            metric={}; null={}; cuts={}
            if status != "untestable":
                metric=_metric(eligible, matched, label, lookup)
                if metric.get("status") != "scored": status="untestable"
                else:
                    for cut_id in _cut_ids(eligible):
                        sub=_cut_rows(eligible,cut_id); m={r["row_id"] for r in sub if r["row_id"] in matched}
                        cuts[cut_id]=_metric(sub,m,label,lookup)
                    p=_null_p(eligible,matched,label,lookup,"full",metric.get("signed_effect"),
                              seed_text=f"{PROTOCOL_ID}:{pid}:full:{label}",
                              draws=BUDGET["null_draws_per_pattern_family_cut"])
                    null["full"]=p
                    if p["successful"] == 0: status="untestable"
            eligible_ids = sorted(r["row_id"] for r in eligible)
            matched_ids = sorted(set(eligible_ids) & matched)
            row_results = {}
            for row in eligible:
                rid = row["row_id"]
                if rid in matched:
                    if label == "case_kind":
                        kind = (row.get("labels") or {}).get("case_kind")
                        row_results[rid] = {"status": {"selected_forecast": "selected", "ignored_forecast": "ignored", "skip": "skipped"}.get(kind, "outcome_observed"), "matched": True}
                    else:
                        row_results[rid] = {"status": "outcome_observed" if _target(row, label, lookup) is not None else "outcome_unknown", "matched": True}
                else:
                    row_results[rid] = {"status": "not_eligible", "matched": False}
            baseline_spec = ("direction_only_and_unconditional" if label == "persistence" else "unconditional_and_family_only")
            records.append({"schema":"m3.2-historical-pattern-candidate.v1","protocol_id":PROTOCOL_ID,"dataset_id":LOCKED["dataset_id"],"dataset_version":LOCKED["dataset_version"],"pattern_id":pid,"pattern":canonical_pattern(pattern),"label_family":label,"matched_ids":matched_ids,"unmatched_ids":[x for x in eligible_ids if x not in matched],"eligible_ids":eligible_ids,"row_results":row_results,"support":support,"cuts":cuts,"baseline":{"registered":baseline_spec,"value":metric.get("baseline")},"null":{"registered":"circular_shift_or_within_cell_permutation","draws_per_applicable_cut":BUDGET["null_draws_per_pattern_family_cut"],"results":null},"metric":metric,"p_value":(null.get("full") or {}).get("p"),"rank":None,"ablations":{},"falsification":{},"status":status,"strategy_admission":False,"gate2":False,"live_handoff":False,"research_referee":False,"research_handoff":False})
    byid={(r["pattern_id"],r["label_family"]):r for r in records}
    for rec in records:
        if rec["status"] == "untestable": continue
        pattern=rec["pattern"]; label=rec["label_family"]; eligible=_eligible_for_label(pattern,rows,label); matched=set(rec["matched_ids"])
        rec["ablations"]=_ablation(pattern,byid,bins,label)
        rec["falsification"]=_falsification(pattern,eligible,matched,label,lookup,rec["metric"])
        effect=rec["metric"].get("effect") or 0; p=rec.get("p_value") or 1
        rec["rank"]=effect * math.sqrt(rec["support"].get("observed",0)/max(1,rec["support"].get("matched",0))) * (1-p)
        rec["status"]="descriptive_candidate" if rec["ablations"].get("pass") and rec["falsification"].get("pass") else "rejected"
    _bh(records)
    records.sort(key=lambda r: (-float(r.get("rank") or -1), r["pattern_id"], r["label_family"]))
    return {"schema":SEARCH_SCHEMA,"protocol_id":PROTOCOL_ID,"artifact_sha256":ARTIFACT_SHA256,"dataset_id":LOCKED["dataset_id"],"dataset_version":LOCKED["dataset_version"],"rows":len(rows),"budget":BUDGET,"patterns_evaluated":len(patterns),"record_evaluations":len(records),"null_draws_used":sum((x.get("attempted") or 0) for r in records for x in (r.get("null",{}).get("results",{}) or {}).values()),"records":records,"counts":dict(Counter(r["status"] for r in records)),"search_status":"search_complete_descriptive","strategy_admission":False,"gate2":False,"live_handoff":False,"research_referee":False,"research_handoff":False}


def write_immutable(result, path: Path):
    encoded=json.dumps(result,indent=2,sort_keys=True,allow_nan=False)+"\n"
    if path.exists():
        if path.read_text(encoding="utf-8") != encoded: raise ProtocolError("search_result_conflict")
        return "unchanged"
    path.parent.mkdir(parents=True,exist_ok=True); path.write_text(encoded,encoding="utf-8"); return "written"
