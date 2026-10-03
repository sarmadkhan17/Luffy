"""Transport-neutral Owner Interface contract: typed request, typed result.

Nothing here knows about Telegram updates, WhatsApp payloads, OpenClaw events
or HTTP objects. The wire form is a small, strictly validated JSON object:
unknown keys, wrong types, missing times and oversized fields are refused,
never coerced or defaulted.

A request carries the channel's *authenticated identity* (Telegram sender id,
dashboard session, provider sender). It never carries a principal: the kernel
resolves identity → principal itself, so a client cannot assert one.
"""
from __future__ import annotations

import hashlib
import math
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

WIRE_VERSION = 2

READ_OPERATIONS = ("status", "health")
#: containment: must never wait behind recovery
CONTAINMENT_OPERATIONS = ("freeze", "halt", "panic")
#: guarded release: the Supervisor's recovery path only
RECOVERY_OPERATIONS = ("resume", "unhalt")
#: owner intents the kernel executes on its next cycle
INTENT_OPERATIONS = ("close_trade", "set_market_type")
GOVERNANCE_OPERATIONS = ("approval_decision",)
CONTROL_OPERATIONS = CONTAINMENT_OPERATIONS + RECOVERY_OPERATIONS + INTENT_OPERATIONS + GOVERNANCE_OPERATIONS
OPERATIONS = READ_OPERATIONS + CONTROL_OPERATIONS
CHANNELS = ("telegram", "dashboard", "openclaw", "whatsapp", "cli", "test")


class Status:
    ACCEPTED = "ACCEPTED"          # read answered / containment or intent applied
    ACTIVATED = "ACTIVATED"        # guarded recovery proved safe → ACTIVE
    CONTAINED = "CONTAINED"        # guarded recovery ran, stays contained
    ALREADY_SET = "ALREADY_SET"    # the requested state already held
    REFUSED = "REFUSED"            # definitively not executed, and never will be
    IN_PROGRESS = "IN_PROGRESS"    # this request id is reserved/running; ask again
    OUTCOME_UNKNOWN = "OUTCOME_UNKNOWN"  # may or may not have executed; never re-run
    UNAVAILABLE = "UNAVAILABLE"    # never delivered to the kernel
    # revision-4 names kept as aliases of the same values
    PENDING = IN_PROGRESS
    ERROR = OUTCOME_UNKNOWN


class Disposition:
    """The retry contract, derived from the status (wording is not the contract).

    COMPLETED        the request reached a recorded final outcome — a retry of the
                     same id replays it
    REFUSED          definitively not executed; no earlier delivery can execute it
    IN_PROGRESS      reserved or running now; retry the same id later
    OUTCOME_UNKNOWN  execution status cannot be established; it may have run or
                     may still execute. Keep the same id to resolve its outcome.
    UNAVAILABLE      never reached the kernel; retrying the same id is safe
    """
    COMPLETED = "COMPLETED"
    REFUSED = "REFUSED"
    IN_PROGRESS = "IN_PROGRESS"
    OUTCOME_UNKNOWN = "OUTCOME_UNKNOWN"
    UNAVAILABLE = "UNAVAILABLE"


def disposition_of(status: str) -> str:
    return {Status.REFUSED: Disposition.REFUSED, Status.IN_PROGRESS: Disposition.IN_PROGRESS,
            Status.OUTCOME_UNKNOWN: Disposition.OUTCOME_UNKNOWN,
            Status.UNAVAILABLE: Disposition.UNAVAILABLE}.get(status, Disposition.COMPLETED)


_ID = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")
_TOKEN = re.compile(r"^[A-Za-z0-9._:@+-]{1,128}$")
_META_VALUE = re.compile(r"^[A-Za-z0-9/_.:@+-]{1,64}$")
#: the only metadata a request may carry into audit storage
META_KEYS = ("command", "confirm_ref")
_REQUEST_KEYS = {"request_id", "operation", "channel", "identity", "request_ref",
                 "issued_at", "args", "meta"}
_REQUIRED = {"request_id", "operation", "channel", "identity", "issued_at"}
_TRADE_ID = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")


class MalformedRequest(ValueError):
    """A payload that is not a valid OwnerRequest. Never executed."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def derive_request_id(channel: str, identity: str, external_ref: str) -> str:
    """Stable request id for one external message: redelivery → same id."""
    digest = hashlib.sha256(f"{channel}\x1f{identity}\x1f{external_ref}".encode())
    return f"{channel}-{digest.hexdigest()[:40]}"


def new_request_id(channel: str) -> str:
    return f"{channel}-{uuid.uuid4().hex}"


def _check_meta(meta) -> dict:
    if not isinstance(meta, dict):
        raise MalformedRequest("meta must be an object")
    out = {}
    for k, v in meta.items():
        if k not in META_KEYS:
            raise MalformedRequest(f"meta key refused: {k!r}")
        if not isinstance(v, str) or not _META_VALUE.match(v):
            raise MalformedRequest(f"meta value refused for {k!r}")
        out[k] = v
    return out


def _check_args(operation: str, args) -> dict:
    if not isinstance(args, dict):
        raise MalformedRequest("args must be an object")
    if operation == "close_trade":
        if set(args) != {"trade_id"} or not isinstance(args["trade_id"], str) \
                or not _TRADE_ID.match(args["trade_id"]):
            raise MalformedRequest("close_trade needs exactly a valid trade_id")
    elif operation == "set_market_type":
        if set(args) != {"market"} or args["market"] not in ("spot", "futures"):
            raise MalformedRequest("set_market_type needs market spot|futures")
    elif operation == "approval_decision":
        if (set(args) != {"item_id", "binding_hash", "decision"}
                or not isinstance(args["item_id"], str) or not _TRADE_ID.fullmatch(args["item_id"])
                or not isinstance(args["binding_hash"], str)
                or not re.fullmatch(r"[0-9a-f]{64}", args["binding_hash"])
                or args["decision"] not in ("APPROVED", "REJECTED")):
            raise MalformedRequest("approval_decision needs exact item_id, binding_hash and decision")
    elif args:
        raise MalformedRequest(f"{operation} takes no args")
    return dict(args)


@dataclass(frozen=True)
class OwnerRequest:
    request_id: str
    operation: str
    channel: str
    identity: str                        # adapter-authenticated channel identity
    issued_at: float                     # when the owner issued it (epoch s); required
    request_ref: str | None = None       # the channel's own message/update reference
    args: dict = field(default_factory=dict)
    meta: dict = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.request_id, str) or not _ID.match(self.request_id):
            raise MalformedRequest("request_id must match [A-Za-z0-9._:-]{8,128}")
        if self.operation not in OPERATIONS:
            raise MalformedRequest(f"unsupported operation: {self.operation!r}")
        if self.channel not in CHANNELS:
            raise MalformedRequest(f"unknown channel: {self.channel!r}")
        if not isinstance(self.identity, str) or not _TOKEN.match(self.identity):
            raise MalformedRequest("identity must be a short identifier")
        if self.request_ref is not None and (
                not isinstance(self.request_ref, str) or not _TOKEN.match(self.request_ref)):
            raise MalformedRequest("request_ref must be a short identifier")
        if (isinstance(self.issued_at, bool) or not isinstance(self.issued_at, (int, float))
                or not math.isfinite(self.issued_at)):
            raise MalformedRequest("issued_at must be finite epoch seconds")
        object.__setattr__(self, "args", _check_args(self.operation, self.args))
        object.__setattr__(self, "meta", _check_meta(self.meta))

    @property
    def is_control(self) -> bool:
        return self.operation in CONTROL_OPERATIONS

    def fingerprint(self) -> str:
        """What a request id is bound to; a reused id with another body is refused."""
        body = "|".join((self.operation, self.channel, self.identity,
                         str(self.request_ref), repr(sorted(self.args.items()))))
        return hashlib.sha256(body.encode()).hexdigest()

    def to_wire(self) -> dict:
        return {"request_id": self.request_id, "operation": self.operation,
                "channel": self.channel, "identity": self.identity,
                "request_ref": self.request_ref, "issued_at": float(self.issued_at),
                "args": dict(self.args), "meta": dict(self.meta)}

    @classmethod
    def from_wire(cls, raw) -> "OwnerRequest":
        if not isinstance(raw, dict):
            raise MalformedRequest("request must be an object")
        extra, missing = set(raw) - _REQUEST_KEYS, _REQUIRED - set(raw)
        if extra or missing:
            raise MalformedRequest(f"bad keys: extra={sorted(extra)} missing={sorted(missing)}")
        return cls(request_id=raw["request_id"], operation=raw["operation"],
                   channel=raw["channel"], identity=raw["identity"],
                   issued_at=raw["issued_at"], request_ref=raw.get("request_ref"),
                   args=raw["args"] if "args" in raw else {},
                   meta=raw["meta"] if "meta" in raw else {})


@dataclass(frozen=True)
class OwnerResult:
    """The safety contract. Adapters render it; wording is not the contract."""
    request_id: str | None
    operation: str | None
    status: str
    principal: str | None = None           # resolved by the kernel, never asserted
    channel: str | None = None
    request_ref: str | None = None
    control_state_before: str | None = None
    control_state_after: str | None = None
    reasons: tuple = ()
    supervisor_outcome: str | None = None
    risk_release: dict | None = None
    audit_event_ids: tuple = ()
    transition_event_id: int | None = None
    replayed: bool = False                 # returned from the idempotency record
    data: dict | None = None               # read operations only
    ts: str = field(default_factory=_now_iso)

    @property
    def disposition(self) -> str:
        return disposition_of(self.status)

    def to_wire(self) -> dict:
        d = asdict(self)
        d["reasons"] = list(self.reasons)
        d["audit_event_ids"] = list(self.audit_event_ids)
        d["disposition"] = self.disposition
        return d

    @classmethod
    def from_wire(cls, raw) -> "OwnerResult":
        if not isinstance(raw, dict) or not isinstance(raw.get("status"), str):
            raise MalformedRequest("result must be an object with a status")
        known = set(cls.__dataclass_fields__)
        kw = {k: v for k, v in raw.items() if k in known}
        kw["reasons"] = tuple(kw.get("reasons") or ())
        kw["audit_event_ids"] = tuple(kw.get("audit_event_ids") or ())
        return cls(**kw)

    def with_(self, **kw) -> "OwnerResult":
        d = {**asdict(self), "reasons": tuple(self.reasons),
             "audit_event_ids": tuple(self.audit_event_ids)}
        d.update(kw)
        return OwnerResult(**d)


def refused(req, reason: str, status: str = Status.REFUSED, **kw) -> OwnerResult:
    return OwnerResult(request_id=getattr(req, "request_id", None),
                       operation=getattr(req, "operation", None),
                       channel=getattr(req, "channel", None),
                       request_ref=getattr(req, "request_ref", None),
                       status=status, reasons=(reason,), **kw)
