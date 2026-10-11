# Sol2 root recovery before unreadable table audits

Date: 2026-10-11. No sudo, deployment, migration, service interruption,
research activation or venue call.

## Recovery evidence

- Root `/dev/sda2`, ext4, capacity 52,518,420,480 B. Initial available
  21,848,064 B (20.84 MiB); immediately before cleanup 20,520,960 B.
- Largest allocated directory consumers from `du -x`: `/home` 20,748,230,656 B,
  `/tmp` 9,176,027,136 B, `/usr` 7,585,382,400 B, `/var` 6,794,477,568 B.
- `/tmp/claude-1000` occupied 7,801,434,112 B. Two 3,583,008,768 B
  databases dominated: original `luffy_copy.db` and derived `t1.db`.
- Provenance: Claude session `7d5822ef-6a81-4970-89ed-93b0a310f1e0`,
  completed October 8 UTC; its retained `scratchpad/proof.py` makes `t1.db`
  with `shutil.copy(luffy_copy.db, t1.db)` then deliberately modifies/deletes
  risk state to test recovery refusal. Session transcript reports scratch-only
  testing and a separately retained recovery handoff under
  `/mnt/luffy-data/luffy/recovery/research-state-transfer-r1/`.
- Only the disposable `t1.db`, `t1.db-wal`, `t1.db-shm` were unlinked from
  `/tmp/claude-1000/-mnt-luffy-data-luffy-production/7d5822ef-6a81-4970-89ed-93b0a310f1e0/scratchpad/`.
  They were unchanged across a 30-second sample, owned by this account,
  not symlinks, and absent from accessible process file descriptors and cwd.
- Allocated bytes reclaimed: **3,583,197,184 B (3.337 GiB)**.
- Available after cleanup: **3,603,718,144 B**. Three subsequent samples,
  25 seconds apart: 3,603,566,592; 3,602,935,808; 3,602,632,704 B.
  The >=2 GiB gate held across the 50-second observation.

## Ongoing growth and ownership

- Syslog grew 277,707 B in 30 seconds (~9 KiB/s); subsequent samples agreed.
  `/var/log/syslog` ~430 MB, rotated `syslog.1` ~215 MB; journal ~851 MB.
- Last 1,000 syslog messages: 352 bootstrap heartbeat service messages,
  126 safety service messages, 483 component JSON messages.
- Nine distinct transient user bootstrap timer pairs (18 timers) repeatedly
  launch the existing operational `scripts.monitor`: heartbeat every ~10 s,
  safety every ~30 s. Example unit
  `luffy-bootstrap-85d1c3e68c464130b5ca06915b1fa15b-heartbeat-only.service`
  runs `/mnt/luffy-data/luffy/operational/frozen-demo-de6b35fc83e8/venv/bin/python -B -m scripts.monitor --heartbeat-only`.
  They remain untouched. Persistent log growth is not remediated by this cleanup;
  50-second headroom verification is not a claim of indefinitely bounded usage.
- System writers: `rsyslogd` PID 1407 (syslog account), system journal PID 402
  (root). Neither files nor writers were modified.
- Unique accessible deleted-open root files total 18,132,992 B, mostly
  update-manager PID 361343 apt package lists. Left untouched; memfd/tmpfs
  allocations excluded. 272 process FD directories inaccessible without sudo,
  so the deleted-open inventory is limited to permitted observations.
- Other retained consumers: pytest fixtures ~662 MB; shared Codex runtime
  ~1.84 GB; Claude jobs/projects/plugins ~2.6 GB; active Codex history/log
  SQLite files. These are not assumed disposable.

## Preservation and integration

Original `luffy_copy.db` backup, other scratch DBs, scripts, recovery candidate,
Claude registry reports, Sol1 operational tree, commits, repositories and all
operational databases preserved. No git reset/clean/stash or service action.
Pre-existing dirty graphify artifacts and untracked reports/checkpoints remain.

After the root gate, three native agents were assigned one table each:
`research_unreadable_plans`, `research_unreadable_evidence`,
`research_unreadable_results`. Shared-file edits held for reconciliation.
Minimal Claude execution failed with `Not logged in · Please run /login`;
the explicitly requested native audits continued.

## Table closure and final checks

All three native audits concluded KEEP: plans 12 columns, evidence 13,
results 14. Whole-table need, each column, canonical writer, canonical reader,
direct and transitive consumers, duplication, cost and historical compatibility
are documented in the three sibling `sol2-unreadable-*-review.md` reports.
No source/schema correction was justified; no code was modified, so no AST
graph update was needed. Existing dirty Graphify outputs remain untouched by
staging (queries can update their existing cache).

Root independently inspected the actual operational journal
`/mnt/luffy-data/luffy/operational/frozen-demo-de6b35fc83e8/data/luffy.db`
with a read-only SQLite connection, `query_only`, 1-second busy timeout and
bounded progress handler. Each table has 0 rows and occupies 12,288 B including
its two autoindexes; all three total 36,864 B. These are distinct checks from
the agents' production `data/luffy.db` inspection, which agreed.

Results agent ran:
`./venv/bin/python -m pytest tests/test_strategy_health_unreadable_run.py tests/test_strategy_health_unreadable_bank.py --basetemp=/mnt/luffy-data/sol2-unreadable-results-tests -q`
with **200 passed in 64.94s**, exit 0. Canonical real-step writes/loads,
run and Bank chain validation, duplicate/conflict behavior, corrupt/missing
upstream refusal and historical byte pins passed. Its owned 306,997,032 B
test directory was removed. Evidence agent cancelled its duplicate owned
test run and cleaned its exclusive directory; no duplicate result is claimed.
Broad read-only `dbstat` scans were stopped on owned helper PIDs only and
replaced with exact-name queries. No service process was stopped.

Final pre-commit root available: **3,601,362,944 B (3.354 GiB)**, above the
gate about five minutes after recovery. Net available increase from initial
sample: 3,579,514,880 B; attributed reclaimed allocation remains
3,583,197,184 B. Concurrent logs/processes explain the difference.

CLAUDE_WORK_PRESERVED: Yes, including original 3,583,008,768 B scratch backup,
proof script, recovery handoff/candidate, unfinished registry report, Sol1
operational tree and all databases/backups/commits.
INTEGRATION_CONFLICTS: None introduced; only four exclusive report files staged.
CLOSURE: Offline audits and safe root recovery complete. Ongoing timer-driven
log growth is recorded, not eliminated; no deployment or operational action.
