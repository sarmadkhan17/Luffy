# Lean offline CI migration

This checkout starts from the completed unified candidate `7ae12a4`, whose tree
matches the verified `4674faa` resolution. It incorporates the previously
verified simplification and the final essential cleanup. Production, Opus's CI
implementation/state, trading logic, configuration, risk thresholds, strategy
requirements, frozen scripts and research records are unchanged.

`lean-ci-manifest.json` uses the existing `luffy.lean_ci.v1` profile format.
The complete census is 7,271 cases: 1,462 core cases in 74 existing modules,
5,301 additional ordinary offline cases, 505 archived research cases in 21
modules, and three operational cases. `core-nodeids.json` seals the core's
collected identities. The core always runs; select the existing extended paths
for affected functionality. No new runner, framework or safety mechanism exists.

## Functional coverage and historical identities

`final-essential-review.json` reviews all 27 essential and 65 partial identities
by actual test bodies, constructor helpers and current consumers. Each item
contains original assertions, the baseline source/hash, a functional reason and
named current witnesses. Three essential identities merge into direct behavior
witnesses, 15 have changed current contracts, and nine exercise retired code.
Of the partial identities, 63 exercise retired adapters; one surviving internal
route is merged into its existing producer/restart test and one semantic
route-tampering refusal is restored. Every named witness collected and passed.
Changed contracts and retired adapters are not wholesale historical equivalence.
Original implementations/tests remain in the bundled baseline history.

The current contracts require authenticated research transfer, exact Backtest
PASS versions, ten cost-complete paper trades and explicit owner deployment.
Legacy auto-promotion, unversioned installation, unverified calibration writers,
the genome reviewer and the external decay/unreadable-bank worker are retired.
Retained original rows are not rewritten. Historical paired v2 hypotheses retain
complementary comparisons, content identity and exact replay; unsupported
shapes cannot be newly generated or acquire quantitative validation authority.

Standalone cognition replay keeps its static import boundary and fresh-process
import/execution checks. The duplicate package-wide restriction was obsolete:
other cognition modules are accounting/research adapters. Current pre-RNG tests
require generator/world/injection refusal, while deterministic arrays still
exercise all 384 imported statistics. The pilot's exact historical bytes and
non-final GO verdict replace an obsolete assertion that no pilot ever ran.
Neither that verdict nor offline test success authorizes another run or trading.

## Profiles and reproducibility

- Core: ordinary `python -m pytest`; operational markers remain excluded.
- Extended: explicit `extended_paths`; operational markers remain excluded.
- Archive: `python -m pytest -o addopts= <one archive module per process>`.
  Versioned script globals require separate processes. The unchanged three
  truth-freeze modules need a writable disposable clone because their verifier
  rewrites `verification_result.json`; compare frozen hashes afterwards. Never
  execute them against original evidence directories. All frozen package bytes
  remained identical in this verification. Original versions of the two updated
  M32 test modules remain at `7ae12a4` in bundle history.
- Operational: exact `operational_nodeids`, with `-o addopts= -m operational`,
  only in a separately authorized deployment environment. Missing service/data,
  deselection, skip or inherited failure never establishes success.

The handoff retains authenticated ignored historical inputs with byte hashes
and provenance; hydrate only disposable checkouts. The old population-v1
capture verifies its self-hash and replays without modifying its contents.
No frozen artifact is regenerated. The whole census can be collected with
`python -m pytest -o addopts= --collect-only tests` after input hydration.

## Existing Opus consumer

Verify the bundle/evidence hashes and clone independently. Recollect the exact
core identities and review each disposition before changing an old ID obligation.
The lean profile is not a `critical-gates.v1` manifest: do not feed it to the
existing critical importer. That importer and its accepted-pin policy remain
unchanged. Use the existing consumer's review/import interfaces for explicit
witness mappings or retired-feature dispositions, preserving unrelated pins.
The earlier six-case quantity merge retains all numeric inputs, fixture/body
assertions and its internal mutation-check caller; the prior sealed handoff
contains the compatible critical manifests for that merge.

The final handoff reports 972 distinct focused passes, four archived truth-freeze
passes with unchanged package bytes, and seven detected critical mutants with
passing controls. These are selected regressions, not a fresh full-core,
full-suite, operational or release pass. CI pin migration and the three
operational checks remain outside this offline cleanup.
