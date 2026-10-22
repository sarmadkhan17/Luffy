"""Bounded projection of the safety-health artifact for the owner health read.

The artifact (``SafetyHealth.read()``) carries per-condition ``context`` and
per-producer ``heartbeats`` that embed large market diagnostics; the whole
artifact can exceed the IPC frame limit, which silently dropped the health
answer. The owner health response needs only the decision-relevant flags, so
this module projects those and nothing else. It never decides anything: the
flags are derived from the same artifact value with the same rules as
``SafetyHealth.entry_block`` / ``active_reasons`` / ``bootstrap_block``, and
the artifact itself is not modified.

``response_fits`` checks the encoded envelope the IPC server would send for the
worst-case nonce the server accepts (128 astral characters, 12 bytes each under
``ensure_ascii``) plus a MAC, so an over-bound answer is refused explicitly
instead of truncated or dropped. A standard client uses a 32-char hex nonce, so
its real frame is smaller than this envelope bound.
"""
import json

from ..observability.safety import SafetyHealth

PROJECTION = "owner-health-safety.v1"
OVERSIZE_REASON = "health_detail_exceeds_response_bound"
_NONCE_MAX, _MAC_LEN = 128, 64          # ipc: any str nonce of 16..128 chars, hex sha256 MAC
WORST_NONCE = "\U0010ffff" * _NONCE_MAX  # 12 bytes/char once json-escaped (surrogate pair)
_RESERVE = 1024                         # extra headroom beyond the worst-case envelope
_SMALL_STR = 128
_BOUND_KEYS = ("incident_id", "detected_at", "resolved_at")


def _scalar(value):
    """Small identifiers/times only; anything else is dropped, not guessed."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str) and len(value) <= _SMALL_STR:
        return value
    return None


def _condition(c: dict) -> dict:
    out = {"status": c.get("status"), "reason": c.get("reason")}
    if out["reason"] is not None and not isinstance(out["reason"], str):
        out["reason"] = str(out["reason"])          # as SafetyHealth.active_reasons
    for key in _BOUND_KEYS:
        v = _scalar(c.get(key))
        if v is not None:
            out[key] = v
    return out


def project_safety(v: dict) -> dict:
    """Essential, bounded view of one ``SafetyHealth.read()`` value.

    Reasons and condition names are kept whole: if they are themselves too
    large the caller refuses the answer rather than truncating them.
    """
    conditions = v.get("conditions") or {}
    bootstrap_blocked = SafetyHealth.bootstrap_block(v)
    active = [k + ":" + str(c.get("reason")) for k, c in conditions.items()
              if c.get("status") == "ACTIVE"]
    out = {
        "projection": PROJECTION,
        "schema": v.get("schema"),
        "revision": v.get("revision"),
        "observed_at": _scalar(v.get("observed_at")),
        "recovery_required": v.get("recovery_required"),
        "bootstrap_block": bootstrap_blocked,
        "entry_block": ("contained_bootstrap_not_ready" if bootstrap_blocked else
                        "critical_safety_requires_recovery"
                        if v.get("recovery_required") or active else None),
        "active_reasons": active + (["contained_bootstrap_not_ready"]
                                    if bootstrap_blocked else []),
        "conditions": {k: _condition(c) for k, c in conditions.items()},
        "heartbeat_count": len(v.get("heartbeats") or {}),
        "omitted": ["conditions.*.context", "heartbeats", "bootstrap.token"],
    }
    if "bootstrap" in v:
        boot = v["bootstrap"]
        status = boot.get("status") if isinstance(boot, dict) else None
        out["bootstrap"] = {
            "status": status if isinstance(status, str) and len(status) <= _SMALL_STR else None,
            "token_present": bool(isinstance(boot, dict) and isinstance(boot.get("token"), str)
                                  and boot["token"]),
        }
    return out


def frame_size(result, nonce: str = WORST_NONCE) -> int:
    """Bytes of the IPC response frame for ``result`` (same encoding as
    ``ipc.write_frame``, excluding the 4-byte length header). The default nonce
    gives the worst-case envelope bound; pass a real nonce for a measured size."""
    from .ipc import WIRE_VERSION
    frame = {"v": WIRE_VERSION, "nonce": nonce, "result": result.to_wire(),
             "mac": "0" * _MAC_LEN}
    return len(json.dumps(frame, default=str).encode("utf-8"))


def response_fits(result) -> bool:
    from .ipc import MAX_FRAME
    return frame_size(result) + _RESERVE <= MAX_FRAME
