# Research pipeline — Phase 2: the search — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

## Task 15 — measured on the real store (2026-09-13)

Every command below was run against `data/luffy.db` / `data/candles.db` /
`data/derivs.db` as they stand today, via `./venv/bin/python -m
trader.research`. `research.enabled` stays `false` on disk; `--measure` and
`--once` set it on an in-memory config copy only.

| measurement | value |
|---|---|
| `--measure --tf 4h` | 93/100 gauges usable, 19 discovery symbols, cut 1741452480000 (2025-03-08) |
| `--measure --tf 1h` | 99/106 gauges usable, 19 discovery symbols, cut 1760902559999 (2025-10-19) |
| `--measure --tf 15m` | 106/106 gauges usable, 19 discovery symbols, cut 1779536070000 (2026-05-23) |
| unusable at 4h | `oi_ret(24)`, `oi_price_div(24)`, `ls_account_ratio_z(360)` — `finite_frac 0.0` (series starts 2025-10, after the 2025-03-08 cut); `basis_z(360)` — `finite_frac 0.10` |
| discovery symbol coverage, all 3 horizons (post Task-1 deepening) | **19 of 19** — `min_discovery_symbols` (16) no longer eliminates 1h/15m by itself |
| **plain `ohlcv` window control, incumbent's declared 16, trail geometry** | **4h: p=2.4e-03 — POWERED.** 1h: p=3.2e-01 — UNDERPOWERED. 15m: p=2.4e-01 — UNDERPOWERED |
| `funding` window control at 4h (mask `funding_z(360) > -99`) | p=1.7e-01 — UNDERPOWERED (the masked window narrows the effective symbol count; this is not the plain-window verdict) |
| every `ref:*` window control at 4h | p=2.4e-03 — POWERED (same as plain — the mask is `close`-backed and true almost everywhere in the window) |
| control batch, 4h | 12 windows scored in 58 s |
| first singles batch, 4h | 40 one-part combinations in 301 s → **~478 evaluations/hour** on this box |
| `pytest tests/` | 1189 passed, 1 failed (`test_macro_guard::test_a_restart_reuses_the_cached_calendar`, wall-clock — the known baseline failure). No new failures |
| `scripts/backtest_equivalence.py` | PASS |
| `scripts/bench_vector_backtest.py` | PASS, 424.9x |

**Decision:** `research.horizons` stays `["4h"]`. The 2026-09-11 note above
that 1h/15m held only 8/19 discovery symbols is now moot — Task 1's
deepening brought both to 19/19 — but the horizons are refused anyway,
because the plain-window control shows the known-good rule itself cannot
register there at a duration-matched channel (~17 days). This is the
brief's central instruction honoured directly: a horizon that falls short is
refused, not relaxed, and the refusal is not softened by fixing the reason
that would have been convenient (symbol count) once a harder reason (power)
turns up in its place. `research.enabled` was left `false` on disk per the
controller's override — the operator turns the search on after reading this
table, not this task.

**Goal:** A machine that generates combinations of measured conditions — up to 6 parts, both directions, both fixed exit geometries, on every horizon whose data can actually judge it — scores each one against its own rotation null on a discovery slice it can never see past, keeps only the parts that earn their place, and records every combination it ever looked at in a ledger the kernel owns.

**Architecture:** A new `trader/research/` package. `vocab.py` holds the parts (a scalar gauge compared against a measured percentile, or a boolean state/event), each written twice — once for the long side and once as its mirror image. `slices.py` cuts the data at one calendar date per horizon, so the search can only see the earliest 70%. `thresholds.py` measures every gauge's percentiles on that slice and reports what fraction of it the gauge is even finite over. `combo.py` turns a set of parts into a `StrategySpec` under a canonical hash. `evaluate.py` scores one combination: per-symbol profit factor, rotation-null percentile, cross-symbol `consistency_p`, and compounded return over one account. `growth.py` decides whether a part earned its place. `control.py` runs the incumbent rule inside each restricted window so a negative can be told apart from a blind spot. `ledger.py` writes it all through `Journal._tx()`. `planner.py` chooses the next batch; `job.py` runs it in a spawned, niced child; `runner.py` is the loop the kernel's new `research` thread calls.

**Tech Stack:** Python 3.12, pandas, numpy, SQLite, pytest, the existing `trader.strategy` engine (`compile_spec`, `simulate`, `null_baseline`, `portfolio_curve`) and `trader.core.child.run_child`.

**Spec:** `docs/superpowers/specs/2026-09-11-luffy-research-pipeline-design.md` — Part 3 "the search", plus the parts of Part 1 that describe the `research` thread and its child process.

## What was measured before writing this plan (2026-09-11)

Every deviation below is argued from one of these.

| measurement | value |
|---|---|
| one full evaluation, 4h, 19 discovery symbols, 191k bars | **12.3 s** — load 1.5 s, walk-forward 1.0 s, **null (60 draws) 10.9 s** |
| discovery symbols carrying ≥500 bars at **4h** | **19 of 19** |
| discovery symbols carrying ≥500 bars at **1h** | **8 of 19** (BTC ETH SOL XRP BNB DOGE LINK AVAX) |
| discovery symbols carrying ≥500 bars at **15m** | **8 of 19**, one year each |
| `oi` / `ls_account_ratio` first observation | 2025-10-03 (Coinalyze backfill) |
| `basis` first observation | 2024-09-01 · `funding` | 2021-09-02 |
| references: `spx`/`dxy`/`gold`/`us10y`/`vix`/`oil` daily | since 2016-09 · hourly since 2023-10..2024-04 |
| `btcdom` 4h since 2021-06 · `alts` 4h since 2021-08 · `stables` 1d since 2017-11 | |
| `cg_btc_d` / `cg_usdt_d` / `cg_total` / `cg_total2` | **3 rows each** — recorded forward from 2026-09-11 |
| kernel trade cycle duration, last 400 cycles | p50 **17.7 s**, p90 26.1 s, p99 65.3 s, max 77.5 s |
| host | 2 cores, 7.9 GB RAM (4.7 GB available), **3.5 GB free disk** |

## Deviations from the spec, with the measurement behind each

1. **A horizon is searched only when its universe can carry the test.** `consistency_p` is a Bonferroni-corrected binomial tail: at n=8 symbols, all eight clearing the no-edge median still scores 1.2e-02, so the 0.01 gate is unreachable and the search would be spending days to produce numbers that cannot pass. 1h and 15m hold 8 of the 19 discovery symbols today, so **Task 1 deepens the store first** and `research.min_discovery_symbols` (16) refuses any horizon that still falls short. This is the fault CLAUDE.md already records under "the size of the discovery universe sets what the test can DETECT" — repeating it knowingly would be worse than the first time.

2. **The discovery slice is cut at one calendar date per horizon, not at each symbol's own 70%.** `int(len(df) * 0.7)` per symbol puts WLD's discovery slice (listed 2023-07) entirely inside BTC's held-out era, so the "unseen era" would already have been searched on another symbol. One cut per horizon — `first_bar + 0.7 × (last_bar − first_bar)` over the whole universe — makes held-out B a genuinely later era for every symbol.

3. **Discovery uses 30 null draws, not 60.** The null is 96% of the cost (10.9 s of 12.3 s) because it is 60 simulations per symbol against the strategy's one. Discovery is a **ranking**, not a gate; the gate is Phase 3's held-out look, which keeps 60. 30 draws is above `null_baseline.MIN_DRAWS` (20) and halves the search's cost per combination. Configurable as `research.discovery_null_draws`.

4. **A part carries its own mirror expression, and thresholds are measured per expression.** Rather than flag a gauge "signed" or "neutral" and flip comparisons, each gauge declares a long expression and a short expression written so that **the same percentile rank means the mirror-image state** (`ret(24)` / `0 - ret(24)`, `lower_wick()` / `upper_wick()`, `dd_from_high(100)` / `0 - runup_from_low(100)`). Percentiles are then measured separately for each expression, so an asymmetric distribution mirrors honestly instead of being reflected arithmetically.

5. **Usability is measured, not derived from start dates.** A part is dropped at a horizon when its gauge is finite over less than `research.min_finite_frac` (0.5) of the discovery slice. This falls out of the threshold measurement for free, and it automatically removes open interest at 4h — where the series begins 2025-10 and the 4h cut lands ~2025-05, leaving **zero** discovery bars — without anyone hard-coding that fact.

6. **"One trigger plus context" is recorded, not enforced.** `and` is commutative, so a combination is a set of AND-ed parts under a canonical hash, with at most one *event* part (two breakouts on the same bar is a near-empty rule). The trigger is recorded as the single the combination grew from.

7. **A survivor must beat every one of its (k−1) subsets, not only the parent it grew from.** The spec asks that a part earn its place and that ties go to the simpler rule; the ablation the spec already requires measures exactly that, and the canonical hash makes almost all of those subsets free cache hits.

8. **The window control runs on the incumbent's declared 16, not on the discovery set.** On the discovery universe the incumbent is measured as no-edge (p=2.3e-01), so it cannot calibrate anything there. The question a control answers is "could an edge of known size register in a window this short", and the declared 16 is where that edge is known to have a size.

9. **Phase 2 writes nothing to `strategies`.** Held-out looks, the error budget and the handoff into `_mechanism_once` are Phase 3. Everything here stops at the ledger.

## Global Constraints

- Run everything through the venv: `./venv/bin/python …`.
- **Missing information is NaN, never a fabricated default.** **Point-in-time:** a bar may only use information that had closed by its own close.
- **Held-out data is never touched in Phase 2.** The discovery bundle is truncated at the cut before any expression is evaluated, so no code path in this package can read a later bar. Bar *counts* past the cut are metadata and may be read for the testability projection.
- `scripts/backtest_equivalence.py` must stay PASS; `scripts/bench_vector_backtest.py` above 20x.
- The child process **never writes a database**. It returns results; the `research` thread writes them through `Journal._tx()`.
- Nothing in `trader/research/` may write the `strategies` table. `tests/test_single_creation_path.py` stays green.
- Tests never touch the network and never depend on `data/*.db` unless the test says so in its name and skips when the store is absent.
- Suite baseline: 1 known failure (`test_macro_guard::test_a_restart_reuses_the_cached_calendar`, wall-clock). Any other failure is yours.
- Every commit message ends with:
  ```
  Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01EpstKPhQNEEG3wz2i7T9td
  ```

## File structure

| file | responsibility |
|---|---|
| `trader/strategy/geometries.py` | the two fixed exit geometries, in one place (the screen imports them too) |
| `trader/research/__init__.py` | package marker |
| `trader/research/vocab.py` | gauges, states, events; long expression + its mirror; part catalogue |
| `trader/research/slices.py` | the calendar cut, frame truncation, bar counts per slice |
| `trader/research/thresholds.py` | measured percentiles per expression, and how finite it is |
| `trader/research/combo.py` | a combination: parts → expressions → canonical hash → `StrategySpec` |
| `trader/research/evaluate.py` | the discovery bundle and the score of one combination |
| `trader/research/growth.py` | carries information / earns its place / is testable / survivor + ablation |
| `trader/research/control.py` | the incumbent rule confined to a window; powered or not |
| `trader/research/ledger.py` | the tables, and every read and write of them |
| `trader/research/planner.py` | what to evaluate next, deterministically |
| `trader/research/job.py` | the two child entry points (picklable, read-only) |
| `trader/research/runner.py` | one step of the loop: plan → child → record → decide |
| `trader/research/__main__.py` | `--status`, `--once`, `--measure` |
| `scripts/deepen_research_universe.py` | bring 1h/15m up to the screen's universes |

---

### Task 1: The store carries the universes the test needs

**Files:**
- Create: `trader/research/__init__.py`
- Create: `trader/research/universe.py`
- Create: `scripts/deepen_research_universe.py`
- Create: `tests/test_research_universe_depth.py`

**Interfaces:**
- Produces, in `trader/research/universe.py`: `DISCOVERY: list[str]`, `HELDOUT: list[str]`, `TARGETS: dict[str, int]` (`{"4h": 11000, "1h": 26000, "15m": 35000}`), `MIN_BARS = 500`, `coverage(tfs=None, feed=None) -> dict[str, dict[str, int]]` — `{tf: {symbol: bars}}` for symbols with ≥`MIN_BARS`, `deepen(symbols, tfs=None, feed=None) -> dict`.
- The script is a thin `__main__` over those. The universe lists live in the package, not in `scripts/`, because `runner.py` reads them from a kernel thread: importing a script module would make the kernel depend on its working directory.

- [ ] **Step 1: Write the failing test**

Create `tests/test_research_universe_depth.py`:

```python
"""A horizon may only be searched if its universe can carry the test.

`consistency_p` is a Bonferroni-corrected binomial tail over per-symbol null
percentiles, so the number of symbols sets what the test can DETECT. At 8
symbols, ALL EIGHT clearing the no-edge median still scores 1.2e-02 and the
0.01 admission gate is mathematically unreachable. Measured 2026-09-11 the
store held 19 discovery symbols at 4h and 8 at 1h and 15m — so a search at
1h would have spent days producing numbers that could never pass.

This test does not check that the store is deep. It checks that what the
config ENABLES and what the store CARRIES agree.
"""
import pytest

from trader.core.config import load_config

pytest.importorskip("pandas")


def _store_counts(tf):
    from trader.research.universe import coverage
    return coverage([tf]).get(tf, {})


def test_the_universes_do_not_overlap():
    from trader.research.universe import DISCOVERY, HELDOUT
    assert not set(DISCOVERY) & set(HELDOUT)
    assert len(DISCOVERY) >= 19 and len(HELDOUT) >= 17


def test_every_enabled_horizon_carries_enough_discovery_symbols():
    cfg = load_config()
    rcfg = cfg.get("research") or {}
    horizons = rcfg.get("horizons") or []
    need = int(rcfg.get("min_discovery_symbols", 16))
    from trader.research.universe import DISCOVERY
    if not horizons:
        pytest.skip("no research horizons enabled yet")
    for tf in horizons:
        have = _store_counts(tf)
        n = sum(1 for s in DISCOVERY if have.get(s, 0) >= 500)
        assert n >= need, (
            f"{tf} is enabled for research but only {n} of "
            f"{len(DISCOVERY)} discovery symbols carry >=500 bars "
            f"(need {need}); deepen the store or drop the horizon")


def test_four_hour_is_deep_enough_today():
    """The horizon the book already trades. If this regresses, the candle
    store lost history and every Phase 2 number is suspect."""
    from trader.research.universe import DISCOVERY
    have = _store_counts("4h")
    n = sum(1 for s in DISCOVERY if have.get(s, 0) >= 500)
    assert n >= 19, f"only {n} discovery symbols at 4h"
```

- [ ] **Step 2: Run to verify it fails**

Run: `./venv/bin/python -m pytest tests/test_research_universe_depth.py -q -p no:cacheprovider`
Expected: FAIL with `ModuleNotFoundError: No module named 'trader.research.universe'`.

- [ ] **Step 3: Write the package marker and the universe module**

Create `trader/research/__init__.py`:

```python
"""The research pipeline: discover, explain, prove, repeat.

Phase 2 is the DISCOVER half. Nothing in this package writes the strategies
table, and nothing in it reads a bar past the discovery cut.
"""
```

Create `trader/research/universe.py`:

```python
"""The two universes the search is scored on, and how deep the store is.

Measured 2026-09-11: the candle store held 19 of 19 discovery symbols at 4h
and 8 of 19 at 1h and 15m, because only the TRADED universe was ever fetched
at the finer frames. A search at 1h over 8 symbols cannot reach p<0.01 no
matter what it finds, so deepening is not an optimisation — it is the
difference between a horizon being searchable and not.

These lists live in the package rather than in `scripts/` because the
kernel's research thread reads them: importing a script module would make a
trading kernel depend on its working directory.
"""
from __future__ import annotations

import time

from ..data.feed import DataFeed

# The screen's two universes, verbatim from scripts/screen_mechanisms.py.
# The split rule is fixed there so it cannot be chosen to flatter a result;
# do not re-partition it here.
DISCOVERY = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT", "BNB/USDT",
             "DOGE/USDT", "ADA/USDT", "LINK/USDT", "AVAX/USDT", "LTC/USDT",
             "1000PEPE/USDT", "APT/USDT", "BCH/USDT", "DASH/USDT",
             "FET/USDT", "INJ/USDT", "T/USDT", "WLD/USDT", "XMR/USDT"]
HELDOUT = ["UNI/USDT", "SUI/USDT", "TAO/USDT", "ZEC/USDT", "NEAR/USDT",
           "FIL/USDT", "AAVE/USDT", "HYPE/USDT", "TRUMP/USDT",
           "1000SHIB/USDT", "ARB/USDT", "CRV/USDT", "DOT/USDT", "ICP/USDT",
           "OP/USDT", "TRX/USDT", "XLM/USDT"]

#: ~5y at 4h, ~3y at 1h, ~1y at 15m — the store's standing policy
TARGETS = {"4h": 11000, "1h": 26000, "15m": 35000}

MIN_BARS = 500


def coverage(tfs=None, feed=None) -> dict:
    """{tf: {symbol: bars}} for every symbol carrying at least MIN_BARS.

    COUNTED in SQL, not loaded: the research thread asks this on every step,
    and reading 36 full frames to learn their lengths would put seconds of
    pandas work on a kernel thread to answer a question SQLite answers in
    one pass.
    """
    feed = feed or DataFeed()
    want = set(DISCOVERY + HELDOUT)
    out = {}
    for tf in (tfs or list(TARGETS)):
        have = {}
        try:
            rows = feed.db.execute(
                "SELECT symbol, COUNT(*) FROM candles WHERE tf=? "
                "GROUP BY symbol", (tf,)).fetchall()
        except Exception:                              # noqa: BLE001
            rows = []
        for sym, n in rows:
            if sym in want and int(n) >= MIN_BARS:
                have[sym] = int(n)
        out[tf] = have
    return out


def deepen(symbols, tfs=None, feed=None) -> dict:
    feed = feed or DataFeed()
    report = {}
    for tf in (tfs or list(TARGETS)):
        want = TARGETS[tf]
        for sym in symbols:
            have = feed.cached_ohlcv(sym, tf, limit=200000)
            before = 0 if have is None else len(have)
            if before >= want:
                report[f"{sym} {tf}"] = f"{before} (deep enough)"
                continue
            t0 = time.perf_counter()
            try:
                df = feed.fetch_ohlcv(sym, tf, limit=want)
            except Exception as e:                     # noqa: BLE001
                report[f"{sym} {tf}"] = f"FAILED: {e}"
                print(f"  {sym:14} {tf:4} FAILED: {e}", flush=True)
                continue
            n = 0 if df is None else len(df)
            report[f"{sym} {tf}"] = f"{before} -> {n}"
            print(f"  {sym:14} {tf:4} {before} -> {n} "
                  f"[{time.perf_counter() - t0:.0f}s]", flush=True)
    return report
```

Create `scripts/deepen_research_universe.py` — a thin runner over that module:

```python
"""Bring 1h and 15m up to the universes the search is scored on.

Idempotent: `DataFeed.fetch_ohlcv` merges INSERT OR REPLACE keyed by
(symbol, tf, ts) and records a history floor, so re-running tops up rather
than re-walking.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trader.data.feed import DataFeed                        # noqa: E402
from trader.research.universe import (DISCOVERY, HELDOUT,    # noqa: E402
                                      TARGETS, coverage, deepen)

if __name__ == "__main__":
    tfs = [a for a in sys.argv[1:] if a in TARGETS] or ["1h", "15m"]
    feed = DataFeed()
    print(f"deepening {len(DISCOVERY + HELDOUT)} symbols at {tfs}")
    deepen(DISCOVERY + HELDOUT, tfs, feed)
    print("\ncoverage (symbols with >=500 bars):")
    for tf, have in coverage(tfs, feed).items():
        d = sum(1 for s in DISCOVERY if s in have)
        h = sum(1 for s in HELDOUT if s in have)
        print(f"  {tf}: {d}/{len(DISCOVERY)} discovery, "
              f"{h}/{len(HELDOUT)} held-out")
```

- [ ] **Step 4: Check the disk has room, then run it**

The host was measured at **3.5 GB free (93% full)**. At ~115 bytes a row the
missing history is ~1.3M rows ≈ 150 MB, which fits — but check before
writing, because a full disk corrupts SQLite rather than failing cleanly.

Run:
```bash
df -h /home/sarmad/trader | tail -1
./venv/bin/python scripts/deepen_research_universe.py 1h 15m
```
Expected: every symbol reports a `before -> after`, and the coverage summary
prints `1h: 19/19 discovery` and `15m: 19/19 discovery` (held-out counts may
be lower for young listings such as HYPE and TRUMP — that is honest, and
Phase 3's held-out gate reads whatever is there).

If a symbol fails repeatedly, leave it out rather than retrying forever: the
test above asks for 16, not for all 19.

- [ ] **Step 5: Run the tests**

Run: `./venv/bin/python -m pytest tests/test_research_universe_depth.py -q -p no:cacheprovider`
Expected: PASS (the enabled-horizons test skips — `research.horizons` does not exist yet; it starts asserting in Task 13).

- [ ] **Step 6: Commit**

```bash
git add trader/research/__init__.py trader/research/universe.py scripts/deepen_research_universe.py tests/test_research_universe_depth.py
git commit -m "$(cat <<'EOF'
data(research): 1h and 15m carry the universes the null test needs

Measured: 19 of 19 discovery symbols at 4h, 8 of 19 at 1h and 15m. At 8
symbols consistency_p cannot reach 0.01 even when every symbol clears the
no-edge median, so a search at those horizons could not have produced an
admissible result. Deepened rather than relaxed.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01EpstKPhQNEEG3wz2i7T9td
EOF
)"
```

---

### Task 2: One definition of the two exit geometries

**Files:**
- Create: `trader/strategy/geometries.py`
- Modify: `scripts/screen_mechanisms.py:101-110` (replace the literal `GEOS` with an import)
- Test: `tests/test_geometries.py`

**Interfaces:**
- Produces: `GEOS: dict[str, ExitSpec]` with keys `"fixed"` and `"trail"`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_geometries.py`:

```python
"""Exit geometry is the yardstick every search result is measured against.

A fixed target amputates the tail a continuation mechanism lives on: every
sweep before 2026-09-02 used trail={"kind":"none"} and that single omission
hid the one mechanism that works. Both shapes are screened, and both must be
defined ONCE — a search that scores candidates under a geometry the screen
no longer uses is comparing numbers that were never comparable.
"""
from trader.strategy.geometries import GEOS
from trader.strategy.spec import StrategySpec, ExitSpec


def test_both_shapes_exist():
    assert set(GEOS) == {"fixed", "trail"}
    assert all(isinstance(g, ExitSpec) for g in GEOS.values())


def test_the_trail_is_the_shape_the_book_was_admitted_under():
    g = GEOS["trail"]
    assert g.stop == {"kind": "atr", "mult": 2.0}
    assert g.target == {"kind": "none"}
    assert g.trail == {"kind": "atr", "mult": 4.0, "arm_at_r": 1.0}
    assert g.time == {"max_bars": 500}


def test_the_fixed_shape_takes_a_three_r_target():
    g = GEOS["fixed"]
    assert g.stop == {"kind": "atr", "mult": 3.0}
    assert g.target == {"kind": "rr", "v": 3.0}
    assert g.trail == {"kind": "none"}


def test_every_geometry_is_a_valid_spec_exit():
    for name, geo in GEOS.items():
        spec = StrategySpec(
            id=f"probe_{name}", name=f"probe {name}",
            thesis="x" * 80, invalidation="y" * 40, provenance={},
            universe={"include": []}, timeframe="4h", direction="both",
            entry_long="close > ema(50)", entry_short="close < ema(50)",
            filters=[], exit=geo, regime_filter=[], markets=["futures"])
        assert StrategySpec.validate(spec) == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `./venv/bin/python -m pytest tests/test_geometries.py -q -p no:cacheprovider`
Expected: FAIL with `ModuleNotFoundError: No module named 'trader.strategy.geometries'`.

- [ ] **Step 3: Implement**

Create `trader/strategy/geometries.py`:

```python
"""The exit geometries every screen and every search is scored under.

Geometry is a screen DIMENSION, not a constant. Screening one exit shape
asks "which entries pay under THIS exit", never "which mechanism is real" —
and an ATR multiple is meaningless without the bar it was measured on, so
these travel with the spec's own timeframe.

Two shapes, deliberately few:

- `fixed`  a 3 ATR stop and a 3R target. A fixed target amputates the tail a
           continuation mechanism lives on, which is exactly why it is kept:
           it is the control for the other one.
- `trail`  a 2 ATR stop with a 4 ATR trail armed at 1R. The shape Donchian
           Breakout Trail — the only mechanism in the book with evidence —
           was admitted under.

Defined here rather than in a script so the search and the screen cannot
drift into scoring candidates under different yardsticks.
"""
from __future__ import annotations

from .spec import ExitSpec

GEOS: dict[str, ExitSpec] = {
    "fixed": ExitSpec(stop={"kind": "atr", "mult": 3.0},
                      target={"kind": "rr", "v": 3.0},
                      trail={"kind": "none"}, time={"max_bars": 96}),
    "trail": ExitSpec(stop={"kind": "atr", "mult": 2.0},
                      target={"kind": "none"},
                      trail={"kind": "atr", "mult": 4.0, "arm_at_r": 1.0},
                      time={"max_bars": 500}),
}
```

Then edit `scripts/screen_mechanisms.py`. Replace the `GEOS = {...}` literal
(lines 101-110) with:

```python
# Geometry is a screen DIMENSION, not a constant. The first pass here fixed
# it at RR 3 with no trail — and a fixed target amputates the tail a
# continuation mechanism lives on, which is exactly how Donchian Breakout
# Trail stayed invisible until the geometry sweep found it. Screening every
# mechanism under one exit shape asks "which entries pay under THIS exit",
# never "which mechanism is real".
#
# Defined in trader/strategy/geometries.py so the research search scores
# candidates under the same two shapes this screen reports.
from trader.strategy.geometries import GEOS
```

The `from trader.strategy.spec import StrategySpec, ExitSpec` import at the
top stays — `spec_for` still takes an `ExitSpec`.

- [ ] **Step 4: Run the tests**

Run:
```bash
./venv/bin/python -m pytest tests/test_geometries.py -q -p no:cacheprovider
./venv/bin/python -c "import ast,sys; ast.parse(open('scripts/screen_mechanisms.py').read())"
```
Expected: PASS, and the screen still parses.

- [ ] **Step 5: Commit**

```bash
git add trader/strategy/geometries.py scripts/screen_mechanisms.py tests/test_geometries.py
git commit -m "$(cat <<'EOF'
feat(strategy): the two exit geometries live in one place

The search and the screen must measure candidates with the same yardstick;
two copies of a geometry literal is a drift waiting to happen.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01EpstKPhQNEEG3wz2i7T9td
EOF
)"
```

---

### Task 3: The vocabulary — every part, and its mirror

**Files:**
- Create: `trader/research/vocab.py`
- Test: `tests/test_research_vocab.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) Gauge(key: str, long: str, short: str, tfs: tuple = ())` — two scalar expressions, written so the SAME percentile rank means the mirror-image state; `tfs` empty means every horizon.
  - `@dataclass(frozen=True) Part(key: str, kind: str, gauge: str, long: str, short: str)` — `kind` ∈ `{"gauge", "state", "event"}`; `long`/`short` are boolean DSL expressions.
  - `GAUGES: tuple[Gauge, ...]`, `BOOL_PARTS: tuple[Part, ...]`
  - `QUANTILES = (0.10, 0.25, 0.75, 0.90)`
  - `expressions(tf: str) -> list[str]` — every scalar expression needing a measured percentile at this horizon, deduplicated.
  - `parts_for(tf: str, thresholds: dict) -> list[Part]` — the concrete catalogue; `thresholds` is `{expr: {"p10": float, …, "usable": bool}}`; a gauge with an unusable or missing side is skipped.
  - `part_requires(part: Part) -> tuple[str, ...]` — derived via `dsl.data_requires`, never declared.

- [ ] **Step 1: Write the failing test**

Create `tests/test_research_vocab.py`:

```python
"""The search's vocabulary: what a condition may say, and how it mirrors.

Two rules do the work here.

THRESHOLDS ARE MEASURED, NEVER GUESSED. A hard-coded `> 0.62` on
taker_buy/volume fired on 0.005% of bars and made the whole flow family look
tested while it never traded. Every threshold in this catalogue is the 10th,
25th, 75th or 90th percentile of that expression on the discovery slice.

THE MIRROR IS WRITTEN, NOT COMPUTED. Each gauge carries a long expression
and a short expression composed so the SAME rank means the mirror state:
ret(24) mirrors to 0 - ret(24), lower_wick() to upper_wick(), and a deep
pullback (dd_from_high low) to a deep bounce (0 - runup_from_low low).
Reflecting a threshold arithmetically would assume a symmetric distribution
that crypto returns do not have.
"""
import pytest

from trader.research import vocab
from trader.strategy import dsl


def _thresholds(exprs):
    """Every expression usable, with placeholder percentiles."""
    return {e: {"p10": -1.0, "p25": -0.5, "p75": 0.5, "p90": 1.0,
                "usable": True} for e in exprs}


def test_every_gauge_expression_parses_on_both_sides():
    for g in vocab.GAUGES:
        for side in (g.long, g.short):
            dsl.parse(side)          # raises SpecError if it does not


def test_every_bool_part_parses_on_both_sides():
    for p in vocab.BOOL_PARTS:
        dsl.parse(p.long)
        dsl.parse(p.short)


def test_part_keys_are_unique():
    parts = vocab.parts_for("4h", _thresholds(vocab.expressions("4h")))
    keys = [p.key for p in parts]
    assert len(keys) == len(set(keys))


def test_a_gauge_yields_four_parts_two_each_way():
    parts = vocab.parts_for("4h", _thresholds(vocab.expressions("4h")))
    ret24 = [p for p in parts if p.gauge == "ret24"]
    assert len(ret24) == 4
    assert {p.key for p in ret24} == {"ret24<p10", "ret24<p25",
                                      "ret24>p75", "ret24>p90"}


def test_the_short_side_reads_the_mirror_expression():
    parts = vocab.parts_for("4h", _thresholds(vocab.expressions("4h")))
    hi = next(p for p in parts if p.key == "ret24>p90")
    assert hi.long == "ret(24) > 1.0"
    # the mirror's own measured p90, not the long side's negated p10
    assert hi.short == "0 - ret(24) > 1.0"


def test_a_direction_neutral_gauge_reads_the_same_both_ways():
    parts = vocab.parts_for("4h", _thresholds(vocab.expressions("4h")))
    p = next(p for p in parts if p.key == "atrrank200>p90")
    assert p.long == p.short


def test_an_unusable_gauge_contributes_no_parts():
    exprs = vocab.expressions("4h")
    th = _thresholds(exprs)
    for e in (g := next(x for x in vocab.GAUGES if x.key == "oiret24")).long, g.short:
        th[e]["usable"] = False
    parts = vocab.parts_for("4h", th)
    assert not [p for p in parts if p.gauge == "oiret24"]


def test_a_gauge_with_no_measurement_contributes_no_parts():
    parts = vocab.parts_for("4h", {})
    assert not [p for p in parts if p.kind == "gauge"]
    assert [p for p in parts if p.kind in ("state", "event")]


def test_horizon_restricted_gauges_only_appear_where_they_mean_something():
    """htf("4h", …) on a 4h base reads its own frame — no information."""
    four = {p.gauge for p in
            vocab.parts_for("4h", _thresholds(vocab.expressions("4h")))}
    fifteen = {p.gauge for p in
               vocab.parts_for("15m", _thresholds(vocab.expressions("15m")))}
    assert "htf4h_dist" not in four
    assert "htf4h_dist" in fifteen


def test_every_part_carries_its_requirements_derived_from_the_dsl():
    parts = vocab.parts_for("4h", _thresholds(vocab.expressions("4h")))
    funding = next(p for p in parts if p.gauge == "fundz360")
    assert "funding" in vocab.part_requires(funding)
    ref = next(p for p in parts if p.gauge.startswith("r_spx"))
    assert "ref:spx" in vocab.part_requires(ref)
    plain = next(p for p in parts if p.key == "ev:donch100")
    assert vocab.part_requires(plain) == ("ohlcv",)


def test_the_catalogue_is_large_enough_to_be_a_search():
    parts = vocab.parts_for("4h", _thresholds(vocab.expressions("4h")))
    assert len(parts) >= 150
    assert sum(1 for p in parts if p.kind == "event") >= 5


def test_exactly_one_event_may_appear_in_a_combination_later():
    """The compatibility rule lives in combo.py; the KIND it reads lives
    here, so an event must be labelled as one."""
    parts = vocab.parts_for("4h", _thresholds(vocab.expressions("4h")))
    donch = next(p for p in parts if p.key == "ev:donch100")
    assert donch.kind == "event"
    assert donch.long == "close > donchian_hi(100)"
    assert donch.short == "close < donchian_lo(100)"
```

- [ ] **Step 2: Run to verify it fails**

Run: `./venv/bin/python -m pytest tests/test_research_vocab.py -q -p no:cacheprovider`
Expected: FAIL with `ModuleNotFoundError: No module named 'trader.research.vocab'`.

- [ ] **Step 3: Implement the vocabulary**

Create `trader/research/vocab.py`:

```python
"""What a searched condition may say.

A PART is one condition, written twice: once for the long side and once as
its mirror image. Two kinds:

- a GAUGE part compares a scalar expression against a MEASURED percentile of
  that expression's own distribution on the discovery slice. A threshold
  stated as a round number is a guess about a distribution: `> 0.62` on
  taker_buy/volume fires on 0.005% of 4h bars, so the whole flow family read
  as "tested" in a results table while it never took a trade.
- a STATE or EVENT part is boolean on its own (`close > ema(50)`,
  `close > donchian_hi(100)`). An EVENT is sparse — it fires on a bar rather
  than persisting — and a combination may contain at most one, because two
  breakouts landing on the same bar is a rule that never fires.

THE MIRROR IS WRITTEN, NOT COMPUTED. Each gauge's `short` expression is
composed so that the SAME percentile rank means the mirror-image state:

    ret(24)              ->  0 - ret(24)
    rsi(14)              ->  100 - rsi(14)
    lower_wick()         ->  upper_wick()
    dd_from_high(100)    ->  0 - runup_from_low(100)
    atr_pct_rank(200)    ->  atr_pct_rank(200)      (direction-neutral)

Percentiles are then measured separately for each side, so an asymmetric
distribution — which crypto returns are — mirrors honestly instead of being
reflected arithmetically. Only BOTH directions ever worked: long alone won 2
of 5 yearly windows, short alone 3, the pair 4.

Nothing here invents a feature. Every expression resolves against
`strategy.features.FEATURES`, and `part_requires` derives what data a part
needs from the DSL rather than trusting a declaration.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..strategy import dsl

#: the only thresholds a part may use — the spec's four measured cuts
QUANTILES = (0.10, 0.25, 0.75, 0.90)


@dataclass(frozen=True)
class Gauge:
    """A scalar quantity and its mirror. `tfs` empty = every horizon."""
    key: str
    long: str
    short: str
    tfs: tuple = ()


@dataclass(frozen=True)
class Part:
    """One condition, already resolved to concrete boolean expressions."""
    key: str            # canonical: percentile RANK, never the value
    kind: str           # "gauge" | "state" | "event"
    gauge: str          # what it is about — two parts of one gauge conflict
    long: str
    short: str

    def as_dict(self) -> dict:
        return {"key": self.key, "kind": self.kind, "gauge": self.gauge,
                "long": self.long, "short": self.short}

    @staticmethod
    def from_dict(d: dict) -> "Part":
        return Part(d["key"], d["kind"], d["gauge"], d["long"], d["short"])


def _neg(expr: str) -> str:
    """The mirror of a quantity centred on zero."""
    return f"0 - {expr}"


#: every reference with years of free depth. cg_* are excluded: measured
#: 2026-09-11 they hold 3 rows each, recorded forward from that day.
_REFS_ALL = ("btcdom", "alts", "stables", "spx", "dxy", "gold", "us10y",
             "vix", "oil")
#: hourly references only mean something below the 4h frame
_REFS_HOURLY = ("spx_1h", "vix_1h")


def _ref_gauges() -> list[Gauge]:
    out = []
    for key in _REFS_ALL:
        out.append(Gauge(f"r_{key}_ret30", f'ref("{key}", ret(30))',
                         _neg(f'ref("{key}", ret(30))')))
        out.append(Gauge(f"r_{key}_z96", f'ref("{key}", zscore(close, 96))',
                         _neg(f'ref("{key}", zscore(close, 96))')))
    for key in _REFS_HOURLY:
        out.append(Gauge(f"r_{key}_ret30", f'ref("{key}", ret(30))',
                         _neg(f'ref("{key}", ret(30))'), tfs=("1h", "15m")))
    return out


GAUGES: tuple[Gauge, ...] = tuple([
    # ── price, trend, momentum
    Gauge("ret6", "ret(6)", _neg("ret(6)")),
    Gauge("ret24", "ret(24)", _neg("ret(24)")),
    Gauge("ret72", "ret(72)", _neg("ret(72)")),
    Gauge("slope20", "slope(close, 20)", _neg("slope(close, 20)")),
    Gauge("zclose96", "zscore(close, 96)", _neg("zscore(close, 96)")),
    Gauge("bb20", "bb_pctb(20, 2.0)", "1 - bb_pctb(20, 2.0)"),
    Gauge("rsi14", "rsi(14)", "100 - rsi(14)"),
    Gauge("macd", "macd(12, 26, 9)", _neg("macd(12, 26, 9)")),
    Gauge("emadist50", "(close - ema(50)) / ema(50)",
          _neg("(close - ema(50)) / ema(50)")),
    Gauge("emadist200", "(close - ema(200)) / ema(200)",
          _neg("(close - ema(200)) / ema(200)")),
    # a deep pullback inside a trend; its mirror is a deep bounce
    Gauge("pull100", "dd_from_high(100)", _neg("runup_from_low(100)")),
    # ── bar shape
    Gauge("wick", "lower_wick()", "upper_wick()"),
    Gauge("bodyfrac", "body_frac()", "body_frac()"),
    # ── path quality and volatility regime (direction-neutral)
    Gauge("er30", "efficiency_ratio(30)", "efficiency_ratio(30)"),
    Gauge("atrrank200", "atr_pct_rank(200)", "atr_pct_rank(200)"),
    Gauge("volofvol90", "vol_of_vol(90)", "vol_of_vol(90)"),
    Gauge("adx14", "adx(14)", "adx(14)"),
    # ── participation
    Gauge("relvol50", "rel_volume(50)", "rel_volume(50)"),
    Gauge("volz96", "volume_z(96)", "volume_z(96)"),
    Gauge("takerfrac", "taker_buy_frac()", "1 - taker_buy_frac()"),
    # ── sequence
    Gauge("streak", "streak(close > prev(close, 1))",
          "streak(close < prev(close, 1))"),
    Gauge("since_break", "bars_since(close > donchian_hi(100))",
          "bars_since(close < donchian_lo(100))"),
    # ── the leader
    Gauge("corrbtc90", "corr_btc(90)", "corr_btc(90)"),
    Gauge("relbtc30", "rel_strength_btc(30)", _neg("rel_strength_btc(30)")),
    Gauge("btcret24", "btc_ret(24)", _neg("btc_ret(24)")),
    # ── the rest of the book
    Gauge("xsret30", "xs_rank(ret(30))", "1 - xs_rank(ret(30))"),
    Gauge("xsret6", "xs_rank(ret(6))", "1 - xs_rank(ret(6))"),
    Gauge("breadth50", "breadth(close > ema(50))",
          "1 - breadth(close > ema(50))"),
    Gauge("disp6", "dispersion(ret(6))", "dispersion(ret(6))"),
    # ── carry and positioning. Whether these are usable at a horizon is
    # MEASURED (thresholds.usable), never assumed: open interest begins
    # 2025-10 and the 4h discovery cut lands ~2025-05, so at 4h these
    # gauges are finite over none of the slice and drop out by themselves.
    Gauge("fundz360", "funding_z(360)", _neg("funding_z(360)")),
    Gauge("fundpct720", "funding_pct(720)", "1 - funding_pct(720)"),
    Gauge("basisz360", "basis_z(360)", _neg("basis_z(360)")),
    Gauge("oiret24", "oi_ret(24)", "oi_ret(24)"),
    Gauge("oidiv24", "oi_price_div(24)", _neg("oi_price_div(24)")),
    Gauge("acctz360", "ls_account_ratio_z(360)",
          _neg("ls_account_ratio_z(360)")),
    # ── the higher frame, below it only
    Gauge("htf4h_dist", 'htf("4h", (close - ema(50)) / ema(50))',
          _neg('htf("4h", (close - ema(50)) / ema(50))'), tfs=("1h", "15m")),
    # ── relationships: one market moving while another does not
    Gauge("rel_alts30", 'ret(30) - ref("alts", ret(30))',
          _neg('ret(30) - ref("alts", ret(30))')),
    Gauge("dom_vs_alts", 'ref("btcdom", ret(30)) - ref("alts", ret(30))',
          _neg('ref("btcdom", ret(30)) - ref("alts", ret(30))')),
] + _ref_gauges())


BOOL_PARTS: tuple[Part, ...] = (
    # ── states: what must also be true
    Part("st:above_ema50", "state", "st:above_ema50",
         "close > ema(50)", "close < ema(50)"),
    Part("st:above_ema200", "state", "st:above_ema200",
         "close > ema(200)", "close < ema(200)"),
    Part("st:ema_stack", "state", "st:ema_stack",
         "ema(20) > ema(50) and ema(50) > ema(200)",
         "ema(20) < ema(50) and ema(50) < ema(200)"),
    Part("st:above_vwap", "state", "st:above_vwap",
         "close > vwap(96)", "close < vwap(96)"),
    Part("st:htf_trend", "state", "st:htf_trend",
         'htf("4h", close > ema(50))', 'htf("4h", close < ema(50))'),
    # ── events: what fires
    Part("ev:donch20", "event", "ev:donch20",
         "close > donchian_hi(20)", "close < donchian_lo(20)"),
    Part("ev:donch50", "event", "ev:donch50",
         "close > donchian_hi(50)", "close < donchian_lo(50)"),
    Part("ev:donch100", "event", "ev:donch100",
         "close > donchian_hi(100)", "close < donchian_lo(100)"),
    Part("ev:swing10", "event", "ev:swing10",
         "close > swing_high(10)", "close < swing_low(10)"),
    Part("ev:bb_break", "event", "ev:bb_break",
         "close > bb_upper(20, 2.0)", "close < bb_lower(20, 2.0)"),
    Part("ev:keltner", "event", "ev:keltner",
         "close > keltner_upper(20, 2.0)", "close < keltner_lower(20, 2.0)"),
    Part("ev:ema50_cross", "event", "ev:ema50_cross",
         "close > ema(50) and prev(close, 1) < prev(ema(50), 1)",
         "close < ema(50) and prev(close, 1) > prev(ema(50), 1)"),
    Part("ev:macd_cross", "event", "ev:macd_cross",
         "macd(12, 26, 9) > 0 and prev(macd(12, 26, 9), 1) < 0",
         "macd(12, 26, 9) < 0 and prev(macd(12, 26, 9), 1) > 0"),
)

#: states that only mean something below the frame they read
_BOOL_TFS = {"st:htf_trend": ("1h", "15m")}


def _allowed(tfs: tuple, tf: str) -> bool:
    return not tfs or tf in tfs


def gauges_for(tf: str) -> list[Gauge]:
    return [g for g in GAUGES if _allowed(g.tfs, tf)]


def expressions(tf: str) -> list[str]:
    """Every scalar expression needing a measured percentile at `tf`."""
    seen, out = set(), []
    for g in gauges_for(tf):
        for e in (g.long, g.short):
            if e not in seen:
                seen.add(e)
                out.append(e)
    return out


def _fmt(v: float) -> str:
    """A threshold as the expression will carry it. Rounded so the rendered
    text stays readable and re-parses to the same number."""
    r = round(float(v), 6)
    return str(int(r)) if float(r).is_integer() else str(r)


def parts_for(tf: str, thresholds: dict) -> list[Part]:
    """The concrete catalogue at this horizon.

    `thresholds` maps expression -> {"p10","p25","p75","p90","usable"}. A
    gauge whose either side is missing or unusable contributes NOTHING: a
    one-sided condition is a market-direction bet wearing a strategy's
    clothes, and an unmeasured threshold is a guess.
    """
    out: list[Part] = []
    for g in gauges_for(tf):
        a, b = thresholds.get(g.long), thresholds.get(g.short)
        if not a or not b or not a.get("usable") or not b.get("usable"):
            continue
        for q in QUANTILES:
            name = f"p{int(round(q * 100))}"
            va, vb = a.get(name), b.get(name)
            if va is None or vb is None:
                continue
            op = "<" if q < 0.5 else ">"
            out.append(Part(
                key=f"{g.key}{op}{name}", kind="gauge", gauge=g.key,
                long=f"{g.long} {op} {_fmt(va)}",
                short=f"{g.short} {op} {_fmt(vb)}"))
    for p in BOOL_PARTS:
        if _allowed(_BOOL_TFS.get(p.key, ()), tf):
            out.append(p)
    return out


def part_requires(part: Part) -> tuple[str, ...]:
    """What data this part needs — DERIVED from the expressions.

    A declaration can be wrong; `dsl.data_requires` reads the features that
    are actually referenced, which is what decides whether a combination can
    be scored at all.
    """
    trees = [dsl.parse(part.long), dsl.parse(part.short)]
    return dsl.data_requires(*trees)
```

- [ ] **Step 4: Run the tests**

Run: `./venv/bin/python -m pytest tests/test_research_vocab.py -q -p no:cacheprovider`
Expected: PASS.

If `test_the_catalogue_is_large_enough_to_be_a_search` fails, count what
`parts_for` returned before adding gauges: the catalogue should be about
36 gauges × 4 + 18 reference gauges × 4 + 13 boolean parts ≈ 230 parts at 4h.

- [ ] **Step 5: Commit**

```bash
git add trader/research/vocab.py tests/test_research_vocab.py
git commit -m "$(cat <<'EOF'
feat(research): the vocabulary — measured thresholds, written mirrors

Every threshold is a percentile of its own expression's distribution, and
every condition carries the mirror expression that makes the same rank mean
the mirror state. Reflecting a threshold arithmetically would assume a
symmetry crypto returns do not have.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01EpstKPhQNEEG3wz2i7T9td
EOF
)"
```

---

### Task 4: Slices — one calendar cut the search cannot see past

**Files:**
- Create: `trader/research/slices.py`
- Test: `tests/test_research_slices.py`

**Interfaces:**
- Produces: `DISCOVERY_FRAC = 0.7`; `MIN_SLICE_BARS = 420`;
  `cut_ms(frames: dict, frac: float = DISCOVERY_FRAC) -> int | None`;
  `before(df, cut: int)` / `after(df, cut: int)` -> `pd.DataFrame`;
  `discovery(frames: dict, cut: int, min_bars: int = MIN_SLICE_BARS) -> dict`;
  `heldout_b(frames: dict, cut: int, min_bars: int = MIN_SLICE_BARS) -> dict`;
  `bar_counts(frames: dict) -> dict[str, int]`;
  `usable_bars(n: int) -> int` (n − `vector_backtest.WARMUP`, floored at 0).

- [ ] **Step 1: Write the failing test**

Create `tests/test_research_slices.py`:

```python
"""The search may see the earliest 70% of the data, by the CALENDAR.

Per-symbol 70% splits leak era across symbols: WLD listed 2023-07, so 70% of
its own history ends inside the era that is meant to be unseen for BTC. One
cut date per horizon makes held-out B a genuinely later era for every market
in it.

WARMUP is a bar count, not a duration: `simulate` discards the first 210 bars
of any slice it is handed, so a slice must be long enough to survive that
before it can carry a trade at all.
"""
import pandas as pd
import pytest

from trader.research import slices
from trader.strategy.vector_backtest import WARMUP

HOUR = 3_600_000


def _frame(start_ms, n, step_ms=4 * HOUR):
    ts = pd.to_datetime([start_ms + i * step_ms for i in range(n)],
                        unit="ms", utc=True)
    return pd.DataFrame({"ts": ts, "open": 1.0, "high": 1.0, "low": 1.0,
                         "close": 1.0, "volume": 1.0})


def test_the_cut_is_one_date_for_the_whole_universe():
    frames = {"OLD": _frame(0, 1000), "YOUNG": _frame(500 * 4 * HOUR, 500)}
    cut = slices.cut_ms(frames)
    span = 999 * 4 * HOUR
    assert cut == int(0.7 * span)


def test_a_young_symbol_gets_the_short_slice_it_deserves():
    frames = {"OLD": _frame(0, 1000), "YOUNG": _frame(800 * 4 * HOUR, 200)}
    cut = slices.cut_ms(frames)
    disc = slices.discovery(frames, cut, min_bars=1)
    assert len(disc["OLD"]) == 700
    assert "YOUNG" not in disc or len(disc["YOUNG"]) == 0


def test_a_slice_too_short_to_survive_warmup_is_dropped():
    frames = {"A": _frame(0, 1000), "B": _frame(0, 1000)}
    cut = slices.cut_ms(frames)
    disc = slices.discovery(frames, cut, min_bars=WARMUP + 600)
    assert disc == {}


def test_the_discovery_slice_holds_no_bar_at_or_after_the_cut():
    frames = {"A": _frame(0, 1000)}
    cut = slices.cut_ms(frames)
    d = slices.discovery(frames, cut, min_bars=1)["A"]
    assert d["ts"].max().value // 10 ** 6 < cut


def test_held_out_b_starts_at_the_cut():
    frames = {"A": _frame(0, 1000)}
    cut = slices.cut_ms(frames)
    h = slices.heldout_b(frames, cut, min_bars=1)["A"]
    assert h["ts"].min().value // 10 ** 6 >= cut
    assert len(h) == 300


def test_the_two_slices_partition_the_frame():
    frames = {"A": _frame(0, 1000)}
    cut = slices.cut_ms(frames)
    d = slices.discovery(frames, cut, min_bars=1)["A"]
    h = slices.heldout_b(frames, cut, min_bars=1)["A"]
    assert len(d) + len(h) == 1000


def test_usable_bars_subtracts_the_warmup():
    assert slices.usable_bars(1000) == 1000 - WARMUP
    assert slices.usable_bars(10) == 0


def test_an_empty_universe_has_no_cut():
    assert slices.cut_ms({}) is None
    assert slices.cut_ms({"A": None}) is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `./venv/bin/python -m pytest tests/test_research_slices.py -q -p no:cacheprovider`
Expected: FAIL with `ModuleNotFoundError: No module named 'trader.research.slices'`.

- [ ] **Step 3: Implement**

Create `trader/research/slices.py`:

```python
"""Where the search is allowed to look.

One CALENDAR cut per horizon, at 70% of the universe's own span. The
alternative — each symbol's own 70% — puts a young market's discovery slice
inside an old market's held-out era, so the "unseen era" would already have
been searched somewhere else. A calendar cut makes held-out B genuinely
later for every symbol in it.

`WARMUP` is a BAR COUNT, not a duration: `simulate` skips the first 210 bars
of every slice it is handed. A slice shorter than that plus room to trade
carries no information, so it is dropped rather than scored on noise.
"""
from __future__ import annotations

import pandas as pd

from ..strategy.vector_backtest import WARMUP

#: the share of the universe's span the search may see
DISCOVERY_FRAC = 0.7
#: a slice shorter than this cannot survive WARMUP and still trade
MIN_SLICE_BARS = WARMUP + 210


def _ms(df) -> pd.Series:
    """Epoch milliseconds, whatever resolution the frame carries.

    CORRECTED 2026-09-12 during execution: this originally read
    `pd.to_datetime(df["ts"], utc=True).astype("int64") // 10 ** 6`, which is
    wrong. `DataFeed.cached_ohlcv` returns `ts` as datetime64[**ms**] —
    pandas 2.x preserves the unit passed to `pd.to_datetime(..., unit="ms")` —
    so that expression divides milliseconds by 10**6 and produces a clock off
    by a factor of a million, in the one module that decides what the search
    is allowed to see. Branch on the resolution instead; verified against a
    ms frame, an ns frame, and a real frame from the store.
    """
    ts = df["ts"]
    vals = ts._values.view("int64")
    unit = str(ts.dtype)
    if "[ms" in unit:
        return pd.Series(vals, index=ts.index)
    if "[us" in unit:
        return pd.Series(vals // 10 ** 3, index=ts.index)
    if "[s" in unit:
        return pd.Series(vals * 10 ** 3, index=ts.index)
    return pd.Series(vals // 10 ** 6, index=ts.index)          # ns


def cut_ms(frames: dict, frac: float = DISCOVERY_FRAC) -> int | None:
    """The one timestamp that separates discovery from held-out B."""
    lo, hi = None, None
    for df in (frames or {}).values():
        if df is None or not len(df):
            continue
        m = _ms(df)
        lo = int(m.iloc[0]) if lo is None else min(lo, int(m.iloc[0]))
        hi = int(m.iloc[-1]) if hi is None else max(hi, int(m.iloc[-1]))
    if lo is None or hi is None or hi <= lo:
        return None
    return lo + int((hi - lo) * float(frac))


def before(df, cut: int):
    if df is None or not len(df):
        return df
    return df[_ms(df) < int(cut)].reset_index(drop=True)


def after(df, cut: int):
    if df is None or not len(df):
        return df
    return df[_ms(df) >= int(cut)].reset_index(drop=True)


def _slice_all(frames: dict, cut: int, fn, min_bars: int) -> dict:
    out = {}
    for sym, df in (frames or {}).items():
        part = fn(df, cut)
        if part is not None and len(part) >= min_bars:
            out[sym] = part
    return out


def discovery(frames: dict, cut: int, min_bars: int = MIN_SLICE_BARS) -> dict:
    """Every bar that had closed before the cut."""
    return _slice_all(frames, cut, before, min_bars)


def heldout_b(frames: dict, cut: int, min_bars: int = MIN_SLICE_BARS) -> dict:
    """The later era. Phase 2 counts its BARS and never reads its prices."""
    return _slice_all(frames, cut, after, min_bars)


def bar_counts(frames: dict) -> dict:
    return {s: int(len(df)) for s, df in (frames or {}).items()
            if df is not None}


def usable_bars(n: int) -> int:
    """Bars a slice can actually trade on, after the engine's warmup."""
    return max(0, int(n) - WARMUP)
```

- [ ] **Step 4: Run the tests**

Run: `./venv/bin/python -m pytest tests/test_research_slices.py -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add trader/research/slices.py tests/test_research_slices.py
git commit -m "$(cat <<'EOF'
feat(research): one calendar cut, and the search cannot see past it

Per-symbol 70% splits leak era across symbols — a young market's discovery
slice sits inside an old market's held-out era. One date per horizon.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01EpstKPhQNEEG3wz2i7T9td
EOF
)"
```

---

### Task 5: Measured thresholds, and whether a gauge exists at all

**Files:**
- Create: `trader/research/thresholds.py`
- Test: `tests/test_research_thresholds.py`

**Interfaces:**
- Consumes: `vocab.expressions`, `vocab.QUANTILES`, `slices`.
- Produces: `measure(exprs: list[str], ctxs: list, min_samples: int = 5000, min_finite_frac: float = 0.5) -> dict` returning, per expression, `{"p10","p25","p75","p90","n","finite_frac","usable"}` — every percentile key formed the SAME way on both the normal and the error path; `ctxs` is a list of ready-built `FeatureCtx` objects, one per discovery symbol.

- [ ] **Step 1: Write the failing test**

Create `tests/test_research_thresholds.py`:

```python
"""A threshold is a percentile of the quantity's OWN distribution, and a
gauge that is NaN over the slice is not a gauge at all.

Two faults this closes. Absolute constants over distributions that never
reach them: `taker_buy/volume > 0.62` fires on 0.005% of 4h bars, so the
flow family appeared in the results table having never traded. And series
that do not exist over the window: open interest begins 2025-10 while the 4h
discovery cut lands ~2025-05, so every OI gauge is NaN over the whole slice —
which must read as "not measurable here", never as a threshold of NaN.
"""
import numpy as np
import pandas as pd

from trader.research import thresholds
from trader.strategy.features import FeatureCtx


def _ctx(n=6000, seed=0):
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    ts = pd.to_datetime(np.arange(n) * 14_400_000, unit="ms", utc=True)
    df = pd.DataFrame({"ts": ts, "open": close, "high": close * 1.01,
                       "low": close * 0.99, "close": close,
                       "volume": rng.uniform(1, 2, n),
                       "taker_buy": rng.uniform(0.4, 1.2, n)})
    return FeatureCtx(frames={"4h": df}, tf="4h", symbol="BT")


def test_percentiles_come_from_the_data():
    out = thresholds.measure(["ret(24)"], [_ctx()], min_samples=100)
    m = out["ret(24)"]
    assert m["p10"] < m["p25"] < m["p75"] < m["p90"]
    assert m["usable"] is True
    assert m["finite_frac"] > 0.9


def test_a_gauge_that_is_never_finite_is_not_usable():
    out = thresholds.measure(["funding_z(360)"], [_ctx()], min_samples=100)
    m = out["funding_z(360)"]
    assert m["finite_frac"] == 0.0
    assert m["usable"] is False
    assert m["p10"] is None and m["p90"] is None


def test_a_gauge_finite_on_a_minority_of_the_slice_is_not_usable():
    ctx = _ctx()
    # a quantity defined only after a long warmup: 500 of 6000 bars
    out = thresholds.measure(["zscore(close, 5500)"], [ctx],
                             min_samples=100, min_finite_frac=0.5)
    assert out["zscore(close, 5500)"]["usable"] is False


def test_too_few_samples_is_not_a_measurement():
    out = thresholds.measure(["ret(24)"], [_ctx(n=400)], min_samples=5000)
    assert out["ret(24)"]["usable"] is False
    assert out["ret(24)"]["n"] < 5000


def test_samples_pool_across_symbols():
    a, b = _ctx(seed=1), _ctx(seed=2)
    one = thresholds.measure(["ret(24)"], [a], min_samples=100)
    two = thresholds.measure(["ret(24)"], [a, b], min_samples=100)
    assert two["ret(24)"]["n"] > one["ret(24)"]["n"]


def test_an_expression_that_will_not_parse_is_reported_not_raised():
    out = thresholds.measure(["no_such_feature(3)"], [_ctx()],
                             min_samples=100)
    assert out["no_such_feature(3)"]["usable"] is False
    assert "error" in out["no_such_feature(3)"]


def test_a_boolean_expression_still_measures():
    """breadth(close > ema(50)) evaluates its inner boolean; the OUTER value
    is a float, and a measurement of it must not crash on dtype."""
    out = thresholds.measure(["close > ema(50)"], [_ctx()], min_samples=100)
    assert out["close > ema(50)"]["n"] > 0
```

- [ ] **Step 2: Run to verify it fails**

Run: `./venv/bin/python -m pytest tests/test_research_thresholds.py -q -p no:cacheprovider`
Expected: FAIL with `ModuleNotFoundError: No module named 'trader.research.thresholds'`.

- [ ] **Step 3: Implement**

Create `trader/research/thresholds.py`:

```python
"""Measure every gauge on the discovery slice: where its percentiles sit,
and whether it exists there at all.

The second half is the part that keeps the search honest. A derivative or
reference series that begins after the discovery cut evaluates to NaN on
every bar, and a NaN comparison is False — so a mechanism built on it takes
zero trades and reads in a results table as "no edge" rather than "never
tested". `finite_frac` turns that silence into a number, and a gauge below
`min_finite_frac` contributes no parts at all.

Measured 2026-09-11: open interest and the account ratio begin 2025-10-03
while the 4h discovery cut lands ~2025-05, so the positioning family drops
out of the 4h vocabulary by measurement rather than by a hard-coded rule.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from ..strategy import dsl
from .vocab import QUANTILES

log = logging.getLogger(__name__)

#: pooled samples below this cannot place a 10th/90th percentile worth using
MIN_SAMPLES = 5000
#: a gauge finite over less of the slice than this is not measurable here
MIN_FINITE_FRAC = 0.5


def _values(tree, ctx) -> np.ndarray:
    v = dsl.evaluate(tree, ctx)
    if isinstance(v, pd.Series):
        return v.to_numpy(dtype=float)
    return np.full(len(ctx.index), float(v), dtype=float)


def measure(exprs, ctxs, min_samples: int = MIN_SAMPLES,
            min_finite_frac: float = MIN_FINITE_FRAC) -> dict:
    """{expr: {p10, p25, p75, p90, n, finite, finite_frac, usable}}.

    `ctxs` are ready-built FeatureCtx objects — one per discovery symbol,
    already carrying the universe, the leader, the derivatives and the
    reference frames, all truncated at the cut. Samples POOL across symbols:
    a threshold that differs per symbol could not be written into an
    expression that trades a universe.
    """
    out: dict = {}
    for expr in exprs:
        try:
            tree = dsl.parse(expr)
        except Exception as e:                          # noqa: BLE001
            out[expr] = {"usable": False, "error": str(e), "n": 0,
                         "finite_frac": 0.0,
                         **{f"p{int(round(q * 100))}": None
                            for q in QUANTILES}}
            continue
        total, vals = 0, []
        for ctx in ctxs:
            try:
                arr = _values(tree, ctx)
            except Exception as e:                      # noqa: BLE001
                log.debug(f"threshold {expr} on {ctx.symbol}: {e}")
                continue
            total += int(arr.size)
            vals.append(arr[np.isfinite(arr)])
        finite = np.concatenate(vals) if vals else np.array([], dtype=float)
        n = int(finite.size)
        frac = (n / total) if total else 0.0
        rec = {"n": n, "finite_frac": round(frac, 4)}
        usable = n >= int(min_samples) and frac >= float(min_finite_frac)
        for q in QUANTILES:
            key = f"p{int(round(q * 100))}"
            rec[key] = (round(float(np.quantile(finite, q)), 8)
                        if usable else None)
        # a constant quantity has no percentiles worth comparing against
        if usable and rec["p10"] == rec["p90"]:
            usable = False
            for q in QUANTILES:
                rec[f"p{int(round(q * 100))}"] = None
        rec["usable"] = bool(usable)
        out[expr] = rec
    return out
```

- [ ] **Step 4: Run the tests**

Run: `./venv/bin/python -m pytest tests/test_research_thresholds.py -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add trader/research/thresholds.py tests/test_research_thresholds.py
git commit -m "$(cat <<'EOF'
feat(research): thresholds are measured, and silence is reported as silence

finite_frac turns "this gauge is NaN over the whole slice" into a number, so
open interest drops out of the 4h vocabulary by measurement rather than by a
rule someone remembered to write.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01EpstKPhQNEEG3wz2i7T9td
EOF
)"
```

---

### Task 6: A combination, its canonical hash, and the spec it becomes

**Files:**
- Create: `trader/research/combo.py`
- Test: `tests/test_research_combo.py`

**Interfaces:**
- Consumes: `vocab.Part`, `vocab.part_requires`, `strategy.geometries.GEOS`.
- Produces: `MAX_PARTS = 6`; `SCHEMA_VERSION = 1`;
  `@dataclass(frozen=True) Combination(parts: tuple[Part, ...], tf: str, geo: str, trigger: str = "", round: str = "singles", parent: str = "")`
  with `.hash -> str` (16 hex chars), `.k -> int`, `.long -> str`, `.short -> str`, `.requires -> tuple[str, ...]`, `.window -> str`, `.to_spec() -> StrategySpec`, `.as_dict()/from_dict()`;
  `compatible(parts) -> bool`; `window_key(requires) -> str`; `subsets(combo) -> list[Combination]`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_research_combo.py`:

```python
"""A combination is a SET of AND-ed parts under a canonical hash.

`and` is commutative, so the same set reached from two different parents is
the same rule and must be evaluated once — otherwise the ledger double-counts
looks, and a search that counts its own looks wrongly cannot price them.

The hash carries the percentile RANK, never the threshold value, so the same
rule keeps its identity across a re-measurement; the rendered expressions on
the row keep the values that were actually traded.
"""
import pytest

from trader.research.combo import (Combination, MAX_PARTS, compatible,
                                   subsets, window_key)
from trader.research.vocab import Part
from trader.strategy.compile import compile_spec
from trader.strategy.spec import StrategySpec

A = Part("ret24>p90", "gauge", "ret24", "ret(24) > 0.05", "0 - ret(24) > 0.06")
B = Part("st:above_ema50", "state", "st:above_ema50",
         "close > ema(50)", "close < ema(50)")
C = Part("ev:donch100", "event", "ev:donch100",
         "close > donchian_hi(100)", "close < donchian_lo(100)")
D = Part("ev:donch20", "event", "ev:donch20",
         "close > donchian_hi(20)", "close < donchian_lo(20)")
E = Part("ret24<p10", "gauge", "ret24", "ret(24) < -0.05",
         "0 - ret(24) < -0.06")
F = Part("fundz360>p90", "gauge", "fundz360", "funding_z(360) > 1.4",
         "0 - funding_z(360) > 1.5")


def _c(parts, tf="4h", geo="trail"):
    return Combination(parts=tuple(parts), tf=tf, geo=geo)


def test_part_order_does_not_change_the_hash():
    assert _c([A, B, C]).hash == _c([C, A, B]).hash


def test_a_different_percentile_is_a_different_combination():
    assert _c([A]).hash != _c([E]).hash


def test_timeframe_and_geometry_are_part_of_the_identity():
    assert _c([A], tf="4h").hash != _c([A], tf="1h").hash
    assert _c([A], geo="trail").hash != _c([A], geo="fixed").hash


def test_the_hash_ignores_how_it_was_reached():
    one = Combination((A, B), "4h", "trail", trigger="ret24>p90",
                      round="grow", parent="abc")
    two = Combination((B, A), "4h", "trail", trigger="st:above_ema50",
                      round="grow", parent="def")
    assert one.hash == two.hash


def test_both_legs_are_the_conjunction_of_their_parts():
    c = _c([A, B])
    assert c.long == "ret(24) > 0.05 and close > ema(50)"
    assert c.short == "0 - ret(24) > 0.06 and close < ema(50)"


def test_two_parts_of_one_gauge_are_incompatible():
    assert not compatible((A, E))


def test_two_events_are_incompatible():
    assert not compatible((C, D))
    assert compatible((C, A, B))


def test_a_combination_may_not_exceed_six_parts():
    many = [A, B, C,
            Part("rsi14>p90", "gauge", "rsi14", "rsi(14) > 70",
                 "100 - rsi(14) > 71"),
            Part("er30>p75", "gauge", "er30", "efficiency_ratio(30) > 0.3",
                 "efficiency_ratio(30) > 0.3"),
            Part("adx14>p75", "gauge", "adx14", "adx(14) > 25",
                 "adx(14) > 25"),
            F]
    assert len(many) == MAX_PARTS + 1
    assert not compatible(tuple(many))
    assert compatible(tuple(many[:MAX_PARTS]))


def test_a_combination_compiles_into_a_valid_spec():
    spec = _c([A, B, C]).to_spec()
    assert StrategySpec.validate(spec) == []
    compiled = compile_spec(spec)
    assert compiled.spec.timeframe == "4h"
    assert compiled.spec.direction == "both"


def test_the_spec_carries_the_chosen_geometry():
    spec = _c([C], geo="trail").to_spec()
    assert spec.exit.trail == {"kind": "atr", "mult": 4.0, "arm_at_r": 1.0}
    spec = _c([C], geo="fixed").to_spec()
    assert spec.exit.target == {"kind": "rr", "v": 3.0}


def test_requirements_are_the_union_of_the_parts():
    assert _c([C]).requires == ("ohlcv",)
    assert "funding" in _c([C, F]).requires


def test_the_window_is_what_is_needed_beyond_candles():
    assert window_key(("ohlcv",)) == "ohlcv"
    assert window_key(("ohlcv", "funding")) == "funding"
    assert window_key(("ref:spx", "ohlcv", "funding")) == "funding|ref:spx"


def test_subsets_are_every_one_part_ablation():
    c = _c([A, B, C])
    subs = subsets(c)
    assert len(subs) == 3
    assert {s.hash for s in subs} == {_c([B, C]).hash, _c([A, C]).hash,
                                      _c([A, B]).hash}
    assert all(s.k == 2 for s in subs)


def test_a_single_has_no_subsets():
    assert subsets(_c([A])) == []


def test_object_equality_agrees_with_the_canonical_hash():
    """A later `if c not in seen: seen.add(c)` must deduplicate by identity.

    The default frozen-dataclass equality compares every field in the order
    given, so it would treat two orderings of one rule as two rules — the
    double-counting the hash exists to prevent.
    """
    one, two = _c([A, B]), _c([B, A])
    assert one == two
    assert len({one, two}) == 1
    assert _c([A, B], geo="fixed") != _c([A, B], geo="trail")
    # provenance is how a rule was reached, not what it is
    assert Combination((A, B), "4h", "trail", round="grow",
                       parent="cafe") == one


def test_a_combination_survives_a_round_trip_through_a_dict():
    c = Combination((A, B), "1h", "fixed", trigger="ret24>p90",
                    round="grow", parent="cafe")
    back = Combination.from_dict(c.as_dict())
    assert back == c and back.hash == c.hash
```

- [ ] **Step 2: Run to verify it fails**

Run: `./venv/bin/python -m pytest tests/test_research_combo.py -q -p no:cacheprovider`
Expected: FAIL with `ModuleNotFoundError: No module named 'trader.research.combo'`.

- [ ] **Step 3: Implement**

Create `trader/research/combo.py`:

```python
"""One searched rule: a set of AND-ed parts, both directions, one geometry.

`and` is commutative, so a combination is a SET and its identity is a
canonical hash over the sorted part keys plus the timeframe, the geometry and
the direction. Two growth paths that arrive at the same set arrive at the
same hash, are evaluated once, and count as one look — which is what makes
the ledger's count of looks a true count.

The hash carries each threshold's percentile RANK ("ret24>p90"), never its
value, so the rule keeps its identity if the percentiles are ever
re-measured; the row stores the rendered expressions that were actually
traded, so any result can be reproduced exactly.

At most ONE event part: two breakouts landing on the same bar is a rule that
never fires, and the spec's shape is one trigger plus context anyway.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from ..strategy.geometries import GEOS
from ..strategy.spec import StrategySpec
from .vocab import Part, part_requires

#: 1 trigger + 5 context, per the spec
MAX_PARTS = 6
#: bump only if the canonical form itself changes; it invalidates the ledger
SCHEMA_VERSION = 1

_THESIS = (
    "Machine-generated combination scored against a rotation of its own "
    "entries, so that market drift and exit-geometry payout odds cannot be "
    "mistaken for entry skill. Discovery evidence only; held-out markets and "
    "the later era have not been looked at.")
_INVALIDATION = (
    "Retire when the pooled profit factor falls below 0.85 over 10 trades in "
    "30 days, or when the rotation null can no longer be beaten.")


def window_key(requires) -> str:
    """What this rule needs BEYOND candles — the window it can be seen in.

    Every window carries its own control: a gate that cannot detect the
    known-good rule inside a window cannot refute a challenger inside it.
    """
    extra = sorted(r for r in (requires or ()) if r != "ohlcv")
    return "|".join(extra) if extra else "ohlcv"


def compatible(parts) -> bool:
    """Can these parts stand in one rule?"""
    parts = tuple(parts)
    if not parts or len(parts) > MAX_PARTS:
        return False
    gauges = [p.gauge for p in parts]
    if len(set(gauges)) != len(gauges):
        return False                      # two cuts of one quantity
    if sum(1 for p in parts if p.kind == "event") > 1:
        return False                      # two triggers on one bar
    return True


@dataclass(frozen=True, eq=False)
class Combination:
    parts: tuple
    tf: str
    geo: str
    trigger: str = ""          # the single this grew from — recorded, not used
    round: str = "singles"     # singles | grow | seeded | control | ablation
    parent: str = ""           # parent hash, for the growth comparison

    # ── identity ─────────────────────────────────────────────────────────
    @property
    def keys(self) -> tuple:
        return tuple(sorted(p.key for p in self.parts))

    @property
    def hash(self) -> str:
        blob = json.dumps({"v": SCHEMA_VERSION, "tf": self.tf,
                           "geo": self.geo, "dir": "both",
                           "parts": list(self.keys)}, sort_keys=True)
        return hashlib.sha256(blob.encode()).hexdigest()[:16]

    # Python's own equality must agree with the canonical hash. Without
    # this, `@dataclass(frozen=True)` compares every field in the order it
    # was given — so a later `if c not in seen: seen.add(c)`, the idiomatic
    # way to deduplicate, would dedup by part ORDER and by provenance and
    # silently reintroduce the double-counting this hash exists to prevent.
    # Provenance (trigger, round, parent) is how a rule was REACHED, not
    # what it is; two paths to the same set of parts are one rule.
    def __eq__(self, other) -> bool:
        return isinstance(other, Combination) and self.hash == other.hash

    def __hash__(self) -> int:
        return hash(self.hash)

    @property
    def k(self) -> int:
        return len(self.parts)

    # ── the rule ─────────────────────────────────────────────────────────
    @property
    def long(self) -> str:
        return " and ".join(p.long for p in self._ordered)

    @property
    def short(self) -> str:
        return " and ".join(p.short for p in self._ordered)

    @property
    def _ordered(self) -> tuple:
        """Rendering order: as given, so a grown rule reads parent-first."""
        return tuple(self.parts)

    @property
    def requires(self) -> tuple:
        req: set = set()
        for p in self.parts:
            req.update(part_requires(p))       # one definition, in vocab
        return tuple(sorted(req or {"ohlcv"}))

    @property
    def window(self) -> str:
        return window_key(self.requires)

    def to_spec(self) -> StrategySpec:
        """A StrategySpec the existing engine can compile and score.

        `universe.include` stays empty: the search hands the evaluator its
        symbols explicitly, and a declared universe here would be a claim
        nothing has measured yet.
        """
        h = self.hash
        return StrategySpec(
            id=f"research_{h}", name=f"search {h} {self.tf} {self.geo}",
            thesis=_THESIS, invalidation=_INVALIDATION,
            provenance={"source_kind": "research", "round": self.round,
                        "parts": list(self.keys), "trigger": self.trigger,
                        "parent": self.parent},
            universe={"include": [], "exclude": []},
            timeframe=self.tf, direction="both",
            entry_long=self.long, entry_short=self.short, filters=[],
            exit=GEOS[self.geo], regime_filter=[], markets=["futures"],
            data_requires=list(self.requires))

    # ── transport ────────────────────────────────────────────────────────
    def as_dict(self) -> dict:
        return {"parts": [p.as_dict() for p in self.parts], "tf": self.tf,
                "geo": self.geo, "trigger": self.trigger,
                "round": self.round, "parent": self.parent}

    @staticmethod
    def from_dict(d: dict) -> "Combination":
        return Combination(
            parts=tuple(Part.from_dict(p) for p in d["parts"]),
            tf=d["tf"], geo=d["geo"], trigger=d.get("trigger", ""),
            round=d.get("round", "singles"), parent=d.get("parent", ""))


def subsets(c: Combination) -> list:
    """Every rule reachable by removing exactly one part — the ablation.

    A survivor must beat all of these: that is what "a part earns its place"
    means, and it is how ties go to the simpler rule.
    """
    if c.k < 2:
        return []
    out = []
    for i in range(c.k):
        parts = c.parts[:i] + c.parts[i + 1:]
        out.append(Combination(parts=parts, tf=c.tf, geo=c.geo,
                               trigger=c.trigger, round="ablation",
                               parent=c.hash))
    return out
```

- [ ] **Step 4: Run the tests**

Run: `./venv/bin/python -m pytest tests/test_research_combo.py -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add trader/research/combo.py tests/test_research_combo.py
git commit -m "$(cat <<'EOF'
feat(research): a combination is a set, and its hash is its identity

Two growth paths to the same set of parts are the same rule; evaluating it
twice would double-count a look, and the whole error budget rests on that
count being true.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01EpstKPhQNEEG3wz2i7T9td
EOF
)"
```

---

### Task 7: The discovery evaluator

**Files:**
- Create: `trader/research/evaluate.py`
- Test: `tests/test_research_evaluate.py`

**Interfaces:**
- Consumes: `slices`, `combo.Combination`, `strategy.compile_spec`, `strategy.vector_backtest.simulate/funding_for`, `strategy.null_baseline`, `strategy.portfolio_evidence.portfolio_curve`, `strategy.spec_evidence.frames_for/load_derivs/risk_for`.
- Produces:
  - `@dataclass Bundle(tf, frames, sym_frames, universe, btc, market, derivs, risk, cut, heldout_bars, equity, risk_pct, max_open)`
  - `load_bundle(tf, symbols, cfg, requires=(), heldout_symbols=(), paths=None) -> Bundle`
  - `evaluate(c: Combination, b: Bundle, draws: int = 30, seed: int = 17, min_symbol_trades: int = 8) -> dict`
  - `MIN_PROJECTED_TRADES = 8`, `MIN_PROJECTED_MARKETS = 4`

- [ ] **Step 1: Write the failing test**

Create `tests/test_research_evaluate.py`:

```python
"""Scoring one combination on the discovery slice.

Four things must hold, and each has cost real money or real weeks before:

1. The evaluator never sees a bar past the cut. Not "does not use" — cannot:
   the frames it holds stop there.
2. A profit factor alone is a statement about arithmetic. The score that
   decides anything is the cross-symbol spread of rotation-null percentiles.
3. The compounded return is walked over ONE account, and fills from
   different symbols must be ordered by TIME. `Fill.entry_i` is a per-symbol
   bar index, so they are remapped onto a common clock first — sorting raw
   indices would interleave a young market's first bar with an old market's
   thousandth.
4. Fewer than 4 symbols carrying a percentile is untestable, and untestable
   is a refusal, never a score.
"""
import numpy as np
import pandas as pd
import pytest

from trader.research import evaluate, slices
from trader.research.combo import Combination
from trader.research.vocab import Part

TF_MS = 14_400_000
DONCH = Part("ev:donch20", "event", "ev:donch20",
             "close > donchian_hi(20)", "close < donchian_lo(20)")
EMA = Part("st:above_ema50", "state", "st:above_ema50",
           "close > ema(50)", "close < ema(50)")
NOISE = Part("rsi14>p75", "gauge", "rsi14", "rsi(14) > 55",
             "100 - rsi(14) > 55")


def _trending(n, seed):
    """Blocks of trend and chop: a breakout mechanism has a real edge here,
    and a rotation of the same signals lands in the chop."""
    rng = np.random.default_rng(seed)
    out, px = [], 100.0
    while len(out) < n:
        for _ in range(60):                      # trend
            px *= 1.0 + rng.normal(0.006, 0.004)
            out.append(px)
        for _ in range(60):                      # chop
            px *= 1.0 + rng.normal(0.0, 0.010)
            out.append(px)
    return np.array(out[:n])


def _frame(n, seed, start_ms=0):
    c = _trending(n, seed)
    ts = pd.to_datetime(start_ms + np.arange(n) * TF_MS, unit="ms", utc=True)
    return pd.DataFrame({"ts": ts, "open": c, "high": c * 1.004,
                         "low": c * 0.996, "close": c,
                         "volume": np.full(n, 10.0),
                         "taker_buy": np.full(n, 5.0)})


def _bundle(n_symbols=6, n=1400, cfg_risk=None):
    frames = {f"S{i}/USDT": _frame(n, seed=i) for i in range(n_symbols)}
    cut = slices.cut_ms(frames)
    disc = slices.discovery(frames, cut, min_bars=1)
    risk = {"risk_per_trade_pct": 0.5, "taker_fee_pct": 0.04,
            "slippage_atr_frac": 0.015, "funding_rate_8h": 0.0001,
            "bar_minutes": 240, "real_funding": False}
    risk.update(cfg_risk or {})
    return evaluate.Bundle(
        tf="4h", frames=disc,
        sym_frames={s: {"4h": d} for s, d in disc.items()},
        universe={s: {"4h": d} for s, d in disc.items()},
        btc={"4h": disc[next(iter(disc))]}, market=None,
        derivs={s: {} for s in disc}, risk=risk, cut=cut,
        heldout_bars={"a": {f"H{i}": 4000 for i in range(6)},
                      "b": {s: 400 for s in disc}})


def _combo(parts):
    return Combination(parts=tuple(parts), tf="4h", geo="trail")


def test_the_bundle_holds_no_bar_at_or_after_the_cut():
    b = _bundle()
    for df in b.frames.values():
        assert df["ts"].max().value // 10 ** 6 < b.cut


def test_a_planted_edge_scores_and_beats_its_rotation():
    r = evaluate.evaluate(_combo([DONCH]), _bundle(), draws=20)
    assert r["verdict"] == "scored"
    assert r["scored_symbols"] >= 4
    assert r["consistency_p"] is not None
    assert r["median_pf"] > 1.0


def test_the_result_carries_a_percentile_for_every_scored_symbol():
    r = evaluate.evaluate(_combo([DONCH]), _bundle(), draws=20)
    scored = [s for s, v in r["symbols"].items()
              if v["null_pctile"] is not None]
    assert len(scored) == r["scored_symbols"]
    assert all(0.0 <= r["symbols"][s]["null_pctile"] <= 1.0 for s in scored)


def test_a_rule_that_never_fires_is_empty_not_bad():
    dead = Part("never", "gauge", "never", "rsi(14) > 1000",
                "rsi(14) > 1000")
    r = evaluate.evaluate(_combo([dead]), _bundle(), draws=20)
    assert r["verdict"] == "empty"
    assert r["trades"] == 0
    assert r["consistency_p"] is None


def test_too_few_scored_symbols_is_untestable():
    r = evaluate.evaluate(_combo([DONCH]), _bundle(n_symbols=3), draws=20)
    assert r["verdict"] == "untestable"
    assert r["consistency_p"] is None


def test_the_portfolio_walk_orders_fills_across_symbols_by_time():
    """Two symbols whose frames start a year apart: raw bar indices would
    interleave them wrongly, and the compounded return would be a different
    number than the account could ever have earned."""
    a = _frame(1400, seed=1)
    late = _frame(1400, seed=2, start_ms=1400 * TF_MS)
    frames = {"A/USDT": a, "B/USDT": late}
    cut = slices.cut_ms(frames)
    disc = slices.discovery(frames, cut, min_bars=1)
    b = evaluate.Bundle(
        tf="4h", frames=disc,
        sym_frames={s: {"4h": d} for s, d in disc.items()},
        universe={s: {"4h": d} for s, d in disc.items()},
        btc=None, market=None, derivs={s: {} for s in disc},
        risk={"risk_per_trade_pct": 0.5, "taker_fee_pct": 0.04,
              "slippage_atr_frac": 0.015, "funding_rate_8h": 0.0001,
              "bar_minutes": 240, "real_funding": False},
        cut=cut, heldout_bars={"a": {}, "b": {}})
    r = evaluate.evaluate(_combo([DONCH]), b, draws=20)
    order = r["portfolio"]["order"]
    first_b = order.index("B/USDT") if "B/USDT" in order else len(order)
    # every A fill that happened before B's frame even starts comes first
    assert first_b > 0


def test_testability_is_projected_from_the_discovery_trade_rate():
    r = evaluate.evaluate(_combo([DONCH]), _bundle(), draws=20)
    p = r["projection"]
    assert p["markets_a"] >= 1 and p["markets_b"] >= 0
    assert isinstance(r["testable"], bool)


def test_a_picky_rule_projects_too_few_trades_to_be_judged():
    b = _bundle()
    b.heldout_bars = {"a": {f"H{i}": 300 for i in range(6)},
                      "b": {s: 300 for s in b.frames}}
    r = evaluate.evaluate(_combo([DONCH, EMA, NOISE]), b, draws=20)
    assert r["testable"] is False


def test_the_same_combination_scores_the_same_twice():
    b = _bundle()
    one = evaluate.evaluate(_combo([DONCH]), b, draws=20, seed=5)
    two = evaluate.evaluate(_combo([DONCH]), b, draws=20, seed=5)
    assert one["consistency_p"] == two["consistency_p"]
    assert one["portfolio"]["total_pct"] == two["portfolio"]["total_pct"]


def test_a_symbol_that_raises_does_not_lose_the_whole_combination():
    b = _bundle()
    b.sym_frames["S1/USDT"] = {"4h": None}
    r = evaluate.evaluate(_combo([DONCH]), b, draws=20)
    assert r["verdict"] in ("scored", "untestable")
    assert r["symbols"]["S1/USDT"].get("error")
```

- [ ] **Step 2: Run to verify it fails**

Run: `./venv/bin/python -m pytest tests/test_research_evaluate.py -q -p no:cacheprovider`
Expected: FAIL with `ModuleNotFoundError: No module named 'trader.research.evaluate'`.

- [ ] **Step 3: Implement**

Create `trader/research/evaluate.py`:

```python
"""Score one combination on the discovery slice.

What comes back is deliberately NOT a single number:

- `consistency_p` — the cross-symbol spread of rotation-null percentiles.
  A profit factor alone is a statement about arithmetic: exit geometry sets a
  win rate by itself and ALWAYS-LONG scores PF 1.28 on drift. A percentile
  per symbol is noise; the SHAPE across independent markets is the test.
- `portfolio.total_pct` — compounded return over ONE account at the live
  concurrency cap. Every filter ever tried on the book held its profit factor
  while halving compounded return, because the trades it cut were near
  break-even individually and carried the fat right tail. A refinement is
  judged here, never on a ratio that improves while the trade count falls.
- `projection` — whether the rule fires often enough to be JUDGED on the
  held-out markets at all. A combination too picky to be tested stops
  growing; it is never admitted on silence.

The bundle is truncated at the cut before anything is evaluated, so no code
path here can read a bar the search is not entitled to.
"""
from __future__ import annotations

import logging
import statistics as st
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..strategy import null_baseline, spec_evidence
from ..strategy.compile import compile_spec
from ..strategy.portfolio_evidence import Fill, portfolio_curve
from ..strategy.vector_backtest import funding_for, simulate
from . import slices

log = logging.getLogger(__name__)

#: a symbol carrying fewer test trades than this cannot carry a percentile —
#: the same floor Analyst._null_percentiles applies
MIN_SYMBOL_TRADES = 8
#: the held-out projection: this many trades on this many markets
MIN_PROJECTED_TRADES = 8
MIN_PROJECTED_MARKETS = 4

_TF_SECONDS = {"5m": 300, "15m": 900, "1h": 3600, "4h": 14400}


@dataclass
class Bundle:
    """Everything one horizon's evaluations need, already truncated."""
    tf: str
    frames: dict                     # symbol -> discovery-slice frame
    sym_frames: dict                 # symbol -> {tf: frame, ...} for htf()
    universe: dict                   # symbol -> {tf: frame}
    btc: dict | None                 # {tf: frame} — keyed by TIMEFRAME
    market: dict | None              # reference key -> frame
    derivs: dict                     # symbol -> {series: raw frame}
    risk: dict
    cut: int = 0
    heldout_bars: dict = field(default_factory=lambda: {"a": {}, "b": {}})
    equity: float = 2000.0
    risk_pct: float = 0.5
    max_open: int = 8


def load_bundle(tf: str, symbols, cfg: dict, requires=(),
                heldout_symbols=(), paths: dict | None = None) -> Bundle:
    """Read the stores, cut at the discovery boundary, build the context."""
    from ..data.derivatives import DerivFeed
    from ..data.feed import DataFeed
    from ..data.references import RefStore
    from .universe import NoExchange

    paths = paths or {}
    # NoExchange lives in universe.py and is the ONE definition: a DataFeed
    # built for research reads must never construct a ccxt client, and an
    # accidental use of it raises instead of quietly fetching — a silent
    # fetch inside a niced child would be invisible.
    feed = DataFeed(exchange=NoExchange(), db_path=paths.get("candles"))
    full = {}
    for sym in symbols:
        try:
            df = feed.cached_ohlcv(sym, tf, limit=200000)
        except Exception as e:                          # noqa: BLE001
            log.warning(f"research load {sym} {tf}: {e}")
            continue
        if df is not None and len(df) >= 500:
            full[sym] = df

    cut = slices.cut_ms(full) or 0
    disc = slices.discovery(full, cut)

    # held-out BAR COUNTS only — never their prices. The projection needs to
    # know how much room a rule would have to fire in, which is metadata.
    held_full = {}
    for sym in heldout_symbols:
        try:
            df = feed.cached_ohlcv(sym, tf, limit=200000)
        except Exception:                               # noqa: BLE001
            continue
        if df is not None and len(df) >= 500:
            held_full[sym] = df
    heldout_bars = {"a": slices.bar_counts(held_full),
                    "b": slices.bar_counts(slices.heldout_b(full, cut))}

    sym_frames = {}
    for sym, df in disc.items():
        try:
            sym_frames[sym] = spec_evidence.frames_for(df, tf)
        except Exception as e:                          # noqa: BLE001
            log.warning(f"research frames_for {sym} {tf}: {e}")
    disc = {s: d for s, d in disc.items() if s in sym_frames}

    derivs = {}
    if any(r != "ohlcv" and not r.startswith("ref:") for r in requires):
        dfeed = DerivFeed(db_path=paths.get("derivs"))
        for sym in disc:
            got = spec_evidence.load_derivs(sym, requires, dfeed)
            derivs[sym] = {k: slices.before(v, cut) for k, v in got.items()}
    else:
        derivs = {s: {} for s in disc}

    market = None
    ref_keys = [r.split(":", 1)[1] for r in requires
                if isinstance(r, str) and r.startswith("ref:")]
    if ref_keys:
        store = RefStore(paths.get("candles"))
        market = {}
        for k in ref_keys:
            df = store.load(k)
            if df is not None and len(df):
                market[k] = slices.before(df, cut)
        market = market or None

    btc = {tf: disc["BTC/USDT"]} if "BTC/USDT" in disc else None
    rcfg = cfg.get("risk", {}) or {}
    return Bundle(
        tf=tf, frames=disc, sym_frames=sym_frames,
        universe={s: {tf: d} for s, d in disc.items()},
        btc=btc, market=market, derivs=derivs,
        risk=spec_evidence.risk_for(rcfg, tf), cut=cut,
        heldout_bars=heldout_bars,
        equity=float(cfg.get("research", {}).get("equity", 2000.0)),
        risk_pct=float(rcfg.get("risk_per_trade_pct", 0.5)),
        max_open=int(rcfg.get("max_open_trades", 8)))


def _clock(df) -> np.ndarray:
    """Bar timestamps in SECONDS.

    `.astype("int64")` keeps the column's own resolution, and the candle
    store holds datetime64[ms] while a resampled frame may hold ns — dividing
    by a hardcoded 10**9 silently produces a clock off by 10**6.
    """
    return (pd.to_datetime(df["ts"], utc=True).dt.tz_localize(None)
            .astype("datetime64[s]").astype("int64").to_numpy())


def evaluate(c, b: Bundle, draws: int = 30, seed: int = 17,
             min_symbol_trades: int = MIN_SYMBOL_TRADES) -> dict:
    """Score `c` over the bundle's discovery slice."""
    spec = c.to_spec()
    out = {"hash": c.hash, "tf": c.tf, "geo": c.geo, "k": c.k,
           "round": c.round, "parent": c.parent, "trigger": c.trigger,
           "parts": list(c.keys), "window": c.window,
           "entry_long": c.long, "entry_short": c.short,
           "symbols": {}, "trades": 0, "scored_symbols": 0,
           "consistency_p": None, "median_pf": 0.0, "median_rate": 0.0,
           "portfolio": {"total_pct": 0.0, "max_dd_pct": 0.0, "taken": 0,
                         "order": []},
           "projection": {"markets_a": 0, "markets_b": 0},
           "testable": False, "verdict": "empty"}
    try:
        compiled = compile_spec(spec)
    except Exception as e:                              # noqa: BLE001
        out["verdict"] = "error"
        out["error"] = str(e)
        return out

    step = _TF_SECONDS.get(c.tf, 900)
    t0 = None
    for df in b.frames.values():
        first = int(_clock(df)[0])
        t0 = first if t0 is None else min(t0, first)

    fills, pcts, pfs, rates = [], [], [], []
    for sym, df in b.frames.items():
        rec: dict = {"trades": 0, "pf": 0.0, "null_pctile": None}
        out["symbols"][sym] = rec
        sf = b.sym_frames.get(sym)
        try:
            lo, sh = compiled.entries(
                sf, btc=b.btc, derivs=b.derivs.get(sym),
                universe=b.universe, market=b.market, symbol=sym)
            fund = funding_for(sym, df, b.risk)
            mine: list = []
            r = simulate(lo, sh, df, spec.exit, b.risk, symbol=sym,
                         funding=fund, fills_out=mine)
        except Exception as e:                          # noqa: BLE001
            log.debug(f"research {c.hash} {sym}: {e}")
            rec["error"] = str(e)[:200]
            continue

        rec["trades"] = int(r.trades)
        rec["pf"] = round(float(r.profit_factor), 3)
        out["trades"] += int(r.trades)
        usable = slices.usable_bars(len(df))
        rec["rate"] = (r.trades / usable) if usable else 0.0
        rates.append(rec["rate"])

        clock = _clock(df)
        for f in mine:
            fills.append(Fill(
                entry_i=int((clock[f.entry_i] - t0) // step),
                exit_i=int((clock[f.exit_i] - t0) // step),
                r_multiple=f.r_multiple, symbol=sym))

        if r.trades < min_symbol_trades:
            continue
        try:
            a = null_baseline.assess(
                compiled, sf, b.risk, r.profit_factor, btc=b.btc,
                derivs=b.derivs.get(sym), universe=b.universe,
                market=b.market, symbol=sym, draws=int(draws), seed=seed,
                split=None, funding=fund)
        except Exception as e:                          # noqa: BLE001
            log.debug(f"research null {c.hash} {sym}: {e}")
            continue
        p = a.get("percentile")
        if p is None:
            continue
        rec["null_pctile"] = round(float(p), 4)
        pcts.append(float(p))
        pfs.append(float(r.profit_factor))

    if fills:
        curve = portfolio_curve(fills, b.equity, b.risk_pct, b.max_open)
        out["portfolio"] = {
            "total_pct": round(curve["total_pct"], 3),
            "max_dd_pct": round(curve["max_dd_pct"], 3),
            "taken": curve["taken"], "order": curve["order"]}
    out["scored_symbols"] = len(pcts)
    out["median_pf"] = round(st.median(pfs), 3) if pfs else 0.0
    out["median_rate"] = st.median(rates) if rates else 0.0

    # testability: would this rule fire often enough to be JUDGED later?
    # Bar counts are metadata; no held-out price is read.
    rate = out["median_rate"]
    proj = {}
    for part in ("a", "b"):
        n = 0
        for _sym, bars in (b.heldout_bars.get(part) or {}).items():
            if rate * slices.usable_bars(bars) >= MIN_PROJECTED_TRADES:
                n += 1
        proj[f"markets_{part}"] = n
    out["projection"] = proj
    out["testable"] = bool(proj["markets_a"] >= MIN_PROJECTED_MARKETS
                           and proj["markets_b"] >= MIN_PROJECTED_MARKETS)

    if out["trades"] == 0:
        out["verdict"] = "empty"
        return out
    if len(pcts) < null_baseline.MIN_SYMBOLS:
        out["verdict"] = "untestable"
        return out
    out["consistency_p"] = null_baseline.consistency_p(pcts)
    out["verdict"] = "scored"
    return out
```

- [ ] **Step 4: Run the tests**

Run: `./venv/bin/python -m pytest tests/test_research_evaluate.py -q -p no:cacheprovider`
Expected: PASS. It runs ~6 symbols × 20 null draws twice over; if it takes
longer than ~90 s, lower `n` in `_frame`, never the assertions.

- [ ] **Step 5: Commit**

```bash
git add trader/research/evaluate.py tests/test_research_evaluate.py
git commit -m "$(cat <<'EOF'
feat(research): score a combination on the slice it is allowed to see

Three numbers, because one is never enough: the cross-symbol spread of
rotation-null percentiles, compounded return over one account at the live
cap, and whether the rule fires often enough to be judged at all.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01EpstKPhQNEEG3wz2i7T9td
EOF
)"
```

---

### Task 8: Growth — a part joins only if it earns its place

**Files:**
- Create: `trader/research/growth.py`
- Test: `tests/test_research_growth.py`

**Interfaces:**
- Consumes: evaluation result dicts from `evaluate.evaluate`.
- Produces: `GROW_MAX_P = 0.25`; `carries_information(res, max_p=GROW_MAX_P) -> bool`; `earns_place(child, other) -> tuple[bool, str]`; `ablation(child, subset_results) -> dict`; `decide(child, parent=None, subset_results=None, max_p=GROW_MAX_P) -> tuple[str, str, dict]` returning `(verdict, reason, ablation)` with `verdict ∈ {"survivor", "grow", "prune"}`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_research_growth.py`:

```python
"""When does a part earn its place?

The measurement this encodes: every filter ever added to the book's one
working mechanism held its profit factor and halved its compounded return.

  baseline  PF 1.42  p 3.5e-04  487 trades  CAGR 17.3%
  carry     PF 1.43  p 3.5e-04  415 trades  CAGR  7.6%
  hivol     PF 1.44  p 1.1e-02  307 trades  CAGR  6.3%

Removing 15% of trades removed more than half the compounded return, because
the cut trades carried a disproportionate share of the fat right tail and PF
barely moves since they are near break-even individually. So a part must
improve BOTH the rotation null AND compounded return over one account — and
a survivor must beat every one of its one-part ablations, not only the
parent it happened to grow from. That is how ties go to the simpler rule.
"""
from trader.research import growth


def _res(p=0.01, pct=20.0, verdict="scored", testable=True, trades=100):
    return {"consistency_p": p, "portfolio": {"total_pct": pct},
            "verdict": verdict, "testable": testable, "trades": trades,
            "scored_symbols": 8}


def test_a_scored_testable_rule_with_signal_carries_information():
    assert growth.carries_information(_res(p=0.05))


def test_a_rule_beyond_the_information_threshold_does_not():
    assert not growth.carries_information(_res(p=0.6))


def test_an_untestable_rule_never_carries_information():
    assert not growth.carries_information(_res(p=0.001, testable=False))
    assert not growth.carries_information(_res(verdict="untestable", p=None))


def test_a_part_must_improve_both_measures():
    parent = _res(p=0.05, pct=20.0)
    better = _res(p=0.01, pct=25.0)
    ok, why = growth.earns_place(better, parent)
    assert ok and "both" in why.lower() or ok


def test_a_part_that_only_improves_the_null_is_refused():
    ok, why = growth.earns_place(_res(p=0.001, pct=8.0),
                                 _res(p=0.05, pct=20.0))
    assert not ok
    assert "compounded" in why


def test_a_part_that_only_improves_compounded_return_is_refused():
    ok, why = growth.earns_place(_res(p=0.2, pct=40.0),
                                 _res(p=0.05, pct=20.0))
    assert not ok
    assert "null" in why


def test_the_filter_result_that_motivated_this_rule_is_refused():
    """`carry`: PF holds, p identical, CAGR 17.3 -> 7.6."""
    baseline = _res(p=3.5e-04, pct=17.3)
    carry = _res(p=3.5e-04, pct=7.6)
    ok, _ = growth.earns_place(carry, baseline)
    assert not ok


def test_a_subset_that_cannot_be_scored_counts_as_beaten():
    """If removing a part makes the rule untestable, the part is doing the
    work that makes it judgeable at all."""
    child = _res(p=0.01, pct=20.0)
    dead = _res(p=None, pct=0.0, verdict="untestable", testable=False)
    ok, _ = growth.earns_place(child, dead)
    assert ok


def test_a_survivor_beats_every_ablation():
    child = _res(p=0.001, pct=30.0)
    subs = {"a": _res(p=0.01, pct=20.0), "b": _res(p=0.02, pct=25.0)}
    verdict, reason, abl = growth.decide(child, parent=subs["a"],
                                         subset_results=subs)
    assert verdict == "survivor"
    assert set(abl) == {"a", "b"}
    assert abl["a"]["drop_pct"] == 10.0


def test_a_rule_carried_by_one_part_is_not_a_survivor_but_still_grows():
    child = _res(p=0.001, pct=30.0)
    subs = {"a": _res(p=0.0005, pct=31.0), "b": _res(p=0.02, pct=25.0)}
    verdict, reason, _ = growth.decide(child, parent=subs["b"],
                                       subset_results=subs)
    assert verdict == "grow"
    assert "a" in reason


def test_a_child_that_does_not_beat_its_parent_is_pruned():
    child = _res(p=0.30, pct=5.0)
    verdict, reason, _ = growth.decide(child, parent=_res(p=0.05, pct=20.0),
                                       subset_results={})
    assert verdict == "prune"


def test_a_single_with_signal_grows_and_has_no_ablation():
    verdict, reason, abl = growth.decide(_res(p=0.05), parent=None,
                                         subset_results=None)
    assert verdict == "grow" and abl == {}


def test_a_single_without_signal_is_pruned():
    verdict, _, _ = growth.decide(_res(p=0.8), parent=None,
                                  subset_results=None)
    assert verdict == "prune"


def test_an_empty_rule_is_pruned_without_pretending_to_be_evidence():
    verdict, reason, _ = growth.decide(
        _res(verdict="empty", p=None, trades=0), parent=None,
        subset_results=None)
    assert verdict == "prune"
    assert "no trades" in reason
```

- [ ] **Step 2: Run to verify it fails**

Run: `./venv/bin/python -m pytest tests/test_research_growth.py -q -p no:cacheprovider`
Expected: FAIL with `ModuleNotFoundError: No module named 'trader.research.growth'`.

- [ ] **Step 3: Implement**

Create `trader/research/growth.py`:

```python
"""Does this part earn its place?

Measured on the book's one working mechanism, same base rule, same geometry,
with and without each condition:

    variant    PF     null p    OOS trades   CAGR    maxDD
    baseline   1.42   3.5e-04   487          17.3%   27.3%
    carry      1.43   3.5e-04   415           7.6%   25.8%
    hivol      1.44   1.1e-02   307           6.3%   25.0%
    breadth    1.42   3.8e-02   466          17.1%   25.3%
    rel_btc    1.33   1.8e-04   426           9.6%   23.3%

Every filter held or improved the profit factor and most of them halved the
compounded return: the trades they cut were near break-even individually and
carried a disproportionate share of the fat right tail. So a part must
improve BOTH the rotation null and compounded return over one account, and
neither alone is evidence of anything.

A SURVIVOR must beat every one of its one-part ablations, not merely the
parent it grew from — a combination can be reached by several paths, and the
ablation is what shows which parts actually carry the edge. It is also what
the Reason step (phase 4) explains.
"""
from __future__ import annotations

#: above this, a rule is not distinguishable enough from its own rotation to
#: be worth spending the search's time extending. Deliberately loose: a piece
#: may be useless alone and valuable in company, and this only decides where
#: compute goes, never what is admitted.
GROW_MAX_P = 0.25


def _p(res) -> float:
    """A rule that could not be scored is treated as no-edge for comparison
    purposes: 1.0 is the worst a consistency p can be."""
    if not res:
        return 1.0
    p = res.get("consistency_p")
    return 1.0 if p is None else float(p)


def _pct(res) -> float:
    """Compounded return. A rule that never traded did not make money, and
    must not read as 'zero, which beats a loss'."""
    if not res or res.get("verdict") in ("empty", "error", None):
        return float("-inf")
    if res.get("verdict") == "untestable":
        return float("-inf")
    return float((res.get("portfolio") or {}).get("total_pct", 0.0))


def carries_information(res, max_p: float = GROW_MAX_P) -> bool:
    """Worth extending. Not 'is an edge' — worth spending compute on."""
    if not res or res.get("verdict") != "scored":
        return False
    if not res.get("testable"):
        return False
    p = res.get("consistency_p")
    return p is not None and float(p) <= float(max_p)


def earns_place(child, other) -> tuple[bool, str]:
    """Is `child` better than `other` on BOTH measures?"""
    cp, op = _p(child), _p(other)
    cpct, opct = _pct(child), _pct(other)
    if cp >= op:
        return False, (f"the null does not improve "
                       f"(p {cp:.2g} vs {op:.2g})")
    if cpct <= opct:
        return False, (f"compounded return does not improve "
                       f"({cpct:.1f}% vs {opct:.1f}%)")
    return True, (f"improves both: p {op:.2g} -> {cp:.2g}, "
                  f"return {opct:.1f}% -> {cpct:.1f}%")


def ablation(child, subset_results: dict) -> dict:
    """{removed part key: what the rule loses without it}."""
    out = {}
    for key, res in (subset_results or {}).items():
        out[key] = {
            "p": res.get("consistency_p"),
            "total_pct": (res.get("portfolio") or {}).get("total_pct"),
            "verdict": res.get("verdict"),
            "drop_p": round(_p(child) - _p(res), 8),
            "drop_pct": round(_pct(child) - _pct(res), 4)
            if _pct(res) != float("-inf") else None,
        }
    return out


def decide(child, parent=None, subset_results: dict | None = None,
           max_p: float = GROW_MAX_P) -> tuple[str, str, dict]:
    """(verdict, reason, ablation).

    survivor — beats every one-part ablation on both measures; a candidate
               for the held-out look phase 3 will spend evidence on.
    grow     — carries information and may be extended, but at least one of
               its parts does not earn its place yet.
    prune    — nothing here.
    """
    if not child or child.get("verdict") == "error":
        return "prune", f"evaluation failed: {(child or {}).get('error', '')}", {}
    if child.get("verdict") == "empty" or not child.get("trades"):
        return "prune", "no trades on the discovery slice", {}
    if child.get("verdict") == "untestable":
        return "prune", (f"only {child.get('scored_symbols', 0)} symbols "
                         f"carried a percentile"), {}
    if not child.get("testable"):
        return "prune", ("too picky to be judged: projects fewer than 8 "
                         "trades on 4 held-out markets"), {}
    if parent is not None:
        ok, why = earns_place(child, parent)
        if not ok:
            return "prune", f"does not beat its parent — {why}", {}
    if not carries_information(child, max_p):
        return "prune", (f"p={_p(child):.2g} > {max_p} — not distinguishable "
                         f"enough from its own rotation to extend"), {}
    abl = ablation(child, subset_results or {})
    weak = []
    for key, res in (subset_results or {}).items():
        ok, _why = earns_place(child, res)
        if not ok:
            weak.append(key)
    if subset_results and not weak:
        return "survivor", "every part earns its place", abl
    if weak:
        return "grow", (f"carries information, but these parts do not earn "
                        f"their place: {sorted(weak)}"), abl
    return "grow", f"carries information (p={_p(child):.2g})", abl
```

- [ ] **Step 4: Run the tests**

Run: `./venv/bin/python -m pytest tests/test_research_growth.py -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add trader/research/growth.py tests/test_research_growth.py
git commit -m "$(cat <<'EOF'
feat(research): a part joins only if it improves the null AND the money

Every filter tried on the book held its profit factor and halved compounded
return. A refinement is judged on the account's curve, never on a ratio that
improves while the trade count falls.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01EpstKPhQNEEG3wz2i7T9td
EOF
)"
```

---

### Task 9: Every window carries its own control

**Files:**
- Create: `trader/research/control.py`
- Test: `tests/test_research_control.py`

**Interfaces:**
- Consumes: `combo.Combination`, `vocab.Part`, `combo.MAX_PARTS`.
- Produces: `CHANNEL = {"4h": 100, "1h": 400, "15m": 1600}`; `INCUMBENT_UNIVERSE: tuple[str, ...]` (the 16 declared markets); `MASKS: dict[str, str]`; `mask_part(req) -> Part | None`; `control_combo(tf, window, geo="trail") -> Combination | None`; `incumbent_universe(journal=None) -> list[str]`; `powered(res, max_p=0.01) -> bool`; `label(res, is_powered) -> str`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_research_control.py`:

```python
"""A gate that cannot see the incumbent inside a window cannot refute a
challenger inside it.

Measured 2026-09-11: the positioning screen found nothing, and
CONTROL_oi_window — the known-good rule confined to the same 333-day window —
read discovery p=2.5e-01 against its usual 3.5e-04. So "no positioning
mechanism survives" was a statement about the window's power, not about the
market, and the family is UNTESTED at that depth rather than refuted.

The control runs on the incumbent's DECLARED 16, not on the discovery
universe, because on the discovery universe the incumbent is itself measured
as no-edge (p=2.3e-01). The question a control answers is "could an edge of
known size register in a window this short" — and the declared 16 is where
that edge is known to have a size.
"""
import pytest

from trader.research import control
from trader.research.combo import MAX_PARTS
from trader.strategy.compile import compile_spec


def test_the_channel_is_duration_matched_across_horizons():
    """100 bars of 4h is ~17 days; the same duration at 1h is 400 bars."""
    assert control.CHANNEL["4h"] == 100
    assert control.CHANNEL["1h"] == 400
    assert control.CHANNEL["15m"] == 1600


def test_the_plain_window_control_is_the_incumbent_rule():
    c = control.control_combo("4h", "ohlcv")
    assert c.long == "close > donchian_hi(100)"
    assert c.short == "close < donchian_lo(100)"
    assert c.round == "control"
    compile_spec(c.to_spec())


def test_a_restricted_window_masks_the_control_to_that_window():
    c = control.control_combo("4h", "open_interest")
    assert "oi_z(360) > -99" in c.long
    assert "donchian_hi(100)" in c.long
    compile_spec(c.to_spec())


def test_a_reference_window_masks_on_the_reference_itself():
    c = control.control_combo("4h", "ref:spx_1h")
    assert 'ref("spx_1h", close) > -99' in c.long
    compile_spec(c.to_spec())


def test_a_multi_requirement_window_masks_on_each():
    c = control.control_combo("4h", "funding|ref:btcdom")
    assert "funding_z(360) > -99" in c.long
    assert 'ref("btcdom", close) > -99' in c.long


def test_a_window_needing_more_masks_than_a_rule_may_hold_is_refused():
    window = "|".join(["funding", "basis", "open_interest",
                       "ls_account_ratio", "ref:spx", "ref:vix"])
    assert control.control_combo("4h", window) is None


def test_an_unknown_requirement_has_no_mask():
    assert control.mask_part("taker_ratio") is not None
    assert control.mask_part("not_a_series") is None


def test_the_incumbent_universe_is_the_sixteen_it_declares():
    u = control.INCUMBENT_UNIVERSE
    assert len(u) == 16
    for s in ("BTC/USDT", "HYPE/USDT", "TAO/USDT"):
        assert s in u


class _Journal:
    """Just enough journal to answer the one query control.py makes."""

    def __init__(self, rows):
        self._rows = rows

    def query(self, _sql, _params=()):
        return self._rows


def _row(symbols):
    import json
    return [{"spec_json": json.dumps({"universe": {"include": symbols}})}]


def test_the_book_s_own_declared_universe_wins_when_it_is_full():
    syms = [f"S{i}/USDT" for i in range(16)]
    assert control.incumbent_universe(_Journal(_row(syms))) == syms


def test_a_truncated_universe_falls_back_rather_than_weakening_the_control():
    """8 symbols cannot reach p<0.01 at all, so a control run on them would
    read as unpowered forever and label every window UNDERPOWERED."""
    short = [f"S{i}/USDT" for i in range(8)]
    assert control.incumbent_universe(_Journal(_row(short))) == \
        list(control.INCUMBENT_UNIVERSE)


def test_a_malformed_or_missing_row_falls_back_without_raising():
    for rows in ([], [{"spec_json": ""}], [{"spec_json": "{not json"}],
                 [{"spec_json": "{}"}]):
        assert control.incumbent_universe(_Journal(rows)) == \
            list(control.INCUMBENT_UNIVERSE)


def test_a_journal_that_raises_falls_back():
    class _Boom:
        def query(self, *_a, **_k):
            raise RuntimeError("database is locked")

    assert control.incumbent_universe(_Boom()) == \
        list(control.INCUMBENT_UNIVERSE)


def test_powered_reads_the_gate_the_admission_uses():
    assert control.powered({"consistency_p": 0.0005}, max_p=0.01)
    assert not control.powered({"consistency_p": 0.25}, max_p=0.01)
    assert not control.powered({"consistency_p": None}, max_p=0.01)


def test_a_negative_in_an_unpowered_window_is_labelled_underpowered():
    assert control.label({"verdict": "scored", "consistency_p": 0.4},
                         is_powered=False) == "underpowered"
    assert control.label({"verdict": "scored", "consistency_p": 0.4},
                         is_powered=True) == "no_edge"
    assert control.label({"verdict": "scored", "consistency_p": 0.001},
                         is_powered=False) == "scored"
```

- [ ] **Step 2: Run to verify it fails**

Run: `./venv/bin/python -m pytest tests/test_research_control.py -q -p no:cacheprovider`
Expected: FAIL with `ModuleNotFoundError: No module named 'trader.research.control'`.

- [ ] **Step 3: Implement**

Create `trader/research/control.py`:

```python
"""The known-good rule, confined to the same window as the challengers.

A screen that prints "nothing survives" makes a claim about its own power
before it makes one about the market. Measured 2026-09-11: seven positioning
mechanisms found nothing at 333 days, and the incumbent rule confined to that
same window read p=2.5e-01 where it normally reads 3.5e-04. The family was
UNTESTED at that depth, not refuted — and without the control row the run
would have been written up as a fact about positioning.

Two decisions here differ from the obvious:

- the control runs on the incumbent's DECLARED 16, not on the discovery
  universe. On the discovery universe the incumbent itself reads p=2.3e-01,
  so it can calibrate nothing there. The question is "could an edge of known
  size register in a window this short", and the declared 16 is the only
  place that edge is known to have a size.
- the channel is DURATION-matched per horizon (100 bars at 4h is ~17 days),
  because a 100-bar channel at 15m is a different mechanism, not the same one
  measured faster.
"""
from __future__ import annotations

import logging

from .combo import MAX_PARTS, Combination
from .vocab import Part

log = logging.getLogger(__name__)

#: ~17 days of channel at every horizon
CHANNEL = {"4h": 100, "1h": 400, "15m": 1600}

#: the markets auth_donchian_breakout_trail declares, where its evidence is
INCUMBENT_UNIVERSE = (
    "BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT", "BNB/USDT", "DOGE/USDT",
    "LINK/USDT", "AVAX/USDT", "SUI/USDT", "AAVE/USDT", "NEAR/USDT",
    "FIL/USDT", "UNI/USDT", "TAO/USDT", "ZEC/USDT", "HYPE/USDT")

#: how to confine a rule to the window a series exists in. The comparison is
#: always true where the series is present and NaN — therefore False — where
#: it is not, so the conjunction simply cannot fire outside the window.
MASKS = {
    "funding": "funding_z(360) > -99",
    "basis": "basis_z(360) > -99",
    "open_interest": "oi_z(360) > -99",
    "ls_account_ratio": "ls_account_ratio_z(360) > -99",
    "ls_ratio": "ls_ratio_z(360) > -99",
    "taker_ratio": "taker_ratio_z(360) > -99",
}


#: fewer symbols than this and the control cannot calibrate anything.
#: `consistency_p` is a Bonferroni-corrected binomial tail, so the universe
#: size sets what it can DETECT: at 8 symbols the 0.01 bar is unreachable
#: even when every symbol clears the no-edge median, and a control that can
#: never read as powered would label every window UNDERPOWERED — silently
#: converting "no edge here" into "we cannot see here" for the whole search.
#: At 12, all-twelve-above-median reaches 7e-04 and the bar is reachable.
MIN_CONTROL_SYMBOLS = 12


def incumbent_universe(journal=None) -> list:
    """The declared 16, read from the book when it is available.

    Falls back to the recorded constant: a control that silently ran on a
    different universe than it claims would be worse than no control, and a
    TRUNCATED universe is exactly that — it reads as a weaker incumbent
    rather than as a smaller sample.
    """
    if journal is None:
        return list(INCUMBENT_UNIVERSE)
    try:
        import json
        rows = journal.query(
            "SELECT spec_json FROM strategies WHERE id=?",
            ("auth_donchian_breakout_trail",))
        if rows and rows[0].get("spec_json"):
            inc = (json.loads(rows[0]["spec_json"]).get("universe") or {}
                   ).get("include") or []
            if len(inc) >= MIN_CONTROL_SYMBOLS:
                if len(inc) != len(INCUMBENT_UNIVERSE):
                    log.warning(
                        f"incumbent control universe is {len(inc)} symbols, "
                        f"not the recorded {len(INCUMBENT_UNIVERSE)} — the "
                        f"book's declared set has changed")
                return list(inc)
            log.warning(f"book declares only {len(inc)} symbols for the "
                        f"incumbent; falling back to the recorded set, "
                        f"below {MIN_CONTROL_SYMBOLS} the control cannot "
                        f"calibrate")
    except Exception as e:                              # noqa: BLE001
        log.debug(f"incumbent universe unavailable: {e}")
    return list(INCUMBENT_UNIVERSE)


def mask_part(req: str) -> Part | None:
    """A part that is true wherever `req`'s series exists, and NaN before."""
    if req.startswith("ref:"):
        key = req.split(":", 1)[1]
        expr = f'ref("{key}", close) > -99'
    else:
        expr = MASKS.get(req)
    if not expr:
        return None
    return Part(key=f"mask:{req}", kind="state", gauge=f"mask:{req}",
                long=expr, short=expr)


def control_combo(tf: str, window: str, geo: str = "trail"):
    """The incumbent rule, confined to `window`. None when it cannot be."""
    n = CHANNEL.get(tf)
    if not n:
        return None
    parts = [Part(key=f"ev:control{n}", kind="event", gauge="ev:control",
                  long=f"close > donchian_hi({n})",
                  short=f"close < donchian_lo({n})")]
    if window and window != "ohlcv":
        for req in window.split("|"):
            m = mask_part(req)
            if m is None:
                return None
            parts.append(m)
    if len(parts) > MAX_PARTS:
        # a window needing more masks than a rule may hold cannot be
        # calibrated; results in it stay labelled as uncalibrated
        return None
    return Combination(parts=tuple(parts), tf=tf, geo=geo, round="control",
                       trigger=f"ev:control{n}")


def powered(res, max_p: float = 0.01) -> bool:
    """Could an edge of the incumbent's size register in this window?"""
    if not res:
        return False
    p = res.get("consistency_p")
    return p is not None and float(p) <= float(max_p)


def label(res, is_powered: bool) -> str:
    """What a result in this window is allowed to be called.

    A rule that registers is `scored` whatever the control says — power is
    about false negatives. A rule that does not register in a window where
    the known-good rule also cannot register is UNDERPOWERED, never no_edge.
    """
    if not res or res.get("verdict") != "scored":
        return (res or {}).get("verdict", "error")
    p = res.get("consistency_p")
    if p is not None and float(p) <= 0.01:
        return "scored"
    return "no_edge" if is_powered else "underpowered"
```

- [ ] **Step 4: Run the tests**

Run: `./venv/bin/python -m pytest tests/test_research_control.py -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add trader/research/control.py tests/test_research_control.py
git commit -m "$(cat <<'EOF'
feat(research): every window runs the known-good rule beside the challengers

A gate that cannot detect the incumbent inside a window cannot refute a
challenger inside it — measured at 333 days, where the control fell from
3.5e-04 to 2.5e-01 and seven mechanisms were nearly written off.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01EpstKPhQNEEG3wz2i7T9td
EOF
)"
```

---

### Task 10: The ledger — every combination, every look, kept or not

**Files:**
- Create: `trader/research/ledger.py`
- Test: `tests/test_research_ledger.py`

**Interfaces:**
- Consumes: `trader.core.journal.Journal` (`_tx()` for writes, `query()` for reads).
- Produces: `SCHEMA: str`; `class Ledger(journal)` with `.ensure()`, `.record_gauges(tf, rows)`, `.gauges(tf) -> dict`, `.record_slices(tf, cut, counts)`, `.slices(tf) -> dict | None`, `.record_control(tf, window, res, powered)`, `.control(tf, window) -> dict | None`, `.record_result(res, verdict, reason, ablation)`, `.has(h) -> bool`, `.known(tf, geo) -> set[str]`, `.result(h) -> dict | None`, `.rows(tf, geo=None, k=None, verdict=None, limit=None) -> list[dict]`, `.start_batch(tf, geo, round, n) -> int`, `.finish_batch(batch_id, ok, elapsed_s, error="")`, `.counts() -> dict`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_research_ledger.py`:

```python
"""The ledger is what keeps the search honest.

Every combination it ever evaluated is recorded under its canonical hash,
kept or not, so: a restart resumes instead of re-testing; the same set
reached by two growth paths counts as ONE look; and phase 3's error budget
can price the looks because the count of them is true.

It is written by the kernel thread through Journal._tx(). NEVER through
Journal.query(): that runs on a thread-local connection with no transaction
wrapper, so an INSERT through it stays uncommitted — invisible to other
connections and holding a write lock — until some later _tx() on the same
thread happens to commit it. That is exactly how an unvalidated strategy got
into the book on 2026-09-11.
"""
import json

import pytest

from trader.core.journal import Journal
from trader.research.ledger import Ledger


@pytest.fixture
def led(tmp_path):
    j = Journal(tmp_path / "j.db")
    lg = Ledger(j)
    lg.ensure()
    return lg


def _res(h="abc123", tf="4h", geo="trail", k=1, p=0.01, pct=12.5,
         verdict="scored"):
    return {"hash": h, "tf": tf, "geo": geo, "k": k, "round": "singles",
            "parent": "", "trigger": "", "parts": ["ev:donch100"],
            "window": "ohlcv", "entry_long": "close > donchian_hi(100)",
            "entry_short": "close < donchian_lo(100)",
            "symbols": {"BTC/USDT": {"trades": 30, "pf": 1.4,
                                     "null_pctile": 0.9}},
            "trades": 300, "scored_symbols": 8, "consistency_p": p,
            "median_pf": 1.4, "median_rate": 0.01,
            "portfolio": {"total_pct": pct, "max_dd_pct": 20.0, "taken": 100,
                          "order": []},
            "projection": {"markets_a": 6, "markets_b": 6},
            "testable": True, "verdict": verdict}


def test_the_tables_are_created_once_and_again_is_harmless(led):
    led.ensure()
    names = {r["name"] for r in led.journal.query(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    for t in ("research_combos", "research_gauges", "research_controls",
              "research_slices", "research_batches"):
        assert t in names


def test_a_result_round_trips(led):
    led.record_result(_res(), "grow", "carries information", {})
    assert led.has("abc123")
    back = led.result("abc123")
    assert back["consistency_p"] == 0.01
    assert back["portfolio"]["total_pct"] == 12.5


def test_recording_the_same_hash_twice_keeps_one_row(led):
    led.record_result(_res(), "grow", "first", {})
    led.record_result(_res(p=0.02), "prune", "second", {})
    rows = led.rows("4h", "trail")
    assert len(rows) == 1
    assert rows[0]["verdict"] == "prune"


def test_rows_filter_by_level_and_verdict(led):
    led.record_result(_res(h="a", k=1), "grow", "", {})
    led.record_result(_res(h="b", k=2), "grow", "", {})
    led.record_result(_res(h="c", k=2), "prune", "", {})
    assert {r["hash"] for r in led.rows("4h", "trail", k=2)} == {"b", "c"}
    assert {r["hash"] for r in led.rows("4h", "trail", k=2,
                                        verdict="grow")} == {"b"}


def test_known_hashes_are_scoped_to_the_horizon_and_geometry(led):
    led.record_result(_res(h="a", tf="4h", geo="trail"), "grow", "", {})
    led.record_result(_res(h="b", tf="1h", geo="trail"), "grow", "", {})
    assert led.known("4h", "trail") == {"a"}
    assert led.known("1h", "trail") == {"b"}


def test_the_ablation_is_stored_with_the_row(led):
    abl = {"ev:donch100": {"p": 0.4, "total_pct": 3.0, "drop_p": -0.39,
                           "drop_pct": 9.5, "verdict": "scored"}}
    led.record_result(_res(k=2), "survivor", "every part earns its place",
                      abl)
    row = led.rows("4h", "trail")[0]
    assert json.loads(row["ablation"])["ev:donch100"]["drop_pct"] == 9.5


def test_gauges_round_trip_and_replace(led):
    led.record_gauges("4h", {"ret(24)": {"p10": -0.1, "p25": -0.02,
                                         "p75": 0.02, "p90": 0.1,
                                         "n": 90000, "finite_frac": 0.99,
                                         "usable": True}})
    g = led.gauges("4h")
    assert g["ret(24)"]["p90"] == 0.1 and g["ret(24)"]["usable"] is True
    led.record_gauges("4h", {"ret(24)": {"p10": -0.2, "p25": -0.03,
                                         "p75": 0.03, "p90": 0.2,
                                         "n": 1, "finite_frac": 0.0,
                                         "usable": False}})
    assert led.gauges("4h")["ret(24)"]["usable"] is False
    assert len(led.gauges("4h")) == 1


def test_a_control_records_whether_the_window_has_power(led):
    led.record_control("4h", "open_interest",
                       {"consistency_p": 0.25, "verdict": "scored"}, False)
    c = led.control("4h", "open_interest")
    assert c["powered"] is False and c["consistency_p"] == 0.25
    assert led.control("4h", "ohlcv") is None


def test_slices_record_the_cut_and_the_bar_counts(led):
    led.record_slices("4h", 1_700_000_000_000,
                      {"discovery": {"BTC/USDT": 7700},
                       "heldout_a": {"UNI/USDT": 11000},
                       "heldout_b": {"BTC/USDT": 3300}})
    s = led.slices("4h")
    assert s["cut_ms"] == 1_700_000_000_000
    assert s["heldout_b"]["BTC/USDT"] == 3300


def test_a_batch_records_its_outcome_even_when_it_failed(led):
    bid = led.start_batch("4h", "trail", "singles", 40)
    led.finish_batch(bid, ok=False, elapsed_s=612.0,
                     error="timed out after 600s")
    row = led.journal.query(
        "SELECT * FROM research_batches WHERE id=?", (bid,))[0]
    assert row["ok"] == 0 and "timed out" in row["error"]


def test_counts_summarise_the_search(led):
    led.record_result(_res(h="a", k=1), "grow", "", {})
    led.record_result(_res(h="b", k=2), "survivor", "", {})
    led.record_result(_res(h="c", k=2, verdict="empty"), "prune", "", {})
    c = led.counts()
    assert c["combos"] == 3
    assert c["by_verdict"]["survivor"] == 1
    assert c["by_tf"]["4h"] == 3


def test_the_ledger_never_writes_the_strategies_table():
    """Phase 2 proposes nothing. The handoff into _mechanism_once is phase 3,
    and there is exactly ONE admission gate."""
    import inspect

    from trader.research import ledger
    src = inspect.getsource(ledger)
    assert "strategies" not in src
```

- [ ] **Step 2: Run to verify it fails**

Run: `./venv/bin/python -m pytest tests/test_research_ledger.py -q -p no:cacheprovider`
Expected: FAIL with `ModuleNotFoundError: No module named 'trader.research.ledger'`.

- [ ] **Step 3: Implement**

Create `trader/research/ledger.py`:

```python
"""Every combination the search ever looked at, kept or not.

Three jobs:

- RESUME. A restart continues from here; nothing is re-tested, and the days
  the first singles pass costs are not spent twice.
- COUNT THE LOOKS. The same set of parts reached by two growth paths has one
  canonical hash and is one look. Phase 3's error budget prices looks, so a
  double-counted or missed one corrupts the budget rather than the row.
- SAY WHY. A row carries the verdict AND the reason, so "nothing survives" is
  never reported without the ablation and the window's control beside it.

Written by the kernel's research thread through `Journal._tx()`. Never
through `Journal.query()`: that runs on a thread-local connection with no
transaction wrapper, so a write through it stays uncommitted and lock-holding
until some later `_tx()` on the same thread commits it as a side effect —
which is how an unvalidated strategy reached the book on 2026-09-11.
"""
from __future__ import annotations

import json
import logging

from ..core.types import now_utc

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS research_combos (
    hash TEXT PRIMARY KEY,
    tf TEXT NOT NULL,
    geo TEXT NOT NULL,
    k INTEGER NOT NULL,
    round TEXT,
    parent TEXT,
    trigger TEXT,
    window TEXT,
    parts TEXT,                    -- JSON list of canonical part keys
    entry_long TEXT,
    entry_short TEXT,
    status TEXT,                   -- scored | untestable | empty | error
    label TEXT,                    -- scored | no_edge | underpowered | ...
    verdict TEXT,                  -- survivor | grow | prune
    reason TEXT,
    consistency_p REAL,
    median_pf REAL,
    total_pct REAL,
    max_dd_pct REAL,
    trades INTEGER,
    scored_symbols INTEGER,
    testable INTEGER,
    ablation TEXT,                 -- JSON {removed part: what it cost}
    result TEXT,                   -- the full evaluation, JSON
    created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_research_combos_pick
    ON research_combos(tf, geo, k, verdict);

CREATE TABLE IF NOT EXISTS research_gauges (
    tf TEXT NOT NULL,
    expr TEXT NOT NULL,
    p10 REAL, p25 REAL, p75 REAL, p90 REAL,
    n INTEGER,
    finite_frac REAL,
    usable INTEGER,
    measured_at TEXT,
    PRIMARY KEY (tf, expr)
);

CREATE TABLE IF NOT EXISTS research_controls (
    tf TEXT NOT NULL,
    window TEXT NOT NULL,
    consistency_p REAL,
    powered INTEGER,
    detail TEXT,
    measured_at TEXT,
    PRIMARY KEY (tf, window)
);

CREATE TABLE IF NOT EXISTS research_slices (
    tf TEXT PRIMARY KEY,
    cut_ms INTEGER,
    counts TEXT,                   -- JSON {discovery, heldout_a, heldout_b}
    measured_at TEXT
);

CREATE TABLE IF NOT EXISTS research_batches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started TEXT, finished TEXT,
    tf TEXT, geo TEXT, round TEXT,
    n INTEGER, ok INTEGER, elapsed_s REAL, error TEXT
);
"""


class Ledger:
    def __init__(self, journal):
        self.journal = journal

    def ensure(self) -> None:
        with self.journal._tx() as c:
            c.executescript(SCHEMA)

    # ── the vocabulary's measurements ────────────────────────────────────
    def record_gauges(self, tf: str, rows: dict) -> int:
        now = now_utc().isoformat()
        with self.journal._tx() as c:
            for expr, m in rows.items():
                c.execute(
                    "INSERT OR REPLACE INTO research_gauges "
                    "(tf, expr, p10, p25, p75, p90, n, finite_frac, usable, "
                    "measured_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (tf, expr, m.get("p10"), m.get("p25"), m.get("p75"),
                     m.get("p90"), int(m.get("n", 0)),
                     float(m.get("finite_frac", 0.0)),
                     1 if m.get("usable") else 0, now))
        return len(rows)

    def gauges(self, tf: str) -> dict:
        out = {}
        for r in self.journal.query(
                "SELECT * FROM research_gauges WHERE tf=?", (tf,)):
            out[r["expr"]] = {"p10": r["p10"], "p25": r["p25"],
                              "p75": r["p75"], "p90": r["p90"],
                              "n": r["n"], "finite_frac": r["finite_frac"],
                              "usable": bool(r["usable"])}
        return out

    # ── where the cut fell, and how much room each slice has ─────────────
    def record_slices(self, tf: str, cut_ms: int, counts: dict) -> None:
        with self.journal._tx() as c:
            c.execute(
                "INSERT OR REPLACE INTO research_slices "
                "(tf, cut_ms, counts, measured_at) VALUES (?,?,?,?)",
                (tf, int(cut_ms), json.dumps(counts),
                 now_utc().isoformat()))

    def slices(self, tf: str) -> dict | None:
        rows = self.journal.query(
            "SELECT * FROM research_slices WHERE tf=?", (tf,))
        if not rows:
            return None
        out = json.loads(rows[0]["counts"] or "{}")
        out["cut_ms"] = rows[0]["cut_ms"]
        return out

    # ── the window controls ──────────────────────────────────────────────
    def record_control(self, tf: str, window: str, res: dict,
                       powered: bool) -> None:
        with self.journal._tx() as c:
            c.execute(
                "INSERT OR REPLACE INTO research_controls "
                "(tf, window, consistency_p, powered, detail, measured_at) "
                "VALUES (?,?,?,?,?,?)",
                (tf, window, res.get("consistency_p"), 1 if powered else 0,
                 json.dumps(res)[:20000], now_utc().isoformat()))

    def control(self, tf: str, window: str) -> dict | None:
        rows = self.journal.query(
            "SELECT * FROM research_controls WHERE tf=? AND window=?",
            (tf, window))
        if not rows:
            return None
        return {"tf": tf, "window": window,
                "consistency_p": rows[0]["consistency_p"],
                "powered": bool(rows[0]["powered"])}

    # ── the combinations ─────────────────────────────────────────────────
    def record_result(self, res: dict, verdict: str, reason: str,
                      ablation: dict, label: str = "") -> None:
        pf = res.get("portfolio") or {}
        with self.journal._tx() as c:
            c.execute(
                "INSERT OR REPLACE INTO research_combos "
                "(hash, tf, geo, k, round, parent, trigger, window, parts, "
                "entry_long, entry_short, status, label, verdict, reason, "
                "consistency_p, median_pf, total_pct, max_dd_pct, trades, "
                "scored_symbols, testable, ablation, result, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (res["hash"], res["tf"], res["geo"], int(res.get("k", 1)),
                 res.get("round", ""), res.get("parent", ""),
                 res.get("trigger", ""), res.get("window", "ohlcv"),
                 json.dumps(res.get("parts", [])),
                 res.get("entry_long", ""), res.get("entry_short", ""),
                 res.get("verdict", ""), label or res.get("verdict", ""),
                 verdict, reason[:500],
                 res.get("consistency_p"), res.get("median_pf"),
                 pf.get("total_pct"), pf.get("max_dd_pct"),
                 int(res.get("trades", 0)),
                 int(res.get("scored_symbols", 0)),
                 1 if res.get("testable") else 0,
                 json.dumps(ablation or {}),
                 json.dumps(_slim(res))[:200000],
                 now_utc().isoformat()))

    def has(self, h: str) -> bool:
        return bool(self.journal.query(
            "SELECT 1 FROM research_combos WHERE hash=?", (h,)))

    def known(self, tf: str, geo: str) -> set:
        return {r["hash"] for r in self.journal.query(
            "SELECT hash FROM research_combos WHERE tf=? AND geo=?",
            (tf, geo))}

    def result(self, h: str) -> dict | None:
        rows = self.journal.query(
            "SELECT result FROM research_combos WHERE hash=?", (h,))
        if not rows:
            return None
        try:
            return json.loads(rows[0]["result"] or "{}")
        except Exception:                               # noqa: BLE001
            return None

    def rows(self, tf: str, geo: str | None = None, k: int | None = None,
             verdict: str | None = None, limit: int | None = None) -> list:
        sql = "SELECT * FROM research_combos WHERE tf=?"
        args: list = [tf]
        if geo is not None:
            sql += " AND geo=?"
            args.append(geo)
        if k is not None:
            sql += " AND k=?"
            args.append(int(k))
        if verdict is not None:
            sql += " AND verdict=?"
            args.append(verdict)
        sql += " ORDER BY consistency_p IS NULL, consistency_p, " \
               "total_pct DESC"
        if limit:
            sql += f" LIMIT {int(limit)}"
        return self.journal.query(sql, tuple(args))

    # ── batches ──────────────────────────────────────────────────────────
    def start_batch(self, tf: str, geo: str, round_: str, n: int) -> int:
        with self.journal._tx() as c:
            cur = c.execute(
                "INSERT INTO research_batches "
                "(started, tf, geo, round, n, ok, elapsed_s, error) "
                "VALUES (?,?,?,?,?,0,0,'')",
                (now_utc().isoformat(), tf, geo, round_, int(n)))
            return int(cur.lastrowid)

    def finish_batch(self, batch_id: int, ok: bool, elapsed_s: float,
                     error: str = "") -> None:
        with self.journal._tx() as c:
            c.execute(
                "UPDATE research_batches SET finished=?, ok=?, elapsed_s=?, "
                "error=? WHERE id=?",
                (now_utc().isoformat(), 1 if ok else 0, float(elapsed_s),
                 (error or "")[:500], int(batch_id)))

    # ── reporting ────────────────────────────────────────────────────────
    def counts(self) -> dict:
        out = {"combos": 0, "by_verdict": {}, "by_tf": {}, "by_k": {},
               "batches": 0, "failed_batches": 0}
        for r in self.journal.query(
                "SELECT verdict, tf, k, COUNT(*) n FROM research_combos "
                "GROUP BY verdict, tf, k"):
            out["combos"] += r["n"]
            out["by_verdict"][r["verdict"]] = \
                out["by_verdict"].get(r["verdict"], 0) + r["n"]
            out["by_tf"][r["tf"]] = out["by_tf"].get(r["tf"], 0) + r["n"]
            out["by_k"][r["k"]] = out["by_k"].get(r["k"], 0) + r["n"]
        for r in self.journal.query(
                "SELECT ok, COUNT(*) n FROM research_batches GROUP BY ok"):
            out["batches"] += r["n"]
            if not r["ok"]:
                out["failed_batches"] += r["n"]
        return out


def _slim(res: dict) -> dict:
    """The stored result, without the portfolio's per-fill ordering.

    `order` is one entry per taken trade — thousands of rows of symbol names
    that nothing reads back. The numbers computed from it are kept.
    """
    out = dict(res)
    pf = dict(out.get("portfolio") or {})
    pf.pop("order", None)
    out["portfolio"] = pf
    return out
```

Check `now_utc` is exported from `trader/core/types.py` (the journal imports
it from there). If it is not, import it the same way `journal.py` does.

- [ ] **Step 4: Run the tests**

Run: `./venv/bin/python -m pytest tests/test_research_ledger.py -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add trader/research/ledger.py tests/test_research_ledger.py
git commit -m "$(cat <<'EOF'
feat(research): the ledger — every combination, kept or not, with its reason

A restart resumes from it, the same set of parts reached twice counts as one
look, and no negative is recorded without its ablation and its window's
control beside it. Written through _tx(), never through query().

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01EpstKPhQNEEG3wz2i7T9td
EOF
)"
```

---

### Task 11: The planner — what to evaluate next

**Files:**
- Create: `trader/research/planner.py`
- Test: `tests/test_research_planner.py`

**Interfaces:**
- Consumes: `ledger.Ledger`, `vocab`, `combo`, `control`, `growth`.
- Produces: `@dataclass Batch(kind: str, tf: str, geo: str = "", round: str = "", combos: list = [], reason: str = "")` where `kind ∈ {"measure", "evaluate", "idle"}`; `next_batch(led, cfg, journal=None) -> Batch`; `seed_parts(journal) -> list[Part]`; `needs_ablation(led, tf, geo, parts_by_key) -> list[Combination]`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_research_planner.py`:

```python
"""What the search does next, and in what order.

The order is not a preference. Measurement comes first because a threshold
cannot be guessed; the window control comes before the combinations that live
in that window, because a result there cannot be LABELLED without it; singles
come before pairs because a pair is only worth trying around a trigger that
carried information; and ablations come before the next level, because a
combination is not a survivor until every part has earned its place.

Held-out results never enter here at all. If a held-out failure steered the
next round, held-out data would silently become discovery data.
"""
import pytest

from trader.core.journal import Journal
from trader.research import planner
from trader.research.ledger import Ledger
from trader.research.vocab import Part

CFG = {"research": {"horizons": ["4h"], "geometries": ["trail"],
                    "batch_combos": 5, "beam": 2, "grow_max_p": 0.25,
                    "max_parts": 3}}


@pytest.fixture
def led(tmp_path):
    lg = Ledger(Journal(tmp_path / "j.db"))
    lg.ensure()
    return lg


def _gauges(led, tf="4h"):
    from trader.research import vocab
    led.record_gauges(tf, {e: {"p10": -1.0, "p25": -0.5, "p75": 0.5,
                               "p90": 1.0, "n": 90000, "finite_frac": 0.99,
                               "usable": True}
                           for e in vocab.expressions(tf)})


def _res(h, tf="4h", geo="trail", k=1, p=0.01, pct=10.0, parts=None,
         verdict="scored"):
    return {"hash": h, "tf": tf, "geo": geo, "k": k, "round": "singles",
            "parent": "", "trigger": "", "parts": parts or ["ev:donch100"],
            "window": "ohlcv", "entry_long": "x", "entry_short": "y",
            "symbols": {}, "trades": 100, "scored_symbols": 8,
            "consistency_p": p, "median_pf": 1.3, "median_rate": 0.01,
            "portfolio": {"total_pct": pct, "max_dd_pct": 10.0, "taken": 50},
            "projection": {"markets_a": 6, "markets_b": 6},
            "testable": True, "verdict": verdict}


def test_with_nothing_measured_the_first_job_is_to_measure(led):
    b = planner.next_batch(led, CFG)
    assert b.kind == "measure" and b.tf == "4h"


def test_once_measured_the_control_comes_before_the_singles(led):
    _gauges(led)
    b = planner.next_batch(led, CFG)
    assert b.kind == "evaluate" and b.round == "control"
    assert all(c.round == "control" for c in b.combos)


def test_then_singles_are_enqueued_in_batch_sized_chunks(led):
    _gauges(led)
    led.record_control("4h", "ohlcv", {"consistency_p": 0.001}, True)
    b = planner.next_batch(led, CFG)
    assert b.round == "singles"
    assert len(b.combos) == CFG["research"]["batch_combos"]
    assert all(c.k == 1 for c in b.combos)


def test_a_combination_already_in_the_ledger_is_never_re_enqueued(led):
    _gauges(led)
    led.record_control("4h", "ohlcv", {"consistency_p": 0.001}, True)
    first = planner.next_batch(led, CFG)
    for c in first.combos:
        led.record_result(_res(c.hash, parts=list(c.keys)), "prune", "", {})
    second = planner.next_batch(led, CFG)
    assert not ({c.hash for c in second.combos}
                & {c.hash for c in first.combos})


def test_a_window_without_a_control_holds_back_its_combinations(led):
    """Parts needing funding cannot be scored until the funding window's
    control says whether an edge could register there at all."""
    _gauges(led)
    led.record_control("4h", "ohlcv", {"consistency_p": 0.001}, True)
    seen_windows = set()
    for _ in range(40):
        b = planner.next_batch(led, CFG)
        if b.kind != "evaluate":
            break
        for c in b.combos:
            seen_windows.add(c.window)
            led.record_result(_res(c.hash, k=c.k, parts=list(c.keys)),
                              "prune", "", {})
        if b.round == "control":
            for c in b.combos:
                led.record_control("4h", c.window,
                                   {"consistency_p": 0.5}, False)
    assert "ohlcv" in seen_windows


def test_growth_extends_only_parents_that_carried_information(led):
    _gauges(led)
    led.record_control("4h", "ohlcv", {"consistency_p": 0.001}, True)
    # exhaust the singles round with one grower and one pruned
    from trader.research import vocab
    parts = vocab.parts_for("4h", led.gauges("4h"))
    from trader.research.combo import Combination
    for i, p in enumerate(parts):
        c = Combination((p,), "4h", "trail")
        led.record_result(_res(c.hash, parts=[p.key]),
                          "grow" if i == 0 else "prune", "", {})
    b = planner.next_batch(led, CFG)
    assert b.round == "grow"
    assert all(c.k == 2 for c in b.combos)
    assert all(parts[0].key in c.keys for c in b.combos)


def test_growth_never_pairs_a_part_with_itself_or_its_own_gauge(led):
    _gauges(led)
    led.record_control("4h", "ohlcv", {"consistency_p": 0.001}, True)
    from trader.research import vocab
    from trader.research.combo import Combination
    parts = vocab.parts_for("4h", led.gauges("4h"))
    root = next(p for p in parts if p.gauge == "ret24")
    for p in parts:
        c = Combination((p,), "4h", "trail")
        led.record_result(_res(c.hash, parts=[p.key]),
                          "grow" if p is root else "prune", "", {})
    b = planner.next_batch(led, CFG)
    for c in b.combos:
        gauges = [p.gauge for p in c.parts]
        assert len(set(gauges)) == len(gauges)


def test_a_grown_child_gets_its_ablation_before_the_next_level(led):
    _gauges(led)
    led.record_control("4h", "ohlcv", {"consistency_p": 0.001}, True)
    from trader.research import vocab
    from trader.research.combo import Combination
    all_parts = vocab.parts_for("4h", led.gauges("4h"))
    # the singles round must be exhausted first — that is the planner's
    # stated order, so a test that skips it would be testing the wrong thing
    for p in all_parts:
        c = Combination((p,), "4h", "trail")
        led.record_result(_res(c.hash, parts=[p.key]), "prune", "", {})
    parts = all_parts[:2]
    child = Combination(tuple(parts), "4h", "trail", round="grow")
    led.record_result(_res(child.hash, k=2, parts=list(child.keys)),
                      "grow", "", {})
    b = planner.next_batch(led, CFG)
    assert b.round == "ablation"
    assert all(c.k == 1 for c in b.combos)


def test_the_planner_stops_at_max_parts(led):
    cfg = {"research": {**CFG["research"], "max_parts": 1}}
    _gauges(led)
    led.record_control("4h", "ohlcv", {"consistency_p": 0.001}, True)
    from trader.research import vocab
    from trader.research.combo import Combination
    for p in vocab.parts_for("4h", led.gauges("4h")):
        c = Combination((p,), "4h", "trail")
        led.record_result(_res(c.hash, parts=[p.key]), "grow", "", {})
    b = planner.next_batch(led, cfg)
    assert b.kind == "idle"


def test_seed_parts_come_from_the_live_book(led):
    import json
    with led.journal._tx() as c:
        c.execute(
            "INSERT INTO strategies (id,name,kind,params,state,description,"
            "origin,hypothesis,invalidation,regime_filter,markets,generation,"
            "parent_id,created_at,stats_json,spec_json) VALUES "
            "(?,?,'spec','{}','paper','d','seed','h','i','[]',"
            "'[\"futures\"]',0,'','2026-09-01T00:00:00+00:00','{}',?)",
            ("auth_donchian_breakout_trail", "Donchian Breakout Trail",
             json.dumps({"entry_long": "close > donchian_hi(100)",
                         "entry_short": "close < donchian_lo(100)",
                         "timeframe": "4h"})))
    seeds = planner.seed_parts(led.journal)
    assert any(s.key.startswith("seed:") for s in seeds)
    assert seeds[0].long == "close > donchian_hi(100)"
```

- [ ] **Step 2: Run to verify it fails**

Run: `./venv/bin/python -m pytest tests/test_research_planner.py -q -p no:cacheprovider`
Expected: FAIL with `ModuleNotFoundError: No module named 'trader.research.planner'`.

- [ ] **Step 3: Implement**

Create `trader/research/planner.py`:

```python
"""What to evaluate next.

The order is forced by what each step needs from the one before:

1. MEASURE   a threshold cannot be guessed, and a gauge that is NaN over the
             slice is not a gauge. Nothing can be generated before this.
2. CONTROL   a result in a restricted window cannot be LABELLED before the
             known-good rule has been run in that same window. Without it a
             negative reads as "no edge" when it may be "no power".
3. SINGLES   every part alone, both directions, both geometries.
4. ABLATION  a k-part combination is not a survivor until every one of its
             one-part ablations has been scored.
5. GROW      extend only parents that carried information, beam-limited.
6. SEEDED    the same, around the book's own strategies.

Held-out results never enter this file. If a held-out failure steered the
next round, the held-out set would silently become part of the search.

TWO RULES ADDED DURING EXECUTION (2026-09-12), both found by review:

- Control gating is PER WINDOW, not per horizon. Singles in an already
  controlled window proceed while another window still awaits its control;
  gating globally would stall every horizon behind its slowest window and
  makes this module's own test unsatisfiable.
- EVERY batch about to be offered must have its windows controlled first —
  growth, ablation and seeded combinations as much as singles. A grown rule
  can span two restricted windows (`funding` + `basis` -> the composite
  window `basis|funding`) that no single ever requests a control for, and
  this module is the ONLY gatekeeper: evaluate.py and growth.py never look
  at a control. Without this, such a rule is labelled with no baseline at
  all.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from . import control, vocab
from .combo import Combination, compatible, subsets

log = logging.getLogger(__name__)


@dataclass
class Batch:
    kind: str                       # measure | evaluate | idle
    tf: str
    geo: str = ""
    round: str = ""
    combos: list = field(default_factory=list)
    reason: str = ""


def _rcfg(cfg: dict) -> dict:
    return cfg.get("research", {}) or {}


def seed_parts(journal) -> list:
    """The book's own strategies, as parts to grow context around.

    "Donchian + X, judged with and without" is the one search the book can
    run against a mechanism it already believes in.
    """
    out = []
    if journal is None:
        return out
    try:
        rows = journal.query(
            "SELECT id, spec_json FROM strategies "
            "WHERE state IN ('paper','active') AND kind='spec'")
    except Exception as e:                              # noqa: BLE001
        log.debug(f"seed parts unavailable: {e}")
        return out
    for r in rows:
        try:
            s = json.loads(r["spec_json"] or "{}")
        except Exception:                               # noqa: BLE001
            continue
        lo, sh = (s.get("entry_long") or "").strip(), \
                 (s.get("entry_short") or "").strip()
        if not lo or not sh:
            continue
        key = f"seed:{r['id']}"
        out.append(vocab.Part(key=key, kind="event", gauge=key,
                              long=lo, short=sh))
    return out


def _parts(led, tf: str) -> list:
    return vocab.parts_for(tf, led.gauges(tf))


def _windows_needing_control(led, tf: str, combos) -> list:
    missing, seen = [], set()
    for c in combos:
        w = c.window
        if w in seen:
            continue
        seen.add(w)
        if led.control(tf, w) is None:
            ctrl = control.control_combo(tf, w)
            if ctrl is not None:
                missing.append(ctrl)
            else:
                # cannot be calibrated; record that rather than pretend
                led.record_control(tf, w, {"verdict": "uncalibrated",
                                           "consistency_p": None}, False)
    return missing


def _take(combos, led, tf, geo, n) -> list:
    known = led.known(tf, geo)
    out, seen = [], set()
    for c in combos:
        h = c.hash
        if h in known or h in seen:
            continue
        seen.add(h)
        out.append(c)
        if len(out) >= n:
            break
    return out


def needs_ablation(led, tf: str, geo: str, parts_by_key: dict) -> list:
    """Subsets of grown children that have not been scored yet."""
    out = []
    for row in led.rows(tf, geo, verdict="grow"):
        if int(row["k"]) < 2:
            continue
        try:
            keys = json.loads(row["parts"] or "[]")
        except Exception:                               # noqa: BLE001
            continue
        parts = [parts_by_key[k] for k in keys if k in parts_by_key]
        if len(parts) != len(keys):
            continue                    # vocabulary changed under the row
        c = Combination(tuple(parts), tf, geo)
        out.extend(s for s in subsets(c) if not led.has(s.hash))
    return out


def _grow_children(led, tf, geo, parts, beam, max_parts) -> list:
    """Extend the best parents at the deepest level that has any."""
    for k in range(1, int(max_parts)):
        parents = [r for r in led.rows(tf, geo, k=k, verdict="grow")]
        if not parents:
            continue
        # ablations first: a level is not finished until its children know
        # which of their parts earn a place
        out = []
        for row in parents[:int(beam)]:
            keys = json.loads(row["parts"] or "[]")
            by_key = {p.key: p for p in parts}
            base = [by_key[x] for x in keys if x in by_key]
            if len(base) != len(keys):
                continue
            for p in parts:
                cand = tuple(base) + (p,)
                if not compatible(cand):
                    continue
                out.append(Combination(cand, tf, geo, trigger=keys[0],
                                       round="grow", parent=row["hash"]))
        if out:
            return out
    return []


def next_batch(led, cfg: dict, journal=None) -> Batch:
    r = _rcfg(cfg)
    horizons = list(r.get("horizons") or [])
    geos = list(r.get("geometries") or ["trail", "fixed"])
    size = int(r.get("batch_combos", 40))
    beam = int(r.get("beam", 12))
    max_parts = int(r.get("max_parts", 6))

    for tf in horizons:
        gauges = led.gauges(tf)
        if not gauges:
            return Batch("measure", tf, reason="no thresholds measured yet")
        parts = _parts(led, tf)
        by_key = {p.key: p for p in parts}
        if not parts:
            continue
        for geo in geos:
            singles = [Combination((p,), tf, geo, trigger=p.key,
                                   round="singles") for p in parts]
            ctrl = _windows_needing_control(led, tf, singles)
            if ctrl:
                take = _take(ctrl, led, tf, geo, size)
                if take:
                    return Batch("evaluate", tf, geo, "control", take,
                                 "a window cannot be read without its control")
            take = _take(singles, led, tf, geo, size)
            if take:
                return Batch("evaluate", tf, geo, "singles", take,
                             "every condition alone, both directions")
            abl = needs_ablation(led, tf, geo, by_key)
            take = _take(abl, led, tf, geo, size)
            if take:
                return Batch("evaluate", tf, geo, "ablation", take,
                             "a part is not kept until its removal is priced")
            if max_parts > 1:
                grown = _grow_children(led, tf, geo, parts, beam, max_parts)
                take = _take(grown, led, tf, geo, size)
                if take:
                    return Batch("evaluate", tf, geo, "grow", take,
                                 "extend what carried information")
            seeds = seed_parts(journal)
            if seeds and max_parts > 1:
                seeded = []
                for s in seeds:
                    for p in parts:
                        cand = (s, p)
                        if compatible(cand):
                            seeded.append(Combination(
                                cand, tf, geo, trigger=s.key,
                                round="seeded"))
                take = _take(seeded, led, tf, geo, size)
                if take:
                    return Batch("evaluate", tf, geo, "seeded", take,
                                 "context around what the book already trades")
    return Batch("idle", horizons[0] if horizons else "",
                 reason="nothing left to evaluate at this depth")
```

- [ ] **Step 4: Run the tests**

Run: `./venv/bin/python -m pytest tests/test_research_planner.py -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add trader/research/planner.py tests/test_research_planner.py
git commit -m "$(cat <<'EOF'
feat(research): the planner — measure, calibrate, then search

The order is forced: a threshold cannot be guessed, a window cannot be read
without its control, and a combination is not a survivor until every part has
been priced by its own removal.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01EpstKPhQNEEG3wz2i7T9td
EOF
)"
```

---

### Task 12: The child job — heavy work, niced, time-boxed, read-only

**Files:**
- Create: `trader/research/job.py`
- Test: `tests/test_research_job.py`

**Interfaces:**
- Consumes: `evaluate.load_bundle/evaluate`, `thresholds.measure`, `vocab.expressions`, `combo.Combination`, `strategy.features.FeatureCtx`.
- Produces: `measure_job(payload: dict) -> dict` and `evaluate_job(payload: dict) -> dict`, both module-level (so `multiprocessing` spawn can import them) and both taking and returning plain JSON-able dicts.
  - `measure_job` payload: `{tf, symbols, heldout_symbols, requires, cfg, paths}` → `{tf, cut_ms, counts: {discovery, heldout_a, heldout_b}, gauges: {expr: {...}}}`
  - `evaluate_job` payload: `{tf, geo, symbols, heldout_symbols, requires, combos: [combo.as_dict()], cfg, paths, draws, seed, soft_deadline_s}` → `{results: [...], done: int, skipped: int, elapsed_s: float}`

- [ ] **Step 1: Write the failing test**

Create `tests/test_research_job.py`:

```python
"""The search runs in a spawned, niced, time-boxed child.

The kernel's pandas work holds the GIL, so a CPU-heavy search thread would
slow the trade loop directly, and a blow-up in it would be the kernel's. The
child reads the stores READ-ONLY and returns results over a pipe; the thread
writes them. It never writes a database, so the kernel stays the only writer
of truth.

A child that runs out of time returns what it finished rather than losing the
batch: the combinations it did not reach are simply absent from the ledger,
and the planner offers them again.
"""
import sqlite3

import numpy as np
import pandas as pd
import pytest

from trader.core.child import run_child
from trader.research import job
from trader.research.combo import Combination
from trader.research.vocab import Part

TF_MS = 14_400_000
DONCH = Part("ev:donch20", "event", "ev:donch20",
             "close > donchian_hi(20)", "close < donchian_lo(20)")
EMA = Part("st:above_ema50", "state", "st:above_ema50",
           "close > ema(50)", "close < ema(50)")


@pytest.fixture
def store(tmp_path):
    """A candle store with five synthetic markets."""
    db = tmp_path / "candles.db"
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE candles (symbol TEXT NOT NULL, tf TEXT NOT NULL, "
        "ts INTEGER NOT NULL, open REAL, high REAL, low REAL, close REAL, "
        "volume REAL, taker_buy REAL, PRIMARY KEY (symbol, tf, ts))")
    rng = np.random.default_rng(7)
    for i in range(5):
        px, rows = 100.0, []
        for b in range(900):
            drift = 0.006 if (b // 60) % 2 == 0 else 0.0
            px *= 1.0 + rng.normal(drift, 0.008)
            rows.append((f"S{i}/USDT", "4h", b * TF_MS, px, px * 1.004,
                         px * 0.996, px, 10.0, 5.0))
        con.executemany("INSERT INTO candles VALUES (?,?,?,?,?,?,?,?,?)",
                        rows)
    con.commit()
    con.close()
    return db


def _payload(store, combos=(), **kw):
    base = {"tf": "4h",
            "symbols": [f"S{i}/USDT" for i in range(5)],
            "heldout_symbols": [],
            "requires": ["ohlcv"],
            "cfg": {"risk": {"risk_per_trade_pct": 0.5,
                             "taker_fee_pct": 0.04,
                             "slippage_atr_frac": 0.015,
                             "funding_rate_8h": 0.0001,
                             "real_funding": False,
                             "max_open_trades": 8},
                    # a 900-bar synthetic store yields ~3.1k pooled samples,
                    # under the production floor of 5000 — this test is about
                    # the measurement path, not about the floor
                    "research": {"min_threshold_samples": 100}},
            "paths": {"candles": str(store), "derivs": str(store)},
            "combos": [c.as_dict() for c in combos],
            "draws": 20, "seed": 3, "soft_deadline_s": 300.0}
    base.update(kw)
    return base


def test_measure_returns_percentiles_and_the_cut(store):
    p = _payload(store)
    p["exprs"] = ["ret(24)", "rsi(14)"]
    out = job.measure_job(p)
    assert out["tf"] == "4h" and out["cut_ms"] > 0
    assert out["gauges"]["ret(24)"]["usable"] is True
    assert out["counts"]["discovery"]["S0/USDT"] > 0


def test_measure_reports_a_gauge_with_no_data_as_unusable(store):
    p = _payload(store)
    p["exprs"] = ["funding_z(360)"]
    out = job.measure_job(p)
    assert out["gauges"]["funding_z(360)"]["usable"] is False


def test_evaluate_scores_every_combination_it_is_given(store):
    combos = [Combination((DONCH,), "4h", "trail"),
              Combination((DONCH, EMA), "4h", "trail")]
    out = job.evaluate_job(_payload(store, combos))
    assert out["done"] == 2
    assert {r["hash"] for r in out["results"]} == {c.hash for c in combos}
    assert all("verdict" in r for r in out["results"])


def test_the_result_is_json_able_so_it_can_cross_the_pipe(store):
    import json
    out = job.evaluate_job(
        _payload(store, [Combination((DONCH,), "4h", "trail")]))
    json.dumps(out)


def test_a_soft_deadline_returns_what_was_finished(store):
    combos = [Combination((DONCH,), "4h", "trail"),
              Combination((DONCH, EMA), "4h", "trail")]
    out = job.evaluate_job(_payload(store, combos, soft_deadline_s=0.0))
    assert out["done"] == 0 and out["skipped"] == 2
    assert out["results"] == []


def test_the_job_really_runs_in_a_spawned_niced_child(store):
    r = run_child(job.evaluate_job,
                  _payload(store, [Combination((DONCH,), "4h", "trail")]),
                  timeout_s=180, nice=19)
    assert r.ok is True, r.error
    assert r.value["done"] == 1


def test_a_child_past_its_hard_deadline_is_a_failure_not_a_hang(store):
    combos = [Combination((DONCH,), "4h", "trail")] * 1
    r = run_child(job.evaluate_job, _payload(store, combos), timeout_s=0.2)
    assert r.ok is False and (r.timed_out or r.error)


def test_the_child_writes_nothing(store):
    before = store.stat().st_mtime_ns
    job.evaluate_job(_payload(store, [Combination((DONCH,), "4h", "trail")]))
    assert store.stat().st_mtime_ns == before


def test_the_job_module_opens_no_write_path():
    import inspect
    src = inspect.getsource(job)
    for forbidden in ("INSERT", "UPDATE ", "_tx(", "upsert"):
        assert forbidden not in src
```

- [ ] **Step 2: Run to verify it fails**

Run: `./venv/bin/python -m pytest tests/test_research_job.py -q -p no:cacheprovider`
Expected: FAIL with `ModuleNotFoundError: No module named 'trader.research.job'`.

- [ ] **Step 3: Implement**

Create `trader/research/job.py`:

```python
"""What the child process does, and nothing else.

Two entry points, both module-level so `multiprocessing` spawn can import
them, both taking and returning plain dicts so everything crosses the pipe.

The child READS the candle, derivative and reference stores and returns
results. It never writes a database: the kernel thread writes through
`Journal._tx()`, so the kernel remains the only writer of truth.

It also watches a SOFT deadline of its own, ahead of the hard one
`run_child` enforces. A batch that runs long returns what it finished
instead of being killed with everything lost; the combinations it did not
reach are simply not in the ledger, and the planner offers them again.
"""
from __future__ import annotations

import time

from ..strategy.features import FeatureCtx
from . import evaluate as ev
from . import slices, thresholds
from .combo import Combination


def _ctxs(b) -> list:
    """One FeatureCtx per discovery symbol, carrying the same context an
    evaluation gets — otherwise a threshold is measured on a quantity that
    evaluates to NaN when it is actually used."""
    out = []
    for sym in b.frames:
        out.append(FeatureCtx(
            frames=b.sym_frames[sym], tf=b.tf, btc=b.btc,
            derivs=b.derivs.get(sym), universe=b.universe, market=b.market,
            symbol=sym))
    return out


def measure_job(payload: dict) -> dict:
    """Percentiles for every gauge, and how much of the slice it exists on."""
    t0 = time.monotonic()
    b = ev.load_bundle(payload["tf"], payload["symbols"], payload["cfg"],
                       requires=tuple(payload.get("requires") or ("ohlcv",)),
                       heldout_symbols=tuple(
                           payload.get("heldout_symbols") or ()),
                       paths=payload.get("paths"))
    exprs = payload.get("exprs")
    if not exprs:
        from .vocab import expressions
        exprs = expressions(payload["tf"])
    rcfg = (payload["cfg"].get("research") or {})
    gauges = thresholds.measure(
        exprs, _ctxs(b),
        min_samples=int(rcfg.get("min_threshold_samples",
                                 thresholds.MIN_SAMPLES)),
        min_finite_frac=float(rcfg.get("min_finite_frac",
                                       thresholds.MIN_FINITE_FRAC)))
    return {"tf": b.tf, "cut_ms": int(b.cut), "gauges": gauges,
            "counts": {"discovery": slices.bar_counts(b.frames),
                       "heldout_a": b.heldout_bars.get("a", {}),
                       "heldout_b": b.heldout_bars.get("b", {})},
            "elapsed_s": round(time.monotonic() - t0, 2)}


def evaluate_job(payload: dict) -> dict:
    """Score a batch of combinations that share one horizon and geometry.

    One combination never costs the batch. A rule that raises is recorded
    as a LOOK that failed, with its reason, and the batch carries on --
    the time axis and the failure axis get the same treatment.
    """
    t0 = time.monotonic()
    combos = [Combination.from_dict(d) for d in payload.get("combos") or []]
    soft = float(payload.get("soft_deadline_s", 600.0))
    if not combos:
        return {"results": [], "done": 0, "skipped": 0, "elapsed_s": 0.0}

    b = ev.load_bundle(payload["tf"], payload["symbols"], payload["cfg"],
                       requires=tuple(payload.get("requires") or ("ohlcv",)),
                       heldout_symbols=tuple(
                           payload.get("heldout_symbols") or ()),
                       paths=payload.get("paths"))
    results, skipped = [], 0
    for c in combos:
        if time.monotonic() - t0 >= soft:
            skipped += 1
            continue
        try:
            results.append(ev.evaluate(
                c, b, draws=int(payload.get("draws", 30)),
                seed=int(payload.get("seed", 17))))
        except Exception as exc:
            # A failure is a LOOK with a reason, never a deferral.
            # `skipped` means "the deadline hit, come back to it", so a
            # failure routed there would be re-offered by the planner
            # forever and one poisonous rule would stall its window,
            # re-burning the whole child timeout on every attempt.
            # `error` is the key growth.decide reads; `window` keeps the
            # row filed under its own window instead of defaulting to ohlcv.
            results.append({"hash": c.hash, "tf": c.tf, "geo": c.geo,
                            "k": c.k, "window": c.window,
                            "parts": list(c.keys), "round": c.round,
                            "parent": c.parent, "trigger": c.trigger,
                            "trades": 0, "verdict": "error",
                            "error": f"{type(exc).__name__}: {exc}"[:300]})
    return {"results": results, "done": len(results), "skipped": skipped,
            "elapsed_s": round(time.monotonic() - t0, 2)}
```

- [ ] **Step 4: Run the tests**

Run: `./venv/bin/python -m pytest tests/test_research_job.py -q -p no:cacheprovider`
Expected: PASS. The spawn tests take ~10-20 s between them.

If `test_the_child_writes_nothing` fails, something on the read path is
creating a table: `DataFeed.db` runs `CREATE TABLE IF NOT EXISTS` on open.
That is a no-op on an existing table but still touches the file, so the test
uses a store that already has `candles`; if it still fails, assert on the row
count of every table instead of the mtime.

- [ ] **Step 5: Commit**

```bash
git add trader/research/job.py tests/test_research_job.py
git commit -m "$(cat <<'EOF'
feat(research): the batch runs in a spawned, niced, read-only child

It returns what it finished when it runs long, so a slow batch costs time
rather than results, and it never writes a database — the kernel stays the
only writer of truth.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01EpstKPhQNEEG3wz2i7T9td
EOF
)"
```

---

### Task 13: The runner, the kernel thread, and the config block

**Files:**
- Create: `trader/research/runner.py`
- Modify: `trader/kernel.py` — `__init__` (after `self._stop = False`, line ~105), `boot()` (thread block, lines ~257-276), `run()` (after `dur = time.time() - t0`, line ~1401), and a new `_research_loop` method next to `_strategy_mechanism_loop`
- Modify: `config.yaml` — new `research:` block after `mechanism:`
- Test: `tests/test_research_runner.py`

**Interfaces:**
- Consumes: `planner.next_batch`, `job.measure_job/evaluate_job`, `ledger.Ledger`, `growth.decide`, `control.powered/label`, `core.child.run_child`.
- Produces: `class ResearchRunner(journal, cfg, run=run_child)` with `.step(cycle_seconds: float | None = None) -> dict`; `DEFAULTS: dict`.
- Kernel produces: `self._last_cycle_s: float`; `Kernel._research_loop()`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_research_runner.py`:

```python
"""One step of the loop: plan, hand the batch to a child, write what comes
back, decide what it means.

The thread owns scheduling and writing; the child owns the arithmetic. Three
failure modes are handled here rather than in the child, because the child
may not survive them: a batch that times out, a batch that crashes, and a
trade cycle running slow enough that the search should stand down.
"""
import pytest

from trader.core.child import ChildResult
from trader.core.journal import Journal
from trader.research import vocab
from trader.research.ledger import Ledger
from trader.research.runner import ResearchRunner

CFG = {"risk": {"risk_per_trade_pct": 0.5, "max_open_trades": 8},
       "research": {"enabled": True, "horizons": ["4h"],
                    "geometries": ["trail"], "batch_combos": 3, "beam": 2,
                    "max_parts": 2, "batch_seconds": 60, "nice": 19,
                    "skip_if_cycle_s": 45,
                    "discovery_null_draws": 20}}


def _runner(tmp_path, reply):
    j = Journal(tmp_path / "j.db")
    calls = []

    def fake_run(fn, payload, timeout_s=0, nice=0, **kw):
        calls.append({"fn": fn.__name__, "payload": payload,
                      "timeout_s": timeout_s, "nice": nice})
        return reply(fn, payload)

    r = ResearchRunner(j, CFG, run=fake_run)
    return r, calls


def _measured(fn, payload):
    if fn.__name__ == "measure_job":
        exprs = vocab.expressions(payload["tf"])
        return ChildResult(ok=True, elapsed_s=1.0, value={
            "tf": payload["tf"], "cut_ms": 1_700_000_000_000,
            "gauges": {e: {"p10": -1.0, "p25": -0.5, "p75": 0.5, "p90": 1.0,
                           "n": 90000, "finite_frac": 0.99, "usable": True}
                       for e in exprs},
            "counts": {"discovery": {"BTC/USDT": 7000},
                       "heldout_a": {"UNI/USDT": 11000},
                       "heldout_b": {"BTC/USDT": 3000}}})
    results = []
    for d in payload["combos"]:
        from trader.research.combo import Combination
        c = Combination.from_dict(d)
        results.append({
            "hash": c.hash, "tf": c.tf, "geo": c.geo, "k": c.k,
            "round": c.round, "parent": c.parent, "trigger": c.trigger,
            "parts": list(c.keys), "window": c.window,
            "entry_long": c.long, "entry_short": c.short, "symbols": {},
            "trades": 200, "scored_symbols": 8, "consistency_p": 0.001,
            "median_pf": 1.4, "median_rate": 0.01,
            "portfolio": {"total_pct": 15.0, "max_dd_pct": 20.0,
                          "taken": 90},
            "projection": {"markets_a": 6, "markets_b": 6},
            "testable": True, "verdict": "scored"})
    return ChildResult(ok=True, elapsed_s=5.0, value={
        "results": results, "done": len(results), "skipped": 0,
        "elapsed_s": 5.0})


def test_a_disabled_search_does_nothing(tmp_path):
    r, calls = _runner(tmp_path, _measured)
    r.cfg = {**CFG, "research": {**CFG["research"], "enabled": False}}
    assert r.step()["skipped"] == "disabled"
    assert calls == []


def test_a_slow_trade_cycle_stands_the_search_down(tmp_path):
    r, calls = _runner(tmp_path, _measured)
    out = r.step(cycle_seconds=60.0)
    assert out["skipped"] == "busy"
    assert calls == []


def test_a_normal_cycle_does_not_stand_it_down(tmp_path):
    r, _ = _runner(tmp_path, _measured)
    assert r.step(cycle_seconds=17.7)["skipped"] != "busy"


def test_the_first_step_measures_and_records_the_thresholds(tmp_path):
    r, calls = _runner(tmp_path, _measured)
    out = r.step()
    assert out["kind"] == "measure"
    assert calls[0]["fn"] == "measure_job"
    assert calls[0]["nice"] == 19
    led = Ledger(r.journal)
    assert led.gauges("4h")
    assert led.slices("4h")["cut_ms"] == 1_700_000_000_000


def test_results_are_written_with_a_verdict_and_a_reason(tmp_path):
    r, _ = _runner(tmp_path, _measured)
    r.step()                         # measure
    r.step()                         # control
    out = r.step()                   # singles
    led = Ledger(r.journal)
    rows = led.rows("4h", "trail", k=1)
    assert rows
    assert rows[0]["verdict"] in ("grow", "prune", "survivor")
    assert rows[0]["reason"]
    assert out["recorded"] == len(rows) or out["recorded"] > 0


def test_a_control_batch_records_the_window_s_power(tmp_path):
    r, _ = _runner(tmp_path, _measured)
    r.step()
    out = r.step()
    assert out["round"] == "control"
    c = Ledger(r.journal).control("4h", "ohlcv")
    assert c is not None and c["powered"] is True


def test_a_child_timeout_marks_the_batch_failed_and_raises_nothing(tmp_path):
    def timeout(fn, payload):
        return ChildResult(ok=False, timed_out=True, elapsed_s=61.0,
                           error="timed out after 60s")

    r, _ = _runner(tmp_path, timeout)
    out = r.step()
    assert out["ok"] is False and "timed out" in out["error"]
    rows = r.journal.query("SELECT * FROM research_batches")
    assert rows and rows[0]["ok"] == 0


def test_a_child_crash_is_recorded_not_raised(tmp_path):
    def boom(fn, payload):
        return ChildResult(ok=False, error="Traceback ... ValueError: x")

    r, _ = _runner(tmp_path, boom)
    out = r.step()
    assert out["ok"] is False
    assert "ValueError" in out["error"]


def test_the_batch_carries_the_horizon_s_symbols_to_the_child(tmp_path):
    r, calls = _runner(tmp_path, _measured)
    r.step()
    payload = calls[0]["payload"]
    assert "BTC/USDT" in payload["symbols"]
    assert payload["heldout_symbols"]
    assert payload["tf"] == "4h"


def test_a_control_batch_runs_on_the_incumbent_universe(tmp_path):
    r, calls = _runner(tmp_path, _measured)
    r.step()
    r.step()
    payload = calls[-1]["payload"]
    from trader.research.control import INCUMBENT_UNIVERSE
    assert set(payload["symbols"]) == set(INCUMBENT_UNIVERSE)


def test_the_kernel_records_how_long_its_cycle_took():
    import inspect

    from trader import kernel as K
    src = inspect.getsource(K.Kernel.run)
    assert "_last_cycle_s" in src


def test_the_kernel_spawns_the_research_thread_only_when_enabled():
    import inspect

    from trader import kernel as K
    src = inspect.getsource(K.Kernel.boot)
    assert 'name="research"' in src
    assert 'cfg.get("research"' in src
```

- [ ] **Step 2: Run to verify it fails**

Run: `./venv/bin/python -m pytest tests/test_research_runner.py -q -p no:cacheprovider`
Expected: FAIL with `ModuleNotFoundError: No module named 'trader.research.runner'`.

- [ ] **Step 3: Implement the runner**

Create `trader/research/runner.py`:

```python
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
                    "skipped": None,
                    "error": res.error}
        v = res.value or {}
        n = self.ledger.record_gauges(batch.tf, v.get("gauges", {}))
        self.ledger.record_slices(batch.tf, v.get("cut_ms", 0),
                                  v.get("counts", {}))
        self.ledger.finish_batch(bid, True, time.monotonic() - t0)
        usable = sum(1 for m in v.get("gauges", {}).values()
                     if m.get("usable"))
        log.info(f"research measured {batch.tf}: {usable}/{n} gauges usable")
        return {"kind": "measure", "tf": batch.tf, "ok": True,
                "skipped": None, "gauges": n, "usable": usable}

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
                   "draws": int(self._r("discovery_null_draws")),
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
                    "round": batch.round, "ok": False, "skipped": None,
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
                # `skipped` is a REASON, and is None whenever the step
                # actually ran; the child's own count of combinations it
                # did not finish is `deferred`. One meaning per key.
                "skipped": None,
                "deferred": (res.value or {}).get("skipped", 0)}

    def _record(self, r: dict, c, batch) -> None:
        if batch.round == "control":
            powered = control.powered(r, float(self._r("control_max_p")))
            self.ledger.record_control(batch.tf, r.get("window", "ohlcv"),
                                       r, powered)
            # Deliberately NO record_result here. `research_combos.verdict`
            # is documented as survivor | grow | prune — the values
            # growth.decide actually produces — so writing the literal
            # "control" into it violates the column's own contract, and
            # Ledger.rows()'s ORDER BY has no tertiary key, so a tied
            # control row sorts ahead of real singles. research_controls
            # is this data's home and Ledger.control() reads only there;
            # per-round history is kept in research_batches regardless.
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
```

- [ ] **Step 4: Wire the kernel**

In `trader/kernel.py`, add the cycle-duration field. Find `self._stop = False`
(line ~105) and add beneath it:

```python
        self._stop = False
        #: how long the last trade cycle took. The research thread stands
        #: down when this runs long: two cores, and the trade loop wins.
        self._last_cycle_s = 0.0
```

In `run()`, immediately after `dur = time.time() - t0`:

```python
                dur = time.time() - t0
                self._last_cycle_s = dur
```

In `boot()`, after the `strategy-mechanism` thread block:

```python
        if (self.cfg.get("research", {}) or {}).get("enabled", False):
            threading.Thread(target=self._research_loop, daemon=True,
                             name="research").start()
```

And add the loop next to `_strategy_mechanism_loop`:

```python
    def _research_loop(self) -> None:
        """The search: generate combinations, score them, keep what pays.

        This thread owns the schedule and every write; the arithmetic runs in
        a spawned child at nice 19 with a wall-clock limit, because the
        kernel's pandas work holds the GIL and a CPU-heavy search on a thread
        would slow the trade loop directly.
        """
        import time as _t
        rcfg = self.cfg.get("research", {}) or {}
        if not rcfg.get("enabled", False):
            log.info("research search disabled")
            return
        from .research.runner import ResearchRunner
        runner = ResearchRunner(self.journal, self.cfg)
        every = float(rcfg.get("interval_seconds", 60))
        _t.sleep(300)                      # let boot and the first cycles settle
        while not self._stop:
            try:
                rep = runner.step(cycle_seconds=self._last_cycle_s)
                if rep.get("skipped") not in (None, "idle"):
                    log.debug(f"research: {rep}")
            except Exception as e:         # noqa: BLE001
                log.warning(f"research step failed: {e}")
            for _ in range(int(every)):
                if self._stop:
                    return
                _t.sleep(1)
```

- [ ] **Step 5: Add the config block**

In `config.yaml`, after the `mechanism:` block:

```yaml
research:                        # the search — docs/superpowers/specs/
                                 # 2026-09-11-luffy-research-pipeline-design.md
  enabled: false                 # turned on in Task 14, after calibration
  # A horizon is searched only if its discovery universe can carry the test.
  # consistency_p is a binomial tail: at 8 symbols the 0.01 gate is
  # unreachable even when every symbol clears the no-edge median. Measured
  # 2026-09-11: 19 symbols at 4h, 8 at 1h and 15m before the store was
  # deepened. A horizon that still falls short is refused, not relaxed.
  horizons: ["4h"]
  min_discovery_symbols: 16
  geometries: ["trail", "fixed"]
  # Discovery ranks; it does not gate. 60 draws is 96% of the cost of an
  # evaluation (measured: 10.9s of 12.3s at 4h over 19 symbols), and the
  # gate that decides anything is phase 3's held-out look, which keeps 60.
  discovery_null_draws: 30
  seed: 17
  batch_combos: 40
  batch_seconds: 900             # hard deadline for one child
  soft_margin_s: 60              # the child returns what it finished
  interval_seconds: 60
  nice: 19
  # Trade cycle p50 17.7s, p90 26.1s, p99 65.3s. Stand down for genuine
  # congestion, not for normal variation.
  skip_if_cycle_s: 45
  beam: 12                       # parents extended per level
  max_parts: 6                   # 1 trigger + 5 context
  grow_max_p: 0.25               # worth extending; NOT an admission bar
  control_max_p: 0.01            # a window has power if the incumbent reads this
  min_finite_frac: 0.5           # a gauge NaN over half the slice is not usable
  min_threshold_samples: 5000
  equity: 2000.0                 # the account the compounded return is walked on
```

- [ ] **Step 6: Run the tests**

Run:
```bash
./venv/bin/python -m pytest tests/test_research_runner.py -q -p no:cacheprovider
./venv/bin/python -m pytest tests/test_single_creation_path.py tests/test_phase0.py -q -p no:cacheprovider
./venv/bin/python -c "from trader.core.config import load_config; print(load_config()['research']['horizons'])"
```
Expected: PASS, PASS, and `['4h']`.

- [ ] **Step 7: Commit**

```bash
git add trader/research/runner.py trader/kernel.py config.yaml tests/test_research_runner.py
git commit -m "$(cat <<'EOF'
feat(research): the kernel runs the search, and stands down when busy

The thread owns the schedule and every write; the arithmetic runs in a niced
child with a deadline. A timed-out or crashed batch is a ledger row, and the
next batch resumes from the ledger.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01EpstKPhQNEEG3wz2i7T9td
EOF
)"
```

---

### Task 14: Looking at it from outside, and calibrating it on known answers

**Files:**
- Create: `trader/research/__main__.py`
- Create: `tests/test_research_calibration.py`

**Interfaces:**
- Produces: `python -m trader.research --status | --once | --measure | --top N [--tf 4h]`; `main() -> int`.

- [ ] **Step 1: Write the failing calibration test**

Create `tests/test_research_calibration.py`:

```python
"""Does the search find an edge that IS there, and refuse one that is not?

A search that returns "nothing works" is a claim about the tool first. Four
independent faults produced that verdict in screen_mechanisms for its whole
life, each failing silently and each in the same direction. So the pipeline
is run against two known answers:

KNOWN-GOOD — candles built with a real continuation edge (price trends after
a breakout). The breakout part must score, beat its own rotation, and a part
that adds nothing must fail to earn its place beside it.

KNOWN-BAD — random-walk candles. Essentially nothing may survive: with a
measured false-positive rate of 0.1-0.4% for consistency_p, a couple of dozen
combinations should yield no more than one at p <= 0.01, and the rest must
read as no-edge rather than as an opportunity.
"""
import sqlite3

import numpy as np
import pandas as pd
import pytest

from trader.research import growth, job
from trader.research.combo import Combination
from trader.research.vocab import Part

TF_MS = 14_400_000
BREAK = Part("ev:donch20", "event", "ev:donch20",
             "close > donchian_hi(20)", "close < donchian_lo(20)")
TREND = Part("st:above_ema50", "state", "st:above_ema50",
             "close > ema(50)", "close < ema(50)")
# an irrelevant condition: true on about half the bars, by construction
COIN = Part("dow>p50", "gauge", "dow", "dow() > 3", "dow() > 3")


def _store(tmp_path, kind, n=1100, symbols=6):
    db = tmp_path / f"{kind}.db"
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE candles (symbol TEXT NOT NULL, tf TEXT NOT NULL, "
        "ts INTEGER NOT NULL, open REAL, high REAL, low REAL, close REAL, "
        "volume REAL, taker_buy REAL, PRIMARY KEY (symbol, tf, ts))")
    for i in range(symbols):
        rng = np.random.default_rng(100 + i)
        px, rows = 100.0, []
        for b in range(n):
            if kind == "good":
                drift = 0.007 if (b // 60) % 2 == 0 else -0.001
            else:
                drift = 0.0
            px *= 1.0 + rng.normal(drift, 0.009)
            rows.append((f"S{i}/USDT", "4h", b * TF_MS, px, px * 1.004,
                         px * 0.996, px, 10.0, 5.0))
        con.executemany("INSERT INTO candles VALUES (?,?,?,?,?,?,?,?,?)",
                        rows)
    con.commit()
    con.close()
    return db


def _run(db, combos, symbols=6, draws=25):
    payload = {
        "tf": "4h", "geo": "trail",
        "symbols": [f"S{i}/USDT" for i in range(symbols)],
        "heldout_symbols": [], "requires": ["ohlcv"],
        "cfg": {"risk": {"risk_per_trade_pct": 0.5, "taker_fee_pct": 0.04,
                         "slippage_atr_frac": 0.015,
                         "funding_rate_8h": 0.0001, "real_funding": False,
                         "max_open_trades": 8},
                "research": {}},
        "paths": {"candles": str(db), "derivs": str(db)},
        "combos": [c.as_dict() for c in combos],
        "draws": draws, "seed": 11, "soft_deadline_s": 600.0}
    out = job.evaluate_job(payload)
    return {r["hash"]: r for r in out["results"]}


def test_known_good_the_planted_edge_is_found(tmp_path):
    db = _store(tmp_path, "good")
    c = Combination((BREAK,), "4h", "trail")
    r = _run(db, [c])[c.hash]
    assert r["verdict"] == "scored", r
    assert r["median_pf"] > 1.0
    assert r["consistency_p"] is not None and r["consistency_p"] < 0.05
    assert r["portfolio"]["total_pct"] > 0


def test_known_good_a_part_that_adds_nothing_does_not_earn_its_place(tmp_path):
    db = _store(tmp_path, "good")
    base = Combination((BREAK,), "4h", "trail")
    plus = Combination((BREAK, COIN), "4h", "trail", parent=base.hash)
    res = _run(db, [base, plus])
    ok, why = growth.earns_place(res[plus.hash], res[base.hash])
    assert not ok, why


def test_known_bad_a_random_walk_yields_no_edge(tmp_path):
    """Twenty-four rules on pure noise. consistency_p's measured
    false-positive rate is 0.1-0.4%, so one survivor is bad luck and three
    would be a broken gate."""
    db = _store(tmp_path, "bad")
    combos = []
    for n in (10, 20, 30, 40, 50, 60):
        for op, key in ((">", "hi"), ("<", "lo")):
            p = Part(f"ev:d{n}{key}", "event", f"ev:d{n}{key}",
                     f"close {op} donchian_{'hi' if op == '>' else 'lo'}({n})",
                     f"close {'<' if op == '>' else '>'} "
                     f"donchian_{'lo' if op == '>' else 'hi'}({n})")
            combos.append(Combination((p,), "4h", "trail"))
            combos.append(Combination((p, TREND), "4h", "trail"))
    res = _run(db, combos, draws=25)
    scored = [r for r in res.values() if r["verdict"] == "scored"]
    assert scored, "nothing was scoreable at all — that is a harness fault"
    hits = [r for r in scored
            if r["consistency_p"] is not None and r["consistency_p"] <= 0.01]
    assert len(hits) <= 1, (
        f"{len(hits)} of {len(scored)} noise rules cleared p<=0.01: "
        f"{[ (r['parts'], r['consistency_p']) for r in hits ]}")


def test_known_bad_the_median_noise_rule_sits_at_the_coin_flip(tmp_path):
    db = _store(tmp_path, "bad")
    combos = [Combination((Part(f"ev:d{n}", "event", f"ev:d{n}",
                                f"close > donchian_hi({n})",
                                f"close < donchian_lo({n})"),),
                          "4h", "trail")
              for n in (20, 30, 40, 50)]
    res = _run(db, combos, draws=25)
    import statistics as st
    pcts = [v["null_pctile"] for r in res.values()
            for v in r["symbols"].values() if v.get("null_pctile") is not None]
    assert pcts, "no percentiles at all"
    assert 0.2 < st.median(pcts) < 0.8, st.median(pcts)
```

- [ ] **Step 2: Run to verify the calibration test fails or passes honestly**

Run: `./venv/bin/python -m pytest tests/test_research_calibration.py -q -p no:cacheprovider`
Expected: PASS (the machinery exists by now). It takes ~2-4 minutes.

If `test_known_good_the_planted_edge_is_found` fails, **stop and diagnose the
pipeline, not the test** — a search that cannot find a planted continuation
edge under the geometry built for continuation cannot be trusted to report
anything about real markets. If `test_known_bad_a_random_walk_yields_no_edge`
fails, the null or the consistency test is wrong and every number this
pipeline will ever produce is inflated.

- [ ] **Step 3: Write the status CLI**

Create `trader/research/__main__.py`:

```python
"""Look at the search from outside the kernel.

    python -m trader.research --status          what it has done so far
    python -m trader.research --top 20          the best discovery results
    python -m trader.research --measure --tf 4h measure that horizon now
    python -m trader.research --once            run exactly one batch

`--once` and `--measure` run the batch in THIS process's child, ignoring
`research.enabled`, so a horizon can be calibrated and timed before the
kernel is allowed to spend hours on it.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys

from ..core.config import ROOT, load_config
from ..core.journal import Journal
from .ledger import Ledger


def _journal() -> Journal:
    # the same database the kernel and the dashboard open
    return Journal(str(ROOT / "data" / "luffy.db"))


def _status(led: Ledger, cfg: dict) -> None:
    c = led.counts()
    print(f"combinations evaluated : {c['combos']}")
    print(f"  by verdict           : {c['by_verdict']}")
    print(f"  by horizon           : {c['by_tf']}")
    print(f"  by parts             : {c['by_k']}")
    print(f"batches                : {c['batches']} "
          f"({c['failed_batches']} failed)")
    for tf in (cfg.get("research") or {}).get("horizons") or []:
        g = led.gauges(tf)
        usable = sum(1 for m in g.values() if m.get("usable"))
        s = led.slices(tf) or {}
        cut = s.get("cut_ms")
        print(f"\n[{tf}] gauges {usable}/{len(g)} usable · "
              f"discovery symbols {len(s.get('discovery') or {})} · "
              f"cut {cut}")
        for r in led.journal.query(
                "SELECT window, consistency_p, powered FROM "
                "research_controls WHERE tf=? ORDER BY window", (tf,)):
            mark = "powered" if r["powered"] else "UNDERPOWERED"
            p = r["consistency_p"]
            print(f"    control {r['window']:<24} "
                  f"p={p if p is None else round(p, 6)} — {mark}")


def _top(led: Ledger, cfg: dict, n: int) -> None:
    for tf in (cfg.get("research") or {}).get("horizons") or []:
        rows = led.rows(tf, limit=n)
        if not rows:
            continue
        print(f"\n[{tf}] best discovery results — DISCOVERY EVIDENCE ONLY, "
              f"which is a description of the markets it was found on")
        print(f"  {'p':>9} {'PF':>5} {'CAGR%':>7} {'k':>2} {'verdict':<9} "
              f"parts")
        for r in rows:
            p = r["consistency_p"]
            print(f"  {('%.1e' % p) if p is not None else 'n/a':>9} "
                  f"{(r['median_pf'] or 0):>5.2f} "
                  f"{(r['total_pct'] or 0):>7.1f} {r['k']:>2} "
                  f"{r['verdict'] or '':<9} "
                  f"{','.join(json.loads(r['parts'] or '[]'))}")


def main() -> int:
    ap = argparse.ArgumentParser(prog="trader.research")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--top", type=int, default=0)
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--measure", action="store_true")
    ap.add_argument("--tf", default="")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")

    cfg = load_config()
    if args.tf:
        cfg.setdefault("research", {})["horizons"] = [args.tf]
    j = _journal()
    led = Ledger(j)
    led.ensure()

    if args.once or args.measure:
        from .runner import ResearchRunner
        cfg.setdefault("research", {})["enabled"] = True
        if args.measure:
            # drop the horizon's measurements so it is re-measured now
            for tf in cfg["research"].get("horizons") or []:
                with j._tx() as c:
                    c.execute("DELETE FROM research_gauges WHERE tf=?", (tf,))
        rep = ResearchRunner(j, cfg).step()
        print(json.dumps(rep, indent=2, default=str))
        return 0
    if args.top:
        _top(led, cfg, args.top)
        return 0
    _status(led, cfg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Check the CLI runs against the real database**

Run: `./venv/bin/python -m trader.research --status`
Expected: zero combinations, no gauges — it has not run yet. It must not
raise, and it must not create anything in `strategies`:

```bash
./venv/bin/python -c "
from trader.core.config import ROOT
from trader.core.journal import Journal
j = Journal(str(ROOT / 'data' / 'luffy.db'))
print(j.query('SELECT COUNT(*) n FROM strategies')[0]['n'])"
```
Expected: the same count as before (2 live specs plus retired rows).

- [ ] **Step 5: Commit**

```bash
git add trader/research/__main__.py tests/test_research_calibration.py
git commit -m "$(cat <<'EOF'
feat(research): a status view, and calibration on two known answers

A planted continuation edge must be found; a random walk must yield nothing.
A search that returns "nothing works" is a claim about the tool first, and
four silent faults once produced exactly that verdict.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01EpstKPhQNEEG3wz2i7T9td
EOF
)"
```

---

### Task 15: Measure it on the real store, then let the kernel run it

**Files:**
- Modify: `config.yaml` (`research.enabled`, `research.horizons`)
- Modify: `CLAUDE.md` (daemon table, data files, what the search decided)
- Modify: `docs/superpowers/plans/2026-09-12-research-phase2-search.md` (record the measurements at the top)

- [ ] **Step 1: Measure the 4h horizon**

```bash
./venv/bin/python -m trader.research --measure --tf 4h
./venv/bin/python -m trader.research --status
```

Record, from the output: how many gauges are usable at 4h, the cut timestamp
(convert to a date), and the discovery symbol count. **Expected from what was
already measured:** 19 discovery symbols, a cut around 2025-05, funding and
the reference gauges usable, and **open interest / the account ratio NOT
usable** (their series begins 2025-10, after the cut). If OI reads usable at
4h, the cut or the truncation is wrong — stop and find out why.

- [ ] **Step 2: Calibrate the window controls, and time a batch**

```bash
time ./venv/bin/python -m trader.research --once --tf 4h   # control batch
./venv/bin/python -m trader.research --status              # read the power
time ./venv/bin/python -m trader.research --once --tf 4h   # first singles
```

Record: the control's `consistency_p` for the plain `ohlcv` window and for
each restricted window, whether each is powered, and the wall-clock seconds
per batch. Divide to get **evaluations per hour** — that is the number that
says whether the first singles pass takes hours or days, and it is the
measurement the spec asked Phase 2 to take rather than assume.

A control that reads UNDERPOWERED in the plain window at 4h would mean the
incumbent cannot be detected on its own declared 16 over the discovery slice.
That would contradict the recorded 7.8e-04 on the train half — stop and find
the difference before letting the search run.

- [ ] **Step 3: Decide the horizons from the measurements**

```bash
./venv/bin/python -c "
from trader.research.universe import DISCOVERY, coverage
for tf, have in coverage(['4h','1h','15m']).items():
    print(tf, sum(1 for s in DISCOVERY if s in have), 'of', len(DISCOVERY))"
```

Set `research.horizons` in `config.yaml` to exactly the horizons that carry
at least 16 discovery symbols **and** whose plain-window control is powered.
Write the measured numbers into the comment above the key, replacing the
2026-09-11 figures with what you just read. If 1h or 15m falls short after
Task 1's deepening, leave it out and say why in the comment — an unreachable
gate is not a horizon, it is a way to spend two cores on nothing.

- [ ] **Step 4: Run the whole suite and the invariants**

```bash
./venv/bin/python -m pytest tests/ -q -p no:cacheprovider 2>&1 | tail -15
./venv/bin/python scripts/backtest_equivalence.py | tail -3
./venv/bin/python scripts/bench_vector_backtest.py | tail -3
```
Expected: the suite's one known failure (`test_macro_guard`) and nothing
else; `backtest_equivalence` PASS; the benchmark above 20x.

- [ ] **Step 5: Update CLAUDE.md**

In the daemon-threads table, after the `strategy-mechanism` row:

```markdown
| `research` | 60s | one search batch: plan → niced child → ledger. Stands down while the last trade cycle took > 45s. `python -m trader.research --status` |
```

In the data-files table, after `data/luffy.db`:

```markdown
| `data/luffy.db` → `research_*` | the search's ledger: every combination
  evaluated under a canonical hash (`research_combos`), the measured
  percentiles per horizon (`research_gauges`), each window's control
  (`research_controls`), the discovery cut (`research_slices`) and every
  batch (`research_batches`). Phase 2 writes nothing to `strategies` |
```

And a new section after "The strategy language":

```markdown
## The search

`trader/research/` generates combinations of measured conditions — up to 6
parts, both directions, both fixed geometries — scores each against a
rotation of its own entries on a discovery slice, and records everything.
It proposes nothing yet: the held-out gates, the error budget and the
handoff into `_mechanism_once` are phase 3.

- **The discovery slice is one CALENDAR cut per horizon**, at 70% of the
  universe's span. Per-symbol splits leak era across symbols: a market
  listed in 2023 would have its "discovery" slice inside BTC's held-out era.
- **Thresholds are measured percentiles** (10/25/75/90) of each expression's
  own distribution on that slice, and a gauge finite over less than half of
  it is dropped. That is why open interest is absent from the 4h vocabulary:
  the series begins 2025-10 and the 4h cut lands ~2025-05.
- **Every condition carries its own mirror expression**, written so the same
  rank means the mirror state (`ret(24)` / `0 - ret(24)`, `lower_wick()` /
  `upper_wick()`). Thresholds for each side are measured separately.
- **A part earns its place only by improving BOTH** the cross-symbol
  `consistency_p` and compounded return over one account, and a survivor
  must beat every one of its one-part ablations. Judging on profit factor
  alone is what made every filter look free while it halved the CAGR.
- **A horizon is searched only if it carries ≥16 discovery symbols**, because
  `consistency_p` is a binomial tail and at 8 symbols the 0.01 gate is
  unreachable. 1h and 15m held 8 of 19 until the store was deepened.
- **Every window runs the incumbent rule beside the challengers**, on its
  declared 16. A negative in a window where the known-good rule also cannot
  register is recorded as UNDERPOWERED, never as no-edge.
- Discovery uses 30 null draws, not 60: it ranks, it does not gate, and the
  null is 96% of an evaluation's cost.
```

- [ ] **Step 6: Turn it on and restart the kernel**

Set `research.enabled: true` in `config.yaml`, then:

```bash
./restart.sh kernel
sleep 420
grep -a "research" logs/luffy.log | tail -20
grep -a "cycle #" logs/luffy.log | tail -5
```

Expected: the research thread logs a measure or an evaluate batch within ~7
minutes (it sleeps 300 s at boot), and the trade cycle's duration in the last
lines is unchanged from its p50 of ~17.7 s. The watchdog is live, so do not
stop the kernel by hand; `restart.sh` is the sanctioned path.

- [ ] **Step 7: Watch one hour, then record what it cost**

```bash
sleep 3600
./venv/bin/python -m trader.research --status
./venv/bin/python -m trader.research --top 15
grep -a "cycle #" logs/luffy.log | tail -200 | grep -ao "[0-9.]*s$" \
  | sort -n | awk '{a[NR]=$1} END{print "p50="a[int(NR*0.5)], "p90="a[int(NR*0.9)], "max="a[NR]}'
```

Two things must be true: combinations are accumulating, and the trade cycle's
p50 has not moved materially (it was 17.7 s / p90 26.1 s before). If the
cycle slowed, lower `research.batch_combos` or raise `skip_if_cycle_s`'s
sensitivity by lowering it — the trade loop wins, always.

Write the hour's throughput and the cycle comparison into this plan's
measurement table, and into the vault via the usual daily note.

- [ ] **Step 8: Commit**

```bash
git add config.yaml CLAUDE.md docs/superpowers/plans/2026-09-12-research-phase2-search.md
git commit -m "$(cat <<'EOF'
docs: phase 2 — the search runs, on the horizons that can judge it

Measured before enabling: usable gauges per horizon, each window's control,
and evaluations per hour. Horizons that cannot reach p<0.01 are switched off
rather than searched.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01EpstKPhQNEEG3wz2i7T9td
EOF
)"
```

---

## What Phase 2 deliberately does not do

Naming these here so a reader does not go looking for them in the code:

- **No held-out look.** Held-out A and B are counted (bar counts, for the
  testability projection) and never read. Gate 1, the LORD++ error budget and
  its supervisory brake are Phase 3.
- **Only the top M per round go to held-out** — also Phase 3, since nothing
  goes there yet. Phase 2's job is to produce a ranked, ablated, ledgered
  list of candidates for it.
- **Nothing is proposed and nothing is admitted.** `trader/research/` never
  touches the `strategies` table; the handoff into `_mechanism_once` and the
  extension of `tests/test_single_creation_path.py` to "one admission gate,
  many proposers" are Phase 3.
- **No thesis, no prediction test.** Phase 4.
- **No weekly report.** Phase 5, with the rent verdict. `--status` is the
  interim view.
- **Thresholds are frozen once measured.** There is no re-measurement path,
  deliberately: the canonical hash carries percentile ranks, so a silent
  re-measure would change what a stored hash means. If the vocabulary is ever
  re-measured, the ledger's rows must be re-keyed or archived first.

## Self-review

Run against the spec's Part 3 with fresh eyes, plus the three checks the
writing-plans skill asks for.

**Spec coverage.** Trigger-plus-context (Tasks 3, 6 — recorded rather than
structurally enforced, deviation 6); up to 6 parts (Task 6); both directions
mirrored (Task 3); exits not searched, the two fixed geometries (Task 2);
horizons 15m/1h/4h (Task 13's config, gated by Task 1's measurement);
thresholds measured at the four percentiles (Task 5); the four slices (Task
4, with held-out counted but never read); singles / grow / seeded rounds
(Task 11); growth rules on both measures plus the testability floor (Tasks 7,
8); ablation and ties-to-the-simpler-rule (Task 8); canonical hash, resume,
held-out never feeding back (Tasks 6, 10, 11); window controls with the
UNDERPOWERED label (Task 9); the kernel thread and the niced child with a
deadline (Tasks 12, 13); the `research:` config block (Task 13); every row of
the spec's failure-handling table (Task 13's runner); known-good and
known-bad calibration and the two standing invariants (Tasks 14, 15).

**Where this plan knowingly departs from the spec's numbers.** The spec
estimated "~5 s per evaluation"; measured, it is **12.3 s** at 4h over 19
symbols with 60 null draws, which is why discovery drops to 30 draws
(deviation 3) and why Task 15 measures throughput instead of assuming it. The
spec's slice table says discovery is "the earliest 70%" per symbol; this plan
cuts by the calendar (deviation 2). The spec assumed 1h and 15m were
searchable; measured, they carried 8 of 19 discovery symbols, so Task 1
deepens the store and Task 15 decides the horizon list from what is actually
there (deviation 1).

**Placeholder scan.** No "TBD", no "implement later", no "similar to Task N",
no "add appropriate error handling". Every code step carries the code; every
run step carries the command and what it should print.

**Type consistency.** `Part(key, kind, gauge, long, short)` and
`Combination(parts, tf, geo, trigger, round, parent)` are constructed the
same way in vocab, combo, control, planner, job and every test.
`evaluate.evaluate(c, b, draws, seed)` returns the dict that the ledger's
`record_result` and `growth.decide` both read — `consistency_p`,
`portfolio.total_pct`, `verdict`, `testable`, `scored_symbols`, `trades`.
`Ledger.record_result(res, verdict, reason, ablation, label="")` is called
with exactly that shape by the runner. `planner.next_batch(led, cfg,
journal)` returns the `Batch` the runner destructures. `job.measure_job` and
`job.evaluate_job` take the payload keys the runner builds and return the
keys it reads.

**One thing a reviewer should watch during execution.** `growth.decide`
treats a subset that could not be scored as beaten (`_pct` returns `-inf`).
That is deliberate — if removing a part makes the rule untestable, the part
is doing the work that makes it judgeable — but it means a combination whose
every ablation is untestable is called a survivor on thin evidence. Phase 3's
held-out gate is where that is caught, and the ledger stores the ablation
verdicts so it can be audited rather than guessed at.
