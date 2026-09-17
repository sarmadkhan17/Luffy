"""The thesis contract: what a proposed explanation of a survivor must say,
and the deterministic policing it gets before anything tests it.

Offline and pure — no database, no network, no LLM. The caller supplies a
CONTEXT built from discovery evidence only (the candidate's rule, its evidence
ids and values, the discovery universe and cut, the window controls). Nothing
here can reach a held-out number, so nothing here can leak one.

What it can decide, and what it cannot:

- It CAN refuse: a malformed thesis, a dangling reference, an "observation"
  that contradicts its cited evidence, a prediction outside the grammar, a
  syntactic restatement of the entry rule, a duplicate, an unavailable
  observable, a discovery-era subset.
- It CAN verify a structured observation {evidence_id, op, value}; it then
  renders the supported wording itself. It CANNOT verify prose: an author's
  narrative is `unverified` unless it is exactly that canonical statement.
- It CANNOT prove novelty or truth. A prediction that is not syntactically
  refuted but touches a gauge the rule already cuts is `needs_review`, and a
  thesis with any such item cannot be well-formed automatically.
- It never passes a thesis. The best status is `well_formed_untested`, and the
  best verdict is `waiting_for_evidence`. `reason_passed` and
  `ready_for_admission_review` need gate evidence this module never sees.

Prediction grammar (pipeline design Part 5, 2026-09-11):

    {"id": "H1",
     "subset":    {"kind": "leg",     "value": "long" | "short"}
                | {"kind": "markets", "value": ["SYM/USDT", ...]}
                | {"kind": "regime",  "value": one of strategy.spec.REGIMES}
                | {"kind": "era",     "value": {"from": "YYYY-MM-DD",
                                                "to":   "YYYY-MM-DD"}},
       -- or, instead of subset --
     "condition": "<DSL boolean expression>",
     "metric":   "mean_r" | "null_pctile" | "hit_rate",
     "relation": ">" | "<",
     "baseline": "complement" | "all",
     "premise_ids": ["P1", ...], "distinguishes": ["R1", ...]}
"""
from __future__ import annotations

import ast
import hashlib
import json
import math
import re
from datetime import datetime, timezone

from ..strategy import dsl
from ..strategy.spec import REGIMES

SCHEMA = "luffy.thesis.v1"

METRICS = ("mean_r", "null_pctile", "hit_rate")
RELATIONS = (">", "<")
BASELINES = ("complement", "all")
SUBSET_KINDS = ("leg", "markets", "regime", "era")
LEGS = ("long", "short")
RIVAL_KINDS = ("shared_market_regime", "selection_from_many_variants",
               "data_provenance", "other")
REQUIRED_RIVALS = ("shared_market_regime", "selection_from_many_variants")
CLAIM_OPS = ("==", "!=", "<", "<=", ">", ">=")

#: every verdict this contract can emit; the other two need gate evidence
VERDICTS = ("research_only", "waiting_for_evidence")
UNREACHABLE_HERE = ("contradicted", "ready_for_admission_review")

_TOP_KEYS = {"schema", "candidate_hash", "identity_sha256", "evidence_sha256",
             "mechanism", "premises", "rivals", "predictions",
             "kill_condition"}
_MECH_KEYS = {"tag", "statement", "premise_ids"}
_PREMISE_KEYS = {"id", "kind", "text", "claim", "evidence_ids"}
_CLAIM_KEYS = {"evidence_id", "op", "value"}
_RIVAL_KEYS = {"id", "kind", "text", "evidence_ids"}
_PRED_KEYS = {"id", "subset", "condition", "metric", "relation", "baseline",
              "premise_ids", "distinguishes"}

_ID = {"premise": re.compile(r"^P[1-9][0-9]?$"),
       "rival": re.compile(r"^R[1-9][0-9]?$"),
       "prediction": re.compile(r"^H[1-9][0-9]?$")}
_TAG = re.compile(r"^[a-z][a-z0-9_]{2,47}$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")

#: advisory flags on free narrative. They only explain why prose is suspect;
#: they never decide support, and a narrative without a flag is not thereby
#: entailed by its claim (regexes cannot establish entailment).
_CAUSAL = re.compile(
    r"\b(because|cause[sd]?|causing|driv(?:e|es|en|ing)|due to|leads? to|"
    r"results? in|explain(?:s|ed)?|so that|therefore|thus|hence|forc(?:e|es|ed)|"
    r"trigger(?:s|ed)?|in order to|reason)\b", re.I)
#: who traded. Price and volume cannot identify participants.
_ACTORS = re.compile(
    r"\b(traders?|market ?makers?|whales?|retail|institutions?|funds?|"
    r"liquidations?|squeez\w*|capitulat\w*|dealers?|hedg\w*|arbitrageurs?|"
    r"participants?|buyers?|sellers?|shorts? covering|trapped)\b", re.I)


def sha256_json(obj) -> str:
    blob = json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      allow_nan=False)
    return hashlib.sha256(blob.encode()).hexdigest()


def _err(errors: list, code: str, path: str, detail: str) -> None:
    errors.append({"code": code, "path": path, "detail": detail})


# ── schema ───────────────────────────────────────────────────────────────
def _keys(obj, allowed: set, required: set, path: str, errors: list) -> bool:
    if not isinstance(obj, dict):
        _err(errors, "schema", path, "must be an object")
        return False
    for k in sorted(set(obj) - allowed):
        _err(errors, "schema_unknown_key", f"{path}.{k}",
             "key not in the thesis schema")
    for k in sorted(required - set(obj)):
        _err(errors, "schema_missing_key", f"{path}.{k}", "required")
    return True


def _text(v, path: str, errors: list, lo: int, hi: int) -> bool:
    if not isinstance(v, str) or not lo <= len(v.strip()) <= hi \
            or "<fill" in v:
        _err(errors, "schema", path, f"must be a filled string of {lo}-{hi} chars")
        return False
    return True


def _id_list(v, path: str, errors: list, min_n: int = 0) -> list:
    if not isinstance(v, list) or len(v) < min_n or \
            not all(isinstance(x, str) for x in v) or len(set(v)) != len(v):
        _err(errors, "schema", path,
             f"must be a list of >= {min_n} unique strings")
        return []
    return v


# ── expressions ──────────────────────────────────────────────────────────
def _num(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) \
            and not isinstance(node.value, bool):
        return float(node.value)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        v = _num(node.operand)
        return None if v is None else -v
    return None


_FLIP = {">": "<", "<": ">", ">=": "<=", "<=": ">=", "==": "==", "!=": "!="}
_OPS = {ast.Gt: ">", ast.Lt: "<", ast.GtE: ">=", ast.LtE: "<=",
        ast.Eq: "==", ast.NotEq: "!="}


def bound(node) -> tuple | None:
    """`X op c` as (render(X), op, c); `0 - X op c` becomes `X flip(op) -c`.
    None when the node is not a single comparison against a constant."""
    if not (isinstance(node, ast.Compare) and len(node.ops) == 1):
        return None
    op = _OPS.get(type(node.ops[0]))
    left, right = node.left, node.comparators[0]
    c = _num(right)
    if c is None and _num(left) is not None:            # c op X
        left, right, c, op = right, left, _num(left), _FLIP[op]
    if op is None or c is None:
        return None
    if isinstance(left, ast.BinOp) and isinstance(left.op, ast.Sub) \
            and _num(left.left) == 0.0:
        return ast.unparse(left.right), _FLIP[op], -c
    return ast.unparse(left), op, c


def _interval(op: str, c: float) -> tuple:
    """(lo, lo_closed, hi, hi_closed) of {x : x op c}."""
    inf = math.inf
    return {">": (c, False, inf, False), ">=": (c, True, inf, False),
            "<": (-inf, False, c, False), "<=": (-inf, False, c, True)}[op]


def _against(cond: tuple, part: tuple) -> str:
    """How a comparison relates to a part every entry on one side satisfies:
    `implied` (true on every entry), `empty` (true on none), `unknown`."""
    lhs, op, c = cond
    plhs, pop, t = part
    if lhs != plhs or op in ("==", "!=") or pop in ("==", "!="):
        return "unknown"
    alo, alc, ahi, ahc = _interval(pop, t)             # the entries
    blo, blc, bhi, bhc = _interval(op, c)              # the condition
    lo_in = alo > blo or (alo == blo and (blc or not alc))
    hi_in = ahi < bhi or (ahi == bhi and (bhc or not ahc))
    if lo_in and hi_in:
        return "implied"
    if ahi < blo or (ahi == blo and not (ahc and blc)) or \
            bhi < alo or (bhi == alo and not (bhc and alc)):
        return "empty"
    return "unknown"


def _relate(node, side: dict) -> str:
    """implied | empty | unknown, for one side of the entry rule.
    `side` = {"texts": set of rendered part/rule texts, "bounds": [...]}"""
    text = ast.unparse(node)
    if text in side["texts"]:
        return "implied"
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        inner = _relate(node.operand, side)
        return {"implied": "empty", "empty": "implied"}.get(inner, "unknown")
    if isinstance(node, ast.BoolOp):
        rs = [_relate(v, side) for v in node.values]
        if isinstance(node.op, ast.And):
            if "empty" in rs:
                return "empty"
            return "implied" if all(r == "implied" for r in rs) else "unknown"
        if "implied" in rs:
            return "implied"
        return "empty" if all(r == "empty" for r in rs) else "unknown"
    b = bound(node)
    if b is not None:
        for pb in side["bounds"]:
            r = _against(b, pb)
            if r != "unknown":
                return r
    return "unknown"


def rule_sides(rule: dict) -> dict:
    """Pre-rendered texts and bounds of the rule's long and short sides."""
    out = {}
    for side in LEGS:
        texts, bounds, lhs = set(), [], set()
        whole = dsl.parse(rule[f"entry_{side}"])
        texts.add(ast.unparse(whole.body))
        for p in rule["parts"]:
            node = dsl.parse(p[side]).body
            texts.add(ast.unparse(node))
            b = bound(node)
            if b is not None:
                bounds.append(b)
                lhs.add(b[0])
        out[side] = {"texts": texts, "bounds": bounds, "lhs": lhs}
    return out


def window_key(requires) -> str:
    extra = sorted(r for r in (requires or ()) if r != "ohlcv")
    return "|".join(extra) if extra else "ohlcv"


# ── predictions ──────────────────────────────────────────────────────────
def _date_ms(s: str) -> int | None:
    if not isinstance(s, str) or not _DATE.match(s):
        return None
    try:
        d = datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return int(d.timestamp() * 1000)


def _subset(sub, ctx: dict, path: str, errors: list, codes: list,
            review: list) -> str | None:
    if not isinstance(sub, dict) or set(sub) != {"kind", "value"}:
        _err(errors, "schema", path, "subset must be exactly {kind, value}")
        return None
    kind, val = sub["kind"], sub["value"]
    if kind not in SUBSET_KINDS:
        _err(errors, "schema", f"{path}.kind", f"must be one of {SUBSET_KINDS}")
        return None
    if kind == "leg":
        if val not in LEGS:
            _err(errors, "schema", f"{path}.value", f"leg must be {LEGS}")
            return None
        return f"leg:{val}"
    if kind == "regime":
        if not isinstance(val, str):
            _err(errors, "schema", f"{path}.value", "regime must be a string")
            return None
        if val not in REGIMES:
            codes.append(("unavailable_regime",
                          f"regime {val!r} is not one of {sorted(REGIMES)}"))
        return f"regime:{val}"
    if kind == "markets":
        if not isinstance(val, list) or not val or \
                not all(isinstance(x, str) for x in val) or \
                len(set(val)) != len(val):
            _err(errors, "schema", f"{path}.value",
                 "markets must be a non-empty list of unique symbols")
            return None
        universe = set(ctx["discovery_symbols"])
        unknown = sorted(set(val) - universe)
        if unknown:
            codes.append(("unavailable_market",
                          f"not in the discovery universe: {unknown}"))
        elif set(val) == universe:
            codes.append(("discovery_restatement",
                          "the whole discovery universe is the pooled result "
                          "already shown"))
        else:
            review.append("a market subset of the discovery universe can only "
                          "be tested in the later era, not on unseen markets")
        return "markets:" + ",".join(sorted(val))
    # era
    if not isinstance(val, dict) or set(val) != {"from", "to"}:
        _err(errors, "schema", f"{path}.value", "era must be {from, to}")
        return None
    lo, hi = _date_ms(val["from"]), _date_ms(val["to"])
    if lo is None or hi is None or lo >= hi:
        _err(errors, "schema", f"{path}.value",
             "era dates must be YYYY-MM-DD with from < to")
        return None
    cut = ctx["cut_ms"]
    if hi <= cut:
        codes.append(("discovery_era_already_seen",
                      "the whole era lies before the discovery cut"))
    elif lo < cut:
        codes.append(("era_straddles_cut",
                      "the era mixes discovery bars with later ones"))
    return f"era:{val['from']}..{val['to']}"


def _condition(text, ctx: dict, sides: dict, path: str, errors: list,
               codes: list, review: list) -> str | None:
    if not isinstance(text, str) or not text.strip():
        _err(errors, "schema", path, "condition must be a non-empty string")
        return None
    try:
        tree = dsl.parse(text)
    except dsl.SpecError as e:
        codes.append(("unsupported_expression", str(e)))
        return None
    node = tree.body
    canon = ast.unparse(node)
    boolish = isinstance(node, (ast.Compare, ast.BoolOp)) or (
        isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not))
    if not boolish:
        codes.append(("not_a_condition",
                      "must be a comparison, and/or, or not"))
        return canon
    if not (dsl.features_used(tree)):
        codes.append(("constant_condition", "reads no feature"))
        return canon
    req = set(dsl.data_requires(tree)) | set(ctx["requires"])
    window = window_key(req)
    ctl = ctx["controls"].get(window)
    if ctl is None:
        review.append(f"no window control is recorded for {window!r}; "
                      "whether the gate can see anything there is unmeasured")
    elif not ctl:
        codes.append(("unavailable_observable",
                      f"window {window!r} control is not powered at this horizon"))
    rel = {s: _relate(node, sides[s]) for s in LEGS}
    if all(r != "unknown" for r in rel.values()):
        codes.append(("entry_rule_tautology",
                      f"on the entry rule's sides this condition is {rel}: "
                      "it selects all trades, none, or exactly one leg"))
    elif any(r != "unknown" for r in rel.values()):
        review.append(f"decided by the entry rule on one side only ({rel})")
    touched = sorted(l for s in LEGS for l in sides[s]["lhs"] if l in canon)
    if touched and all(r == "unknown" for r in rel.values()):
        review.append("re-cuts a quantity the rule already thresholds "
                      f"({touched[0]}); the ablation may already answer it")
    return f"cond:{canon}"


def _prediction(p, i: int, ctx: dict, sides: dict, errors: list) -> dict:
    path = f"predictions[{i}]"
    out = {"id": p.get("id") if isinstance(p, dict) else None,
           "status": "refused", "codes": [], "review": [], "canonical": None}
    n0 = len(errors)
    if not _keys(p, _PRED_KEYS, _PRED_KEYS - {"subset", "condition"},
                 path, errors):
        return out
    if not isinstance(p.get("id"), str) or not _ID["prediction"].match(p["id"]):
        _err(errors, "schema", f"{path}.id", "must match H<n>")
    has_sub, has_cond = "subset" in p, "condition" in p
    codes, review = [], []
    target = None
    if has_sub == has_cond:
        _err(errors, "schema", path, "exactly one of subset or condition")
    elif has_sub:
        target = _subset(p["subset"], ctx, f"{path}.subset", errors, codes,
                         review)
    else:
        target = _condition(p["condition"], ctx, sides, f"{path}.condition",
                            errors, codes, review)
    for key, allowed in (("metric", METRICS), ("relation", RELATIONS),
                         ("baseline", BASELINES)):
        if p.get(key) not in allowed:
            _err(errors, "schema", f"{path}.{key}", f"must be one of {allowed}")
    _id_list(p.get("premise_ids"), f"{path}.premise_ids", errors, 1)
    _id_list(p.get("distinguishes"), f"{path}.distinguishes", errors, 1)
    out["codes"] = [{"code": c, "detail": d} for c, d in codes]
    out["review"] = review
    if target is not None:
        out["canonical"] = {"target": target, "metric": p.get("metric"),
                            "baseline": p.get("baseline"),
                            "relation": p.get("relation")}
    if len(errors) > n0 or codes:
        out["status"] = "refused"
    elif review:
        out["status"] = "needs_review"
    else:
        out["status"] = "accepted_for_testing"
    return out


# ── premises ─────────────────────────────────────────────────────────────
def _check_claim(have, op: str, want) -> bool:
    if isinstance(want, bool) or isinstance(have, bool):
        if op not in ("==", "!="):
            return False
        return (bool(have) == bool(want)) == (op == "==")
    if isinstance(want, (int, float)) and isinstance(have, (int, float)):
        h, w = float(have), float(want)
        eq = math.isclose(h, w, rel_tol=1e-6, abs_tol=1e-12)
        return {"==": eq, "!=": not eq, "<": h < w and not eq,
                "<=": h < w or eq, ">": h > w and not eq,
                ">=": h > w or eq}[op]
    if isinstance(want, str) and isinstance(have, str) and op in ("==", "!="):
        return (have == want) == (op == "==")
    return False


def canonical_observation(eid: str, op: str, value, recorded) -> str:
    """The only wording this contract vouches for: rendered from the checked
    structure, never from what the author wrote."""
    dump = lambda v: json.dumps(v, sort_keys=True, allow_nan=False)  # noqa: E731
    return f"{eid} {op} {dump(value)} (recorded: {dump(recorded)})"


def _premise(p, i: int, ctx: dict, errors: list) -> dict:
    """An observed premise has two separate halves. The structured claim
    {evidence_id, op, value} is checked mechanically; when it holds, the
    validator renders `supported_statement` itself. The author's `text` is a
    narrative that is never verified. The premise is `supported_observation`
    only when that narrative is absent or exactly the canonical statement;
    any other wording, however innocuous, leaves it `unsupported` while the
    verified structured observation stays recorded for use."""
    path = f"premises[{i}]"
    has = isinstance(p, dict)
    out = {"id": p.get("id") if has else None,
           "declared_kind": p.get("kind") if has else None,
           "status": "unsupported", "supported_statement": None,
           "observation": None,
           "narrative": {"text": None, "status": "absent", "flags": []},
           "reasons": []}
    kind = p.get("kind") if has else None
    required = {"id", "kind"} if kind == "observed" else {"id", "kind", "text"}
    if not _keys(p, _PREMISE_KEYS, required, path, errors):
        return out
    if not isinstance(p.get("id"), str) or not _ID["premise"].match(p["id"]):
        _err(errors, "schema", f"{path}.id", "must match P<n>")
    if "text" in p:
        _text(p.get("text"), f"{path}.text", errors, 10, 400)
    text = p["text"].strip() if isinstance(p.get("text"), str) else None
    if text:
        flags = []
        if _CAUSAL.search(text):
            flags.append("causal language: a value supports only its number, "
                         "never the cause")
        if _ACTORS.search(text):
            flags.append("names who traded; price and volume cannot identify "
                         "participants")
        out["narrative"] = {"text": text, "status": "unverified",
                            "flags": flags}
    evidence = ctx["evidence"]
    cited = _id_list(p.get("evidence_ids", []), f"{path}.evidence_ids", errors)
    unknown = sorted(e for e in cited if e not in evidence)
    if unknown:
        out["reasons"].append(f"unrecognized evidence ids {unknown}")
    if kind == "proposed":
        if "claim" in p:
            _err(errors, "schema", f"{path}.claim",
                 "a proposed premise carries no observation claim")
        out["status"] = "proposed_unverified"
        out["narrative"]["status"] = "proposed_unverified"
        out["reasons"].append("proposals are never supported by this contract; "
                              "citations give context, not support")
        return out
    if kind != "observed":
        _err(errors, "schema", f"{path}.kind", "must be observed or proposed")
        return out
    claim = p.get("claim")
    if claim is None:
        out["reasons"].append("labelled observed without a checkable claim")
        return out
    if not _keys(claim, _CLAIM_KEYS, _CLAIM_KEYS, f"{path}.claim", errors):
        return out
    eid, op = claim.get("evidence_id"), claim.get("op")
    if op not in CLAIM_OPS:
        _err(errors, "schema", f"{path}.claim.op", f"must be one of {CLAIM_OPS}")
        return out
    if eid not in evidence:
        out["reasons"].append(f"claim cites unrecognized evidence id {eid!r}")
        return out
    have, want = evidence[eid], claim.get("value")
    held = _check_claim(have, op, want)
    out["observation"] = {"evidence_id": eid, "op": op, "value": want,
                          "recorded_value": have, "verified": held}
    if not held:
        _err(errors, "observation_contradicts_evidence", f"{path}.claim",
             f"{eid} is {have!r}, not {op} {want!r}")
        out["status"] = "contradicted"
        return out
    try:
        canon = canonical_observation(eid, op, want, have)
    except (TypeError, ValueError):
        out["observation"]["verified"] = False
        out["reasons"].append("claim value is not canonical JSON")
        return out
    out["supported_statement"] = canon
    out["reasons"].append(f"structured claim verified: {canon}")
    if text is not None and text == canon:
        out["narrative"].update(status="canonical", flags=[])
    elif text is not None:
        out["reasons"].append("narrative is not the canonical statement and is "
                              "never verified; only supported_statement is")
        return out
    if unknown:
        return out
    out["status"] = "supported_observation"
    return out


# ── the whole thesis ─────────────────────────────────────────────────────
def validate(thesis, ctx: dict) -> dict:
    """Police one thesis against discovery context. Pure and deterministic.

    ctx keys: candidate_hash, identity_sha256, evidence_sha256,
    evidence {id: value}, rule {parts [{key,long,short}], entry_long,
    entry_short}, requires, discovery_symbols, cut_ms,
    controls {window: powered bool}, provenance_rival_required bool.
    """
    errors: list = []
    report = {"schema": SCHEMA, "status": "rejected", "errors": errors,
              "premises": [], "rivals": [], "predictions": [],
              "review_items": [], "thesis_sha256": None,
              "mechanism_status": "absent",
              "novelty": "not proven: only syntactic restatements, "
                         "tautologies, duplicates and unavailable "
                         "observables are detected"}
    if not isinstance(thesis, dict):
        _err(errors, "schema", "$", "thesis must be a JSON object")
        return report
    try:
        report["thesis_sha256"] = sha256_json(thesis)
    except (TypeError, ValueError):
        _err(errors, "schema", "$", "thesis is not canonical JSON")
        return report
    _keys(thesis, _TOP_KEYS, _TOP_KEYS, "$", errors)
    if thesis.get("schema") != SCHEMA:
        _err(errors, "schema", "$.schema", f"must be {SCHEMA!r}")
    for k in ("candidate_hash", "identity_sha256", "evidence_sha256"):
        if k in thesis and thesis.get(k) != ctx[k]:
            _err(errors, "identity_mismatch", f"$.{k}",
                 "does not pin this dossier's candidate/evidence")
    _text(thesis.get("kill_condition"), "$.kill_condition", errors, 20, 400)

    premises = thesis.get("premises")
    if not isinstance(premises, list) or not 1 <= len(premises) <= 12:
        _err(errors, "schema", "$.premises", "1-12 premises required")
        premises = []
    report["premises"] = [_premise(p, i, ctx, errors)
                          for i, p in enumerate(premises)]
    pids = [p["id"] for p in report["premises"]]
    if len(set(pids)) != len(pids):
        _err(errors, "duplicate_id", "$.premises", "premise ids repeat")
    kinds = {p["id"]: p["declared_kind"] for p in report["premises"]}

    mech = thesis.get("mechanism")
    if _keys(mech, _MECH_KEYS, _MECH_KEYS, "$.mechanism", errors):
        if not isinstance(mech.get("tag"), str) or not _TAG.match(mech["tag"]):
            _err(errors, "schema", "$.mechanism.tag", "snake_case, 3-48 chars")
        _text(mech.get("statement"), "$.mechanism.statement", errors, 40, 800)
        refs = _id_list(mech.get("premise_ids"), "$.mechanism.premise_ids",
                        errors, 1)
        for r in refs:
            if r not in kinds:
                _err(errors, "dangling_reference", "$.mechanism.premise_ids",
                     f"unknown premise {r!r}")
        if refs and not any(kinds.get(r) == "proposed" for r in refs):
            _err(errors, "mechanism_without_proposal", "$.mechanism",
                 "a mechanism built only from observations restates evidence")
        report["mechanism_status"] = "proposed_unverified"

    rivals = thesis.get("rivals")
    if not isinstance(rivals, list) or not 2 <= len(rivals) <= 8:
        _err(errors, "schema", "$.rivals", "2-8 rivals required")
        rivals = []
    rids = []
    for i, r in enumerate(rivals):
        path = f"$.rivals[{i}]"
        entry = {"id": r.get("id") if isinstance(r, dict) else None,
                 "kind": r.get("kind") if isinstance(r, dict) else None,
                 "unrecognized_evidence": []}
        if _keys(r, _RIVAL_KEYS, {"id", "kind", "text"}, path, errors):
            if not isinstance(r.get("id"), str) or not _ID["rival"].match(r["id"]):
                _err(errors, "schema", f"{path}.id", "must match R<n>")
            if r.get("kind") not in RIVAL_KINDS:
                _err(errors, "schema", f"{path}.kind",
                     f"must be one of {RIVAL_KINDS}")
            _text(r.get("text"), f"{path}.text", errors, 20, 600)
            cited = _id_list(r.get("evidence_ids", []),
                             f"{path}.evidence_ids", errors)
            entry["unrecognized_evidence"] = sorted(
                e for e in cited if e not in ctx["evidence"])
        report["rivals"].append(entry)
        rids.append(entry["id"])
    if len(set(rids)) != len(rids):
        _err(errors, "duplicate_id", "$.rivals", "rival ids repeat")
    have = {r["kind"] for r in report["rivals"]}
    need = list(REQUIRED_RIVALS)
    if ctx.get("provenance_rival_required"):
        need.append("data_provenance")
    for k in need:
        if k not in have:
            _err(errors, "missing_rival", "$.rivals", f"needs a {k} rival")

    preds = thesis.get("predictions")
    if not isinstance(preds, list) or not 2 <= len(preds) <= 4:
        _err(errors, "prediction_count", "$.predictions",
             "2-4 predictions required")
        preds = preds if isinstance(preds, list) else []
    sides = rule_sides(ctx["rule"])
    seen: dict = {}
    for i, p in enumerate(preds[:8]):
        res = _prediction(p, i, ctx, sides, errors)
        if isinstance(p, dict):
            for ref in p.get("premise_ids") or []:
                if ref not in kinds:
                    _err(errors, "dangling_reference",
                         f"$.predictions[{i}].premise_ids",
                         f"unknown premise {ref!r}")
            for ref in p.get("distinguishes") or []:
                if ref not in rids:
                    _err(errors, "dangling_reference",
                         f"$.predictions[{i}].distinguishes",
                         f"unknown rival {ref!r}")
        can = res["canonical"]
        if can:
            key = (can["target"], can["metric"], can["baseline"])
            if key in seen:
                prior = seen[key]
                code = ("duplicate_prediction" if prior["relation"] ==
                        can["relation"] else "contradictory_pair")
                res["codes"].append({"code": code,
                                     "detail": f"same test as {prior['id']}"})
                res["status"] = "refused"
            else:
                seen[key] = {"id": res["id"], "relation": can["relation"]}
        report["predictions"].append(res)
    if len({p["id"] for p in report["predictions"]}) != len(report["predictions"]):
        _err(errors, "duplicate_id", "$.predictions", "prediction ids repeat")

    for p in report["predictions"]:
        if p["status"] == "refused":
            _err(errors, "prediction_refused", f"$.predictions[{p['id']}]",
                 "; ".join(c["code"] for c in p["codes"]) or "schema")
        for item in p["review"]:
            report["review_items"].append(f"{p['id']}: {item}")

    if errors:
        report["status"] = "rejected"
    elif report["review_items"]:
        report["status"] = "needs_review"
    else:
        report["status"] = "well_formed_untested"
    return report


def verdict(*, survivor: bool, survivor_label: str, thesis_report: dict | None,
            external_validation: dict | None = None) -> dict:
    """The dossier's decision state. Gate evidence is never available to this
    slice, so the two admission-side states are unreachable by construction."""
    common = {"demo_eligible": False, "gate_writes": "none",
              "external_validation": external_validation or
              {"status": "not_consulted",
               "detail": "discovery-only tool: no held-out, referee or "
                         "gate-2 evidence is read"}}
    if not survivor:
        out = {"state": "research_only",
               "blocking_condition": f"not a discovery survivor "
                                     f"(verdict={survivor_label!r})",
               "next_action": "none; pick a current discovery survivor"}
    elif thesis_report is None:
        out = {"state": "research_only",
               "blocking_condition": "no thesis supplied",
               "next_action": "fill thesis.scaffold.json from the listed "
                              "evidence ids and rerun with --thesis"}
    elif thesis_report["status"] == "rejected":
        codes = sorted({e["code"] for e in thesis_report["errors"]})
        out = {"state": "research_only",
               "blocking_condition": f"thesis rejected: {', '.join(codes)}",
               "next_action": "write a new thesis (a frozen thesis is never "
                              "edited) that clears every listed error"}
    elif thesis_report["status"] == "needs_review":
        out = {"state": "research_only",
               "blocking_condition": "thesis needs human review: "
                                     + " | ".join(thesis_report["review_items"]),
               "next_action": "design reviewer (Codex) rules on each review "
                              "item; ambiguity is never accepted automatically"}
    else:
        out = {"state": "waiting_for_evidence",
               "blocking_condition": "predictions untested: no gate-1 held-out "
                                     "look or gate-2 prediction test is "
                                     "recorded here, and the referee stop "
                                     "(research.referee=false) is upstream",
               "next_action": "keep the thesis frozen; test it only through "
                              "the registered referee/gate-2 protocol once "
                              "the gate decision lifts the stop"}
    out.update(common)
    assert out["state"] in VERDICTS
    return out
