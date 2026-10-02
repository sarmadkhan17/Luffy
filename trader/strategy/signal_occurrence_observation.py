"""Repeated-rejection observation per strategy-signal-occurrence.v1.

A read-only, deterministic summary of persisted decision rows grouped by the
exact signal-occurrence key (`signal_occurrence.signal_occurrence` is the only
key derivation). For each key it counts the decisions that carried it and
what happened to those DECISIONS: executed, refused with codes, not executed
with explicitly empty codes, or codes not recorded (SQL NULL / legacy).

Attribution: reason codes and `executed` belong to the decision row. A
decision carrying several occurrences contributes the same decision-level
outcome to each of them, unsplit. The truthful reading is "a decision
carrying this occurrence was refused with these codes" — never "this signal
caused this refusal".

Counts are SCAN OBSERVATIONS: a 60 s loop re-observes one 4h occurrence many
times. They are not independent evidence, opportunities, episodes,
usefulness, missed profit or salience. Nothing here triggers, ranks,
thresholds or groups episodes.

Malformed source evidence is never folded into a legitimate class: present
but undecodable reason codes, an unsupported codes version, an unreadable
`executed` or unreadable `signals_json` are reported with a deterministic
reason, and malformed signal payloads never fabricate an occurrence.
"""
from __future__ import annotations

import json

from ..core.reason_codes import VERSION as REASON_CODES_VERSION, VOCABULARY
from . import signal_occurrence as so

SCHEMA = "strategy-signal-occurrence-observation.v1"

#: journal columns this observation reads — nothing else
COLUMNS = ("id", "ts", "scan_id", "symbol", "action", "executed",
           "reason_codes", "reason_codes_version", "signals_json")

# ── per-decision outcome classes (decision-level, not signal-level) ──────
EXECUTED = "executed"
CODED_REFUSAL = "coded_refusal"
UNCODED_NON_EXECUTION = "uncoded_non_execution"
CODES_NOT_RECORDED = "codes_not_recorded"
INVALID_OUTCOME_EVIDENCE = "invalid_outcome_evidence"

# ── reason_codes state carried in each history entry ────────────────────
CODES_RECORDED = "recorded"            # a decoded list (possibly [])
CODES_NULL = "not_recorded"            # SQL NULL
CODES_INVALID = "invalid"

# ── invalid source-evidence reasons ─────────────────────────────────────
REASON_CODES_UNDECODABLE = "reason_codes_undecodable"
REASON_CODES_NOT_LIST = "reason_codes_not_list"
REASON_CODES_NON_STRING = "reason_codes_non_string_entry"
REASON_CODES_UNKNOWN_CODE = "reason_codes_unknown_code"
REASON_CODES_VERSION_UNSUPPORTED = "reason_codes_version_unsupported"
REASON_CODES_VERSION_WITHOUT_CODES = "reason_codes_version_without_codes"
EXECUTED_UNREADABLE = "executed_unreadable"
SIGNALS_JSON_UNDECODABLE = "signals_json_undecodable"
SIGNALS_JSON_NOT_LIST = "signals_json_not_list"
SIGNALS_JSON_NOT_RECORDED = "signals_json_not_recorded"
#: keyless reasons for list entries signal_occurrence() cannot be asked
#: about (they are not signal payloads at all)
SIGNAL_ENTRY_NOT_OBJECT = "signal_entry_not_object"
SIGNAL_ENTRY_UNREADABLE = "signal_entry_unreadable"


def _decode_codes(raw, version):
    """(state, codes, invalid_reason). Strict: unlike reason_codes.decode, a present
    but unreadable value is INVALID, never NULL/not-recorded."""
    if raw is None:
        if version is not None:
            return CODES_INVALID, None, REASON_CODES_VERSION_WITHOUT_CODES
        return CODES_NULL, None, None
    if version != REASON_CODES_VERSION:
        return CODES_INVALID, None, REASON_CODES_VERSION_UNSUPPORTED
    try:
        codes = json.loads(raw)
    except (TypeError, ValueError):
        return CODES_INVALID, None, REASON_CODES_UNDECODABLE
    if not isinstance(codes, list):
        return CODES_INVALID, None, REASON_CODES_NOT_LIST
    if not all(isinstance(c, str) for c in codes):
        return CODES_INVALID, None, REASON_CODES_NON_STRING
    if not all(c in VOCABULARY for c in codes):
        return CODES_INVALID, None, REASON_CODES_UNKNOWN_CODE
    return CODES_RECORDED, codes, None


def _decode_executed(raw):
    """True/False for a stored 0/1, else None (unreadable)."""
    if type(raw) is int and raw in (0, 1):
        return bool(raw)
    return None


def _classify(executed, state, codes):
    """A valid persisted executed fact wins: malformed codes on an executed
    row are surfaced as a diagnostic but never erase the execution."""
    if executed is None:
        return INVALID_OUTCOME_EVIDENCE
    if executed:
        return EXECUTED
    if state == CODES_INVALID:
        return INVALID_OUTCOME_EVIDENCE
    if state == CODES_NULL:
        return CODES_NOT_RECORDED
    return CODED_REFUSAL if codes else UNCODED_NON_EXECUTION


def _decode_signals(raw):
    """(list, None) or (None, invalid_reason)."""
    if raw is None:
        return None, SIGNALS_JSON_NOT_RECORDED
    try:
        sigs = json.loads(raw)
    except (TypeError, ValueError):
        return None, SIGNALS_JSON_UNDECODABLE
    if not isinstance(sigs, list):
        return None, SIGNALS_JSON_NOT_LIST
    return sigs, None


def _occurrence(entry):
    if not isinstance(entry, dict):
        return None, SIGNAL_ENTRY_NOT_OBJECT
    try:
        key, why = so.signal_occurrence(entry)
    except Exception:           # e.g. params not a mapping, symbol not str
        return None, SIGNAL_ENTRY_UNREADABLE
    if key is None and not isinstance(why, str):
        return None, SIGNAL_ENTRY_UNREADABLE    # persisted reason unreadable
    return key, why


def _row_sort_key(row):
    return (str(row.get("ts")), str(row.get("id")))


def observe(rows) -> dict:
    """Summarise decision rows (dicts with COLUMNS). Pure; output depends on
    row content only, never on row order."""
    rows = sorted(rows, key=_row_sort_key)
    groups: dict[tuple, dict] = {}
    keyless: dict[str, dict] = {}
    invalid: list[dict] = []
    n_duplicate_entries = 0

    for row in rows:
        did, ts = row.get("id"), row.get("ts")
        state, codes, codes_bad = _decode_codes(
            row.get("reason_codes"), row.get("reason_codes_version"))
        executed = _decode_executed(row.get("executed"))
        if codes_bad:
            invalid.append({"decision_id": did, "ts": ts,
                            "field": "reason_codes", "reason": codes_bad})
        if executed is None:
            invalid.append({"decision_id": did, "ts": ts,
                            "field": "executed", "reason": EXECUTED_UNREADABLE})
        outcome = _classify(executed, state, codes)

        sigs, sigs_bad = _decode_signals(row.get("signals_json"))
        if sigs_bad:
            invalid.append({"decision_id": did, "ts": ts,
                            "field": "signals_json", "reason": sigs_bad})
            continue

        keys: list[tuple] = []
        for entry in sigs:
            key, why = _occurrence(entry)
            if key is None:
                k = keyless.setdefault(why, {"n_signal_entries": 0,
                                             "decision_ids": set()})
                k["n_signal_entries"] += 1
                k["decision_ids"].add(did)
            elif key in keys:
                n_duplicate_entries += 1        # never count one row twice
            else:
                keys.append(key)

        for key in keys:
            g = groups.setdefault(key, {"counts": dict.fromkeys(
                (EXECUTED, CODED_REFUSAL, UNCODED_NON_EXECUTION,
                 CODES_NOT_RECORDED, INVALID_OUTCOME_EVIDENCE), 0),
                "history": []})
            g["counts"][outcome] += 1
            g["history"].append({
                "ts": ts, "decision_id": did, "scan_id": row.get("scan_id"),
                "decision_symbol": row.get("symbol"),
                "decision_action": row.get("action"),
                "executed": executed,
                "decision_outcome": outcome,
                "reason_codes_state": state,
                "reason_codes": list(codes) if codes is not None else None,
                "reason_codes_invalid": codes_bad,
                # the outcome is shared unsplit by every occurrence this
                # decision carried — context, not attribution
                "n_occurrences_in_decision": len(keys),
            })

    occurrences = []
    for key in sorted(groups):
        g, c = groups[key], groups[key]["counts"]
        spec_id, fp, symbol, action, tf, close_ms = key
        hist = g["history"]
        occurrences.append({
            "occurrence_version": so.VERSION,
            "spec_id": spec_id, "spec_fingerprint": fp, "symbol": symbol,
            "action": action, "signal_timeframe": tf,
            "signal_bar_close_ms": close_ms,
            "n_observing_decisions": len(hist),
            "n_executed": c[EXECUTED],
            "n_coded_refusal": c[CODED_REFUSAL],
            "n_uncoded_non_execution": c[UNCODED_NON_EXECUTION],
            "n_codes_not_recorded": c[CODES_NOT_RECORDED],
            "n_invalid_outcome_evidence": c[INVALID_OUTCOME_EVIDENCE],
            "executed_ever": c[EXECUTED] > 0,
            "first_observed_ts": hist[0]["ts"],
            "last_observed_ts": hist[-1]["ts"],
            "history": hist,
        })

    return {
        "schema": SCHEMA,
        "occurrence_version": so.VERSION,
        "reason_codes_version": REASON_CODES_VERSION,
        "counts_are": "scan_observations",
        "n_decisions_read": len(rows),
        "n_duplicate_occurrence_entries": n_duplicate_entries,
        "occurrences": occurrences,
        "keyless_signals": {
            why: {"n_signal_entries": k["n_signal_entries"],
                  "n_decisions": len(k["decision_ids"])}
            for why, k in sorted(keyless.items())},
        "invalid_source_evidence": sorted(
            invalid, key=lambda e: (str(e["ts"]), str(e["decision_id"]),
                                    e["field"])),
    }


def observe_journal(journal) -> dict:
    return observe(journal.decision_observation_rows())
