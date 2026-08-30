# Agents Command Deck — Design Spec

**Date:** 2026-08-30
**Status:** Approved for planning
**Scope:** Wire the finished command-deck cockpit mock into the live dashboard's
OS → Agents tab, driven by real data from an enriched `/api/org` endpoint.

## Goal

Replace the raw analyst stats table in the OS → Agents tab
(`#agents-tbl`, fed by `/api/agents_stats`) with the command-deck cockpit
(`design/agents-cockpit-v2/command-deck.html`): a tiered command hierarchy
(Manager core → 6 staff → 7 analysts) with hex-emblem nodes, animated circuit
wires, filter chips, and a right-hand detail panel — every element fed by real,
journal-derived data.

The deck currently renders from hardcoded `MGR`/`STAFF`/`ANS` arrays and a
`Math.random()` sparkline. This work makes it live.

## Decisions (locked)

1. **Placement:** OS → Agents tab. The deck replaces `loadAgents()` and the
   `#agents-tbl` table.
2. **Data fidelity:** real backend data — extend `build_company()` /`/api/org`,
   do not fake fields on the frontend.
3. **Old view:** removed. `loadAgents()`, the `#agents-tbl` markup, and the
   `/api/agents_stats` fetch path are deleted. The numbers it showed survive
   inside the deck's analyst nodes + detail panel.
4. **Company tab:** unchanged this pass. It now overlaps the deck (both show the
   full org via `/api/org`); retiring or repurposing it is a later decision.
5. **API shape:** enrich the existing `/api/org` payload in place — no new
   `/api/deck` endpoint. One source of truth, no drift; the Company tab's
   vis-network renderer ignores the extra fields.

## Architecture

Two processes already talk only through the journal. This work touches only the
**dashboard** process (read-only FastAPI) and its single-page frontend. No
kernel, no trade-loop, no order-path changes. `build_company()` stays read-only:
journal queries + vault mtimes, no network calls.

```
GET /api/org
  → build_company(journal, cfg)      # enriched: +category +stats +bar +signal +core
  → index.html loadDeck()            # builds nodes from employees[], renders deck
      ↳ soft-poll every 8s           # updates status/metrics in place, no relayout
```

## Backend — enrich `/api/org`

### Two decoupled concerns: filter-category and node-color

The mock conflated these (Theorist is filter-group "Research" but amber-colored;
Strategist's `STRATEGY` category matched *no* filter chip — a latent bug). We
separate them cleanly:

**`category`** — drives the filter chips + filtering, aligned to the org
grouping and the existing chip set. Six values, every node matched:

| category   | roles | filter chip |
|------------|-------|-------------|
| BRAIN      | Manager | Manager |
| ANALYST    | the 7 analysts | Analysts |
| RESEARCH   | Researcher, Theorist, Strategist | Research |
| RISK       | Risk Officer | Risk |
| EXECUTION  | Trader | Execution |
| KNOWLEDGE  | Librarian | Knowledge |

**Node color** — a per-role map on the **frontend** (keyed by role/name, not
category), preserving the deck's distinct hues:

| role | color |
|------|-------|
| Manager | `#9b7bff` (brain violet) |
| analysts | `#22d3c5` (analyst teal) |
| Researcher | `#c678dd` (research magenta) |
| Theorist | `#ffb224` (insight amber) |
| Strategist | `#4da3ff` (strategy blue) |
| Trader | `#00d1a0` (exec green) |
| Risk Officer | `#ff5c74` (risk red) |
| Librarian | `#22d3c5` (knowledge teal) |

Add the declarative `category` to each employee block in `org.yaml` (the
org-chart source of truth). `trader/org.py` reads it (default `""`); `Org.load()`
assigns `ANALYST` to auto-discovered analysts. Both the category→chip and
role→color maps live on the **frontend**, so `org.yaml` carries only the
category label and stays presentation-light. Category is metadata-only — it
never touches decision logic (consistent with the org layer's hard constraint).

### build_company(): new per-employee fields

Each entry in `employees[]` gains (existing fields unchanged):

| field      | type                       | notes |
|------------|----------------------------|-------|
| `category` | str                        | from org.yaml / auto `ANALYST` |
| `stats`    | `[[label, value], …]` (3)  | the node's stat triple |
| `bar`      | int 0–100                  | node gauge (see honesty note) |
| `signal`   | `[int, …]` (~24)           | hourly histogram for the panel sparkline |
| `core`     | `[[label, value], …]` (4)  | **Manager only**; else omitted/empty |

**Per-role `stats` triples and sources:**

| role       | stats triple                        | source |
|------------|-------------------------------------|--------|
| Manager    | (uses `core` instead)               | — |
| Analysts   | `Accuracy`, `Samples`, `Votes 24h`  | `agent_accuracy()` (acc%, n) + 24h vote count from `votes` |
| Researcher | `Ideas 24h`, `Accepted`, `Sources`  | `brain_events` kinds `harvest_cycle`/`harvest_accepted`/`crawl_doc` (24h counts) |
| Strategist | `Reviews`, `Active`, `Promoted`     | `brain_events` `review_complete`/`proposal_accepted` (24h) + `strategies` WHERE status=ACTIVE |
| Theorist   | `Autopsies`, `Beliefs`, `Last run`  | `brain_events` `autopsy` (24h) + doctrine belief count + last-autopsy age |
| Trader     | `Open`, `Exposure`, `Day P&L`       | `open_trades()`, exposure (already computed in `risk()`), sum realized_pnl since midnight UTC |
| Risk       | `Heat`, `Breaker`, `Guard`          | heat% (below), control state, `news_guard_state` kv |
| Librarian  | `Notes`, `Links`, `Updated`         | vault `*.md` count, vault-graph edge count, newest mtime age |

**Manager `core` (4 tiles):** `Equity` (equity table), `Control` (kv
`control_state`), `Cycle` (age since last `cycles` row, e.g. `3s`/`4m`),
`Heat` (see below).

**Heat:** `open_risk / (equity × heat_cap)` as a percentage, mirroring
`risk.py` (`heat_cap = portfolio_heat_cap_pct/100`). If per-trade open risk
isn't reconstructable from `open_trades()`, fall back to notional-exposure ÷
equity and label consistently. Resolve the exact formula during implementation
by reading how `risk.py` accumulates `open_risk`.

**`signal` (sparkline):** a real 24-bucket hourly histogram, replacing the
mock's `Math.random()`. Analysts → hourly `votes` count; event-driven staff →
hourly `brain_events` count for their kinds; Trader → hourly trade count;
Manager → hourly `cycles` count. Single `GROUP BY strftime('%H', ts)` per node,
zero-filled to 24 buckets.

**`bar` (0–100):** analysts = accuracy%. Staff = 24h activity normalized against
a per-role soft cap (clamped 0–100). **Honesty note:** the staff bar is a rough
activity gauge, not a precise KPI — code comment says so. Analyst bars are exact.

### New / reused journal queries

Reuse `agent_accuracy()`, `open_trades()`, `kv_get()`, `query()`. New read-only
`query()` calls: 24h vote count per agent, hourly histograms, `brain_events`
24h counts by kind, active-strategy count, vault-graph edge count. All read-only,
no schema changes.

## Frontend — `loadDeck()` in the Agents tab

The deck is a full-page app (own left rail, `position:fixed` 3-column grid). The
dashboard is already the shell, so we embed only the parts that matter.

- **Strip** the deck's `.app` grid, `.rail` left nav, and fixed positioning.
  Keep the **center command-board** (tiers + `#wires` SVG + `.scan`/`.frame`
  HUD detailing) and the **right detail panel**, fitted into `#os-agents`.
- **Namespace** the deck's CSS to avoid colliding with dashboard styles (scope
  under a wrapper class, e.g. `.deck`).
- **Replace** hardcoded `MGR`/`STAFF`/`ANS` with a builder that maps
  `/api/org` `employees[]` → node model: split by `category`
  (`BRAIN` → manager tier, `ANALYST` → analyst tier, rest → staff tier);
  color each node via the frontend **role→color** map and its filter group via
  the **category→chip** map; feed `stats`/`bar`/`signal`/`core`/`feed`/`desc`/
  `node`/`status`.
- **Wires, emblems, filters, selection, panel** logic ports over unchanged; the
  emblem glyph per role is chosen by category/name.
- **Detail panel** uses real `feed`, real `signal` sparkline, `wraps` as the
  related-modules chips, and the existing "open knowledge file" action
  (`openCoNote(e.node)` already exists for the Company tab — reuse it).
- **Soft-poll every 8s** while the Agents tab is visible: update node
  status/metrics/bars in place (like `loadCompany(soft)`), no relayout. Recompute
  wires only on resize.
- **Graceful degradation:** if `/api/org` fails or `employees` is empty, show a
  restrained "agents data unavailable" state (matching existing patterns).

### Removed

`loadAgents()`, the `#agents-tbl` table markup, and the `if(name==='agents')
loadAgents()` call are deleted. `/api/agents_stats` endpoint: remove it too,
since nothing else consumes it (verify with a grep before deleting; if another
caller exists, leave the endpoint and only drop the frontend use).

## Testing

- **Backend:** extend the existing org/company test(s) to assert `/api/org`
  employees carry the new fields with correct shapes/types against a seeded
  journal — `category` present, `stats` length 3, `bar` in 0–100, `signal`
  length 24, Manager carries `core` length 4. Assert `build_company` still makes
  no network calls and stays read-only.
- **Regression:** full `pytest` suite stays green. (Note: `tests/
  test_brain_reconnect.py` has 2 pre-existing FK failures unrelated to this
  work — not introduced here.)
- **Frontend:** load the dashboard, open OS → Agents, confirm the deck renders
  live nodes, the panel shows real feed + sparkline, filters work, and status
  updates on the 8s poll. Requires `./restart.sh dashboard`.

## Out of scope

- Kernel / trade-loop / order-path changes (none).
- Retiring or repurposing the Company tab (later decision).
- LLM persona rewrites or any change to attribution (metadata-only stays).
- New DB tables or columns.

## Risks / open items

- **Heat formula:** exact `open_risk` reconstruction from `open_trades()` — 
  resolve by reading `risk.py` during implementation; documented fallback exists.
- **vault-graph edge count** for Librarian `Links`: reuse the same source
  `/api/vault/graph` uses; if too heavy for an 8s poll, cache or approximate.
- **CSS collisions:** namespace the deck styles under a wrapper to avoid leaking
  into the rest of `index.html`.
