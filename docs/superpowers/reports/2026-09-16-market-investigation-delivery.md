# Persistent market investigations — implementation and demo delivery

Update: M1.5 authentication is now accepted; see the later
[dashboard/memory delivery](2026-09-16-dashboard-memory-delivery.md) for 183-test
evidence, deployed owner login and M2.3/M2.4. The report below preserves the
original deployment snapshot and its then-open authentication limitation.

Date: 2026-09-16. Initial acceptance: **M1.1–M1.4 accepted; M1.5 partial**. This is
engineering and forward-observation evidence, not predictive or economic validation.
The canonical checklist is now 9/55 accepted items; M1 as a whole remains unaccepted.

## What changed for the owner

Before this delivery, an attention event produced an anomaly row and a separate
price forecast. It did not maintain a trigger-specific question with continuing
assessment history. Now an investigation retains its original question, three
observable alternatives, frozen measurements, exact source versions, unknowns,
and append-only updates. The separate worker reuses an open case across scans.

In the synthetic volume walkthrough, both branches start with the same dossier
and an indistinguishable assessment. Two target bars add information but do not
resolve the test. At the complete window, sustained volume makes persistence
compatible and contradicts the other observable paths; baseline volume instead
makes normalization compatible. Earlier updates are byte-for-byte unchanged.
Neither result establishes who traded, why, a calibrated probability, or an edge.

Actual demo investigations opened for **BR/USDT** and **FIL/USDT**, both prompted
by volatility transitions. Their owner dossiers show observed volume/volatility/
relative-move/breadth facts, missing participation/liquidity/sector/catalyst/
cross-market inputs, all alternatives and their invalidators, source details,
and the next action **WAIT**. Their complete measurement window ends
**2026-09-17 16:00 UTC / 19:00 Bahrain**. No actual investigation outcome has resolved.

## Implemented and tested

- [Pure records and update functions](../../../trader/cognition/investigation.py):
  immutable state, investigation, alternative, measurement, evidence and update
  records; deterministic IDs, explicit input times and revisions, null probabilities.
- [Read-only adapter and independent ledger](../../../trader/observability/investigation.py):
  coherent completed-scan/version joins, source evaluator replay verification,
  independent SQLite persistence and retained replay inputs, bounded opt-in CLI,
  durable activation, duplicate prevention and structured diagnostics/health.
- [Owner renderer](../../../trader/dashboard/web/investigation.js), dashboard API,
  and HTML panel: facts, unknowns, competing paths, update history and next test.
  Untrusted text uses text nodes. Configured-token authentication tests pass;
  **the deployed dashboard does not have a configured token**, as detailed below.
- WAIT requires the complete frozen window. ACQUIRE recommends timestamped taker
  buy/total volume to investigate buyer-led versus seller-led turnover after a
  volume measurement; neither participant explanation is assessed and no source
  is automatically fetched. RESEARCH freezes a mechanical development question
  and counter-baseline for later work. UNASSESSABLE explains invalid scales,
  missing initial components or expired data. None grants trading authority.

**161 passed in 13.44 seconds on final source**:

```bash
./venv/bin/python -m pytest \
  tests/test_market_investigation.py tests/test_investigation_consumer.py \
  tests/test_investigation_view.py tests/test_attention_learning.py \
  tests/test_attention_view.py tests/test_attention_telemetry.py \
  tests/test_cognition_contracts.py tests/test_cognition_attention.py \
  tests/test_cognition_replay.py tests/test_single_creation_path.py -q
```

Coverage includes identical prefixes and JSON round trips; future/unavailable,
malformed and mismatched versions; missing and zero scales; relative reversal;
full window/cohort requirements; delayed arrivals; revision versus new input;
immutable terminal outcomes; irrelevant prose; restart/deduplication; activation,
staleness, bounds, retention, source failure, write locks and timeout rollback;
reserved terminal update capacity; persistent health counts; isolated watchdog
opt-in; configured-token API access and safe DOM rendering. The earlier run had
157 passes and one absolute-import convention failure, fixed without weakening
the import-boundary test. This was not a full-repository suite.

`git diff --check` and `bash -n scripts/watchdog.sh` pass. A headless Chromium
session rendered both actual dossiers and asserted their symbols, WAIT actions
and unknown inputs. Screenshot and textual capture are retained below.

## Frozen measurement and scope

Catalog **`catalog_9777745b96669be5`**, schema `investigation.v2`, remains separate
from cognition v1 and the 16 registered price forecasts. The prototype uses
N=20 baseline bars, H=5 future 4h bars and a 30-second publication guard. The
consumer has a 20-second enforced deadline; near-boundary registrations refuse.
The whole target window opens after registration. Current coverage remains the
existing capped strategy-volume subset, not the whole venue.

Volume uses all five log-volume measurements against the frozen prior-N mean/SD.
Volatility uses SD of all five **future intrabar log(close/open)** returns against
frozen baseline successive-close volatility. Relative movement sums those future
intrabar returns for each member of the **complete frozen cohort**, subtracts the
cohort median and scales by frozen asset sigma times sqrt(H). This is an explicit
new protocol, not silent reuse of v1's anchor-to-endpoint return. Missing any
required key blocks measurement; a nearby bar or cohort subset cannot substitute.
A zero future volatility scale is not testable. Delayed input carries its actual
observation time; terminal updates never change. Missing data expires after a day.
Thresholds are prototype definitions, not tuned/profitable/calibrated parameters.

Bounds: 32 active investigations, 256 retained cases, 32 updates/registrations per
invocation, 64 updates per case with a reserved terminal slot, three alternatives,
one next action, 512 diagnostic rows, 30-day terminal retention and a 32 MiB SQLite
main-file allocation ceiling. SQLite rollback journals are additional temporary
storage; this is not a total-filesystem quota. Source reads cap scan payload at
2 MiB, 4,096 bars at 4 KiB each, 64 membership records and a one-second SQL progress
deadline; lock timeout is 100 ms. The CLI owns a nonblocking process lock and a
20-second wall alarm; watchdog adds a 25-second outer timeout. Capacity/failure
counts and last successful scan survive CLI invocations in atomic health output.

No trading/network/LLM dependencies enter the pure core. The process has no
admission authority. The existing attention and forecast stores are read only;
new state lives in `data/investigation.db`. Memory retrieval remains M2 and
strategy construction remains M3.

## Deployed and forward-observed

Activation: **2026-09-16 17:32:58.968 UTC**. The first invocation refused the
pre-activation scan. A subsequent completed scan registered the two cases at
**17:33:56.496 UTC**:

| Symbol | Investigation | Source scan |
|---|---|---|
| BR/USDT | `investigation_631a0ba4382eff9a` | `scan_8274dd85643b4587b12caa37e29af0ce` |
| FIL/USDT | `investigation_fe69c2f19aa8f4d1` | `scan_8274dd85643b4587b12caa37e29af0ce` |

Every retained source ID/value/availability joined exactly to the completed scan;
all 416 input versions were checked for each case. Registration guard checks pass.
Repeated manual and scheduled invocations retain two cases, zero duplicates and
unchanged initial assessments. Observed worker times: 276.887 ms for registration,
111.393 ms for a repeat; the final health-counter version took 91.268 ms. Main DB
size was 528,384 bytes. These are workload snapshots, not a stress benchmark.
There are no LLM calls or new model-cost estimates.

`data/investigation.enabled` enables the separate watchdog invocation. Remove that
flag to stop future scheduled work; retained dossiers remain readable. The CLI
requires `--once --enable`. Only the dashboard was restarted; watchdog pause state
was restored to unpaused. The kernel was not restarted, and no positions/stops
were changed by this work.

The final runtime was **FROZEN**, with three open journal trades: existing
MacroGuard logged its Federal Funds Rate freeze at 17:33:27 UTC (20:33:27 local),
until 20:00 UTC. This was not a coordinator control mutation. FROZEN still manages
exits; no unfreeze was attempted. Demo flag was true; referee/handoff stayed false.
No new direct venue-protection reconciliation was performed for this isolated
consumer/dashboard delivery; journal status is not asserted as new venue truth.

At 17:37:28 UTC the original forecast ledger still had **16 pending, zero matured
pending targets**. Its first target remains September 17 00:00 UTC. Exact outcome
joins cannot yet be verified because no outcome is available. The new source joins
above do not substitute for that future M2.1 verification.

## Acceptance limitation: M1.5 remains partial

The panel is implemented, tested and demo-deployed, with synthetic and actual
forward-opened walkthroughs. However, live probing returned HTTP 200 without a
token. `.env` and the runtime have no `DASH_TOKEN`; the pre-existing server fallback
allows unauthenticated access and the configured bind is `0.0.0.0:8080`. Network
reachability beyond localhost was not tested. The configured-token tests return
401 correctly, but that is not proof of deployed authentication.

No token was printed/generated and existing owner access settings were preserved.
Do not describe the live panel as authenticated or check M1.5 until a non-default
access configuration is in place, owner access is preserved, and deployed tests
prove unauthenticated refusal and authenticated rendering. This concrete gap,
not forecast waiting, is the next exact task. Do not re-audit the entire project.

## Evidence and next task

- [Frozen catalog](../artifacts/investigation/2026-09-16-catalog-v2.json)
- [Synthetic replayable walkthrough](../artifacts/investigation/2026-09-16-synthetic-walkthrough.json): volume-only, volatility-only, contradiction, missing-data and paired branches.
- [Actual forward-opened cases and retained source inputs](../artifacts/investigation/2026-09-16-forward-opened.json)
- [Deployment/maturity snapshot](../artifacts/investigation/2026-09-16-deployment.json)
- [Rendered owner panel](../artifacts/investigation/2026-09-16-owner-panel.png) and [visible text](../artifacts/investigation/2026-09-16-owner-panel.txt)
- [Final source hashes](../artifacts/investigation/2026-09-16-source-manifest.json) and [registration-time hashes](../artifacts/investigation/2026-09-16-registration-source-manifest.json). The final consumer edit only added persistent health counters; frozen catalog and measurement code did not change.
- [Structured troubleshooting](../artifacts/investigation/2026-09-16-troubleshooting.jsonl)

**Next: finish M1.5's deployed authentication acceptance and recheck the existing
rendered dossiers.** Then proceed to M2 usable point-in-time memory; verify old
forward outcomes against exact target keys/versions when actually available.
No waiting is required for memory engineering. Existing trades still use legacy
strategies. This delivery produced/admitted no strategy candidate, changed no
strategy selection, and established no better strategy, calibration or profitability.
