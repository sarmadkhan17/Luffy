# Runtime observer and Attention repair R1

The retained write refusal remains **UNCONFIRMED**. At 1791198455.1222882 the
observer reported journal `WRITE_UNAVAILABLE` and only `OperationalError`.
It discarded the SQLite code/name/message, connection identity, transaction
state and INSERT-versus-COMMIT phase. The retained observer process was 806731;
its pass took about 632 seconds (59.154 CPU seconds). Read/quick_check had
succeeded. Current ample capacity and owner permissions cannot establish the
original cause. A temporary WAL reproduction of the deployed zero-timeout
operation returns **SQLITE_BUSY (5)** during INSERT with a legitimate writer.
This confirms a defect in the probe's contention classification, not the
historical incident's precise cause.

Live monitoring now performs a bounded read, BEGIN IMMEDIATE, write and actual
commit using the existing five-second database-operation policy. It reports
operation completion/failure, elapsed/CPU time, process/connection identity,
transaction state and allowlisted SQLite diagnostics. BUSY remains unresolved
writability and fails closed on deadline exhaustion. FULL (13), READONLY (8)
and injected IOERR_WRITE (778, during commit) retain distinct failures. An
incident resolves only after a committed write; recovery_required is retained.

Full quick_check is an explicit `scripts.monitor --integrity-only` maintenance
pass with retained integrity status. `--safety-only` does not scan the database;
`--heartbeat-only` permits an independently scheduled heartbeat observation.
The current full preflight verified the journal in 369.692 elapsed / 48.429 CPU
seconds. That result is integrity verification, unlike the live probe.

Each of the seven failed scan IDs was reproduced separately from verified
retained decision-universe frame chunks. Their original transport packets and
exact capture cuts were not retained; reconstructed capture cuts are labelled,
not asserted byte-identical to an unavailable original packet. The errors had
no child return code and occurred during strict parent transport encoding.
The offending field was candle `source_receipt.supersedes`: pandas represented
an absent predecessor revision as NaN in mixed revision frames. The producer
now restores the existing nullable metadata contract and preserves integral
receipt clocks. Missing required metadata still refuses. Transport and child
failures carry bounded diagnostic reasons, not arbitrary exception messages.
The producer dependency is included in collector/evaluator code manifests.

All seven corrected reproductions traverse the real Collector thread, worker
transport, Store, evaluator and completion proof. About 16.474 MB logical scan
records are losslessly delivered in 17,355-byte manifests through the existing
source reader. The normal 64 MiB store ceiling, capture cap, 50 ms capture
budget, worker timeout and five-second source deadline remain unchanged.
Measured capture overruns remain explicit; this repair does not claim to meet
the producer capture budget. Failed placeholder scans are never selected as
successful empty scans: the source reader requires a non-null payload and the
collector's bound completion proof.

The two learning refusal identities are `decision:dec_d72a24cda3` (HYPE) and
`decision:dec_fc07d32f87` (QNT), measured at 1791197713906 and 1791197719648.
Their original decision registration succeeded. The same nullable predecessor
field became NaN in their 1h/4h forward 5-minute target-bar captures. Exact
retained target revision IDs and field diagnostics are recorded externally.
Normalization occurs inside the existing learning capture savepoint. Required
non-finite values still refuse; optional unavailable fields use their existing
null contract, never zero. An invalid capture does not erase unrelated valid
captures. In an isolated clone, the original forward path now captures both
measurements with COMPLETE replay and no logical-retry duplicate. Historical
production refusal rows are preserved; no historical repair/backfill is run.

Verification includes real concurrent writers, real quota/read-only failures,
OS EIO injection against a temporary database, commit-failure rollback,
independent heartbeat ordering, unchanged integrity status under live probes,
missing/corrupt/future evidence refusals, and normal producer/worker/reader
integration with mixed revisions and a production-shaped 557-ticker source
population at the configured 64 MiB ceiling. Coherent deployment follows only
passing focused regressions. Controlled FROZEN observation is reported in:

`/mnt/luffy-recovery/recovery/runtime-observer-attention-repair-r1/evidence/`

No production reset, migration, historical recovery, provider enablement,
automatic restart, safety-policy increase or activation is part of this repair.
The compact representation and protection/authority paths are retained.
