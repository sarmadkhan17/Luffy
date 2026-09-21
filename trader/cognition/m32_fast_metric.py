"""Exact-equivalent optimized M3.2 ``_metric`` for whole-block-permutation validation worlds.

This reproduces ``m32_search._metric`` bit for bit (float64) on the synthetic-world row
representation used by the Scheme D harness (a fixed row grid of ``blocks x rows_per_block`` slots,
memberships fixed at slots, outcome + mask tensors permuted by whole blocks).  It does not change the
estimand.  It replaces per-hypothesis dict/string/Counter work by pre-encoded arrays:

* permutation-invariant quantities (overall class counts, overall median, MAD, baseline scores, the
  class list, value ranks) are computed once per world;
* class counts of hit rows are gathered from pair-block tensors ``P[b, b', h] = sum_r M[b, r, h] Z[b', r]``
  so a draw costs ``blocks x hypotheses`` additions instead of a 3840 x 384 pass;
* ``Counter.most_common(1)`` tie-breaks (first class encountered in slot order) are reproduced from
  first-occurrence tensors, only for the hypotheses that actually tie;
* the persistence median of the hit rows is an order statistic located in the fixed global value ranking:
  a pair-block histogram over rank chunks finds the chunk holding the k-th hit, and a small in-chunk
  cumulative count finds the row (ties in value are irrelevant: equal floats);
* ``sum(scores)`` uses the CPython 3.12 Neumaier compensated algorithm, replicated column-wise.

Anything outside the proven-equivalent domain (class codes outside 0..9, or both +0.0 and -0.0 among
valid persistence values, where the reference's stable-sort tie order is arrangement dependent)
falls back to the reference implementation, so equivalence is total by construction.
No RNG is constructed here.
"""
from __future__ import annotations

import math
from typing import Optional, Sequence

import numpy as np

from .m32_search import _metric

KINDS = ("case_kind", "persistence", "continuation")
BIG = 1 << 40
NONE8 = 255


def compensated_sum_columns(scores: Sequence[np.ndarray]) -> np.ndarray:
    """Column-wise replica of CPython 3.12 ``sum(list_of_floats)`` (int start 0, Neumaier compensation)."""
    total = np.float64(0.0) + scores[0]
    comp = np.zeros_like(total)
    for x in scores[1:]:
        t = total + x
        comp = comp + np.where(np.abs(total) >= np.abs(x), (total - t) + x, (x - t) + total)
        total = t
    return np.where((comp != 0) & np.isfinite(comp), total + comp, total)


def reference_rows(categorical, persistence, observed, block_map, blocks: int, rows_per_block: int):
    """Row list and lookup exactly as the Scheme D harness (``_arranged_rows``) builds them."""
    n = blocks * rows_per_block
    idx = np.arange(n)
    if block_map is None:
        source = idx
    else:
        source = np.asarray(block_map)[idx // rows_per_block] * rows_per_block + idx % rows_per_block
    cat, per, obs = categorical[source], persistence[source], observed[source]
    rows, lookup = [], {}
    for i in range(n):
        target = f"target-{i}"
        rows.append({"row_id": f"row-{i}",
                     "features": {"direction": 1, "current_row_id": target},
                     "labels": {"case_kind": int(cat[i]) if obs[i] else None,
                                "price_change_bps": float(per[i]) if obs[i] else None}})
        if obs[i]:
            lookup[target] = {"row_type": f"class-{int(cat[i])}", "producer": "synthetic"}
    return rows, lookup


def reference_statistics(memberships, categorical, persistence, observed, labels, block_map=None,
                         blocks: int = 48, rows_per_block: int = 80):
    """Reference: production ``_metric`` per hypothesis.  Returns a list of signed effects (None = untestable)."""
    rows, lookup = reference_rows(np.asarray(categorical), np.asarray(persistence, dtype=np.float64),
                                  np.asarray(observed), block_map, blocks, rows_per_block)
    ids = np.asarray([f"row-{i}" for i in range(blocks * rows_per_block)], dtype=object)
    out: list[Optional[float]] = []
    for h, label in enumerate(labels):
        result = _metric(rows, set(ids[memberships[:, h]]), label, lookup)
        out.append(result.get("signed_effect"))
    return out


class FastMetric:
    """Per-world encoder; ``statistics(block_map)`` returns ``(signed, ok)`` for all hypotheses."""

    def __init__(self, memberships, categorical, persistence, observed, labels, *,
                 blocks: int = 48, rows_per_block: int = 80, chunk: int = 64, collect_counters: bool = False):
        self.memberships = np.ascontiguousarray(memberships, dtype=np.bool_)
        self.R, self.H = self.memberships.shape
        self.B, self.RPB = int(blocks), int(rows_per_block)
        if self.R != self.B * self.RPB:
            raise ValueError("row count does not match the block geometry")
        self.labels = tuple(labels)
        if len(self.labels) != self.H or any(label not in KINDS for label in self.labels):
            raise ValueError("bad hypothesis labels")
        self.categorical = np.asarray(categorical)
        self.persistence = np.asarray(persistence, dtype=np.float64)
        self.observed = np.asarray(observed, dtype=np.bool_)
        self.ar = np.arange(self.B, dtype=np.int64)
        self.cat_idx = np.asarray([h for h, l in enumerate(self.labels) if l != "persistence"], dtype=np.int64)
        self.per_idx = np.asarray([h for h, l in enumerate(self.labels) if l == "persistence"], dtype=np.int64)
        self.Hc, self.Hp = len(self.cat_idx), len(self.per_idx)
        self.chunk = max(1, int(chunk))
        self.counters = {} if collect_counters else None
        self.unsupported = self._unsupported_reason()
        self._FH = self._FN = None
        if self.unsupported is None:
            self._precompute()

    # ------------------------------------------------------------------ domain
    def _unsupported_reason(self) -> Optional[str]:
        obs = self.observed
        if obs.any():
            if not np.issubdtype(self.categorical.dtype, np.integer):
                return "categorical_not_integer"
            cat = self.categorical[obs]
            if cat.min() < 0 or cat.max() > 9:
                return "class_code_outside_0_9"      # sorted(set(labels), key=str) would differ from numeric order
        if self.Hp:
            valid = obs & np.isfinite(self.persistence)
            zeros = self.persistence[valid][self.persistence[valid] == 0]
            if zeros.size and np.signbit(zeros).any() and (~np.signbit(zeros)).any():
                return "mixed_signed_zero"           # stable-sort tie order would decide the sign of zero
        return None

    def _count(self, key: str, n: int = 1) -> None:
        if self.counters is not None:
            self.counters[key] = self.counters.get(key, 0) + n

    # ------------------------------------------------------------------ per-world precompute
    def _precompute(self) -> None:
        B, RPB, obs = self.B, self.RPB, self.observed
        self.N = int(obs.sum())
        self.cls = np.unique(self.categorical[obs]) if self.N else np.empty(0, dtype=np.int64)
        self.K = len(self.cls)
        self.n_c = np.asarray([int((obs & (self.categorical == c)).sum()) for c in self.cls], dtype=np.int64)
        M3 = self.memberships.reshape(B, RPB, self.H)
        self._M3c = np.ascontiguousarray(M3[:, :, self.cat_idx])
        self._Z = [(obs & (self.categorical == c)).reshape(B, RPB) for c in self.cls]
        if self.N and self.Hc:
            Mf = self._M3c.astype(np.float32)
            self.Pc = np.empty((B, B, self.K * self.Hc), dtype=np.int16)
            for j, Z in enumerate(self._Z):
                self.Pc[:, :, j * self.Hc:(j + 1) * self.Hc] = np.matmul(Z.astype(np.float32)[None], Mf).astype(np.int16)
            first = np.full((self.K, B), BIG, dtype=np.int64)
            for j, Z in enumerate(self._Z):
                has = Z.any(axis=1)
                first[j, has] = Z.argmax(axis=1)[has]
            self.FOr = first
            counts = self.n_c
            top = np.flatnonzero(counts == counts.max())
            self._maj_static = int(top[0]) if len(top) == 1 else None
            self._baselines = [self._baseline(m) for m in range(self.K)]
        if self.N and self.Hp:
            self._precompute_persistence()

    def _baseline(self, m: int) -> float:
        """f1(majority, majority) with the reference's own Python arithmetic."""
        scores = []
        for j in range(self.K):
            tp, fp, fn = (int(self.n_c[j]), self.N - int(self.n_c[j]), 0) if j == m else (0, 0, int(self.n_c[j]))
            scores.append(0.0 if 2 * tp + fp + fn == 0 else 2 * tp / (2 * tp + fp + fn))
        return sum(scores) / len(scores)

    def _precompute_persistence(self) -> None:
        B, RPB, Hp, CH = self.B, self.RPB, self.Hp, self.chunk
        valid = self.observed & np.isfinite(self.persistence)
        self.Nv = int(valid.sum())
        if not self.Nv:
            return
        xv = self.persistence[valid]
        srt = np.sort(xv)
        n = len(srt)
        self.med = srt[n // 2] if n % 2 else (srt[n // 2 - 1] + srt[n // 2]) / 2
        dev = np.sort(np.abs(xv - self.med))
        mad = dev[n // 2] if n % 2 else (dev[n // 2 - 1] + dev[n // 2]) / 2
        self.denom = mad + 1.0
        order = np.argsort(xv, kind="stable")
        slots = np.flatnonzero(valid)[order]                        # source slots in ascending value rank
        self.xs_pad = np.append(xv[order], 0.0)
        self.src_b_pad = np.append(slots // RPB, 0)
        self.src_r_pad = np.append(slots % RPB, 0)
        self.NC = (self.Nv + CH - 1) // CH
        rank_pad = np.full(self.NC * CH, self.Nv, dtype=np.int64)
        rank_pad[:self.Nv] = np.arange(self.Nv)
        self.chunk_rank = rank_pad.reshape(self.NC, CH)
        self.chunk_valid = self.chunk_rank < self.Nv
        self.Mper = np.ascontiguousarray(self.memberships[:, self.per_idx])
        # pair-block histogram Q[b, b', h, t]: hit rows of hypothesis h in block b whose source row in block b'
        # falls in rank chunk t.  Each source row belongs to exactly one chunk.
        M3 = self.Mper.reshape(B, RPB, Hp).astype(np.int16)
        Q = np.zeros((B, B, Hp, self.NC), dtype=np.int16)
        rank_of_slot = np.full(self.R, -1, dtype=np.int64)
        rank_of_slot[slots] = np.arange(self.Nv)
        for rank, slot in enumerate(slots):
            Q[:, slot // RPB, :, rank // CH] += M3[:, slot % RPB, :]
        self.Q = Q.reshape(B, B, Hp * self.NC)
        self._cols = np.arange(Hp)

    def _first_tensors(self):
        """First-occurrence row index (or 255) of each class among hit / non-hit rows, per block pair."""
        if self._FH is None:
            B, RPB, K, Hc = self.B, self.RPB, self.K, self.Hc
            self._FH = np.full((K, B, B, Hc), NONE8, dtype=np.uint8)
            self._FN = np.full((K, B, B, Hc), NONE8, dtype=np.uint8)
            for j, Z in enumerate(self._Z):
                for b in range(B):
                    for target, member in ((self._FH, self._M3c[b]), (self._FN, ~self._M3c[b])):
                        hit = member[None, :, :] & Z[:, :, None]                # (B', RPB, Hc)
                        has = hit.any(axis=1)
                        target[j, b] = np.where(has, hit.argmax(axis=1), NONE8)
        return self._FH, self._FN

    # ------------------------------------------------------------------ public
    def statistics(self, block_map=None):
        """Signed effects for all hypotheses and an ``ok`` mask (False = the reference returns None)."""
        if block_map is None:
            pi = self.ar
        else:
            pi = np.asarray(block_map, dtype=np.int64)
            if pi.shape != (self.B,) or not np.array_equal(np.sort(pi), self.ar):
                raise ValueError("block_map must be a permutation of the blocks")
        signed = np.full(self.H, np.nan)
        ok = np.zeros(self.H, dtype=np.bool_)
        if self.unsupported is not None:
            self._count("fallback_vectors")
            ref = reference_statistics(self.memberships, self.categorical, self.persistence, self.observed,
                                       self.labels, None if block_map is None else pi, self.B, self.RPB)
            for h, v in enumerate(ref):
                if v is not None:
                    signed[h], ok[h] = v, True
            return signed, ok
        if not self.N:
            return signed, ok
        with np.errstate(all="ignore"):              # IEEE inf/nan propagate silently, as in the Python reference
            if self.Hc:
                s, o = self._categorical(pi)
                signed[self.cat_idx], ok[self.cat_idx] = s, o
            if self.Hp and self.Nv:
                s, o = self._persistence(pi)
                signed[self.per_idx], ok[self.per_idx] = s, o
        return signed, ok

    # ------------------------------------------------------------------ categorical
    def _majority(self, counts, active, pi, hit: bool) -> np.ndarray:
        mx = counts.max(axis=0)
        best = counts.argmax(axis=0)
        tie = ((counts == mx).sum(axis=0) > 1) & active
        if tie.any():
            t = np.flatnonzero(tie)
            self._count("hit_ties" if hit else "nonhit_ties", len(t))
            tensor = self._first_tensors()[0 if hit else 1]
            f = tensor[:, self.ar, pi][:, :, t].astype(np.int64)
            slots = np.where(f == NONE8, BIG, (self.ar * self.RPB)[None, :, None] + f).min(axis=1)
            slots = np.where(counts[:, t] == mx[t], slots, BIG)
            best[t] = slots.argmin(axis=0)
        return best

    def _overall_majority(self, pi) -> int:
        if self._maj_static is not None:
            return self._maj_static
        self._count("overall_ties")
        f = self.FOr[:, pi]                                            # (K, B): first r of class j in source block pi[b]
        slots = np.where(f >= BIG, BIG, self.ar[None, :] * self.RPB + f).min(axis=1)
        slots = np.where(self.n_c == self.n_c.max(), slots, BIG)
        return int(slots.argmin())

    def _categorical(self, pi):
        K, Hc, N = self.K, self.Hc, self.N
        hc = self.Pc[self.ar, pi].sum(axis=0, dtype=np.int16).reshape(K, Hc).astype(np.int64)
        hit_total = hc.sum(axis=0)
        ok = hit_total > 0
        nonhit = self.n_c[:, None] - hc
        nonhit_total = N - hit_total
        hm = self._majority(hc, ok, pi, True)
        nm = np.where(nonhit_total > 0, self._majority(nonhit, ok & (nonhit_total > 0), pi, False), hm)
        maj = self._overall_majority(pi)
        scores = []
        for j in range(K):
            pm, nq = hm == j, nm == j
            n_j = int(self.n_c[j])
            tp = np.where(pm & nq, n_j, np.where(pm, hc[j], np.where(nq, nonhit[j], 0)))
            fp = np.where(pm & nq, N - n_j, np.where(pm, hit_total - hc[j], np.where(nq, nonhit_total - nonhit[j], 0)))
            fn = n_j - tp
            den = 2 * tp + fp + fn
            scores.append(np.where(den == 0, 0.0, (2 * tp) / np.where(den == 0, 1, den)))
        mean = compensated_sum_columns(scores) / K
        signed = mean - self._baselines[maj]
        self._count("categorical_vectors")
        self._count("zero_lift", int(((signed == 0) & ok).sum()))
        return signed, ok

    # ------------------------------------------------------------------ persistence
    def _select(self, cumh: np.ndarray, k: np.ndarray, inv: np.ndarray) -> np.ndarray:
        """Value of the k-th (0-based) smallest hit row of every persistence hypothesis."""
        cols, CH = self._cols, self.chunk
        t = np.minimum((cumh <= k[:, None]).sum(axis=1), self.NC - 1)
        pre = np.where(t > 0, cumh[cols, np.maximum(t - 1, 0)], 0)
        rows = self.chunk_rank[t]                                              # (Hp, CH) ranks in the chunk
        slots = inv[self.src_b_pad[rows]] * self.RPB + self.src_r_pad[rows]    # slot each source row lands on
        bits = self.Mper[slots, cols[:, None]] & self.chunk_valid[t]
        within = np.cumsum(bits, axis=1)
        pos = np.minimum((within <= (k - pre)[:, None]).sum(axis=1), CH - 1)
        return self.xs_pad[self.chunk_rank[t, pos]]

    def _persistence(self, pi):
        inv = np.empty(self.B, dtype=np.int64)
        inv[pi] = self.ar
        hist = self.Q[self.ar, pi].sum(axis=0, dtype=np.int16).reshape(self.Hp, self.NC)
        cumh = np.cumsum(hist, axis=1, dtype=np.int64)
        n = cumh[:, -1]
        ok = n > 0
        v1 = self._select(cumh, np.maximum((n - 1) // 2, 0), inv)
        v2 = self._select(cumh, np.maximum(n // 2, 0), inv)
        hmed = np.where(n % 2 == 1, v1, (v1 + v2) / 2)
        signed = (hmed - self.med) / self.denom
        self._count("persistence_vectors")
        self._count("persistence_even", int(((n % 2 == 0) & ok).sum()))
        return signed, ok

    # ------------------------------------------------------------------ convenience
    @classmethod
    def from_world(cls, world, **kwargs):
        """Encode a Scheme D ``SyntheticWorld`` (labels from the frozen hypothesis table)."""
        from .m32_scheme_d_validation import HYPOTHESIS_TABLE
        return cls(world.memberships, world.categorical, world.persistence, world.observed,
                   [h.label for h in HYPOTHESIS_TABLE], **kwargs)

    def nbytes(self) -> int:
        total = 0
        for name in ("Pc", "Mper", "_M3c", "FOr", "xs_pad", "Q", "_FH", "_FN"):
            arr = getattr(self, name, None)
            if isinstance(arr, np.ndarray):
                total += arr.nbytes
        return total
