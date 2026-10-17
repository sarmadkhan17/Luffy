# Functional test profiles

The default `python -m pytest` selects the 69 modules in `lean-ci-manifest.json`:
1,434 collected cases covering strategy creation/backtests/qualification, paper
trading and ten-trade probation, risk and execution, containment/recovery,
market-data/accounting integrity, and active research/learning/replay.

This is a proposed CI contract. A collected case must pass; a failure, error,
skip, xfail, timeout or missing case is not approval. The candidate already had
source/test-contract failures. This change does not grant trading authority.

`lean-ci-manifest.json` contains explicit path lists, not historical test-count
allowances. Use its `core_paths` for the default core and `extended_paths` for
broader offline integration. Every trader/configuration change still requires
extended coverage; default-core success alone is insufficient for such changes.
No runtime strategy, threshold, trading configuration or risk policy is changed.

Run each historical `archive_paths` module separately, overriding default
addopts with `-o addopts=`. Keeping original paths and bytes preserves dated
script attestations, freeze bindings, version-specific globals and original
assertions. These 507 cases remain mandatory when the bound calibration scripts,
`trader/cognition/m32*`, historical test modules or frozen input bindings change.
They are archived validation, not deleted protections or replacements by generic
boundary witnesses. A historical failure remains a historical failure.

Three cases are marked `operational`. They inspect a deployed candle store or
use actual user-systemd timers. Their synthetic refusal and safety companions
remain offline. In an authorized deployment environment run their exact
`operational_nodeids` with `-o addopts= -m operational`; missing services/data do
not establish success. They were not executed during this task.

For a complete collection census, including archived and operational cases:
`python -m pytest -o addopts= --collect-only tests`. Execution of that entire
selection includes real operational checks and needs its corresponding context.

## Existing Opus consumer

No CI tool, active state, accepted retirement policy, tracker, SDD or production
checkout is changed. Do not import this file as an existing critical-gates v1
manifest. The sealed handoff includes compatible `critical-gates.json` and
`critical-reconciliation.json`, plus exact six-case quantity-test replacements.
They retain every existing pin and add the current functional core; importing
them does not silently shrink the active pin set. Missing historical pins whose
full equivalence is unproved remain unresolved.

The existing importer deliberately rejects dropped pins. The existing mapping
consumer accepts witnessed replacements as manual review only. Opus should:

1. Verify the handoff SHA256SUMS and Git bundle; create an isolated checkout.
2. Read the complete classification and original-source dependency inventory.
   Distinguish the 7,508-case old baseline from this candidate's 7,247-case census;
   1,138 old identities were already absent before this task.
3. Recollect and execute the functional core fresh. Verify all six old quantity
   inputs against their explicit replacements and retained identical assertions.
4. Consume profile path lists for ordinary offline integration; execute extended
   coverage for runtime changes, bound archives for historical changes, and
   operational checks only in the deployment profile. Consumers must override
   `-m` for operational execution rather than accepting a deselection as success.
5. Import the sealed compatible manifests only through the existing tool's
   documented `critical import DIR` interface. This task did not run that command
   against Opus state. Old unresolved pins are not made green by this import.
6. Replace old ID obligations only after reviewing an exact witnessed merge or
   an individually established retired feature. Never use a passing generic
   refusal test as equivalence for every removed historical positive assertion.
   Ambiguous preexisting missing protections remain review items.

The evidence archive contains raw before/after JUnit/events, the full census,
static dependencies and limitations, seven detected in-memory critical mutants,
the retained transient-file monitor interruption and complete baseline tail run,
exact historical fixture hashes, current nonpasses and a file-level parity
check. Static dependency inventories are not line/branch coverage measurements.

No shared helper was removed: all sixteen have existing consumers or documented
explicit plugin use. Identical bodies with different fixtures, modules or
versioned implementations were retained. The sole code merge removes a duplicate
quantity-refusal function while preserving all six numeric cases and migrating
its internal mutation-check caller. The initial missed caller and successful
affected-module rerun remain in the raw campaign evidence.


## Final essential cleanup, 2026-10-17

The verified unified tree `7ae12a4` equals the earlier `4674faa` resolution
snapshot. This branch incorporates the previously delivered simplification,
then reviews all 27 essential and 65 partial identities in
`final-essential-review.json`. Read each item's functional disposition, original
assertions, source hash and named current witnesses. A changed contract or a
retired adapter is not identical historical behavior. No missing-ID policy was
modified or automatically accepted in Opus.

The core adds five existing modules for the supervised child deadline, internal
question routing/rehashed-route refusal, historical paired-hypothesis replay,
pre-RNG generator/world/injection refusal and four-state restore containment.
No test runner, fixture framework, trading code, risk threshold or strategy
requirement was added. Current source behavior is unchanged.

The duplicate package-wide cognition import guard was obsolete: dataset and
outcome adapters authenticate trade accounting, while other adapters consume
research/observability. Static standalone replay-core checks and fresh-process
import/execution isolation remain. Historical M32 tests now refuse current
pre-RNG generation/injection; a deterministic 384-statistic fixture preserves
actual metric coverage without RNG. The pilot evidence tree is pinned to its
recorded bytes and non-final GO verdict; absence of an authorized historical run is no
longer asserted. Their original tests remain recoverable from `7ae12a4`.
The three truth-freeze test modules and every frozen package/script/artifact
remain byte-identical. Their verifier writes a report, so execute them in a
writable disposable clone and compare protected hashes afterwards. This is an
archive execution constraint, never permission to alter original records.

Existing small fixture defects are fixed locally: missing query/cut interfaces,
missing venue identity/clock, frozen allocation retry semantics, required-value
capture isolation, explicit ASGI test configuration and temporary browser output.
In-process ASGI tests start no service and authorize no production change.
The old population v1 artifact is an authenticated detached input, not generated
or backfilled; its exact hash and provenance are in the handoff evidence.

The prior sections describe the previous 7,247-case snapshot. Use the new census
and focused evidence accompanying this bundle for final counts. Do not infer a
fresh full-suite or deployment pass from selected regressions. Existing Opus
importer/policy changes, historical pin acceptance and operational verification
remain the consumer's responsibility.
