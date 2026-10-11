# Sol1: next three research tables — integration

## Disk gate and preservation

Initial request was blocked: root had 23,879,680 B (22.77 MiB) available.
On explicit continuation, root had **3,586,711,552 B (3.340 GiB)** available;
operational and recovery paths shared `/mnt/luffy-data`, with
**5,471,178,752 B (5.096 GiB)** available. This passed Sol2's independently
observed >=2 GiB root gate. No worktrees, builds, tests or database copies
were started before that gate. Three native agents were then assigned the
requested table names. Sol2's recorded Claude execution failure was reused;
the current no-provider-call instruction precluded another provider probe.

Sol2's preceding recovery reclaimed 3,583,197,184 allocated bytes, documented
in [its recovery report](2026-10-11-sol2-root-recovery.md). That reclamation is
not attributed to this audit. Ongoing timer-driven system log growth remains
outside this task; passing the disk gate does not establish bounded growth.

## Independent storage and authority verification

Lead inspected three existing journals with SQLite `mode=ro`, `query_only`,
one-second busy timeout and per-query eight-second progress deadline. Exact
table/index names limited `dbstat`; no broad scan, backup or checkpoint.

| Context | Existing journal file bytes | Each audited table rows | Each audited table bytes | Three-table total bytes |
| --- | ---: | ---: | ---: | ---: |
| Production `data/luffy.db` | 3,582,115,840 | 0 | 12,288 | 36,864 |
| Operational `frozen-demo-de6b35fc83e8/data/luffy.db` | 2,695,376,896 | 0 | 12,288 | 36,864 |
| Recovery `research-state-transfer-r1/candidate/luffy.db` | 15,482,880 | 0 | 12,288 | 36,864 |

Each empty table has one 4,096-byte table page and two 4,096-byte autoindex
pages. Across three tables and three journals, total allocation is 110,592 B.
This is allocation within database files, not guaranteed filesystem space
recoverable by dropping a table. Empty tables cannot account for root pressure.
Actual populated historical receipt integrity and production per-row timing
cannot be inferred from these empty tables; synthetic compatibility checks
are reported separately.

All three contexts had `state_kv.control_state=FROZEN` and
`macro_guard_operator_hold=1`. The initial preservation digests were:

- `config.yaml`: `c9d43eaf894a3cfe5fa227ebca98b4d1dc9388fd83c9b1a78d37291f31e71532`.
- `data/doctrine.json`: `e174dbe8139f729b0224821520c4c0c4b1e6a50802026283bc3ba58a759f4c59`.

`research.referee=false` and `research.handoff=false` remain required. No
held-out evaluation, research admission, trading-authority change or service
action was performed. DENIED/context-only authority is preserved.

## Table decisions and consumer correction

| Table | Schema columns | Whole-table decision | Issue and correction |
| --- | ---: | --- | --- |
| `research_next_questions` | 12 | KEEP | Durable explicit gap filings differ from source results and Bank content. Missing direct contract coverage exposed a read-only Bank consumer compatibility bug; install the existing verifying evidence SQL function on that connection and add focused regressions. |
| `research_registrations` | 6 | KEEP | Independent immutable first-registration chronology; envelope/projection redundancy is verified. No source/schema/storage correction justified. |
| `research_unreadable_questions` | 10 | KEEP | Isolated context-only questions for unreadable observations underpin their own verified chain. No source/schema/storage correction justified. |

Exact schema, every column's necessity, canonical writers/readers, all
inventoried direct/transitive consumers, duplicate representation, competing
objects, historical compatibility and measured cost are in the sibling
[next-question](2026-10-11-sol1-next-questions-review.md),
[registration](2026-10-11-sol1-registrations-review.md) and
[unreadable-question](2026-10-11-sol1-unreadable-questions-review.md) reviews.

`research_bank_view.open_readonly` deliberately bypasses Journal initialization
to avoid DDL/backfill. It also inadvertently bypassed `journal_evidence.install`,
so evidence-backed `brain_events` reads failed with `no such function:
journal_detail_v1`. Installing the existing read/verification UDF on the
read-only connection fixes the canonical consumer without changing schema,
canonical bytes, historical data, registration semantics or authority.

## Integration and validation

**433 distinct focused tests passed**, plus **22 receipt-validation assertions**:

- Next questions: 84 existing Bank tests and 8 new direct contract tests.
- Registrations: 100 receipt/generated-negative-memory tests and 4 mocked
  external/router tests; 22 extra in-memory receipt-reader/report checks.
- Unreadable questions: 96 Bank tests and 141 question/run tests.

The unreadable agent corrected an invocation error (missing TMPDIR parent) and
reran complete affected suites. The next-question regression exposed the missing
UDF, then passed after the minimal repair; a separate corruption probe needed
rollback of a denied-write read snapshot before observing subsequent corruption.
These are documented in the table reports. No outstanding source failure remains.
Historical missing-table reads still fail closed, do not create tables and do
not backfill records. Owner research/capability/cost consumers, duplicate/conflict
semantics, source corruption refusal and first-registration compatibility passed.
Full application/UI suites were not run; non-test consumer paths were inspected.

Final bounded read-only verification confirmed all nine audited table instances
still empty, all three FROZEN/hold values unchanged, and both preservation digests
identical. No operational/production/recovery database was copied or mutated by
this work. Existing fixture helpers copied small synthetic journals exclusively
inside disposable test directories after the disk gate.

Owned test storage cleaned and verified absent:

| Agent | Allocated temporary bytes reclaimed |
| --- | ---: |
| Next questions | 307,142,656 |
| Registrations | 471,248,896 |
| Unreadable questions | 464,224,256 |
| Total | **1,242,615,808 (1.157 GiB)** |

This is cumulative removed allocation from test artifacts, not recovery of
preexisting storage. Lead created no temporary directory or database.

Pre-commit headroom after test cleanup: root **3,566,641,152 B (3.322 GiB)**;
data/operational/recovery **5,470,662,656 B (5.095 GiB)**. Concurrent system writers
account for headroom drift; the >=2 GiB root gate remained satisfied.

INTEGRATION_CONFLICTS: None introduced. Only the two-line canonical consumer
repair, its exclusive test module and four task reports are selected for commit.
Existing dirty Graphify artifacts and unrelated untracked reports/checkpoints
are preserved and excluded from staging. No reset/clean/stash was used.

COMMIT: Lead's selective integration commit contains all three audits and the
canonical read-only consumer correction; its hash is returned with closure.

CLOSURE: All three tables KEEP; each current column is reviewed. No storage
deletion/migration justified. Offline reader repair and focused compatibility
regressions complete. No deployment, restart, provider call or operational change.
`graphify update .` completed successfully, exit 0: 26,487 nodes, 67,635 edges,
1,277 communities. This was AST-only with no provider call or semantic labeling.
Its curated-graph backup under `graphify-out/2026-10-11/` and refreshed graph/cache
are persistent repository graph artifacts, retained and excluded from commit.
Post-refresh available space: root **3,562,381,312 B (3.318 GiB)**;
data/operational/recovery **5,164,822,528 B (4.810 GiB)**. Graph artifacts account
for additional data-mount allocation beyond the post-test-cleanup sample.
