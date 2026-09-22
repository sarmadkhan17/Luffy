"""RNG-free, non-inferential fixtures for the M3.2 shard layer.

``ArithmeticToken`` is NOT a random generator: it returns fixed arithmetic patterns keyed by the protocol seed
tuple, mimicking the API shapes the runner consumes (same idea as the bundle preflight stub).  ``StubRegistry``
hands out one token per seed tuple and refuses duplicates, like the protocol ``SeedRegistry``, but never
constructs a numpy/python generator.  Worlds, receipts and statistics below are produced by the *real* runner code
(``generate_world``, ``apply_injection``, ``world_receipt`` with the exact-equivalent ``FastMetric``) fed with these
tokens; they carry no statistical meaning and are never a validation world.
"""
from __future__ import annotations

import contextlib
import dataclasses
import hashlib
from pathlib import Path

import numpy as np

FIXTURE_CELLS = (("N1/C0", 4), ("N3/C4", 3), ("P1/C0", 4), ("P4/C3", 3), ("P7/C1", 2))   # (cell name, worlds)
FIXTURE_DRAWS = 3
_STATE = {"rejections": True}


class ArithmeticToken:
    """Deterministic arithmetic stand-in for a generator (no randomness anywhere)."""

    def __init__(self, key):
        self.key = tuple(int(k) for k in key)
        self.base = sum((i + 1) * v for i, v in enumerate(self.key)) % 10_007
        self.calls = 0

    def _offset(self) -> int:
        self.calls += 1
        return (self.base + 7_919 * self.calls) % 10_007

    def _arr(self, size) -> np.ndarray:
        n = int(np.prod(size)) if size is not None else 1
        return np.arange(n, dtype=np.int64) + self._offset()

    @staticmethod
    def _shape(values, size):
        return values.reshape(size) if size is not None else values[0]

    def random(self, size=None):
        return self._shape((self._arr(size) * 0.6180339887498949) % 1.0, size)

    def integers(self, lo, hi, size=None):
        return self._shape(self._arr(size) % (hi - lo) + lo, size)

    def standard_normal(self, size=None):
        return self._shape(np.sin(self._arr(size) * 1.7) * 1.5, size)

    def standard_t(self, df, size=None):
        return self._shape(np.cos(self._arr(size) * 0.9) * 2.0, size)

    def geometric(self, p, size=None):
        return self._shape(self._arr(size) % 3 + 1, size)

    def choice(self, a, size=None, p=None):
        return self._shape(self._arr(size) % a, size)

    def permutation(self, x):
        x = np.asarray(x)
        return np.roll(x, 1 + self._offset() % max(len(x) - 1, 1)) if len(x) > 1 else x.copy()


class StubRegistry:
    """Same call shape as ``SeedRegistry.generator``; returns tokens, refuses duplicate tuples, builds no generator."""

    def __init__(self, master_seed: int):
        self.master_seed, self.seen = master_seed, set()

    def generator(self, phase, correlation, dgp, world, stream):
        key = (self.master_seed, phase, correlation, dgp, world, stream)
        if key in self.seen:
            raise RuntimeError("duplicate_seed_tuple")
        self.seen.add(key)
        return ArithmeticToken(key)


def fixture_plan(runner, cells=FIXTURE_CELLS) -> list[dict]:
    by_name = {c["name"]: c for c in runner.cell_plan()}
    return [{**by_name[name], "worlds": worlds} for name, worlds in cells]


def _stub_exact_bh(exceedances, draws=None):
    """Deterministic stand-in so fixtures exercise non-zero R/V/true-discovery paths (draws are far below 7,679)."""
    return {h for h, x in enumerate(exceedances) if x is not None and x == 0 and h % 2 == 0}


@contextlib.contextmanager
def installed(runner, *, rejections: bool = True, refuse_every: int = 4):
    """Open the FastMetric gate (RNG-free, hash-level); optionally stub BH so fixtures reject some hypotheses and mark
    every ``refuse_every``-th world (index % refuse_every == refuse_every - 1) as a link-crossing refused world."""
    old_gate, old_bh, old_gen = dict(runner._GATE), runner.exact_bh, runner.generate_world
    runner.open_statistic_gate(runner.equivalence_gate_static())
    if rejections:
        runner.exact_bh = _stub_exact_bh
    if refuse_every:
        def generate(dgp, correlation, world_index, data_rng, member_rng):
            world = old_gen(dgp, correlation, world_index, data_rng, member_rng)
            if world_index % refuse_every == refuse_every - 1:
                world = dataclasses.replace(world, metadata={**world.metadata, "links_valid": False})
            return world
        runner.generate_world = generate
    try:
        yield
    finally:
        runner._GATE.clear()
        runner._GATE.update(old_gate)
        runner.exact_bh, runner.generate_world = old_bh, old_gen


def fixture_sha256() -> str:
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
