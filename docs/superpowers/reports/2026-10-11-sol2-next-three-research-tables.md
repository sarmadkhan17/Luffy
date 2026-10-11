# SOL2 — next three tables: lead integration

Three native agents were assigned exclusively to `research_unreadable_bank_objects`,
`research_unreadable_runs` and `research_archive`. Research remains DENIED.
No activation, new collection, provider call, real-data evaluation, trading
change, deployment, restart, operational migration or database cleanup occurred.
Prior recorded Claude execution failure (`Not logged in`) was reused; the
user explicitly requested native agents. Sol1 and Claude artifacts were preserved.

## Schema and table decisions

Complete column purposes, canonical writers/readers, inventoried direct and
transitive consumers, whole-table challenge, duplication, retention and costs:

- [Bank objects](2026-10-11-sol2-unreadable-bank-objects-review.md): 14 columns,
  KEEP; durable filing identity and first-registration chronology support
  verified recall, shadow recovery and independently replayable learning snapshots.
- [Runs](2026-10-11-sol2-unreadable-runs-review.md): 11 columns, KEEP;
  immutable invocation outcomes and telemetry cannot be reconstructed from
  semantic object rows, particularly failures, conflicts and not-run steps.
- [Archive](2026-10-11-sol2-research-archive-review.md): 6 columns, KEEP;
  transactionally preserves old combo/control rows before explicit rekey deletion.
  No automated runtime reader exists; manual historical inspection and compatibility
  tests remain distinct from a live consumer. No archive correction was justified.

## Independent storage and authority checks

Lead checked existing journals through SQLite URI `mode=ro`, `query_only`,
one-second busy timeout, and an eight-second per-query progress deadline.
Exact table/index names bounded `dbstat`; no full-database scan, copy,
checkpoint or VACUUM was used.

| Context | Journal file bytes | Bank rows / allocated bytes | Run rows / allocated bytes | Archive rows / allocated bytes |
| --- | ---: | ---: | ---: | ---: |
| Production `data/luffy.db` | 3,582,115,840 | 0 / 12,288 | 0 / 8,192 | 0 / 4,096 |
| Operational `frozen-demo-de6b35fc83e8/data/luffy.db` | 2,695,376,896 | 0 / 12,288 | 0 / 8,192 | 0 / 4,096 |
| Recovery `research-state-transfer-r1/candidate/luffy.db` | 15,482,880 | 0 / 12,288 | 0 / 8,192 | 0 / 4,096 |

Total is 24,576 B per journal, 73,728 B across all three contexts.
These are allocated database pages, not filesystem space available from a DROP.
Empty stores cannot account for root pressure or supply historical production
runtime measurements. All three journals report FROZEN and operator hold=1.
`research.referee=false` and `research.handoff=false` remain unchanged.

## Proven offline correction and integration

The unreadable Bank storage writer imported learning capture directly.
Actual ShadowJournal storage under the child learning-import boundary therefore
failed before insertion. The regular Bank family already has an overridden
capture hook; the unreadable family bypassed that boundary.

The correction extracts `_capture_research_unreadable_bank` in Journal and
overrides it in ShadowJournal. Normal Journal retains exactly the existing
first-insert learning callback and arguments; shadow storage retains Bank
persistence and atomic registration without importing learning. Canonical
bytes/IDs/hashes, SQL schema, duplicate/conflict semantics and historical rows
are unchanged. Dedicated synthetic regressions cover this boundary, the normal
callback and rollback of both object and receipt on capture failure.

Sol1 committed its separately owned read-only Bank evidence-function repair
as `1338cab44659eefb3084c1a1795d053c1aa7d5ce` during this audit. Its file
remained byte-identical to the lead's initial preservation hash. An older
Bank test pin rejected that accepted change: only this path now pins to
Sol1's exact committed blob. All other original historical pins remain intact.
The source-caller guard now includes the isolated shadow hook. No Sol1 source
was reverted or modified, and no tests were suppressed.

## Disk gate, preservation and log diagnosis

Initial root available: 3,566,440,448 B (>2 GiB test gate).
Operational/recovery share `/mnt/luffy-data`, initially 5,469,790,208 B free.
Agents rechecked root before their test invocations; basetemp/TMPDIR and
owned disposable databases were placed under recovery, not root.

Preservation hashes checked after implementation:

- `config.yaml`: c9d43eaf894a3cfe5fa227ebca98b4d1dc9388fd83c9b1a78d37291f31e71532.
- `data/doctrine.json`: e174dbe8139f729b0224821520c4c0c4b1e6a50802026283bc3ba58a759f4c59.
- Sol1 `research_bank_view.py`: 40252bdf773e84996d19224821f162700cc36ffc50baea216e5c45385a8a12dd.

All unchanged. No reset, clean, stash or deletion of unrelated files occurred.
Pre-existing dirty Graphify outputs and preserved untracked work are excluded
from selective staging. AST-only `graphify update .` uses no provider or semantic
extraction; persistent graph backups/cache are retained.

LOG_GROWTH_STATUS: **Still growing; diagnosis only.** All nine transient bootstrap
pairs (18 timers) remain active. Representative units invoke the frozen operational
`scripts.monitor --heartbeat-only` every 10 seconds and `--safety-only` every
30 seconds. A 1,000-line syslog sample had 353 bootstrap heartbeat messages,
126 safety messages, nine unique pair tokens and 480 component JSON messages.
Representative services exit status 1; emitted observations describe stale
heartbeats and failed bootstrap/recovery-required state. Repeated timer launches
and their observations continue despite those failures. No timer, service,
system logging configuration or root log file was modified.

Same-inode syslog measurement: 302,958 B in 39.47 seconds (7,675 B/s);
longer sample 2,857,813 B in 315.03 seconds (9,072 B/s). These observed rates
are samples, not indefinite forecasts or proof of bounded retention. Timer-driven
root growth remains open pending separately authorized remediation.

## Final verification

TESTS: Bank capture/recall/historical checks: **92 passed, 43 deselected**
in 167.62 s, including three new synthetic capture-boundary regressions.
Shared run/Bank integration suite after the hook correction: **199 passed**
in 55.47 s, plus one inherited historical-pin failure whose module had been
collected before the accepted pin update. The exact parameter passed on
final source in 0.95 s. All 200 shared cases are therefore covered; no claim
of a single uninterrupted 200-pass invocation is made. Archive: **3 passed**
in 8.09 s. These tests used synthetic records and mocked runners only.
Focused diffs pass `git diff --check`.

Final `graphify update .` exited 0: **26,502 nodes, 67,677 edges,
1,288 communities**. The final pass includes the accepted historical pin;
no semantic extraction or labeling was performed. Persistent graph outputs,
cache and curated backups remain excluded from this task's commit.

Owned disposable test allocation removed:

| Owner | Allocated bytes removed |
| --- | ---: |
| Bank | 432,717,824 |
| Runs, two passes | 617,967,616 |
| Archive | 4,190,208 |
| Total | **1,054,875,648** |

SPACE_RECLAIMED: **1,054,875,648 B** cumulative owned test allocation
(0.982 GiB). This returns newly created scratch space; it is not a net
reclamation of preexisting databases or root logs. Lead's small audit evidence
and runs' small test/cleanup records remain in recovery storage. Disposable
pytest/TMPDIR/cache trees and reproduction databases were removed.

LEAD: Three exclusive native table agents integrated by the root coordinator.
INTEGRATION_CONFLICTS: Resolved the inherited historical test pin narrowly;
no source ownership conflict. Sol1 commit/file and Claude work preserved.
ROOT_FREE: **3,560,468,480 B (3.316 GiB)** after cleanup and graph refresh.
OPERATIONAL_FREE: **5,424,570,368 B (5.052 GiB)**.
RECOVERY_FREE: **5,424,570,368 B (5.052 GiB)**; shared mount with operational.
LOG_GROWTH_STATUS: Ongoing nine-pair timer emissions; root syslog still growing.
Diagnosis complete; operational remediation remains outside authorization.
COMMIT: Selective lead integration commit containing only the two source
files, two test files and these four audit reports; exact hash returned at closure.
CLOSURE: Requested three-table audits, proven offline correction, canonical
writer/reader/consumer verification, focused compatibility tests and owned
scratch cleanup complete. All three KEEP. Research remains DENIED. No deployment.
