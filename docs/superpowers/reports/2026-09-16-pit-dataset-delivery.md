# M3.1 point-in-time dataset integration

Date: 2026-09-16. **Implemented and tested the bounded dataset builder and explicit
caller; M3.1 remains partial.** Final unchanged source passed **268 relevant tests
in 17.42 seconds** (previous 221 plus 47 dataset regressions). No search, candidate,
admission, changed trading decision or better strategy is established. Count stays
12/55; earlier M1 and M2.3/M2.4 acceptance is preserved.

## Delivered behavior

[Builder](../../../trader/cognition/dataset.py),
[caller](../../../scripts/build_pit_dataset.py), and
[tests](../../../tests/test_pit_dataset.py) are three new files. Existing runtime,
source producers, configuration, authentication and frozen artifacts are unchanged.

- A declaration fixes the common discovery cut, start, observed universe,
  dependence grouping, coverage expectations and resource limits before capture.
  A separate immutable receipt records the actual CLI freeze clock and exact code
  hashes. A declaration cannot supply its own freeze timestamp.
- Features and labels are separate. Forecast/investigation features use their
  registration clock; retrospectively observed journal fields are observation-only.
  Strategy attribution does not invent historical strategy definitions.
- Original resolution, availability/import and local restoration clocks remain
  intact. Eligibility uses their maximum, the discovery cut and actual capture.
  Sequences use a prior case only when its complete knowledge clock precedes the
  current registration. A delayed import cannot create historical memory.
- Forward rows must originate after the observed freeze and inside the declared
  window. Restoring an old case later does not make it prospective. Archives that
  lack local restoration receipts are learned at the actual read time, never at
  their original import time. Retrospective rows remain explicitly labelled.
- Observed price/path changes, declared simulated returns and verified actual
  execution accounting remain distinct. Unknown actual P&L stays unknown.
- Shared cases, sequence parents and overlapping market windows share conservative
  dependence groups. Symbols are not assumed independent. Source references,
  retained raw receipts, exclusions and code manifests travel with the dataset.
- Replay rebuilds from those receipts. Conflicting or tampered frozen files are
  refused; identical store input on a later capture clock leaves output unchanged.
  Resource limits include sequence rows, payloads and SQLite query runtime.
- Coverage reports missing kinds/symbols, sequence gaps, evictions and selective
  sampling. Investigation cases are conditioned on failure/volatility labels;
  they cannot establish population failure rates. `cut_reached` is only a clock
  fact; `search_ready` remains false pending a separate protocol/coverage review.

## Retained evidence

[Tests](../artifacts/pit-dataset/2026-09-16-tests.json),
[source hashes](../artifacts/pit-dataset/2026-09-16-source-manifest.json), and
[dataset summaries](../artifacts/pit-dataset/2026-09-16-summaries.json) are saved.
All three datasets independently replayed after final testing, and source hashes
were rechecked unchanged. `git diff --check` passed. This is not the full repository
test suite or a performance evaluation.

| Evidence | Rows and meaning |
|---|---|
| [Synthetic dataset](../artifacts/pit-dataset/2026-09-16-synthetic-dataset.json) | 18 base cases and 3 sequences; 16 base PIT rows, 2 observation-only rows, 2 conservative groups. Selected/ignored forecasts, failures, transitions, synthetic counterfactuals and journal cases represented. Zero verified actual results. Synthetic clocks and costs are fixtures only. |
| [Live retrospective snapshot](../artifacts/pit-dataset/2026-09-16-retrospective_snapshot-dataset.json) | At the declared 20:31:12.658 UTC cut: 78 eligible retained skips, 50 exclusions from 128 retained records, one conservative group. All included rows observation-only, actual P&L unknown; zero PIT rows. |
| [Forward initial capture](../artifacts/pit-dataset/2026-09-16-forward-dataset.json) | Zero eligible rows, 128 exclusions, `partial_not_search_ready`. Correctly excludes existing history before the forward window. |

The [forward declaration](../artifacts/pit-dataset/2026-09-16-forward-declaration.json)
was actually [frozen](../artifacts/pit-dataset/2026-09-16-forward-freeze.json) at
**20:38:40.140 UTC on September 16**. It covers registrations from **September 17
00:00 UTC to the shared September 18 00:00 UTC discovery cut**, using the 16 symbols
in the [observed universe receipt](../artifacts/pit-dataset/2026-09-16-universe-receipt.json).
The declared four-hour dependence padding is a conservative engineering grouping,
not estimated market correlation. Category minimums expose absence; they do not
force trades or establish statistical power. This declaration is neither a
non-trade cost campaign nor a Gate 2 protocol.

## Invocation and deployment status

The CLI was explicitly exercised against the live store with read-only SQLite.
It is **not scheduled**, does not retain every newly arriving registration and
does not change the investigation worker or trading behavior. A new capture must
use a new output filename; never overwrite the saved initial capture.

```bash
./venv/bin/python -m scripts.build_pit_dataset freeze \
  --declaration declaration.json --receipt freeze.json
./venv/bin/python -m scripts.build_pit_dataset build \
  --declaration declaration.json --receipt freeze.json \
  --store data/investigation.db --output new-capture.json
```

The existing forward receipt must be reused for that declared window. Code hashes
at freeze and capture are preserved independently. Replaying a dataset is
`trader.cognition.dataset.replay(json.loads(path.read_text()))` with the saved
artifact; it does not need the live source ledger.

[Runtime/access checks](../artifacts/pit-dataset/2026-09-16-runtime-access.json):
worker healthy, FROZEN, demo true, three legacy open journal trades,
research.referee=false and research.handoff=false. Dashboard listener remains
127.0.0.1:8080; unauthenticated API 401, browser login 200, authenticated API 200.
SSH tunnelling remains `ssh -L 8080:127.0.0.1:8080 <server>` followed by browser
login. DASH_TOKEN stayed private. No orders, controls, stops, restarts or watchdog
flags changed. This was not venue reconciliation. Original BR/FIL registrations
were not amended with retroactive memory.

## Exact outcomes and next task

[Exact verification](../artifacts/pit-dataset/2026-09-16-maturity.json) at
**20:38:55.594 UTC**: all 16 original forecasts immature, with explicit
`missing_versions_retry`; original scan envelopes are absent. No nearby bars,
invented receipts or backdated restorations were used. Newer independent receipts
remain separate. First original targets close **September 17 00:00 UTC**; original
BR/FIL windows close **16:00 UTC**. Rerun `scripts.verify_memory_outcomes` with a
new `/tmp` output at maturity. M2.1 and a naturally resolved case changing a later
investigation remain pending.

**Next exact engineering task: finish M3.1's prospective population/coverage
producer.** Capture registration-time state for all eligible selected, ignored
and investigation episodes before labels; retain unresolved, missing and expired
cases plus terminal results regardless of classification. Record the eligible
population and selection reasons so discovery can measure what was omitted.
Reuse this builder and the frozen forward window; preserve exact source versions
and all clocks with append-only bounded export before eviction. Already missed
registrations must be marked missing, never reconstructed as prospective.
Require replay/coverage review before M3.2 search; do not resume Gate 2. Waiting
for outcomes must not block this engineering. Full accounting remains M8.

[Structured troubleshooting](../artifacts/pit-dataset/2026-09-16-troubleshooting.jsonl)
records sandbox/CLI permission recovery, independent clock/coverage review,
regressions and successful final checks. Claude CLI implemented the code and
tests; the coordinator reviewed and verified them. Claude then reached its
session limit during documentation; direct completion used the owner's existing
authorization. OmniQuant was considered as an optional research lead; no
dependency or integration was added.
