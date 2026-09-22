# Donchian Breakout Trail — retirement attribution audit

**Date:** 2026-09-20
**Spec:** `auth_donchian_breakout_trail` (`kind='spec'`, timeframe 4h, direction both)
**Scope:** read-only audit. No strategy state, trading, exit, risk, promotion-logic
or historical row was modified. The strategy was **not** reactivated.

---

## 1. Exact retirement trigger

| field | value |
|---|---|
| event | `brain_events` id **1672** |
| ts | `2026-09-20T09:04:33.264893+00:00` |
| kind | `statistical_transition` |
| subject | `auth_donchian_breakout_trail` |
| detail | `{"from": "paper", "to": "retired", "reason": "failed probation: PF 0.00, WR 0%, 6 trades", "trades": 6, "pf": 0.0, "winrate": 0.0}` |

Code path: `trader/kernel.py:1377` → `trader/strategy/promotion.py:50`
`evaluate_population()` → the `paper` branch at `promotion.py:85`, condition
`st["consecutive_losses"] >= DEMOTE_CONSEC_LOSSES (6)`.

Evidence consumed by that trigger: six closed realized trades, via
`promotion.py:30 _stats_for()`, which filters `status == 'closed'`. No backtest,
no pooled profit factor, no universe evidence, no control.

`strategies.retire_reason` for this row is **empty** — `transition()` in
`promotion.py:59` writes `state` and `state_changed_at` only. Every other
`kind='spec'` retirement in the journal carries written evidence in
`retire_reason`. The empty field is the identifying fingerprint of this path.

---

## 2. Closed vs open trade evidence

Thirteen entries exist for the spec. The trigger read seven of them (six at the
time it fired; a seventh closed at 10:12 the same day).

> **Excursion columns are intrabar.** MFE/MAE below are extreme *intrabar*
> excursions reconstructed from candles. They are **not** necessarily prices the
> exit engine ever evaluated: production reads the last **closed 15m bar close**
> as its mark (§3a), so an intrabar spike can exceed any price the engine saw.
> Do not read an intrabar MFE as "the trail should have fired here" — see §10.

**Closed — 7 of 13. Every one exited `close_reason='sl_fill'`.**

| symbol | side | intrabar MFE_R | intrabar MAE_R | exit_R | realized_R | USDT | held (h) |
|---|---|---|---|---|---|---|---|
| HYPE/USDT | short | 0.15 | −1.04 | −1.01 | −1.03 | −7.04 | 26.2 |
| FIL/USDT | long | **1.31** | −0.97 | −0.92 | −0.93 | −6.41 | 34.6 |
| XRP/USDT | short | 0.66 | −0.87 | −0.78 | −0.80 | −4.60 | 65.9 |
| AAVE/USDT | short | 0.96 | −1.11 | −1.06 | −1.07 | −6.31 | 30.2 |
| LINK/USDT | short | 0.35 | −1.09 | −1.01 | −1.03 | −17.79 | 28.3 |
| AAVE/USDT | long | 0.44 | −0.96 | −1.00 | −1.02 | −24.97 | 30.2 |
| SUI/USDT | long | 0.44 | −1.08 | −1.01 | −1.02 | −27.20 | 20.7 |

**Open — 6 of 13. Excluded from the trigger's sample entirely.**

| symbol | side | intrabar MFE_R | intrabar MAE_R | mark_R | USDT | trail active |
|---|---|---|---|---|---|---|
| AVAX/USDT | long | 7.88 | −0.32 | +6.58 | +163.62 | yes (22 ratchets) |
| NEAR/USDT | long | 4.86 | −0.32 | +3.46 | +86.42 | yes (14 ratchets) |
| UNI/USDT | long | 2.39 | −0.21 | +1.40 | +8.68 | yes (4 ratchets) |
| ZEC/USDT | long | 0.88 | −0.46 | −0.27 | −6.57 | no |
| HYPE/USDT | long | 0.68 | −0.40 | −0.02 | −0.41 | no |
| SOL/USDT | long | 0.66 | −0.89 | −0.65 | −16.85 | no |

Supporting observations:

- Closed holding time spans 20.7–65.9h. Open positions had been held 40.2–72.2h
  at audit time and were still running.
- All five closed **short** trades lost, during a period in which the long leg
  produced the three trailing winners. The spec's registered invalidation states
  *"Do not judge the long and short legs separately: each fails alone and the
  pair is the strategy."* The consecutive-loss trigger effectively judged the
  short leg in isolation.
- Entry-signal quality is not recoverable: `decisions.score` is a constant ±0.6
  across all 13 entries under `scouts.strategy_leads`. Only direction is
  recorded, not conviction.
- Fees and funding are unattributed. All 15 `trade_accounting_bookings` rows
  carry `funding_usdt: null`, `net_economic_pnl_usdt: null`, and reasons
  `["booking_not_verified_by_exact_order_fills", "funding_unattributed"]`.
  `execution_accounting` is empty. All P&L above is venue-reported realized or
  mark-to-market, **not** net economic P&L.

---

## 3. Intrabar MFE/MAE reconstruction summary

No maximum favorable or adverse excursion is persisted anywhere in the engine.
`trades` has no MFE/MAE column; `close_reason` is the only exit attribution
recorded. Excursions below were reconstructed for this audit from
`data/candles.db`.

Method: R = `trades.initial_risk` (per-unit initial risk at entry). For each
trade, bars between `opened_at` and `closed_at` (or now, for open positions) were
read at the finest timeframe achieving >90% expected bar coverage — 15m for
twelve trades, 5m for AAVE/USDT short — with realised coverage 99–100%. MFE is
the extreme favourable intrabar excursion (high for longs, low for shorts), MAE
the extreme adverse one, both normalised by R.

**These are intrabar figures and are a measure of the opportunity, not of what
the exit engine evaluated.** The engine's mark is the last closed 15m bar close
(§3a), so evaluated R is bounded by 15m closes and is generally lower than
intrabar MFE. For FIL the gap is decisive: intrabar MFE +1.31R against a maximum
evaluated mark of +1.082R (§10).

Distribution across the seven closed losers:

| intrabar MFE threshold | count |
|---|---|
| ≥ 0.25R | 6 of 7 |
| ≥ 0.50R | 3 of 7 (FIL 1.31, AAVE-short 0.96, XRP 0.66) |
| ≥ 1.00R (nominal trail arming threshold) | 1 of 7 (FIL) |
| ≥ 2.00R (first R at which the trail can lock profit) | 0 of 7 |

Exit geometry: stop = 2×ATR, trail = 4×ATR armed at 1.0R. Because the trail sits
2R below the running peak, it cannot lock any profit until MFE exceeds 2R. No
closed trade reached that. Direct exit-policy give-back on closed trades is
therefore real but small — at most three trades, and only FIL was recoverable
even in principle.

### 3a. What price the exit engine actually evaluates

Established by inspection while resolving §10, and load-bearing for every
excursion figure above:

- `trader/kernel.py:1171` passes `snap.price` to `ExitEngine.manage()` as `mark`.
- `trader/kernel.py:752` sets `price = float(dfs[exec_tf]["close"].iloc[-1])`
  with `exec_tf = "15m"` (`config.yaml:40`), and `trader/data/feed.py:236`
  applies `closed_bars`.
- Therefore **production trail arming uses the last closed 15m bar close** — not
  a live ticker, not the bar high, not the spec's 4h close. The cycle polls every
  60s (`scan_interval_seconds: 60`), but the evaluated price only changes when a
  15m bar closes.
- `atr` is passed separately and *is* on the spec's declared frame:
  `kernel.py:1161` resolves `exits.atr_timeframe(t)` → 4h.

### 3b. Geometry mismatch: `arm_at_r` cannot produce the first ratchet

The spec's declared geometry is internally inconsistent, independently of any
defect:

| quantity | value |
|---|---|
| `arm_at_r` | 1.0 |
| trail width | ~4×ATR (2.0R at entry, since the stop is ~2×ATR) |
| initial stop | ~0.92R below entry (FIL: exactly 0.915R) |

The ratchet is gated on `better` — the trail may only tighten, never loosen — so
the first stop move requires

```
mark > (4·ATR / R) − 0.915  ≈  +1.1R    (at entry ATR)
```

Arming at 1.0R is therefore **decorative**: no ratchet is possible at the arming
threshold itself. And the requirement rises materially when ATR expands, which is
the regime a breakout strategy trades by construction — on FIL, ATR4h expanded
0.03403 → 0.05045 (+48%) during the rally, widening the trail from 2.09R to 3.11R
and pushing the required mark to roughly +2.2R. The trail level moved *down* while
price moved up.

Confirmed against the cases that did ratchet, same spec, same rule:

| trade | predicted first-ratchet threshold | first ratchet actually logged |
|---|---|---|
| AVAX/USDT | > +1.07R | **R=1.06** |
| NEAR/USDT | — | R=1.23 |
| FIL/USDT | > +1.18R | never reached (max evaluated +1.082R) |

This is a geometry mismatch to reconsider when the spec is re-specified, **not**
an implementation defect. No exit change is proposed on this evidence.

---

## 4. Closed-only vs all-entry mark-to-market

| basis | n | profit factor | gross win | gross loss | net USDT |
|---|---|---|---|---|---|
| closed only (what the trigger read) | 7 | **0.00** | 0.00 | 94.30 | −94.30 |
| all entries, marked to market | 13 | **2.19** | 258.73 | 118.13 | +140.59 |

**Neither figure establishes anything about the mechanism.** The closed-only
figure is drawn from a censored sample (§5). The mark-to-market figure is
unrealised, concentrated in three open trend positions, and could be given back
in full. The comparison is presented to size the sampling bias, not to argue
that the strategy works.

---

## 5. Why closed-only sampling is exit-policy contaminated

The contamination runs opposite to the usual concern, and is larger than it.

The usual concern — profitable entries whose gains the shared exit mechanism
handed back, booked wholly as strategy losses — is **largely not** what happened.
Six of seven closed losers never reached the trail's 1R arming threshold at all;
their exits were the untouched initial 2×ATR stop. Those are genuine entry
failures.

The actual defect is that **the exit policy determines which trades enter the
scoring sample.** A trade becomes visible to `_stats_for()` only by closing. The
trailing exit closes losers quickly — the initial stop is 2×ATR away and is hit
within 20–66h — while deliberately keeping winners open and ratcheting behind
them, by design and in service of the spec's stated thesis that *"the tail of a
breakout is fatter than its body."*

Consequently the closed-trade sample is not merely failing to separate entry edge
from exit-policy performance; it is **anti-correlated with exit-policy success**.
Every trade on which the exit policy was working was, at the moment of the
trigger, still open and therefore excluded. `logs/luffy.log` records 40 TRAIL
ratchets in the retained window, on exactly three symbols — AVAX, NEAR and UNI —
which are exactly the three excluded winners.

Two further consequences for the statistic itself:

- Six consecutive losses is nominally p ≈ 0.016 one-sided. That p-value assumes a
  random draw of entries. This is the subset the exit policy selected for closure,
  so the nominal p is invalid and no significance claim survives.
- The spec's own registered invalidation requires *"below profit factor 1.0 over
  **40** out-of-sample trades."* The trigger fired at 7 closed trades, roughly a
  fifth of the registered bar, on a biased subsample.

---

## 6. Legacy genome lifecycle incorrectly touching `kind='spec'`

`trader/strategy/promotion.py` implements the deterministic lifecycle rules for
**legacy genomes**. `trader/kernel.py:1371` states this explicitly in the comment
immediately above the call: *"Lifecycle rules for the legacy genomes."*

`evaluate_population()` iterates `journal.list_strategies()`. That helper
(`trader/core/journal.py:507`) applies a state filter when one is passed and no
filter at all otherwise — it never filters on `kind`. Called with no argument, it
returns every row in `strategies`, including `kind='spec'` rows.

The Donchian spec was therefore swept by the legacy realized-trade rules and
retired on a 6-consecutive-loss counter that was never intended to govern spec
rows. This is the same class of boundary violation as the second-creation-path
defect removed on 2026-09-11 (`brain_events` id 1169), and it cuts against the
repository invariant that spec creation and admission run through one path.

Corroborating evidence that this is the wrong path rather than a legitimate
second gate: `auth_donchian_breakout_trail` is the only `kind='spec'` row in the
journal with an empty `retire_reason`. The other six spec retirements all carry
written evidence, e.g. `"decayed: pooled PF 0.63 < 0.85"` for
`auth_aggressor_divergence_at_highs`.

---

## 7. Correct spec retirement path and its evidence requirements

Specs retire through `trader/brain/analyst.py:502 review_deployed()` →
`trader/strategy/rolling.py:199 has_decayed()`, invoked from
`trader/kernel.py:493`, which writes both `state='retired'` **and** a
`retire_reason` drawn from the recorded verdict.

Evidence that gate requires, read from `config.yaml` at audit time:

| setting | value |
|---|---|
| `decay_recent_days` | 30 |
| `decay_min_trades` | 10 |
| `decay_floor_pf` | 0.85 |

Semantics that matter here, per `rolling.py:199`:

- Scored on **backtested** recent bars across the spec's own declared universe,
  pooled, after costs — not on the realized live book.
- The window is explicitly **not** widened for retirement, so last year's trades
  cannot be pulled in to answer whether the spec still works.
- Fewer than `min_trades` recent trades returns `(False, verdict="idle — too few
  trades to judge")`. **Idleness is not decay**, and is handled by the population
  cap rather than by retirement.

No `spec_decayed` event exists for `auth_donchian_breakout_trail` in
`brain_events`. This gate never ran on the spec. Under it, seven realized live
trades would not have been admissible evidence for retirement at all.

---

## 8. Disposition

**The retirement evidence is invalid and attribution-contaminated. No automatic
reactivation follows from that.**

- The evidence that produced the retirement does not support it: wrong lifecycle
  path, censored sample, 7 trades against a registered 40-trade invalidation, no
  reason recorded.
- The mark-to-market counter-figure does not support reinstatement either. It is
  unrealised, concentrated in three open positions, and no better powered.
- Correct disposition: leave `state='retired'` in place; record the contamination
  in `retire_reason` as provenance; requeue the spec for a clean decision through
  `recent_verdict`/`has_decayed` on its declared 4h universe.
- Open positions remain managed by `ExitEngine` under their venue protective
  stops, unchanged.

A well-formed thesis with invalid retirement evidence is untested, not
readmitted. Reinstatement requires the spec path's own evidence, produced by that
path.

---

## 9. Proposed corrections (not applied)

### 9.1 Smallest scope-filter correction

Prevent the legacy genome lifecycle from governing spec rows. One condition, in
`trader/strategy/promotion.py:52`:

```python
for row in journal.list_strategies():
    sid, state = row["id"], row["state"]
    if state == "retired" or row["kind"] == "spec":
        continue        # specs retire via analyst.review_deployed/has_decayed
    st = _stats_for(journal, sid)
```

This is the minimal change that closes the defect. It leaves the legacy genome
rules, the spec decay gate, exits, risk and sizing untouched, and changes no
existing row.

### 9.2 `retire_reason` provenance fix

`transition()` at `promotion.py:59` writes `state` and `state_changed_at` but not
`retire_reason`, which is why this retirement left no traceable justification and
had to be identified by elimination against the other writers
(`kernel.py:496`, `brain/tv.py:165`, `brain/strategist.py:133`, all of which do
write one).

Proposal: have `transition()` persist the same `reason` string it already builds
and already logs to `brain_events`, so every state change in `strategies` carries
its own provenance. No rule or threshold changes; this is a write-completeness
fix. Existing rows are left as they are.

### 9.3 MFE/MAE persistence at close

No excursion data exists anywhere in the engine. `trades` records
`close_reason` and nothing else about the path, so exit-policy performance is
currently unauditable without reconstructing excursions from `data/candles.db`,
as §3 did.

Proposal: persist `mfe_r` and `mae_r` on `trades` at close, computed over the
holding window on the spec's declared timeframe. This is the enabling change for
ever answering "entry edge or exit policy" from the journal itself, and would
have made this audit a query rather than a reconstruction. It records observation
only and alters no exit, stop or sizing behaviour.

---

## 10. RESOLVED — FIL/USDT +1R trailing anomaly

**Status: resolved 2026-09-20 by reconstruction. Correct behaviour under the
current design — not a polling/data issue and not an exit-engine bug.**

The original entry recorded this as *plausible, not confirmed*, on the basis that
FIL printed 12×15m bars with **highs** at or above +1R while the stop never left
0.895043 (−0.915R). That comparison was the error: it measured intrabar highs
against a rule that reads closed 15m closes (§3a).

Reconstruction over the full holding window (`2026-09-13T16:00:43Z` →
`2026-09-15T02:34:07Z`), replaying the production rule at
`trader/engine/exits.py:172` against the actual 4h ATR at each timestamp:

- **138 closed 15m bars = 138 distinct evaluated marks.**
- **FIL crossed +1R on 4 evaluated closes, and the trail armed on each.**
- Maximum *evaluated* mark: **+1.082R**. Maximum intrabar high: +1.314R — never
  evaluated by the engine.

| bar close (UTC) | mark | r_now | ATR4h | trail level | armed | better | hyst | move |
|---|---|---|---|---|---|---|---|---|
| 09-14 03:00 | 1.0245 | 1.077 | 0.03839 | −1.286R | True | **False** | True | no |
| 09-14 10:30 | 1.0212 | 1.026 | 0.04479 | −1.731R | True | **False** | True | no |
| 09-14 14:00 | 1.0210 | 1.023 | 0.05045 | −2.082R | True | **False** | True | no |
| 09-14 14:15 | 1.0248 | 1.082 | 0.05045 | −2.024R | True | **False** | True | no |

The other 134 evaluations had `r_now < 1.0` and did not arm.

**No stop move occurred because on every armed evaluation the computed 4×ATR
trail sat BELOW the incumbent stop.** The best trail level reachable in the whole
window was −1.114R against an incumbent stop of −0.915R. The ratchet-only `better`
gate correctly refused to loosen protection; moving the stop to −1.29R would have
widened risk below the entry stop. The implemented rule produces **zero** stop
moves here, and that is the right answer.

Corroborating record state: `sl_order_id` remains the single unchanged id
`1000000204180018` and `stop_loss` is still the entry value — `_move_stop()`
replaces the order and would have written a new id.

The retained-log gap (`logs/luffy.log` starts 2026-09-16) is now immaterial: no
claim is made here about whether FIL was managed on any particular cycle, because
under *perfect* 60-second management the implemented rule still yields zero stop
moves. The outcome is fully explained without assuming a management gap.

Incidental, evidence-backed, not causal: FIL's entry stop sits 1.75×ATR4h from
entry rather than the declared 2.0, making R ~12% tighter than the spec geometry
implies and raising the ratchet threshold further.

The underlying design observation is recorded separately in §3b.

---

## 11. Latent defect: spec `arm_at_r` is not wired through

Found while resolving §10. Recorded separately because it **did not affect FIL**
and is not part of that resolution.

- `SpecExit.from_spec` (`trader/engine/exits.py:60`) captures `max_bars`,
  `timeframe`, `trail_atr_mult`, `has_target`, `stop_atr_mult` and `stop_pct`. It
  does **not** capture the spec's `arm_at_r`.
- `ExitConfig.trail_after_r` is likewise not read from config in
  `ExitEngine.__init__` (`exits.py:97`), so it is always the dataclass default of
  1.0.
- Arming therefore always uses 1.0 regardless of what a spec declares. Donchian's
  declared `arm_at_r` is 1.0, so production behaviour and declared behaviour
  coincided here — which is why this surfaced only as a side observation.
- A spec declaring e.g. `arm_at_r: 2.0` would be silently armed at 1.0, i.e.
  traded on geometry it was not validated with. That is the same class of fault
  the `SpecExit` docstring was written to close.

Proposed (not applied, and not required by §10): wire `arm_at_r` through
`SpecExit.from_spec` and read `trail_after_r` from config. This changes live
behaviour only for a spec declaring an `arm_at_r` other than 1.0.

---

## Evidence sources

- `data/luffy.db` — `brain_events` (ids 1672, 1169, 912, 1604), `strategies`,
  `trades`, `decisions`, `trade_accounting_bookings`, `execution_accounting`
- `data/candles.db` — `candles` (15m, 5m, 4h) for intrabar MFE/MAE
  reconstruction, and the 138-mark exit-evaluation replay in §10
- `logs/luffy.log` — TRAIL ratchet records, retained window 2026-09-16 to
  2026-09-20 (AVAX/NEAR first-ratchet R values used as the §3b control)
- `config.yaml` — `decay_recent_days`, `decay_min_trades`, `decay_floor_pf`
- `trader/strategy/promotion.py`, `trader/strategy/rolling.py`,
  `trader/brain/analyst.py`, `trader/core/journal.py`, `trader/kernel.py`
  (`:752` mark source, `:1161` ATR frame, `:1171` manage call),
  `trader/data/feed.py:236`, `trader/agents/indicators.py` (ATR, period 14),
  `trader/engine/exits.py` (`:60` SpecExit, `:97` ExitConfig, `:172` trail)
- `config.yaml` — `timeframes.execution: 15m`, `scan_interval_seconds: 60`
