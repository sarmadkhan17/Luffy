# M2 case producers and forward non-trade caller

Date: 2026-09-16. Implemented and tested the dependency-ready M2.2/M2.5
integration. **221 relevant final-source tests passed in 12.65 seconds**.
M2.2/M2.5 remain partial for complete M8 execution accounting and M3 pattern
production. M1 and M2.3/M2.4 acceptance is preserved; count remains 12/55.
No better strategy, predictive calibration or economic benefit is established.

## Implemented behavior

- The investigation worker produces versioned `false_signal` cases when the
  registered same-direction alternative is contradicted by a complete exact
  window. This is an observable alternative failure, **not a claim that the
  investigation issued a trade signal or selected that alternative**.
- Measured volatility investigations produce `regime_transition` cases describing
  the frozen volatility dimension and its later persistence/normalization/reversal.
  This is not a discovered latent regime model. Missing or untestable windows
  produce no classified case. Both producers replay baseline and terminal inputs.
- Cases carry populated investigation, failure/regime and cross-market cohort
  references. Cross-market references identify frozen membership, input versions
  and configuration; they do not establish causality or a cross-market strategy.
- New journal imports carry exact observed decision references and versioned
  strategy-attribution receipts. **Strategy definition versions remain unknown**
  where the journal retained only IDs; current definitions are never attached as
  historical versions. Actual execution P&L remains unknown without M8 receipts.
- Existing registration-phase links retain their original cutoff. Explicit
  `phase=outcome` links carry actual later knowledge/import times; retrospective
  references cannot masquerade as information available at registration.
- Compatible earlier case records concretely change later evidence requests,
  retaining a no-memory comparator. Resolution, availability, original import
  and local restoration clocks all gate retrieval. Original BR/FIL contexts are
  untouched. Owner rendering distinguishes observed path scores from price bps.
- Complete raw case evidence is losslessly packed inside the existing 64 KiB
  record cap. Decoding is limited to 2 MiB and verifies integrity. No source
  versions are dropped. This is a worker resource bound, not a machine disk
  shortage. Existing count/retention and worker bounds remain in force.
- Case imports precede frequent skip imports to avoid starvation. Archive restore
  now refuses differences in the entire terminal record, including links, even
  when the source digest agrees.

Source: [pure adapters](../../../trader/cognition/outcomes.py),
[bounded producers/retrieval](../../../trader/observability/outcomes.py),
[owner renderer](../../../trader/dashboard/web/investigation.js).

## Declared forward experiment caller

[Caller](../../../scripts/register_nontrade_experiment.py) uses the existing
`register_counterfactual` API. A declaration supplies `schema_version` equal to
`nontrade-experiment.v1`, `id`, `decision_id`, integer `direction` (+1/-1),
`cost_bps`, `cost_source`, `notional`, and `currency` equal to `USDT`.
There are no cost/notional defaults. Cost provenance names a declared simulation
assumption, not verified actual costs. Invocation:

```bash
./venv/bin/python -m scripts.register_nontrade_experiment --declaration /path/to/declaration.json
```

It requires a fresh non-executed journal decision joined to the exact fresh scan,
freezes the last closed exact baseline, stamps current registration after reads,
and targets the next unopened 4h bar with a publication guard. Changed declarations
conflict; an identical declaration retries idempotently. Existing exact-target
resolution, explicit missing-target retries and immutable terminal outcomes are
reused. It never orders or changes admission/risk. **No live experiment was
registered: no live cost/notional declaration was supplied.** Synthetic caller
registration and resolution passed. This is an explicit callable integration,
not an automatically enabled simulation campaign.

## Evidence and deployment

[Tests](../artifacts/memory-producers/2026-09-16-tests.json) cover the previous
205-test relevant suite plus 16 producer/caller regressions: exact replay,
forged inputs, concrete paired reasoning, delayed knowledge/restoration, terminal
conflicts, bounded decompression, reference provenance, fresh-decision joins,
explicit costs, forward registration, missing-target retry and resolution.
This is not the full repository suite or performance validation. JavaScript
syntax and tracked-diff whitespace checks passed.

[Synthetic case archive](../artifacts/memory-producers/2026-09-16-synthetic-cases.json),
[paired reasoning](../artifacts/memory-producers/2026-09-16-paired-reasoning.json),
[synthetic declaration receipt](../artifacts/memory-producers/2026-09-16-synthetic-registration.json),
and [final source hashes](../artifacts/memory-producers/2026-09-16-source-manifest.json)
retain reproducible evidence. Synthetic monetary values are fixtures only.

Final-source worker invocation at 19:48 UTC: `ok`, 783.632 ms, zero refusals/retries,
two active investigations. Runtime snapshot retained 128 forward-observed skip
records, 96 with the new references; later invocations can replace retained skips.
No live classified case, resolved price memory or registered counterfactual was
observed. The watchdog launches fresh modules, so producer code is deployed;
new classifications have synthetic acceptance and await real terminal inputs.
[Runtime snapshot](../artifacts/memory-producers/2026-09-16-runtime.json).

Read-only status at 19:49 UTC: FROZEN, three open legacy journal trades. Demo true;
research.referee=false and research.handoff=false. No trading/safety controls,
orders, stops, frozen research, doctrine, kernel/dashboard processes or watchdog
flags changed. This is not venue reconciliation.

Dashboard remains **127.0.0.1:8080**. Unauthenticated API 401, owner browser-session
login 200, authenticated `/api/investigations/latest` 200. Both original cases
remain `pre_memory_registration`. DASH_TOKEN was neither printed nor saved.
Remote access remains `ssh -L 8080:127.0.0.1:8080 <server>` followed by the browser
login at `http://127.0.0.1:8080`. [Access evidence](../artifacts/memory-producers/2026-09-16-access.json).

## Maturity and next exact work

At 18:53 UTC all 16 original baselines still joined exact retained versions.
At **19:48:29 UTC**, all 16 original forecasts remained pending/immature, but
**each now lacks one exact baseline version in the live shared store**. Original
scan envelopes are also absent. The verifier explicitly reports
`missing_versions_retry`; no nearby bars were used or receipts backdated.
Newer forecasts retain their independent prospective receipts.
[Exact verification](../artifacts/memory-producers/2026-09-16-maturity.json).

M2.1 remains waiting. After September 17 **00:00 UTC**, rerun
`scripts.verify_memory_outcomes` with a new output filename; verify exact versions
and immutable measured outcomes, or retain explicit retry for missing evidence.
Original BR/FIL windows close September 17 **16:00 UTC**. No naturally resolved
live memory case has yet changed a later investigation.

Next dependency-ready engineering: **M3.1 forward point-in-time dataset producer**
using these retained case/decision/cohort receipts. Declare discovery cut,
coverage and dependence units before any search; preserve missing original
versions explicitly. Do not treat synthetic acceptance as pattern discovery or
resume Gate 2. Full strategy-definition lineage needs prospective producer
receipts; full actual execution accounting remains M8. A live non-trade campaign
requires an explicit cost/notional declaration; do not invent one.

Claude CLI remained session-limited when checked; direct work used existing owner
authorization. [Structured troubleshooting](../artifacts/memory-producers/2026-09-16-troubleshooting.jsonl)
records sandbox refusal, fixture/storage issues, regression recovery and the
changed exact-source availability. No candidate was created/admitted and no
trading decision was changed through this memory integration.

## Subsequent work

The [PIT dataset delivery](2026-09-16-pit-dataset-delivery.md) reuses these producers
with explicit declarations, clock-safe sequences and coverage reporting; 268
relevant final-source tests passed. M3.1 prospective population capture remains
partial. This does not change the historical M2 acceptance above.
