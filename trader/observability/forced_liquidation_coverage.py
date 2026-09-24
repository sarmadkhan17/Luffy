"""Pure coverage evaluation, window seal and replay for forced-liquidation-participation.v1.

Input is the exported append-only log (``forced_liquidation_store.load``) and
its metadata blobs. Every decision is recomputed from raw bytes: hash chain,
frame filter/mapping (compared against what the recorder stored), liveness,
clock ordering, receipt-time membership and the ordered-window hash. The same
function is the replay.

Coverage is local recorder transport/persistence coverage only. It never
claims Binance published every liquidation, counts, notional, intensity,
long/short position side, cause or "no liquidations occurred".

Authority: ``evaluate`` is the pure evaluator for deterministic/fixture replay
and accepts any ``CoveragePolicy``; its receipts carry ``authority`` NON_PRODUCTION.
Only ``seal`` writes a PRODUCTION receipt, and it takes no policy argument: it
uses the canonical frozen ``PRODUCTION_POLICY`` and
``PRODUCTION_CLOCK_HEALTH_POLICY``. Neither is frozen, so every production
seal is ``COVERAGE_POLICY_PREREQUISITE_REQUIRED`` (UNASSESSABLE); capture,
sealing of the prefix and replay still work.

Prerequisites that block PASS in every mode:

* transport policy: ``CoveragePolicy.documentation_sha256`` must name an
  archived ``transport_documentation`` blob whose bytes hash to it, archived
  before the window starts; otherwise ``TRANSPORT_DOCUMENTATION_UNVERIFIED``.
* absolute UTC: offset-drift checks prove only relative clock stability. PASS
  needs ``ClockHealthEvidence`` (archived receipt blob, declared absolute
  uncertainty covering the window). No LUFFY clock-health source or threshold
  exists, so production reports ``CLOCK_HEALTH_POLICY_PREREQUISITE_REQUIRED``.
* registration: the case binds investigation/protocol/baseline/registration
  identities; registration must precede ``window_start_ms``. The frozen
  registration receipt bytes must be archived as an
  ``investigation_registration_receipt`` blob (hash = the case's
  ``registration_receipt_sha256``, archived before the window starts) whose
  content binds the case identity; otherwise INVALID.

Seal-time authority state: every receipt archives ``authority_state`` - the
authority plus the canonical production policy and clock-health policy in
force (explicit ``NOT_FROZEN``/None) with their digests. Replay evaluates
against that archived state, never the current module globals, so a later
freeze or version change cannot alter an existing seal's replay.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, is_dataclass

from ..core.instrument_registry import InstrumentId
from ..core.types import MarketType
from . import forced_liquidation as F
from . import forced_liquidation_store as S

EVALUATOR_VERSION = "forced-liquidation-coverage.v1"
SEAL_SCHEMA = "forced-liquidation-window-seal.v1"
AUTHORITY_SCHEMA = "forced-liquidation-authority-state.v1"

PASS = "PASS"
GAP = "GAP"
UNKNOWN = "UNKNOWN"
INVALID = "INVALID"
POLICY_REQUIRED = "COVERAGE_POLICY_PREREQUISITE_REQUIRED"
CLOCK_REQUIRED = "CLOCK_HEALTH_POLICY_PREREQUISITE_REQUIRED"

PRODUCTION = "PRODUCTION"
NON_PRODUCTION = "NON_PRODUCTION"
DOCUMENTATION_KIND = "transport_documentation"
CLOCK_RECEIPT_KIND = "clock_health_receipt"
REGISTRATION_RECEIPT_KIND = "investigation_registration_receipt"
#: Case identity a registration receipt must bind (exact value and JSON type).
REGISTRATION_FIELDS = ("case_id", "investigation_id", "protocol_id", "protocol_hash",
                       "instrument_id", "window_id", "window_start_ms", "window_end_ms",
                       "registration_utc_ms", "baseline_hash")

FROZEN = "FROZEN"
NOT_FROZEN = "NOT_FROZEN"
NOT_APPLICABLE = "NOT_APPLICABLE"
_AUTHORITY_SLOTS = ("production_policy", "production_clock_health_policy")

RESULTS = {(): "NO_PUBLISHED_SNAPSHOT_OBSERVED", ("BUY",): "BUY_FORCE_ORDER_OBSERVED",
           ("SELL",): "SELL_FORCE_ORDER_OBSERVED", ("BUY", "SELL"): "BOTH_ORDER_SIDES_OBSERVED"}
UNASSESSABLE = "UNASSESSABLE"

_CONTROL = (S.SESSION_OPEN, S.HANDSHAKE, S.HANDSHAKE_FAILURE, S.SESSION_CLOSE,
            S.WRITE_FAILURE, S.READER_ERROR, S.DROP)
_FAILURES = (S.WRITE_FAILURE, S.READER_ERROR, S.DROP)
_FAILURE_REASON = {S.WRITE_FAILURE: "WRITE_FAILURE", S.READER_ERROR: "READER_ERROR",
                   S.DROP: "UNRESOLVED_DROP"}


@dataclass(frozen=True)
class CoveragePolicy:
    """A prospectively frozen transport/coverage policy. All fields required."""

    policy_id: str
    documentation_sha256: str
    ping_interval_s: float
    ping_timeout_s: float
    max_liveness_gap_ms: int
    max_clock_offset_drift_ms: int

    def __post_init__(self):
        if not self.policy_id or not _is_sha256(self.documentation_sha256):
            raise ValueError("policy identity and documentation hash required")
        if not (self.max_liveness_gap_ms > 0 and self.max_clock_offset_drift_ms >= 0):
            raise ValueError("invalid policy bounds")


@dataclass(frozen=True)
class ClockHealthEvidence:
    """Archived receipt that local UTC was within ±max_abs_uncertainty_ms of
    true UTC throughout [valid_from_utc_ms, valid_to_utc_ms]. The receipt
    bytes must be archived as a ``clock_health_receipt`` blob."""

    receipt_id: str
    source: str
    receipt_sha256: str
    max_abs_uncertainty_ms: int
    valid_from_utc_ms: int
    valid_to_utc_ms: int
    test_only: bool

    def __post_init__(self):
        if not self.receipt_id or not self.source or not _is_sha256(self.receipt_sha256):
            raise ValueError("clock-health receipt identity required")
        if not (type(self.max_abs_uncertainty_ms) is int and self.max_abs_uncertainty_ms >= 0
                and type(self.valid_from_utc_ms) is int and type(self.valid_to_utc_ms) is int
                and self.valid_from_utc_ms < self.valid_to_utc_ms
                and type(self.test_only) is bool):
            raise ValueError("invalid clock-health bounds")


#: No frozen production timing policy exists; see module docstring.
PRODUCTION_POLICY: CoveragePolicy | None = None
#: No LUFFY clock-health source/threshold exists; see module docstring.
PRODUCTION_CLOCK_HEALTH_POLICY = None


def _is_sha256(value) -> bool:
    return isinstance(value, str) and len(value) == 64 and set(value) <= set("0123456789abcdef")


def _slot(value) -> dict:
    if value is None:
        return {"status": NOT_FROZEN, "value": None, "sha256": None}
    value = asdict(value) if is_dataclass(value) else value
    return {"status": FROZEN, "value": value, "sha256": F.digest(value)}


def production_authority_state() -> dict:
    """Snapshot of the canonical production prerequisites in force now; only
    ``seal`` reads the globals, and the snapshot is archived in the receipt."""
    return validate_authority_state({
        "schema": AUTHORITY_SCHEMA, "authority": PRODUCTION,
        "production_policy": _slot(PRODUCTION_POLICY),
        "production_clock_health_policy": _slot(PRODUCTION_CLOCK_HEALTH_POLICY)})


NON_PRODUCTION_STATE = {"schema": AUTHORITY_SCHEMA, "authority": NON_PRODUCTION,
                        **{k: {"status": NOT_APPLICABLE, "value": None, "sha256": None}
                           for k in _AUTHORITY_SLOTS}}


def validate_authority_state(state) -> dict:
    """Archived authority state must be exactly well formed and self-consistent."""
    if not (isinstance(state, dict) and set(state) == {"schema", "authority", *_AUTHORITY_SLOTS}
            and state["schema"] == AUTHORITY_SCHEMA):
        raise ValueError("malformed authority state")
    authority = state["authority"]
    if authority not in (PRODUCTION, NON_PRODUCTION):
        raise ValueError("unknown seal authority")
    for key in _AUTHORITY_SLOTS:
        slot = state[key]
        if not (isinstance(slot, dict) and set(slot) == {"status", "value", "sha256"}):
            raise ValueError(f"malformed {key}")
        status, value, sha = slot["status"], slot["value"], slot["sha256"]
        if authority == NON_PRODUCTION:
            ok = status == NOT_APPLICABLE and value is None and sha is None
        elif status == NOT_FROZEN:
            ok = value is None and sha is None
        else:
            ok = status == FROZEN and value is not None and sha == F.digest(value)
        if not ok:
            raise ValueError(f"inconsistent {key}")
    policy = state["production_policy"]["value"]
    if policy is not None:
        if not isinstance(policy, dict):
            raise ValueError("malformed production_policy")
        CoveragePolicy(**policy)  # raises on a malformed archived policy
    return state


@dataclass(frozen=True)
class WindowCase:
    """Frozen prospective case identity; every field enters seals and replay."""

    case_id: str
    protocol_id: str
    protocol_hash: str
    investigation_id: str
    baseline_hash: str
    registration_receipt_sha256: str
    registration_utc_ms: int
    window_id: str
    instrument_id: InstrumentId
    window_start_ms: int
    window_end_ms: int  # close of the final registered target bar; exclusive

    def __post_init__(self):
        if self.protocol_id != F.PROTOCOL_ID:
            raise ValueError("unsupported protocol")
        if not self.case_id or not self.window_id or not self.investigation_id:
            raise ValueError("case, investigation and window identity required")
        if not all(_is_sha256(h) for h in (self.protocol_hash, self.baseline_hash,
                                           self.registration_receipt_sha256)):
            raise ValueError("protocol, baseline and registration receipt hashes required")
        if not (type(self.registration_utc_ms) is int and self.registration_utc_ms > 0):
            raise ValueError("registration UTC time required")
        iid = self.instrument_id
        if (not isinstance(iid, InstrumentId) or iid.venue != F.VENUE
                or iid.market_type is not MarketType.FUTURES):
            raise ValueError("binance_usdm futures InstrumentId required")
        if not (type(self.window_start_ms) is int and type(self.window_end_ms) is int
                and 0 < self.window_start_ms < self.window_end_ms):
            raise ValueError("half-open window [start, end) with integer ms required")

    @property
    def window_key(self) -> str:
        return f"{self.protocol_id}|{self.case_id}|{self.window_id}"

    def as_dict(self) -> dict:
        return {"case_id": self.case_id, "protocol_id": self.protocol_id,
                "protocol_hash": self.protocol_hash, "investigation_id": self.investigation_id,
                "baseline_hash": self.baseline_hash,
                "registration_receipt_sha256": self.registration_receipt_sha256,
                "registration_utc_ms": self.registration_utc_ms,
                "window_id": self.window_id, "instrument_id": self.instrument_id.value,
                "window_start_ms": self.window_start_ms, "window_end_ms": self.window_end_ms}

    @classmethod
    def from_dict(cls, d: dict) -> "WindowCase":
        venue, market, venue_symbol = d["instrument_id"].split(":")
        fields = {k: d[k] for k in cls.__dataclass_fields__ if k != "instrument_id"}
        if set(d) != set(cls.__dataclass_fields__):
            raise ValueError("case identity fields differ from the frozen contract")
        return cls(instrument_id=InstrumentId(venue, MarketType(market), venue_symbol), **fields)


class _Reasons:
    def __init__(self):
        self.items: dict[tuple, dict] = {}

    def add(self, code, klass, **detail):
        key = (code, F.encode(detail))
        self.items.setdefault(key, {"code": code, "class": klass, **detail})

    def sorted(self):
        return [self.items[k] for k in sorted(self.items)]

    def classes(self):
        return {r["class"] for r in self.items.values()}


def _verify_chain(records, reasons) -> bool:
    prev = S.GENESIS
    for expected, r in enumerate(records, 1):
        ok = (r["seq"] == expected and r["prev_hash"] == prev and r["kind"] in S.KINDS
              and r["record_hash"] == S.record_hash(r["seq"], r["kind"], r["session_id"],
                                                    r["utc_ms"], r["mono_ns"], r["raw_sha256"],
                                                    r["body"], r["prev_hash"])
              and ((r["raw"] is None and r["raw_sha256"] is None)
                   or (r["raw"] is not None and F.sha256(bytes(r["raw"])) == r["raw_sha256"]))
              and (r["kind"] != S.FRAME or r["raw"] is not None))
        if not ok:
            reasons.add("INTEGRITY_FAILURE", INVALID, seq=r["seq"])
            return False
        prev = r["record_hash"]
    return True


def _symbol_map(blobs, key):
    blob = blobs.get(key) if key else None
    if blob is None:
        return None
    raw = bytes(blob["raw"])
    env = json.loads(blob["envelope"])
    if F.sha256(raw) != key:
        return None
    try:
        return F.SymbolMap.from_exchange_info(raw, source_url=env.get("source_url"),
                                              environment=env.get("environment"),
                                              received_utc_ms=env.get("received_utc_ms"))
    except (ValueError, TypeError):
        return None


class _Session:
    def __init__(self, sid):
        self.sid, self.records = sid, []

    def of(self, *kinds):
        return [r for r in self.records if r["kind"] in kinds]

    @property
    def first_utc(self):
        return self.records[0]["utc_ms"]

    @property
    def last_utc(self):
        return self.records[-1]["utc_ms"]


def _live_points(session, first_failure_seq):
    """Pongs whose payload matches an earlier ping of the same session."""
    pending, points = {}, []
    for r in session.records:
        if first_failure_seq is not None and r["seq"] >= first_failure_seq:
            break
        body = r["body_obj"]
        if r["kind"] == S.PING_SENT:
            pending[body.get("payload_hex")] = r
        elif r["kind"] == S.PONG and body.get("solicited") is True:
            ping = pending.pop(body.get("payload_hex"), None)
            if ping is not None and ping["utc_ms"] <= r["utc_ms"]:
                points.append((r, ping))
    return points


def _archived_blob(blobs, key, kind, before_ms) -> bool:
    """The blob exists, its bytes hash to ``key``, it has the declared kind and
    was archived before ``before_ms``."""
    blob = blobs.get(key)
    if blob is None or F.sha256(bytes(blob["raw"])) != key:
        return False
    try:
        env = json.loads(blob["envelope"])
    except (TypeError, ValueError):
        return False
    archived = env.get("archived_utc_ms")
    return (env.get("kind") == kind and type(archived) is int and archived < before_ms)


def _check_registration(blobs, case, reasons) -> dict:
    """Verify the supplied frozen registration receipt artifact; never creates one."""
    start, key = case.window_start_ms, case.registration_receipt_sha256
    ref = {"sha256": key, "kind": None, "archived_utc_ms": None, "verified": False}
    blob = blobs.get(key)
    if blob is None:
        reasons.add("REGISTRATION_RECEIPT_MISSING", INVALID)
        return ref
    raw = bytes(blob["raw"])
    if F.sha256(raw) != key:
        reasons.add("REGISTRATION_RECEIPT_HASH_MISMATCH", INVALID)
        return ref
    try:
        env = json.loads(blob["envelope"])
        if not isinstance(env, dict):
            raise ValueError
    except (TypeError, ValueError):
        reasons.add("REGISTRATION_RECEIPT_ENVELOPE_MALFORMED", INVALID)
        return ref
    kind, archived = env.get("kind"), env.get("archived_utc_ms")
    ref["kind"] = kind if isinstance(kind, str) else None
    ref["archived_utc_ms"] = archived if type(archived) is int else None
    ok = True
    if kind != REGISTRATION_RECEIPT_KIND:
        reasons.add("REGISTRATION_RECEIPT_KIND_MISMATCH", INVALID)
        ok = False
    if not (type(archived) is int and archived < start):
        reasons.add("REGISTRATION_RECEIPT_ARCHIVED_LATE", INVALID)
        ok = False
    try:
        content = F.strict_json(raw)
        if not isinstance(content, dict):
            raise ValueError
    except (UnicodeDecodeError, ValueError):
        reasons.add("REGISTRATION_RECEIPT_MALFORMED", INVALID)
        return ref
    expected = case.as_dict()
    for field in REGISTRATION_FIELDS:
        value = content.get(field)
        if (field not in content or type(value) is not type(expected[field])
                or value != expected[field]):
            reasons.add("REGISTRATION_RECEIPT_MISMATCH", INVALID, field=field)
            ok = False
    registered = content.get("registration_utc_ms")
    if not (type(registered) is int and registered < start):
        reasons.add("REGISTRATION_RECEIPT_NOT_PROSPECTIVE", INVALID)
        ok = False
    ref["verified"] = ok
    return ref


def _check_prerequisites(blobs, case, policy, clock, state, reasons):
    start, end = case.window_start_ms, case.window_end_ms
    if case.registration_utc_ms >= start:
        reasons.add("REGISTRATION_NOT_PROSPECTIVE", INVALID,
                    registration_utc_ms=case.registration_utc_ms)
    if policy is None:
        reasons.add("COVERAGE_POLICY_NOT_FROZEN", POLICY_REQUIRED)
    elif not _archived_blob(blobs, policy.documentation_sha256, DOCUMENTATION_KIND, start):
        reasons.add("TRANSPORT_DOCUMENTATION_UNVERIFIED", POLICY_REQUIRED)
    if state["authority"] == PRODUCTION:
        # Canonical values come from the archived seal-time state, not globals.
        applied = asdict(policy) if policy is not None else None
        if applied != state["production_policy"]["value"]:
            reasons.add("POLICY_NOT_CANONICAL", INVALID)
        if state["production_clock_health_policy"]["status"] != FROZEN:
            reasons.add("CLOCK_HEALTH_POLICY_NOT_FROZEN", CLOCK_REQUIRED)
        if clock is not None and clock.test_only:
            reasons.add("TEST_ONLY_CLOCK_HEALTH", INVALID)
    if clock is None:
        reasons.add("ABSOLUTE_UTC_UNVERIFIED", CLOCK_REQUIRED)
        return 0
    u = clock.max_abs_uncertainty_ms
    if not _archived_blob(blobs, clock.receipt_sha256, CLOCK_RECEIPT_KIND, end + u + 1):
        reasons.add("CLOCK_HEALTH_RECEIPT_UNVERIFIED", CLOCK_REQUIRED)
    if not (clock.valid_from_utc_ms <= start - u and clock.valid_to_utc_ms >= end + u):
        reasons.add("CLOCK_HEALTH_NOT_COVERING_WINDOW", CLOCK_REQUIRED)
    return u


def evaluate(records, blobs, case: WindowCase, policy: CoveragePolicy | None,
             clock: ClockHealthEvidence | None = None) -> dict:
    """Pure deterministic receipt for one registered window, for replay and
    fixtures. Any caller policy is accepted; authority is NON_PRODUCTION."""
    return _evaluate(records, blobs, case, policy, clock, NON_PRODUCTION_STATE)


def _evaluate(records, blobs, case, policy, clock, state) -> dict:
    reasons = _Reasons()
    registration = _check_registration(blobs, case, reasons)
    start, end = case.window_start_ms, case.window_end_ms
    symbol = case.instrument_id.venue_symbol
    records = [dict(r) for r in records]
    prefix = ({"last_seq": records[-1]["seq"], "head_record_hash": records[-1]["record_hash"]}
              if records else None)
    ctx = (case, policy, clock, state, registration)
    if not _verify_chain(records, reasons):
        return _receipt(ctx, reasons, [], [], [], [], [], [], None, prefix)
    for r in records:
        r["body_obj"] = json.loads(r["body"])

    # Absolute UTC uncertainty widens both brackets: a receipt time within u of
    # a boundary cannot be assigned to the window, and coverage must span it.
    u = _check_prerequisites(blobs, case, policy, clock, state, reasons)
    lo_cut, hi_cut = start - u, end + u

    sessions: dict[str, _Session] = {}
    for r in records:
        sessions.setdefault(r["session_id"], _Session(r["session_id"])).records.append(r)

    # Sessions touching the window, or bracketing it with a post-cut live point.
    relevant = [s for s in sessions.values() if s.first_utc < hi_cut and s.last_utc >= lo_cut]

    # Global receipt-time ordering must follow durable sequence.
    for a, b in zip(records, records[1:]):
        if b["utc_ms"] < a["utc_ms"] and b["utc_ms"] < hi_cut and a["utc_ms"] >= lo_cut:
            reasons.add("CLOCK_REGRESSION", UNKNOWN, seq=b["seq"])

    segments, session_refs, maps = [], [], {}
    for s in relevant:
        opened = s.of(S.SESSION_OPEN)
        body = opened[0]["body_obj"] if opened else {}
        ref = {"session_id": s.sid, "first_seq": s.records[0]["seq"], "last_seq": s.records[-1]["seq"],
               "url": body.get("url"), "environment": body.get("environment"),
               "mapping_sha256": (body.get("mapping") or {}).get("blob"),
               "closed": bool(s.of(S.SESSION_CLOSE)), "attempt": body.get("attempt")}
        session_refs.append(ref)
        if s.of(S.HANDSHAKE_FAILURE):
            reasons.add("RECONNECT_ATTEMPT_FAILED", GAP, session_id=s.sid)
        if not opened:
            if s.of(S.FRAME, S.PONG):
                reasons.add("SESSION_WITHOUT_OPEN", INVALID, session_id=s.sid)
            continue
        valid = True
        if body.get("url") != F.STREAM_URL:
            reasons.add("SESSION_URL_MISMATCH", INVALID, session_id=s.sid)
            valid = False
        if body.get("environment") != F.ENVIRONMENT:
            reasons.add("SESSION_ENVIRONMENT_MISMATCH", INVALID, session_id=s.sid)
            valid = False
        handshake = s.of(S.HANDSHAKE)
        if not handshake or handshake[0]["body_obj"].get("status") != "OK":
            reasons.add("HANDSHAKE_NOT_RECORDED", UNKNOWN, session_id=s.sid)
            valid = False
        symbols = _symbol_map(blobs, ref["mapping_sha256"])
        if symbols is None:
            reasons.add("MAPPING_METADATA_UNVERIFIED", INVALID, session_id=s.sid)
            valid = False
        else:
            maps[s.sid] = symbols
            status = symbols.resolve(symbol)["status"]
            if status == F.AMBIGUOUS:
                reasons.add("IDENTITY_AMBIGUITY", INVALID, session_id=s.sid, symbol=symbol)
                valid = False
            elif status != F.EXACT:
                reasons.add("CASE_INSTRUMENT_UNMAPPED", INVALID, session_id=s.sid, symbol=symbol)
                valid = False
        transport = body.get("transport") or {}
        if policy is not None and (transport.get("ping_interval_s") != policy.ping_interval_s
                                   or transport.get("ping_timeout_s") != policy.ping_timeout_s):
            reasons.add("TRANSPORT_POLICY_MISMATCH", INVALID, session_id=s.sid)
            valid = False
        # Clock mapping within the session.
        monos = [r["mono_ns"] for r in s.records]
        if any(b < a for a, b in zip(monos, monos[1:])):
            reasons.add("MONOTONIC_REGRESSION", UNKNOWN, session_id=s.sid)
            valid = False
        offsets = [r["utc_ms"] - r["mono_ns"] // 1_000_000 for r in s.records]
        if policy is not None and max(offsets) - min(offsets) > policy.max_clock_offset_drift_ms:
            reasons.add("CLOCK_OFFSET_DRIFT", UNKNOWN, session_id=s.sid)
            valid = False
        failures = s.of(*_FAILURES)
        for r in failures:
            if r["utc_ms"] < hi_cut:
                reasons.add(_FAILURE_REASON[r["kind"]], UNKNOWN, session_id=s.sid, seq=r["seq"])
        if not s.of(S.SESSION_CLOSE) and s.last_utc < hi_cut:
            reasons.add("SESSION_UNSEALED", UNKNOWN, session_id=s.sid)
        if not valid:
            continue
        points = _live_points(s, failures[0]["seq"] if failures else None)
        ref["live_points"] = len(points)
        if not points:
            reasons.add("MISSING_LIVENESS", UNKNOWN, session_id=s.sid)
            continue
        if policy is None:
            continue
        lo = prev = points[0][0]["utc_ms"]
        for pong, _ in points[1:]:
            t = pong["utc_ms"]
            if t - prev > policy.max_liveness_gap_ms:
                if prev >= lo_cut and t > lo_cut and prev < hi_cut:
                    reasons.add("LIVENESS_GAP", GAP, session_id=s.sid, after_utc_ms=prev)
                segments.append((lo, prev))
                lo = t
            prev = t
        segments.append((lo, prev))

    covered = []
    for lo, hi in sorted(segments):
        if covered and lo <= covered[-1][1]:
            covered[-1] = (covered[-1][0], max(covered[-1][1], hi))
        else:
            covered.append((lo, hi))
    if policy is not None:
        if not any(lo <= lo_cut and hi >= hi_cut for lo, hi in covered):
            reasons.add("WINDOW_NOT_COVERED", GAP)
            if not any(lo <= lo_cut <= hi for lo, hi in covered):
                reasons.add("START_NOT_BRACKETED", GAP)
            if not any(lo <= hi_cut <= hi for lo, hi in covered):
                reasons.add("END_NOT_BRACKETED", GAP)
        if not relevant:
            reasons.add("NO_SESSION", GAP)

    # Frames by durable receipt time only; exchange E/T never decide membership.
    # Within u of a boundary a case-relevant item cannot be placed in or out.
    admissible, decisions, observed = [], [], set()
    valid_sids = set(maps)
    for r in records:
        t = r["utc_ms"]
        if r["kind"] != S.FRAME or not (lo_cut <= t < hi_cut):
            continue
        inside, ambiguous = start <= t < end, not (start + u <= t < end - u)
        symbols = maps.get(r["session_id"])
        if symbols is None:
            reasons.add("FRAME_FROM_UNVERIFIED_SESSION", INVALID, seq=r["seq"])
            continue
        body = F.classify_frame(bytes(r["raw"]), symbols)
        if body != r["body_obj"]:
            reasons.add("STORED_DECISION_MISMATCH", INVALID, seq=r["seq"])
        for item in body["items"]:
            relevance = _relevance(item, symbol)
            if ambiguous and (relevance == "CASE_ADMISSIBLE" or relevance.startswith("CASE_BLOCKING:")):
                reasons.add("RECEIPT_TIME_MEMBERSHIP_AMBIGUOUS", UNKNOWN, seq=r["seq"],
                            index=item["index"])
            if not inside:
                continue
            decisions.append({"seq": r["seq"], "index": item["index"],
                              "disposition": item["disposition"], "relevance": relevance})
            if relevance == "CASE_ADMISSIBLE":
                observed.add(item["S"])
                admissible.append({"seq": r["seq"], "index": item["index"],
                                   "raw_sha256": r["raw_sha256"], "side": item["S"]})
            elif relevance.startswith("CASE_BLOCKING:"):
                code = relevance.split(":", 1)[1]
                klass = INVALID if code in ("IDENTITY_AMBIGUITY", "CASE_INSTRUMENT_UNMAPPED") else UNKNOWN
                reasons.add(code, klass, seq=r["seq"], index=item["index"])
        if r["session_id"] not in valid_sids:
            reasons.add("FRAME_FROM_UNVERIFIED_SESSION", INVALID, seq=r["seq"])

    included = _included(records, relevant, lo_cut, hi_cut)
    return _receipt(ctx, reasons, covered, session_refs, included, decisions,
                    admissible, sorted(observed), maps, prefix)


def _relevance(item, symbol) -> str:
    s, disposition = item.get("s"), item["disposition"]
    if not isinstance(s, str) or not s:
        return ("CASE_BLOCKING:UNATTRIBUTABLE_FRAME")
    if s != symbol:
        if s.strip().upper() == symbol:
            return "CASE_BLOCKING:SYMBOL_VARIANT_AMBIGUOUS"
        return "EXCLUDED_OTHER_INSTRUMENT"
    return {F.ACCEPTED: "CASE_ADMISSIBLE", F.EXCLUDED_ST2: "EXCLUDED_ST2",
            F.IDENTITY_AMBIGUOUS: "CASE_BLOCKING:IDENTITY_AMBIGUITY",
            F.UNMAPPED: "CASE_BLOCKING:CASE_INSTRUMENT_UNMAPPED",
            F.UNCLASSIFIABLE_ST: "CASE_BLOCKING:ST_UNCLASSIFIABLE",
            }.get(disposition, "CASE_BLOCKING:CASE_FRAME_INVALID")


def _included(records, relevant, start, end):
    """Records replay needs: session control records, window-interval liveness
    and frames, the matching pings, and each session's first post-cut pong."""
    rel = {s.sid for s in relevant}
    keep = set()
    for s in relevant:
        pending = {}
        post_cut = False
        for r in s.records:
            body = r["body_obj"]
            if r["kind"] == S.PING_SENT:
                pending[body.get("payload_hex")] = r["seq"]
            if r["kind"] in _CONTROL:
                keep.add(r["seq"])
            elif start <= r["utc_ms"] < end:
                keep.add(r["seq"])
            elif r["kind"] == S.PONG and r["utc_ms"] >= end and not post_cut:
                keep.add(r["seq"])
                post_cut = True
            if r["kind"] == S.PONG and r["seq"] in keep and body.get("payload_hex") in pending:
                keep.add(pending[body["payload_hex"]])
        before = [r for r in s.records if r["kind"] == S.PONG and r["utc_ms"] < start]
        if before:
            keep.add(before[-1]["seq"])
            match = [p for p in s.records if p["kind"] == S.PING_SENT and p["seq"] < before[-1]["seq"]
                     and p["body_obj"].get("payload_hex") == before[-1]["body_obj"].get("payload_hex")]
            if match:
                keep.add(match[-1]["seq"])
    return [{"seq": r["seq"], "kind": r["kind"], "session_id": r["session_id"],
             "utc_ms": r["utc_ms"], "record_hash": r["record_hash"], "raw_sha256": r["raw_sha256"]}
            for r in records if r["seq"] in keep and r["session_id"] in rel]


def _receipt(ctx, reasons, covered, sessions, included, decisions, admissible,
             observed, maps, prefix) -> dict:
    case, policy, clock, state, registration = ctx
    classes = reasons.classes()
    status = next((c for c in (INVALID, POLICY_REQUIRED, CLOCK_REQUIRED, UNKNOWN, GAP)
                   if c in classes), PASS)
    provenance = {"authority": state["authority"], "authority_state": state,
                  "registration_receipt": registration,
                  "case_sha256": F.digest(case.as_dict()),
                  "policy": asdict(policy) if policy is not None else None,
                  "clock_health": asdict(clock) if clock is not None else None}
    manifest = {"case": case.as_dict(), "instrument_id": case.instrument_id.value,
                **provenance,
                "sessions": [s["session_id"] for s in sessions], "records": included,
                "filter_decisions": decisions,
                "mapping_sha256": sorted({m.raw_sha256 for m in (maps or {}).values()})}
    ordered_hash = F.digest(manifest) if maps is not None else None
    receipt = {
        "schema": SEAL_SCHEMA, "evaluator_version": EVALUATOR_VERSION,
        "parser_version": F.PARSER_VERSION, "protocol_id": F.PROTOCOL_ID,
        "case": case.as_dict(), "window_key": case.window_key, **provenance,
        "coverage": {"status": status, "reasons": reasons.sorted(),
                     "covered_intervals_utc_ms": [list(x) for x in covered]},
        "sessions": sessions, "included_records": included, "filter_decisions": decisions,
        "admissible_snapshots": admissible,
        # Side presence only: no counts, totals, position side or cause.
        "observed_order_sides": observed if status == PASS else None,
        "diagnostic_order_sides": observed,
        "empty_window_manifest": ({"admissible_snapshots": [], "coverage": PASS,
                                   "claim": "no admissible snapshot received; not no liquidations"}
                                  if status == PASS and not observed else None),
        "result": RESULTS[tuple(observed)] if status == PASS else UNASSESSABLE,
        "last_included_seq": included[-1]["seq"] if included else None,
        "log_prefix": prefix,
        "ordered_window_hash": ordered_hash,
    }
    return receipt


def _write_seal(store, window_key, case, policy, clock, state, sealed_utc_ms) -> dict:
    records, blobs = S.load(store.path)
    if not records or records[-1]["utc_ms"] < case.window_end_ms:
        raise ValueError("window pending: durable log has not reached window_end_ms")
    receipt = _evaluate(records, blobs, case, policy, clock, state)
    if (state["authority"] == PRODUCTION and receipt["coverage"]["status"] == PASS
            and any(state[k]["status"] != FROZEN for k in _AUTHORITY_SLOTS)):
        raise RuntimeError("production PASS without frozen prerequisites")  # defensive
    text = F.encode(receipt)
    seal_hash = F.sha256(text.encode())
    store.put_seal(window_key, sealed_utc_ms, seal_hash, text)
    return {**receipt, "seal_hash": seal_hash}


def seal(store: S.LogStore, case: WindowCase, sealed_utc_ms: int) -> dict:
    """Production seal: evaluate the durable log prefix under the canonical
    frozen ``PRODUCTION_POLICY`` and append an immutable PRODUCTION receipt.

    There is no policy or clock argument: a caller cannot supply authority.
    While ``PRODUCTION_POLICY`` or ``PRODUCTION_CLOCK_HEALTH_POLICY`` is None
    the sealed status is a prerequisite code and the result UNASSESSABLE. The
    globals in force are archived as ``authority_state``; replay uses that.
    The prefix is the whole log at seal time and must already reach past the
    cut; later records never enter it. Before that the window is PENDING.
    """
    # No production clock-health source exists to supply evidence; see module docstring.
    state = production_authority_state()
    return _write_seal(store, case.window_key, case, PRODUCTION_POLICY, None, state,
                       sealed_utc_ms)


def seal_fixture(store: S.LogStore, case: WindowCase, policy: CoveragePolicy,
                 clock: ClockHealthEvidence | None, sealed_utc_ms: int) -> dict:
    """Non-production seal for deterministic tests: NON_PRODUCTION authority,
    stored under a ``fixture|`` key so it never occupies a production window."""
    return _write_seal(store, "fixture|" + case.window_key, case, policy, clock,
                       NON_PRODUCTION_STATE, sealed_utc_ms)


def replay(records, blobs, seal_text: str) -> dict:
    """Recompute a sealed receipt from the archived log; report every mismatch.

    Registration, policy, clock-health and authority provenance come from the
    seal and are re-checked. A seal replays against its archived seal-time
    ``authority_state`` (never the current module globals), so later policy
    freezes cannot change the result, while a forged authority, altered
    archived state or identity cannot match.
    """
    sealed = json.loads(seal_text)
    try:
        case = WindowCase.from_dict(sealed["case"])
        policy = CoveragePolicy(**sealed["policy"]) if sealed["policy"] else None
        clock = ClockHealthEvidence(**sealed["clock_health"]) if sealed["clock_health"] else None
        state = validate_authority_state(sealed["authority_state"])
        if sealed["authority"] != state["authority"]:
            raise ValueError("authority differs from archived authority state")
    except (KeyError, TypeError, ValueError) as exc:
        return {"match": False, "differences": ["provenance"], "error": type(exc).__name__,
                "receipt": None}
    prefix = sealed.get("log_prefix") or {"last_seq": 0}
    again = _evaluate(list(records)[:prefix["last_seq"]], blobs, case, policy, clock, state)
    diffs = sorted(k for k in set(sealed) | set(again) if sealed.get(k) != again.get(k))
    return {"match": not diffs and F.encode(again) == seal_text, "differences": diffs,
            "receipt": again}
