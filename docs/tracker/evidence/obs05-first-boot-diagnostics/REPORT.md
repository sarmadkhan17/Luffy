# OBS-05 first-boot refusal diagnostics

CLOSED at engineering/offline diagnostic scope, implementation `4d5f21b7371201780248bfa374bc1d7e4fecfb97`. The owner-reported prior RUN-01 refusal cause remains UNKNOWN; no credential values, live account state or permission readiness were inspected. RUN-01 remains BLOCKED / LIVE_PROOF_PENDING.

The existing generic refusal swallowed credential, request, inventory and local reconciliation failures. First-boot preflight now publishes a fixed allow-listed reason in `facts.protection.reasons`, propagated to the top-level preflight reasons. Every failure remains FAIL, `allow=false`, `trading_authority=false`; no diagnostic permits startup or trades. Existing valid-receipt admission is preserved.

Codes:

- `first_boot_static_facts_not_pass`
- `first_boot_unexpected_failure`
- `local_execution_ledger_not_clear`
- `read_credentials_missing`
- `read_credentials_separation_violation`
- `read_credentials_unusable`
- `snapshot_stale_or_unverifiable`
- `venue_authentication_or_permission_failure`
- `venue_orders_nonzero`
- `venue_positions_nonzero`
- `venue_response_malformed`
- `venue_timeout_or_network_failure`

Credential missing/separation failures use typed RuntimeError subclasses retaining their existing messages and rejection rules. Read credentials must differ from the trading key AND secret; unusable non-string/blank/whitespace-padded values are refused before transport. Authentication and permission errors share a category, as do network/timeouts. Response shape/quantity UNKNOWN is distinguished from explicit nonzero positions/orders. A positive algo listing total remains a refusal. Unknown exception types map to one fixed unexpected code.

No exception text or class name, raw response body, symbols/order payloads, signature, headers or credential material enters the diagnostic. Secret sentinels in exception messages, custom class names and inventory bodies are absent from serialized preflight/controller results. Missing/malformed local execution tables remain blocking and diagnostic.

37 focused diagnostic cases passed; 598 combined offline regressions passed, including OBS-01/02/04/05, RUN-01, kernel recovery, entry-risk/recovery guards, protection snapshots and GET-only venue transport. All network requests use fake transport with real sockets denied in the new diagnostic/first-boot suites; all controller launches use inert seams. Every tested refusal records zero launch events and no trading authority. Static/protection proof, Phase B freshness/instance binding, deadline, FROZEN/operator hold/recovery enforcement and graceful containment are unchanged.

SEC-01 already owns credential scope and least-privilege access (EVIDENCE_TO_MAP). There is no separately mapped read-permission defect supported by current evidence. Only OBS-05 is changed; SEC-01 is linked for impact and its status/closing condition remain untouched. No additional ID or permission-readiness closure is created.

All 178 tracker closing conditions and all other 177 rows remain unchanged. Prior OBS-05 evidence is retained. STATE/NEXT select only the completed diagnostic correction, without runtime, provider or trading authority. The canonical YAML and optional Markdown/manifest are synchronized. AST Graphify updates ran in an isolated worktree to preserve pre-existing production graph changes; no semantic/provider extraction occurred. The local guidance's provider-backed Claude availability probe was omitted because the current session prohibits provider calls.

No Kernel/Dashboard launch, real venue call or provider call occurred. A separately owner-authorized exact-revision RUN-01 retry can now diagnose a refusal, but no successful startup or account/API readiness is claimed here.
