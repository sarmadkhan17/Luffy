## Conflict Detection Report

### BLOCKERS (0)

None. No ADR-classified documents exist in this ingest set, so there is no
LOCKED-vs-LOCKED contradiction to check. No cycles were found in the
cross-reference graph (all doc-to-doc `cross_refs` point from an
implementation plan toward its spec, or from a spec toward CLAUDE.md — a
one-directional DAG, never back-referenced). `EXISTING_CONTEXT` for this
merge-mode run contains only a codebase map — no existing PROJECT.md,
REQUIREMENTS.md, ROADMAP.md, STATE.md or phase CONTEXT.md — so there is no
existing locked decision for anything in this ingest to contradict. No
document was classified UNKNOWN or low-confidence.

### WARNINGS (0)

None. Only one PRD (`REQUIREMENTS.md`) exists in this ingest set, so there is
no case of two PRDs defining the same requirement with divergent acceptance
criteria to preserve as competing variants.

### INFO (9)

[INFO] Auto-resolved: CLAUDE.md supersedes REQUIREMENTS.md on deployment mode
  Note: `REQUIREMENTS.md` (PRD, precedence 6) states "Mode: LIVE from Phase 1 (no demo phase)" and "$2,000+ real USDT". CLAUDE.md (DOC, precedence 0 — outranks every spec/plan per the manifest) states `BINANCE_DEMO=true` routes every order to Binance Demo Trading (trades journalled `exec_mode="live"` meaning real orders were sent, to a demo venue). CLAUDE.md wins as the current statement of fact; `REQUIREMENTS.md`'s self-declared "FROZEN" status does not override the manifest's precedence ranking. Recorded verbatim in `requirements.md` (REQ-capital-deployment) without alteration; supersession recorded here only.

[INFO] Auto-resolved: CLAUDE.md supersedes REQUIREMENTS.md on the risk envelope
  Note: `REQUIREMENTS.md` specifies risk per trade 5% equity, portfolio heat cap <=15%, per-symbol cap <=8%, max open positions 10, daily loss breaker -6%, staged de-risk -8/-12/-20% DD. CLAUDE.md states current caps of max 8 open trades, 0.5% risk each, and documents the measurement behind the change ("0.5% with 8 beats 0.75% with 4 on BOTH return and drawdown... relaxing the cap above 8 makes it WORSE"). CLAUDE.md wins. Recorded verbatim in `requirements.md` (REQ-risk-envelope).

[INFO] Auto-resolved: CLAUDE.md supersedes REQUIREMENTS.md's Strategy Genome System
  Note: `REQUIREMENTS.md` describes a `StrategyGenome` (`family: momo|meanrev|breakout|rotation|carry`, gene schema) as the strategy representation. CLAUDE.md documents the current representation as a `StrategySpec` compiled from a restricted expression DSL over a 74-feature registry (`strategy/features.py` etc.), per `docs/superpowers/specs/2026-08-31-strategy-generation-rebuild-design.md` (precedence 3), which explicitly supersedes `Genome`/`FAMILY_GENE_SPECS`/`library.EVALUATORS`. CLAUDE.md wins as the confirmed-implemented state.

[INFO] Auto-resolved: CLAUDE.md supersedes REQUIREMENTS.md's 5-analyst roster
  Note: `REQUIREMENTS.md` lists five analysts (structure, flow, momentum, value, rotation). CLAUDE.md lists seven (adds positioning, depth) and documents that the analyst votes no longer set the trade score (measured net anti-edge; strategy signal required via `require_strategy_signal`). CLAUDE.md wins.

[INFO] Auto-resolved: CLAUDE.md supersedes the Risk Officer caps in the strategy-first core design spec
  Note: `docs/superpowers/specs/2026-09-01-researcher-and-strategy-first-core-design.md` (SPEC, precedence 2) decides "initial margin <= 15% of equity per position; max 4 open." CLAUDE.md (precedence 0) states the current caps are max 8 open trades, 0.5% risk each, and its own "Validate the config, not just the strategy" section documents the change from the old 4-position/1.5% setting (which tripped `halt_drawdown_pct`) to the current 8-slot/0.5% setting as a measured improvement. CLAUDE.md wins; the spec's figure is recorded in `constraints.md` with an explicit superseded note rather than silently dropped.

[INFO] Auto-resolved: CLAUDE.md supersedes the macro-guard plan's Finnhub-primary calendar
  Note: `docs/superpowers/plans/2026-08-29-macro-guard-agent.md` (DOC, precedence 5) builds MacroGuard around the Finnhub economic calendar API as the primary source. CLAUDE.md (precedence 0) states the current default calendar is the free, keyless ForexFactory feed; a Finnhub key is tried first only if present, and the free Finnhub tier cannot read the required endpoint (HTTP 403), so the ForexFactory fallback is the normal path. CLAUDE.md wins as the current, measured behavior.

[INFO] Auto-resolved: CLAUDE.md supersedes NEXT-SESSION-TODO.md's population/test-count snapshot
  Note: `docs/superpowers/plans/NEXT-SESSION-TODO.md` (DOC, precedence 7 — lowest-ranked document in the manifest) records a 2026-08-31 snapshot: `population=19` (15 legacy genomes + 4 compiled specs), 414 tests passing, legacy `Genome` path retirement deferred until the spec population "has traded enough paper to trust." CLAUDE.md's current state (single creation path enforced by `tests/test_single_creation_path.py`, 1218 tests, all legacy genomes since retired) supersedes this snapshot in every particular. Kept in `context.md` for provenance only, per the manifest's explicit lowest-precedence ranking.

[INFO] Auto-resolved: CLAUDE.md's admission gate supersedes the strategy-generation-rebuild-design spec's portfolio keep/replace rule
  Note: `docs/superpowers/specs/2026-08-31-strategy-generation-rebuild-design.md` (precedence 3) specifies Analyst admission via `max_corr < 0.6` OR `PF > 1.15 x best_correlated.PF` against the book. CLAUDE.md and the higher-precedence `docs/superpowers/specs/2026-09-11-luffy-research-pipeline-design.md` (precedence 1) describe the current, refined admission gate: pooled PF >= 1.15 over >= 20 trades, signal overlap < 0.6, AND cross-symbol rotation-null consistency p < 0.01, with <4 testable symbols REFUSED outright. This is an evolutionary refinement rather than a flat contradiction; the current (higher-precedence) rule is recorded as the locked decision in `decisions.md`, and the earlier rule is not separately transcribed as a competing requirement since only one is in force.

[INFO] Auto-resolved: CLAUDE.md confirms rather than contradicts the rent-check design's numeric bar
  Note: `docs/superpowers/specs/2026-09-11-luffy-pays-rent-design.md` (precedence 1) decides the $50/week bar and "death is a human act." CLAUDE.md's `rent-check` daemon-thread entry independently states "the week's net off the venue income ledger against the $50 bar... Reports only — never stops Luffy," confirming rather than contradicting the spec. Recorded as a single locked decision in `decisions.md` citing both sources, not as a conflict requiring resolution — included here only for completeness of the audit trail.
