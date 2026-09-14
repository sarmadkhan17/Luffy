# Phase 1: Correctness & Booking Fixes - Pattern Map

**Mapped:** 2026-09-13
**Files analyzed:** 11 (2 source fixes, 1 new script, 6 strategy-engine touches, 1 CLAUDE.md doc edit, plus their test files)
**Analogs found:** 9 / 11 (exact or role-match); 2 are direct self-modifications with no separate analog needed

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|---|---|---|---|---|
| `trader/agents/macro_guard.py` (`_load_cached`) | service (in-process agent) | transform (clock-seam refactor) | itself — `_find_active_event` in the same file already does this correctly | exact (in-file precedent) |
| `scripts/read_prod_fee.py` (new, name at Claude's discretion) | script / one-shot CLI, read-only external API call | request-response (REST read, no writer loop) | `scripts/monitor.py` (read-only venue query script) | role-match |
| `trader/strategy/vector_backtest.py` (`simulate`, new `score_from` param) | service / core computation engine | batch/transform (vectorized backtest) | itself — `vector_walk_forward`'s existing `run(a,b)` closure is the in-file precedent for windowed slicing | exact (in-file precedent) |
| `trader/strategy/rolling.py` (`_score_window`) | service / windowed evaluator | batch/transform | `rolling_windows` in the same file (sibling function, same module) | exact |
| `trader/strategy/null_baseline.py` (`null_pfs`, offset guard) | service / statistical control | batch/transform | itself — `edge_percentile`'s existing `if not null: return None` is the precedent for the UNTESTED-not-fabricated shape | exact (in-file precedent) |
| `trader/strategy/spec_evidence.py` (`run_gauntlet` / walk-forward consumer) | service | batch/transform | `vector_backtest.vector_walk_forward` (the function it calls) | exact |
| `CLAUDE.md` (WARMUP bullet, fee bullet) | doc/config | n/a | itself — the file's own established "Fixed:"/"CLOSED \<date\>:" annotation convention | exact (in-file precedent) |
| `tests/test_macro_guard.py` | test | request-response (mocked network) | `test_a_restart_reuses_the_cached_calendar` (existing, unmodified per D-01) + `_guard_ff` helper | exact — no new test needed, existing one must pass unmodified |
| `tests/test_vector_backtest.py` (new `score_from` cases) | test | unit | `test_warmup_signals_are_ignored` (same file) | exact |
| `tests/test_rolling.py` (new 4h/decay case) | test | unit | existing decay/verdict tests in the same file (1h/60d cases) | role-match (different timeframe) |
| `tests/test_null_baseline.py` (new short-window / distinctness cases) | test | unit | existing `null_pfs` tests in the same file | role-match |

## Pattern Assignments

### `trader/agents/macro_guard.py` (service, transform) — FIX-01

**Analog:** itself — `_find_active_event`, which already routes through the injectable clock correctly (line ~268: `self._now()`), versus the buggy `_load_cached` two lines below it.

**Bug location** (lines 133-165, read this session):
```python
def _load_cached(self) -> None:
    """A day-old calendar still lists today's events; nothing beats it
    except a fresh one, and everything beats no guard at all."""
    try:
        raw = json.loads(self._cache_path.read_text())
        fetched = float(raw["fetched"])
    except Exception:
        return
    age = time.time() - fetched                      # BUG: raw clock
    if age > _CACHE_MAX_AGE:
        return
    horizon = datetime.now(timezone.utc) - timedelta( # BUG: raw clock
        minutes=self.post_min)
    events = []
    for e in raw.get("events", []):
        ...
        if when >= horizon:
            events.append({"event": e["event"], "when": when})
    if not events:
        return
    self._calendar = events
    self._cal_fetched = fetched
    self._sources_ok = True
    if age > _CACHE_STALE:
        log.warning(...)
```

**Correct-clock pattern to copy** (`self._now()` is already defined and already used correctly elsewhere in the class — do not invent a new abstraction):
```python
age = self._now().timestamp() - fetched
if age > _CACHE_MAX_AGE:
    return
horizon = self._now() - timedelta(minutes=self.post_min)
```

**Do NOT touch** (`_from_finnhub`, line ~174): `today = date.today()` is a THIRD raw-clock read but is out of scope — it asks "what does the real calendar look like for the next 7 real days", not "what time is it for freeze-window purposes". Leave it raw.

**Error handling pattern already in file** (copy this shape for any related touch): bare `try/except Exception: return` on cache load — fail-open, matches the module's documented "any network/parse error means no freeze" contract; do not add a scarier error path here.

---

### `scripts/read_prod_fee.py` (new script, request-response) — FIX-03

**Analog:** `scripts/monitor.py` (lines 1-40) for script shell conventions: docstring stating purpose + exact invocation command, `sys.path.insert(0, str(Path(__file__).resolve().parents[1]))` before internal imports, `# noqa: E402` on post-path-insert imports.

**Imports/shell pattern to copy** (`scripts/monitor.py:20-31`):
```python
from __future__ import annotations

import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trader.core.config import load_config          # noqa: E402
from trader.core.journal import Journal             # noqa: E402
from trader.data.feed import DataFeed, make_exchange  # noqa: E402
```
Adapt: this script must NOT import/call `make_exchange` (that reads `BINANCE_API_KEY`/`BINANCE_SECRET_KEY` via `Env.binance_keys()` — the demo key). Build its own bare `ccxt.binanceusdm` instance directly, per RESEARCH.md's Code Example, reading distinctly-named env vars via `Env.get(...)`.

**Credential-reading pattern to copy** (`trader/core/config.py:21-42`, `Env.get`/`Env.binance_keys`):
```python
class Env:
    @classmethod
    def get(cls, key: str, default: str = "") -> str:
        ...
    @classmethod
    def binance_keys(cls) -> tuple[str, str]:
        ...
```
Use `Env.get("BINANCE_PROD_READONLY_KEY")` / `Env.get("BINANCE_PROD_READONLY_SECRET")` directly — never `Env.binance_keys()` (that is the demo-key path and must stay untouched so the kernel's credential surface cannot pick up the production key).

**make_exchange pattern to deliberately NOT reuse** (`trader/data/feed.py:23-47`) — shown so the new script's author can see exactly what it must avoid inheriting (the `.env`-driven demo/production toggle and shared key path):
```python
def make_exchange(market_type="futures", demo=None, with_keys=True):
    import ccxt
    from ..core.config import Env
    key, secret = Env.binance_keys() if with_keys else ("", "")
    klass = ccxt.binanceusdm if market_type == "futures" else ccxt.binance
    ex = klass({"apiKey": key, "secret": secret, "enableRateLimit": True,
               "options": {"defaultType": "future" ...}})
    use_demo = (Env.get("BINANCE_DEMO", "true").lower() in (...) if demo is None else demo)
    if use_demo:
        ex.enable_demo_trading(True) ...
    return ex
```

**Output location:** write JSON to `.planning/phases/01-correctness-booking-fixes/<name>.json` (git-tracked), never `data/*.json` (gitignored — confirmed in `.gitignore`). `.planning/phases/01-correctness-booking-fixes/01-production-fees.json` already exists as the target shape/precedent (already-measured data from this session, JSON with per-symbol maker/taker rates + UTC timestamp + key restrictions block).

**Never-touch guard:** no ccxt trading method call (`create_order`, etc.) — only `fapiPrivateGetCommissionRate` and an account-restrictions read.

---

### `trader/strategy/vector_backtest.py` (`simulate`, service/batch) — FIX-04 core

**Analog:** itself — the existing signature and the sibling `vector_walk_forward`'s `run(a, b)` closure (lines 229-260) is the in-file precedent for "slice wider than the scored window, then only score a sub-range."

**Current signature to extend** (lines 61-90, read this session):
```python
def simulate(long: np.ndarray, short: np.ndarray, df: pd.DataFrame,
             exit_spec: ExitSpec, risk_cfg: dict, equity: float = 2000.0,
             genome_id: str = "", symbol: str = "BT",
             exit_sig: np.ndarray | None = None,
             funding: np.ndarray | None = None,
             fills_out: list | None = None) -> BacktestResult:
    ...
    n = len(df)
    if n <= WARMUP + 1:
        return res
```
Add `score_from: int = 0`, and change the cursor line to `cursor = max(WARMUP, score_from)` (exact location per RESEARCH.md: `vector_backtest.py:105`, the line currently reading `cursor = WARMUP`). Backward compatibility is structural: `max(WARMUP, 0) == WARMUP`, so every call site that omits the new kwarg is byte-identical to today — verified against the existing regression test below.

**Regression test that must keep passing unmodified** (`tests/test_vector_backtest.py:126-131`):
```python
def test_warmup_signals_are_ignored():
    df = _ramp()
    n = len(df)
    lo = np.zeros(n, bool); lo[5] = True
    r = simulate(lo, np.zeros(n, bool), df, ExitSpec(), RISK)
    assert r.trades == 0
```

**New test shape to add** (copy this file's fixture conventions — `_ramp()`, `_flat()`, `RISK` dict at top of `tests/test_vector_backtest.py`):
```python
def test_score_from_scores_bars_before_the_default_warmup_within_a_wider_frame():
    df = _ramp()  # or a wider df built the same way, len > WARMUP + N
    n = len(df)
    lo = np.zeros(n, bool)
    lo[WARMUP - 5] = True          # would be ignored at score_from=0
    r_default = simulate(lo, np.zeros(n, bool), df, ExitSpec(), RISK)
    assert r_default.trades == 0
    r_widened = simulate(lo, np.zeros(n, bool), df, ExitSpec(), RISK,
                         score_from=WARMUP - 10)
    assert r_widened.trades == 1
```

---

### `trader/strategy/rolling.py` (`_score_window`, service/batch) — FIX-04

**Analog:** `rolling_windows` in the same file (lines 66-96) — sibling function, same module, already slices sub-windows and calls `simulate` per-window; `_score_window` (lines 104-147) is the function being modified.

**Current buggy shape to replace** (lines 104-127, verbatim per research):
```python
def _score_window(compiled, frames: dict, risk_cfg: dict, timeframe: str,
                  recent_days: float, btc=None, derivs_for=None) -> dict:
    n = bars(timeframe, recent_days)
    universe = {s: {timeframe: f} for s, f in frames.items()
                if not s.startswith("_") and f is not None}
    per_symbol, gross_win, gross_loss, trades, wins = {}, 0.0, 0.0, 0, 0
    for sym, df in frames.items():
        if sym.startswith("_") or df is None or len(df) < n:
            continue
        recent = df.iloc[-n:].reset_index(drop=True)     # <- no prefix, bug
        derivs = derivs_for(sym) if derivs_for else None
        fund = funding_for(sym, recent, risk_cfg)
        try:
            # NOTE: universe carries the FULL frames, not `recent` — ...
            lo, sh = compiled.entries({timeframe: recent}, btc=btc,
                                      derivs=derivs, universe=universe,
                                      market=frames.get("_market"),
                                      symbol=sym)
            r = simulate(lo, sh, recent, compiled.spec.exit, risk_cfg,
                         symbol=sym, funding=fund)
        except Exception as e:
            log.warning(f"recent {compiled.spec.id} {sym}: {e}")
            continue
```

**Widened shape (design from RESEARCH.md Pattern 1, built on this exact function):**
```python
from .vector_backtest import WARMUP

def _score_window(compiled, frames, risk_cfg, timeframe, recent_days,
                  btc=None, derivs_for=None):
    n = bars(timeframe, recent_days)
    for sym, df in frames.items():
        if sym.startswith("_") or df is None or len(df) < n:
            continue
        start = max(0, len(df) - n - WARMUP)
        ext = df.iloc[start:].reset_index(drop=True)
        score_from = len(ext) - n
        # universe still keyed off the FULL frames dict — unchanged, see
        # Pitfall 3 below
        lo, sh = compiled.entries({timeframe: ext}, ...)
        r = simulate(lo, sh, ext, compiled.spec.exit, risk_cfg,
                     symbol=sym, funding=fund, score_from=score_from)
```

**Critical invariant to preserve (do not touch)** — the load-bearing comment at `rolling.py:118-122`, keep it verbatim and keep `universe` built from the FULL `frames` dict, never from `ext`:
```python
# NOTE: universe carries the FULL frames, not `recent` — a
# cross-sectional feature aligns peers onto the base symbol's
# bars by timestamp, so a longer peer frame is harmless while a
# frame sliced to match `recent` would produce NaN for the
# whole window.
```

**Error handling pattern already in file** (copy verbatim shape): `try/except Exception as e: log.warning(f"recent {compiled.spec.id} {sym}: {e}"); continue` — per-symbol soft-fail, never aborts the whole pooled result.

---

### `trader/strategy/null_baseline.py` (`null_pfs`, service/batch) — FIX-04 D-07 guard

**Analog:** itself — `edge_percentile` (lines ~70+) already establishes the "empty is a real answer, not zero" idiom that the new guard must follow.

**Current guard, insufficient per D-07** (lines 58-62):
```python
n = len(df)
if n <= WARMUP + 2:
    return []
rng = np.random.default_rng(seed)
lo_off, hi_off = WARMUP + 1, max(WARMUP + 2, n - WARMUP - 1)
if hi_off <= lo_off:
    return []
```

**UNTESTED-not-fabricated idiom to extend the guard with** (from `edge_percentile`, same file):
```python
def edge_percentile(actual_pf, null):
    if not null:
        return None
    ...
```
Add `MIN_DISTINCT_OFFSETS` (value MEASURED at execution time per D-07/Pattern 4, not picked here) alongside the existing `hi_off <= lo_off` check:
```python
MIN_DISTINCT_OFFSETS = ...  # measured, not guessed
...
if hi_off - lo_off < MIN_DISTINCT_OFFSETS:
    return []
```
No downstream change needed: `edge_percentile`/`consistency_p` already return `None` on `null=[]`, and `Analyst.admit` already treats `null_consistency_p is None` as `untestable: True` and refuses (`trader/brain/analyst.py:370-376`).

**Rotation-preserving pattern to copy verbatim (do not reimplement)** — `rotate_entries` (lines 29-36):
```python
def rotate_entries(a: np.ndarray, offset: int) -> np.ndarray:
    """Circularly shift a boolean entry array.
    np.roll and not a reshuffle: ...
    """
    return np.roll(np.asarray(a, dtype=bool), int(offset))
```

---

### `trader/strategy/spec_evidence.py` / `vector_backtest.vector_walk_forward` (batch) — FIX-04

**Analog:** the function's own existing `run(a, b)` closure (`vector_backtest.py:245-255`) — the fix widens only this closure's slice boundaries, entries are already computed on the untruncated frame upstream (lines 241-242) and must stay that way (see Anti-Pattern below).

**Fix shape (from RESEARCH.md Pattern 2, built on the exact function read this session):**
```python
def run(a, b):
    ext_a = max(0, a - WARMUP)
    score_from = a - ext_a          # 0 for the train half
    sl = slice(ext_a, b)
    return simulate(lo[sl], sh[sl], df.iloc[sl].reset_index(drop=True),
                    compiled.spec.exit, risk_cfg,
                    genome_id=compiled.spec.id, symbol=symbol,
                    exit_sig=None if ex is None else ex[sl],
                    funding=None if fund is None else fund[sl],
                    score_from=score_from)
```
Train half (`run(0, cut)`): `ext_a=0`, `score_from=0` — byte-identical to today. Test half (`run(cut, len(df))`): recovers the first 210 bars of the test half that are currently discarded.

**Anti-pattern (do not do this) — explicit in RESEARCH.md:**
```python
# WRONG: recomputing entries on a truncated frame reintroduces the exact
# bug vector_walk_forward's own docstring says it avoids
lo, sh = compiled.entries(df.iloc[a:b], ...)   # do NOT do this inside run()
```
Entries must stay computed once, on the full frame, before `run()` slices.

---

### `CLAUDE.md` (doc edit) — FIX-03 + FIX-04 correction

**Analog:** the file's own established self-correction convention — copy this exact shape (seen throughout the file, e.g. the "Closed 2026-09-11:" and "CLOSED 2026-09-02 18:48" annotations):
```markdown
**Closed <date>:** <what was wrong>. <what is true now>, measured <how>.
```
Apply to:
1. The WARMUP bullet ("harmless at 4h... not a fault at 4h or below") — replace with D-05's measurement (4h decay windows are 180 bars, `simulate` requires >211, so decay can never fire at 4h) and the fix description.
2. The fee bullet ("Production fees are UNMEASURED") — replace with D-03a's figures: taker 0.0500% / maker 0.0200% uniform across the declared 16, demo underquotes 14/16 by 1bp, `config.yaml` stays at the demo-measured 0.04, backtests charge the demo rate (say this plainly per D-03a).

---

## Shared Patterns

### Fail-open / UNTESTED-not-fabricated
**Source:** `trader/strategy/null_baseline.py` (`edge_percentile`'s `if not null: return None`), `trader/agents/macro_guard.py` (module docstring: "any network/parse error means no freeze... reports that explicitly")
**Apply to:** `null_baseline.py`'s new distinctness guard, `vector_backtest.simulate`'s existing `n <= WARMUP+1` early return — never let a short/degenerate window produce a fabricated score. This is the single most load-bearing convention across all of FIX-04.

### In-file precedent over new abstraction
**Source:** `trader/agents/macro_guard.py` (`self._now()` already exists, already correctly used by `_find_active_event`), `trader/strategy/vector_backtest.py` (`vector_walk_forward`'s `run()` closure already widens/slices correctly)
**Apply to:** Every fix in this phase reuses an existing seam in the SAME file/module rather than introducing a new one (no new `Clock` class, no new credential-fetch abstraction, no new windowing helper module). Planner should assign each plan action to modify the existing function, not add a parallel one.

### Script shell conventions
**Source:** `scripts/monitor.py` (lines 1-31)
**Apply to:** `scripts/read_prod_fee.py` (new) and any new before/after evidence script for D-08 — docstring with purpose + invocation line, `sys.path.insert` + `# noqa: E402` pattern, `warnings.filterwarnings("ignore")`.

### Read-only sqlite access to the live journal
**Source:** RESEARCH.md's confirmed-safe pattern (verified this session): `sqlite3.connect("file:...?mode=ro", uri=True)`
**Apply to:** The new D-08 before/after evidence script, which must read `strategies.spec_json` from `data/luffy.db` in the main checkout WITHOUT constructing a real `Journal` object (which writes on `__init__` — see `trader/core/journal.py:193-197`, confirmed this session). Call `rolling.has_decayed`/`recent_verdict` directly; neither touches `self.journal` (confirmed against `trader/brain/analyst.py`).

## No Analog Found

| File | Role | Data Flow | Reason |
|---|---|---|---|
| D-08's before/after evidence script (new, name/location at Claude's discretion) | script | batch/read-only | No existing script calls `rolling.has_decayed`/`recent_verdict` standalone outside the Analyst class; closest partial analogs are `scripts/universe_extension.py` (read-only measurement script shape, sqlite candle reads) and `scripts/deployment_frontier.py`/`scripts/mech.py` (mentioned in the phase context as analogs for structure) — use `universe_extension.py`'s import/sys.path header and its read-only sqlite3 pattern (`sqlite3.connect("data/candles.db")`) as the closest available shape, adapted to `mode=ro` URI for `luffy.db` per the Read-only sqlite access shared pattern above. |

## Metadata

**Analog search scope:** `trader/agents/`, `trader/strategy/`, `trader/brain/analyst.py`, `trader/core/`, `trader/data/feed.py`, `scripts/`, `tests/test_macro_guard.py`, `tests/test_vector_backtest.py`
**Files scanned:** ~14 (all full-file reads confirmed by RESEARCH.md's Sources section, cross-checked directly this session for `macro_guard.py`, `vector_backtest.py`, `rolling.py`, `null_baseline.py`, `monitor.py`, `config.py`, `feed.py`, `test_vector_backtest.py`, `test_macro_guard.py`, `universe_extension.py`)
**Pattern extraction date:** 2026-09-13
