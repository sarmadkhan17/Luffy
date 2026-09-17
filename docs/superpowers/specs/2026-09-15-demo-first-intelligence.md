# Luffy intelligence — demo-first priorities

Owner: Astra/Codex (design/review); Claude (coding/fixes); Luna (assessment).
Status: intelligence design and read-only readiness assessment, not deployed.
User direction on 2026-09-15: all three capabilities, ordered strategy discovery
and evaluation, decision explanations, then broader market reasoning/learning.
Target: existing demo trading setup. No real-money launch requested.

## Outcome to build toward

Luffy turns a discovered pattern into a defensible decision:

> Here is the rule and the proposed reason it could work. Here is the evidence
> against it. This specific test would change the verdict. Here is why I may
> propose it for demo, why I must wait, or why I rejected it.

The intelligence objective is to improve the candidate-to-decision process.
Broad anomaly investigation is useful, but is not a prerequisite for the next
entry by an existing demo strategy.

## Current evidence (read-only snapshot, 2026-09-15 ~15:10 UTC)

- Kernel heartbeat/control state ACTIVE; configured research enabled, 4h only.
- Journal includes Donchian FIL long opened September 13 16:00 UTC, closed
  September 15 02:34 UTC, and HYPE short opened September 13 12:00 UTC, closed
  September 14 14:14 UTC. These are journal records, not a fresh venue audit.
- Two compiled specs have state `paper`: Donchian Breakout Trail and Funding
  Filtered Trend Pullback. A registry state alone does not establish validated
  evidence. Journal execution label `live` alone does not establish real-money
  venue use; this work targets the existing demo setup specified by the user.
- Today's journal decisions are HOLD; 13,975 inspected rows have no strategy
  signals recorded. Empty signal records do not prove every evaluator ran or
  every possible setup was absent. Many HOLD rows have no explanatory text.
- Discovery ledger: 117 survivors; research_candidates and research_tests empty.
- `research.referee=false`, `research.handoff=false`. No implemented research
  step produces `reason_passed`; the kernel handoff expects that state.
- The September 15 addition to the phase-3 plan already selected option 3,
  dependence-corrected consistency. Its reported incumbent calibration still
  fails the new gate, so the stop remains. This is not an unresolved choice
  between the older three options and not permission to relax a threshold.

References: `config.yaml` research settings; `trader/research/referee.py:gate1`;
`trader/kernel.py:_research_handoff`; phase-3 plan's final "Decision taken";
read-only SQL over cycles/decisions/trades/research tables in `data/luffy.db`.
The snapshot will age while the kernel continues operating.

## Priority 1 — candidate reasoning tied to testable decisions

### First deliverable: a candidate dossier and a strict thesis contract

Use an existing discovery survivor; freeze its hash, rule, geometry, source,
timeframe and discovery slice. Do not launch another broad search just to fill
this dossier. Discovery rank is scheduling evidence, not proof of admissibility.

The dossier has six parts:

1. **Observed pattern:** rule and discovery facts with exact evidence references;
   separate code availability, data coverage and statistical testability.
2. **Proposed mechanism:** a falsifiable proposal, with premises that are not
   observed marked explicitly. Price/volume alone cannot identify who traded.
3. **Rival explanations:** at least the shared-market-regime explanation, selection
   from many tried variants, and any specific data/provenance alternative that
   applies. Keep contradictory evidence visible.
4. **Novel consequences:** 2–4 frozen predictions in the existing thesis grammar
   (subset/condition, metric, relation and baseline). Repeated discovery metrics,
   entry-rule tautologies and unavailable observables are refused with reasons.
5. **Next test:** which claim it distinguishes, data needed, whether that evidence
   has already been seen, and the existing budget/gate it must pass.
6. **Verdict:** research_only, waiting_for_evidence, contradicted, or
   ready_for_admission_review. Each names its exact blocking condition and next
   action. The final state requires actual gate evidence, not persuasive prose.

The proposer sees discovery data and rule/ablation information only. It may see
that an external test passed, never the held-out numbers before freezing its
predictions. An LLM may propose the mechanism; deterministic validation and the
existing research evaluator test it. Outputs must cite supplied evidence IDs;
unrecognized facts become unsupported premises, not observations.

Do not replace the existing research gates with an LLM judge. Do not fill
`reason_passed` until the implemented thesis test meets its registered rule.
The current referee stop remains an upstream blocker even after a good thesis
contract exists. The current inspected history is development evidence and
cannot become an untouched test simply by renaming the slice.

### Worked discovery case, not an admission recommendation

Candidate `55243573515adc3b`, parts
`r_alts_z96>p75` and `r_vix_ret30>p75`, is a current discovery survivor.

- **Question:** is the combination contributing information beyond the shared
  alt-market state, and does that contribution survive outside discovery?
- **Proposal:** the volatility context may distinguish when an alt-market trend
  rule behaves differently. The observed conjunction does not establish this.
- **Rival:** one market-wide trend episode lifts many correlated symbols and
  gets counted repeatedly; another is selecting a lucky threshold combination.
- **Required evidence:** discovery ablation first, then predeclared incremental
  comparisons on untouched evidence under the dependence-respecting protocol.
  If ablation already answered a proposed prediction, it is not a novel thesis
  consequence and must not be sold as one.
- **Current verdict:** research_only. The ledger contains no recorded referee or
  thesis pass. No new metric is calculated from held-out data for this card.

This is the reasoning behavior to implement before asking the machine for more
candidates. The useful result may be a precise rejection or evidence request.

## Priority 2 — explain every trade and every wait

The same structured decision record should support both a brief explanation
and inspection of the evidence. It needs:

- strategy/rule/version and symbol eligibility;
- last fully closed signal bar and data freshness;
- each prerequisite's observed value and pass/fail/unknown result;
- emitted signal, or explicit reason no signal could be evaluated;
- decision layer's acceptance/refusal, and actual risk/order result if reached;
- what observable change would make the next decision different.

For today's inspected rows the honest summary is "HOLD; no strategy signal was
recorded; some rows also record a higher-timeframe veto." The journal does not
currently support inventing the exact failed entry predicate for each blank
HOLD row. Claude should add missing predicate/execution attribution through a
separately reviewed diagnostic change; Codex defines the explanation contract.

A readable card should answer "why now?", "why this rule?", "why this size?"
and "why no trade?" with evidence. It must distinguish a normal absent setup,
missing data, evaluator failure, policy refusal and order failure. A trade count
is not the objective and a low count alone does not justify loosening the rules.

## Priority 3 — market investigations and learning

Retain `2026-09-15-cognition-investigation.md` as the third-priority design:
trigger-relevant alternatives, discriminating observations, honest uncertainty,
and case memory visible only after its evidence became available. It should
later supply useful questions and experience to candidate reasoning.

Neither the 940-candle repair nor an anomaly hypothesis is a trading signal.
The original and repaired cognition runs remain fixed research artifacts.

## Claude handoff boundaries

- Claude retains coding and fixes; Codex continues reasoning design and review.
- Start implementation with the discovery-only dossier/thesis validator and
  synthetic demonstrations of a supported premise, an unsupported causal story,
  a tautological prediction, missing gate evidence, and a valid next-test request.
- The demo decision explanation work can proceed independently of the referee
  gate. Instrumentation must preserve decision semantics and existing changes.
- Keep all existing gate settings and runtime behavior while implementing and
  validating these contracts. Starting a new demo strategy requires an actual
  admission decision or a separately explicit experimental trading policy;
  existing-demo scope alone does not waive the recorded admission requirements.
- Any proposal to collect more independent eras must predeclare a separate
  evaluation. This design does not widen the completed cognition audit.

## Definition of useful progress

1. A concrete candidate dossier exposes a checkable claim, relevant rival,
   discriminating test and correct next action from the evidence available.
2. A user can explain an actual demo trade or wait from the same decision facts;
   unavailable facts are plainly identified.
3. A prior result changes later questions/assessments without hindsight leakage.

Measure these with paired fixtures and case review first. Claims that the system
predicts better or earns more require a separate prospective comparison. The
near-term delivery is useful strategy reasoning and decision visibility, while
existing eligible demo strategies continue operating.
