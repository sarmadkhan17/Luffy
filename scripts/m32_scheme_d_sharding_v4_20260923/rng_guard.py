"""RNG-attempt guard for the M3.2 v4 sharding integration tests and the v4 qualification tool.

Scope, not global session wrapping: enter ``RNGGuard()`` narrowly around the specific import / operation calls
under test (after any ``tmp_path`` fixture setup and before importing the v4 runner), per the owner instruction to
avoid false positives from ``tempfile`` (stdlib name generation) or pytest internals.  ``shard_layer.py``'s own
file mechanics never touch ``random``/``tempfile`` by construction (``unique_tmp_name`` is host+pid+time_ns+counter
based, deliberately RNG-free); the guard exists to catch anything unintended reachable *through* the frozen v4
runner or its dependencies, not to police the test harness itself.

Deliberately does NOT wrap ``SeedRegistry.generator`` (the sole legitimate, owner-authorization-gated RNG
construction path).  A historical preparation draft once wrapped that method directly (a spy that called through to
the real generator) and is recorded, not erased, in
``scripts/m32_scheme_d_validation_bundle_v4_20260922/artifacts/preparation_audit.json``
(``rng_objects_constructed`` 40/40/40, zero random variates or validation worlds actually produced because the spy's
generators were never consumed).  This guard takes the opposite approach: it never touches ``SeedRegistry`` at all,
and instead blocks every constructor/output primitive a generator or variate could come from, so a violation is
caught at the primitive, however it is reached.
"""
from __future__ import annotations

import os
import random as _random_mod
import secrets as _secrets_mod

import numpy as np

NUMPY_CONSTRUCTOR_CLASSES = ("Generator", "RandomState", "SeedSequence", "PCG64", "PCG64DXSM",
                             "MT19937", "Philox", "SFC64")
STDLIB_RANDOM_OUTPUT_FUNCS = ("random", "randint", "randrange", "choice", "choices", "uniform", "shuffle",
                              "sample", "getrandbits", "gauss", "normalvariate", "triangular", "betavariate",
                              "expovariate", "gammavariate", "lognormvariate", "paretovariate", "vonmisesvariate",
                              "weibullvariate", "binomialvariate")
SECRETS_OUTPUT_FUNCS = ("token_bytes", "token_hex", "token_urlsafe", "randbelow", "choice")


class RNGGuardViolation(RuntimeError):
    """Raised the instant a guarded scope attempts to construct or draw from a random source."""


class RNGGuard:
    """Counts and fails closed on numpy/stdlib/OS random-number construction and output within its ``with`` scope."""

    def __init__(self):
        self.constructor_attempts = 0
        self.random_variates_requested = 0
        self.validation_worlds_generated = 0
        self._patches: list[tuple] = []

    def _wrap(self, owner, attr: str, label: str, counter_name: str) -> None:
        """Replace a *module*-level attribute (never a C-extension type's own ``__init__``: ``numpy.random.Generator``
        et al. are immutable extension types and refuse ``setattr`` on themselves; ``numpy.random.Generator`` the
        module attribute is an ordinary, reassignable name, and every call site here looks the name up fresh at
        call time, so replacing the module binding intercepts construction exactly the same as wrapping ``__init__``
        would if it were possible)."""
        original = getattr(owner, attr, None)
        if original is None:
            return

        def blocked(*_a, **_k):
            setattr(self, counter_name, getattr(self, counter_name) + 1)
            raise RNGGuardViolation(f"{counter_name}:{label}")
        self._patches.append((owner, attr, original))
        setattr(owner, attr, blocked)

    def note_validation_world(self) -> None:
        """Called by a defensive wrapper around any production/validation entry point reachable in guarded scope."""
        self.validation_worlds_generated += 1
        raise RNGGuardViolation("validation_world_generation_blocked")

    def __enter__(self) -> "RNGGuard":
        for name in NUMPY_CONSTRUCTOR_CLASSES:
            self._wrap(np.random, name, f"numpy.random.{name}", "constructor_attempts")
        self._wrap(np.random, "default_rng", "numpy.random.default_rng", "constructor_attempts")
        self._wrap(_random_mod, "Random", "random.Random", "constructor_attempts")
        for fn in STDLIB_RANDOM_OUTPUT_FUNCS:
            self._wrap(_random_mod, fn, f"random.{fn}", "random_variates_requested")
        self._wrap(os, "urandom", "os.urandom", "random_variates_requested")
        for fn in SECRETS_OUTPUT_FUNCS:
            self._wrap(_secrets_mod, fn, f"secrets.{fn}", "random_variates_requested")
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        for owner, attr, original in reversed(self._patches):
            setattr(owner, attr, original)
        self._patches.clear()
        return False

    def counters(self) -> dict:
        return {"constructor_attempts": self.constructor_attempts,
                "random_variates_requested": self.random_variates_requested,
                "validation_worlds_generated": self.validation_worlds_generated}
