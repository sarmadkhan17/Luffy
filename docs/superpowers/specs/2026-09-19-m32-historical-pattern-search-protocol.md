# M3.2 historical pattern search protocol — frozen design

Status: protocol design only. M3.2 search has not run.

Protocol ID: `3a6e87c28a8f20e49bb148c4dacfc4b34040781b93d38fe7eeeda39fd78880bd`

This protocol is frozen before any M3.2 search. It is a discovery protocol,
not a strategy, performance, admission, Gate 2, referee, or live-handoff
protocol. It does not change `research.referee` or `research.handoff`.

## 1. Locked input and boundaries

The only input is the accepted historical PIT dataset:

`docs/superpowers/artifacts/pit-dataset/2026-09-19-historical-discovery-artifact.json`

The search must verify, then pin, these fields before reading rows:

- `dataset_id = m3.1-historical-discovery-2026-09-19`
- `dataset_version = 5d4d1fc16549f2385e89fdafd6d19525069185ca0348566f53096a4ddaefddff`
- `schema_version = pit-dataset.v1`
- `declaration.discovery_cut_ms = 1789830909506`
- `sufficiency.status = sufficient`
- `sufficiency.population_sampling_claim = false`
- `replay_passed = true`
- `source_meta.adapter_rejections = []` (if present)

No database, current candle store, vault note, strategy definition, repaired
receipt, archive-read timestamp, live feed, or held-out result may be read.
The artifact is not edited, re-cut, or supplemented. Missing values remain
missing. A row's `known_ms` and `point_in_time_features` remain evidence about
availability; `as_of_ms` is not silently treated as a historical observation
clock for observation-only rows.

The frozen artifact contains 227 rows: 161 states, 1 failure, 1 transition,
and 64 sequence rows; 163 base outcomes include selected, ignored, skip, and
failure/transition cases. All rows are in dependence group `g0000`; base rows
cover only September 2026; all stored regimes are `unknown`; and execution
accounting is unavailable. These facts are reported in every search result.

## 2. Searchable feature and state space

The vocabulary is allow-listed and versioned in the protocol. No arbitrary
JSON-key discovery, target-derived feature, label-derived feature, or LLM
feature generation is permitted.

### Forecast state atoms

For rows with `producer=forecast`, `row_type=state`, and a finite value:

- `recent_move_bps`
- each element of `bar_returns_bps[0:5]`, named `bar_return_0_bps` through
  `bar_return_4_bps`
- `direction` as a categorical state (`long`, `short`)
- `window_bars` as a categorical state
- `selected` is an outcome/context field, never a searchable feature

Numeric values are converted to three-valued discretized states using
quantiles computed once from the complete accepted artifact: `low` (<=p33.33),
`mid`, `high` (>=p66.67). Exact ties use the lower bin. The quantile table is
part of the search receipt. Quantiles are never recomputed per symbol, cut,
or candidate.

### Journal states

For rows with `producer=journal`, `row_type=state`, the searchable states are
the exact categorical values of `action`, `executed`, `attribution_linked`,
`registration_time_availability`, and `skip_reason`, with missing values as
`missing` rather than a negative. `strategy_attribution` is retained only as a
reported field and cannot be searched as a proxy for a strategy definition.

### Investigation states and transitions

For the two registered investigation cases, the searchable categorical states
are the exact values of `family`, `cohort`, `sign`, and `catalog_id`, where
present. A transition atom is a pair of adjacent accepted rows linked by
`prior_row_id`/`current_row_id`, represented as
`field:old_value->new_value`; no adjacency is invented across a missing link.

### Sequence atoms

Only the 64 `row_type=sequence`, `producer=sequence_link` rows are used for
sequence mining. A sequence is the canonical ordered tuple of the linked
prior/current row signatures, preserving `prior_known_ms`, `current_row_id`,
`elapsed_ms`, and the linked row types. Maximum sequence length is 4 links;
only explicit links in the artifact count. Sequence atoms cannot use later
outcome fields.

## 3. Pattern representation

Every pattern has a canonical JSON representation, sorted keys, no floats
other than already-frozen bin boundaries, and a SHA-256 `pattern_id`.

- `state`: conjunction of 1–3 distinct state atoms from one row family.
- `transition`: one explicit transition atom, optionally conjoined with one
  state atom from the same linked episode.
- `sequence`: ordered 2–4-link tuple, optionally with one fixed categorical
  state guard.

Patterns are evaluated against each eligible row/episode once. Overlapping
matches are retained and reported; they are not treated as independent
observations. There is no wildcard, fuzzy match, arbitrary threshold, or
post-hoc pattern merging. Mirroring `direction` creates a separate pattern
ID, not a free extra interpretation.

## 4. Outcomes, selected/ignored/skips/failures

The search runs three predeclared label families independently:

1. `case_kind`: `selected_forecast`, `ignored_forecast`, `skip`,
   `false_signal`, `regime_transition`, and `failure` (failure is the row
   type, not inferred from a missing label).
2. Forecast persistence: the signed `price_change_bps` and its direction-
   aligned value, only where the artifact supplies it. This is observed price
   movement, not P&L, fills, fees, funding, or execution evidence.
3. Sequence/state continuation: the exact next linked `row_type`/producer
   category and explicit `prior_observed`/`current_features` values, only for
   sequence rows with both links present.

Every row is assigned exactly one result for every attempted pattern family:
`selected`, `ignored`, `skipped`, `failed`, `outcome_observed`,
`outcome_unknown`, `not_eligible`, or `search_error`. The output includes the
IDs of all matched and unmatched eligible rows, not only positives. Unknown
actual accounting is never converted to zero or to a failure.

## 5. Support and sample rules

These are descriptive power floors, not evidence of an edge:

- state pattern: at least 12 matched base rows, at least 8 rows with an
  observed label, and at least 3 distinct `underlying_id`/`case_id` episodes;
- transition pattern: at least 8 explicit transitions and at least 3
  episodes;
- sequence pattern: at least 8 matched sequences and at least 3 distinct
  prior/current episode chains;
- every reported calendar/regime cell needs at least 5 observed labels or is
  `underpowered`;
- a pattern with fewer than 3 independent dependence groups is never marked
  statistically testable. The accepted artifact has one group, so every
  result is `descriptive_only` and no p-value is an admission claim.

Support is counted before outcome filtering and reported separately from
outcome support. The same underlying episode cannot inflate an independent
sample count.

## 6. Common calendar and regime cuts

Cuts are global and calendar-aligned, never chosen per symbol or per pattern.
The required cut dimensions are:

- full sample;
- UTC hour buckets `00–05`, `06–11`, `12–17`, `18–23`;
- weekday groups `Mon–Thu` and `Fri–Sun`;
- calendar month;
- declared regime label, including `unknown`.

The cut clock is `features.as_of_ms` for point-in-time rows and the recorded
row clock for observation-only rows. A cell is emitted even when empty and is
marked `unobserved`; no nearby bar, neighboring month, or cross-symbol row
fills it. With the accepted artifact, only September 2026 and `unknown` have
meaningful coverage; this is a limitation, not a reason to collapse cuts.

## 7. Baselines and nulls

Each pattern is compared with all of the following, computed on the same
eligible rows and same common cut:

- unconditional label-frequency baseline;
- direction-only baseline for forecast persistence;
- calendar-only baseline for each calendar cut;
- family-only baseline (`forecast`, `journal`, `investigation`, or
  `sequence_link`).

The primary null preserves row counts, pattern support, episode sizes,
calendar cell, symbol, and dependence-group membership. It randomly circularly
shifts outcomes within each common calendar cell and episode block, with seed
`sha256(protocol_id + pattern_id + cut_id + label_family)`, 2,000 draws per
pattern/family/cut. A draw that cannot preserve the required support is
recorded as a failed null draw, not replaced. The exact number attempted,
successful, and failed is output.

For numeric persistence, the null shifts signed outcomes; it does not shuffle
feature values into the future. For categorical outcomes, it permutes labels
within the same cell. A second simple sanity null reverses the time order
within each block and is a falsification check, not a second opportunity to
select a candidate.

## 8. Multiple testing and fixed search budget

The search budget is fixed before execution:

- at most 32 numeric/categorical feature families;
- at most 96 state atoms after finite-value and support screening;
- at most 256 state patterns (including ablations);
- at most 64 transition patterns;
- at most 64 sequence patterns;
- at most 384 total unique `pattern_id`s;
- at most 2,000 null draws per attempted pattern/family/cut;
- at most 3 calendar/regime cut dimensions scored per pattern in addition to
  the full sample: hour, weekday, month, and regime are all reported, but
  only the first three nonempty dimensions in that fixed order spend null
  draws; empty/underpowered cells never spend draws;
- one deterministic lexicographic enumeration order, stopped at the budget.

The budget is a computational and look ledger. Every selected, ignored,
skipped, failed, underpowered, duplicate, rejected, and null-failed attempt
gets a receipt. No retry with a changed seed, threshold, cut, label, or
eligibility rule is allowed. A new budget requires a new protocol ID.

Within the frozen budget, report Benjamini–Hochberg q-values separately by
label family and pattern family using the full-sample primary metric. Because
the artifact has one dependence group, q-values are labelled descriptive and
cannot support admission or a claim of independent replication.

## 9. Ranking metric

Patterns are ranked within label family by a deterministic composite:

`rank = effect_size × sqrt(observed_support / max(1, support)) ×
        (1 - null_percentile)`,

with ties broken by lower null percentile, larger observed support, lower
pattern complexity, then `pattern_id`.

`effect_size` is predeclared per family: macro-F1 improvement over the
appropriate categorical baseline for `case_kind` and continuation labels;
absolute Hodges–Lehmann median shift in direction-aligned bps divided by the
baseline MAD plus one bps for persistence. Null percentile is the fraction of
successful null draws at least as favorable as observed. No PF, CAGR, Sharpe,
trade P&L, fees, funding, or execution metric is calculated from this
artifact.

The rank is for ordering work and freezing descriptive candidates only. It is
not a pass/fail strategy score.

## 10. Ablation and falsification

Every conjunction is compared with each one-atom deletion and its family-only
baseline. A pattern is not a historical candidate if removing an atom leaves
the metric unchanged within the predeclared tolerance, or if the sign reverses
in the full sample and in every nonempty common cut.

Required checks are:

- direction mirror: swap long/short interpretation;
- reversed-time null block;
- calendar-only and family-only baselines;
- leave-one-symbol-out descriptive stability;
- leave-one-episode-out stability;
- selected-vs-ignored contrast where both labels exist;
- skip/failure visibility check: removing skips/failures must not be allowed
  to improve a claimed result silently;
- sequence-link integrity and no-overlap audit;
- replay of the search from the search receipt produces byte-identical rows.

Any failed check is retained with reason and makes the candidate
`falsified_or_unstable`; it is not repaired by changing the pattern.

## 11. Stopping rules

Stop immediately, with a terminal search receipt, on any of:

- input hash, dataset version, cut, schema, replay result, or code manifest
  differs from the locked values;
- any row is read outside the accepted artifact or a missing value is
  fabricated;
- the pattern/null/look budget is exhausted;
- a deterministic enumeration reaches its end;
- a null family cannot meet its support-preservation rule;
- a row-link, clock, or dependence-group invariant fails;
- a caller requests strategy admission, Gate 2, live handoff, a research flag
  change, or a new historical look.

The normal completion result is `search_complete_descriptive`; an empty or
underpowered result is valid. There is no “search until a candidate appears.”

## 12. Candidate freeze format

A candidate is a frozen descriptive record, not a strategy:

```json
{
  "schema": "m3.2-historical-pattern-candidate.v1",
  "protocol_id": "...",
  "dataset_id": "m3.1-historical-discovery-2026-09-19",
  "dataset_version": "...",
  "pattern_id": "...",
  "pattern": {"family": "state|transition|sequence", "atoms": []},
  "label_family": "case_kind|persistence|continuation",
  "support": {"matched": 0, "observed": 0, "episodes": 0,
               "dependence_groups": 0},
  "cuts": {},
  "baseline": {},
  "null": {"seed": "...", "attempted": 0, "successful": 0},
  "metric": {},
  "rank": null,
  "ablations": {},
  "falsification": {},
  "status": "descriptive_candidate|underpowered|falsified_or_unstable|rejected",
  "strategy_admission": false,
  "gate2": false,
  "live_handoff": false,
  "research_referee": false,
  "research_handoff": false
}
```

The candidate file is immutable. A candidate is emitted only if it is within
the budget, has complete matched/unmatched accounting, passes replay, and is
not `underpowered` or `falsified_or_unstable`. The search may still emit the
latter statuses in the complete result ledger.

## 13. Exact gate into prospective shadow validation

Moving a frozen descriptive candidate into a new prospective shadow protocol
requires all conditions below, recorded in a separate owner-reviewed change:

1. the candidate has the exact freeze format above and `status=
   descriptive_candidate`; no candidate mutation or post-search tuning;
2. the historical search receipt shows input/cut/code hashes match and all
   ablation, null, link, and replay checks passed;
3. the candidate has at least 12 historical matches, 8 observed outcomes, and
   3 episodes, plus at least 3 nonempty common calendar/regime cells; it is
   still labelled descriptive because the current artifact has one dependence
   group;
4. the owner approves a new prospective declaration with a new calendar
   window, explicit universe, per-symbol receipts, exact feature clocks, and a
   coverage review proving accepted prospective sampling. The old artifact and
   cut remain unchanged;
5. the shadow runner is registered before the new window opens, uses the same
   canonical `pattern_id`, fixed features and fixed thresholds, records
   selected/ignored/skipped/failure/not-eligible outcomes and unknowns, and
   has an explicit stop barrier;
6. the runner is observation-only: no order, strategy-spec write, admission,
   risk change, Gate 2 evaluation, `research.referee` change,
   `research.handoff` change, or live handoff is permitted;
7. the new protocol is separately hashed and frozen, and the owner reviews
   the prospective shadow receipt before any further validation design.

Failure of any condition leaves the candidate historical-only and records the
reason. A candidate is never moved by a passing historical rank alone.

## Exact reuse and new implementation boundary

Reuse without behavioral change:

- `trader.cognition.historical_pit.historical_sufficiency()` for the locked
  sufficiency assertions and limitation reporting;
- `trader.cognition.dataset.replay()` and the artifact's embedded dataset
  version/replay hash for input integrity;
- `trader.cognition.contracts.load_input()`/`to_dict()` conventions for typed
  records, clocks, and missing-value handling;
- `trader.research.ledger.Ledger` only as a model for durable, transactional
  look receipts and immutable rows; do not write its strategy/referee tables;
- `trader.research.fdr` only for reporting the predeclared descriptive BH
  q-values, not for admission;
- `trader.research.portfolio_null` and `trader.strategy.null_baseline` are
  not reused for this dataset because they assume simulated trade fills and
  market-bar portfolios, which this artifact does not contain;
- existing canonical hashing helpers such as `stable_id` where their input
  contract is suitable and documented in the new implementation.

New code required:

- a read-only artifact loader that verifies the exact input contract and
  refuses other files;
- a frozen vocabulary/binning module for the allow-listed state atoms;
- deterministic state, transition, and sequence enumerators with budget
  receipts and complete selected/ignored/skip/failure accounting;
- common UTC calendar/regime cut builder;
- support-aware categorical/numeric scoring and the declared constrained
  null generator;
- dependence/episode accounting, ablation and falsification checks;
- immutable candidate-freeze writer and byte-identical replay verifier;
- focused tests proving no future reads, no fabricated missing values, no
  artifact mutation, budget exhaustion, deterministic seeds/order, and
  strategy/referee/handoff tables and flags remain untouched.

No existing M3.2 search runner may be invoked by this design. The existing
bar-based `trader.research.runner` is a different pipeline and must not be
pointed at this artifact.

## Risks and limitations

- One dependence group means no independent-market inference or honest
  population significance is available.
- One month and unknown regimes make regime/calendar stability weak or
  unavailable; empty cells are not evidence of absence.
- Retained retrospective rows are not complete population sampling; an
  ignored row is not proof that an underlying event did not occur.
- Investigation rows are classification-conditioned, so their rates cannot
  be interpreted as event prevalence.
- Forecast outcomes are observed price changes, not executable economics.
- Small support, overlap, and sequence dependence make rank ordering fragile.
- The fixed budget limits discoverable vocabulary and does not protect against
  all researcher degrees of freedom outside the protocol.

## Smallest implementation slice

Before any search, implement only:

1. `trader/cognition/m32_protocol.py`: locked artifact loader, canonical
   protocol constants, vocabulary, bins, cuts, pattern IDs, and support
   accounting;
2. `scripts/m32_protocol_check.py --artifact <frozen artifact>`: read-only
   validation plus a zero-search manifest showing the exact input hashes,
   available feature families, row accounting, and expected budget;
3. `tests/test_m32_protocol.py`: artifact immutability, exact input pin,
   deterministic canonicalization, common-cut behavior, missing-value
   handling, dependence accounting, and refusal of strategy/live/referee
   side effects;
4. this document's protocol ID and a short handoff entry after the check
   passes.

Do not implement the enumerator, null runner, candidate writer, or run any
M3.2 search in this slice. The next authorization after this slice must be an
explicit search-start decision.
