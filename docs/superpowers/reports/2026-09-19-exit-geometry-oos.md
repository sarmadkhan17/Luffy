# Exit geometry OOS comparison — 25% peak-profit giveback

Status: research-only. No live strategy, config, exit, production, or demo state changed.

## Frozen protocol

The candidate and evaluation contract were frozen before the replay in [`2026-09-19-25pct-giveback-oos-freeze.json`](../artifacts/exit-geometry/2026-09-19-25pct-giveback-oos-freeze.json).

- Freeze SHA-256: `526e461089f78e43329f30b2baf89c82b5233256fd5f8aaa9ca1377aea177886`
- Candidate: 2 ATR initial stop, no target, 500-bar cap; after +1R, ratchet to 75% of current peak favorable profit.
- Control: current 2 ATR stop, no target, 4 ATR trail after +1R, 500-bar cap.
- Entries: unchanged Donchian(100), both directions, same 16-symbol declared universe, native 4h bars.
- OOS: time-ordered 70% warmup/train and 30% held-out test per symbol; only test-half entries scored.
- Portfolio: 4,900 USDT, 0.5% risk, first 30 trades at 0.5x, 8 slots, 8%/12% drawdown derisk, 6% daily breaker, 20% halt, 0.04% fee, 0.015 ATR slippage, signed funding where retained with the validated flat fallback.

## Held-out portfolio result

| Metric | 4 ATR control | 25% giveback | Candidate minus control |
|---|---:|---:|---:|
| Total profit | 2,318 USDT | **3,371 USDT** | **+1,052 USDT** |
| CAGR | 29.1% | **41.2%** | **+12.1 pp** |
| Max drawdown | 13.2% | **11.2%** | **−2.0 pp** |
| Mean R | **+0.221** | +0.174 | −0.047 |
| Large-winner retention* | **4.75%** | 3.78% | −0.97 pp |
| Peak-to-exit giveback | 62.2% | **25.8%** | −36.4 pp |
| Mean / median holding time | 94.4h / 64h | **42.1h / 24h** | shorter |
| Fees | 165 USDT | 370 USDT | +205 USDT |
| Funding | 58 USDT | 54 USDT | −4 USDT |
| Raw / accepted trades | 477 / 414 | 728 / 703 | more capital reuse |
| Accepted-entry rate | 86.8% | **96.6%** | +9.8 pp |
| Halt breaker | not reached | not reached | — |

\* Retention is gross exit R divided by the maximum favorable excursion available through the full 500-bar opportunity horizon for trades whose opportunity reached at least 4R. It is deliberately stricter than measuring only the excursion before the exit.

## Robustness

The candidate was profitable on 12 of 16 declared symbols by accepted-trade aggregate; the control was profitable on 11 of 16. HYPE and SUI had only one accepted OOS trade each, and TAO had none, so those symbols are not powered.

By fixed point-in-time regime label:

| Regime | Control mean R / trades | Candidate mean R / trades |
|---|---:|---:|
| Trend up | +0.398 / 158 | +0.089 / 315 |
| Trend down | +0.083 / 171 | **+0.238 / 282** |
| Range/other | +0.169 / 85 | **+0.257 / 106** |

The candidate’s portfolio improvement is therefore not uniform: it gives up some of the control’s strongest upside-trend economics while improving downside trend, range, turnover, and drawdown behavior.

## Judgment

1. **Does it beat the 4 ATR control OOS?** Yes on this held-out replay’s net portfolio economics: profit, CAGR, drawdown, and capital reuse all improve. No on every requested dimension: mean R and strict full-horizon large-winner retention decline.
2. **Confidence:** medium-low. The comparison is internally paired and costed, but it is one historical held-out segment, the candidate was selected after the earlier full-history study, and the currently available collection window has not been accepted as complete prospective PIT coverage.
3. **Live-change proposal:** not yet. The result is strong enough to propose the candidate for owner review and a fresh forward experiment, not strong enough to recommend changing the live exit.
4. **Exact next step:** owner-review this frozen report; if approved, run the 25% candidate and 4 ATR control side-by-side in a newly frozen forward window with complete declared-universe receipts and unchanged risk controls. Do not edit the authored spec or live config until that window is accepted and the owner explicitly approves a change.

The repository graph query was used only to locate the existing OOS, portfolio-risk, and frozen-evidence contracts; no graph rebuild or production action was performed.
