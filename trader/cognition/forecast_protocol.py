"""Frozen direction-competition.v1 contract and pure closed-bar validation."""
import hashlib
import json
import math

TF = 14_400_000
PROTOCOL = {
    "version": "direction-competition.v1", "timeframe": "4h", "recent_bars": 5,
    "neutral_bps": 25, "grace_ms": 86_400_000, "fresh_ms": 300_000,
    "registration_guard_ms": 30_000,
    "max_episodes": 4096, "retention_ms": 30 * 86_400_000,
    "target": "close of first 4h bar opening strictly after registration",
    "baseline": "last closed price observed at registration; not executable",
    "hypotheses": ["persistence", "reversal", "unresolved"],
    "probability": None, "authority": "shadow_only",
}


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


PROTOCOL_ID = hashlib.sha256(encode(PROTOCOL).encode()).hexdigest()


def usable(bar, as_of):
    values = [bar.get(k) for k in ("open", "high", "low", "close", "volume")]
    if not all(isinstance(v, (float, int)) and not isinstance(v, bool)
               and math.isfinite(v) for v in values):
        return False
    op, high, low, close, volume = values
    return (min(op, high, low, close) > 0 and volume >= 0
            and low <= min(op, close) <= max(op, close) <= high
            and bar["open_ms"] % TF == 0
            and bar["open_ms"] + TF <= as_of and bar["available_ms"] <= as_of)

