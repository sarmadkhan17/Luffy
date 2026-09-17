# Cognition historical replay — 2026-09-15 run

Sections 1–6 are derived from the script's generated `/tmp/luffy-history-20260915/report.md`,
not copied verbatim: some lines (e.g. file hashes, unresolved reasons) were added from
report.json and the manifest. "Findings", "Reproducibility" and "Recommendation" were
written by hand, based only on the generated artefacts.

Retrospective reconstruction from a local candle store. **Not** a verified point-in-time record. Offline research artefact only. Nothing here places orders, admits strategies, writes to production stores or changes runtime state. No significance, predictive, causal, edge, PnL or profitability claim.

## Command

```bash
./venv/bin/python -m scripts.cognition_history --db /home/sarmad/trader/data/candles.db --timeframe 4h --start 2026-08-31T00:00:00Z --stop 2026-09-14T00:00:00Z --resolve-until 2026-09-15T00:00:00Z --output-dir /tmp/luffy-history-20260915
```

## Identity

- input sha256 (canonical): `446a7a573e7fcabf63ce62860bb423403ffe3c5c5c2cb8c8e5fee419c429434c`
- config sha256 (canonical): `dd6f5093c8c9e311591d19488541637370b0bcd3689bc70b5474d197c6ae93e6`
- trace sha256 (canonical): `a3a83eea276e588daeefb73b3b19d163ccc98f3bf6345a5e64114b16b9ded244`
- config_id: `cfg_fe9a78632b92e8d3`; config: frozen `CognitionConfig()` defaults
- rerun of exported input.json through replay.run identical: `True`
- source: `/home/sarmad/trader/data/candles.db` (table `candles`)

File sha256 (bytes as written): input.json `910fe6d6aae89977b8569261ba1957885bdae26c86f75fde44a95a85e2bf0f65`,
trace.json `9aace3fe8e8e40ecfdebd989768894c527e10690ff558b21885fc349aa749449`,
report.json `b804cc66171c9280fdc9a7cfe65679d8d6ec884da762ed39f40322bce3d5d2a7`.

## Window

- timeframe 4h; decisions [2026-08-31T00:00:00Z, 2026-09-14T00:00:00Z) = 84
- warmup from 2026-08-26T16:00:00Z (26 bars); resolve_until 2026-09-15T00:00:00Z; last deadline 2026-09-14T16:00:00Z; complete horizon `True`

## Universe and coverage

- symbols: 63 (rule: All distinct stored symbol keys for the timeframe with at least one candle whose open ts is in [start - (window+short+1)*tf, start), i.e. fully closed by start. Chosen only from pre-start rows, sorted, frozen from start through resolve_until. No survival, coverage or forward-return filter. Assumed static research cohort, NOT historical tradability or index membership.)
- symbols first stored after start (excluded): 0
- possible aliases (not merged): none detected
- rows read 6368
- bars expected 7308, valid 6368, missing 940 (42 symbols with gaps), rejected rows 0, export-refused rows 0

| symbol | valid | missing | rejected |
|---|---|---|---|
| 1000PEPE/USDT | 92 | 24 | 0 |
| 1000SHIB/USDT | 92 | 24 | 0 |
| ADA/USDT | 92 | 24 | 0 |
| APT/USDT | 92 | 24 | 0 |
| ARB/USDT | 92 | 24 | 0 |
| BCH/USDT | 92 | 24 | 0 |
| CL/USDT | 92 | 24 | 0 |
| CRV/USDT | 92 | 24 | 0 |
| CYS/USDT | 92 | 24 | 0 |
| DASH/USDT | 92 | 24 | 0 |
| DEXE/USDT | 92 | 24 | 0 |
| DOT/USDT | 92 | 24 | 0 |
| ETHFI/USDT | 95 | 21 | 0 |
| FET/USDT | 92 | 24 | 0 |
| ICP/USDT | 92 | 24 | 0 |
| INJ/USDT | 92 | 24 | 0 |
| INTC/USDT | 92 | 24 | 0 |
| KORU/USDT | 92 | 24 | 0 |
| LSK/USDT | 113 | 3 | 0 |
| LTC/USDT | 92 | 24 | 0 |
| MAGMA/USDT | 92 | 24 | 0 |
| META/USDT | 92 | 24 | 0 |
| MOVR/USDT | 92 | 24 | 0 |
| NVDA/USDT | 95 | 21 | 0 |
| OP/USDT | 92 | 24 | 0 |
| PROM/USDT | 92 | 24 | 0 |
| QQQ/USDT | 92 | 24 | 0 |
| RAYSOL/USDT | 105 | 11 | 0 |
| RIVER/USDT | 105 | 11 | 0 |
| SNDK/USDT | 92 | 24 | 0 |
| SOXL/USDT | 92 | 24 | 0 |
| SPCX/USDT | 92 | 24 | 0 |
| T/USDT | 92 | 24 | 0 |
| TRUMP/USDT | 92 | 24 | 0 |
| TRX/USDT | 92 | 24 | 0 |
| UAI/USDT | 107 | 9 | 0 |
| WLD/USDT | 92 | 24 | 0 |
| XAG/USDT | 92 | 24 | 0 |
| XAU/USDT | 92 | 24 | 0 |
| XLM/USDT | 92 | 24 | 0 |
| XMR/USDT | 92 | 24 | 0 |
| ZRO/USDT | 92 | 24 | 0 |

## Decisions

- decisions 84; universe rows 5292; eligible 4642; ineligible 650; selected 241; eligible-unselected 4401
- decisions selecting nothing 1; cohort ok 84; selected per decision {0: 1, 1: 2, 2: 4, 3: 77}
- row status {'ok': 4642, 'stale': 650}
- omission reasons {'below_min_salience': 2859, 'beyond_top_k': 932, 'open_episode': 610, 'stale': 650}
- episodes 241; framing {'asset_specific': 67, 'unknown': 174}
- framing reasons {'divergence_without_broad_move': 67, 'no_discriminating_evidence': 174}
- hypothesis outcome status {'asset_divergence': {'confirmed': 65, 'falsified': 134, 'unresolved': 42}, 'market_continuation': {'confirmed': 48, 'falsified': 151, 'unresolved': 42}, 'unknown': {'confirmed': 86, 'falsified': 113, 'unresolved': 42}}
- outcomes 10007: {'confirmed': 199, 'falsified': 398, 'measured': 8489, 'unresolved': 921}; rejected inputs 0

## Baselines (descriptive only)

| bucket | samples | status | unresolved frac | abs z: n / mean / median | abs log-return: n / mean / median |
|---|---|---|---|---|---|
| raw:selected | 241 | {'measured': 227, 'unresolved': 14} | 0.05809 | 227 / 1.1 / 0.63 | 227 / 0.04723 / 0.01797 |
| raw:unselected | 4401 | {'measured': 4209, 'unresolved': 192} | 0.04363 | 4209 / 0.9626 / 0.6271 | 4209 / 0.03847 / 0.02077 |
| relative:selected | 241 | {'measured': 199, 'unresolved': 42} | 0.1743 | 199 / 1.136 / 0.5348 | 199 / 0.03898 / 0.01638 |
| relative:unselected | 4401 | {'measured': 3854, 'unresolved': 547} | 0.1243 | 3854 / 0.8563 / 0.4872 | 3854 / 0.03317 / 0.01606 |

Relative log-return is the asset's forward log return minus the frozen cohort median.

Unresolved reasons (report.json): raw:selected deadline_bar_unavailable 14; raw:unselected
deadline_bar_unavailable 192; relative:selected cohort_incomplete 28 + deadline_bar_unavailable 14;
relative:unselected cohort_incomplete 355 + deadline_bar_unavailable 192.

## Assumptions and limitations

- available_ms = close_ms for every candle: an assumed zero arrival lag; the store records no arrival time.
- The store records no revisions: each (symbol, tf, ts) has one row as it is now.
- Membership is an assumed static research cohort, not historical tradability.
- Venue/source per row is unknown; candles.db is a local store of mixed or unknown provenance.
- Symbol keys are preserved as stored; possible aliases are disclosed, not merged.
- Rows may reflect storage selection or survivorship (what was ever fetched/kept).
- Missing bars are missing: no interpolation, resampling or fill-in.
- No participation series are exported (taker_buy/OI not used).
- Thresholds are the uncalibrated defaults; no tuning was done.
- Selected vs unselected differences are not tested for significance and are not evidence of edge.

## Findings (hand-written, from manifest.json per-symbol coverage)

- **The missing bars are all at the end of the window, with no holes in the middle.** All 63 symbols start at the
  warmup's first bar (open 2026-08-26T16:00Z). None has a gap inside the window (row status
  has no `gap`/`warmup`). 21 symbols run to the last expected bar (open 2026-09-14T20:00Z).
  36 stop at the bar opening 2026-09-10T20:00Z (24 missing each). The other 6 stop at bars opening
  2026-09-11T08:00Z (2), 2026-09-13T00:00Z (2), 2026-09-13T08:00Z and 2026-09-14T08:00Z. Once the
  anchor bar is missing, a symbol is `stale`. That gives 36×17 + 2×14 + 2×4 + 2 + 0 = 650 stale rows, which is every
  ineligible row. The likeliest cause is the live store no longer refreshing symbols that left
  the scanned universe. That is a storage-selection effect, and this run cannot confirm it.
- The universe rule kept them in the cohort rather than dropping them for poor coverage. As
  designed, the eligible cohort shrinks after 2026-09-11. Baseline-relative outcomes for decisions whose frozen cohort includes a symbol that later
  went stale remain `cohort_incomplete` (383 = 28 selected + 355 unselected). This count refers to
  baseline_relative outcomes only, not to all hypothesis outcomes. The full-cohort rule
  never grades a subset.
- 77 of 84 decisions hit K=3. `beyond_top_k` (932) and `open_episode` (610) are frequent,
  so with these defaults K binds most of the time. This is an observation, not a tuning proposal.
- Framing was never `market_wide` or `contested`: no decision had `broad = true`.
- The selected and unselected baseline figures are shown for selection-bias inspection only. The samples
  overlap across 4h decisions with 20h horizons, so they are not independent. No inference is drawn.
- No issue was found in existing cognition code. The run completed with 0 rejected inputs, and every
  status and reason stayed within the documented vocabulary.

## Reproducibility

- The manifest ties `config`, `config_id`, `end_ms` (1789430400000) and the three canonical hashes
  to this export. `rerun_identical: true` means a second `replay.run` over the serialised
  input.json text produced a byte-identical trace.
- Independent replay check (completed by the parent):

  ```bash
  ./venv/bin/python -m trader.cognition.replay --input /tmp/luffy-history-20260915/input.json \
      --output /tmp/luffy-history-20260915/independent-trace.json --end-ms 1789430400000
  ```

  Output: decisions 84, episodes 241, outcomes 10007, resolved 9086. trace.json and
  independent-trace.json have the same sha256,
  `9aace3fe8e8e40ecfdebd989768894c527e10690ff558b21885fc349aa749449`.
- Tests: the parent independently ran all cognition and history tests: 73 passed in 2.79s.
- Reruns do not reread the live DB. A later export over the same bounds could differ if the store
  is backfilled, and the input hash would show that.

## Recommendation

Keep this original gappy run as recorded. Before widening the window or interpreting scanner
quality, diagnose source coverage and storage selection for the stale tail. Any coverage-controlled
subset must be a predeclared sensitivity analysis, reported alongside this original run. It cannot
prove point-in-time eligibility. No tuning is chosen here.
