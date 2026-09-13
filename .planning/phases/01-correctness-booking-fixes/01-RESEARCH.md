# Phase 1: Correctness & Booking Fixes - Research

**Researched:** 2026-09-13
**Domain:** Internal codebase correctness fixes (Python trading system) — no new external library needed. All three fixes are read-verify-fix work against `trader/agents/macro_guard.py`, a to-be-written read-only Binance script, and `trader/strategy/vector_backtest.py` + its callers.
**Confidence:** HIGH — every claim below was verified by reading the actual source this session (cited with path:line), and the FIX-01 diagnosis was additionally confirmed by *running* the failing test.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**FIX-01 — MacroGuard cache clock**
- D-01: Fix the production code, not the test. Root cause: `MacroGuard._load_cached()` (`trader/agents/macro_guard.py` ~L133-165) measures cache age with `time.time()` and the event horizon with `datetime.now(timezone.utc)`, bypassing the injectable `self._now()` the test pins. Route both through `self._now()`. Do not monkeypatch `time`/`datetime` inside the test. Check the rest of the class for other raw-clock reads on the same path (`_fetch`'s TTL uses `time.time()` for fetch recency — decide whether that belongs on the same clock; the cache-age and horizon reads definitely do).
- D-02: "Full suite green" means the whole `tests/` directory, run from a checkout that has `venv/` and the real `data/` stores. Measured 2026-09-13: worktree reads 15 failed / 1203 passed, 14 of those are environment artifacts (pass 29/29 in main checkout). The one genuine failure is the macro_guard test. Verify green in an environment with the data, and say which one was used.

**FIX-03 — Production taker fee**
- D-03: Source is a read-only production Binance API key ("Enable Reading" only, IP-restricted), stored in `.env` under names distinct from `BINANCE_API_KEY`/`BINANCE_SECRET_KEY`. A one-shot script calls `commissionRate` against production for every declared symbol and records per-symbol maker/taker rates with a UTC timestamp. Script must never place an order; kernel must never read the production key. Creating the key is a human step — needs a checkpoint. Reversibility: reversible (key can be revoked).
- D-03a (MEASURED 2026-09-13): operator supplied a key, read was taken. `.planning/phases/01-correctness-booking-fixes/01-production-fees.json`. Key: enableReading true, everything else false, **ipRestrict false** (not what D-03 asked for — key is exposed via chat transcript and should be revoked by the operator). `/fapi/v1/commissionRate` on all 16 declared symbols: taker 0.0500%, maker 0.0200%, uniform. Demo read 0.0400% taker on 14/16 (0.0500% on TAO/HYPE) — so `taker_fee_pct: 0.04` under-charges production by 1bp/side on 14 symbols. Remaining work: repeatable read script under `scripts/` (env names per D-03), a durable data record, CLAUDE.md text. `config.yaml` stays 0.04, no boot guard. State plainly in CLAUDE.md that backtests charge the demo rate.
- D-04: Record only — no boot guard (operator declined). Record measured rates in a durable data file plus CLAUDE.md (replace "Production fees are UNMEASURED"). Do NOT change `config.yaml`'s `taker_fee_pct` — that's a go-live decision, out of scope.

**FIX-04 — Warmup bar accounting (widened)**
- D-05: Scope widened to fix the fault at 4h in this phase (not only 1d+). Confirmed by reading the code: `Analyst.review_deployed` → `rolling.has_decayed(recent_days=30)` → `_score_window` slices `bars("4h",30)=180` bars, and `simulate` returns empty when `n <= WARMUP+1` (211). A 4h spec's decay check can never see a trade — Donchian Breakout Trail cannot be retired by decay. Admission at 4h/90d is 540 bars with the first 210 (39%) unscored. `rolling_windows` at 4h/60d is 360 bars, 58% unscored; 1h/30d decay loses 29%. CLAUDE.md's "harmless at 4h" claim is wrong and must be corrected.
- D-06: Fix shape — indicators warm up on bars BEFORE the scored window; only the window's own bars are scored. Do NOT scale `WARMUP` by duration (it's a bar-count indicator lookback, correctly so). Where no prior history exists, the unscorable prefix stays unscored — never fabricated.
- D-07: A loud guard remains for when the fix cannot cover a case: when a slice still cannot yield enough scored bars, or the rotation null has too few distinct offsets for its draws, the result must surface as UNTESTED/untestable, never a score and never silently empty. Covers 1d+ and any short slice.
- D-08: Before/after evidence is part of the deliverable — decay verdict + admission window/evidence for Donchian Breakout Trail and `spec_funding_filtered_trend_pullback`, plus the Donchian declared-16 headline. Surface before/after readings to the operator before the kernel runs a decay sweep on the fixed code. A retirement outcome is legitimate, not a regression to suppress.
- D-09: Invariants that must hold: `scripts/backtest_equivalence.py` PASS, `scripts/bench_vector_backtest.py` > 20x, `spec_evidence` UNTESTED semantics unchanged. Check whether the equivalence script's own `range(WARMUP, n-1)` loop is part of the contract or something to move. Re-examine `research/slices.py` (`MIN_SLICE_BARS`, WARMUP-based bar counting) and the 2026-09-13 power measurement — say whether it still stands if scored bars change.

### Claude's Discretion
- The FIX-01 clock refactor details, the fee script's file name and output format, the exact D-07 guard thresholds (to be MEASURED against the live callers, not picked), and how the warmup prefix is threaded through `simulate`/`rolling`/`null_baseline`/`research.evaluate` callers.
- Plan split: the three fixes are independent. FIX-03 blocks on a human key step, so it should not gate FIX-01/FIX-04.

### Deferred Ideas (OUT OF SCOPE)
- Kernel boot guard refusing `BINANCE_DEMO=false` without a measured production fee: offered and declined (D-04).
- Switching `taker_fee_pct` to the production figure: part of the go-live decision (out of scope).
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| FIX-01 | `test_a_restart_reuses_the_cached_calendar` passes regardless of wall-clock date; full suite green | Exact raw-clock lines identified and the failure mechanism reproduced by *running* the test (see Pitfall 1 and Code Examples). Full-suite-green execution strategy documented under Validation Architecture / Environment Availability, including a same-git-object-store isolated-worktree approach that never touches the live main checkout. |
| FIX-03 | Production taker fee read and recorded from a live production-key source before `BINANCE_DEMO=false` | Measurement already taken (D-03a); remaining work is a repeatable script + durable record scoped precisely below (script design, env var names, where the durable record must live given `data/*.json` is gitignored). |
| FIX-04 | `vector_backtest.WARMUP` no longer silently discards scored data (widened to 4h per D-05) | Full caller map (`rolling.py` x3, `vector_backtest.py` x2, `null_baseline.py`, `research/evaluate.py`, `scripts/mech.py`, `scripts/backtest_equivalence.py`) with per-caller before/after bar-loss analysis, a concrete `score_from` design compatible with every caller and with the existing regression test `test_warmup_signals_are_ignored`, and the D-07 null-offset-distinctness gap identified in `null_baseline.null_pfs`. |
</phase_requirements>

## Summary

All three fixes are narrow, well-isolated, internal-codebase corrections; none require a new external library. FIX-01 is a two-line clock-routing fix in `macro_guard.py`, verified this session by actually running the failing test and tracing the exact mechanism (a raw `datetime.now(timezone.utc)` computing the cache's event-horizon cutoff, which disagrees with the test's pinned `self._now()` once real wall-clock time has moved past the cached event — exactly the "week of 2026-09-04 passed" failure CLAUDE.md describes). FIX-03's hard part (getting a production key and taking the measurement) is already done; what remains is turning an ad-hoc read into a committed, repeatable script plus a durable record placed where it will actually survive (not `data/*.json`, which is gitignored — `.planning/phases/01-correctness-booking-fixes/` already holds the measurement and CLAUDE.md is the narrative home). FIX-04 is the substantial one: `WARMUP=210` is a correct indicator-lookback bar count, but every windowed caller (`_score_window`, `vector_walk_forward`, `null_baseline.null_pfs`) currently treats it as "skip the first 210 bars of whatever array I was handed," discarding real signal even when a longer history exists just before the window. The fix threads a `score_from` parameter through `simulate()` (default 0, fully backward compatible with the one existing regression test that pins current behavior) and has each windowed caller widen its slice by up to `WARMUP` bars of genuine prior history before calling in, scoring trades only from the window's true start. Two research pipeline modules (`research/slices.py`, `research/evaluate.py`) already avoid this bug by construction — their "windows" already start at the true beginning of each symbol's stored history — so the 2026-09-13 research power measurement is unaffected by this fix.

**Primary recommendation:** Fix FIX-01 by routing `_load_cached()`'s two raw-clock reads through `self._now()`; write FIX-03's script standalone (never touching `make_exchange()` or the kernel's credential path) with the durable record under `.planning/phases/01-correctness-booking-fixes/`; implement FIX-04 by adding a `score_from: int = 0` parameter to `vector_backtest.simulate()` and widening the windowed callers' slices to include up to `WARMUP` bars of genuine prior history, leaving `research/slices.py`/`evaluate.py` untouched.

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Macro-event freeze clock | Backend / Analyst-adjacent agent (`trader/agents/macro_guard.py`) | — | Pure in-process decision logic; no client/server split in this system — "Backend" here means the kernel's daemon-thread agents, as opposed to the dashboard (read-mostly UI tier). |
| Production fee measurement | Backend / one-shot script (`scripts/`) | Data / durable record (`.planning/`, CLAUDE.md) | A script run once by a human, writing a fact the rest of the system reads later; not a runtime dependency of the kernel. |
| Backtest bar accounting (WARMUP) | Backend / strategy evaluation engine (`trader/strategy/`) | — | `vector_backtest.py`, `rolling.py`, `null_baseline.py`, `spec_evidence.py`, `brain/analyst.py` are all one tier: the offline/online evaluation engine the kernel's brain tick and the Analyst call synchronously. No DB schema change, no API surface change. |

## Standard Stack

No new packages. This phase touches only first-party modules already in the dependency graph (`numpy`, `pandas`, `ccxt`, `requests`, `pytest` — all already installed per `requirements`/`venv`). No `Package Legitimacy Audit` is needed: nothing is installed.

## Architecture Patterns

### System Architecture Diagram (data flow relevant to this phase)

```
FIX-01                                   FIX-04
┌─────────────────┐                      ┌──────────────────────────────┐
│ requests.get()   │  (mocked in tests)   │ Analyst.review_deployed()    │
│ Finnhub/FF feed  │                      │   -> rolling.has_decayed()   │
└────────┬─────────┘                      │        -> recent_verdict()  │
         │ events                          │           -> _score_window()│
         v                                 │              df.iloc[-n:]  │
┌─────────────────┐   self._now() (PINNED  │              (RECENT window,│
│ _fetch_calendar  │   in test, real clock  │               no prefix)   │
│  -> _save_cached │   in production)       │                 |          │
│  -> _load_cached │◄───────┐               │                 v          │
│    age = ???     │        │               │        compiled.entries() │
│    horizon = ???│◄────────┘  BUG: uses     │        simulate(..., cursor=WARMUP)
└────────┬─────────┘  time.time()/           │            (LOCAL array index,
         │             datetime.now(utc)      │             discards first 210
         v             instead of self._now() │             bars of the WINDOW,
┌─────────────────┐                           │             not of history)
│ _find_active_    │  uses self._now()        └──────────────────────────────┘
│  event() (OK)    │  correctly already
└────────┬─────────┘
         v
   check() -> {active, why}


FIX-03
┌───────────────────┐   read-only key    ┌────────────────────┐   writes    ┌──────────────────────────┐
│ operator creates    │──────────────────►│ scripts/read_prod_  │────────────►│ .planning/phases/01.../   │
│ Binance prod key     │  BINANCE_PROD_    │  fee.py (new)       │             │  NN-production-fees.json  │
│ (Enable Reading only)│  READONLY_KEY/    │  ccxt.binanceusdm    │             │  (durable, git-tracked)   │
└───────────────────┘  SECRET (.env)      │  demo=False, no      │             └──────────────────────────┘
                                            │  order methods used  │
                                            │  fapiPrivateGet      │             CLAUDE.md text updated
                                            │  CommissionRate      │             (narrative home)
                                            └──────────┬───────────┘
                                                        │ never touched by
                                                        v
                                             kernel / make_exchange() / .env
                                             BINANCE_API_KEY (demo trading key)
```

### Recommended Project Structure (files touched, no new directories)
```
trader/agents/macro_guard.py         # FIX-01: route 2 raw-clock reads to self._now()
scripts/<new fee-read script>.py     # FIX-03: new, standalone, read-only
.planning/phases/01-.../*.json       # FIX-03: durable record (already has 01-production-fees.json)
CLAUDE.md                            # FIX-03 + FIX-04: correct two stale claims
trader/strategy/vector_backtest.py   # FIX-04: simulate() gains score_from
trader/strategy/rolling.py           # FIX-04: _score_window widens its slice
trader/strategy/null_baseline.py     # FIX-04: null_pfs offset range + distinctness guard
trader/strategy/spec_evidence.py     # FIX-04: vector_walk_forward test-half widening
tests/test_macro_guard.py            # FIX-01: no test change per D-01 (fix production code)
tests/test_rolling.py                # FIX-04: new 4h/30d decay coverage (Wave-0 gap)
tests/test_vector_backtest.py        # FIX-04: new score_from tests
tests/test_null_baseline.py          # FIX-04: new short-window/offset-distinctness tests
```

### Pattern 1: Backward-compatible `score_from` on `simulate()`
**What:** Add `score_from: int = 0` to `vector_backtest.simulate()`. Internally, `cursor = max(WARMUP, score_from)` replaces the current `cursor = WARMUP` (`trader/strategy/vector_backtest.py:105`). A caller that widens its slice to include real prior history passes `score_from = <index within the widened array where the intended window begins>`; a caller that passes an unwidened array (default) gets today's exact behavior, because `max(WARMUP, 0) == WARMUP`.
**When to use:** Every caller that currently slices a SUB-WINDOW out of a longer available history before calling `simulate` (`rolling._score_window`, `spec_evidence`/`vector_backtest.vector_walk_forward`'s test half, `null_baseline.null_pfs`'s embedding of a rotated array — see Pattern 2 for why the null needs a variant of this).
**Why this is the minimal-blast-radius shape:** `n = len(df)` and the early-return `if n <= WARMUP + 1: return res` (`vector_backtest.py:88-90`) still guard the absolute floor. The existing regression test `test_warmup_signals_are_ignored` (`tests/test_vector_backtest.py:126-131`) calls `simulate()` with NO `score_from` argument and asserts a signal at index 5 is ignored — `max(WARMUP, 0) = WARMUP = 210 > 5`, so this test is untouched by the change. `[VERIFIED: tests/test_vector_backtest.py:126-131]` — quoted:
```python
def test_warmup_signals_are_ignored():
    df = _ramp()
    n = len(df)
    lo = np.zeros(n, bool); lo[5] = True
    r = simulate(lo, np.zeros(n, bool), df, ExitSpec(), RISK)
    assert r.trades == 0
```
**Example (new `_score_window` shape, `trader/strategy/rolling.py:104-147`):**
```python
# Source: this session's design, built on the exact function read at
# trader/strategy/rolling.py:104-147 — replaces the `recent = df.iloc[-n:]`
# line only; the rest of the function (universe, funding_for, per_symbol
# bookkeeping) is unchanged.
from .vector_backtest import WARMUP

def _score_window(compiled, frames, risk_cfg, timeframe, recent_days,
                  btc=None, derivs_for=None):
    n = bars(timeframe, recent_days)
    for sym, df in frames.items():
        if sym.startswith("_") or df is None or len(df) < n:
            continue
        start = max(0, len(df) - n - WARMUP)     # up to WARMUP bars of REAL
        ext = df.iloc[start:].reset_index(drop=True)   # prior history, or
        score_from = len(ext) - n                # less, if none exists —
        # score_from < WARMUP exactly when the symbol's own history is too
        # short to supply a full prefix; simulate()'s max(WARMUP, score_from)
        # then still floors at WARMUP, so no bar is ever fabricated.
        ...
        lo, sh = compiled.entries({timeframe: ext}, ...)      # warmed on ext
        r = simulate(lo, sh, ext, compiled.spec.exit, risk_cfg,
                     symbol=sym, funding=fund, score_from=score_from)
```
**Effect on the D-05 headline case:** 4h/30-day decay check, `n = bars("4h",30) = 180`. If ≥210 bars of real history precede the 30-day window (true for every symbol with more than ~35 days on file), `start = len(df) - 180 - 210`, `ext` is `180+210=390` bars, `score_from = 210`. `cursor = max(210, 210) = 210` in the WIDENED array's own indexing — which lines up EXACTLY with the boundary between prefix and scored window. Candidates in `ext[210:390]` (the true last-30-days window) become eligible; today they are all discarded because `n(=180) <= WARMUP+1(=211)` triggers `simulate`'s empty-return before even reaching the candidate loop.

### Pattern 2: Widening `vector_walk_forward`'s test half (no re-computation needed)
**What:** `vector_backtest.vector_walk_forward` (`trader/strategy/vector_backtest.py:229-260`) already computes `lo, sh = compiled.entries(frames, ...)` on the FULL, untruncated frame before slicing `lo[a:b]` — so indicators ARE already correctly warmed up for the test half using the train half's history. The only bug is that `run(a, b)` (`vector_backtest.py:248-253`) calls `simulate(lo[a:b], sh[a:b], df.iloc[a:b].reset_index(drop=True), ...)` — a LOCALLY re-indexed array — so `simulate`'s own `cursor = WARMUP` still discards the test half's first 210 bars even though the signal computation upstream already used real history to warm them.
**Fix:** Widen only the SLICE boundaries passed into `simulate`, not the entry computation (already correct):
```python
# Source: built on trader/strategy/vector_backtest.py:245-255 (read this
# session); only `run()` changes.
def run(a, b):
    ext_a = max(0, a - WARMUP)
    score_from = a - ext_a          # 0 for the train half (a=0 already)
    sl = slice(ext_a, b)
    return simulate(lo[sl], sh[sl], df.iloc[sl].reset_index(drop=True),
                    compiled.spec.exit, risk_cfg,
                    genome_id=compiled.spec.id, symbol=symbol,
                    exit_sig=None if ex is None else ex[sl],
                    funding=None if fund is None else fund[sl],
                    score_from=score_from)
```
For `train = run(0, cut)`: `a=0` so `ext_a=0`, `score_from=0` — byte-identical to today. For `test = run(cut, len(df))`: the test half now legitimately scores from bar `cut` instead of `cut+210`, recovering exactly the trades D-05 says are lost.
**Where this ALSO fixes admission's null-consistency check:** `Analyst._null_percentiles` (`trader/brain/analyst.py:384-416`) calls `vector_walk_forward` for the test-half PF, then separately calls `null_baseline.assess(..., split=0.7, part="test", ...)`. `assess()` (`trader/strategy/null_baseline.py:84-116`) ALSO already computes `lo, sh = compiled.entries(frames, ...)` on the FULL frame before slicing (`null_baseline.py:98-104`) — so the same Pattern-2 widening applies inside `assess()`'s own `if split is not None:` branch, and inside `null_pfs` itself (see Pattern 3 for the offset-range interaction that follows).

### Pattern 3: The null's rotation offsets need a distinct fix, not just a wider slice
**What:** `null_baseline.null_pfs` (`trader/strategy/null_baseline.py:41-69`) draws rotation offsets from `[WARMUP+1, n-WARMUP-1]` (`null_baseline.py:60`) and calls `simulate(rotate_entries(long, off), ...)` on the SAME (possibly short) `df` it was handed. Two designs are available; **this is explicitly Claude's Discretion per CONTEXT.md** — present both, recommend B:

- **Option A — rotate the widened array.** Pass the same widened `ext`/`score_from` as Pattern 1/2 straight into `null_pfs`, and change the offset bounds to `[max(WARMUP, score_from)+1, len(ext)-WARMUP-1]`. Simple, but for a genuinely short scored window (e.g. the 180-bar 4h/30-day decay window, `ext` ≈ 390 bars), the bound becomes `[211, 179]` — an EMPTY range (`hi_off <= lo_off`, already guarded at `null_baseline.py:61-62`, returns `[]`). This is not a bug in the option, it is D-07's guard firing correctly: a scored window this short cannot support a rotation null AT ALL, prefix or no prefix, because `np.roll` on the full widened array wraps the SAME small usable range around every time.
- **Option B (recommended) — rotate only the scored-window-length entry array, then embed it into the widened array padded with `False` in the prefix.** Compute the rotation over an array of length `n_window` (the true scored-window length, e.g. 180), exactly as `rotate_entries` already does (`np.roll`, preserving clustering — `null_baseline.py:31-38`), THEN place it into a zero-padded array of length `len(ext)` at offset `score_from`, and call `simulate(embedded_long, embedded_short, ext, ..., score_from=score_from)`. Because indicators are warmed via `ext`'s real prefix regardless of where within the window the (rotated) entries land, the offset bound can shrink to something like `[1, n_window-1]` (mod `n_window`) instead of `[WARMUP+1, n_window-WARMUP-1]` — which is a STRICT IMPROVEMENT in available distinct offsets for short windows, not merely a wash. This is a larger change to `null_pfs`'s contract (it now needs both `n_window` and `score_from`/prefix length as arguments) but is more coherent with D-06's "warm up on bars before the window; only the window's own bars are scored" framing, since the ENTRIES the null rotates are exactly the window's own entries, never the prefix's.
**Recommendation:** Implement Option B, but budget it as a distinct, reviewable sub-task from Pattern 1/2 (different function signature, different existing tests to protect — see Common Pitfalls). Whichever option is chosen, D-07's guard (see next) is still required, because even Option B cannot manufacture distinct offsets a genuinely tiny window (e.g., a 10-day/60-bar 4h slice) doesn't have.

### Pattern 4: The D-07 "too few distinct offsets" guard does not exist today — must be added
**What:** The ONLY existing guard in `null_pfs` is `if hi_off <= lo_off: return []` (`null_baseline.py:61-62`) — this checks the range is non-empty, NOT that it contains enough distinct values to make `draws` (e.g. 20-100) meaningfully independent draws. A range of width 2 (e.g. `[211, 213)`) passes this guard today and would silently produce up to 100 draws that are really 2 distinct rotations repeated ~50 times each — understating the null's true variance.
**Fix:** Add an explicit `MIN_DISTINCT_OFFSETS` check: `if hi_off - lo_off < MIN_DISTINCT_OFFSETS: return []`. **The threshold value itself is Claude's Discretion, to be MEASURED, not picked** — the research obligation this session was to map WHERE the check is needed and confirm none exists yet; the plan/execution should measure how many distinct offsets a 4h/30-day post-fix window realistically has under Option A vs Option B (see Pattern 3) and pick a floor that keeps real windows scoreable while refusing degenerate ones. `edge_percentile`/`consistency_p` already return `None` on an empty null (`null_baseline.py:72-81`, `145-174`) and `Analyst.admit` already treats `null_consistency_p is None` as `untestable: True` and REFUSES (`trader/brain/analyst.py:370-376`) — so plumbing a new empty-list case through this guard requires NO downstream changes; the UNTESTED path already exists and is exercised.

### Anti-Patterns to Avoid
- **Scaling `WARMUP` by timeframe or duration:** explicitly forbidden by D-06. `WARMUP` is an indicator-lookback bar count; a 15m spec and a 4h spec both need roughly the same NUMBER of bars to warm up a `donchian(100)` or `ema(200)`, not the same number of days.
- **Recomputing `compiled.entries()` on a truncated frame inside `null_pfs`/`vector_walk_forward`:** both already compute entries on the untruncated frame (`vector_backtest.py:241-242`, `null_baseline.py:98`) — re-truncating before calling `compiled.entries()` would reintroduce exactly the bug `vector_walk_forward`'s own docstring says it avoids (`vector_backtest.py:235-238`, quoted in Pitfall 3 below).
- **Touching `research/slices.py`/`research/evaluate.py`:** their windows already start at each symbol's true stored-history beginning (`slices.discovery()` truncates only the LATER boundary via `before(df, cut)`, never the earlier one — `trader/research/slices.py:64-67`); there is no "history before the window" to recover there. `test_usable_bars_subtracts_the_warmup` (`tests/test_research_slices.py:73-75`) already locks `usable_bars(n) == n - WARMUP` — leave it as-is.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| "Is this feature/indicator warmed up yet" | A new per-feature lookback tracker (`arg_specs`-based auto-computed warmup) | Keep the flat `WARMUP=210` bar-count constant (D-06) | Locked decision; a per-feature computed warmup is a bigger, riskier change than this phase's scope. See Open Questions for a related but out-of-scope gap this surfaced. |
| Cache-freshness clock in `MacroGuard` | A second injectable clock object, a `Clock` protocol/class | Route the two raw reads through the EXISTING `self._now()` method | `self._now()` already exists and is already the seam the test pins (`macro_guard.py:263-264`); adding a second abstraction duplicates it. |
| Production-fee credential handling | A new `Env` method or a change to `make_exchange()` | A standalone script reading `Env.get("BINANCE_PROD_READONLY_KEY")`/`Env.get("BINANCE_PROD_READONLY_SECRET")` directly, building its own bare `ccxt.binanceusdm` instance | D-03 requires the kernel to NEVER read the production key; `make_exchange()` is the kernel's shared credential path (`trader/data/feed.py:23-47`, reads `BINANCE_API_KEY`/`SECRET_KEY` via `Env.binance_keys()`) — reusing or extending it risks a future accidental wire-up. Keep FIX-03 fully outside it. |

**Key insight:** every "don't hand-roll" in this phase is really "don't build a new abstraction when the codebase already has the exact seam you need, sitting one line away from where the bug is."

## Runtime State Inventory

Not applicable — this phase is a rename/refactor/migration trigger check: none of FIX-01/03/04 rename, rebrand, or migrate an existing identifier, key, or stored value. All three are pure logic corrections against existing names, tables, and files. Confirmed by reading each touched module: no schema change, no config key rename, no ID rename.

## Common Pitfalls

### Pitfall 1: `MacroGuard._load_cached`'s TWO raw-clock reads, and which one actually breaks the test
**What goes wrong:** `_load_cached()` has two independent real-wall-clock reads: `age = time.time() - fetched` (`macro_guard.py:141`) and `horizon = datetime.now(timezone.utc) - timedelta(minutes=self.post_min)` (`macro_guard.py:148`). Only the SECOND one (`horizon`) is what fails `test_a_restart_reuses_the_cached_calendar` — confirmed by tracing the test and by actually running it (see below). The `age` check passes trivially in this test because both the cache write and the cache read happen moments apart in real wall-clock time within the same test process, regardless of which real-world day the suite runs on.
**Why it happens:** The test pins `g._now`/`fresh._now` to a fixed instant (`"2026-09-04T12:35:00+00:00"`), and `_find_active_event()` correctly uses `self._now()` (`macro_guard.py:268`). But `_load_cached()` (called from inside `_fetch_calendar()`, which `_find_active_event()` calls) computes `horizon` from the REAL current wall clock, not the pinned fictional one. Once real wall-clock time moves past the pinned date (any run after 2026-09-04), `horizon` = (real "today") − 2h is far later than the cached Non-Farm Employment event's timestamp (2026-09-04T12:30 UTC), so `when >= horizon` is False for every cached event, `_load_cached` discards the entire cache (`macro_guard.py:157-158`, `return` with `self._calendar` left empty), `_fetch_calendar` then tries a live fetch, which the test has made throw (`requests.get` monkeypatched to `_boom`), and `check()` correctly (but wrongly for the test's intent) reports `"no calendar source reachable"` — `active: False`.
**How to avoid:** Route `horizon` (and, per D-01, `age` too, for consistency and to guard against a future test/scenario that exploits the same real-vs-fictional divergence) through `self._now()`: `horizon = self._now() - timedelta(minutes=self.post_min)`, `age = self._now().timestamp() - fetched`. `self._now()` returns a UTC-aware `datetime`; `.timestamp()` gives the same POSIX-epoch float `time.time()` would, so this is a drop-in replacement with no unit mismatch.
**Warning signs:** Any test that (a) pins `_now()` to a date OTHER than the real current date, AND (b) writes a cache file and reads it back within the same test, is exposed to this bug. `test_an_all_past_cache_is_not_mistaken_for_a_quiet_week` (`tests/test_macro_guard.py:325-345`) does NOT pin `_now()`, so it is unaffected either way — confirmed safe by tracing.
**Empirically confirmed this session:**
```
$ /home/sarmad/trader/venv/bin/python -m pytest tests/test_macro_guard.py -v
... 20 passed, 1 failed ...
FAILED tests/test_macro_guard.py::test_a_restart_reuses_the_cached_calendar - AssertionError: restart lost the calendar
```
`[VERIFIED: tests/test_macro_guard.py — pytest run this session, main-checkout venv against worktree code]`

### Pitfall 2: `_from_finnhub`'s `date.today()` is a THIRD raw-clock read, and should probably stay raw
**What goes wrong (if "fixed" carelessly):** `_from_finnhub()` (`macro_guard.py:170-200`) builds its API query range from `date.today()` (`macro_guard.py:174`), local system date, not filtered through `self._now()`. If a future change routes this through `self._now()` too "for consistency," any test that pins `_now()` to a different date than a real Finnhub-mocked response's date range would start silently requesting the wrong week from a REAL (non-mocked) Finnhub call in production.
**Why it happens:** This read exists to ask "what does the real world's calendar look like for the next 7 real days" — a genuinely different question from "what time is it, for the purpose of judging whether a cached/live event is currently active." D-01 does not name this line; it is included here because the instruction says to check the rest of the class.
**How to avoid:** Leave `date.today()` as-is. It is not implicated in the failing test (no test currently pins `_now()` while also exercising a live/mocked Finnhub date-range assertion in a way that would break).
**Warning signs:** none currently; flagged for completeness only.

### Pitfall 3: `_score_window`'s `universe` dict must keep carrying FULL frames, not the widened window
**What goes wrong:** `_score_window` (`rolling.py:104-147`) already has a load-bearing comment: *"universe carries the FULL frames, not `recent` — a cross-sectional feature aligns peers onto the base symbol's bars by timestamp, so a longer peer frame is harmless while a frame sliced to match `recent` would produce NaN for the whole window"* (`rolling.py:118-122`, quoted verbatim). When implementing Pattern 1's `ext` widening, do NOT accidentally slice `universe`'s peer frames to match `ext` either — peers should stay at their full length exactly as they are today. Only the anchor symbol's own frame widens.
**Why it happens:** It is tempting to make "the frame passed to `compiled.entries()`" and "the frames peers are keyed by in `universe`" symmetric during a refactor; they are deliberately asymmetric today for a documented reason unrelated to this fix, and the fix must not disturb it.
**How to avoid:** Keep `universe = {s: {timeframe: f} for s, f in frames.items() ...}` (`rolling.py:108-109`) unchanged — it already uses the FULL per-symbol frames from the outer `frames` dict, not `recent`/`ext`. Only the anchor `sym`'s own `df`/`ext` and the call to `compiled.entries({timeframe: ext}, ...)` change.
**Warning signs:** A cross-sectional spec (`xs_rank`, `breadth`, `dispersion`) silently taking zero trades in a widened-window test where it previously took trades in the old `recent`-only version — a symptom the codebase has hit before (`brain/analyst.py:392-396` has an identical comment guarding the same pattern in `_null_percentiles`).

### Pitfall 4: `WARMUP=210` is already thinner than several registered features' own maximum declared lookback
**What goes wrong:** This is NOT something FIX-04 is scoped to fix (D-06 keeps `WARMUP` a flat constant), but research surfaced it and it is relevant risk context for D-08's before/after evidence and for anyone tempted to raise `WARMUP` later. Several registered features accept lookback arguments well above 210: `zscore`/`pct_rank`/`sma_of`/`dd_from_high` allow up to 500 (`trader/strategy/features.py:225,231,248,407`), `funding_z`/`funding_cum`/`funding_pct` up to 2000-4000 (`trader/strategy/features_deriv.py:56,65,155`), `oi_z`/`taker_ratio_z`/`ls_ratio_z`/`ls_account_ratio_z`/`basis_z` up to 2000 (`features_deriv.py:84,95,122,138,149`). The live 15m spec `spec_funding_filtered_trend_pullback` uses `ema(200)` (`entry_long`/`entry_short`, read from `strategies.spec_json` this session — see Pitfall 5), which is only a 10-bar margin under `WARMUP=210`.
**Why it happens:** `WARMUP` was chosen once as a flat safety margin; the feature registry's `arg_specs` ranges were extended independently over time (per CLAUDE.md's "14 new features added" note) without re-checking the margin still holds for the widest ones.
**How to avoid (within this phase's scope):** Nothing — D-06 explicitly keeps `WARMUP` a fixed bar count and this phase does not touch feature `arg_specs`. Flag it as an Open Question (below) for a future phase; do not let it scope-creep into FIX-04.
**Warning signs:** A future spec using `zscore(x, 400)` or `funding_z(1000)` on a 15m/1h frame would have its FIRST ~200-800 "scored" trades computed on a not-yet-stable feature value, currently invisible because nothing checks `arg_specs`' upper bound against `WARMUP`.

### Pitfall 5: The durable FIX-03 record must NOT land in `data/`
**What goes wrong:** `data/*.json` and `data/*.db*` are gitignored (`[VERIFIED: .gitignore]`, quoted: `data/*.db*` / `data/*.json`). A script that writes its measurement to `data/production_fees.json` (matching the pattern of `data/agent_weights.json` etc.) would produce a file that is invisible to git — not "durable" in the sense D-03/D-04 need (a record that survives a fresh clone / is reviewable in a PR).
**Why it happens:** Every OTHER "measured state" file in this codebase (`data/agent_weights.json`, `data/ewa_state.json`, `data/doctrine.json`, `data/agent_calibration.json`) lives under `data/` by convention, and that convention is exactly the wrong one for a fact that needs to be committed.
**How to avoid:** The discuss-phase session already established the right pattern: `.planning/phases/01-correctness-booking-fixes/01-production-fees.json` (already exists, `[VERIFIED: this file was read directly this session]`). The plan should have the new script WRITE to a path under `.planning/phases/01-correctness-booking-fixes/` (or a project-root non-gitignored location the operator prefers), not under `data/`.
**Warning signs:** `git status` after running the script shows nothing new — that is the tell that the write landed in a gitignored path.

## Code Examples

### FIX-01 — the exact two lines to change
```python
# Source: trader/agents/macro_guard.py:133-165 (read this session)
# BEFORE (both raw-clock reads):
    def _load_cached(self) -> None:
        try:
            raw = json.loads(self._cache_path.read_text())
            fetched = float(raw["fetched"])
        except Exception:
            return
        age = time.time() - fetched                                    # <-- L141
        if age > _CACHE_MAX_AGE:
            return
        horizon = datetime.now(timezone.utc) - timedelta(               # <-- L148
            minutes=self.post_min)
        ...

# AFTER (both routed through the existing injectable clock):
    def _load_cached(self) -> None:
        try:
            raw = json.loads(self._cache_path.read_text())
            fetched = float(raw["fetched"])
        except Exception:
            return
        age = self._now().timestamp() - fetched
        if age > _CACHE_MAX_AGE:
            return
        horizon = self._now() - timedelta(minutes=self.post_min)
        ...
```
No test changes required or permitted by D-01. `test_a_restart_reuses_the_cached_calendar` should pass unmodified once these two lines change — traced by hand above and consistent with every other existing macro_guard test (none of which regress; see the per-test trace under Validation Architecture).

### FIX-03 — script skeleton (never touches `make_exchange`/kernel credentials)
```python
# Source: pattern for a standalone script, informed by trader/data/feed.py:23-47
# (make_exchange, read this session — deliberately NOT reused) and
# trader/core/config.py:21-42 (Env, read this session — Env.get() reused,
# Env.binance_keys() deliberately NOT reused).
import ccxt
from trader.core.config import Env

key = Env.get("BINANCE_PROD_READONLY_KEY")
secret = Env.get("BINANCE_PROD_READONLY_SECRET")
if not key or not secret:
    raise SystemExit("set BINANCE_PROD_READONLY_KEY/_SECRET in .env first")

ex = ccxt.binanceusdm({"apiKey": key, "secret": secret,
                       "enableRateLimit": True,
                       "options": {"defaultType": "future"}})
# demo mode is NEVER enabled here — this must hit production to answer the
# question FIX-03 exists to answer. No ex.enable_demo_trading(True) call.
restrictions = ex.privateGetAccountApiRestrictions... # or the sapi equivalent
                                                       # used for 01-production-fees.json
for symbol in DECLARED_16:                            # from the Donchian spec's
    rate = ex.fapiPrivateGetCommissionRate({"symbol": ex.market(symbol)["id"]})
    ...
# write result to .planning/phases/01-correctness-booking-fixes/<name>.json,
# NEVER to data/ (gitignored — see Pitfall 5)
```
The exact `apiRestrictions` ccxt call path used to produce `01-production-fees.json` should be recovered/confirmed during planning (it was run ad-hoc during discuss-phase, not from a committed script) — this is the one piece of FIX-03 this research could not verify from source, since no such script exists in the repo yet. Flagged under Open Questions.

### FIX-04 — the equivalence harness's own WARMUP usage is NOT part of the caller set that needs widening
```python
# Source: scripts/backtest_equivalence.py:61 (read this session) — UNCHANGED
for i in range(WARMUP, n - 1):
    window = df.iloc[max(0, i - 400): i + 1]
    ...
```
This loop replays the LEGACY bar-by-bar evaluator over the FULL, untruncated frame (never a sub-window), and its `range(WARMUP, n-1)` exists to line up 1:1 with `simulate()`'s own `cursor=WARMUP` on that SAME full, unwidened frame (`engine_equivalence`, `backtest_equivalence.py:94-95`, calls `simulate(lo, sh, df, ...)` with no `score_from`). Because `score_from` defaults to 0 and `max(WARMUP, 0) == WARMUP`, this file requires ZERO changes — confirmed by reading the whole file this session. `scripts/bench_vector_backtest.py` similarly calls `vector_backtest()` on full, unsliced frames (`bench_vector_backtest.py:49-53`) — also zero changes needed. This directly answers D-09's question about the equivalence script's loop: it is part of the contract as-is and must NOT move.

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|---------------|--------|
| `_score_window`/`vector_walk_forward`/`null_pfs` treat `WARMUP` as "skip first 210 bars of whatever array I was handed" | `simulate()` takes an explicit `score_from`; windowed callers widen their slice by up to `WARMUP` bars of genuine prior history | This phase (FIX-04) | 4h decay checks (180-bar windows) go from 100% empty to scoreable; 4h/90d admission recovers up to 39% of previously-discarded bars; walk-forward test halves recover their first 210 bars |
| `MacroGuard._load_cached` mixes `self._now()` (event-window logic) with raw `time.time()`/`datetime.now()` (cache-freshness logic) | Both routed through `self._now()` | This phase (FIX-01) | A pinned/injected clock (tests, or any future backtest-replay use of MacroGuard) now sees ONE consistent notion of "now" throughout the class |
| Production Binance taker fee: "UNMEASURED" (CLAUDE.md, prior to 2026-09-13) | Measured: 0.05% taker / 0.02% maker uniform across all 16 declared symbols, demo underquotes 14/16 by 1bp | 2026-09-13 (D-03a, this phase makes it repeatable + durably recorded) | `config.yaml`'s `taker_fee_pct: 0.04` is now KNOWN to be a demo-only figure, not merely assumed close enough |

**Deprecated/outdated:** CLAUDE.md's WARMUP bullet ("harmless at 4h... not a fault at 4h or below") is factually superseded by D-05's measurement and must be corrected as part of this phase's documentation work (not a separate task — CLAUDE.md is a living doc per the project's own convention of correcting itself inline, see the many "CLOSED"/"Fixed" annotations already in the file).

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | The exact ccxt/REST call path used to produce `01-production-fees.json`'s `apiRestrictions` block was NOT recovered from a committed script (none exists) — the FIX-03 script skeleton above infers a plausible equivalent (`ex.privateGetAccountApiRestrictions` or the `sapi` restrictions endpoint) rather than citing verified working code. | Code Examples (FIX-03) | Low — this is one auxiliary read (key restrictions), not the core `commissionRate` measurement (which IS verified against `01-production-fees.json`'s actual recorded output). Worst case the script needs one extra debugging pass against the real ccxt method name during execution. |
| A2 | `Option B` (Pattern 3) for threading the warmup prefix through the rotation null is a NEW design proposed this session, not lifted from any existing code path in this repo — it is architecturally consistent with D-06 but has not been prototyped or run. | Architecture Patterns, Pattern 3 | Medium — if Option B proves harder to implement than estimated, Option A (simpler, but produces more UNTESTED verdicts for short windows) is a safe fallback already fully specified, and D-07's guard covers the gap in both cases. |
| A3 | The `MIN_DISTINCT_OFFSETS` threshold for the D-07 null-offset guard is unmeasured — CONTEXT.md explicitly defers this to execution-time measurement, so no number is asserted here. | Architecture Patterns, Pattern 4 | Low — explicitly flagged as needing measurement, not asserted as fact; the planner is expected to add a measurement step, not pick a number from this document. |

## Open Questions

1. **Should the D-07 "too few distinct offsets" guard also apply retroactively to the CURRENT (pre-fix) `null_pfs` bound `[WARMUP+1, n-WARMUP-1]`, independent of the score_from widening?**
   - What we know: the current guard (`hi_off <= lo_off`) only checks the range is non-empty, not that it has enough distinct values for `draws` independent samples (see Pitfall/Pattern 4).
   - What's unclear: whether any CURRENTLY-PASSING caller (e.g., research's `evaluate.py`, which passes large full-history frames) would newly start reporting UNTESTED if a stricter distinctness floor were applied everywhere, not just to the newly-widened windowed callers.
   - Recommendation: measure `hi_off - lo_off` for every existing caller's typical `n` (research discovery slices are large — thousands of bars — so this is very unlikely to bite there) before setting a global floor; if it never binds for `research/evaluate.py`, applying the guard everywhere is strictly safer than scoping it only to the newly-widened windowed callers.

2. **Exact ccxt method for the `apiRestrictions` read in the FIX-03 script.**
   - What we know: `01-production-fees.json` already contains a correctly-shaped `key_restrictions` block (`ipRestrict`, `enableReading`, etc. — matches Binance's `/sapi/v1/account/apiRestrictions` response shape).
   - What's unclear: which literal ccxt call produced it (no script committed).
   - Recommendation: during planning/execution, check `ccxt.binanceusdm`'s implicit API method names (likely `sapiGetAccountApiRestrictions` or similar) against the installed ccxt version in `venv/`, or fall back to a raw signed `requests` call to `/sapi/v1/account/apiRestrictions` if ccxt doesn't expose it on the `binanceusdm` class (it may only exist on the spot `binance` class, sharing the same API key).

3. **`WARMUP=210` vs. features with declared `arg_specs` lookback up to 4000 (Pitfall 4) — real risk or theoretical?**
   - What we know: `spec_funding_filtered_trend_pullback` (live, 15m) uses `ema(200)`, a 10-bar margin under `WARMUP`. No currently-live spec uses a feature near the 500-4000 end of the range.
   - What's unclear: whether any RETIRED or historical spec ever ran at a widened-lookback setting that would have silently under-warmed.
   - Recommendation: out of scope for this phase per D-06; log as a candidate for a future phase, do not let it expand FIX-04's scope.

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| `venv/` (Python + deps) | Running any test | ✓ (main checkout only: `/home/sarmad/trader/venv/bin/python`) | confirmed working this session (ran pytest successfully) | Worktree has no `venv/` — always invoke the main checkout's interpreter, with cwd set appropriately (see Validation Architecture) |
| `data/candles.db`, `data/derivs.db` | Full-suite "environment artifact" tests (`test_research_runner.py`, `test_research_universe_depth.py`), FIX-04's before/after evidence measurement | ✓ in main checkout (`/home/sarmad/trader/data/`), ✗ in worktree | 323MB / 73MB, confirmed present this session | See "Full-suite-green execution strategy" below — do NOT copy/symlink `data/luffy.db` (624MB, main checkout's LIVE journal) |
| `data/luffy.db` | FIX-04's D-08 before/after spec evidence (reading `strategies.spec_json`) | ✓ in main checkout, read-only access confirmed safe this session via `sqlite3.connect("file:...?mode=ro", uri=True)` | live, 624MB | Never construct a real `Journal(<main-checkout-path>)` — even though its schema statements are `CREATE TABLE IF NOT EXISTS`, `Journal.__init__` unconditionally runs a real write transaction (`c.execute("UPDATE strategies SET state_changed_at=...")`, `trader/core/journal.py:193-197`) against whatever path it's given. Use a raw read-only sqlite3 connection instead. |
| Binance production API access | FIX-03's fee-read script | Partial — a key was used once during discuss-phase and should now be treated as revoked/exposed (D-03a) | n/a | A fresh key is needed to re-run/verify the script; this is a human checkpoint per D-03, already anticipated in CONTEXT.md |

**Missing dependencies with no fallback:** none — every dependency this phase needs is either already available (main checkout venv+data) or is an anticipated human step (FIX-03's key).

**Missing dependencies with fallback:** worktree lacks `venv/`/real data; fallback is documented below (run from main checkout's interpreter against an isolated copy of the code+data, never against the live main checkout's working tree).

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest (confirmed this session: `plugins: anyio-4.14.2`, ran successfully against `tests/test_macro_guard.py`) |
| Config file | none found (no `pytest.ini`/`pyproject.toml [tool.pytest]` section located; pytest uses default rootdir discovery from the invocation cwd) |
| Quick run command | `/home/sarmad/trader/venv/bin/python -m pytest tests/test_macro_guard.py tests/test_rolling.py tests/test_vector_backtest.py tests/test_null_baseline.py tests/test_selection_window_widens.py -v` (run from the worktree root; all five files were confirmed this session to import/execute correctly against the worktree's `trader/` package using the main checkout's venv, with no dependency on `data/`) |
| Full suite command | `/home/sarmad/trader/venv/bin/python -m pytest tests/` — but see "Full-suite-green execution strategy" below for WHERE to run it |

### Full-suite-green execution strategy (D-02) — critical safety note
The main checkout at `/home/sarmad/trader` is git branch `fix/market-data-truth` — confirmed this session to be the EXACT merge-base of the worktree's branch (`docs/claude-md-refresh`), i.e. the worktree's code is main-checkout's code plus a few docs-only commits so far, and Phase 1's actual code changes will be new commits on top of that. **The main checkout is presumed to be the LIVE production checkout** (per CLAUDE.md's watchdog/kernel description) — do not `git checkout` a different commit there, do not point a real `Journal` at its `data/luffy.db`, and do not run `python -m trader.kernel` from it.

Recommended safe procedure for verifying "full suite green, in an environment with the data" once FIX-01/FIX-04 code changes exist as commits:
1. Create a SEPARATE git worktree from the shared object store (not touching the main checkout's working tree): `git worktree add /tmp/luffy-verify <phase-1-branch-or-commit>` (run from within a worktree that already has permission to do so, or ask the operator).
2. Symlink ONLY the read-only-needed data files into it: `ln -s /home/sarmad/trader/data/candles.db /tmp/luffy-verify/data/candles.db` and the same for `derivs.db`. Do NOT symlink `luffy.db` — no test in the suite opens it by a literal path (confirmed by grep this session: only `tests/test_phase0.py`-style `Journal(tmp_path / "j.db")` patterns were found; the only literal `"data/..."` paths in any test are `data/authored_specs/*.json` and `data/seed_specs`, both already tracked in git and present in the worktree).
3. Run `venv/bin/python -m pytest tests/` there (using the MAIN CHECKOUT's `venv/bin/python` interpreter, invoked with cwd `/tmp/luffy-verify` so relative `data/...` paths resolve to the symlinks).
4. Record which environment was used (per D-02) and delete the scratch worktree afterward (`git worktree remove /tmp/luffy-verify`).

This avoids: (a) ever writing to the live main checkout, (b) the previously-observed hazard of test runs dirtying `knowledge/` in the worktree under test (a scratch worktree's dirty vault pages are simply discarded with it), and (c) the two checkouts' `trader/` packages colliding on `sys.path`.

**Confirmed this session (safe to run directly in the current worktree, no data needed):** the five test files listed above as the "quick run command" all execute correctly against the worktree using the main checkout's venv, with `test_macro_guard.py` showing exactly 1 failure (the target of FIX-01) and 20 passes.

### Phase Requirements → Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| FIX-01 | `test_a_restart_reuses_the_cached_calendar` passes | unit | `venv/bin/python -m pytest tests/test_macro_guard.py -v` | ✅ (test exists; confirmed failing this session; no test edits needed per D-01) |
| FIX-01 | Full suite green | integration | `venv/bin/python -m pytest tests/` (in the isolated-worktree-with-data setup above) | ✅ (suite exists; D-02 already measured baseline: 15 failed/1203 passed in worktree, only macro_guard genuine) |
| FIX-03 | Production fee measured + recorded, repeatably | manual + smoke | New script under `scripts/`, run once with a fresh key; smoke-test the script's argument/error handling (missing env vars) without hitting the network | ❌ Wave 0 — script doesn't exist yet; a smoke test for its "missing credentials" error path can be pure-Python (no network) |
| FIX-04 | 4h/30-day decay window scores real trades post-fix | unit | `venv/bin/python -m pytest tests/test_rolling.py -v -k decay` | ❌ Wave 0 — NO existing test exercises a 4h timeframe or a window ≤211 bars; every existing `test_rolling.py`/`test_selection_window_widens.py` decay/verdict test uses `"1h"` with `recent_days=60` (1440 bars, far above WARMUP) or monkeypatches `_score_window` away entirely. Confirmed by reading the full file this session. |
| FIX-04 | `simulate()`'s `score_from` default preserves current behavior | unit | `venv/bin/python -m pytest tests/test_vector_backtest.py -v -k warmup` | ✅ existing (`test_warmup_signals_are_ignored`) — must keep passing unmodified; new tests should ADD `score_from` coverage alongside it, not replace it |
| FIX-04 | Rotation null offset-distinctness guard (D-07) | unit | `venv/bin/python -m pytest tests/test_null_baseline.py -v` | ❌ Wave 0 — no existing test constructs a short-enough `df` to hit the `hi_off <= lo_off` path at all (smallest `n` used is 900, `WARMUP`-bound range is comfortably positive) |
| FIX-04 | `scripts/backtest_equivalence.py` PASS | integration/smoke | `venv/bin/python scripts/backtest_equivalence.py` (needs `data/`, run in the isolated-worktree-with-data setup) | ✅ existing script; `test_engine_equivalence_on_legacy_genomes` (`tests/test_vector_backtest.py:178-197`) already exercises its `engine_equivalence` function in a pytest-visible, no-real-data way |
| FIX-04 | `scripts/bench_vector_backtest.py` > 20x | integration/smoke | `venv/bin/python scripts/bench_vector_backtest.py` (needs `data/`) | ✅ existing script, no pytest wrapper — run manually/in CI as a smoke gate |
| FIX-04 | D-08 before/after evidence for Donchian + funding-filtered spec | manual, evidence-gathering | New read-only script (see Code Examples pattern in D-08 section below) | ❌ Wave 0 — no existing script calls `rolling.has_decayed`/`recent_verdict` standalone; must be new, and must avoid ever constructing a real `Journal` against the main checkout's `data/luffy.db` |

### Sampling Rate
- **Per task commit:** `venv/bin/python -m pytest tests/test_macro_guard.py tests/test_rolling.py tests/test_vector_backtest.py tests/test_null_baseline.py tests/test_selection_window_widens.py -v` (fast, no data dependency, confirmed runnable directly against the worktree)
- **Per wave merge:** full suite in the isolated-worktree-with-data setup, plus `scripts/backtest_equivalence.py` and `scripts/bench_vector_backtest.py`
- **Phase gate:** Full suite green (D-02) + both invariant scripts PASS (D-09) + D-08's before/after evidence surfaced to the operator, before the kernel is allowed to run a decay sweep on the fixed code

### Wave 0 Gaps
- [ ] `tests/test_rolling.py` — new tests: a 4h-timeframe `_score_window`/`has_decayed`/`recent_verdict` case with a window ≤ `WARMUP+1` bars that currently returns "idle — too few trades" and should return real trade evidence post-fix (covers D-05's exact headline claim)
- [ ] `tests/test_vector_backtest.py` — new tests: `simulate(..., score_from=N)` scores only bars `>= max(WARMUP, N)`; a call with `score_from=0` (or omitted) is byte-identical to today's behavior on every existing test in the file
- [ ] `tests/test_null_baseline.py` — new tests: a short-`df` case that currently returns `[]` via `hi_off <= lo_off`, and (once Pattern 3/4 land) a case with a wide-enough range but too few DISTINCT offsets relative to `draws`, asserting the new guard also returns `[]`/UNTESTED rather than a low-variance null
- [ ] New standalone read-only evidence script (D-08) — not a pytest file, but should exist under `scripts/` so before/after evidence is reproducible; must use `sqlite3.connect("file:...?mode=ro", uri=True)` for reading `strategies.spec_json` from the main checkout, and either a scratch `Journal(tmp_path/"scratch.db")` or no `Analyst` construction at all (calling `rolling.has_decayed`/`recent_verdict` directly, since neither touches `self.journal`) — confirmed this session that `Analyst.evaluate()`/`admit()`/`_ctx()` never call `self.journal`, only `review_deployed()` and `confirm_on_tv()` do (`[VERIFIED: trader/brain/analyst.py]`, full file read this session)
- [ ] FIX-03's script — a smoke test for its credential-missing error path (no network needed)

## Security Domain

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | Partially (FIX-03 only) | Binance API key/secret via `.env`, never hardcoded — matches existing `Env` pattern (`trader/core/config.py:21-42`) |
| V3 Session Management | No | No session concept in this phase's scope |
| V4 Access Control | Yes (FIX-03) | Read-only key scope enforced at the EXCHANGE side (Binance's "Enable Reading" toggle), not something this codebase can enforce in software — the script's only software-side control is which env vars it reads and that it never calls an order-placing ccxt method |
| V5 Input Validation | Minimal | FIX-04's `score_from` is an internally-computed integer, never user/network input; FIX-03's script has fixed, hardcoded symbol lists (the declared 16) |
| V6 Cryptography | No | No new cryptographic operations |

### Known Threat Patterns for this stack

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Production API key leakage via logs/transcripts | Information Disclosure | Already identified as having occurred (D-03a: key pasted into chat transcript, operator advised to revoke). The new script must never log the raw key/secret; follow `macro_guard.py`'s own `_scrub()` pattern (`macro_guard.py:49-52`) if the script ever logs error text that might embed a credential. |
| Kernel accidentally gaining production trading capability | Elevation of Privilege | D-03's explicit requirement: distinct env var names (`BINANCE_PROD_READONLY_*`) that `make_exchange()`/`Env.binance_keys()` never reads, so no code path in the kernel can pick up the production key even by accident. Verified this session: `Env.binance_keys()` only reads `BINANCE_API_KEY`/`BINANCE_SECRET_KEY` (`trader/core/config.py:32-34`) — a new, differently-named var is invisible to it by construction. |
| A read-only-intended script accidentally placing a live order | Tampering | Never call an ccxt trading method (`create_order`, etc.) from the FIX-03 script; only call `fapiPrivateGetCommissionRate` and the restrictions-read endpoint. Code-review checklist item, not an automatable control within this codebase. |
| Test suite accidentally writing to the live production journal/vault | Tampering (self-inflicted) | Covered under Validation Architecture's isolated-worktree strategy; never construct a real `Journal`/`Vault` against the main checkout's real paths. |

## Sources

### Primary (HIGH confidence — read directly this session)
- `trader/agents/macro_guard.py` (full file) — FIX-01 root cause
- `tests/test_macro_guard.py` (full file) — FIX-01 test behavior, confirmed by running it
- `trader/strategy/vector_backtest.py` (full file) — FIX-04 core engine
- `trader/strategy/rolling.py` (full file) — FIX-04 windowed callers
- `trader/strategy/null_baseline.py` (full file) — FIX-04 rotation null
- `trader/strategy/spec_evidence.py` (full file) — FIX-04 gauntlet/missing-data semantics
- `trader/brain/analyst.py` (full file) — FIX-04 admission/decay call sites, journal-write audit
- `trader/research/slices.py`, `trader/research/evaluate.py` (full files) — confirmed unaffected by FIX-04
- `scripts/backtest_equivalence.py`, `scripts/bench_vector_backtest.py` (full files) — D-09 invariants
- `trader/core/journal.py` (partial, `__init__`/`_conn`/`_tx`/write methods) — confirmed `Journal()` construction writes even on an already-populated DB
- `trader/data/feed.py` (partial, `make_exchange`, `DataFeed.__init__`, `cached_ohlcv`, `fetch_ohlcv` header) — confirmed read-only safety of `cached_ohlcv`, confirmed `make_exchange`'s credential path
- `trader/core/config.py` (full file) — `Env`, `load_config`
- `trader/strategy/features.py`, `trader/strategy/features_deriv.py` (grepped registrations) — Pitfall 4's lookback-range evidence
- `data/authored_specs/donchian_breakout_trail.json` (full file, present in worktree) — Donchian's exact entry/exit geometry
- `.planning/phases/01-correctness-booking-fixes/01-production-fees.json` — FIX-03's already-measured data
- `.planning/phases/01-correctness-booking-fixes/01-CONTEXT.md`, `.planning/REQUIREMENTS.md`, `.planning/STATE.md` — phase scope and locked decisions
- `.gitignore` — confirmed `data/*.db*`, `data/*.json` are gitignored
- `config.yaml` (grepped keys) — confirmed `taker_fee_pct`, `decay_recent_days`, `select_recent_days`, etc.
- Live command executions this session: `pytest tests/test_macro_guard.py -v` (main checkout venv against worktree code); read-only `sqlite3` queries against `/home/sarmad/trader/data/luffy.db` via `file:...?mode=ro` URI (confirmed no write occurs); `git` branch/merge-base inspection confirming the main checkout is on `fix/market-data-truth`, the exact ancestor of this worktree's branch

### Secondary (MEDIUM confidence)
- None used — all findings this session were verified directly against source or by execution; no web search was needed since this phase requires no external library research.

### Tertiary (LOW confidence)
- The exact ccxt method name for the `apiRestrictions` read (Open Question 2) — inferred from the shape of `01-production-fees.json`, not verified against a committed script or the installed ccxt version's method list.

## Metadata

**Confidence breakdown:**
- FIX-01: HIGH — root cause identified, traced line-by-line, and confirmed by actually running the failing test this session.
- FIX-03: HIGH for what's already measured (D-03a); MEDIUM for the exact `apiRestrictions` ccxt call (one auxiliary field, not the core fee measurement).
- FIX-04: HIGH for the caller map, the bug mechanism, and the `score_from`-default backward-compatibility argument (all verified by reading source + the existing regression test). MEDIUM for the specific null-baseline threading design (Pattern 3's Option A/B) — this is a genuine design choice this research surfaces but does not resolve, exactly matching CONTEXT.md's delegation of that choice to execution-time discretion.

**Research date:** 2026-09-13
**Valid until:** This is a fast-moving internal codebase (CLAUDE.md is updated same-day repeatedly); treat this research as valid only until the FIX-01/03/04 commits land — after that, CLAUDE.md itself becomes the source of truth for what was actually fixed and how, and this document's line-number citations may drift.
