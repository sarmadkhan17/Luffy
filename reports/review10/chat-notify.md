# Review 10 chat / notify partition

Baseline: `4deca5c275c30c2c59c227b5170bab651cf777fe`. All 7 canonical owned files fully inspected; exact hashes, line counts, consumers, costs, classification and SDD boundaries in `chat-notify-files.json`. KEEP 4 / SIMPLIFY 3 / MERGE 0 / REMOVE 0. No historical evidence or original records altered.

Implemented paths:

- Dashboard `/api/chat` → fresh ChatEngine → AnalystAgent → BrainLLM purpose `chat` → finite owner_query → existing typed owner evidence readers. Supported kinds are decision/trade/research/portfolio/world/strategies/cost/approvals/learning/capability. Records carry identity, source, hash and temporal/verification fields. Exact-detail queries follow retained registrations and snapshots; missing evidence remains unavailable. Runtime chat does not directly read arbitrary Knowledge Vault notes or prove research intelligence beyond those established readers. No new memory/research connector was added.
- The agent returns unavailable when no exact evidence was consulted, or unknown when no consulted record citation appears. Citation presence alone does **not** verify every generated sentence or arithmetic claim; provider semantic correctness remains a limitation. `brain.enabled=false` is an intentional containment switch, not a missing-function defect. All verification used stub providers.
- Kernel/producer recorded results → optional Telegram.send. Kernel owns polling and typed owner adapters; this 2-file notify package is a sender, not an autonomous polling/control service. Inspected entry alerts after decision update, confirmed macro/risk transition alerts, exit-return alerts, manual-close result alerts, and flatten completeness messages. Transport acceptance is not venue-state or owner-read confirmation.

Confirmed defects corrected:

1. Runtime execution accepted legacy helper names and undeclared `root` kwargs despite advertising only finite owner_query schemas. `_exec` now accepts owner_query only, requires valid JSON object arguments, and rejects keys outside kind/identity/offset. Root/config stay engine-controlled. Existing legacy module helpers/schemas remain available for compatibility; no raw SQL/path/control tool is exposed.
2. Legacy get_pnl fabricated zero from missing PNL and ignored retained UNKNOWN/estimate booking classifications. It now reuses existing `dashboard.economics.journal_pnl` and `booking_receipts`; unknown totals/win rate/PF remain None, unknown count and recorded quality are explicit, and whole-trade completeness is false. Tests cover null, retained UNKNOWN scalar-zero and retained estimates. There is no parallel accounting classifier. This legacy helper is outside canonical Stage8 chat after strict dispatch.
3. Telegram send reported HTTP success without Bot API `ok=true`; now it requires acknowledgement.
4. Telegram request exceptions could log bot-token-bearing URLs; logs now contain exception class only.

Root separately found and corrected an out-of-partition caller issue: exchange-exit alerts described estimated booked PNL without qualification. Root owns that narrowly scoped Kernel wording change and its tests; it does not modify accounting/control state. Existing HTML formatting is intentionally preserved because kernel entry alerts use `<b>`; malformed/oversize producer content can still fail delivery and is not grounds for a replacement alert/outbox system.

Boundaries/costs:

- No chat control authority, activation, provider retries, new notifications, queues, database migrations, graph machinery, or changes to FROZEN/Risk/Execution. No full CI, services, production databases, provider calls or trading activity.
- Default tool loop is 5 rounds plus forced final response; recent history is bounded to 6 × 400 characters. Query/record bounds come from existing authority. No new durable chat memory or duplicate knowledge representation was introduced.
- Legacy PNL all-time aggregation scans retained trades and now reads their receipt histories through the shared reader: O(trades + receipts) volume and O(trades) SQL queries. This is compatibility-only, unscheduled work. No legacy file was removed based on static references.
- Chat still lacks deterministic claim-by-claim semantic verification. Typed readers disclose total monetary costs unavailable and historical evidence not current venue truth. These are honest evidence/implementation limits; review does not activate future architecture.

Verification (offline):

- `PYTHONPATH=. ./venv/bin/python reports/review10/chat-notify-baseline-repro.py`: original HEAD modules loaded under temporary names (no source swap); fixture DB and fake transport prove all five baseline counterexamples. `chat-notify-baseline-proofs.json` records results.
- `TMPDIR=/tmp ./venv/bin/python -m pytest -q tests/test_review10_chat_notify.py tests/test_chat_agent.py tests/test_stage8_owner_os.py tests/test_owner_interface_boundary.py tests/test_accounting_truth_followup.py`: **180 passed**, `chat-notify-tests.txt` (before adding two receipt-specific regression cases).
- `TMPDIR=/tmp ./venv/bin/python -m pytest -q tests/test_review10_chat_notify.py`: **16 passed**, `chat-notify-regression-tests.txt`, including both added receipt cases.
- `TMPDIR=/tmp ./venv/bin/python -m pytest -q tests/test_owner_telegram_binding.py tests/test_llm_disabled_boundary.py`: **54 passed**, `chat-notify-consumer-tests.txt`.
- Initial broad run encountered five Owner IPC path-security rejections because inherited TMPDIR traversed group-writable `/mnt/luffy-data/test-tmp`; the corrected run uses trusted `/tmp`. Initial attempts shared a log; padding was removed and that limitation is recorded in `chat-notify-tests-initial-environment-failures.txt`. No IPC validation was weakened.

Exact union: **244 unique tested node IDs** in `chat-notify-tested-nodeids.json`; collection-only output in `chat-notify-collected-nodes.txt`. Repeated regression execution is not double-counted. `git diff --check` passes for this partition.

Additional independent-vault review found the existing shared monetary classifier ignored the producer's `booked_pnl_basis='estimated'` marker. Root authorized a two-line `trader/dashboard/economics.py` dependency fix: include direct metadata and recognize retained marker in the existing estimated check. Actual temporary Journal receipt regression now verifies both chat helper and Vault text. Red before: 1 failed/2 passed; final focused regression + acc03 economics + accounting truth: **37 passed** in `chat-notify-economics-marker-after.txt`. This adds one new parameter and seven acc03 test nodes to the prior union, yielding 244 unique nodes. Independent findings/clearance and follow-up are distinguished in `independent-vault-review.md`.

Owner decisions: none required for these bounded changes. Commit/integration handled by root; all changes remain offline for post-Review12 consolidation. Existing gate stop and provider containment preserved.
