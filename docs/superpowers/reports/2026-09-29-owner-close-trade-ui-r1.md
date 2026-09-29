# LUFFY-OWNER-CLOSE-TRADE-UI-R1

> **STATUS: DEFERRED / SAFETY_BLOCKED / NOT_DEPLOYABLE** (parked 2026-09-29)
>
> **Astra's verdict: BLOCK.** Owner decision: park this package. Do not fix
> `Executor.close` here and do not expose Close Trade in the React UI.
>
> **Execution blocker.** It is recorded here and will be addressed in
> **LUFFY-PER-TRADE-EXIT-ATTRIBUTION-R1**. The existing `close_trade`
> execution cannot safely preserve per-trade attribution in one case:
> - several journal trades share one venue position (one net position per symbol), and
> - one of those trades has already partly exited.
>
> The cause is in `Executor.close`. It sends a reduce-only market order for
> the named trade's journal `amount` against the symbol's net venue position.
> It then books the fill, and any residual or venue-window P&L, to that one
> trade. If a sibling trade on the same symbol has partly exited, the order
> can consume quantity that belongs to the sibling and the booked P&L can be
> mis-attributed. Section I items 1–2 below described these properties as
> non-blocking; Astra's review overrules that, and the block stands. This is
> an execution-layer problem, so the UI cannot fix it.
>
> **Frontend idempotency defects.** Astra found two in this package's
> frontend. Per the owner decision they are **not corrected**, because the
> feature is deferred. They must be fixed before this UI is revived. As
> recorded in Astra's review (supplied 2026-09-29). The bold sentences are
> Astra's findings. The explanation after each is the implementer's reading
> of the preserved patch, not Astra's wording.
>
> 1. **A persistence failure can allow an unresolved attempt to be retried
>    with a new request ID after refresh.** When browser storage cannot hold
>    the pending entry, the request id lives only in memory. After a refresh
>    that id is gone, and a new Close Trade creates a new id for the same
>    intended close. Relevant code in the preserved patch:
>    `pending.create` falls back to memory, and `useCloseTrades.submit` then
>    shows only a warning.
> 2. **A stale preflight that survives navigation/remount can emit a second
>    distinct request ID for the same trade.** The in-flight guard
>    (`busy` ref) and the per-trade state live in the Trade book. After
>    navigating away and back, or a remount, a preflight that was still
>    running can finish and create a request id while a new mount has already
>    started its own close for the same trade. The result is two distinct
>    request ids for one intended close.
>
> For both, the kernel's existing de-duplication (`ALREADY_SET` while a close
> is queued, `trade_not_open` once closed) limits the effect at execution. It
> does not satisfy the package's requirement of one request id per intended
> close.
>
> **What was parked:**
> - The React Close Trade feature was removed from the active frontend candidate. The candidate's frontend source, fixtures and tests match base `4fbefa2` again.
> - No Close Trade button is rendered.
> - The LIVE adapter has no `closeTrade` action.
> - No React source sends the `close_trade` mutation.
> - Regression guards: `frontend/tests/closeTradeDeferred.test.tsx` and `frontend/tests/browser/close-trade-deferred.spec.ts`.
> - The full R1 implementation, including its tests and fixture changes, is kept as a patch that applies cleanly to `4fbefa2` (`git apply --check` passes): [`2026-09-29-owner-close-trade-ui-r1-evidence/close-trade-ui-r1.patch`](2026-09-29-owner-close-trade-ui-r1-evidence/close-trade-ui-r1.patch).
>
> **Unchanged:**
> - the kernel Owner Interface `close_trade` command, its GraphQL mutation and the legacy dashboard's close button (pre-existing architecture, addressed separately);
> - `Executor.close`;
> - Owner Interface semantics;
> - Protection Snapshot;
> - Trade History;
> - production.
>
> **Graphify:** the tracked `graphify-out/` files that R1's `graphify update .`
> rewrote were restored to HEAD.
> - Untracked graphify cache/snapshot files were left in place. Some of them predate that run, so they were not deleted on a guess.
> - `graphify-out/cache/last_query_stamp` was already modified before R1 and is left as it is.
>
> Everything below is the original R1 report, kept for the record.

Date: 2026-09-29. Implementer: Opus (Claude Code). Reviewer next: Astra.
Base: `4fbefa22263dbc938199ace30cb12e52b472efc2` (`owner-frontend-live-binding-v1`),
worktree `.claude/worktrees/owner-frontend-live-binding`. Uncommitted.

This package is a UI binding only. It adds no execution path and changes no
kernel, Owner Interface, Protection Snapshot or Trade History code.

**Not done:** no commit; no deploy; no restart; no watchdog change; no Binance
or other venue access; no production data read or written.

**Done beyond the code:** `graphify update .` was run, as CLAUDE.md requires after
code changes. It is AST-only and refreshes the files under `graphify-out/`.

## A. Existing control contract used

The existing typed Owner Interface intent `close_trade`, unchanged:

| Layer | Existing code |
|---|---|
| Wire contract | `trader/owner/contract.py`: `INTENT_OPERATIONS` includes `close_trade`. `args` must be exactly `{"trade_id": str}` matching `[A-Za-z0-9._:-]{1,64}`. |
| HTTP binding | `trader/api/graphql_schema.py`: the `close_trade(trade_id, request_id, issued_at_ms)` mutation calls `submit(...)`, which calls the dashboard gateway. |
| Gateway | `trader/dashboard/server.py::_owner_gateway` → `owner/adapters/dashboard.to_request`. It requires an authenticated session and builds request id `dashboard-<client id>` with identity `session`. It sends the request over authenticated IPC with `OwnerClient`. |
| Executor | `trader/owner/service.py::_close_trade`, in the kernel. In one transaction it checks the trade with that **exact** `trades.id` has `status='open'`, otherwise `REFUSED trade_not_open`. It appends the id once to `state_kv.close_requests`; if the id is already queued it returns `ALREADY_SET`. It records a `manual_close` control event. |
| Execution | `kernel.py::_drain_close_requests`, at the top of every cycle. It reads and clears the queue, skips ids that are no longer open, and calls `Executor.close(trade)`. That sends a reduce-only market order for that trade's journal amount and books the actual fills. |

**Contract sufficiency:** the command names one immutable journal primary key,
not a symbol. The kernel re-checks that exact id at intake and again at drain,
so the contract can identify one exact trade. No contract gap was found and
no workaround was added.

**FROZEN / HALTED:** `_close_trade` does not check the control state.
`Kernel.run` calls `cycle()` in every state, and `cycle()` drains close requests
before any state gate. So under the existing contract an owner close is queued
and executed in ACTIVE, FROZEN and HALTED alike. A close only reduces
exposure. The UI states this and shows the current control state in the
confirmation. It does not reinterpret the rule. A browser test checks the
HALTED case.

## B. Exact request flow

```
Trades → Trade book row → "Inspect <symbol>" (record drawer)
  → Close Trade (open trades only, Owner Interface AVAILABLE)
  → confirmation dialog → "Send close to LUFFY"
  → [freshness] GET /owner-api/v1/trades?status=open&limit=200   (read-only)
  → POST /graphql  mutation close_trade(trade_id, request_id, issued_at_ms)
  → auth Guard (session cookie + same-origin) → gateway → dashboard.to_request
  → OwnerClient IPC → kernel OwnerService.execute → reserve/claim/_close_trade
  → OwnerResult → GraphQL → UI shows the result → trades/overview/health re-read
  → kernel next cycle: _drain_close_requests → Executor.close (existing Risk/Exec)
```

The browser never:
- contacts a venue (unit test: `src/` contains no venue host or order API; browser test: no request left the app's origin during a close);
- writes the journal;
- changes local state to show the trade as closed.

## C. Trade identity / freshness protection

- **Identity:** `trade_id` is the journal `trades.id`, taken from the row the owner is inspecting. The symbol is never sent. A pytest opens two trades on the same symbol through the real route, IPC and `OwnerService`. Only the named id is queued.
- **Freshness before send:** when the owner confirms a new close, the UI first re-reads the open trade book. The trade must still be present with the same `status=open`, `symbol`, `side` and `amount` as in the confirmation dialog. If not, nothing is sent and no request id is created. The trade book is refreshed and the reason is shown:
  - `not_open`: no longer open;
  - `changed`: symbol, side or quantity changed (the new values are shown);
  - `unverifiable`: more than 200 open trades;
  - `read_failed`: the read failed.
- **Kernel check:** the kernel checks again, authoritatively, that the id is open (`trade_not_open`). It checks again when it drains the queue.
- **Protection Snapshot is not used for eligibility.** Eligibility follows the existing contract: journal `status='open'`, plus an AVAILABLE Owner Interface health read so that nothing is sent to an unreachable kernel.
- **After a refresh:** the panel stays inside the drawer. If the row has become closed, the answer and any unresolved request remain visible, but Close Trade is removed.

## D. Confirmation UX

- **Close Trade** appears only in the record drawer of a row with `status=open`. Table rows have no close button, and there is no bulk close.
- The confirmation dialog shows:
  - symbol, side, open quantity (journal), trade ID and current control state;
  - the statement "This sends a real owner control to LUFFY. The kernel checks the trade is still open and market-closes this one trade with a reduce-only order on its next cycle";
  - a note that the kernel queues an owner close in any control state.
- **Cancel** (button, ✕ or Escape) sends nothing, creates no request id and does not re-read the book.
- **While pending:** the button is disabled and reads "Checking trade…" and then "Close pending…". A status line reads "waiting for its answer".
- **Result:** the Owner Interface status, disposition, kernel message, reasons, request id and audit event ids.
  - `ACCEPTED` reads "It is NOT closed yet … shows it closed only after the kernel books the exit".
  - The trade, overview and health queries are refreshed. The row stays `open` until the journal says otherwise.

## E. Idempotency behavior

- Each intended close gets one browser request id (`crypto.randomUUID`) and issue time. They are stored under `luffy.owner.req.<id>` with action `close_trade:<trade id>`. This is the same protocol and key the legacy dashboard uses (`index.html::ownerSubmit`), so an unresolved request from either UI is found and reused.
- The id is released only on a definitive status: `ACCEPTED`, `ALREADY_SET` or `REFUSED`.
  - It is kept on `UNAVAILABLE`, `IN_PROGRESS`, `OUTCOME_UNKNOWN`, no answer, timeout and session expiry.
  - While it is kept, the drawer shows **Retry close (same request)**. Retry re-sends the same id and the original issue time, so the kernel judges the age of the original click.
  - Retry needs no new confirmation because it cannot create a new action; this matches Operations and the legacy dashboard.
- **Double submit:**
  - a per-trade guard is set synchronously before the first await;
  - the button is disabled while a request is in flight;
  - the confirmation dialog closes on the first click.
- **Kernel side (existing):**
  - an exact replay of a request id returns the recorded result with `replayed: true` and the same audit event ids;
  - the same id with a different trade is `REFUSED request_id_conflict`;
  - a new id for an already-queued trade is `ALREADY_SET` and queues nothing.

  The pytest `test_close_trade_ui_binding_exact_id_and_replay` exercises all three through the real route, IPC and service.
- **Browser replay test:** the answer to the retry is dropped after the server executed it. The next retry with the same id returns the recorded result ("recorded result (replay)"). Across three sends the queue holds `["t-btc"]` exactly once.

## F. Failure semantics

| Case | UI behaviour | Request id |
|---|---|---|
| Owner Interface health UNAVAILABLE / read failed | Close Trade disabled with the reason; nothing sent | — |
| Kernel unavailable on send (`UNAVAILABLE`) | "Not delivered … Nothing changed." | kept; retry same id |
| FROZEN / HALTED | queued per existing contract; state shown in confirmation | — |
| Already closed before send | "no longer open … Nothing was sent", book refreshed | none created |
| Changed since display | "changed since it was displayed … Nothing was sent", new values shown | none created |
| Kernel finds it closed (`REFUSED trade_not_open`) | "Refused by the kernel: this trade is not open (already closed)" | released |
| Other refusal (`REFUSED`, e.g. `operation_not_permitted`, `request_stale`) | "Refused by the kernel. It was not executed and will not execute." | released |
| `IN_PROGRESS` | "still processing … Retry sends the same request id" | kept |
| `OUTCOME_UNKNOWN` (e.g. IPC timeout) | "may or may not have been queued. Do not assume either." | kept |
| No answer: transport loss, 150 s client timeout, GraphQL error, answer for another operation | "No answer — outcome unknown" | kept |
| Session expired (401) | app signs out; after sign-in the drawer shows the unresolved request and Retry | kept |
| Duplicate / replay | recorded result shown as "recorded result (replay)" | released |

A timeout is never shown as success or failure. The 150 s client bound is
longer than the dashboard's 120 s kernel control timeout, so the server
normally answers `OUTCOME_UNKNOWN` first.

**Late responses:** per-trade state (in-flight flag, outcome) lives in the
Trade book, above the record drawer, and is keyed by trade id. An answer
arriving after the owner switched to another trade is stored for its own
trade. It shows when that trade is reopened and never in the drawer on screen.

## G. Tests / results

| Suite | Result |
|---|---|
| `npm run typecheck` | pass |
| `npx vitest run` (all) | **97 passed** (10 files); new `tests/closeTrade.test.tsx`: 21 |
| `npx playwright test` (all, LIVE + DEMO + harness) | **89 passed**; new `tests/browser/close-trade.spec.ts`: 10 |
| pytest owner / dashboard / control / supervisor / protection / trade-history set (20 files) | **733 passed** |
| pytest `test_owner_interface.py test_owner_interface_r3.py test_manual_close.py test_owner_frontend_api.py test_owner_dashboard_js.py test_owner_dashboard_browser.py test_single_creation_path.py` | 169 passed |

**New unit tests (`closeTrade.test.tsx`):**
- open shows Close Trade, closed does not, and no row-level close;
- confirmation fields and "real owner control" statement;
- cancel sends nothing;
- exact trade id submitted, once;
- duplicate click prevented while pending;
- authoritative ACCEPTED without an optimistic close;
- refusal;
- `trade_not_open`;
- health unavailable disables the button;
- UNAVAILABLE on send, then same-id retry with the same issue time;
- no-longer-open, changed and unverifiable re-reads send nothing;
- no answer → OUTCOME_UNKNOWN → replay, with the same id throughout;
- session expiry keeps the id;
- a late answer lands on its own trade only;
- adapter: posts only the typed mutation to same-origin `/graphql`; transport failure, GraphQL error and wrong-operation answer each give "unknown"; 401 is SessionExpired;
- `src/` contains no venue endpoint or order API.

**New browser tests (`close-trade.spec.ts`, real FastAPI app and auth Guard):**
- confirmation and cancel;
- exact id with the slow kernel, double click, no off-origin request, row stays open;
- HALTED still queues;
- refusal;
- kernel down → disabled; down at send → UNAVAILABLE → same-id retry;
- closed since display (not sent; book refreshes; answer stays; Close Trade removed);
- kernel-side `trade_not_open` race;
- OUTCOME_UNKNOWN → lost answer after execution → exact replay;
- session expiry at send → sign-in → same-id retry;
- late answer vs. the selected trade.

**Test fixture scope:** the browser server's fake gateway now implements
`close_trade` with `OwnerService._close_trade`'s rules: exact open id, queued
once, `ALREADY_SET`/`trade_not_open`. It adds two modes, `refuse` and
`race_close`. The real kernel service is covered by the new pytest over real
IPC.

**Evidence files:** the full Playwright run rewrites screenshots and
performance JSON under `frontend/evidence/`, which already had uncommitted
changes in this worktree. The directory was copied before the run and restored
byte-for-byte afterwards (`diff -rq` clean). Raw results for this package are in
the job scratch directory, not in `evidence/`.

## H. Exact changed files

New:
- `frontend/src/views/CloseTrade.tsx`
- `frontend/tests/closeTrade.test.tsx`
- `frontend/tests/browser/close-trade.spec.ts`
- `docs/superpowers/reports/2026-09-29-owner-close-trade-ui-r1.md`

Modified:
- `frontend/src/adapters/contracts.ts`: optional `OwnerAdapter.closeTrade`
- `frontend/src/adapters/live.ts`: `CLOSE_TRADE_MUTATION`, `CLOSE_TRADE_TIMEOUT_MS`, `closeTrade()`
- `frontend/src/views/Routes.tsx`: `useCloseTrades()` in TradeBook; `<CloseTradePanel>` in the record drawer
- `frontend/src/views/Operations.tsx`: the note now points to Trades → Inspect → Close Trade
- `frontend/src/live.css`: `.close-trade` spacing
- `tests/owner_frontend_fixture.py`: fake gateway `close_trade`, call args recorded, `refuse`/`race_close` modes
- `tests/owner_frontend_server.py`: `/__test__/calls` exposes `close_requests`
- `tests/test_owner_interface.py`: `test_close_trade_ui_binding_exact_id_and_replay`

Tool side effect: `graphify-out/*`, from `graphify update .`.

No backend production code changed.

## I. Remaining limitations (not blockers)

1. **Freshness is checked at send, not at execution.** The kernel contract carries only `trade_id`, not the side or quantity the owner saw. If the trade partly exits on the venue between the send and the kernel's next cycle, the kernel closes whatever remains of that trade (reduce-only, by id). That matches the owner's intent, "close this trade", but the confirmed quantity is not enforced by the kernel. Enforcing it would need a contract change: an expected amount/side argument.
2. **One venue position per symbol.** When two journal trades share a symbol, the venue holds one net position. `Executor.close` sends reduce-only for the named trade's journal amount and books that trade only. This is existing execution behaviour and unchanged.
3. **Unresolvable ids.** An id whose outcome became `outcome_unknown_after_restart` stays unresolved in the kernel's record. The drawer keeps showing Retry for it until retention expires. The owner must judge from the trade book whether the close happened.
4. **Legacy UI.** The legacy dashboard shares the same pending-request storage. An unresolved legacy close for a trade appears here as Retry, and the reverse.
5. **Browser tests use a fake gateway.** The kernel-side semantics in the browser tests come from the fixture gateway. The real `OwnerService` is covered by pytest over real IPC only.

## J. PACKAGE_STATUS

**READY_FOR_REVIEW**
