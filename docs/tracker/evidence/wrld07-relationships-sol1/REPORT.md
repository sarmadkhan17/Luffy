# [SOL-1][WRLD-07] relationship-state proof

Scope: WRLD-07 architecture/offline. Dependency WRLD-03 is CLOSED. QNT-02 is CLOSED; QNT-03 is eligible after this closure, with its row and work selection unchanged.

Baseline 093187b: existing immutable ordered endpoints, horizon/cut, exact Observation record hashes, WorldModelRecord and WorldHistory already provide identity, provenance, versioned history and exact-cut replay. No replacement history implementation was needed.

ACTUAL GAP: six executable probes accepted VALID relationships without measured method/window/context, with stale or expired evidence, guessed attribution, permanent-beta labels and untested cointegration. See probe.py and baseline.json; identical fixed probes refuse all six (fixed.json). The portfolio reader could infer CURRENT from legacy VALID evidence without a relationship freshness bound.

CHANGE: world.relationship.v2 carries immutable measurement method, explicit endpoint instrument membership, window, maximum age, assumptions and context source cut, plus explicit instability uncertainty. VALID accepts bounded rolling-pearson.v1 correlation or ols-beta.v1 only; it checks endpoint attribution, explicit group membership source references, matching evidence method/window/timeframe, known capture/availability, evidence quality/freshness, finite estimates and correlation range. Unsupported attribution, permanent labels, cointegration and hidden payload claims are refused. No validated stationarity-test path exists here, so cointegration is conservatively unavailable.

Current eligibility expires at the earlier of the relationship window limit and any evidence limit. Rewrapping an archive with a later outer expiry cannot extend this. Legacy v1 records retain their exact original bytes and IDs but never qualify as CURRENT in the portfolio reader. Changes to the content/cut produce new relationship IDs; existing exact-cut history retains prior versions across disk reload, refuses ambiguous cuts and never falls back to a newer relationship.

Evidence matrix in tests/test_wrld07_relationship_state.py covers every requested positive and negative proof: complete metadata, bounded correlation/beta, stale/low-quality evidence refusal, mismatched/guessed endpoint attribution, group membership sourcing without inferred instrument exposure, unsupported cointegration/permanent claims, context cuts, immutable versions, historical sign change, absent-cut refusal and exact pre-fix v1 replay. Adjacent WorldModel, context, cognition replay and portfolio tests cover consumption.

Limit: this closes record validation and offline historical semantics. It does not establish statistical significance, calibrated instability or profitable trading. Instability may honestly be recorded as unassessed. Numeric estimates remain source measurements; this change adds no estimator, automatic group-membership inference, stationarity test or production relationship producer. Context records have no numeric allocation authority.

No provider, paid tool, runtime restart or trading action was used. The local Claude execution availability probe returned Not logged in; the authorized work continued locally. Existing unrelated repository and graph changes were preserved. Graphify AST update was requested after code changes.
