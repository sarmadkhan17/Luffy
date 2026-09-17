# Luffy intelligence — investigations that change with evidence

Design: Astra/Codex. Implementation owner: Claude. Progress assessment: Luna.
Priority: third, after strategy discovery/evaluation and decision explanations,
per the user's demo-first direction. See `2026-09-15-demo-first-intelligence.md`.
Status: design and worked case; not implemented. Preserve cognition v1 and its
original/repaired replay artifacts. No live behavior or evaluation expansion.

## The next capability

Luffy should be able to say:

> This is what changed. These are the competing explanations. This observation
> would distinguish them. Here is what changed my assessment, and what earlier
> cases warn me about. This is what I still cannot know.

The next slice is an **investigation**, continuing across observations, with a
record of what evidence changed its assessment. An anomaly alert starts the
question; a completed price partition does not establish an explanation.

This is the observation-driven entrance to the existing Researcher design
(`2026-09-01-researcher-and-strategy-first-core-design.md`, Researcher section).
It produces RESEARCH, WAIT or ACQUIRE recommendations. It does not bypass the
research pipeline's blind thesis evaluation, confound tests or admission rules.

## Evidence for choosing this slice

Read-only inspection of the repaired replay (251 episodes):

| Dominant trigger | Episodes | Framed unknown |
|---|---:|---:|
| Volatility transition | 154 | 149 |
| Volume anomaly | 48 | 36 |
| Relative-return divergence | 49 | 0 |
| Total | 251 | 185 |

Every episode currently receives market_continuation, asset_divergence and
unknown, regardless of its trigger (`hypotheses.py:frame_episode`). Volume and
volatility can select an asset without supplying the evidence used to frame its
hypotheses. These 202 episodes need questions about the thing actually observed.

`replay.py:run` keeps pending outcomes and deduplication state. Its outcomes do
not enter subsequent attention or hypothesis construction. Thus the current
slice measures results but does not yet learn from them.

There is also a semantic mismatch: the asset-divergence statement says
"persists", but its test is absolute future relative magnitude. Of 81 confirmed
asset-divergence outcomes in this slice, 44 have a sign opposite the initial
relative divergence. That is allowed by v1's documented formula; it cannot
support a claim of same-direction persistence. Leave v1 intact and distinguish
continuation from reversal explicitly in the new catalog.

These observations identify capability gaps. They are not evidence for choosing
profitable thresholds. This window has been inspected and is development data.

## Architecture and ownership

    observation -> question -> competing predictions -> next discriminating test
                         ^                                 |
                         |                                 v
                   prior case memory <- result <- evidence update

Codex owns question design, evidence semantics, decision boundaries and review.
Claude owns implementation and mechanical fixes. Luna assesses whether the work
adds a reasoning capability, rather than only more records or prose.

Preserve v1 replay and public APIs. Build a separate offline investigation runner
with versioned records. Feed it explicit timestamped inputs and the frozen v1
attention decisions; do not change K, salience, membership or selected episodes.
The old traces are comparison artifacts and must never be rewritten.

## 1. Ask a question that matches the observation

Record primary trigger, all component values, source/time evidence IDs, and
which components the candidate explanations address. Unaddressed observations
remain explicit. Market breadth is context; it is not the explanation of every
anomaly. Volume alone does not identify participation, news or informed flow.

First catalog: one primary family per episode, determined by the existing
`dominant` field. Secondary triggers remain visible as unresolved questions;
they do not create an unbounded combinatorial search.

| Family | Competing observable predictions | What this cannot establish |
|---|---|---|
| Volume | anomaly persists; returns toward baseline; flips to the opposite anomaly | who traded or why |
| Volatility | transition persists; normalizes; flips to opposite transition | liquidations, news, positioning causes |
| Relative return | same-direction divergence; convergence; opposite-direction divergence | asset-specific causal mechanism |

A missing observation is an epistemic state (we cannot test), not a competing
hypothesis that wins whenever the others lose. Use prediction_met/not_met and
unresolved/not_testable. Reserve causal language for separately supported claims.

### Frozen measurement contract for the first prototype

These are proposed, uncalibrated definitions for testing the reasoning machinery,
not selected strategy parameters. Freeze the catalog before implementation.

- Keep v1's anchor, N=20, S=5 and H=5 bars and the full frozen cohort.
- Volume: freeze the prior-N mean and SD of log(1+volume) used at the anchor.
  Measure mean log(1+volume) over the H future bars, standardized by that same
  prior SD. This is a dimensionless anomaly score, not a calibrated z probability.
- Volatility: freeze the first-N return SD used at the anchor. Compute the SD of
  H future log returns. Use v1's log-ratio normalization with H in place of S.
- Relative return: use v1's exact-horizon cohort-relative forward score.
- Freeze initial sign from the relevant component. For volume/volatility use
  threshold 2 (existing min_salience default); relative return uses 1 (existing
  persist_z default). For final score x and initial sign s: same direction if
  s*x >= threshold; opposite if s*x <= -threshold; normalization otherwise.
- Initial zero, nonfinite/unusable scale or insufficient history => not_testable.
  Missing required future bars => unresolved, with exact missing keys. Volume and
  volatility require all H bars, not only the horizon endpoint. Relative return
  requires the original full frozen cohort's endpoint bars, never a subset.
- These categories cover measurable outcomes by construction. One winning
  category per episode is not predictive skill. Every issued alternative stays
  in the report; no retrospectively chosen winner counts as a forecast.

## 2. Keep an investigation open and update it honestly

An investigation records immutable `opened_at`, `episode_id`, `catalog_version`,
question, alternatives, frozen measurements, deadline and expected evidence.
Each later update appends an event with `as_of`, available evidence IDs, previous
assessment, new assessment and reason. Earlier events never change.

Use qualitative evidence states: untested, compatible, contradicted,
indistinguishable, unresolved. Probability remains null until independently
calibrated. Current unusual volume is compatible with BOTH persistence and a
one-off event; it is not discriminating evidence between them.

Before the deadline, record the newly observed path and remaining information
needed. Do not classify a partial H-bar sequence as the final test. At the
specified deadline, resolve using exact required bars as available then. A late
arrival can resolve an unresolved test later; record its actual resolution time.
It cannot rewrite a past update or teach an earlier investigation.

Separate two questions explicitly:

1. **Prediction:** which observable path matched the predeclared definition?
2. **Explanation:** what evidence distinguishes why it happened?

A matched price/volume path does not confirm news, liquidation or a participant's
intent. Explanations needing those inputs remain unassessed.

## 3. Choose the next evidence, including choosing to wait

Every active question names the next discriminating test, which alternatives it
could distinguish, required inputs, earliest usable time and availability.

- WAIT when the discriminator is a future observation already expected.
- ACQUIRE when a specific missing source could distinguish causal alternatives;
  name source, timestamp requirements and the claim it could test. This is a
  recommendation only; no connector calls in the first offline slice.
- RESEARCH when the existing prefix supports a mechanical test of a new claim;
  the claim must be frozen before the held-out result is available.
- UNASSESSABLE when no available or specified feasible discriminator exists.

First prototype budgets: at most 3 alternatives and 1 next-evidence action per
investigation update. An existing open investigation receives updates rather
than another alert for the same episode. Deadline resolution does not force a
new explanation or a trade.

## 4. Turn outcomes into usable memory

Store immutable case records: trigger family/sign, catalog/config, timeframe,
cohort rule, source/provenance class, prediction definitions, observations,
resolved results, resolution time and remaining causal uncertainty.

At investigation time T, retrieve at most 5 most recent compatible completed
cases with `resolved_at <= T`, stable tie-breaking, exact same catalog/config,
family/sign/timeframe and provenance class. Show their identities and the reason
for inclusion. Never retrieve an outcome solely because its market deadline
passed if it had not yet been observed/resolved. Never mix original and repaired
versions of one episode as two independent examples.

Memory changes the next dossier concretely: show prior matching and opposing
outcomes, unresolved exclusions and prior failed causal claims; state what must
be tested again instead of presenting an old story as new knowledge. A prior
failed explanation must be disclosed when re-proposed under the same claim ID.
Its scope/version matters; one failure cannot ban all future regimes.

This is the first learning capability: explicit experience changes the questions
and cautions Luffy carries forward. It is not yet calibrated probability learning
or an automatic change to thresholds. Sample counts are descriptive; correlated
assets and overlapping horizons are not independent trials.

## Worked dossier — using only the selected decision's evidence

1000PEPE/USDT, 2026-08-31 04:00 UTC,
episode `ep_e9dd17980836eda4` in the repaired trace:

- **Noticed:** volatility-transition score +3.589; volume anomaly +0.786;
  relative-return divergence -1.516. Market breadth is 15.87% up, market score
  -0.667, and broad=false under the frozen rule. Participation is missing.
- **Question:** is this volatility expansion sustained or a short disturbance?
- **Alternatives:** expansion persists, normalizes, or changes to compression.
  None is favored merely because the initial expansion was observed.
- **Next test:** observe the complete next five 4h return bars, ending
  2026-09-01 00:00 UTC, against the frozen baseline volatility. WAIT until those
  bars are available; record each intermediate observation without moving the goal.
- **Unknown:** whether liquidation, news or positioning caused the expansion.
  OHLCV cannot adjudicate those causes. No claim about order direction follows.
- **Memory:** only compatible cases actually resolved by 04:00 UTC may be used.
  This worked card does not inspect later outcomes or claim a learned probability.

Its current v1 framing is unknown/no_discriminating_evidence. The proposed
investigation supplies a testable next question without pretending the cause is
known. The example is an illustration of the contract, not an independent test.

## Acceptance: demonstrate intelligence, not just passing schemas

Claude's first implementation should include these paired demonstrations:

1. **Trigger relevance:** volume-only and volatility-only alerts produce questions
   and outcome measurements for those observables. A reversed relative move does
   not satisfy same-direction persistence. Unknown is not counted as a forecast.
2. **Evidence-driven change:** two identical prefixes yield identical dossiers;
   append distinguishing evidence and only subsequent updates differ. Change
   irrelevant prose and the structured assessment stays unchanged.
3. **Honest ignorance:** missing participation never supports a liquidation claim;
   missing future bars produce WAIT/unresolved; identical predictions are marked
   indistinguishable, not arbitrarily ranked.
4. **Memory changes reasoning:** a case available before T appears in the next
   matching dossier with its result and caution. Move its resolution after T and
   it disappears. No cross-version duplicate cases or hindsight retrieval.
5. **No hidden forecast score:** report every issued alternative and no-favorite
   decisions. Improvement means demonstrated relevance, useful discriminators,
   correct updates and reusable experience; fewer unknown labels alone is not a
   success metric. Predictive improvement requires a separately predeclared,
   untouched chronological evaluation with a no-memory comparison.

Use synthetic fixtures first. The current repaired window may demonstrate
behavior only; it cannot validate a design derived by inspecting it. Keep the
114-test baseline and all historical artifacts unchanged. The coding handoff is
the separate offline runner, event contracts and these demonstrations; changes to
collection, trading, strategy admission and live behavior are outside this slice.
