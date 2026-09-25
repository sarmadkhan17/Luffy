"""Opportunity Context — opportunity-context.v1.

One immutable, versioned object that binds the existing, already-persisted
evidence for ONE candidate instrument at ONE point-in-time cut:

- the Attention scan row for the candidate (scan identity, rank, selection
  reason) and, when supplied, the frozen investigation-slot allocation;
- the registry Attention `Selection` that named the instrument, when supplied;
- the canonical instrument identity, only when it is actually supplied;
- exact strategy-signal-occurrence identities (strategy-signal-occurrence.v1);
- the investigation case and its latest update, when present;
- a WorldModel / WorldModelRecord identity, only when actually supplied;
- the source/config/code identities those records already carry.

It is references and evidence only. It adds no salience, probability,
expected return, usefulness, direction, asset attribution or opportunity
score, and it copies no Attention salience/component value. It has no Risk,
Execution, order or allocation authority, makes no LLM/network/venue call
and reads no clock. SIGNAL_OCCURRENCE_IS_NOT_OPPORTUNITY_EPISODE holds:
occurrence keys are listed as-is; nothing merges or counts them as
opportunities.

Every input is verified against its own source contract before any
reference is extracted: supported schema, source-defined identities
recomputed from content, cross-record relationships, and every evidence
availability time against the cut. A hash computed here over supplied data
is a payload reference, not provenance; provenance is claimed only where a
persisted record binds that hash (the allocation decision binds the scan
payload; the allocation's ledger state binds case payloads).

Every section is either AVAILABLE or UNKNOWN with an explicit reason. Evidence
for another instrument, or evidence not available at the cut, is refused
rather than silently dropped. The same persisted inputs always rebuild the
same `context_id` (sha256 of canonical JSON), and `from_json` re-verifies the
whole contract, not only the checksum.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, fields, is_dataclass

from trader.cognition import investigation as I
from trader.core.instrument_registry import is_canonical_instrument_id
from trader.engine.protective import venue_key
from trader.observability.registry_selector import _BASE, SELECTED, VENUE, Selection
from trader.observability.selection_persistence import SelectionPersistenceError, _check_selection
from trader.strategy.signal_occurrence import (ENTRY_SERIES_MISALIGNED, INVALID_OCCURRENCE_FIELDS,
                                               NO_CLOSED_BAR_IDENTITY,
                                               SIGNAL_BAR_CLOSE_UNAVAILABLE)
from trader.strategy.signal_occurrence import VERSION as OCCURRENCE_VERSION
from trader.strategy.signal_occurrence import signal_occurrence

SCHEMA_VERSION = "opportunity-context.v1"
AVAILABLE = "AVAILABLE"
UNKNOWN = "UNKNOWN"
AUTHORITY = "NONE"
BOUNDARIES = (
    "REFERENCES_AND_EVIDENCE_ONLY",
    "NO_SCORE_PROBABILITY_EXPECTED_RETURN_OR_DIRECTION",
    "NO_RISK_EXECUTION_ORDER_OR_ALLOCATION_AUTHORITY",
    "SIGNAL_OCCURRENCE_IS_NOT_OPPORTUNITY_EPISODE",
)

# Source contracts accepted (mirrors, pinned by tests against their owners:
# trader.observability.attention.SCHEMA, trader.observability.investigation).
ATTENTION_SCHEMA = "attention.telemetry.v1"
ATTENTION_TIMEFRAME = "4h"
ALLOCATION_SCHEMA = "investigation-allocation-decision.v1"
ALLOCATION_POLICY = "investigation-state-feedback.v1"
ALLOCATION_MODES = (ALLOCATION_POLICY, "legacy-top-k.v0")
MARKET = "futures"
_SCAN_FIELDS = ("scan_id", "schema_version", "timeframe", "as_of_ms", "persisted_at_ms",
                "config", "config_id", "capture_settings", "capture_settings_id",
                "code_manifest", "input_hash", "input_versions", "membership", "rows",
                "observations")
_ALLOCATION_FIELDS = frozenset({
    "schema_version", "policy_version", "mode", "refusal", "source_scan_id",
    "source_scan_sha256", "source_as_of_ms", "decision_cut_ms", "ledger_state",
    "open_episodes", "capacity", "legacy_selected", "active_skipped", "selected", "decision_id"})
_LEDGER_ACTIVE_FIELDS = frozenset({"investigation_id", "episode_id", "symbol",
                                   "registered_ms", "payload_sha256"})
_CATALOGS = {**{f: I.CATALOG_ID for f in I.FAMILIES},
             I.POSITIONING_FAMILY: I.POSITIONING_CATALOG_ID,
             I.CORRELATION_FAMILY: I.CORRELATION_CATALOG_ID}

# ── unavailable reasons (section status UNKNOWN) ──────────────────────────
NOT_SUPPLIED = "not_supplied"
CANDIDATE_NOT_IN_SCAN = "candidate_not_in_scan"
CANONICAL_ID_NOT_SUPPLIED = "canonical_instrument_id_not_supplied"
NO_SIGNAL_SUPPLIED = "no_signal_supplied"
NO_INVESTIGATION_UPDATE = "no_investigation_update"
SCAN_PAYLOAD_NOT_BOUND = "scan_payload_not_bound_by_persisted_record"
CASE_PAYLOAD_NOT_BOUND = "case_payload_not_bound_by_persisted_record"
#: the investigation ledger persists no receipt/hash for an update payload
UPDATE_PAYLOAD_NOT_BOUND = "update_payload_not_bound_by_persisted_record"
#: only the supplied update is seen; neither its predecessors nor that no later
#: update existed at the cut is established here
UPDATE_CHAIN_NOT_VERIFIED = "update_chain_and_latest_at_cut_not_verified"
#: assessment/action derive from outcome evidence this package can neither
#: bind (no receipt) nor reproduce (I.measure needs the target bars)
UPDATE_CONTENT_NOT_VERIFIED = "update_evidence_not_bound_or_reproduced"
_OCCURRENCE_REASONS = frozenset({NO_CLOSED_BAR_IDENTITY, SIGNAL_BAR_CLOSE_UNAVAILABLE,
                                 ENTRY_SERIES_MISALIGNED, INVALID_OCCURRENCE_FIELDS})

# ── refusal reasons (nothing is built) ────────────────────────────────────
INVALID_INPUT = "invalid_input"
INSTRUMENT_MISMATCH = "instrument_mismatch"
FUTURE_EVIDENCE = "future_evidence"
SOURCE_MISMATCH = "source_mismatch"
CORRUPT_EVIDENCE = "corrupt_evidence"


class OpportunityContextRefused(ValueError):
    """The supplied evidence cannot be bound truthfully; nothing is built."""

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason


def canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _sha(value) -> str:
    text = value if isinstance(value, str) else canonical(value)
    return hashlib.sha256(text.encode()).hexdigest()


_HEX64 = re.compile(r"[0-9a-f]{64}")
_STABLE = re.compile(r"[a-z]+_[0-9a-f]{16}")


def _is_ms(value) -> bool:
    return type(value) is int and value >= 0


def _text(value) -> bool:
    return isinstance(value, str) and bool(value)


def _hex(value) -> bool:
    return isinstance(value, str) and _HEX64.fullmatch(value) is not None


def _stable(value) -> bool:
    return isinstance(value, str) and _STABLE.fullmatch(value) is not None


def _refuse(reason, detail):
    raise OpportunityContextRefused(reason, detail)


def _unknown(reason: str) -> dict:
    return {"status": UNKNOWN, "reason": reason}


def _plain(value):
    """Persisted dict form of a frozen dataclass record; dicts pass through."""
    if is_dataclass(value) and not isinstance(value, type):
        return json.loads(canonical(asdict(value)))
    if isinstance(value, dict):
        try:
            return json.loads(canonical(value))
        except (TypeError, ValueError) as exc:
            raise OpportunityContextRefused(INVALID_INPUT, str(exc)) from None
    raise OpportunityContextRefused(INVALID_INPUT, type(value).__name__)


def _not_after(ms, as_of_ms, what):
    if not _is_ms(ms):
        _refuse(INVALID_INPUT, f"{what} time")
    if ms > as_of_ms:
        _refuse(FUTURE_EVIDENCE, f"{what} at {ms} > cut {as_of_ms}")


def _same(symbol, key, what):
    if not isinstance(symbol, str) or not symbol or venue_key(symbol) != key:
        _refuse(INSTRUMENT_MISMATCH, f"{what} {symbol!r} is not {key}")


def _canonical_instrument(iid, key, what):
    """Exact canonical USD-M futures ID for `key`; never a free venue/market
    or symbol spelling (registry_selector.supplemental_symbol's BASE+USDT)."""
    if not is_canonical_instrument_id(iid):
        _refuse(INVALID_INPUT, f"{what} is not canonical")
    venue, market, venue_symbol = iid.split(":")
    if venue != VENUE or market != MARKET:
        _refuse(INVALID_INPUT, f"{what} venue/market {venue}:{market} is not supported")
    if not venue_symbol.endswith("USDT") or not _BASE.fullmatch(venue_symbol[:-4]):
        _refuse(INVALID_INPUT, f"{what} venue symbol {venue_symbol!r} is not supported")
    if venue_symbol != key:
        _refuse(INSTRUMENT_MISMATCH, f"{what} {iid} is not {key}")


def _registry_mapping(selected_id, selected_symbol, key):
    """Shared by build and from_json: canonical ID and its registry spelling."""
    _canonical_instrument(selected_id, key, "registry selected_id")
    # registry_selector.supplemental_symbol: BASE/USDT:USDT for venue BASEUSDT.
    if selected_symbol != selected_id.split(":")[2][:-4] + "/USDT:USDT":
        _refuse(CORRUPT_EVIDENCE, "selected_symbol is not the registry mapping of selected_id")


def _family_catalog(trigger, family, catalog_id):
    """Shared by build and from_json: a case's family owns exactly one catalog."""
    if trigger not in _CATALOGS or family != trigger or catalog_id != _CATALOGS[trigger]:
        _refuse(CORRUPT_EVIDENCE, "investigation family/catalog")


# ── sections ──────────────────────────────────────────────────────────────

def _scan(scan, as_of_ms):
    """Verify a persisted Attention scan payload against the store contract."""
    scan = _plain(scan)
    if scan.get("schema_version") != ATTENTION_SCHEMA or scan.get("timeframe") != ATTENTION_TIMEFRAME:
        _refuse(INVALID_INPUT, "unsupported attention scan schema")
    missing = [k for k in _SCAN_FIELDS if k not in scan]
    if missing:
        _refuse(INVALID_INPUT, f"attention scan lacks {missing}")
    manifest = scan["code_manifest"]
    if (not _text(scan["scan_id"]) or not isinstance(scan["config"], dict)
            or not isinstance(scan["capture_settings"], dict) or not _hex(scan["input_hash"])
            or not isinstance(manifest, dict) or not manifest
            or not all(_text(k) and _hex(v) for k, v in manifest.items())
            or not all(isinstance(scan[k], list)
                       for k in ("input_versions", "membership", "rows", "observations"))):
        _refuse(INVALID_INPUT, "attention scan fields")
    # Content-derived identities exactly as the store derives them.
    if (scan["config_id"] != _sha(scan["config"])
            or scan["capture_settings_id"] != _sha(scan["capture_settings"])):
        _refuse(CORRUPT_EVIDENCE, "attention scan config/capture identity")
    scan_cut = scan["as_of_ms"]
    _not_after(scan_cut, as_of_ms, "attention scan")
    _not_after(scan["persisted_at_ms"], as_of_ms, "attention scan persistence")
    for what, items, name in (("scan membership", scan["membership"], "available_ms"),
                              ("scan input version", scan["input_versions"], "first_seen_ms")):
        for item in items:
            ms = item.get(name) if isinstance(item, dict) else None
            _not_after(ms, as_of_ms, what)
            if ms > scan_cut:
                _refuse(CORRUPT_EVIDENCE, f"{what} after its scan cut")
    for o in scan["observations"]:
        if not isinstance(o, dict) or o.get("as_of_ms") != scan_cut:
            _refuse(CORRUPT_EVIDENCE, "scan observation is not at its scan cut")
        times = (o.get("event_ms"), o.get("available_ms"))
        for name, ms in zip(("event_ms", "available_ms"), times):
            if ms is not None:
                _not_after(ms, as_of_ms, f"scan observation {name}")
                if ms > scan_cut:
                    _refuse(CORRUPT_EVIDENCE, f"scan observation {name} after its scan cut")
        if None not in times and times[0] > times[1]:
            _refuse(CORRUPT_EVIDENCE, "scan observation event after its availability")
    keys = []
    for row in scan["rows"]:
        if not isinstance(row, dict) or not _text(row.get("symbol")):
            _refuse(INVALID_INPUT, "attention scan row")
        keys.append(venue_key(row["symbol"]))
    if len(set(keys)) != len(keys):
        _refuse(CORRUPT_EVIDENCE, "scan has several rows for one instrument")
    return scan


def _allocation(allocation, scan, scan_sha, as_of_ms):
    body = _plain(allocation)
    if body.pop("reused", None) not in (None, True, False):   # retry marker added on read
        _refuse(INVALID_INPUT, "allocation reused marker")
    if (set(body) != _ALLOCATION_FIELDS or body["schema_version"] != ALLOCATION_SCHEMA
            or body["policy_version"] != ALLOCATION_POLICY or body["mode"] not in ALLOCATION_MODES):
        _refuse(INVALID_INPUT, "unsupported allocation schema")
    if body["decision_id"] != _sha({k: v for k, v in body.items() if k != "decision_id"}):
        _refuse(CORRUPT_EVIDENCE, "allocation decision_id")
    ledger = body["ledger_state"]
    if not isinstance(ledger, dict) or ledger.get("sha256") != _sha(
            {k: v for k, v in ledger.items() if k != "sha256"}):
        _refuse(CORRUPT_EVIDENCE, "allocation ledger_state sha256")
    active = ledger.get("active")
    if not isinstance(active, list) or not all(
            isinstance(a, dict) and set(a) == _LEDGER_ACTIVE_FIELDS for a in active):
        _refuse(INVALID_INPUT, "allocation ledger_state active")
    if (body["source_scan_id"] != scan["scan_id"] or body["source_scan_sha256"] != scan_sha
            or body["source_as_of_ms"] != scan["as_of_ms"]):
        _refuse(SOURCE_MISMATCH, "allocation is for another scan")
    cut = body["decision_cut_ms"]
    _not_after(cut, as_of_ms, "allocation decision")
    if cut < scan["as_of_ms"]:
        _refuse(CORRUPT_EVIDENCE, "allocation decided before its scan")
    for a in active:
        _not_after(a["registered_ms"], as_of_ms, "allocation ledger registration")
        if a["registered_ms"] > cut:
            _refuse(CORRUPT_EVIDENCE, "allocation ledger registration after its decision")
    selected = body["selected"]
    if not isinstance(selected, list) or not all(_text(s) for s in selected):
        _refuse(INVALID_INPUT, "allocation selected")
    return body, active


def _attention(scan, allocation, key, as_of_ms):
    if scan is None:
        if allocation is not None:
            _refuse(INVALID_INPUT, "allocation without its scan")
        return _unknown(NOT_SUPPLIED), None, []
    scan = _scan(scan, as_of_ms)
    scan_sha = _sha(scan)
    out = {"status": AVAILABLE, "reason": None,
           "scan_id": scan["scan_id"], "scan_sha256": scan_sha,
           "scan_schema_version": scan["schema_version"],
           "scan_as_of_ms": scan["as_of_ms"], "scan_persisted_at_ms": scan["persisted_at_ms"],
           "config_id": scan["config_id"], "capture_settings_id": scan["capture_settings_id"],
           "code_manifest_sha256": _sha(scan["code_manifest"]), "input_hash": scan["input_hash"]}
    rows = [r for r in scan["rows"] if venue_key(r["symbol"]) == key]
    if rows:
        r = rows[0]
        # Selection provenance only. Salience and component values stay in
        # the scan; they are referenced by scan_sha256, never copied or used.
        out["candidate"] = {"status": AVAILABLE, "reason": None, "scan_symbol": r["symbol"],
                            "row_status": r.get("status"), "eligible": r.get("eligible"),
                            "rank": r.get("rank"), "selection_reason": r.get("reason"),
                            "selected": r.get("selected"),
                            "open_episode_id": r.get("open_episode_id")}
    else:
        out["candidate"] = _unknown(CANDIDATE_NOT_IN_SCAN)
    if allocation is None:
        out["allocation"] = _unknown(NOT_SUPPLIED)
        out["payload_binding"] = _unknown(SCAN_PAYLOAD_NOT_BOUND)
        return out, scan, []
    body, active = _allocation(allocation, scan, scan_sha, as_of_ms)
    out["allocation"] = {"status": AVAILABLE, "reason": None, "decision_id": body["decision_id"],
                         "policy_version": body["policy_version"], "mode": body["mode"],
                         "decision_cut_ms": body["decision_cut_ms"],
                         "candidate_allocated": any(venue_key(s) == key for s in body["selected"])}
    # The persisted, content-addressed allocation names this exact scan payload.
    out["payload_binding"] = {"status": AVAILABLE, "reason": None,
                              "bound_by": "allocation_decision"}
    return out, scan, active


def _selection(sel, key, as_of_ms):
    if sel is None:
        return _unknown(NOT_SUPPLIED)
    if not isinstance(sel, Selection):
        _refuse(INVALID_INPUT, "registry selection must be a Selection")
    try:
        _check_selection(sel)       # the persistence contract's own verifier
    except SelectionPersistenceError as exc:
        _refuse(INVALID_INPUT, f"registry selection: {exc}")
    if sel.outcome != SELECTED or sel.selected_symbol is None:
        _refuse(INSTRUMENT_MISMATCH, "selection names no instrument")
    _same(sel.selected_symbol, key, "registry selection")
    _registry_mapping(sel.selected_id, sel.selected_symbol, key)
    if sel.selected_id not in sel.candidate_ids:
        _refuse(CORRUPT_EVIDENCE, "selected_id is not one of the selection's candidates")
    _not_after(sel.cycle_as_of_ms, as_of_ms, "registry selection")
    _not_after(sel.snapshot_as_of_ms, as_of_ms, "registry snapshot")
    return {"status": AVAILABLE, "reason": None, "selection_id": sel.selection_id,
            "rule_version": sel.rule_version, "outcome": sel.outcome,
            "snapshot_id": sel.snapshot_id, "snapshot_as_of_ms": sel.snapshot_as_of_ms,
            "cycle_as_of_ms": sel.cycle_as_of_ms, "selected_id": sel.selected_id,
            "selected_symbol": sel.selected_symbol,
            "selected_account_eligibility": sel.selected_account_eligibility}


def _instrument(instrument_id, selection, key):
    from_selection = selection.get("selected_id") if selection["status"] == AVAILABLE else None
    if instrument_id is not None:
        _canonical_instrument(instrument_id, key, "instrument_id")
        if from_selection is not None and from_selection != instrument_id:
            _refuse(INSTRUMENT_MISMATCH, "instrument_id != selection")
    iid = instrument_id or from_selection
    if iid is None:
        return {"symbol_key": key, "canonical_id": None, "status": UNKNOWN,
                "reason": CANONICAL_ID_NOT_SUPPLIED, "source": None}
    _canonical_instrument(iid, key, "canonical instrument")
    return {"symbol_key": key, "canonical_id": iid, "status": AVAILABLE, "reason": None,
            "source": "supplied" if instrument_id is not None else "registry_selection"}


def _signal_dict(signal):
    if isinstance(signal, dict):
        return _plain(signal)
    action = getattr(signal.action, "value", signal.action)
    return _plain({"symbol": signal.symbol, "action": action,
                   "params": dict(getattr(signal, "params", None) or {})})


def _occurrences(signals, key, as_of_ms):
    out = []
    for signal in signals:
        d = _signal_dict(signal)
        _same(d.get("symbol"), key, "signal")
        occ, reason = signal_occurrence(d)
        params = d.get("params") or {}
        if occ is None:
            out.append({"status": UNKNOWN, "reason": reason, "occurrence_schema": OCCURRENCE_VERSION,
                        "key": None, "spec_id": params.get("spec_id"),
                        "action": d.get("action")})
            continue
        spec_id, fp, sym, action, tf, close_ms = occ
        _not_after(close_ms, as_of_ms, "signal bar close")
        out.append({"status": AVAILABLE, "reason": None, "occurrence_schema": OCCURRENCE_VERSION,
                    "key": {"spec_id": spec_id, "spec_fingerprint": fp, "symbol": sym,
                            "action": action, "signal_timeframe": tf,
                            "signal_bar_close_ms": close_ms},
                    "spec_id": spec_id, "action": action})
    # Order-independent; exact duplicates (the same occurrence re-observed on
    # later scans) collapse to one reference. Nothing is counted or merged.
    unique = {canonical(o): o for o in out}
    return [unique[k] for k in sorted(unique)]


def _record(value, cls, decode, what):
    """Rebuild a persisted record through its own decoder; exact fields only."""
    try:
        obj = value if isinstance(value, cls) else decode(_plain(value))
        payload = _plain(obj)
    except (TypeError, KeyError, ValueError, AttributeError) as exc:
        raise OpportunityContextRefused(INVALID_INPUT, f"{what}: {exc}") from None
    if not isinstance(value, cls) and payload != _plain(value):
        _refuse(INVALID_INPUT, f"{what} does not round-trip its schema")
    return obj, payload


def _investigation(inv, update, key, as_of_ms, scan, active):
    if inv is None:
        if update is not None:
            _refuse(INVALID_INPUT, "update without its investigation")
        return _unknown(NOT_SUPPLIED)
    obj, d = _record(inv, I.Investigation, I.investigation_from_dict, "investigation")
    s, m = obj.state, obj.measurement
    if obj.schema_version != I.SCHEMA or s.schema_version != I.SCHEMA:
        _refuse(INVALID_INPUT, "unsupported investigation schema")
    _same(s.symbol, key, "investigation")
    _family_catalog(obj.primary_trigger, m.family, m.catalog_id)
    for name in ("as_of_ms", "available_ms", "observed_ms"):
        _not_after(getattr(s, name), as_of_ms, f"investigation state {name}")
    _not_after(obj.registered_ms, as_of_ms, "investigation registration")
    if not s.available_ms <= s.as_of_ms <= s.observed_ms <= obj.registered_ms:
        _refuse(CORRUPT_EVIDENCE, "investigation state/registration order")
    # Source-defined identities (cognition.investigation.make_state/open_*).
    state_fields = {f.name: getattr(s, f.name) for f in fields(s)
                    if f.name not in ("state_id", "schema_version")}
    episode_id = I.stable_id("episode", m.catalog_id, s.symbol, obj.primary_trigger,
                             s.as_of_ms // I.TF * I.TF - I.TF)
    if (s.state_id != I.stable_id("state", state_fields) or obj.episode_id != episode_id
            or obj.investigation_id != I.stable_id("investigation", episode_id, s.state_id,
                                                   obj.registered_ms)):
        _refuse(CORRUPT_EVIDENCE, "investigation state/episode/investigation identity")
    if scan is not None and s.scan_id == scan["scan_id"]:
        manifest = json.loads(s.code_json)
        if (s.as_of_ms != scan["as_of_ms"] or s.config_id != scan["config_id"]
                or s.membership_json != I.encode(scan["membership"])
                or any(manifest.get(k) != v for k, v in scan["code_manifest"].items())):
            _refuse(SOURCE_MISMATCH, "investigation state does not match its attention scan")
    payload_sha = _sha(d)       # == sha256 of the `cases` row payload (I.encode)
    bound = False
    for a in active:            # persisted ledger evidence naming this case
        if a["investigation_id"] == obj.investigation_id or a["episode_id"] == obj.episode_id:
            if (a["investigation_id"], a["episode_id"], a["symbol"], a["registered_ms"],
                    a["payload_sha256"]) != (obj.investigation_id, obj.episode_id, s.symbol,
                                             obj.registered_ms, payload_sha):
                _refuse(SOURCE_MISMATCH, "investigation differs from the allocation ledger")
            bound = True
    out = {"status": AVAILABLE, "reason": None,
           "investigation_id": obj.investigation_id, "episode_id": obj.episode_id,
           "schema_version": obj.schema_version, "payload_sha256": payload_sha,
           "registered_ms": obj.registered_ms, "primary_trigger": obj.primary_trigger,
           "state_id": s.state_id, "source_scan_id": s.scan_id,
           "state_config_id": s.config_id, "state_code_sha256": _sha(s.code_json),
           "measurement_catalog_id": m.catalog_id,
           # IDs above are content-consistent; only a persisted hash proves the
           # whole case (measurement, alternatives, dimensions) is the stored one.
           "payload_binding": ({"status": AVAILABLE, "reason": None, "bound_by": "allocation_ledger"}
                               if bound else _unknown(CASE_PAYLOAD_NOT_BOUND))}
    if update is None:
        out["latest_update"] = _unknown(NO_INVESTIGATION_UPDATE)
        return out
    u, ud = _record(update, I.InvestigationUpdate, I.update_from_dict, "investigation update")
    ev = u.evidence
    if u.schema_version != I.SCHEMA or ev.schema_version != I.SCHEMA:
        _refuse(INVALID_INPUT, "unsupported investigation update schema")
    if u.investigation_id != obj.investigation_id or ev.investigation_id != obj.investigation_id:
        _refuse(SOURCE_MISMATCH, "update is for another investigation")
    _not_after(u.as_of_ms, as_of_ms, "investigation update as_of")
    _not_after(u.observed_ms, as_of_ms, "investigation update observed")
    _not_after(ev.as_of_ms, as_of_ms, "update evidence as_of")
    _not_after(ev.observed_ms, as_of_ms, "update evidence observed")
    if ev.available_ms is not None:
        _not_after(ev.available_ms, as_of_ms, "update evidence available")
        if ev.available_ms > ev.as_of_ms:
            _refuse(CORRUPT_EVIDENCE, "update evidence available after its own as_of")
    # Source-defined identities and relationships (measure/advance).
    if (ev.evidence_id != I.stable_id("evidence", obj.investigation_id, ev.target_versions,
                                      ev.status, ev.reason)
            or u.event_id != I.stable_id("update", obj.investigation_id, u.previous_event_id,
                                         ev.evidence_id)
            or (u.as_of_ms, u.observed_ms) != (ev.as_of_ms, ev.observed_ms)
            or u.input_ids != tuple(v for _, _, v in ev.target_versions)
            or not obj.registered_ms <= u.as_of_ms <= u.observed_ms):
        _refuse(CORRUPT_EVIDENCE, "investigation update/evidence identity")
    if u.previous_event_id is None:
        # Fail-closed consistency only: a first update must be advance(case,
        # evidence). This verifies the transformation, never its evidence input,
        # so it grants no trust to the update's content.
        try:
            replay = I.advance(obj, ev)
        except (TypeError, KeyError, ValueError, AttributeError):
            replay = None
        if replay != u:
            _refuse(CORRUPT_EVIDENCE, "update does not replay from its case and evidence")
    # Assessment, reasons, action and evidence status are withheld: referenced
    # only through payload_sha256/evidence_id, never copied as verified.
    out["latest_update"] = {"status": AVAILABLE, "reason": None, "event_id": u.event_id,
                            "previous_event_id": u.previous_event_id,
                            "evidence_id": ev.evidence_id, "payload_sha256": _sha(ud),
                            "as_of_ms": u.as_of_ms, "observed_ms": u.observed_ms,
                            "payload_binding": _unknown(UPDATE_PAYLOAD_NOT_BOUND),
                            "chain": _unknown(UPDATE_CHAIN_NOT_VERIFIED),
                            "content": _unknown(UPDATE_CONTENT_NOT_VERIFIED)}
    return out


def _world_model(world, as_of_ms):
    if world is None:
        return _unknown(NOT_SUPPLIED)
    from trader.world.model import WorldModel
    from trader.world.replay import WorldModelRecord
    if isinstance(world, WorldModelRecord):
        supplied, record_id_kept = world, True
    elif isinstance(world, WorldModel):
        supplied, record_id_kept = None, False      # not a persisted receipt: no record_id
    else:
        _refuse(INVALID_INPUT, "world model must be WorldModel/WorldModelRecord")
    try:
        if supplied is None:
            supplied = WorldModelRecord.from_model(world)
        # The record's own verifier: canonical JSON, record_id, model_id, evidence.
        record = WorldModelRecord.from_json(supplied.to_json())
        cut = record.reconstruct().as_of_ms
    except (TypeError, KeyError, ValueError, AttributeError) as exc:
        raise OpportunityContextRefused(CORRUPT_EVIDENCE, f"world model: {exc}") from None
    _not_after(cut, as_of_ms, "world model")
    return {"status": AVAILABLE, "reason": None, "as_of_ms": cut, "model_id": record.model_id,
            "record_id": record.record_id if record_id_kept else None}


# ── contract verifier (shared by build and from_json) ─────────────────────

def _any(_):
    return True


def _opt(pred):
    return lambda v: v is None or pred(v)


def _bool(v):
    return type(v) is bool


def _eq(expected):
    return lambda v: v == expected


_TOP = frozenset({"schema_version", "as_of_ms", "authority", "boundaries", "instrument",
                  "attention", "registry_selection", "signal_occurrences", "investigation",
                  "world_model", "context_id"})
_ATTENTION = {"scan_id": _text, "scan_sha256": _hex, "scan_schema_version": _eq(ATTENTION_SCHEMA),
              "scan_as_of_ms": _is_ms, "scan_persisted_at_ms": _is_ms, "config_id": _hex,
              "capture_settings_id": _hex, "code_manifest_sha256": _hex, "input_hash": _hex,
              "candidate": _any, "allocation": _any, "payload_binding": _any}
_CANDIDATE = {"scan_symbol": _text, "row_status": _opt(_text), "eligible": _opt(_bool),
              "rank": _opt(lambda v: type(v) is int and v >= 1),
              "selection_reason": _opt(_text), "selected": _opt(_bool),
              "open_episode_id": _opt(_text)}
_ALLOCATION = {"decision_id": _hex, "policy_version": _eq(ALLOCATION_POLICY),
               "mode": lambda v: v in ALLOCATION_MODES, "decision_cut_ms": _is_ms,
               "candidate_allocated": _bool}
_BINDING = {"bound_by": _eq("allocation_decision")}
_SELECTION = {"selection_id": _hex, "rule_version": _text, "outcome": _eq(SELECTED),
              "snapshot_id": _text, "snapshot_as_of_ms": _is_ms, "cycle_as_of_ms": _is_ms,
              "selected_id": _text, "selected_symbol": _text,
              "selected_account_eligibility": _opt(_text)}
_INVESTIGATION = {"investigation_id": _stable, "episode_id": _stable,
                  "schema_version": _eq(I.SCHEMA), "payload_sha256": _hex,
                  "registered_ms": _is_ms, "primary_trigger": lambda v: v in _CATALOGS,
                  "state_id": _stable, "source_scan_id": _text, "state_config_id": _hex,
                  "state_code_sha256": _hex, "measurement_catalog_id": _stable,
                  "payload_binding": _any, "latest_update": _any}
_CASE_BINDING = {"bound_by": _eq("allocation_ledger")}
_UPDATE = {"event_id": _stable, "previous_event_id": _opt(_stable), "evidence_id": _stable,
           "payload_sha256": _hex, "as_of_ms": _is_ms, "observed_ms": _is_ms,
           "payload_binding": _eq(_unknown(UPDATE_PAYLOAD_NOT_BOUND)),
           "chain": _eq(_unknown(UPDATE_CHAIN_NOT_VERIFIED)),
           "content": _eq(_unknown(UPDATE_CONTENT_NOT_VERIFIED))}
_WORLD = {"as_of_ms": _is_ms, "model_id": _text, "record_id": _opt(_text)}
_OCC_KEY = {"spec_id": _text, "spec_fingerprint": _text, "symbol": _text,
            "action": lambda v: v in ("BUY", "SELL"), "signal_timeframe": _text,
            "signal_bar_close_ms": _is_ms}


def _bad(detail):
    raise OpportunityContextRefused(CORRUPT_EVIDENCE, detail)


def _fields(value, spec, what):
    if not isinstance(value, dict) or set(value) != set(spec):
        _bad(f"{what} fields")
    for name, pred in spec.items():
        if not pred(value[name]):
            _bad(f"{what}.{name}")


def _section(value, spec, unknown_reasons, what) -> bool:
    """True when AVAILABLE with exactly `spec`'s fields; False when UNKNOWN."""
    if isinstance(value, dict) and value.get("status") == UNKNOWN:
        if set(value) != {"status", "reason"} or value["reason"] not in unknown_reasons:
            _bad(f"{what} unknown status/reason")
        return False
    if not isinstance(value, dict) or value.get("status") != AVAILABLE or value.get("reason") is not None:
        _bad(f"{what} status/reason")
    _fields(value, {"status": _any, "reason": _any, **spec}, what)
    return True


def _verify(body) -> None:
    """Semantic contract of a context body (without context_id); any failure,
    including a shared build-time check, is CORRUPT_EVIDENCE."""
    try:
        _check_body(body)
    except OpportunityContextRefused as exc:
        if exc.reason != CORRUPT_EVIDENCE:
            raise OpportunityContextRefused(CORRUPT_EVIDENCE, str(exc)) from None
        raise


def _check_body(body) -> None:
    if not isinstance(body, dict) or set(body) != _TOP - {"context_id"}:
        _bad("top-level fields")
    if body["schema_version"] != SCHEMA_VERSION:
        _bad("schema_version")
    if body["authority"] != AUTHORITY:
        _bad("authority")
    if body["boundaries"] != list(BOUNDARIES):
        _bad("boundaries")
    cut = body["as_of_ms"]
    if not _is_ms(cut):
        _bad("as_of_ms")

    def by_cut(ms, what):
        if ms > cut:
            _bad(f"{what} after the context cut")

    inst = body["instrument"]
    _fields(inst, {"symbol_key": _text, "canonical_id": _any, "status": _any, "reason": _any,
                   "source": _any}, "instrument")
    key = inst["symbol_key"]
    if venue_key(key) != key:
        _bad("instrument.symbol_key")

    sel = body["registry_selection"]
    if _section(sel, _SELECTION, {NOT_SUPPLIED}, "registry_selection"):
        by_cut(sel["cycle_as_of_ms"], "registry selection")
        by_cut(sel["snapshot_as_of_ms"], "registry snapshot")
        if venue_key(sel["selected_symbol"]) != key:
            _bad("registry_selection instrument")
        _registry_mapping(sel["selected_id"], sel["selected_symbol"], key)
    selected_id = sel["selected_id"] if sel["status"] == AVAILABLE else None

    if inst["status"] == AVAILABLE:
        try:
            _canonical_instrument(inst["canonical_id"], key, "instrument")
        except OpportunityContextRefused as exc:
            _bad(str(exc))
        if (inst["reason"] is not None or inst["source"] not in ("supplied", "registry_selection")
                or (selected_id is not None and selected_id != inst["canonical_id"])
                or (inst["source"] == "registry_selection" and selected_id is None)):
            _bad("instrument status/source")
    elif (inst["status"] != UNKNOWN or inst["reason"] != CANONICAL_ID_NOT_SUPPLIED
          or inst["canonical_id"] is not None or inst["source"] is not None
          or selected_id is not None):
        _bad("instrument status/reason")

    att = body["attention"]
    if _section(att, _ATTENTION, {NOT_SUPPLIED}, "attention"):
        by_cut(att["scan_as_of_ms"], "attention scan")
        by_cut(att["scan_persisted_at_ms"], "attention scan persistence")
        if (_section(att["candidate"], _CANDIDATE, {CANDIDATE_NOT_IN_SCAN}, "attention.candidate")
                and venue_key(att["candidate"]["scan_symbol"]) != key):
            _bad("attention.candidate instrument")
        allocated = _section(att["allocation"], _ALLOCATION, {NOT_SUPPLIED}, "attention.allocation")
        bound = _section(att["payload_binding"], _BINDING, {SCAN_PAYLOAD_NOT_BOUND},
                         "attention.payload_binding")
        if allocated != bound:
            _bad("attention payload binding without its allocation")
        if allocated:
            by_cut(att["allocation"]["decision_cut_ms"], "allocation decision")
            if att["allocation"]["decision_cut_ms"] < att["scan_as_of_ms"]:
                _bad("allocation decided before its scan")

    occ = body["signal_occurrences"]
    _fields(occ, {"status": _any, "reason": _any, "items": lambda v: isinstance(v, list)},
            "signal_occurrences")
    items = occ["items"]
    if (occ["status"], occ["reason"]) != ((AVAILABLE, None) if items else (UNKNOWN, NO_SIGNAL_SUPPLIED)):
        _bad("signal_occurrences status/reason")
    texts = [canonical(i) for i in items]
    if texts != sorted(set(texts)):
        _bad("signal_occurrences are not canonically ordered and unique")
    for item in items:
        _fields(item, {"status": _any, "reason": _any, "occurrence_schema": _eq(OCCURRENCE_VERSION),
                       "key": _any, "spec_id": _any, "action": _any}, "signal occurrence")
        if item["status"] == AVAILABLE:
            k = item["key"]
            _fields(k, _OCC_KEY, "signal occurrence key")
            if (item["reason"] is not None or k["symbol"] != key or item["spec_id"] != k["spec_id"]
                    or item["action"] != k["action"]):
                _bad("signal occurrence identity")
            by_cut(k["signal_bar_close_ms"], "signal bar close")
        elif (item["status"] != UNKNOWN or item["reason"] not in _OCCURRENCE_REASONS
              or item["key"] is not None or not _opt(_text)(item["spec_id"])
              or not _opt(_text)(item["action"])):
            _bad("signal occurrence status/reason")

    inv = body["investigation"]
    if _section(inv, _INVESTIGATION, {NOT_SUPPLIED}, "investigation"):
        by_cut(inv["registered_ms"], "investigation registration")
        _family_catalog(inv["primary_trigger"], inv["primary_trigger"], inv["measurement_catalog_id"])
        bound = _section(inv["payload_binding"], _CASE_BINDING, {CASE_PAYLOAD_NOT_BOUND},
                         "investigation.payload_binding")
        if bound and att.get("allocation", {}).get("status") != AVAILABLE:
            _bad("case payload binding without its allocation")
        upd = inv["latest_update"]
        if _section(upd, _UPDATE, {NO_INVESTIGATION_UPDATE}, "investigation.latest_update"):
            by_cut(upd["as_of_ms"], "investigation update as_of")
            by_cut(upd["observed_ms"], "investigation update observed")
            if not inv["registered_ms"] <= upd["as_of_ms"] <= upd["observed_ms"]:
                _bad("investigation update order")

    world = body["world_model"]
    if _section(world, _WORLD, {NOT_SUPPLIED}, "world_model"):
        by_cut(world["as_of_ms"], "world model")


# ── the object ────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class OpportunityContext:
    context_id: str
    canonical_json: str

    def to_dict(self) -> dict:
        return json.loads(self.canonical_json)

    @classmethod
    def from_json(cls, text: str) -> "OpportunityContext":
        """Verify a persisted context: full contract, canonical form and ID."""
        try:
            body = json.loads(text)
        except (TypeError, ValueError) as exc:
            raise OpportunityContextRefused(CORRUPT_EVIDENCE, str(exc)) from None
        if not isinstance(body, dict) or set(body) != _TOP:
            _bad("top-level fields")
        claimed = body.pop("context_id")
        _verify(body)
        if claimed != _context_id(body):
            _bad("context_id")
        if canonical(dict(body, context_id=claimed)) != text:
            _bad("not canonical JSON")
        return cls(claimed, text)


def _context_id(body: dict) -> str:
    return f"{SCHEMA_VERSION}:{_sha(body)}"


def build(*, as_of_ms: int, symbol: str, attention_scan=None, allocation=None,
          registry_selection=None, instrument_id: str | None = None, signals=(),
          investigation=None, investigation_update=None,
          world_model=None) -> OpportunityContext:
    """Bind supplied persisted evidence for `symbol` at cut `as_of_ms`.

    Pure: the same inputs produce the same context_id. Omitted inputs are
    UNKNOWN with a reason; nothing is inferred to fill them."""
    if not _is_ms(as_of_ms):
        _refuse(INVALID_INPUT, "as_of_ms")
    if not isinstance(symbol, str) or not symbol:
        _refuse(INVALID_INPUT, "symbol")
    if isinstance(signals, (str, bytes, dict)) or signals is None:
        _refuse(INVALID_INPUT, "signals must be an iterable of signals")
    key = venue_key(symbol)
    selection = _selection(registry_selection, key, as_of_ms)
    occurrences = _occurrences(list(signals), key, as_of_ms)
    attention, scan, active = _attention(attention_scan, allocation, key, as_of_ms)
    body = {
        "schema_version": SCHEMA_VERSION,
        "as_of_ms": as_of_ms,
        "authority": AUTHORITY,
        "boundaries": list(BOUNDARIES),
        "instrument": _instrument(instrument_id, selection, key),
        "attention": attention,
        "registry_selection": selection,
        "signal_occurrences": {"status": AVAILABLE if occurrences else UNKNOWN,
                               "reason": None if occurrences else NO_SIGNAL_SUPPLIED,
                               "items": occurrences},
        "investigation": _investigation(investigation, investigation_update, key, as_of_ms,
                                        scan, active),
        "world_model": _world_model(world_model, as_of_ms),
    }
    _verify(body)       # the builder emits only what from_json accepts
    context_id = _context_id(body)
    return OpportunityContext(context_id, canonical(dict(body, context_id=context_id)))
