# M8.1 emergency fill accounting — bounded delivery

Implemented September 16, 2026. **119 relevant tests passed against final source**
in 6.67 seconds. Synthetic/offline engineering evidence only; no kernel restart
or live deployment was performed. M8.1 remains partial and unchecked; total remains
12/55. This dependency-ready work proceeds while the M3.1 frozen window is pending.

## Problem and resulting behavior

Previously, a recovered entry followed by emergency flattening could release its
entry barrier with observations only, without a durable accounting record. Retrying
a partial emergency close also replaced its earlier close-order ID.

[Recovery](../../../trader/engine/recovery.py) now retains every observed terminal
close across remaining-size retries and restarts. Once terminal orders, flat venue
and absence of residual protection are confirmed, it archives the recovery intent
in `execution_accounting` in the **same journal transaction** that clears the
recovery barrier. A failed insert/commit leaves the barrier and intent available
for retry. The archive includes decision/strategy/position identity, entry and
close-order observations, original creation clock and flat verification clock.
No new venue calls were added to the safety path.

[Accounting adapter](../../../trader/engine/accounting.py) reconciles exact fills
against those order identities. It checks terminal order quantities, complete
entry-versus-close quantity, symbol, side, timestamps, fill IDs, duplicates,
conflicts, finite numbers, explicit venue realized P&L and USDT commission.
Unrelated orders are excluded. Missing fields, history, earlier close legs,
non-USDT commissions or a full 1000-row response page prevent a verified total.
Fetch errors retain only their type; request payloads/secrets are not archived.

A successful receipt separates venue gross realized P&L, commissions and fill net
**excluding funding**. Funding and net economic P&L remain null, and
`learning_eligible=false`. It does not modify legacy trade P&L or pretend that a
recovery observation is a completed learning outcome. There is no assumed zero
funding, mark-price substitution or guessed fee conversion.

## Capture, replay and evidence

[Read-only CLI](../../../scripts/capture_execution_accounting.py):

```bash
./venv/bin/python -m scripts.capture_execution_accounting --list
./venv/bin/python -m scripts.capture_execution_accounting --intent-id ID --output /tmp/NEW-accounting.json
./venv/bin/python -m scripts.capture_execution_accounting --replay /tmp/NEW-accounting.json
```

Capture requires demo configuration, reads one archived intent and one bounded
venue fill-history page, and writes a new file exclusively. It does not submit
orders or mutate the journal. Rerun with a new filename when missing evidence
arrives. Replay is offline, verifies the digest and recomputes the assessment;
a rehashed altered assessment is rejected. Capture is manually invoked, not a
scheduled collector. Large/truncated histories remain incomplete rather than
silently assuming the first page was sufficient.

- [Synthetic receipt](../artifacts/execution-accounting/2026-09-16T215714Z-synthetic.json):
  two-unit entry, 0.5-unit partial close and 1.5-unit final close; gross 10 USDT,
  commission 0.2 USDT, fill net 9.8 USDT **excluding unknown funding**. These are
  fixture numbers, not demo or real trading performance. Offline CLI replay passed.
- [Verification and source hashes](../artifacts/execution-accounting/2026-09-16T215714Z-verification.json):
  119 passing tests across ten accounting/recovery/exit/protection/reconciliation/
  typed-outcome modules. Includes long/short, multiple fills, duplicate/conflicting
  IDs, missing fields, invalid numeric values, partial-history refusal, redacted
  fetch failures, restart and transaction rollback. Not the entire repository suite.
- [Troubleshooting](../artifacts/execution-accounting/2026-09-16T215714Z-troubleshooting.jsonl):
  Claude session-limited; direct implementation followed the owner's authorization.
- `git diff --check` passed. Existing unrelated edits were preserved.

## Deployment and remaining work

Read-only discovery against the current journal returned `ledger_present=false`
and no IDs. The running kernel has **not loaded this change**; its current process
continues using the prior recovery behavior. The additive table is created by
Journal initialization after the code is loaded. No prospective or historical
recovery records were fabricated, and no frozen population artifacts were altered.

Next M8.1 work: connect exact accounting/provenance to normal, partial, native-stop,
reconciliation and restarted exits; add safely attributed funding and non-USDT
fee handling; keep missing/estimated legacy accounting out of learning as actual.
Capture retries currently require invoking the CLI. Before deployment, verify demo
venue exposure/protection and pending recovery compatibility, then perform a
controlled kernel rollout and verify runtime. Do not force an emergency to obtain
forward evidence. An already-partially-retried legacy recovery may lack earlier
close IDs; preserve that gap rather than reconstructing ownership from proximity.

M3.1 remains a separate observation dependency: natural collection September 17
00:00 to September 18 00:00 UTC, full-window coverage review at/after its cut.
Retain exact-version outcome retries. No M3.2 before coverage review and a separate
frozen protocol, no Gate 2 restart. Trading decisions did not change; no candidate,
admission, profitability or better-performance claim resulted. No roadmap
architecture/dependency change was needed for this bounded accounting ledger.
