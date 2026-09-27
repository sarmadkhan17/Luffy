# LUFFY-DASHBOARD-DEPLOYMENT-PREP-V1 — deployment/migration plan

**Status: PLAN ONLY. NOT_DEPLOYED. Amendment 1 applied (G6, rollback, test count, gate classes, H1 provenance, /api/summary consumers).** Nothing in `/home/sarmad/trader` was
modified, no process was stopped or restarted, the watchdog was not touched,
nothing was merged, cherry-picked to `main` or committed. The health deployment
candidate was not touched (only its file list was compared read-only).

Source: `/home/sarmad/trader-dashboard-integration`, branch
`dashboard-integration-v1`, HEAD `875898e` (verified, clean), integrated
implementation `6d78be9`. Implementer: Opus. Reviewer: Astra.

## Verdict

| Slice | Verdict |
| --- | --- |
| Backend + legacy UI (chat safety, local-first Overview, `/api/v2/summary`, v1 → 410) | **READY_FOR_DEPLOYMENT_PREP** — deployable later as a dashboard-only restart, **only** via the composite below |
| New owner frontend (`frontend/`) | **Not deployable as an owner UI.** Fixture-only `mode: 'DEMO'`; nothing serves it. **READY_FOR_BINDING** |
| Deploying `6d78be9` / `875898e` wholesale (branch checkout/merge) | **NO-GO** — carries 133 unrelated commits including kernel/engine/journal/config |

---

## 1. Production topology (read-only, 2026-09-27 ~17:10 +0300)

| Item | Observed |
| --- | --- |
| Checkout | `/home/sarmad/trader`, branch `main`, HEAD `d46d045` (= `origin/main`) |
| Dirty tracked | `docs/superpowers/reports/2026-09-20-donchian-retirement-attribution-audit.md`, `knowledge/20 Strategies/Donchian_Breakout_Trail.md`, `knowledge/MOC.md`. No untracked files. None overlap the deployment delta. |
| Kernel | PID 418802, `./venv/bin/python -m trader.kernel`, cwd `/home/sarmad/trader`, parent `systemd --user` (PID 1709; restart.sh `setsid nohup`). `LUFFY BOOT` 2026-09-18 05:29:59 (`/tmp/opencode/luffy_kernel.log`). |
| Kernel children | `multiprocessing` resource tracker (419369) and short-lived **spawn** children (`trader/core/child.py`, used by `trader/research/job.py`). Spawn children re-import modules **from disk**. |
| Dashboard | PID 418811, `./venv/bin/python -m trader.dashboard.server`, same cwd/parent, started 1 s after the kernel (same boot). Log `/tmp/opencode/luffy_dash.log`. |
| Port | Dashboard only: `192.168.126.131:8080` (host-only VMware NIC; `config.yaml: dashboard.host/port`). Uvicorn, `access_log=False`. |
| Live clients | 4 established connections at inspection time, all from `192.168.126.1` (the VM host — presumably the owner's browser). Access logging is off, so historical clients are not observable. `/api/summary` consumers: see §3 (in-repo listed; external **UNKNOWN**). |
| Auth | `trader/dashboard/auth.py` `DashboardAuth(DASH_TOKEN)` ASGI middleware on **every** route incl. WS: `x-luffy-token` header, GET `?token=` (legacy), or HMAC-signed `HttpOnly` session cookie from `POST /auth/login`; same-origin check on cookie writes. `.env` `DASH_TOKEN` is set (non-default) — value not printed. Unauthenticated probe: `/`, `/api/overview`, `/api/summary`, `/api/v2/summary`, `/api/org` → **401** (production does not serve `/api/overview`/`v2` yet; the 401 precedes routing). `auth.py` and `trader/api/` are **unchanged** by the integration. |
| Legacy UI | `trader/dashboard/web/index.html` (+ `attention.js`, `investigation.js`), served by explicit `GET /`, `/attention.js`, `/investigation.js` routes, read per request, `no-store`. No `StaticFiles` mount; FastAPI does **not** serve any other static directory. |
| New frontend | Not served anywhere in production. In the preview it is `vite preview` on `127.0.0.1:4173` (loopback of the VM only — not reachable from the owner's host browser). `frontend/dist/` is gitignored and absent. |
| Watchdog | Cron `*/5` + `@reboot` → `scripts/watchdog.sh`. No `data/watchdog.off`. Starts kernel if absent / restarts it if heartbeat > 600 s; starts dashboard if absent; runs one-shots from the **on-disk** checkout every 5 min: `observability.learning` always, and `declared`, `investigation`, `accounting` (all three `.enabled` flags present). All restarts go through `restart.sh` (`kill -9` by module name, then `setsid nohup`). |
| Control state | `state_kv.control_state = ACTIVE` (read-only query). |

### Running code ≠ on-disk HEAD (pre-existing drift)

*Corrected in amendment 1 (the first draft named `e344a65`; that lookup
resolved the reflog entry preceding the boot).* The `main` reflog reached
**`51101d0`** (fast-forward of `fix/declared-population-20260918`) at
**05:29:57**; `LUFFY BOOT` was logged at **05:29:59**. `51101d0` is therefore
the best historical baseline for the running kernel and dashboard. The exact
loaded runtime remains **UNKNOWN** (a dirty tree at boot cannot be excluded,
and `/proc` cannot prove it).

`51101d0 → d46d045` changes **24 runtime files, +3,918/−96**, including
`kernel.py`, `engine/supervisor.py`, `engine/protective.py`,
`engine/reconcile.py`, and 2 lines of `dashboard/server.py` (RECOVERY shown
as alert). The other dashboard/chat runtime files are byte-identical between
`51101d0` and `d46d045`.

Consequences for this package:

- Any kernel restart for any reason (watchdog stale-heartbeat, reboot, operator)
  activates the on-disk `d46d045` kernel code, independent of this dashboard
  package. **H1: URGENT INDEPENDENT RISK — not a blocker to the proven
  dashboard-only restart; not solved in this package.**
- The live DB already contains `d46d045`'s `trades.mfe_r/mae_r/excursion_json`
  columns, so a dashboard restart on the composite runs no new schema work.
- Rollback targets committed `d46d045`, not the reconstructed running code;
  the only dashboard/chat difference from `51101d0` is the 2-line RECOVERY label.

### Current production chat authority (safety finding)

The running `/api/chat` calls `ChatEngine(...).handle(msg, history)` with the
old default `do_ops=True`: phrase-matched **freeze / halt / resume / close-all /
panic are executable from chat today** (authenticated, same-origin). The
integration sets `do_ops=False` at both the default and the call site; no other
caller of `ChatEngine` exists. Deploying the backend slice removes chat
operational authority — a risk reduction, not an expansion.

## 2. Exact deployment delta

`d46d045..6d78be9` = 134 commits / 263 files. Only the **last** commit is the
dashboard integration; its parent `1eb00e1` (`world-model-foundation`) is 133
commits ahead of production.

| Class | Files | Content |
| --- | ---: | --- |
| **A. Runtime (dashboard/chat)** | 8 | `trader/chat/{agent,engine}.py`; `trader/dashboard/{server,enrichment(new),local_view(new)}.py`; `trader/dashboard/web/{index.html,attention.js,investigation.js}` |
| **B. New frontend** | 69 | `frontend/**` source, lockfile, tests, evidence PNG/JSON, `.skills/`. No build output. |
| **C. Tests / reports** | 6 + 28 | `tests/test_{chat_control_separation,dashboard_client_browser,dashboard_integration,dashboard_performance,attention_view,investigation_view}.py`; `docs/superpowers/reports/2026-09-27-dashboard-performance-v1*` |
| **D. Control plane / docs** | 2 | `STATE.yaml`, `NEXT.yaml` — only in `875898e`; **excluded** (cherry-pick `6d78be9` only) |
| **E. Unrelated to this package** | 152 files / 133 commits | 75 `trader/` files incl. `kernel.py` (+204), `engine/{orchestrator,risk,executor,control_fence,recovery}.py`, `core/journal.py` (+1211, schema), `core/types.py`, `research/evaluate.py`, `strategy/*`, `brain/analyst.py`, 30+ new `cognition/`, `observability/`, `world/` modules; `config.yaml` (+20); deletes `CLAUDE.md` and `.claude/CLAUDE.md`, rewrites `AGENTS.md` |

**Deployment basis: composite = `d46d045` + `6d78be9`'s own diff.**
`git merge-tree --write-tree --merge-base=1eb00e1 d46d045 6d78be9` (in a
disposable shared clone under the job tmp; no writes to the production repo):

- clean, **0 conflicts**; composite tree **`daae20a82d8c183cca0192cad70d5366e548576c`**;
- composite diff vs `d46d045` is exactly the same 111 paths as `1eb00e1..6d78be9`;
- the integration runs standalone on production code: on the composite,
  `test_dashboard_integration`, `test_chat_control_separation`,
  `test_dashboard_performance`, `test_attention_view`, `test_investigation_view`
  → **155 passed**; widened to all dashboard/chat/auth/company/control tests
  (+ `test_dashboard_client_browser`, `test_dashboard_auth`,
  `test_dashboard_pipeline`, `test_chat_agent`, `test_company`,
  `test_control_state_recovery`, `test_entry_control_fence`,
  `test_research_control`). The independently verified result for the
  expanded dashboard/chat/auth/control validation set is **238 passed,
  0 skipped** (review-verified; this is the relevant evidence). *The first
  draft's 192 was my narrower file selection, superseded.*
- Isolated G6 pre-check (amendment 1, see §7 G6): **36 passed** on the
  composite (tree `daae20a8…`, identical to the deployment tree) — 4 exact
  phrases × 4 control states × {engine default, `/api/chat`}, plus a 4-case
  negative control proving the probes detect mutation when `do_ops=True`.
  Harness lived only in the disposable clone; it is not committed.

Runtime blob IDs (for pre/post verification):

| Path | best running baseline (`51101d0`; loaded code UNKNOWN) | prod HEAD (`d46d045`) | composite |
| --- | --- | --- | --- |
| `trader/chat/agent.py` | `2aae08d48dea` | `2aae08d48dea` | `c836dbe56345` |
| `trader/chat/engine.py` | `e9e66c3b1bb4` | `e9e66c3b1bb4` | `4fa2f9100c7d` |
| `trader/dashboard/enrichment.py` | absent | absent | `c8bad69ae25e` |
| `trader/dashboard/local_view.py` | absent | absent | `a97c27d3d61a` |
| `trader/dashboard/server.py` | `7a896c87dca0` | `c9bd78df15dc` | `a860cb03df9b` |
| `trader/dashboard/web/attention.js` | `d545989e2823` | `d545989e2823` | `27529918488a` |
| `trader/dashboard/web/index.html` | `7c3d9ddfb504` | `7c3d9ddfb504` | `d569fce65a6b` |
| `trader/dashboard/web/investigation.js` | `2457f79b8d5f` | `2457f79b8d5f` | `833534e261a1` |

**Process boundary:** static frontend addition — none (not served); server +
legacy UI update — **dashboard process restart only**; kernel restart —
**not required**. Proof in §5.

Health candidate (`health-observation-prod-candidate`): zero file overlap with
the 111-path delta; ordering between the two is independent.

## 3. Migration design

Smallest safe migration, in two separately authorized stages.

### Stage 1 — backend + legacy UI (deployable after authorization)

1. Create the deploy commit on `main` by cherry-picking **only** `6d78be9`
   onto `d46d045` (never merge `dashboard-integration-v1` /
   `world-model-foundation`). Its tree must equal `daae20a8…` (gate P1).
2. Fast-forward the production checkout to that commit (`git merge --ff-only`),
   preserving the three dirty docs/knowledge files (no overlap).
3. `./restart.sh dashboard` (kernel untouched).
4. Reload the owner's browser tab(s).

Routing/auth after Stage 1:

- `/` still serves the **legacy** dashboard (updated `index.html`: local-first
  Overview via `/api/overview` + `/api/enrichment`, `/api/v2/summary`, chat
  panel read-only). The legacy UI *is* the owner UI and remains the fallback.
- REST, GraphQL (`/graphql`, unchanged schema and mutations) and `/ws/live`
  (now one shared, off-loop frame per 2.5 s) keep their paths.
- New: `GET /api/overview`, `/api/enrichment`, `/api/v2/summary`.
- `GET /api/summary` → **HTTP 410** `{"error":"gone","use":"/api/v2/summary",
  "migration":…}`. Known in-repo consumers: the legacy page itself (prod
  `d46d045:trader/dashboard/web/index.html:721` and `:1648`), both removed in
  the new `index.html` (tested: no `fetch('/api/summary` in the page), and the
  integration's own tests (`tests/test_dashboard_performance.py`, asserting
  410). **External consumers: UNKNOWN** — access logging is off, and a
  filesystem search can't rule out clients on other machines. **An owner
  tab left open across the restart keeps running old JS**, which polls
  `/api/summary` and would show broken summary tiles until reloaded — hence step 4.
  v2 keeps v1 keys but returns `null` instead of fabricated `0`/`ACTIVE`/`futures`.
  Any external consumer (unknown) would see 410 with an explicit pointer
  (fail-loud, not silently different data).
- Auth unchanged; every new route sits behind the existing middleware.
- Chat: `do_ops=False`; ops phrases get the boundary explanation; no LLM or
  phrase path writes control state.
- Freeze path preserved: Freeze button → `confirm('Set control state to
  FROZEN?')` → typed GraphQL `set_control_state(state:"FROZEN")` → `state_kv`
  + `control_events(actor='dashboard')`; panic → `confirm(...)` → `mutation{panic}`.
  Note: "typed" here means the typed GraphQL mutation behind a browser
  `confirm()`, not a typed-text confirmation field.

### Stage 2 — owner frontend (NOT deployable now)

The frontend cannot truthfully be deployed as the production owner UI: its only
adapter is `mode: 'DEMO'` with fixed fixture scenarios, no backend requests,
no auth awareness. Exposing it to the owner at a production URL would present
fixture equity, positions and health beside a live trading system.

Where it would go once bound (design, not authorized):

- Served **by the existing FastAPI app, same origin**, under `/owner/`
  (explicit routes for `index.html` and a hashed `/owner/assets/*` allow-list,
  `no-store` on HTML) so `DashboardAuth` cookies and the same-origin check apply
  unchanged. No Node process in production; `vite preview` remains dev-only.
- Requires Vite `base: '/owner/'` (currently default `/`, which would collide
  with the legacy `/`).
- `dist/` built from a committed SHA with a recorded sha256 manifest; the
  server serves only manifest files (no directory mount of `frontend/`).
- Legacy `/` stays the default until parity is reviewed; switching the default
  route is its own later decision.
- DEMO builds are never served from the production server. If a DEMO preview is
  ever wanted on production, it needs a non-dismissible DEMO banner and a path
  that cannot be confused with `/owner/` (owner decision; not recommended).

Stage 1 lands `frontend/` source on disk but inert: nothing imports or serves
it, no `dist/`, no `node_modules/` — no fixture leakage path exists (gates P7, S10).

## 4. Production binding gap (minimum before LIVE)

Classification: **AVAIL** = contract exists; **ADAPTER** = thin mapping;
**MISSING** = backend capability absent; **UNSAFE** = not authorized.

| Surface / seam | Need | Backend today | Class |
| --- | --- | --- | --- |
| Overview: equity, balance, prev | value + source time + freshness | `/api/overview.equity` (`journal.equity`, `venue_confirmed:false`, stale threshold) | ADAPTER |
| Overview: equity curve `points` | time series | GraphQL `equity_curve(limit)` | ADAPTER |
| Overview: P&L | realized today / strategy P&L; uPnL | `/api/overview.today/performance`; uPnL estimate in `/api/enrichment` / `v2` (`null` unless all marked) | ADAPTER (must carry estimate label + completeness) |
| Overview: exposure | long/short, completeness | `/api/overview.positions.entry_notional` (entry notional, not marked) | ADAPTER (label basis) |
| Overview: control state | state + freshness | `/api/overview.control` | ADAPTER |
| Overview: positions + **protection** | independently verified protective stop | only `journal.trades` + `has_stop_order_id`, explicitly `venue_verified:false`; no venue algo-order read in dashboard | **MISSING** (read-only venue protection evidence via `engine/protective.py` enumeration) |
| Overview: Needs You | real actionable objects | none | **MISSING** |
| Overview: heartbeat/staleness | kernel liveness | `/api/overview.heartbeat` | ADAPTER |
| LUFFY chat | query-only transport, request/message IDs, cancel, streaming, errors | `POST /api/chat` → `{reply}` text; read-only; no IDs/stream/cancel | ADAPTER for non-streaming text; **MISSING** for cancel/stream/IDs. README forbids binding legacy `/api/chat` by assumption — needs review |
| LUFFY evidence | evidence IDs + provenance per claim | none (reply is prose) | **MISSING** |
| Knowledge graph | bounded nodes/edges, stable IDs, lenses, provenance, timestamps | `/api/vault/graph` (`{nodes,edges}`), `/api/vault/tree`, `/api/vault/file`; unbounded, no lens/provenance/timestamps; code refs not separated | ADAPTER for nodes/edges; **MISSING** pagination/bounds, provenance, source time |
| Live System components | real identities, declared connections | `/api/org` (`org.yaml` roster + per-role status/last_activity); no declared edge list | ADAPTER (nodes); **MISSING** (declared architecture edges) |
| Live System health | last-observed health with time + coverage | `/api/org` `status`/`last_activity`; collector/attention/investigation health files | ADAPTER, must map stale → STALE, missing → UNAVAILABLE |
| Live System events | deduplicated timestamped events bound to edge IDs | `/ws/live` frame (no edge IDs); `control_events`, logs | **MISSING** |
| Session / auth | bootstrap mode, expiry, 401 handling | cookie auth works same-origin; no `/api/session` or mode endpoint; 401 ends legacy session (tested) | **MISSING** (small: authenticated bootstrap returning mode `LIVE`/build SHA) |
| Freshness / disconnect | per-source age, reconnect, dedupe | per-section `freshness`/`age_s`, `cache` meta with `served_at`; WS has no sequence IDs | ADAPTER (REST); **MISSING** (WS resume/dedupe) |
| Controls (freeze/resume/panic/close/approvals) | — | GraphQL mutations exist | **UNSAFE / not authorized** for the new UI |
| Voice / LLM ops | — | — | **UNSAFE / not authorized** |

**Prerequisite package (proposed): `LUFFY-OWNER-FRONTEND-BINDING-V1`** — scope:
(1) authenticated bootstrap/mode endpoint; (2) `LiveAdapter` implementing
Overview (ADAPTER rows only; protection shown as "journal-recorded, not venue
verified" until a separate read-only protection-evidence package), Knowledge
nodes/edges with a hard node cap, Live System nodes/health from `/api/org` with
STALE/UNAVAILABLE mapping and a visible "connections not declared" notice;
(3) chat as read-only non-streaming text via a reviewed contract, cancellation
client-side only, no evidence claims; (4) remove the scenario selector and
fixture adapter from the LIVE build; (5) `base: '/owner/'` and same-origin
FastAPI serving with manifest; (6) tests: no fixture import in LIVE bundle,
401 → session end, stale/missing semantics against real payloads.
Follow-ups (MISSING rows) are separate packages: venue protection evidence,
chat transport with IDs/evidence, knowledge provenance/pagination, system
event stream.

## 5. Restart / blast radius

Import proof: no module outside `trader/dashboard/` and `trader/chat/` imports
either package, at `d46d045` **and** at `6d78be9` (`git grep` over `trader/`,
`scripts/`). The kernel, its spawn children and the watchdog one-shots never
load dashboard/chat code. `restart.sh dashboard` kills only python processes
whose cmdline matches `trader.dashboard.server`.

| Change | Process to restart | Kernel | Supervisor | Risk | Execution | Protective stops | Reconciliation | Watchdog |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `server.py` | dashboard | no | no | no | no | no | no | no change |
| `chat/engine.py`, `agent.py` | dashboard (lazy import inside `/api/chat`; only the dashboard imports it) | no | no | no | no | no | no | no change |
| `enrichment.py`, `local_view.py` | dashboard | no | no | no | no | no | no | no change |
| `index.html`, legacy JS | **none** (read from disk per request) — browser reload only | no | no | no | no | no | no | no change |
| compiled frontend assets | n/a today (not served); Stage 2: dashboard restart to add routes, then none for asset swaps | no | no | no | no | no | no | no change |

Shared-resource couplings (not process coupling):

- **SQLite WAL `data/luffy.db`**: dashboard owner paths now use a read-only
  connection; `Journal(...)` is still constructed at app start (schema no-op
  on the composite — columns already present). GraphQL control mutations still
  write `state_kv`/`control_events` as today.
- **Venue rate budget** (same IP/API key as the kernel): enrichment is
  single-flight, account ≤ 1/60 s, tickers ≤ 1/15 s, bounded timeouts — replaces
  the legacy per-request venue fetches; expected lower, not higher, load.
- **Watchdog race**: `restart.sh` kills then sleeps 1 s; a cron tick landing in
  that second could also start a dashboard, and the loser fails to bind 8080 and
  exits. Harmless; avoid `:00/:05`-minute boundaries. **No `watchdog.off`
  needed** for a dashboard-only restart (it is a restart, not an intentional
  stop). If `watchdog.off` is ever used, removing it re-arms kernel stale checks.
- **On-disk timing**: the moment the checkout changes, only dashboard/chat
  files change, which nothing else loads. If **any other** path changed on disk
  (wholesale deploy), kernel spawn children and the 5-minute one-shots would
  pick it up immediately without a restart — the core reason class E is NO-GO.

Exchange protective stops are independent of the dashboard in all cases.

UNKNOWN: exact code loaded by the running PIDs (best baseline `51101d0`; no
read-only check proves it, because the only dashboard difference is visible
only in RECOVERY state). Do not depend on it: rollback uses committed targets.

## 6. Rollback plan

*Amended: no destructive reset of the production working tree is permitted
anywhere in this plan.*

### Record before execution (part of gate P10)

- `PRE` = production `HEAD` immediately before the fast-forward (must be
  `d46d045`); `C` = the deployment commit SHA after fast-forward; both written
  into the deployment record **before** the dashboard restart.
- `git rev-parse C^{tree}` = `daae20a82d8c183cca0192cad70d5366e548576c`.
- Unrelated dirty files: `git status --porcelain` plus a sha256 of each dirty
  file's content (expected: the 3 docs/knowledge files).
- Blob IDs of the 8 runtime paths at `PRE` and at `C` (§2 table).
- Optional tags (authorized action): `deploy/dashboard-pre-<date>` → `PRE`,
  `deploy/dashboard-<date>` → `C`.

### Rollback rule (single path, whole deployment commit)

1. Pre-check (read-only):
   - `git log --oneline C..HEAD` is empty, **or** no later commit touches any
     of the 111 paths of `C` (`git diff --name-only C HEAD` ∩ those paths = ∅);
   - `git diff --name-only` (dirty set) ∩ those 111 paths = ∅.
   If either fails → **STOP and reconcile**.
2. `git revert --no-edit C` on `main` in the production checkout. This
   produces a new commit from committed history. Unrelated dirty files are
   left in place (they do not overlap the reverted paths).
3. If the revert reports a conflict or refuses to run → `git revert --abort`
   and **STOP and reconcile**. Do **not** use `git reset --hard`, `checkout
   -f`, `stash`-and-reset, `--force`, or any per-file restore to get past it.
4. Verify: `git diff --name-only PRE HEAD` restricted to the 111 paths is
   empty; the 8 runtime blob IDs equal the `PRE` column; `enrichment.py` and
   `local_view.py` are absent; the dirty-file hashes equal those recorded.
5. `./restart.sh dashboard`; reload the owner's browser tab.

The whole matched set — `server.py`, `enrichment.py`, `local_view.py`, chat
`engine.py`/`agent.py`, `index.html`, `attention.js`, `investigation.js` (plus
the inert frontend/tests/report files in the same commit) — is reverted
together. **Partial rollback is prohibited.** Restoring server files alone
would leave the new `index.html` calling routes that no longer exist, and
restoring the page alone would call the retired v1 route.

While a rollback is stopped for reconciliation, trading is not dependent on
the dashboard: the kernel is a separate process, exchange protective stops are
independent, and `./venv/bin/python -m trader.kernel --status` / `--panic`
remain available from the CLI.

Frontend-only rollback: Stage 1 serves nothing from `frontend/` → nothing to
roll back. Stage 2 (future) must define its own whole-commit rollback under
the same rule.

### Rollback triggers (any one)

Dashboard fails to start or bind within 60 s; `/` or `/auth/login` not
200 with valid auth; any 5xx on `/api/overview`, `/api/v2/summary`,
`/graphql`; any change to `control_state`, `panic_requested` or the
`control_events` count that is not attributable to the kernel or to an
explicit owner action (check `actor`); kernel heartbeat age > 120 s after
deploy; kernel PID changed; unexpected files changed on disk; owner-visible
data labelled fresh while its source is stale.

### After rollback revalidate

Kernel PID unchanged and heartbeat fresh; `control_state` equals the
pre-deploy value; no unexplained `control_events`; legacy `/` loads and
`/api/summary` returns 200 again; `ss` shows 8080 bound only by the new
dashboard PID; the dirty-file hashes match. Rollback restores **old chat
operational authority** (`do_ops=True`) — a known regression of rollback, not
a reason to keep a broken deploy.

Legacy reachability: in Stage 1 and after any rollback, the legacy UI is `/`.

## 7. Gates (any mismatch → STOP)

Gate definitions are accepted in this plan; **none of the POST-DEPLOY gates
has been run**. PRE-DEPLOY evidence obtained so far is marked
*(evidence so far)* and must be re-run on the actual commit `C`.

### PRE-DEPLOY — must pass before any production mutation

| # | Gate | Objective check |
| --- | --- | --- |
| P1 | Source SHA | `C`'s parent = `d46d045`; `C^{tree}` = `daae20a82d8c183cca0192cad70d5366e548576c`; `6d78be9` is the only picked commit (never `875898e`, never a merge) |
| P2 | Clean intended diff | `git diff --name-only d46d045 C` = the 111 paths of `1eb00e1..6d78be9`; zero `trader/` paths other than the 8 runtime files; no `config.yaml`, `STATE.yaml`, `NEXT.yaml`, `CLAUDE.md`, `AGENTS.md` |
| P3 | Production unchanged | production `HEAD` = `origin/main` = `d46d045`; `git status --porcelain` = exactly the 3 known docs/knowledge files; else STOP and re-plan (re-run merge-tree + tests) |
| P4 | Composite tests | on `C`, checked out outside production: expanded dashboard/chat/auth/control set **238 passed, 0 skipped** *(evidence so far: 238 independently verified on the composite)*; `scripts/backtest_equivalence.py` not required (no engine change) |
| P5 = **G6** | **Chat cannot mutate operations (isolated, exact artifact)** | See G6 below *(evidence so far: 36 passed on tree `daae20a8…`)* |
| P6 = **G7** | Typed Freeze path preserved — **non-mutating alternative selected** | On `C`, isolated: `test_authenticated_typed_freeze_still_works` and `test_freeze_button_uses_confirmed_typed_action` pass against a temp DB (confirm → typed `set_control_state` mutation → `control_events` actor `dashboard`; cancelled confirm sends nothing). **No live Freeze → ACTIVE test is authorized** in production. |
| P7 | No fixture leakage (static) | in `C`, no route references `frontend/` or `dist`; no `StaticFiles` mount; `frontend/dist` and `node_modules` absent in production |
| P8 | Process boundary (static) | `git grep` on `C`: no module outside `trader/dashboard/` and `trader/chat/` imports either package |
| P9 | v1 migration (static) | new `index.html` contains no `fetch('/api/summary`; `/api/summary` handler returns 410 with `use: /api/v2/summary` (test on `C`) |
| P10 | Rollback artifacts | `PRE`, `C`, dirty-file hashes and blob-ID table recorded (§6) |
| P11 | Watchdog pre-state | `data/watchdog.off` absent; crontab unchanged from §1 |

#### G6 — isolated exact-artifact chat safety gate (replaces the earlier G6)

- **Artifact:** the exact deployment commit `C` (or a tree proven equal by
  `C^{tree}` = `daae20a8…`), checked out outside production.
- **Isolation:** temp SQLite DB (`tmp_path`), mocked hostile LLM (`BrainLLM`
  replaced; it emits operational text **and** requests
  `panic`/`set_control_state`/`close_trade`/`create_order` tool calls),
  all socket/DNS calls denied, in-process `TestClient`. No production DB,
  service, venue or LLM.
- **Phrases (exact):** `"freeze entries"`, `"halt"`, `"panic"`, `"resume"`,
  each under every control state (ACTIVE/FROZEN/HALTED/RECOVERY), through
  (a) `ChatEngine(journal, cfg).handle(phrase, history)` with the default
  argument and (b) authenticated `POST /api/chat?do_ops=true` with body
  `do_ops: true`.
- **Must hold, per case** (checked by state and probes, **never by reply
  wording**):
  1. `control_state` unchanged;
  2. `panic_requested` unchanged;
  3. `control_events` row count unchanged and full DB dump byte-identical;
  4. no control mutation attempted: `ChatEngine._set_state`,
     `Journal.kv_set`, `Journal.log_control_event`, `ControlStateMachine.set`
     never called; SQLite authorizer sees only SELECT/READ/FUNCTION/RECURSIVE;
     every connection's `total_changes` unchanged;
  5. no execution/order mutation attempted: `Executor.open/close/close_partial`
     never called; no network attempt;
  6. no operational tool/action dispatched from chat: `detect_ops` never
     called; hostile tool calls for non-existent operational tools return
     `unknown tool` errors; the route passes `do_ops=False` regardless of
     query/body.
- **Negative control (required):** the same probes, run with `do_ops=True`
  directly on the engine, **must** detect a mutation for each phrase;
  otherwise the gate is blind → STOP.
- **STOP** if any probe fires, any state differs, or the negative control
  fails to detect mutation.
- **Evidence so far:** 36 passed on composite tree `daae20a8…` (32 cases +
  4 negative controls) using the committed fixtures of
  `tests/test_chat_control_separation.py` with a harness in the disposable
  clone only. The four exact phrases are **not** in the committed `PHRASES`
  tuple, so G6 needs that harness (or an equivalent committed test in a
  separate code package) to be re-run on `C`.
- **Production after deploy:** read-only only (S8). **No operational phrase is
  ever sent to the running production chat**, verified or not.

### AUTHORIZATION

| # | Gate | Check |
| --- | --- | --- |
| A1 | Owner authorization | Explicit, recorded owner authorization for: cherry-pick of `6d78be9` onto `main`; fast-forward of the production checkout; `./restart.sh dashboard`. Authorization does **not** include any live control mutation (no live Freeze/Resume/Panic), kernel restart, watchdog change or health-candidate deployment. |

### POST-DEPLOY READ-ONLY / SAFE SMOKE — only possible after the dashboard restart; NOT YET RUN

| # | Gate | Objective check |
| --- | --- | --- |
| S1 | Process boundary | only the dashboard PID changed; kernel PID unchanged; heartbeat advances across the window |
| S2 | Deployed bytes | on-disk blob IDs of the 8 runtime paths equal the `C` column (so P4–P6 evidence applies to what runs) |
| S3 | Auth/session | unauthenticated `/` → 401; `POST /auth/login` → 200 + cookie; wrong origin → 403 (session only; no trading state) |
| S4 | Local-first Overview | `GET /api/overview` 200 with `scope` "local journal/files only" |
| S5 | Stale/error truth | `GET /api/v2/summary`: nulls where incomplete; `freshness` present per source; heartbeat freshness consistent with `data/heartbeat_luffy.json` |
| S6 | v1 migration | `GET /api/summary` → 410 with `use: /api/v2/summary`; owner tab reloaded |
| S7 | Legacy fallback | `/` renders legacy UI; `/ws/live` delivers frames |
| S8 | Chat safety (read-only) | **no chat message containing an operational phrase is sent.** Verify S2, then read-only: `control_state`, `panic_requested`, `control_events` count unchanged across the smoke window except kernel/owner-attributed rows |
| S9 | Typed Freeze (non-mutating) | GraphQL introspection `{__type(name:"Mutation"){fields{name}}}` lists `set_control_state` and `panic`; served `/` contains the Freeze `confirm(...)` + `set_control_state` path. No mutation executed. |
| S10 | DEMO/LIVE leakage | `GET /owner/`, `/assets/index.js` → 404 |
| S11 | Watchdog | `data/watchdog.off` absent; the next cron tick logs no restart |

Any failure → rollback trigger (§6).

## 8. Performance evidence (honest scope)

- Integrated stalled-enrichment Overview p95 **≈ 89.10 ms** (standalone ≈ 87.45).
- Integrated warm Overview p95 **≈ 3.14 ms** (standalone ≈ 4.46).
- The historical **4.46 ms** is warm-cache evidence, **not** a
  stalled-enrichment result; stalled-sample conditions from that run cannot be
  reconstructed (no cache-state telemetry).
- Legacy `GET /` p95 during a 5.05 s venue stall: 60,218 ms → 10.5 ms (synthetic).
- All figures are synthetic/fixture on this VM. They do **not** establish
  production tail latency, WAL contention with the live kernel, or trading-core
  isolation.

Minimal production-safe post-deploy measurement (no stress):

- 20 sequential authenticated GETs each of `/api/overview` and
  `/api/v2/summary`, 1 s apart, from the VM (`curl -w %{time_total}`); record
  p50/max (n too small for a p95 claim).
- Dashboard RSS/CPU via `ps` at +5 min and +60 min.
- Passive kernel isolation: heartbeat age samples and kernel cycle cadence from
  `luffy_kernel.log` for 60 min before vs after; no change in cycle-error lines.
- Venue: count dashboard ticker/account calls over 10 min (should be ≤ 40
  ticker refresh jobs and ≤ 10 account refreshes) and no `-1003`/429 in either log.

## 9. Post-deploy verification checklist

1. S1–S11 run and recorded (read-only; no operational chat phrases, no live
   control mutation).
2. Dashboard new PID bound to `192.168.126.131:8080`; no traceback in `luffy_dash.log`.
3. Owner browser reloaded; Overview shows values with freshness; no 410 in the
   browser console.
4. Measurements in §8 recorded to a post-deploy report.

## 10. NO-GO conditions

- Deploying by checkout/merge of `dashboard-integration-v1`,
  `world-model-foundation`, `6d78be9` or `875898e` as a whole (class E).
- Composite tree ≠ `daae20a8…`, or any `trader/` path outside the 8 runtime files changes.
- Production HEAD or dirty set differs from §1 at deploy time (re-plan).
- Any PRE-DEPLOY gate failing or not run on `C`; G6 negative control not detecting mutation.
- Any mutation path observed through chat (G6 or S8).
- Kernel restart required or kernel PID changes.
- Serving any `frontend/` build from production before the binding package is
  reviewed; any DEMO/fixture data reachable at a production URL.
- Any live Freeze/Resume/Panic test, or any operational phrase sent to production chat.
- Any `git reset --hard`, forced checkout or partial per-file rollback.
- Watchdog modified or `watchdog.off` left behind.
- A1 authorization absent.

## Owner decisions

- **H1 — URGENT INDEPENDENT RISK; not a blocker to the proven dashboard-only
  restart; not solved in this package.** Best historical baseline for the
  running kernel is `51101d0` (reflog 05:29:57, boot 05:29:59); exact loaded
  runtime UNKNOWN. On disk, `51101d0 → d46d045` changes 24 runtime files
  (+3,918/−96) including `kernel.py`, `engine/supervisor.py`,
  `engine/protective.py` and `engine/reconcile.py`. Any unplanned kernel
  restart (watchdog stale heartbeat, reboot) activates that code. Needs its
  own decision/package.
- **H2:** approve the deployment basis = cherry-pick of `6d78be9` only onto
  `main` (recommended) vs waiting for `world-model-foundation` to reach
  production (then re-plan).
- **H3 (resolved by amendment):** no live Freeze → ACTIVE test is authorized;
  G7 uses the isolated test (P6) and read-only introspection (S9).
- **H4:** Stage 1 ships the backend slice ahead of the frontend; it also
  removes current chat operational authority. Confirm you want that separate
  from the frontend timeline (recommended: yes).
- The health deployment candidate remains separate and not authorized here.

## Files

Created: this report only (amended once). No other file created or changed in
either worktree. A disposable shared clone under the session job tmp was used
for the merge-tree simulation, tests and the G6 harness; it wrote nothing to
the production repository.
