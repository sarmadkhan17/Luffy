# Dashboard access and usable investigation memory

Date: 2026-09-16. **M1.5, M2.3 and M2.4 accepted** within the evidence below.
Canonical total: 12/55 items; M0 and M1 accepted, M2 remains partial.
This is engineering acceptance. No predictive or economic validation is claimed.

## Owner access: implemented, tested and deployed

The old `DASH_TOKEN=luffy` fallback allowed unauthenticated requests; GraphQL was
outside the API guard. The dashboard now fails closed for absent, blank or default
credentials. A generated non-default password is stored only as `DASH_TOKEN` in the
server's `.env` (mode 0600); no password was printed or retained in evidence.
The configured bind is now **127.0.0.1:8080**. Every page, API, GraphQL and WebSocket
route is guarded. Login issues a signed, HttpOnly, SameSite=Strict 12-hour session;
unsafe browser requests and WebSocket connections require the same origin.
HTTPS cookies are Secure. Access logging is disabled to avoid recording legacy
read-only query-token clients. New owner access uses the login form, not a URL token.

On this server, open `http://127.0.0.1:8080`. For access from another computer, run:

```bash
ssh -N -L 8080:127.0.0.1:8080 sarmad@<server-address>
```

Then open `http://127.0.0.1:8080` on that computer and enter the password from the
server's `.env` using a trusted local editor. Do not paste it into commands, URLs,
reports or chat. This preserves remote owner access over SSH rather than exposing
plaintext dashboard login on all interfaces. Changing `DASH_TOKEN` and restarting
the dashboard invalidates sessions. There is no unauthenticated fallback.

Live refusal checks returned HTTP 401 for the page, investigation/attention APIs
and GraphQL. Headless Chromium signed in through the form, fetched authenticated
API and GraphQL responses, received a WebSocket message, and rendered both actual
BR/USDT and FIL/USDT dossiers, WAIT actions and missing inputs without JavaScript
errors. [Browser evidence](../artifacts/memory/2026-09-16-browser.json),
[rendered dossiers](../artifacts/memory/2026-09-16-authenticated-dossiers.png),
[visible text](../artifacts/memory/2026-09-16-authenticated-dossiers.txt).
The earlier synthetic M1 walkthroughs remain unchanged and supplement this live
access evidence. Both dashboard restarts preserved the prior watchdog pause state.

## Memory: implemented and tested; runtime adapter deployed

[Pure memory](../../../trader/cognition/memory.py) and
[persistence adapter](../../../trader/observability/memory.py) connect resolved
investigations to later investigations through the existing bounded worker.
They do not change the frozen investigation.v2 measurement or the original price
forecast protocol. Memory is a separately versioned `investigation-memory.v1` record.

Before importing a measured case, the adapter reconstructs its original registration
from exact retained baseline versions, reproduces its frozen measurement and terminal
outcome from exact target versions, and checks its assessment and chronology.
The record distinguishes this outcome as a **non-economic observation**. It retains
source case/update IDs, registration, resolution, availability and actual memory
recording times, schema/catalog/config, trigger/sign, context, measurement and evidence.
Failed replay produces structured refusal diagnostics and degraded worker health.

Retrieval is frozen when a **new** investigation registers. A case must have resolved,
be available, and have been recorded in memory strictly before that registration.
The stable matcher requires compatible protocol/config, trigger/sign and contradiction
context; relative-move cases additionally require the same frozen cohort. It orders
by availability and ID, takes at most three cases, and records inclusion/exclusion
reasons for at most 256 retained candidates. This is a conservative matching rule,
not a learned similarity function or a claim of equivalent regimes.

The new investigation records a caution and a concrete counter-test, with each later
update storing both its original next action and the action's evidence request with
memory. A prior normalization/reversal adds a caution against assuming continuation
and explicitly requests comparison with that prior path. Earlier persistence instead
adds a normalization counter-test. Action kind, frozen target window, assessments,
probabilities and trading authority are unchanged. The owner panel shows the changed
request and the exact prior cases and retrieval reasons behind it.

The [synthetic chronological walkthrough](../artifacts/memory/2026-09-16-memory-paired-walkthrough.json)
contains complete source cases, updates, retained inputs and later dossiers. One
later investigation has a changed request; its no-memory comparator is retained in
the same record. Tests also demonstrate future resolution, delayed import/availability,
wrong versions/types/context, tampered measurements and missing versions being refused;
restart/JSON replay preserves the result. A changed request is not evidence of superior
prediction. No LLM or network call is needed for this memory path.

The worker has run successfully on the deployed source: two active original cases,
zero measured memory cases, zero memory refusals. It retains all existing investigation
bounds, deadline and transaction rollback; memory lives in the same 32 MiB main DB
allocation. At most 32 measured imports are attempted per invocation from a bounded
256-case scan. Memory is deleted with its retained source/target investigation.
Matched records are embedded in the target's frozen context; complete raw source
inputs are not retained indefinitely after source expiry. This is **partial M2.5**,
not acceptance of all long-term memory/export requirements.

Original BR/FIL cases say `pre_memory_registration`; they are not retroactively
rewritten or supplied with later knowledge. No naturally resolved case has yet
changed a live later investigation. That forward observation remains pending.

## Final-source verification and operational facts

**183 passed in 8.66 seconds**, covering authentication, investigation memory,
market investigation, consumer/view, attention learning/view/telemetry, cognition
contracts/attention/replay, and the single strategy-creation boundary. `git diff
--check` and watchdog shell syntax also passed. This was a targeted suite, not the
whole repository. [Source hashes](../artifacts/memory/2026-09-16-source-manifest.json).
An initial memory round-trip test exposed tuple/list differences in nested records;
canonical JSON storage fixed it before the final run.

At **18:13:31 UTC**, all **16** original forecasts remained pending, with **0 matured**:
2 selected and 14 ignored. All registration fields, protocol IDs, exact target keys,
frozen baseline closes and all six retained input versions per forecast joined to
their original scans. First target close remains **September 17 00:00 UTC**.
There are no actual outcome versions or frozen outcome measurements yet to verify.
M2.1 therefore remains unchecked. Never substitute nearby bars when outcomes arrive.

The two original investigations retain exactly their previous registrations and
initial updates. Their frozen baseline measurements reconstruct from retained input
versions. Windows close **September 17 16:00 UTC**. The existing catalog ID remains
`catalog_9777745b96669be5`. Exact IDs, target keys, versions and registration times
are in the [runtime/maturity snapshot](../artifacts/memory/2026-09-16-runtime-and-maturity.json).

Runtime remains **FROZEN**, with MacroGuard logging Federal Funds Rate protection
until 20:00 UTC, and three open legacy-strategy journal trades. Demo is true;
`research.referee=false` and `research.handoff=false`. Watchdog is unpaused; the
investigation worker stays enabled. The kernel was not restarted, no operator or
safety control was changed, and no orders or venue stops were changed. Journal
status is not a new venue reconciliation. No Gate 2 evaluation or admission ran.

## Tools and troubleshooting

Claude CLI still returned a session-limit refusal. Direct implementation proceeded
under the owner's existing authorization. The sandbox failed before execution with
`bwrap: loopback: Failed RTM_NEWADDR: Operation not permitted`; approved escalation
was used. [Structured events](../artifacts/memory/2026-09-16-troubleshooting.jsonl).

At the owner's request, `/home/sarmad/cryptobot_v5` was inspected read-only for
Llama/Groq. No active Llama/Groq adapter or matching `.env` credential entry was
found. Groq mentions in its coordinator/dashboard are historical comments; the
current `bot/agents/llm_reasoning.py` adapter uses DeepSeek. No provider request,
credential transfer, configuration change or model-switch claim was made.

## Next exact task

**M2.2: add typed adapters for selected/ignored price-forecast outcomes and
trade/skip/missed-opportunity records**, keeping observed price movement, simulated
counterfactuals and adequately attributed actual execution P&L separate. Missing
accounting must remain unknown, not estimated actual P&L. Preserve source versions
and actual knowledge/ingestion clocks. Extend retrieval only with explicit
compatibility rules; current retrieval accepts measured investigation observations.
Then finish M2.5 replay/export/retention and distinct regime/pattern/cross-market links.

In parallel, perform M2.1 exact outcome verification once the registered windows
actually mature; preserve explicit missing-data retries and terminal immutability.
Waiting does not block typed adapters. M2.2 and M2.5 remain partial, not accepted.
No new strategy was produced or admitted, existing trading decisions still use
legacy strategies, and no better strategy, profitability or calibration has been
established.


## Continuation delivered

The next-task section above is the earlier handoff. The subsequent
[typed outcome delivery](2026-09-16-typed-outcome-memory-delivery.md) implements
forecast/trade/skip/missed-opportunity adapters and raw retention/export/replay,
with 205 final-source tests and deployed forward skip imports. Remaining work
is recorded in the canonical checklist; the original acceptance evidence above
is preserved.
