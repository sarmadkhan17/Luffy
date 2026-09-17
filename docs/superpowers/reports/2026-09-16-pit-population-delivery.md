# M3.1 prospective population producer

Implemented, tested and enabled in the existing scheduled demo consumers on
September 16, 2026. **M3.1 remains partial; acceptance remains 12/55.** Final
unchanged source passed **269 relevant tests in 22.38 seconds**, including 12 new
population tests. This is engineering evidence, not predictive or economic
validation. No candidate, admission, better strategy or changed trading decision
is established.

## Behavior and boundaries

`trader/observability/population.py` captures registration receipts in the same
transaction as the forecast/investigation registration, before any labels. The
forecast and investigation consumers call it directly; a later CLI scan cannot
reconstruct a missed registration. Forecast receipts include selected/ignored
status, reasons, frozen predictions and exact source bars/versions. Investigation
receipts include the frozen state, exact inputs and memory actually known at
registration. Existing BR/FIL investigations receive no retroactive memory.

Each observed scan records its population rows, declared eligibility, selection
and registration/skip reasons, membership, absent declared symbols and source
manifest. Repeated scan observations are not independent episodes. The eligible
denominator is the observed scan rows and registered episodes, not every market
event. Producer activation after the start, missed registrations, source outages,
missing exact targets and collection intervals over ten minutes are explicit.
Unobserved scans cannot be reconstructed and complete sampling is never asserted.

Unresolved registrations survive as receipts. Updates, missing targets, unavailable
forecasts and expired investigations remain explicit. All terminal investigations
are retained regardless of whether they become false-signal/regime-transition
memory. Terminal identities reject changed payloads, including after export.
Registration and source versions, resolution/availability/import clocks and
consumer/producer hashes survive. No nearby-bar substitution is introduced.

The transactional outbox retains full receipts before source retention can remove
them. Exports use synced temporary files, exclusive links and directory fsync
before acknowledgement. At the next invocation unexported committed receipts must
flush before source pruning; export failure stops the consumer rather than losing
its evidence. Bounds per stream: 32,768 retained event identities, 256 pending
payloads, 2 MiB per payload, 256 MiB export directory, plus existing ledger/page
and consumer runtime bounds. Exhaustion is an error requiring operator attention,
not silent eviction. Temporary crash files count against the export bound.

`scripts/capture_pit_population.py` writes a new self-contained coverage artifact
and calls the existing M2 typed producers and M3.1 builder. Raw population events
remain alongside the typed dataset. Classification-neutral cases and unresolved
cases are preserved in that population layer; the older typed dataset still has
its declared kinds and classification selection limitations. Typed terminal rows
without a matching registration receipt are excluded from prospective use.

The local immutable index independently preserves each import clock. A capture
with matching configured local ledgers verifies that receipt before using it.
Copied exports without that local index are learned at the actual archive read,
never backdated. `population.replay_capture(artifact)` replays without live sources;
raw investigation replay reuses the M2 archive replay. Prior evidence can influence
later reasoning only through the unchanged M2 knowledge-clock rules. Observed
movement, simulated counterfactuals and verified execution P&L remain separate;
missing accounting stays unknown and full accounting remains M8-dependent.

## Tests and retained evidence

- [Tests](../artifacts/pit-dataset/2026-09-16-population-tests.json) and
  [final source hashes](../artifacts/pit-dataset/2026-09-16-population-source-manifest.json).
  The final suite covers 15 relevant modules, not the full repository. Its scope
  differs from the prior 268-test suite; these totals must not be added together.
- Twelve new synthetic tests cover selected/ignored capture, all investigation
  results, expiry and pruning, late activation, missed-registration exclusion,
  export failure and retry, transaction rollback, terminal conflict, clock/window
  boundaries, independent local clocks and source-free replay/tamper refusal.
- The original 268-test evidence and all frozen declaration/capture files remain
  unchanged. All three prior datasets independently replayed after final testing.
  `git diff --check` passed; unrelated workspace changes were preserved.
- [Initial population capture](../artifacts/pit-dataset/2026-09-16-population-initial-capture.json):
  two activation receipts, zero eligible registrations before the future window.
- [Structured troubleshooting](../artifacts/pit-dataset/2026-09-16-population-troubleshooting.jsonl)
  records sandbox failure, Claude session limit, fixture corrections, clock/export
  review, test results, deployment and access retry.

## Deployment and actual runtime

[Configuration receipt](../artifacts/pit-dataset/2026-09-16-population-config.json)
mirrors `data/pit_population.json`. Both existing watchdog consumers now load this
opt-in configuration. Cron actually invokes the watchdog every five minutes;
watchdog is enabled and the investigation enable flag is present. Forecast hook
activation: **21:01:05.280 UTC**; investigation: **21:01:05.475 UTC**. Both exercised
successfully without restarting kernel/dashboard or changing watchdog scheduling.
A later [scheduled check](../artifacts/pit-dataset/2026-09-16-population-scheduled-check.json)
confirms both consumers ran naturally at **21:05:01 UTC**, status ok with population
hooks enabled. This proves deployment and scheduling, not yet a naturally collected
in-window population or measured collection completeness.

Reuse the original freeze **September 16 20:38:40.140 UTC**, 16 observed instruments,
registration window **September 17 00:00 UTC through September 18 00:00 UTC
(exclusive)**. Nothing was re-frozen. The capture CLI itself is not scheduled:

```bash
./venv/bin/python -m scripts.capture_pit_population \
  --config data/pit_population.json --output /tmp/pit-population-NEW-UTC.json
```

Use a genuinely new filename. Exports accumulate in
`data/pit-population-20260917/{forecast,investigation}/`. Preserve them and the
local receipt indices. A lost interval remains a gap; do not manufacture a new
prospective history or change this frozen window.

[Runtime/access evidence](../artifacts/pit-dataset/2026-09-16-population-runtime.json):
healthy attention worker and consumers, demo true, three open journal trades,
referee/handoff false. **Correction to the previous handoff: persisted state is
ACTIVE.** The control journal records MacroGuard changing FROZEN to ACTIVE at
**20:32:27.620677 UTC**, before this session. This task changed no controls, risk,
orders, stops, research or doctrine. This was not venue reconciliation.

Dashboard still listens only on **127.0.0.1:8080**. Unauthenticated API returns
401; browser login, authenticated attention API and dashboard return 200. The
first authenticated summary request exceeded a five-second timeout; the bounded
attention endpoint and browser page succeeded on retry. DASH_TOKEN stayed private.
Owner access remains browser login locally or
`ssh -L 8080:127.0.0.1:8080 <server>` then the same login. No default bypass restored.

## M2.1 and next exact task

[Exact verification](../artifacts/pit-dataset/2026-09-16-population-maturity.json) at
**21:01:42.487 UTC September 16**: 18 forecasts, zero mature. The original 16 still
have missing exact baseline versions and absent scan envelopes; newer independent
receipts remain separate. First original targets close September 17 00:00 UTC;
original BR/FIL windows close 16:00 UTC. M2.1 remains pending. No naturally resolved
case has changed a later investigation. Waiting did not block implementation.

**Next: M3.1 population coverage/replay review and cohort integration.** Verify
naturally scheduled receipts after the declared start; capture/replay to a new
filename; reconcile scan eligibility, selected/ignored/investigation registrations,
missing registrations, unresolved/expired and all terminal results against both
local ledgers. Add an explicit classification-neutral cohort view over these raw
population receipts, preserving the existing typed dataset contract. Review full
window coverage at the common cut and retain honest gaps. Acceptance needs that
review; collection being enabled is insufficient. Do not start M3.2 before coverage
review and a separately frozen search protocol. No Gate 2 restart or admission
bypass. Run exact outcome verification again when targets mature using a new
`/tmp` filename; retain unavailable cases and exact-version retry requirements.
