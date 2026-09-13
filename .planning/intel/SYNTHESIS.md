# Synthesis Summary

Entry point for `gsd-roadmapper`. Mode: **merge** (existing `.planning/`
context was a codebase map only — no PROJECT.md/REQUIREMENTS.md/ROADMAP.md/
STATE.md/phase CONTEXT.md existed to merge against).

## Doc counts by type

| type | count | docs |
|---|---|---|
| DOC  | 11 | CLAUDE.md, macro-guard-agent plan, talk-to-luffy-analyst plan, strategy-foundation plan, feature-layer plan, scraper-outside-world plan, luffy-pays-rent plan, research-phase0-groundwork plan, research-phase1-pieces plan, research-phase2-search plan, NEXT-SESSION-TODO |
| SPEC | 6 | talk-to-luffy-analyst-design, agents-command-deck-design, strategy-generation-rebuild-design, researcher-and-strategy-first-core-design, luffy-pays-rent-design, luffy-research-pipeline-design |
| PRD  | 1 | REQUIREMENTS.md |
| ADR  | 0 | — |
| **Total** | **18** | |

## Cycle detection

Cross-reference graph is a one-directional DAG (implementation plans point at
their spec; specs point at CLAUDE.md; nothing points back). No cycles found.
No docs excluded on this basis.

## Decisions (`decisions.md`)

23 entries. **19 locked** (CLAUDE.md invariants/measurements, plus SPEC
decisions independently confirmed by CLAUDE.md), **4 proposed** (SPEC
`DECIDED` items with no independent CLAUDE.md confirmation found in this
ingest: machine-found-candidates admission routing, Talk-to-Luffy scope, the
Agents Command Deck's own "locked" section, and the Researcher agent
contract — CLAUDE.md explicitly says the Researcher "still does not exist").
No ADRs exist in this repo; CLAUDE.md fills that role per the ingest
guidance.

## Requirements (`requirements.md`)

11 requirements (REQ-identity-mission through REQ-success-criteria-v1),
extracted verbatim from `REQUIREMENTS.md` (the only PRD in the set, manifest
precedence 6 — outranked by CLAUDE.md and every SPEC/most plans). Several
sections describe a state later superseded by measurement (demo vs. live
mode, risk envelope, strategy representation, analyst roster) — supersession
is recorded in `INGEST-CONFLICTS.md`, not by editing the requirement text.

## Constraints (`constraints.md`)

13 entries from the 6 SPEC documents: schema (2), protocol (2), api-contract
(1), nfr (8, including the Risk Officer entry that is superseded numerically
but preserved with a note).

## Context (`context.md`)

14 topic-keyed notes: 4 on genuinely open work (Researcher not built,
research pipeline phases 3-5 not built, the 2026-09-11 second-creation-path
incident, Donchian's unresolved universe generalization, the failing
macro_guard wall-clock test), and 10 tracing each DOC-type implementation
plan's scope and build status against CLAUDE.md's current state.

## Conflicts

**0 blockers, 0 warnings, 9 auto-resolved (INFO).** No ADRs exist, so no
LOCKED-vs-LOCKED case is possible. No existing locked planning decisions
exist in this merge-mode target to contradict. Only one PRD exists, so no
competing-acceptance-criteria case is possible. All 9 INFO entries are
CLAUDE.md (precedence 0) superseding an older, lower-precedence document —
see `INGEST-CONFLICTS.md` for the full list and rationale on each.

Full detail: `.planning/INGEST-CONFLICTS.md`
Per-type intel: `decisions.md`, `requirements.md`, `constraints.md`, `context.md` in this directory.
