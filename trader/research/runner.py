"""One step of the search: plan, run a child, write, decide.

The kernel thread owns the schedule, the budget and every write; the child
owns the arithmetic. This file is where the three failures the spec names are
handled, because the child may not survive them:

- the child crashes or exceeds its deadline -> the batch is marked failed in
  the ledger and the next batch resumes from the ledger;
- the database is locked -> the child never writes, so the thread simply
  retries its `_tx()` on the next batch;
- CPU contention -> `nice 19`, and the search stands down entirely when the
  last trade cycle took longer than `skip_if_cycle_s`. Measured: the cycle
  runs p50 17.7 s, p90 26.1 s, p99 65.3 s, so 45 s stands down for genuine
  congestion without reacting to normal variation.
"""
from __future__ import annotations

import logging
import time

from ..core.child import run_child
from . import control, growth, job, planner
from .combo import subsets
from .ledger import Ledger

log = logging.getLogger(__name__)

DEFAULTS = {
    "enabled": False,
    "horizons": ["4h"],
    "geometries": ["trail", "fixed"],
    "batch_combos": 40,
    "batch_seconds": 900,
    "soft_margin_s": 60,
    "interval_seconds": 60,
    "nice": 19,
    "skip_if_cycle_s": 45.0,
    "discovery_null_draws": 30,
    "seed": 17,
    "beam": 12,
    "max_parts": 6,
    "grow_max_p": 0.25,
    "control_max_p": 0.01,
    "min_discovery_symbols": 16,
}


class ResearchRunner:
    def __init__(self, journal, cfg: dict, run=run_child):
        self.journal = journal
        self.cfg = cfg
        self.run = run
        self.ledger = Ledger(journal)
        self.ledger.ensure()

    # ── config ───────────────────────────────────────────────────────────
    def _r(self, key):
        return (self.cfg.get("research") or {}).get(key, DEFAULTS[key])

    def _null_draws(self) -> int:
        """`discovery_null_draws`, clamped to `null_baseline.MIN_DRAWS`.

        This is a daemon thread inside a live trading kernel: a hand-edited
        config below the floor must not silently make every combination
        read `untestable` while still reporting real trades. Refusing to
        start the loop over a config typo is a worse failure than running
        at the floor and saying so loudly.
        """
        from ..strategy.null_baseline import MIN_DRAWS
        n = int(self._r("discovery_null_draws"))
        if n < MIN_DRAWS:
            log.warning(f"research discovery_null_draws={n} is below "
                        f"null_baseline.MIN_DRAWS={MIN_DRAWS}; clamping to "
                        f"the floor rather than running a search that can "
                        f"never register a percentile")
            return MIN_DRAWS
        return n

    def _symbols(self, tf: str):
        from .universe import DISCOVERY, HELDOUT, coverage
        try:
            have = coverage([tf]).get(tf, {})
        except Exception as e:                          # noqa: BLE001
            log.warning(f"research coverage unavailable: {e}")
            have = {}
        disc = [s for s in DISCOVERY if not have or s in have]
        held = [s for s in HELDOUT if not have or s in have]
        return disc, held

    # ── one step ─────────────────────────────────────────────────────────
    def step(self, cycle_seconds: float | None = None) -> dict:
        if not (self.cfg.get("research") or {}).get("enabled",
                                                    DEFAULTS["enabled"]):
            return {"skipped": "disabled"}
        if cycle_seconds is not None \
                and float(cycle_seconds) > float(self._r("skip_if_cycle_s")):
            return {"skipped": "busy", "cycle_seconds": cycle_seconds}

        batch = planner.next_batch(self.ledger, self.cfg, self.journal)
        if batch.kind == "idle":
            return {"skipped": "idle", "reason": batch.reason}

        disc, held = self._symbols(batch.tf)
        if len(disc) < int(self._r("min_discovery_symbols")):
            return {"skipped": "underpowered_universe", "tf": batch.tf,
                    "discovery_symbols": len(disc)}

        if batch.kind == "measure":
            return self._measure(batch, disc, held)
        return self._evaluate(batch, disc, held)

    # ── the two jobs ─────────────────────────────────────────────────────
    def _paths(self) -> dict:
        p = (self.cfg.get("research") or {}).get("paths") or {}
        return {"candles": p.get("candles"), "derivs": p.get("derivs")}

    def _measure(self, batch, disc, held) -> dict:
        from .vocab import GAUGES, expressions, part_requires, Part
        reqs = {"ohlcv"}
        for g in GAUGES:
            reqs.update(part_requires(
                Part(g.key, "gauge", g.key, f"{g.long} > 0",
                     f"{g.short} > 0")))
        payload = {"tf": batch.tf, "symbols": disc, "heldout_symbols": held,
                   "requires": sorted(reqs), "cfg": self.cfg,
                   "paths": self._paths(),
                   "exprs": expressions(batch.tf)}
        bid = self.ledger.start_batch(batch.tf, "", "measure", 0)
        t0 = time.monotonic()
        res = self.run(job.measure_job, payload,
                       timeout_s=float(self._r("batch_seconds")),
                       nice=int(self._r("nice")))
        if not res.ok:
            self.ledger.finish_batch(bid, False, time.monotonic() - t0,
                                     res.error)
            log.warning(f"research measure {batch.tf} failed: {res.error}")
            return {"kind": "measure", "tf": batch.tf, "ok": False,
                    "skipped": False, "error": res.error}
        v = res.value or {}
        n = self.ledger.record_gauges(batch.tf, v.get("gauges", {}))
        self.ledger.record_slices(batch.tf, v.get("cut_ms", 0),
                                  v.get("counts", {}))
        self.ledger.finish_batch(bid, True, time.monotonic() - t0)
        usable = sum(1 for m in v.get("gauges", {}).values()
                     if m.get("usable"))
        log.info(f"research measured {batch.tf}: {usable}/{n} gauges usable")
        return {"kind": "measure", "tf": batch.tf, "ok": True,
                "skipped": False, "gauges": n, "usable": usable}

    def _evaluate(self, batch, disc, held) -> dict:
        symbols = disc
        if batch.round == "control":
            symbols = control.incumbent_universe(self.journal)
        reqs = {"ohlcv"}
        for c in batch.combos:
            reqs.update(c.requires)
        timeout = float(self._r("batch_seconds"))
        payload = {"tf": batch.tf, "geo": batch.geo, "symbols": symbols,
                   "heldout_symbols": held, "requires": sorted(reqs),
                   "combos": [c.as_dict() for c in batch.combos],
                   "cfg": self.cfg, "paths": self._paths(),
                   "draws": self._null_draws(),
                   "seed": int(self._r("seed")),
                   "soft_deadline_s": max(
                       30.0, timeout - float(self._r("soft_margin_s")))}
        bid = self.ledger.start_batch(batch.tf, batch.geo, batch.round,
                                      len(batch.combos))
        t0 = time.monotonic()
        res = self.run(job.evaluate_job, payload, timeout_s=timeout,
                       nice=int(self._r("nice")))
        if not res.ok:
            self.ledger.finish_batch(bid, False, time.monotonic() - t0,
                                     res.error)
            log.warning(f"research batch {batch.tf}/{batch.geo}/"
                        f"{batch.round} failed: {res.error}")
            return {"kind": "evaluate", "tf": batch.tf, "geo": batch.geo,
                    "round": batch.round, "ok": False, "skipped": False,
                    "error": res.error}

        by_hash = {c.hash: c for c in batch.combos}
        recorded = 0
        for r in (res.value or {}).get("results", []):
            self._record(r, by_hash.get(r["hash"]), batch)
            recorded += 1
        self.ledger.finish_batch(bid, True, time.monotonic() - t0)
        log.info(f"research {batch.tf}/{batch.geo}/{batch.round}: "
                 f"{recorded} evaluated in "
                 f"{(res.value or {}).get('elapsed_s', 0):.0f}s "
                 f"({(res.value or {}).get('skipped', 0)} deferred)")
        return {"kind": "evaluate", "tf": batch.tf, "geo": batch.geo,
                "round": batch.round, "ok": True, "recorded": recorded,
                "skipped": (res.value or {}).get("skipped", 0)}

    def _record(self, r: dict, c, batch) -> None:
        if batch.round == "control":
            # `research_controls` (tf, window) is the only bookkeeping the
            # planner reads to decide a window has already been calibrated
            # (`_windows_needing_control` checks `led.control()`, never
            # `led.known()`), so a control combo has no reason to also
            # occupy a row in `research_combos` under a "verdict" the
            # schema never intended it to hold ("survivor | grow | prune").
            # Writing it there put a tied-score control row in front of the
            # real singles on any unfiltered `rows()` read.
            powered = control.powered(r, float(self._r("control_max_p")))
            self.ledger.record_control(batch.tf, r.get("window", "ohlcv"),
                                       r, powered)
            return
        ctrl = self.ledger.control(batch.tf, r.get("window", "ohlcv"))
        label = control.label(r, bool(ctrl and ctrl["powered"]))
        parent = self.ledger.result(r["parent"]) if r.get("parent") else None
        subset_results = {}
        if c is not None and c.k >= 2:
            for i, s in enumerate(subsets(c)):
                got = self.ledger.result(s.hash)
                if got is not None:
                    subset_results[c.parts[i].key] = got
        verdict, reason, abl = growth.decide(
            r, parent=parent, subset_results=subset_results,
            max_p=float(self._r("grow_max_p")))
        if label == "underpowered" and verdict == "prune":
            reason = (f"{reason} — but the known-good rule cannot register "
                      f"in this window either: UNDERPOWERED, not no-edge")
        self.ledger.record_result(r, verdict, reason, abl, label=label)
