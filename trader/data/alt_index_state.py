"""Dependency-aware reuse for the computed equal-weight alt index (RF01).

`refresh_all` rebuilt the whole return panel and cumulative index every tick.
This keeps the last COMMITTED numeric state in process memory and decides, from
the actual per-member closes and revision IDs, how much has to be recomputed:

    unchanged  numbers and ancestry identical to the committed output and the
               store still ends where it did -> nothing to rebuild or save
    ancestry   numbers identical, constituent revisions differ -> numeric
               state reused, output re-saved with the NEW ancestry
    suffix     recompute from the earliest changed member close: that row, the
               next row (its predecessor close changed) and every cumulative
               level after it, seeded from the stored product, bit-for-bit
               the full cumprod's multiplications
    full       anything whose boundary cannot be shown: cold start, restart,
               membership / min_members change, a moved grid origin (e.g. the
               40000-bar window rolling over rebases every level), a failed
               earlier save

State is installed only by `commit`, after the store accepted the output, so a
failed save or a restart can only lead to a full recompute, never to a stale
reuse. Nothing here reads or writes SQLite; the only store question is
`last_ts('alts')`, answered by the caller.
"""
from __future__ import annotations

import threading
from typing import NamedTuple

import numpy as np
import pandas as pd

from . import ref_sources as rs

ALT_TF_MS = 4 * 3_600_000


class Entry:
    __slots__ = ('members', 'min_members', 'series', 'ancestry', 'grid', 'cum', 'level',
                 'committed', 'out_last_ts', 'complete')

    def __init__(self, members, min_members, series, ancestry, grid, cum, level):
        self.members, self.min_members, self.series = members, min_members, series
        self.ancestry, self.grid, self.cum, self.level = ancestry, grid, cum, level
        self.committed = False
        self.out_last_ts = None      # store.last_ts('alts') right after the committing save
        self.complete = False        # every frame row persisted, none still forming


class Plan(NamedTuple):
    kind: str                # unchanged | ancestry | suffix | full
    reason: str
    frame: object            # DataFrame to save; None when unchanged
    entry: object            # candidate state, installed by commit()
    rows: int                # index rows in the output
    recomputed: int          # rows whose numbers were recomputed

    def report(self) -> dict:
        return dict(kind=self.kind, reason=self.reason, rows=self.rows, recomputed=self.recomputed)


def _difference(old, new):
    """Earliest event time at which two (ts, close) member series differ."""
    (ots, oc), (nts, nc) = old, new
    n = min(len(ots), len(nts))
    same = (ots[:n] == nts[:n]) & (oc[:n].view('i8') == nc[:n].view('i8'))
    i = n if same.all() else int(np.argmin(same))
    if i == n and len(ots) == len(nts):
        return None
    return min(a[i] for a in (ots, nts) if i < len(a))


class AltIndexCache:
    def __init__(self):
        self._lock = threading.Lock()
        self._entries: dict = {}

    def invalidate(self, key=None):
        with self._lock:
            if key is None:
                self._entries.clear()
            else:
                self._entries.pop(key, None)

    def prepare(self, key, frames: dict, min_members: int = 5, *, store_last_ts=None,
                allow_skip: bool = True) -> Plan:
        # allow_skip=False: the caller has no committed-content witness for the
        # stored output (last_ts alone cannot show history is unreplaced), so
        # identical inputs reuse numbers but are still re-saved ('ancestry').
        closes = rs._member_closes(frames)
        if not closes:
            return Plan('full', 'no_members', rs._empty(), None, 0, 0)
        grid = rs._grid_of(closes)
        series = {sym: (s.index.values, np.ascontiguousarray(s.to_numpy(dtype=float)))
                  for sym, s in closes.items()}
        ancestry = rs._constituents(frames)
        with self._lock:
            entry = self._entries.get(key)

        def build(kind, reason, cum, level, recomputed):
            candidate = Entry(tuple(closes), min_members, series, ancestry, grid, cum, level)
            return Plan(kind, reason, rs._alts_frame(grid, level, frames, min_members),
                        candidate, len(grid), recomputed)

        def full(reason):
            cum, level = rs._full_numeric(closes, grid, min_members)
            return build('full', reason, cum, level, len(grid))

        if entry is None or not entry.committed:
            return full('cold' if entry is None else 'uncommitted')
        if entry.members != tuple(closes) or entry.min_members != min_members:
            return full('membership_changed')
        if entry.grid[0] != grid[0]:
            return full('origin_changed')        # every level is rebased
        earliest = None
        for sym in closes:
            d = _difference(entry.series[sym], series[sym])
            if d is not None and (earliest is None or d < earliest):
                earliest = d
        p = len(grid) if earliest is None else int(grid.searchsorted(
            pd.Timestamp(earliest).tz_localize('UTC'), side='left'))
        p = min(p, len(entry.grid), len(grid))
        if earliest is None or p >= len(entry.grid) == len(grid):
            # No member close moved (an off-grid stamp cannot reach the index).
            if not np.array_equal(entry.grid.asi8, grid.asi8):
                return full('grid_changed')
            if (allow_skip and ancestry == entry.ancestry and entry.complete and entry.out_last_ts is not None
                    and store_last_ts == entry.out_last_ts):
                return Plan('unchanged', 'same_inputs', None, entry, len(grid), 0)
            return build('ancestry', 'same_numbers', entry.cum, entry.level, 0)
        if p == 0:
            return full('changed_from_origin')
        sub_cum, sub_level = rs._suffix_numeric(closes, grid, min_members, p, float(entry.cum[p - 1]))
        return build('suffix', 'changed_from_row_%d' % p, np.concatenate([entry.cum[:p], sub_cum]),
                     np.concatenate([entry.level[:p], sub_level]), len(grid) - p)

    def commit(self, key, plan: Plan, saved_rows: int, saved_now_ms: int, last_ts):
        """Install `plan.entry` once the store accepted exactly this output."""
        entry = plan.entry
        if entry is None:
            return
        last = entry.grid[-1]
        entry.committed = True
        entry.out_last_ts = last_ts
        # Every row persisted and none could have been stored as still forming.
        entry.complete = (saved_rows == plan.rows and
                          int(last.timestamp() * 1000) + ALT_TF_MS <= saved_now_ms)
        with self._lock:
            self._entries[key] = entry


CACHE = AltIndexCache()
