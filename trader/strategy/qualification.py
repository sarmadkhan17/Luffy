"""The one authoritative Backtest PASS for an exact strategy version.

LUFFY's lifecycle is

    CREATE -> BACKTEST QUALIFICATION -> >=N PAPER TRADES -> OWNER APPROVAL
    + EXISTING LIVE SAFETY -> BOUNDED LIVE ELIGIBILITY

This module is only the first arrow. It adds no statistics of its own: the
verdict is the Analyst's existing admission decision (recent pooled profit
factor after modelled costs over a minimum trade count, the dependence-aware
rotation null on the common calendar, signal redundancy), measured under the
Factory exit semantics the paper/live engine will run. What this module
contributes is a canonical, hashable record of that decision bound to one
frozen spec, and a pure re-derivation of the verdict from the numbers stored in
it, so a stored PASS cannot be forged by setting a flag.

It consumes no research error budget and reads no protected held-out slice:
the evidence is the same recent-window evidence the Analyst has always used.
A Referee-registered look, when one exists, remains research evidence; it is
no longer a prerequisite for paper probation.
"""
from __future__ import annotations

import hashlib
import json
import math

SCHEMA = "strategy-backtest-qualification.v1"
SOURCE_KIND = "backtest_qualification"
PASS = "PASS"
#: what a qualification grants. Paper probation only; never live authority.
SCOPE = "PAPER_PROBATION_ENTRY_ONLY"

_THRESHOLDS = ("min_trades", "min_pf", "null_max_p", "max_signal_overlap")

#: two strategies firing on the same bars in the same direction are one
#: strategy wearing two names, however well each backtests alone
MAX_SIGNAL_OVERLAP = 0.6


class QualificationRefused(ValueError):
    """The record is not a valid Backtest PASS; `.code` is the stable reason."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _refuse(code: str):
    raise QualificationRefused(code)


def _canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      allow_nan=False)


def digest(obj) -> str:
    return hashlib.sha256(_canonical(obj).encode()).hexdigest()


def _finite(x) -> bool:
    return (isinstance(x, (int, float)) and not isinstance(x, bool)
            and math.isfinite(x))


def thresholds(cfg: dict) -> dict:
    """The admission thresholds in force, defaults included, exactly as
    `Analyst.evaluate`/`admit` read them."""
    s = (cfg or {}).get("strategies") or {}
    return {"min_trades": int(s.get("select_min_trades", 20)),
            "min_pf": float(s.get("select_min_pf", 1.15)),
            "null_max_p": float(s.get("select_null_max_p", 0.01)),
            "max_signal_overlap": MAX_SIGNAL_OVERLAP}


def require_configured_strictness(rec: dict, cfg: dict) -> None:
    """A record may only be turned into a version if it was judged at least as
    strictly as the configuration in force now. A looser recorded threshold is
    a different (weaker) gate and is refused, not trusted."""
    cur, got = thresholds(cfg), rec["thresholds"]
    if (got["min_trades"] < cur["min_trades"] or got["min_pf"] < cur["min_pf"]
            or got["null_max_p"] > cur["null_max_p"]
            or got["max_signal_overlap"] > cur["max_signal_overlap"]):
        _refuse("qualification_thresholds_weaker_than_configured")


def _clean_symbols(per_symbol: dict) -> dict:
    out = {}
    for sym, r in sorted((per_symbol or {}).items()):
        out[str(sym)] = {"trades": int(r["trades"]), "pf": float(r["pf"]),
                         "pnl": float(r["pnl"]), "wr": float(r["wr"])}
    return out


def build(*, spec_id: str, timeframe: str, evaluated_spec_sha256: str,
          declared_universe: dict, declared_regime_filter: list,
          exit_semantics_id: str, ev: dict, th: dict, run: dict) -> dict:
    """The record for a PASSED admission. `ev` is `Analyst.admit`'s evidence.

    The verdict is not copied from `ev`: it is derived from the numbers by
    `verify`, and building refuses unless that derivation passes."""
    recent = ev.get("recent") or {}
    joint = ev.get("null_dependence") or {}
    rec = {
        "schema": SCHEMA, "scope": SCOPE, "spec_id": spec_id,
        "timeframe": timeframe,
        "evaluated_spec_sha256": evaluated_spec_sha256,
        # order is part of the spec's identity, so it is kept as declared
        "declared_universe": {
            "include": list(declared_universe.get("include") or []),
            "exclude": list(declared_universe.get("exclude") or [])},
        "declared_regime_filter": list(declared_regime_filter or []),
        "exit_semantics_id": exit_semantics_id,
        "recent": {"recent_days": float(recent["recent_days"]),
                   "window_days": float(recent.get("window_days",
                                                   recent["recent_days"])),
                   "trades": int(recent["trades"]),
                   "pooled_pf": float(recent["pooled_pf"]),
                   "winrate": float(recent["winrate"]),
                   "pnl": float(recent["pnl"]),
                   "per_symbol": _clean_symbols(recent.get("per_symbol"))},
        "robustness": {
            "consistency_p_dep": ev.get("null_consistency_p"),
            "common_rotation_p": (joint.get("common_rotation") or {}).get("p"),
            "symbols": int(ev.get("null_symbols") or 0)},
        "redundancy": {
            "max_overlap": float((ev.get("redundancy") or {})["max_overlap"]),
            "twin": (ev.get("redundancy") or {}).get("twin") or ""},
        "thresholds": {k: th[k] for k in _THRESHOLDS},
        "run": {k: run.get(k) for k in (
            "contract", "spec_digest", "data_digest", "config_digest",
            "engine_digest", "identity")},
        "verdict": PASS,
    }
    rec["qualification_id"] = digest(rec)
    verify(rec)
    return rec


def verify(rec: dict) -> dict:
    """Re-derive the verdict from the recorded numbers and thresholds.

    Pure; raises `QualificationRefused`. A record passes only if its id is the
    hash of its content, its numbers reconcile with each other, and every
    recorded threshold is met by the recorded measurement."""
    if not isinstance(rec, dict) or rec.get("schema") != SCHEMA:
        _refuse("qualification_schema")
    body = {k: v for k, v in rec.items() if k != "qualification_id"}
    try:
        sealed = digest(body)
    except (TypeError, ValueError):
        _refuse("qualification_not_canonical")
    if sealed != rec.get("qualification_id"):
        _refuse("qualification_id_mismatch")
    if rec.get("verdict") != PASS or rec.get("scope") != SCOPE:
        _refuse("qualification_not_pass")
    th = rec.get("thresholds") or {}
    if set(th) != set(_THRESHOLDS) or not all(_finite(th[k])
                                              for k in _THRESHOLDS):
        _refuse("qualification_thresholds_missing")
    if th["min_trades"] < 1 or th["min_pf"] <= 0 or not \
            0 < th["null_max_p"] < 1 or not 0 < th["max_signal_overlap"] <= 1:
        _refuse("qualification_thresholds_invalid")
    for k in ("spec_id", "timeframe", "evaluated_spec_sha256",
              "exit_semantics_id"):
        if not isinstance(rec.get(k), str) or not rec[k]:
            _refuse(f"qualification_missing:{k}")
    run = rec.get("run") or {}
    if not run.get("identity") or not run.get("data_digest"):
        _refuse("qualification_run_identity_missing")
    r = rec.get("recent") or {}
    syms = r.get("per_symbol") or {}
    if not syms:
        _refuse("qualification_no_symbols_scored")
    try:
        trades = sum(int(s["trades"]) for s in syms.values())
        pnl = sum(float(s["pnl"]) for s in syms.values())
    except (KeyError, TypeError, ValueError):
        _refuse("qualification_symbol_evidence_malformed")
    if not all(_finite(r.get(k)) for k in
               ("trades", "pooled_pf", "winrate", "pnl", "window_days")):
        _refuse("qualification_recent_not_finite")
    if trades != r["trades"]:
        _refuse("qualification_trades_do_not_reconcile")
    # per-symbol pnl is rounded to cents, pooled pnl once more
    if abs(pnl - r["pnl"]) > 0.01 * (len(syms) + 1):
        _refuse("qualification_pnl_does_not_reconcile")
    if not 0.0 <= r["winrate"] <= 1.0:
        _refuse("qualification_winrate_invalid")
    if r["trades"] < th["min_trades"]:
        _refuse("qualification_insufficient_trades")
    if r["pooled_pf"] < th["min_pf"]:
        _refuse("qualification_profit_factor_below_floor")
    rb = rec.get("robustness") or {}
    ps = (rb.get("consistency_p_dep"), rb.get("common_rotation_p"))
    if not all(_finite(p) for p in ps):
        _refuse("qualification_rotation_evidence_unavailable")
    if max(ps) > th["null_max_p"]:
        _refuse("qualification_rotation_null_not_rejected")
    ov = (rec.get("redundancy") or {}).get("max_overlap")
    if not _finite(ov) or ov > th["max_signal_overlap"]:
        _refuse("qualification_signal_overlap")
    return rec
