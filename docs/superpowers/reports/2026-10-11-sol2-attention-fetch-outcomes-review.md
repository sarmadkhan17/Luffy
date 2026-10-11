# SOL2: attention_fetch_outcomes review

Reviewed 2026-10-11 at source HEAD `f579723`. Scope: offline review of this table only. No deployment, migration, restart, provider call, database write, trading change, or registry-removal implementation. FROZEN, operator hold and research DENIED remain untouched.

SCHEMA:

| Column | Contract and actual purpose |
| --- | --- |
| `id INTEGER PRIMARY KEY AUTOINCREMENT` | Durable insertion-order receipt identifier returned by the writer. Reader orders attempts by this value. AUTOINCREMENT also updates shared `sqlite_sequence`; it is not an attempt identity or idempotency key. |
| `selection_id TEXT NOT NULL REFERENCES attention_selections(selection_id)` | Links the attempt to an already committed selector receipt. Journal connections enable foreign keys, so unknown selections fail. The adapter accepts lowercase 64-character SHA256 IDs. |
| `status TEXT NOT NULL` | Supplemental result category: `usable`, `warmup`, `missing`, `stale`, `gap`, or `error`; the persistence adapter checks token syntax, not membership in a status enum. Records the fetch result, never admission or permission to trade. |
| `reason TEXT NOT NULL` | Normalized lowercase `[a-z0-9_]` reason, maximum 64 characters at adapter boundary. `reason_token` converts punctuation/case and truncates; exact original reason survives only in telemetry/capture when those succeed. |
| `started_ms INTEGER NULL` | Fetch start wall-clock epoch milliseconds; nullable for callers lacking timing. |
| `ended_ms INTEGER NULL` | Fetch end wall-clock epoch milliseconds; adapter rejects an end before start when both exist. RegistryAttention clamps stored end to start if the wall clock moves backward. |
| `recorded_at_ms INTEGER NOT NULL` | Supplied recording clock, not a database-generated commit timestamp. RegistryAttention passes the un-clamped fetch-end clock. Thus it can precede the clamped `ended_ms` during clock regression. |

Index `attention_fetch_outcomes_sel(selection_id,id)` serves the only reader's filtered insertion-order query. There is no uniqueness constraint on attempt contents or one-outcome-per-selection requirement, no cascade deletion, no schema status/time CHECK, and no retention rule. The schema permits multiple receipts per selection; existing tests intentionally verify this.

WHOLE_TABLE_NECESSITY:

The entire table is unnecessary to the current normal kernel scan and selection flow. Retained `RegistryAttention.run` still implements a coherent receipt contract, but repository-wide Python references show its only instantiations are in tests. Current kernel startup constructs `Collector` only; `_attention_call('begin', ...)` supplies broad scan frames and membership receipts. `Collector.begin` captures those frames and never calls RegistryAttention. Current `attention` configuration has no supplemental mode. Current production table contains no historical receipts.

The retained contract could be necessary if registry supplemental collection were deliberately restored: the receipt independently records failure or a crash before capture, without undoing an already advanced cursor. Consequently, “empty” alone is not the reason for removal candidacy. The absent normal caller and absent production reader establish current lack of use; separately owned unfinished registry-removal work prevents removing this compatibility surface in SOL2.

Canonical writer: `trader/observability/attention.py:RegistryAttention.run` selects and commits selection/cursor first, calls `supplemental.fetch`, then calls `selection_persistence.record_fetch_outcome`, then returns result/provenance for capture. Adapter validates ID, tokens and clocks, translates FK refusal to UNKNOWN_SELECTION, and calls `Journal.record_attention_fetch_outcome`. Journal appends in its own serialized transaction. Persistence failure suppresses supplemental capture while preserving the previously committed selection/cursor.

Canonical reader: `Journal.attention_fetch_outcomes(selection_id)` returns every receipt ordered by ID. Direct reader consumers are only `tests/test_attention_selection_persistence.py` and `tests/test_attention_kernel_wiring.py`. No production dashboard, scan/admission, learning, replay, retention, or research consumer reads this table. Capture provenance carries `{mode,selection_id,snapshot_id}`; it does not read or reference the outcome receipt ID. Source-wide searches of `attention_fetch_outcomes`, `record_attention_fetch_outcome`, `record_fetch_outcome`, and `RegistryAttention` establish these boundaries.

Comparison with attention.db: `Store.write` persists captured scan scope, supplemental summary/provenance when supplied, candle versions, and causes. Its normal producer is Collector. Successful optional capture duplicates status/reason semantically, although raw reason and timing/failed-before-capture coverage differ. `attention.db` prunes by age/count/size; these journal receipts do not. Production attention.db currently has one retained scan, with no supplemental provenance. `attention_fetch_outcomes` is not interchangeable with candle versions or normal opportunity/deep-slot admission logic. Merging it into retained scan payloads would lose independent failed/pre-capture attempt evidence if that retired workflow were restored.

ISSUE:

Orphaned persistence/API/schema and tests remain for a currently unwired registry workflow. No proven active defect warrants a source correction here. If reactivated, unbounded append-only retention adds journal growth; every fetch adds a separate journal transaction before capture. There is no automatic duplicate suppression, but the canonical RegistryAttention path refuses duplicate selections before fetching, so adding receipt uniqueness would change a legitimate multi-attempt API rather than fix a demonstrated duplicate bug.

Read-only production measurements (`mode=ro`, `query_only=ON`, 200 ms lock timeout, 15-second progress-handler deadline; no whole-database copy):

| Metric | Result |
| --- | --- |
| Rows / distinct selections | 0 / 0 |
| Exact duplicate groups / repeated selections / orphan links | 0 / 0 / 0 |
| Oldest/newest receipt and fetch duration | No observations; NULL |
| Table allocation / index allocation | 4,096 / 4,096 bytes |
| Combined allocation / payload | 8,192 / 0 bytes |
| Combined unused page bytes | 8,176 bytes |
| Reader query plan | Uses `attention_fetch_outcomes_sel`, no separate sort |

Lead independently reports the operational `frozen-demo-de6b35fc83e8` and legacy recovery `research-state-transfer-r1/candidate` copies also contain zero rows and 8,192 bytes of table/index allocation each. These are lead-supplied measurements, not repeated by this agent.

For a bounded processing/storage measurement, the exact live table/index DDL was recreated in SQLite `:memory:` with a minimal FK parent, without filesystem or network activity. 1,000 independent insert commits with one 64-character selection ID, status `usable`, reason `complete_window`, and epoch clocks took 19.15 ms total. Table occupied 126,976 bytes (112,000 payload); index 90,112 bytes (72,744 payload): 217,088 bytes total, excluding parent/shared sqlite_sequence. Reading all 1,000 ordered receipts took 4.62 ms. This is a synthetic allocation/CPU measurement, not production disk/fsync/WAL/lock latency. Current operational receipt processing is zero because the normal caller is absent. Retention is indefinite if reactivated; no measured production growth rate exists. Dropping pages would not necessarily shrink the main DB or reclaim filesystem space without a separately authorized maintenance operation, so 8,192 bytes is allocation, not guaranteed recoverable space.

FIX_STATUS: DOCUMENTATION_ONLY

DECISION: REMOVE_CANDIDATE, conditional on closure of separately owned registry removal and explicit historical compatibility handling. Leave schema, writer, reader and compatibility tests intact in this review. No worthwhile active code correction established. Do not add a retention workflow or merge into attention.db for a dead normal producer.

TESTS:

- `./venv/bin/python -m pytest tests/test_attention_selection_persistence.py -k fetch_outcome tests/test_attention_kernel_wiring.py -q`: **10 passed, 94 deselected**. The filter applies to both files. Covers multiple attempts/null clocks, invalid tokens/clocks, unknown selection FK refusal, rollback without cursor rewind, and outcome commit boundaries.
- `./venv/bin/python -m pytest tests/test_attention_kernel_wiring.py -q`: **46 passed**. Covers retained writer ordering, pre-fetch selection commit, exact selection cut, cursor progress despite fetch failure, token versus exact reason, outcome persistence failure blocking capture, cross-connection commit visibility, optional provenance and legacy manual capture compatibility. All provider behavior uses local fakes/mocks.
- Live read-only schema/projection/index/duplication/orphan checks passed. Historical production table is empty; legacy nullable-clock and multi-attempt compatibility is exercised by the existing fixture tests. No new implementation-mirroring tests added.

COMMIT: Lead integrates this report only; no independent agent commit.

CLOSURE: Review and focused compatibility verification complete. Removal is deferred to its separate owner; this review does not claim registry-removal closure. No code or live data changed. The synthetic benchmark stayed in memory, but pytest created owned disposable fixtures in `/tmp/pytest-of-sarmad/pytest-197` and `pytest-198`. Fixture names and UTC mtimes (10:17:42 and 10:17:48) confirm these directories belong to the two reported test runs. Lead measured and removed those exact directories, reclaiming 42,041,344 allocated bytes. Lead owns aggregate filesystem/recovery headroom and final cleanup reporting. This agent did not delete shared temporary parents or unrelated artifacts.
