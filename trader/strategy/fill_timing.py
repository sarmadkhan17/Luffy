"""D6 — where a closed-bar signal may legitimately fill.

Three facts stay separate: candle close, receipt availability, and the fill.
A signal on bar i is decided no earlier than max(close, availability)
(`market_provenance.decision_ms`) and fills at the open of the first retained
bar starting at or after that instant — never at the signal candle's close,
which was already behind LUFFY when a late receipt arrived. Frames with no
receipt columns, and explicitly retrospective datasets, decide at close under
their own declared "no historical availability claim" evidence scope (never
factory-validated); in a replay frame, unknown availability refuses.

The signal is valid for one bar: availability later than one bar after close,
or a first executable open a full bar after the decision, is refused. No
parameter is added; the window is the bar's own timeframe.
"""
from __future__ import annotations

import numpy as np

from ..core.types import TF_MS
from ..data import market_provenance as mp

POLICY_ID = "next-executable-open.v1"


def _tf(df) -> str:
    tf = df.attrs.get("timeframe")
    if tf in TF_MS:
        return tf
    step = int(np.median(np.diff(mp.ms(df["ts"])))) if len(df) > 1 else 0
    for name, size in TF_MS.items():
        if size == step:
            return name
    raise ValueError("fill_timing_timeframe_unknown")


def fills(df, history=0):
    """(fill_i, reason) per signal bar: first executable bar index, else -1 and why.

    Reasons: availability_unknown, signal_expired, no_executable_bar,
    fill_gap (the next retained bar opens a full bar after the decision),
    unexecutable_price (non-finite open), and, for receipt-bearing frames
    only, history_gap: a bar missing from the `history` bars before the
    signal, which would silently stretch every indicator window."""
    n = len(df)
    fill = np.full(n, -1, dtype=np.int64)
    why = np.full(n, "", dtype=object)
    if n == 0:
        return fill, why
    tf = _tf(df)
    step = TF_MS[tf]
    ts = mp.ms(df["ts"])
    retrospective = df.attrs.get("read_mode") == "retrospective"
    decision = mp.decision_ms(df.drop(columns="available_at_ms") if retrospective and "available_at_ms" in df else df, tf)
    unknown = np.isnan(decision)
    first = np.searchsorted(ts, np.where(unknown, 0.0, decision), side="left")
    clipped = np.minimum(first, n - 1)
    lag = ts[clipped] - decision
    opens = df["open"].to_numpy(float)
    gapped = np.zeros(n, dtype=bool)
    if history and "available_at_ms" in df and not retrospective and n > history:
        gapped[history:] = ts[history:] - ts[:-history] != history * step
    for reason, refused in (
            ("history_gap", gapped),
            ("unexecutable_price", ~np.isfinite(opens[clipped])),
            ("fill_gap", lag >= step),
            ("no_executable_bar", first >= n),
            ("signal_expired", decision - (ts + step) > step),
            ("availability_unknown", unknown)):
        why[refused] = reason          # later entries take precedence
    fill[why == ""] = first[why == ""]
    return fill, why


def summary(df, history=0) -> dict:
    """Counts of executable signal bars and refusals by reason, for evidence."""
    fill, why = fills(df, history)
    out = {"policy": POLICY_ID, "executable": int((fill >= 0).sum())}
    for reason in sorted(set(why[fill < 0])):
        out[reason] = int((why == reason).sum())
    return out
