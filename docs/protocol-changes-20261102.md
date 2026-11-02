# Protocol change proposals (2026-11-02): proposals only, nothing implemented

**Status: for approval. No code, gate, threshold or config value was changed for any item in this document.**
Evidence tags: **VERIFIED** = read in code or measured (file:line, or the stated query); **INFERRED** = reasoned, with the assumption named.

## 0. Correction to my earlier `testable` trace — please read first

My Phase A report said the held-out B window is at least 442 bars and that a rule needs about **0.035 trades per bar per symbol**. **Both numbers were wrong**, and the 0.035 was repeated in your last message. I used 20 for the minimum number of rotation draws, taken from a config comment about a different module. The referee uses `portfolio_null.MIN_DRAWS = 199` (`trader/research/portfolio_null.py:41`; imported as `pn` at `referee.py:33`). Corrected, VERIFIED by running the repo's own constants:

| quantity | value | source |
|---|---|---|
| `WARMUP` | 210 bars | `strategy/vector_backtest.py:32` |
| `ROTATION_FLOOR_BARS` | 2·(210+1) + 199 = **621** | `referee.py:57` |
| minimum protected window | int(621 / (1 − 0.7)) + 1 = **2,070 bars** (≈ 345 days at 4h) | `closure/manifest.py:56` |
| held-out B at that minimum | last 30% = **621 bars**; usable after warmup = **411** | `slices.DISCOVERY_FRAC = 0.7` |
| `testable` requirement | at least **4 markets** with `median_rate × usable_bars ≥ 8` trades, in A **and** B | `evaluate.py:47-50, 522-534` |
| **rate needed at the minimum window** | 8 / 411 = **0.0195 trades per bar per symbol** (one trade per ~51 bars = 8.6 days at 4h) | derived |

So the corrected gap is not "0.002 versus 0.035 (about 20×)". It is **observed rates of about 0.002 to 0.010 versus 0.0195 needed at the minimum window: roughly 2× to 10×**, and the gap closes completely if the held-out window is longer. That changes which options matter (see §2).

**What the rates are** (assumptions stated):
* ≈ **0.0017** — Donchian Breakout Trail over the last 90 days: "15 trades in 90 days across 16 symbols" (`config.yaml` comment at lines 258-260; VERIFIED text, 15/16/540 bars). Recent, and it has been decaying.
* ≈ **0.0100** — Donchian over its long run: "1724 trades and 4.9 years" (`config.yaml` comment, lines 45-50; VERIFIED text; assumes 16 symbols, INFERRED).
* **0.004 – 0.010** — implied by the four `INCONCLUSIVE_POWER` development runs (240 to 560 trades; study covered 39 symbols, VERIFIED in `schedule_events`). The fit window of those runs was not recoverable, so this range is INFERRED.

**Rate needed as a function of the held-out window** (4h bars; protected window P = B / 0.3):

| B (bars) | usable | rate needed | one trade per | protected window P | years of 4h |
|---|---|---|---|---|---|
| 621 (the minimum, in force) | 411 | 0.0195 | 51 bars (8.6 d) | 2,070 | 0.95 |
| 1,000 | 790 | 0.0101 | 99 bars (16.5 d) | 3,333 | 1.52 |
| 1,500 | 1,290 | 0.0062 | 161 bars (26.9 d) | 5,000 | 2.28 |
| 2,000 | 1,790 | 0.0045 | 224 bars (37.3 d) | 6,667 | 3.04 |
| 3,000 | 2,790 | 0.0029 | 349 bars (58.1 d) | 10,000 | 4.57 |
| 4,000 | 3,790 | 0.0021 | 474 bars (79.0 d) | 13,333 | 6.09 |

Smallest window that makes a rule testable: rate 0.0100 → B ≥ 1,010 (P ≈ 3,367 bars, 1.5 y); rate 0.0044 → B ≥ 2,028 (P ≈ 6,761, 3.1 y); rate 0.0017 → B ≥ 4,916 (P ≈ 16,386, 7.5 y, longer than any retained history).

**The binding constraint is therefore the length of the registered held-out window (set to the minimum), not the strategy.** `testable=False` says "this rule, at its measured trade rate, cannot be judged in a 621-bar B window". It says nothing about edge.

---

## 1. Proposals for the `INCONCLUSIVE_POWER` / `testable` constraint

Each option states: what changes in the protocol; how the error budget is protected; the effect on the estimand; what must be shown first.

### Option A — Longer protected window (more bars in B)
* **Changes:** the length of the protected window for new studies: the existing `research.closure.auto.protected_extra_bars` knob (default 0, `closure/auto.py:29`; that it lengthens the protected window, and so B, is read from `publish_job` at `auto.py:198-199` and is INFERRED, not yet traced through envelope construction), and/or the 70/30 split fraction `DISCOVERY_FRAC` (`slices.py:20`) for a new horizon family. The 621-bar rotation floor and every gate threshold are untouched.
* **Error budget:** no new test and no new alpha. Each look still spends exactly one `alpha_t`. The protected cut is frozen at the first look (`research_slices` / `spent_cut`), so existing looks are unaffected. More candidates become testable, so looks accumulate faster: the budget should be planned for the extra look rate (see §3 for the per-look alphas).
* **Estimand:** unchanged (per-symbol consistency plus the cohort rotation null). It becomes a claim about a longer recent period.
* **Cost:** history. A study needs `fit_bars + P` bars of retained, eligible history per symbol. For B = 1,500 (P = 5,000) plus a 1,620-bar fit that is 6,620 bars, 3.0 years at 4h, which most of the 39 covered symbols cannot supply (listings are young; BTC has about 4.9 years). The study cohort shrinks to symbols with long history.
* **Fit:** rules with a rate ≥ about 0.006 per bar per symbol (a trade per symbol every ~27 days). It cannot rescue a 0.0017 rule.
* **Needed first:** the count of symbols with `fit + P` bars of eligible history for each candidate P (a cheap coverage query on the migrated store).

### Option B — Pooled testability and a cohort statistic (the original Task 2.1)
* **Changes:** (1) replace the per-market `testable` projection with a pooled criterion based on projected **effective** sample size; (2) add a cohort-level statistic (mean per-trade edge) with calendar-time block-bootstrap standard errors beside the per-symbol consistency gate. The estimator and its block length are **fixed and documented before any candidate is evaluated with it**, and every pooled result reports the **effective sample size**, not the trade count.
* **Error budget:** one look per candidate, spending one `alpha_t` as today. The pooled test must be shown to hold its level under cross-sectional dependence **before use**: run `scripts/calibrate_null_dependence.py`-style calibration on no-edge data and require the measured false-positive rate at `alpha_t` not to exceed `alpha_t`. Register it in the **same** LORD++ sequence (global false-discovery control), not a separate sequence, so it cannot inflate the budget.
* **Estimand — this changes:** from "an edge is present in a consistent majority of markets" (a breadth claim, a binomial over symbols) to "the cohort-**average** per-trade edge is positive" (which a few markets can carry). To limit this, require a heterogeneity guard (the result survives dropping the best-performing 20% of symbols).
* **Effect, quantified (INFERRED, ρ̄ = 0.1 as in the test fixtures; ρ̄ must be measured):**

  | symbols | rate | pooled trades in B at the 621-bar minimum | effective n | pooled trades at B = 1,500 | effective n |
  |---|---|---|---|---|---|
  | 16 | 0.0017 | 11.2 | 4.5 | 35.1 | 14.0 |
  | 30 | 0.0017 | 21.0 | 5.4 | 65.8 | 16.9 |
  | 30 | 0.0044 | 54.3 | 13.9 | 170.3 | 43.7 |
  | 30 | 0.0100 | 123.3 | 31.6 | 387.0 | 99.2 |

  At the minimum window, pooling a Donchian-recent rule gives an effective n of about 5, so **pooling alone does not rescue it**. Pooling helps materially only for rules with rate ≥ about 0.004, or combined with Option A.
* **Verdict:** not recommended alone. Consider after A, and only with the calibration and heterogeneity guard.

### Option C — Re-open `1h` for compositional derivatives hypotheses
* **Changes:** register `1h` as a new hypothesis family for compositional research only (legacy brute-force stays `4h`-only).
* **What it really trades:** the 621-bar floor and the 8-trade requirement are in **bars**. At 1h the minimum protected window is 2,070 bars = 86 days, but a rule must produce 8 trades per market in 411 usable bars = 17 days: **0.47 trades per symbol per day**, versus 0.117 per day at 4h. So 1h trades calendar time for signal frequency; it helps only if the signal fires about 4× as often per day.
* **Error budget:** a new family doubles the hypothesis space, so it must be registered in the LORD++ sequence from its first look (§3 profile). The control's power at 1h was measured **underpowered** on 2026-09-13 (p = 0.32; `config.yaml:131-138`, VERIFIED text) and must be **re-measured on the actual retained derivatives data** before 1h is opened.
* **Data (INFERRED, to be measured):** REST history for OI and long/short series is about 20 days at 1h (`config.yaml:29-33`); retained history only starts when the recorder started. A study needs `fit (2,000) + protected (2,070) = 4,070` bars = about 170 days of retained 1h derivative data for such features. Funding-only or basis-only hypotheses are not limited this way.
* **Estimand:** unchanged, at a different timescale.
* **Verdict:** only after the power re-measurement shows adequate power; do not propose opening on the strength of the bar-count argument alone.

### Option D — Frequency screening at proposal time (no gate change)
* **Changes:** use the existing `testable` projection **before** spending discovery compute and gauntlet time: label candidates "structurally untestable at the registered window (needs B ≥ N bars)" as a distinct state, separate from `INCONCLUSIVE_POWER`. Optionally prefer proposing rules whose recent rate meets the requirement.
* **Error budget:** untouched. No look is spent and the referee is unchanged.
* **Estimand:** unchanged. Costs are already in the evaluation, so higher-frequency rules still pay for them.
* **Risk:** steers search toward frequent rules, which adds multiple-testing noise at discovery (not at the gate). Keep the existing ablation and null requirements.
* **Verdict:** cheap and safe; recommended regardless of the other choices.

### Option E — Accept that low-frequency strategies stay unvalidated
* **Changes:** nothing in the gates. Add an honest label ("untestable at the registered window; needs about P bars") and stop reporting these as power failures.
* **Consequence:** the best-evidenced strategy (Donchian, recent rate 0.0017) never graduates through the Factory. Today it trades through the owner-demo path with `NOT_ASSERTED_OWNER_AUTHORIZED` (see `docs/guard-review-20261102.md`). That path stays valid for demo; it is not validation.
* **Verdict:** the honest default for rules like Donchian-recent, whatever else is adopted.

### Option F — Forward (prospective) accumulation
* **Changes:** register the frozen candidate now and take its single look when enough forward bars exist. No gate changes; no optional stopping (the look time is pre-fixed by bar count).
* **Cost:** time. For rate 0.0017 it is B ≥ 4,916 bars = 2.2 years. Reasonable only for rules at or above about 0.006.

### Rejected (stated so they are not reopened by accident)
* Lowering `MIN_PROJECTED_TRADES`, `MIN_PROJECTED_MARKETS` or `MIN_SYMBOL_TRADES`: loosens a gate.
* Shortening the 621-bar rotation floor: it sets the resolution of the exact rotation null; a shorter floor tests at a level the data cannot resolve.

### Recommended sequencing
1. **D** now (no protocol change). 2. **A** for new studies, after measuring how many symbols can support `fit + P`. 3. **B** only with the pre-registered estimator, the calibration result and the heterogeneity guard. 4. **C** only if the 1h control-power re-measurement is adequate. 5. **E** as the stated fallback for rules that no option makes testable.

---

## 2. Task 3.1 — legacy survivors, using the closure qualifier's overlap refusal

### Facts (VERIFIED unless marked)
* Journal snapshot: **124 `survivor`** and **65 `grow`** combos, all `4h/trail`, **all created between 2026-09-13 and 2026-09-18**, all `testable = 1`. No held-out look has ever been spent (`research_tests` empty, `research_budget` has one row).
* The recorded 4h discovery cut is **2025-03-08 17:08 UTC** (`research_slices.cut_ms = 1741452480000`, measured 2026-09-13). By the legacy design, the 1.65 years after that cut were never read by discovery.
* The legacy referee `examine` fixes B at the discovery or first-look cut (`referee.py:571-578`). **It has no per-candidate selection-date check** (VERIFIED by reading `examine` and `load_heldout`).
* The closure qualifier **does** refuse overlap: it requires the protected window to be proven unexposed by a forward seal that predates the data, and any exposure mark overlapping the window blocks the look before any held-out price is read (`closure/qualification.py:198-205`; proven by `tests/test_heldout_overlap_refusal.py`).
* A forward seal written today **cannot** prove unexposure for bars that are already available (`manifest.exposure_state` returns `UNKNOWN` when the data's first availability precedes the seal; tested). Free text cannot assert "unexposed" (existing test `test_free_text_cannot_assert_unexposed`).

### Proposal
1. **First batch, capped at 10, ranking fixed now.** Rank survivors by recorded discovery evidence only: unrounded `consistency_p` ascending, then `median_pf` descending, then `hash`; take the top 10 among `testable = 1`. The criterion uses no held-out data and is fixed by this document before any forward bars are examined. (The top 10 by that rule are listed in the journal snapshot and can be reproduced; they are not copied here.)
2. **Selection date.** Use the ledger's `created_at` as the lower bound of each candidate's selection date (2026-09-13 to 2026-09-18). Record, per candidate, an exposure mark of `DEVELOPMENT_EXPOSED` covering its discovery universe from the start of its fit window to its selection date.
3. **Forward-only held-out windows.** A look is allowed only on bars that became available **after** the candidate's selection date, with the 10-bar embargo. A candidate without enough forward bars is **DEFERRED** (no look spent, recorded with the shortfall), never tested.
4. **Which qualifier.** Two routes, both honouring (3):
   * **Route S (strict, matches your requirement):** qualify through the closure qualifier. Because a seal placed now cannot cover already-available bars, the earliest qualifiable window is the data that arrives after a seal placed now, plus the full minimum protected window of 2,070 bars (≈ 345 days at 4h). **Result: none of the 10 is testable before about October 2027** (a seal in early November 2026 plus 345 days).
   * **Route L (legacy referee with one added refusal):** add a tightening to `referee.examine` that refuses when `cut < candidate selection_ms` (B must start after selection). B then runs from the selection date to now. With 621 bars needed (103.5 days at 4h), a candidate selected on 2026-09-13/18 first has enough forward bars on about **2026-12-25 to 2026-12-30**. This is a new refusal, so it needs your approval as a protocol change; it loosens nothing.
5. **Stricter than the legacy design.** Under the legacy design, B started at the 2025-03-08 cut and the data from that cut to selection (about 18 months) was unseen by discovery. Requiring windows after the selection date discards that unseen history. If you prefer to use it, Route L without the added refusal (the existing behaviour) has ample power now, at the cost of the "forward only" property. This is your decision; both are stated so the trade is explicit.

### LORD++ budget each look would spend (VERIFIED by running `fdr.alpha_at` with `fdr_target = 0.10`, `lord_w0 = 0.05`)
Zero looks are spent today, so the next look is `t = 1`. Level held to at each look:

| look t | alpha_t if no earlier look rejected | alpha_t if every earlier look rejected |
|---|---|---|
| 1 | 2.50e-03 | 2.50e-03 |
| 2 | 5.44e-04 | 3.05e-03 |
| 3 | 4.63e-04 | 6.01e-03 |
| 5 | 3.27e-04 | 7.73e-03 |
| 10 | 1.82e-04 | 1.03e-02 |

* Ten looks with no rejection spend a cumulative alpha of **5.36e-03** of the 0.10 target, leaving the sequence able to continue at about 1.8e-4 per look (worst case).
* Every one of these levels is above the resolution of the referee's draws (`referee_max_draws = 19999` resolves down to about 5e-5, `config.yaml:213-217`), so none would be deferred for resolvability.
* Each look is final and spends its alpha whether or not the candidate passes; a failed look is not repeated for the same candidate.

### Decisions needed from you
1. Route S (strict, closure qualifier, about October 2027) or Route L (legacy referee plus the added "B after selection" refusal, about late December 2026)?
2. Keep the "forward after selection date" rule, or accept the legacy unseen window after the 2025-03-08 cut?
3. Approve capping the first batch at 10 with the ranking criterion above?
4. For Options A to F in §1: which to take forward, and in what order?
