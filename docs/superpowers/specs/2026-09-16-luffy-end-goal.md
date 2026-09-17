# Luffy — owner end goal (authoritative requirements)

Date: 2026-09-16. Status: **authoritative owner requirements.** This document
records what the owner wants Luffy to become. It is **not** a claim about what
exists today; nothing here should be read as implemented unless separately
evidenced.

Two kinds of text appear below and are kept visually separate:

- **Owner requirement** — faithful transcription/normalization of the owner's
  stated goal. Substance is preserved; only wording and ordering are normalized.
- *Implementation interpretation* — engineering reading added for this repo
  (constraints, disambiguation, what does **not** follow). Marked in italics
  and prefixed *Interpretation:*. Owner may overrule any interpretation.

This supersedes narrower earlier framings of Luffy's objective (crypto-futures
strategy discovery as the identity; Gate 2 as the goal). Those remain valid as
*current implementation scope and as a subordinate research task*, not as the
end goal.

---

## 1. End goal

Luffy is **Jarvis specialized in trading**: a complete autonomous trader. It
continuously understands markets, discovers the best risk-adjusted
opportunities available to it, formulates and tests hypotheses, chooses how to
express them, executes safely, learns from outcomes, and continuously improves
its own trading intelligence.

The source of profit is irrelevant. Long, short, hedged, and holding cash when
there is no sufficient edge are all equally valid outcomes.

## 2. Economic survival

Luffy should eventually pay its own operating and living costs.

```
weekly_required_income = infrastructure + data + API/LLM + services
                         + other agreed operating costs
```

Evaluate this over **rolling weeks**. Luffy must **never force trades** to hit
an income requirement. It must assess whether opportunities show positive
expectancy, whether that expectancy is sufficient to cover costs, whether it is
robust after fees, slippage, funding and other costs, whether it is stable
across regimes, and whether the strategy population generates enough
opportunity without unnatural risk increase. When no genuine opportunity
exists, preserve capital.

Economic survival is a **viability constraint, not a trade-generation trigger**.

*Interpretation: cost inputs and any required-income figure come from owner
agreement and recorded costs; this document does not define a weekly target or
capital amount, and none should be invented.*

## 3. What Luffy is not

Not an EMA-crossover bot, not a fixed-indicator strategy, not independent
buy/sell signals, not majority-vote agents, not a prompt-driven LLM trade
decider, not random strategies traded immediately, not crypto-only, not
long-only.

Intelligence comes from market data **plus** quantitative methods, machine
learning, hypothesis reasoning, memory, experience, risk management and
execution quality together.

## 4. Universe: whole accessible, dynamic

The tradable/observable universe is the whole accessible universe and is
dynamic: crypto (BTC, ETH, SOL, other liquid assets) and, where the account,
jurisdiction and exchange support it, gold, silver, oil, equity/index exposures
and other TradFi instruments.

Cross-market relationships matter: BTC/ETH/alts, BTC dominance,
TOTAL1/TOTAL2/broader crypto structure, equities, DXY, Treasury yields/rates,
commodities, volatility measures and macro conditions.

Luffy should **discover** its tradable universe from live instrument and
exchange metadata rather than fixed assumptions, and must distinguish an
**observed reference market** from an **accessible executable instrument**.

*Interpretation: a reference series used as evidence does not imply an order can
be placed on it; executability is a separate, verified property.*

## 5. Attention allocation is the first intelligence problem

Luffy cannot analyze everything deeply. The pipeline is:

```
universe → cheap continuous scan → unusual events / anomalies / transitions
        → candidate ranking → deep investigation → hypotheses → trade or ignore
```

The cheap scan looks for unusual behavior, relative strength/weakness,
volatility/liquidity/volume/positioning anomalies, structural transitions,
cross-market divergence, unexpected behavior, catalysts and correlation
changes, and pre-move conditions.

The scan's output is **"this deserves attention"**, not "buy". It is **not**
chiefly a bullish ranking.

## 6. Why this asset

Luffy must distinguish whether a move is market-wide, sector rotation,
asset-specific, driven by derivatives leverage/positioning/liquidations, or
macro-driven/event-driven. The same price move can imply a different
opportunity depending on cause.

## 7. Questions Luffy must ask

What is happening; why might it be happening; who appears to be driving it;
what should happen next if the explanation is true; what evidence contradicts
it; what would falsify it; what historical analogues exist; how those analogues
resolved; whether payoff is sufficient after costs and risk; and what the best
expression of the trade is.

Separate **observed facts** from proposed causal or participant explanations;
treat those explanations as **hypotheses** until supported.

## 8. Competing hypotheses

Maintain competing explanations, e.g. breakout continuation, short squeeze,
liquidity-driven breakout, distribution into buyers, mean reversion,
cross-market repricing, and "unknown / insufficient evidence".

Each carries supporting evidence, contradicting evidence, expected
consequences, historical analogues, an estimated probability and an
invalidation condition. Probabilities update as evidence arrives; commit only
when a hypothesis is sufficiently supported.

Do **not** pretend an LLM-stated probability is calibrated.

*Interpretation: LLM-produced probabilities are labeled as unvalidated
subjective estimates unless a measured calibration record exists.*

## 9. Contradiction is informative

Conflicting evidence must not be averaged away. Example: price/trend bullish
while CVD weakens, OI is crowded, breadth is weak, relative strength
deteriorates and TOTAL2 does not confirm. The correct response is to
investigate participation divergence and consider a crowded breakout,
distribution, or trapped positioning.

## 10. World model

A **world model** sits between raw analysis and strategy generation: market and
per-asset state, regime, liquidity conditions, positioning, cross-market
relationships, transitions, active hypotheses, historical analogues and
expected next states.

Indicators are **evidence**, not state. Example of the intended abstraction:
"compression transitioning to expansion with rising participation but weak
cross-asset confirmation".

## 11. Automatic pattern discovery

Luffy should discover patterns rather than only be told them, using methods
such as clustering, regime detection, sequence models, change-point detection,
anomaly detection, Bayesian and tree models, feature-interaction search,
hidden-state models, representation learning and conditional-probability
estimation.

Targets include: states (conditions A+B+C), transitions (A→B), sequences,
relative relationships, failure conditions, and cross-market/macro→crypto
effects. Discovery must find **failure** conditions as well as profitable ones.

*Interpretation: the method list is a set of alternatives, not a mandate that
all be implemented.*

## 12. Surprise drives curiosity

Surprise sets research priority. Examples: BTC rising while alt breadth
collapses; price rising while participation falls; open interest building
without price movement; a correlation breakdown; a peer sharply outperforming;
a breakout failing immediately; an asset resilient to a bearish macro shock.

## 13. Attention itself learns

Luffy should learn which combinations of anomalies actually precede meaningful
opportunities. The **scanner evolves**, not only the strategies.

## 14. Strategy emergence

```
observation → repeated pattern → statistical edge → mechanism hypothesis
→ predicted consequences → validation → strategy genome → research
→ probation → live
```

The LLM contributes explanations, mutations and questions; quantitative testing
decides whether an edge exists.

*Interpretation: this diagram is preserved as owner vision. The "live" stage
describes the intended lifecycle; it is not a current authorization to deploy
live-money trading.*

## 15. Strategy population

Strategies are genomes carrying hypothesis, mechanism, market compatibility,
regime compatibility, entries, exits, invalidation, payoff profile, cost
sensitivity, lineage, performance and confidence.

They survive, mutate, specialize, combine, weaken, recover and retire. Past
success does not earn permanent trust.

*Interpretation: "genome" here is a conceptual contract. It does not authorize
a second, automatic admission path into trading.*

## 16. Direction and expression

Output space: long, short, pairs, relative value, hedges, options/convex
expressions where supported, and **no trade**.

Luffy optimizes the best instrument and expression, not just direction: a
bullish thesis may be best expressed in a different asset, gold may be
preferable to uncertain crypto, and shorting one asset against another may beat
either outright.

## 17. Portfolio intelligence

Decisions are portfolio-level: existing exposure, duplicated risk and shared
factors, marginal benefit of a new position, hedging benefit, false
diversification, and portfolio-level expected payoff. BTC + ETH + SOL + MSTR
may be one dominant risk-on factor, rather than four independent sources of
risk.

## 18. Deterministic risk constitution

Risk is deterministic code with **no LLM dependency**: maximum risk, portfolio
heat, sizing, leverage limits, stop rules and native exchange protection,
exchange reconciliation, daily circuit breakers, panic handling and execution
safety.

Intelligence proposes; the constitution rejects.

## 19. Initial components

Approximately **12–15 specialized components/agents**, as conceptual domains:

- **Market Intelligence** — Market Perception; Structure/Auction; Flow &
  Positioning; Volatility; Global/Cross-Asset; Fundamental/On-chain.
- **Cognition** — World Model; Hypothesis Engine; Pattern Discovery; Memory;
  Critic / Counter-Thesis.
- **Opportunity & Strategy** — Opportunity Ranking; Relative-Value / Trade
  Expression; Strategy Researcher.
- **Portfolio & Action** — Portfolio & Risk; Execution & Reconciliation.

*Interpretation: the count and list are the owner's framing, not a rigid
numerical requirement. Not all components are LLM agents, and the roles are
conceptual domains — they do not require separate processes.*

## 20. Allocation of work

- **Deterministic / quantitative:** data ingestion, indicators, state
  calculations, microstructure, risk, sizing, execution and reconciliation,
  portfolio optimization, backtesting, transaction-cost models.
- **Machine learning:** regimes, patterns, nonlinear relationships, state
  clustering, anomalies, outcome prediction, sequences, adaptive scoring.
- **LLM:** hypotheses, competing explanations, questions, unstructured
  information, mutation proposals, postmortems, explanations, documentation and
  owner interaction.

The LLM is **not** a calculator, **not** execution, and **not** a hard risk
authority.

## 21. Memory

A rich memory chain links market state + event + hypothesis + counter-hypothesis
+ evidence + decision + execution + outcome + regime + aftermath.

Memory types: episodic, pattern, strategy, failure, regime, cross-market,
owner-preference and doctrine memory.

*Interpretation: the desired evolving doctrine does not authorize rewriting
currently frozen data/doctrine artifacts (e.g. `data/doctrine.json`). Versioned
doctrine proposals are the acceptable mechanism.*

## 22. Learning from everything

Learn from executed trades, skipped trades, missed opportunities, false
signals, market events, failed and successful hypotheses, regime shifts,
strategy degradation and prediction errors. **A skipped setup is a learning
observation**, not an absence of data.

*Interpretation: counterfactual outcomes of non-trades must be labeled
simulated and must never be reported as actual PnL.*

## 23. Time horizons

- **Fast (ms–seconds):** prices, order book, flow, risk checks, positions,
  execution. **No LLM.**
- **Medium (seconds–minutes):** state updates, ranking, hypotheses, strategy
  selection, portfolio decisions. Quantitative + ML + bounded reasoning.
- **Slow (hours–days):** pattern discovery, strategy evolution, autopsies,
  research, doctrine updates, learning. LLM is most useful here.

## 24. Economic survival brain

```
gross PnL − fees − slippage − funding − data − infra − LLM − services
    = net economic contribution
```

Tracked alongside weekly/monthly required return, cost/PnL ratio, capital
efficiency, risk-adjusted profit, and probability of covering cost. Economic
pressure never overrides risk rules; a forced trade is already a failure of
survival logic.

*Interpretation: avoid double-counting slippage, funding and fees already
reflected in fills/PnL; distinguish actual cash costs from estimates, and
distinguish demo/hypothetical results from real income. Do not invent weekly
targets or capital amounts, and do not claim a calibrated coverage probability
without measured calibration.*

---

## Owner operating instructions

- Luffy (and agents working on it) **may research and change architecture and
  process** in service of this goal.
- Keep the **owner in the loop**: the owner must be able to know what Luffy is
  doing and why.
- Provide **comprehensive troubleshooting logging**.

---

## Implementation acceptance contract (future requirement — not a claim that it exists)

This section is a clearly labeled **acceptance contract for future
implementation work**. It describes what must eventually be true. It does not
assert that any of it is implemented today.

### A. Owner-readable narrative

Luffy should be able to report, in owner-readable form:

- what it is currently focused on;
- what it promoted or ignored in attention, and why;
- active hypotheses, contradicting evidence, confidence and confidence updates;
- why it traded or did not trade;
- why it chose a particular expression/instrument;
- any deterministic risk veto that was applied;
- execution and reconciliation results;
- outcomes and what was learned;
- degraded or unhealthy components;
- an economic cost breakdown;
- what changed in the system and why;
- meaningful progress and incident reports.

### B. Structured diagnostics

Structured logs with **correlation IDs** must let a reader trace:

```
event → evidence → hypothesis → experiment/strategy version → decision
      → order / fill / protection → outcome / postmortem
```

Records should carry: UTC event time **and** observation time; point-in-time
availability, provenance, freshness and missingness of inputs; code, model,
prompt, config and schema versions or safe hashes; references to inputs; reason
codes; risk checks applied; quantities; latencies, retries, errors and stack
traces; external API health; resource and token/cost usage; cancellations,
rejections and partial fills; attention drops, skips and unknowns; learning and
version changes; and owner control actions.

### C. Replay and honesty

Logs should make **replay/reconstruction feasible** from retained, versioned
inputs. They must not fabricate hidden LLM reasoning and must not require
private chain-of-thought: record observable evidence and a concise stated
rationale.

### D. Safety and hygiene

- Never log credentials, auth headers or secrets.
- Bounded retention, rotation and storage.
- Capture logging failures and drop counters; no silent loss of critical audit
  records.
- Never block fast safety actions on an LLM call or a log sink.
- Use redacted payload references where full payloads would be needed; avoid
  dumping everything.

---

## Relationship to current repo state

- Current implementation scope is **crypto futures on the demo setup**, with the
  recorded research stop in effect. That is scope, not identity.
- Gate 2 and the referee protocol are a **subordinate research task** under this
  goal, not the goal itself.
- Deterministic risk control, honest evidence standards, frozen research
  artifacts and the recorded operational stop remain binding while the design
  evolves toward this end goal.

Handoff: [end-goal handoff](../reports/2026-09-16-end-goal-handoff.md).
