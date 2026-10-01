"""Signal-occurrence identity — strategy-signal-occurrence.v1.

A signal occurrence is ONE signal from this exact strategy-spec version, for
this canonical instrument/action, evaluated on this exact closed signal bar.

It is NOT a multi-bar setup, an independent economic opportunity, an
episode, a thesis or a trade. Consecutive bars on which a condition stays
true are separate occurrences; nothing here merges them.

Key: (spec_id, spec_fingerprint, symbol, action, signal_timeframe,
signal_bar_close_ms), where `symbol` is `protective.venue_key` (BTC/USDT,
BTC/USDT:USDT and BTCUSDT are one market) and `signal_bar_close_ms` is the
integer close time of the fully closed bar the entry series was evaluated
on. The key is read only from fields the compiled spec evaluator recorded at
the firing branch; it is never reconstructed from `signal_bar_age_min`, the
decision timestamp, the wall clock or a nearest bar. Anything that cannot
establish the exact bar gets no key and an explicit unavailable reason.
"""
from __future__ import annotations

import hashlib
import json

from ..engine.protective import venue_key

VERSION = "strategy-signal-occurrence.v1"
FINGERPRINT_SCHEMA = "strategy-spec-signal-fingerprint.v1"

#: the spec content that governs whether and which way the compiled
#: evaluator fires. Exits, regime_filter (overwritten by the Analyst from
#: evidence), provenance, thesis prose, names and compiler-derived
#: data_requires are deliberately outside: they do not change the signal.
FINGERPRINT_FIELDS = ("entry_long", "entry_short", "filters", "timeframe",
                      "universe", "direction")

# ── unavailable reasons ─────────────────────────────────────────────────
#: signal carries no recorded closed-bar identity (legacy/library evaluator,
#: pre-v1 row, or no signal at all)
NO_CLOSED_BAR_IDENTITY = "no_closed_bar_identity"
#: compiled evaluator fired but could not establish the evaluated bar's
#: exact close (unknown timeframe length, unreadable/NaT bar time)
SIGNAL_BAR_CLOSE_UNAVAILABLE = "signal_bar_close_unavailable"
#: the entry series is not aligned row-for-row with the signal-timeframe frame
ENTRY_SERIES_MISALIGNED = "entry_series_misaligned"
#: recorded fields are present but not a valid exact identity
INVALID_OCCURRENCE_FIELDS = "invalid_occurrence_fields"

_ACTIONS = {"BUY", "SELL"}


def spec_fingerprint(spec) -> str:
    """sha256 of the canonical JSON of the signal-governing spec content."""
    payload = {"schema": FINGERPRINT_SCHEMA}
    payload.update({f: getattr(spec, f) for f in FINGERPRINT_FIELDS})
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      allow_nan=False)
    return hashlib.sha256(blob.encode()).hexdigest()


def bar_open_ms(value) -> int | None:
    """Exact integer epoch-ms of a bar's `ts` cell, or None.

    Timestamps convert through their integer nanosecond value, never through
    a float; integer cells are already epoch-ms. Anything else is unknown."""
    import numpy as np
    import pandas as pd
    if isinstance(value, (bool, np.bool_)):
        return None
    if isinstance(value, (int, np.integer)):
        return int(value)
    if isinstance(value, pd.Timestamp):     # NaT is not a Timestamp
        ns = int(value.value)
        return ns // 1_000_000 if ns % 1_000_000 == 0 else None
    return None


def signal_occurrence(signal) -> tuple[tuple | None, str | None]:
    """(key, None) for a signal with an exact recorded occurrence, else
    (None, reason). `signal` is a StrategySignal or its persisted dict."""
    if signal is None:
        return None, NO_CLOSED_BAR_IDENTITY
    if isinstance(signal, dict):
        params = signal.get("params") or {}
        symbol, action = signal.get("symbol"), signal.get("action")
    else:
        params = getattr(signal, "params", None) or {}
        symbol, action = signal.symbol, signal.action
    action = getattr(action, "value", action)
    if "signal_bar_close_ms" not in params:
        return None, NO_CLOSED_BAR_IDENTITY
    close_ms = params.get("signal_bar_close_ms")
    if close_ms is None:
        return None, (params.get("signal_occurrence_unavailable")
                      or SIGNAL_BAR_CLOSE_UNAVAILABLE)
    spec_id, fp = params.get("spec_id"), params.get("spec_fingerprint")
    tf = params.get("signal_timeframe")
    if (type(close_ms) is not int or action not in _ACTIONS
            or not (isinstance(spec_id, str) and spec_id)
            or not (isinstance(fp, str) and fp)
            or not (isinstance(tf, str) and tf)
            or not (isinstance(symbol, str) and symbol)):
        return None, INVALID_OCCURRENCE_FIELDS
    return (spec_id, fp, venue_key(symbol), action, tf, close_ms), None


def occurrence_key(signal) -> tuple | None:
    return signal_occurrence(signal)[0]
