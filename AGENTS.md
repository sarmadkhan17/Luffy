# LUFFY — agent operating guide

Shared working rules for Claude/Opus, Astra and any other development agent.
This file holds stable rules only. It never records package progress, commit
SHAs, percentages or temporary task status; those live in `STATE.yaml` and
`NEXT.yaml`. Explicit instructions from the owner in the current session take
precedence.

## Authority order

1. `SDD.md` — authoritative target architecture and locked design.
2. `STATE.yaml` — what the repository currently proves.
3. `NEXT.yaml` — active package, last completed work and parked boundaries.
4. `AGENTS.md` — agent working rules only.
5. Task-specific source, tests and artifacts.

- The old roadmap, handoffs, reports, `REQUIREMENTS.md` and historical design
  docs do not override SDD/STATE/NEXT.
- **Locked** = do not redesign. **Implemented** = verify it; do not rebuild it.
- Discuss and work only genuine gaps unless the owner explicitly reopens
  something.

## Repository safety

- Verify `pwd`, branch and HEAD before any work.
- Never confuse `/home/sarmad/trader` (main) with a worktree; confirm which
  checkout the task's HEAD belongs to.
- Preserve unrelated dirty files. Never stage, restore or delete unrelated
  changes.
- `knowledge/` and `graphify-out/` changes are user-owned unless explicitly in
  scope.

## Real-money safety

- If HEAD, the active package, source/result provenance, files to commit,
  Risk/Execution impact or deployment state is ambiguous, resolve it from
  repository evidence before acting.
- Never infer deployment. A commit is not a deployment.
- Live-path changes touching entry, sizing, Risk, Execution, stops, orders,
  accounting or deployment require, in order:
  Opus implementation → Astra independent review → reconciliation → focused
  tests → commit → a separate, explicit deployment decision.

## Workflow roles

- **Opus/Claude:** implementation.
- **Astra:** independent reviewer and challenger.
- **ChatGPT:** reconciliation, sequencing and control-plane guidance.
- Opus and Astra never edit the same files simultaneously.

## Package discipline

- One bounded package at a time.
- The implementation commit is separate from the STATE/NEXT control-plane
  commit.
- After closure, return to `NO_ACTIVE_PACKAGE`. Do not automatically continue
  into another package.
- Preserve every parked boundary recorded in `NEXT.yaml`.

## Testing

- Run focused tests for the active package:
  `./venv/bin/python -m pytest tests/<relevant_test>.py`
- Never run the full `pytest tests/` unless the owner explicitly asks.
- Never use `-x` or `--maxfail=1`.
- If a focused failure looks baseline/unrelated, rerun only that exact failing
  test on clean HEAD. Known baseline failures do not justify a full-suite rerun.
- Use the repo-local `./venv/bin/python` when present. For
  `/home/sarmad/trader-world`, if no venv exists, use
  `/home/sarmad/trader/venv/bin/python` only after confirming imports resolve
  to trader-world.

## Graphify and knowledge

- When the user types `/graphify`, use the installed graphify skill
  (`.claude/skills/graphify/SKILL.md`) before doing anything else.
- Do **not** run `graphify update .` by default; run it only when explicitly
  requested.
- Do not modify `graphify-out/` or `knowledge/` to repair tests. Graphify-only
  failures that reproduce on clean HEAD are
  `BASELINE_KNOWLEDGE_GRAPH_FAILURE`.
- Use Graphify (`graphify query|path|explain`) for code navigation when useful.
  Do not run full semantic extraction unless explicitly requested.
- For trading-knowledge questions, use the structured lookup when applicable:
  `./venv/bin/python scripts/memory_lookup.py "<Concept>" [relation]` or
  `--reverse <relation> "<target>"`. Answer only the requested relation from
  the returned memory unless broader context is asked for.

## Evidence rules

- No fabricated data, labels, mappings, thresholds or authority.
- Preserve point-in-time and provenance semantics; missing data is NaN or
  explicitly UNAVAILABLE, never a fabricated value.
- `TESTED` does not mean deployed.
- Descriptive evidence is not trading edge unless a contract proves it.
- `context_only` memory cannot become suppression, ranking or authority by
  inference.

### Research contamination and error budget

- Discovery uses one shared calendar cut per horizon across all symbols;
  per-symbol cuts leak eras.
- Freeze the thesis and protocol before evaluation.
- Proposers see discovery evidence only; never expose held-out numbers or
  relabel inspected history as untouched data.
- Every held-out look spends its registered error budget. No retry, missing
  data, renamed slice or persuasive explanation may waive a gate.
- Funding is signed; actual and null runs must use the same cost model.

## Operational safety

- Never print, log or commit secrets or `.env` values.
- Do not run the live kernel, place orders, change control state, spend money,
  authorize validation or deploy without explicit authorization.
- Preserve frozen research artifacts and owner authorization boundaries.
- `scripts/watchdog.sh` restarts stopped or stale processes. Before an
  authorized intentional stop, create `data/watchdog.off`; account for that
  flag when restoring service.

## Trading-path invariants

- Use `engine/protective.py` for protective-stop placement, cancellation and
  enumeration. Binance protective stops use conditional/algo IDs, not ordinary
  order IDs. Never remove a venue stop merely because its journal ID is
  missing.
- Sizing, stops and trails must use ATR on the spec's declared timeframe.

## Historical docs

- Open historical docs only when task-relevant.
- Never treat the old demo-first scope, Gate 2, the old roadmap or archived
  handoffs as current authority where they conflict with SDD/STATE/NEXT.
