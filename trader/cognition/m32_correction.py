"""Fail-closed M3.2 corrective sufficiency audit.

This module reads the frozen PIT artifact and optional accounting stores.  It
never mutates either source and never runs the M3.2 search.
"""
from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .m32_protocol import ARTIFACT_SHA256, LOCKED, load_locked_artifact, rows_of
from .m32_evidence import complete_actual, eligible_actuals, independence_groups, pit_regime

AUDIT_SCHEMA = "m3.2-corrective-sufficiency-audit.v2"
AUDIT_FILENAME = "2026-09-19-m32-corrective-sufficiency-audit-v2.json"


def _sha(path: Path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def _finite(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _complete_actual(payload: dict) -> bool:
    """Replay canonical typed execution receipts, including whole-trade captures."""
    return complete_actual(payload)


def _explicit_failure(row: dict) -> bool:
    labels = row.get("labels") or {}
    evidence = row.get("failure") or row.get("failure_evidence")
    return (row.get("row_type") == "failure" and isinstance(evidence, dict)
            and isinstance(evidence.get("status"), str)
            and isinstance(labels.get("actual_status"), str)
            and labels.get("actual_status") == "verified_failure")


def _read_typed_store(path: Path):
    counts = Counter()
    accepted = []
    refusal = Counter()
    if not path.is_file():
        refusal["source_missing"] += 1
        return counts, accepted, refusal
    try:
        db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        cols = {r[1] for r in db.execute("pragma table_info(typed_outcomes)")}
        if not {"payload", "imported_ms"} <= cols:
            refusal["typed_outcome_schema_missing"] += 1
            db.close()
            return counts, accepted, refusal
        for payload, imported_ms in db.execute("select payload, imported_ms from typed_outcomes"):
            try:
                obj = json.loads(payload)
            except (TypeError, ValueError):
                refusal["invalid_typed_outcome_json"] += 1
                continue
            actual = (obj.get("actual_execution") or {}) if isinstance(obj, dict) else {}
            status = actual.get("status") if isinstance(actual, dict) else None
            counts[status or "absent"] += 1
            if _complete_actual(obj):
                accepted.append({"record": obj, "local_imported_ms": imported_ms})
            elif status in {"verified_actual", "verified_failure"}:
                refusal["incomplete_authoritative_outcome"] += 1
        db.close()
    except sqlite3.Error:
        refusal["typed_outcome_store_unreadable"] += 1
    return counts, accepted, refusal


def _read_accounting(path: Path):
    refusal = Counter(); accepted = []
    if not path.is_file():
        refusal["source_missing"] += 1
        return accepted, refusal
    try:
        db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        tables = {r[0] for r in db.execute("select name from sqlite_master where type='table'")}
        if "execution_accounting" not in tables:
            refusal["execution_accounting_table_missing"] += 1
        else:
            count = db.execute("select count(*) from execution_accounting").fetchone()[0]
            if not count:
                refusal["execution_accounting_table_empty"] += 1
            for (raw,) in db.execute("select payload from execution_accounting"):
                try:
                    obj = json.loads(raw)
                    if (isinstance(obj, dict) and _complete_actual(obj.get("record"))
                            and type(obj.get("local_imported_ms")) is int):
                        accepted.append(obj)
                    else:
                        refusal["canonical_receipt_or_local_clock_missing"] += 1
                except (TypeError, ValueError):
                    refusal["invalid_accounting_json"] += 1
        if "trade_accounting_bookings" in tables:
            for (raw,) in db.execute("select payload from trade_accounting_bookings"):
                try: obj = json.loads(raw)
                except (TypeError, ValueError):
                    refusal["invalid_booking_json"] += 1; continue
                refusal["booking_not_complete_funding_fee_verified"] += 1
        db.close()
    except sqlite3.Error:
        refusal["accounting_store_unreadable"] += 1
    return accepted, refusal


def _row_outcome_summary(rows):
    status = Counter((r.get("labels") or {}).get("actual_status", "absent") for r in rows)
    return {"status_counts": dict(sorted(status.items())),
            "verified_actual": status.get("verified_actual", 0),
            "verified_failure": status.get("verified_failure", 0),
            "unknown_or_unavailable": status.get("unknown", 0) + status.get("unavailable", 0)}


def _coverage(rows):
    months = set(); regimes = Counter(); symbols = set(); groups = Counter()
    clocks = []
    for row in rows:
        f = row.get("features") or {}; c = row.get("clocks") or {}
        clock = f.get("as_of_ms") if row.get("point_in_time_features") else c.get("known_ms")
        if isinstance(clock, int):
            clocks.append(clock); months.add(datetime.fromtimestamp(clock / 1000, timezone.utc).strftime("%Y-%m"))
        regime = pit_regime(row)
        regimes[regime] += 1; symbols.add(row.get("symbol")); groups[row.get("dependence_group", "unknown")] += 1
    return {"calendar_months": sorted(months), "calendar_month_count": len(months),
            "regimes": dict(sorted(regimes.items())), "non_unknown_regime_count": len(set(regimes) - {"unknown"}),
            "symbols": sorted(x for x in symbols if x), "symbol_count": len(symbols),
            "clock_min_ms": min(clocks) if clocks else None, "clock_max_ms": max(clocks) if clocks else None,
            "dependence_groups": dict(sorted(groups.items())), "effective_independent_groups": len(groups),
            "effective_independent_sample_size": None}


def build_independence_groups(rows: Iterable[dict], pad=14_400_000):
    return independence_groups(list(rows), pad)


def audit(artifact=None, investigation_db=None, luffy_db=None):
    artifact = artifact or load_locked_artifact(); rows = rows_of(artifact)
    investigation_db = Path(investigation_db or "data/investigation.db")
    luffy_db = Path(luffy_db or "data/luffy.db")
    typed_counts, typed_accepted, typed_refusal = _read_typed_store(investigation_db)
    accounting_accepted, accounting_refusal = _read_accounting(luffy_db)
    dataset = artifact["dataset"]
    declaration = dataset["declaration"]
    envelopes = [e for e in dataset.get("receipts", [])
                 if e.get("record", {}).get("kind") == "executed_trade"]
    embedded, _ = eligible_actuals(rows, envelopes, declaration)
    joined, join_refusal = eligible_actuals(rows, envelopes + typed_accepted + accounting_accepted, declaration)
    embedded_verified, verified_after = len(embedded), len(joined)
    coverage = _coverage(rows)
    independence = build_independence_groups(rows, declaration["dependence"]["window_pad_ms"])
    refusal = typed_refusal + accounting_refusal
    refusal.update(join_refusal)
    if not typed_accepted: refusal["no_verified_typed_outcomes"] += 1
    if not accounting_accepted: refusal["no_verified_accounting_outcomes"] += 1
    power_reasons = []
    if independence["effective_groups_after"] < 3: power_reasons.append("dependence_groups_below_3")
    if coverage["calendar_month_count"] < 3: power_reasons.append("calendar_months_below_3")
    if coverage["non_unknown_regime_count"] < 2: power_reasons.append("regime_coverage_insufficient")
    if verified_after == 0: power_reasons.append("verified_outcomes_zero")
    if independence["refusal"]: power_reasons.append("dependence_validation_failed")
    power_reasons.append("group_null_and_power_protocol_not_validated")
    return {"schema": AUDIT_SCHEMA, "artifact_sha256": ARTIFACT_SHA256, "dataset_id": LOCKED["dataset_id"],
            "dataset_version": LOCKED["dataset_version"], "search_rerun": False,
            "outcome_eligibility": {"before": _row_outcome_summary(rows), "embedded_verified_actual": embedded_verified,
                "typed_store_status_counts": dict(sorted(typed_counts.items())), "verified_outcome_count_after": verified_after,
                "replayable_typed_receipts_before_join": len(typed_accepted), "replayable_accounting_receipts_before_join": len(accounting_accepted),
                "unknown_unavailable_remain": _row_outcome_summary(rows)["unknown_or_unavailable"],
                "refusals": dict(sorted(refusal.items())),
                "explicit_failure_rows_authoritative": sum(1 for r in rows if _explicit_failure(r))},
            "persistence_search": {"registered": True, "label_family": "persistence", "search_code_fixed": True,
                "eligible_price_persistence_rows": sum(1 for r in rows if r.get("row_type") != "sequence" and _finite((r.get("labels") or {}).get("price_change_bps")))},
            "dependence": {"before": coverage["effective_independent_groups"], "after": independence["effective_groups_after"],
                "effective_independent_sample_size": None,
                "independent_units_upper_bound": independence["effective_groups_after"], "group_audit": independence,
                "membership_provenance_preserved": True, "point_in_time_clocks_preserved": True},
            "coverage": coverage, "power_audit": {"pass": False, "necessary_floors_pass": len(power_reasons) == 1, "reasons": power_reasons},
            "source_hashes": {str(p): _sha(p) for p in (investigation_db, luffy_db) if p.is_file()}}


def write_immutable(report, path: Path):
    encoded = json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n"
    if path.exists():
        if path.read_text(encoding="utf-8") != encoded: raise ValueError("audit_result_conflict")
        return "unchanged"
    path.parent.mkdir(parents=True, exist_ok=True); path.write_text(encoded, encoding="utf-8"); return "written"
