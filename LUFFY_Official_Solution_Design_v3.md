# LUFFY Official Solution Design (SD) v3.0

**Status:** Official Target Architecture - Freeze Candidate  
**Purpose:** Single build authority for AI development agents  
**Target:** Autonomous multi-market trading intelligence and execution system  
**Date:** 2026-09-19

> This document defines the LUFFY we want to build. It is not a manual for the current codebase. AI agents are expected to read the codebase themselves. Existing implementation appears only in the final migration section so agents can measure progress against the target.

---

# 1. System Mission / End Goal

LUFFY is an autonomous multi-market trading intelligence system whose primary economic objective is to **maximize absolute profit within owner-defined risk limits**.

LUFFY must continuously:

1. observe markets and relevant external information;
2. discover which markets, mechanisms, anomalies, or questions deserve attention;
3. maintain a structured world model of the financial environment;
4. generate competing hypotheses and predictive ideas;
5. research those ideas using market data and external evidence;
6. falsify weak ideas rather than merely confirming them;
7. convert validated opportunities into executable strategies;
8. choose position size and capital allocation globally across the portfolio;
9. execute safely through deterministic risk and execution systems;
10. learn from executed trades, rejected trades, missed opportunities, failed research, execution mistakes, and regime shifts;
11. preserve failed knowledge so it does not repeatedly rediscover the same dead ends;
12. explain its actions and uncertainty from exact internal evidence;
13. detect persistent capability gaps and raise deterministic alerts for engineering review;
14. continuously improve its trading intelligence without continuously changing its own production architecture.

LUFFY is not limited to crypto, Binance, current agents, current indicators, or current strategies. It must be architected to extend to any market that is legally and technically accessible and for which valid data, research, portfolio, risk, and execution support exists.

LUFFY deliberately avoids HFT/microsecond architecture. The target domain is event-driven trading from seconds through minutes, hours, days, and longer horizons where research quality, portfolio construction, execution quality, and risk control dominate raw instruction latency.

---

# 2. Owner Decisions That Are Binding

These are product decisions, not implementation suggestions.

## 2.1 Objective

Primary optimization objective: **maximize expected absolute profit** subject to hard risk constraints and capital-survival rules.

Risk is a constraint, not the objective function. LUFFY must not maximize Sharpe at the expense of materially lower absolute profit unless the owner later changes the objective.

## 2.2 Market scope

Ultimate scope: any legally/technically accessible market, including where appropriate:

- crypto spot and derivatives;
- equities;
- index products;
- futures;
- commodities;
- FX;
- rates;
- options only after dedicated pricing/Greeks/volatility/risk support exists;
- additional market types added through adapters and validation.

Availability is discovered from venue capability data, never assumed from symbol names or marketing materials.

## 2.3 Capital range

The architecture must support progression from approximately **$2,000 to $100,000+** without redesigning the intelligence model. Capacity, liquidity, margin, and market impact become progressively more important as capital scales.

## 2.4 Autonomy

Within owner-defined limits LUFFY may autonomously:

- create strategies;
- test strategies;
- retire strategies;
- reduce or increase strategy allocation;
- reallocate capital between markets and strategies;
- create research questions;
- discover new research sources;
- add new data sources that are free or already approved;
- create/reconfigure intelligence components that fit the approved architecture;
- recalibrate models and thresholds where the SD explicitly allows adaptation.

A newly validated strategy requires **owner approval before its first real-money deployment**. After approval, LUFFY may manage its allocation automatically within risk limits.

## 2.5 Architecture and production-code changes

LUFFY does not run a periodic LLM that asks whether it should rewrite itself.

Instead, deterministic monitoring detects persistent capability gaps such as repeated missing data, untestable hypotheses, replay/live mismatch, unacceptable latency, execution faults, or unsupported exposure models. These become alerts with evidence.

Architecture/code changes are then designed by development agents and approved by the owner. LUFFY itself does not silently modify production architecture.

## 2.6 Risk authority

Risk-limit changes require owner approval. LUFFY may recommend changes but cannot expand hard risk boundaries itself.

Absolute account drawdown hard stop: **20%** from high-water mark, owner-configurable.

Capital-preservation mode is required before the catastrophe limit is reached when evidence indicates abnormal risk or system uncertainty.

## 2.7 Research/data spending

Paid research/data budget target: **$100-$300/month only when LUFFY earns enough to justify it**.

A paid source must justify:

- what capability or hypothesis it unlocks;
- why existing sources are insufficient;
- expected economic/research value;
- ongoing cost.

## 2.8 Machine learning

Advanced machine-learning systems are **not a required initial architecture**. Statistical/quantitative methods and deterministic models are preferred first. ML may be added later through an approved SD revision when a measurable problem warrants it.

---

# 3. Simplicity and Complexity Budget

LUFFY must be sophisticated in reasoning but simple in implementation wherever possible.

Complexity is a cost. Every new agent, process, database, service, MCP, paid data feed, recurrent LLM call, or model increases:

- failure modes;
- maintenance burden;
- latency;
- engineering difficulty;
- observability requirements;
- cost;
- probability of contradictory behavior.

Therefore use this escalation ladder:

`deterministic rule -> statistical method -> quantitative model -> LLM reasoning -> heavier learning system only if necessary`

Engineering preference:

`function call > new service`  
`existing process > new process`  
`SQLite/local store > distributed database unless scale proves otherwise`  
`deterministic monitoring > LLM monitoring`  
`structured memory > vector/semantic guessing`  
`cheap source filter > sending whole documents to an LLM`

A proposed component must answer:

1. what concrete problem does it solve?
2. why can the existing architecture not solve it?
3. how will value be measured?
4. what is the operational/LLM/data cost?
5. what is the rollback path?

---

# 4. Target Architecture at a Glance

The target system is a closed event-driven loop:

```text
External Markets + Financial World
             |
             v
Data Acquisition + Provenance
             |
             v
Perception / Measurement
             |
             v
Attention Engine --------------------+
             |                        |
             v                        |
Hierarchical World Model             |
             |                        |
             v                        |
Hypothesis / Opportunity Engine      |
             |                        |
             +<--> Critic/Falsifier  |
             |                        |
             v                        |
Research Intelligence ---------------+
             |
             v
Validated Opportunity / Strategy
             |
             v
Capital Allocation + Portfolio Optimizer
             |
             v
Deterministic Risk Authority
             |
             v
Execution + Protection + Reconciliation
             |
             v
Outcome / Attribution / Replay
             |
             v
Memory + Learning
             |
             +-----------------------> World Model / Research

Monitoring & Alerts observe every layer.
Operator/Chat explains and controls through typed interfaces.
```

The architecture is logically layered but does **not** require one software service per box. In the initial implementation, most layers should remain Python modules inside a small number of processes.

---

# 5. Runtime and Infrastructure Architecture

## 5.1 Compute philosophy

The SD does not impose a fixed CPU/GPU ceiling. Compute is provisioned according to demonstrated need and owner preference.

However, LUFFY must separate workloads logically into:

### Live Core

Must remain reliable, bounded, deterministic, and protected from research load.

Responsibilities:

- live market state;
- strategy evaluation;
- portfolio/risk decision;
- execution;
- protective order management;
- reconciliation;
- health monitoring.

### Research Compute

May use additional CPU, processes, machines, or cloud resources when justified.

Responsibilities:

- backtesting;
- null simulations;
- large universe research;
- literature/document analysis;
- expensive statistical validation;
- future ML training if approved.

Research must never starve the live core.

## 5.2 Deployment principle

Begin with the smallest reliable topology: one live kernel process plus operator/dashboard process if needed, local durable databases, and background workers where safe.

Scale out only when measured resource contention, data volume, or availability requirements justify it.

## 5.3 Technology baseline

Primary language: **Python**.

Baseline technology classes:

- Python 3.x;
- NumPy for numerical arrays;
- pandas/Polars for tabular/time-series work as appropriate;
- SciPy or equivalent tested numerical/statistical routines;
- Numba/vectorization where profiling shows value;
- FastAPI for operator/API layer;
- WebSocket for live dashboard updates;
- GraphQL only where it materially simplifies operator/agent querying;
- SQLite/WAL initially for operational truth;
- Parquet for analytical bulk history where useful;
- YAML/Markdown for structured human-readable semantic memory;
- pytest for deterministic tests.

A future language transition is not prescribed. If Python becomes a measured blocker, a separate transition design is created at that time.

---

# 6. Core Data Architecture

## 6.1 Data classes

LUFFY distinguishes:

1. market observations;
2. reference/macro observations;
3. derived features;
4. world-state facts;
5. hypotheses;
6. research evidence;
7. strategies;
8. opportunities;
9. portfolio state;
10. risk decisions;
11. orders/fills;
12. outcomes;
13. semantic knowledge;
14. health/alert events.

## 6.2 Point-in-time contract

For any datum `x`, maintain where relevant:

- event timestamp `t_event`;
- time it became knowable `t_available`;
- time LUFFY received it `t_received`;
- source;
- transform version;
- quality state;
- freshness.

A model evaluated at time `t` may consume only values with `t_available <= t`.

## 6.3 Missing-data rule

Missing, stale, unsupported, or untrusted data must not be converted to numeric zero.

Quality states:

`VALID | STALE | MISSING | SUSPECT | REPAIRED | UNSUPPORTED`

Downstream logic must explicitly decide what each quality state permits.

## 6.4 Canonical asset/instrument model

Separate the economic asset from its specific tradable representation.

`Asset`: BTC, gold, crude oil, Apple equity, USD, etc.

`Instrument`: Binance BTC perpetual, BTC spot, CME future, ETF, reference index, etc.

Minimum instrument metadata:

```text
instrument_id
asset_id
venue
venue_symbol
asset_class
market_type
quote/settlement asset
contract multiplier
expiry if any
tradable/reference_only
shortable
margin model
minimum order constraints
fee model
market hours
capability version
```

All internal portfolio/risk logic uses canonical IDs rather than raw venue strings.

---

# 7. Market Perception Layer

Perception measures the market. It does not decide whether to trade.

The system should support mechanism families rather than a fixed list of indicators.

## 7.1 Price/structure

Possible measurements:

- returns `r_t = ln(P_t / P_{t-1})`;
- rolling high/low structure;
- swing points;
- break/reclaim behavior;
- gap/displacement;
- range compression/expansion;
- support/resistance persistence;
- efficiency ratio;
- breakout continuation/failure.

## 7.2 Momentum/trend

Candidate measurements:

- EMA/MA slopes and separation;
- ADX/DMI;
- rate of change;
- directional persistence;
- variance ratio;
- autocorrelation;
- trend efficiency;
- cross-sectional momentum;
- acceleration/deceleration;
- change-point detection where useful.

## 7.3 Value/mean reversion

Candidate measurements:

- VWAP/anchored VWAP deviation;
- rolling z-score;
- volatility-normalized displacement;
- residual from reference/factor model;
- short-horizon reversion after liquidity events.

Z-score:

`z_t = (x_t - mean_t) / std_t`

A z-score is not itself an edge. It becomes a candidate mechanism only after conditional forward-return validation.

## 7.4 Volatility

Measurements may include:

- realized volatility;
- ATR normalized by price;
- volatility-of-volatility;
- range expansion;
- downside/upside realized volatility;
- volatility clustering;
- jump/extreme-move indicators;
- future options/IV measures only when options architecture exists.

## 7.5 Flow / market microstructure

Where data supports it:

- aggressor imbalance;
- taker buy/sell share;
- order-book imbalance;
- spread;
- depth;
- trade intensity;
- liquidity depletion/replenishment;
- absorption proxies;
- liquidation flows.

Historical testing requires stored point-in-time microstructure data. Current snapshot-only order books must not be used to claim historical evidence.

## 7.6 Positioning/derivatives

Candidate measures:

- funding level and change;
- open-interest level/change/acceleration;
- long-short ratios;
- basis;
- liquidation intensity;
- positioning-price divergences.

## 7.7 Cross-market context

LUFFY must measure time-varying relationships rather than assume them.

Methods may include:

- rolling correlation;
- beta;
- partial correlation where justified;
- lead-lag correlation;
- relative strength;
- breadth;
- dispersion;
- factor exposure;
- cointegration only when stationarity assumptions are tested.

No relationship is permanent doctrine merely because it existed historically.

---

# 8. Attention Engine

The Attention Engine answers:

> What deserves investigation now?

It does not answer:

> What should we trade?

## 8.1 Event-driven triggers

Attention may be triggered by:

- unusual return/volume/volatility;
- regime transition;
- correlation breakdown;
- positioning extreme;
- liquidity change;
- strategy degradation;
- unexplained portfolio loss;
- repeated rejected opportunities;
- contradiction in world model;
- new external research claim;
- owner question;
- new data becoming available.

## 8.2 Priority

When several items compete, prioritize the most urgent/valuable first.

A practical score can use normalized components:

`A = w1*economic_relevance + w2*novelty + w3*urgency + w4*uncertainty_reduction + w5*portfolio_impact - w6*research_cost`

Weights must be calibrated; the formula is a design template, not a permanently hardcoded truth.

## 8.3 Cost control

Low-value research may be paused when LLM/data/compute cost rises.

---

# 9. Hierarchical World Model

LUFFY maintains a structured, multi-horizon model of the financial environment.

Hierarchy:

```text
Global financial state
    -> Asset-class state
        -> Sector/group state
            -> Instrument state
    -> Cross-market relationship state
```

Horizons may include:

- short-term;
- intraday;
- swing;
- structural.

A market may simultaneously be weak short-term and strong on a longer horizon. LUFFY must preserve such contradictions rather than force a single label.

## 9.1 World-state dimensions

At minimum where relevant:

- trend/structure;
- volatility;
- liquidity;
- positioning;
- breadth;
- dispersion;
- macro/event context;
- stress;
- cross-market relationships;
- data health;
- portfolio exposure.

## 9.2 Confidence

Each world-state claim stores confidence and evidence. Confidence changes with supporting/contradicting evidence.

Knowledge is not automatically deleted because it becomes old; instead current confidence/validity is updated while history remains available.

## 9.3 Strategy access

Strategies may query the world model directly through a stable typed interface. They must not scrape arbitrary internal state or use unversioned narrative text.

---

# 10. Hypothesis and Predictive Opportunity Engine

LUFFY may generate causal/mechanistic hypotheses or purely predictive hypotheses. Causality is not required if predictive evidence is robust and economically useful.

A hypothesis contains:

```text
id
claim
scope
expected direction/effect
required observables
evidence_for
evidence_against
confidence
research status
source/provenance
related world-state conditions
candidate strategy expression
```

Explicit natural-language invalidation text is not mandatory for every hypothesis, but every executable strategy requires deterministic entry/exit/risk behavior and measurable failure criteria.

Multiple competing hypotheses about the same situation are allowed and encouraged.

---

# 11. Critic / Falsification System

The system must actively search for reasons an apparent edge may be false.

Required checks where relevant:

- market beta/drift;
- selection bias;
- multiple comparisons;
- insufficient sample size;
- correlated-symbol pseudo-replication;
- look-ahead;
- survivorship;
- stale/missing data;
- transaction costs;
- regime concentration;
- parameter instability;
- data-vendor artifacts;
- venue-specific effects;
- capacity/slippage degradation;
- alternative explanations.

A negative/inconclusive result is a first-class result, not a failed pipeline run.

---

# 12. Autonomous Research Intelligence

The old model of a scraper reading a static list of websites is insufficient.

Research begins from a question, not a URL.

## 12.1 Research flow

```text
Anomaly / loss / contradiction / owner question / knowledge gap
                         |
                         v
                  Research Question
                         |
                         v
                   Research Planner
                         |
                         v
                    Source Router
                         |
             +-----------+-----------+
             |                       |
       Internal data            External evidence
             |                       |
             +-----------+-----------+
                         |
                    Evidence Bank
                         |
                         v
               Experiment / Falsifier
                         |
                         v
           Supported / Refuted / Inconclusive
```

## 12.2 Research question generation

LUFFY automatically creates research questions from:

- anomalies;
- losing clusters;
- strategy decay;
- unexpected correlations;
- missed opportunities;
- contradictory signals;
- capability/data gaps;
- external claims;
- owner questions.

Research is **event-driven**, not a permanent queue processed just because CPU is idle.

## 12.3 Source Router

The router maps question type to likely authoritative source classes.

Examples:

- exchange mechanics -> exchange documentation;
- macro/rates -> central banks, FRED, BIS, IMF;
- company fundamentals -> filings and issuer sources;
- academic factors -> arXiv/SSRN/Semantic Scholar/Crossref/universities;
- crypto derivatives -> exchanges/derivatives providers;
- on-chain -> chain/indexer/on-chain providers;
- market ideas -> specialist research/TradingView/quant communities;
- breaking events -> reputable news/official announcements.

## 12.4 Source Registry

Each source records:

```text
source_id
type
domains
primary/secondary
credibility class
cost
access method
historical depth
rate limits
known weaknesses
```

LUFFY may discover a new source. Paid-source adoption requires owner approval and the cost/value justification defined earlier.

## 12.5 LLM cost discipline

Never send the research web to an LLM wholesale.

Pipeline:

`search metadata -> deterministic filter -> deduplicate -> cheap relevance scoring -> extract relevant passages -> LLM only on high-value evidence`

LLM calls are cached by source hash + task + prompt version where possible.

## 12.6 Research Bank

The Research Bank stores structured research objects, not just bookmarks.

A research object includes:

- question;
- sources;
- extracted claims;
- supporting evidence;
- contradictory evidence;
- experiments;
- conclusions;
- limitations;
- next questions;
- cost;
- status.

Failed hypotheses remain stored so LUFFY does not repeat identical research unless genuinely new evidence/data appears.

---

# 13. Quantitative Research Framework

Every quantitative claim must define the null and what would falsify the claim.

## 13.1 Core return definitions

Log return:

`r_t = ln(P_t / P_{t-1})`

Simple return:

`R_t = P_t / P_{t-1} - 1`

Trade P&L must include actual or conservative estimates of:

`net_pnl = gross_pnl - fees - slippage - funding/borrow - other execution costs`

## 13.2 Profit factor

`PF = gross_profit / abs(gross_loss)`

PF cannot be used alone as evidence of timing edge because exit geometry and market drift can create apparently favorable PF.

## 13.3 Expectancy

For trade-level normalized return `x`:

`E[x] = p_win*E[x|win] + (1-p_win)*E[x|loss]`

Expected absolute economic contribution should be measured at portfolio/account level, not just average trade.

## 13.4 Drawdown

For equity curve `E_t`:

`peak_t = max_{u <= t} E_u`

`DD_t = (peak_t - E_t) / peak_t`

`MDD = max_t DD_t`

Hard owner stop: `MDD >= 20%` => no new risk; enter halt/capital-preservation behavior as defined by risk state.

## 13.5 Null models

A research result needs a null that preserves irrelevant structure while destroying the claimed informational relationship.

Examples:

- circular/time-shift signal null;
- common cross-symbol rotation for market-wide mechanisms;
- permutation/bootstrap where assumptions are appropriate;
- matched regime control;
- always-long/always-short/control strategy where drift is a concern.

The null must be justified per mechanism.

## 13.6 Empirical p-value

For `B` null draws and statistic `T` where larger is better:

`p = (1 + count(T_null >= T_actual)) / (B + 1)`

Never report a p-value more precise than the number of draws permits.

## 13.7 Cross-market dependence

Symbol-level observations are often dependent.

Where evidence is aggregated across correlated markets, use one or more of:

- common-factor/common-rotation null;
- cluster bootstrap;
- effective sample size;
- hierarchical modeling if later justified.

A simple design-effect approximation may be used when empirically calibrated:

`n_eff = n / (1 + (n-1)*rho_bar)`

where `rho_bar` is a measured average dependence statistic. It must not be assumed.

## 13.8 Multiple testing

Research discovery creates repeated hypotheses. False discovery control is required when many candidates are tested.

Approved baseline techniques may include:

- separate discovery and held-out stages;
- family-wise corrections for small fixed sets;
- online FDR such as LORD++ where sequential testing is appropriate.

The production admission rule must be calibrated by simulation/control tests, not chosen because a familiar alpha such as 0.05 sounds standard.

## 13.9 Walk-forward validation

Preferred sequence:

`discovery -> validation/held-out market -> held-out era -> shadow/forward evidence`

Avoid random IID splits for time-series strategies unless the question specifically justifies them.

Where labels overlap or future windows contaminate adjacent samples, use purging/embargo as appropriate.

## 13.10 Ablation and parameter stability

A strategy improvement must demonstrate that added conditions contribute economically.

Required comparisons should include where relevant:

- baseline vs added filter;
- parameter neighborhood/sensitivity surface;
- removal of each component;
- alternate cost assumptions;
- universe changes;
- regime changes.

Do not accept a filter merely because PF increases while compounded account return collapses.

## 13.11 Statistical power

`UNTESTED` is different from `FAILED`.

If the sample cannot detect a known control or minimum economically relevant effect, the research result is underpowered and must not be interpreted as no-edge.

---

# 14. Strategy Architecture

## 14.1 Strategy definition

A strategy is an executable, testable mapping from approved information to trade intent.

Minimum fields:

```text
strategy_id
version
hypothesis/research references
instrument/universe rules
timeframe/horizon
entry logic
exit logic
risk geometry
data requirements
world-model requirements
state
validated evidence
capacity estimate
```

## 14.2 Single creation path

There is one authoritative path:

`research/idea -> strategy specification -> quantitative validation -> shadow/paper -> owner approval for first real deployment -> active`

No scraper, LLM, dashboard, or legacy component may directly inject an unvalidated strategy into live eligibility.

## 14.3 Strategy states

`PROPOSED -> RESEARCH -> VALIDATED -> SHADOW/PAPER -> APPROVAL_REQUIRED -> ACTIVE -> DEGRADED -> RETIRED`

## 14.4 Strategy access to world model

Strategies may query current structured world-state features. Backtests must have point-in-time historical equivalents; otherwise that dependency is live-only and cannot be claimed as historically validated.

## 14.5 Strategy decay

LUFFY may automatically reduce or stop strategy allocation when evidence weakens.

Retirement/demotion logic must distinguish:

- genuine deterioration;
- insufficient recent opportunities;
- temporary regime absence;
- data failure.

---

# 15. Opportunity and Expression Decision

An opportunity is not automatically a trade.

For each executable candidate, estimate:

- expected net profit;
- evidence strength;
- data quality;
- regime fit;
- liquidity/execution quality;
- capacity;
- portfolio interaction;
- uncertainty.

## 15.1 Required economic edge

A new position requires positive expected net value after expected costs and an uncertainty reserve.

Conceptually:

`EV_net = EV_gross - fees - slippage - funding_or_borrow - uncertainty_reserve`

Trade only when `EV_net > 0` with sufficient calibrated confidence.

Exact uncertainty-reserve estimation is a quant calibration problem, not an arbitrary constant.

## 15.2 Confidence decomposition

Do not use one unexplained LLM confidence number.

At minimum retain:

- evidence strength;
- data quality;
- strategy reliability;
- regime fit;
- execution/liquidity confidence.

A calibrated composite score may be produced for sizing/ranking, but components remain visible.

## 15.3 Position expression

Current target supports directional single-instrument positions as the primary unit.

Multi-leg/pair/basket expressions are allowed as a future capability only when they clearly add value and the dedicated execution/risk architecture is implemented. They must not complicate the initial system merely because they are theoretically attractive.

## 15.4 Cash

Cash/no-trade is an explicit candidate. It wins whenever no opportunity offers sufficiently positive expected value after costs and uncertainty.

---

# 16. Global Capital Allocation and Portfolio Optimization

LUFFY optimizes the whole portfolio, not individual trades independently.

Objective: maximize expected absolute profit subject to owner risk constraints, capacity, liquidity, margin, and evidence quality.

## 16.1 Opportunity score

A practical ranking can combine:

- expected net profit;
- evidence strength;
- liquidity;
- capital efficiency;
- portfolio diversification/concentration effect;
- strategy reliability.

No single factor is permanently privileged except the owner objective and hard risk constraints.

## 16.2 Common-factor exposure

LUFFY must detect when different symbols/strategies are effectively the same bet.

Methods may include:

- rolling correlations;
- factor betas;
- PCA/factor decomposition where stable enough;
- clustering;
- scenario co-movement;
- shared strategy/mechanism lineage.

If multiple strategies depend on the same latent factor, they may be treated as one risk cluster and reduced together.

## 16.3 Capacity

Each instrument/strategy should eventually maintain a capacity estimate based on:

- spread;
- depth;
- volume;
- expected market impact;
- turnover;
- historical slippage;
- venue limits.

Scaling stops before marginal size destroys expected edge.

## 16.4 Opportunity cost

When capital/margin is constrained, LUFFY may reduce or close a weaker position to fund a stronger one if expected incremental benefit after switching cost and portfolio impact is positive.

## 16.5 Cash position

Unused capital is an explicit portfolio allocation, not an error condition.

## 16.6 Re-optimization cadence

Portfolio optimization is event-driven, not forced every cycle.

Trigger on material events such as:

- new opportunity;
- exit;
- strategy confidence change;
- regime/world-state change;
- risk-state change;
- material correlation/factor shift;
- capital change.

---

# 17. Deterministic Risk Authority

Risk is deterministic and cannot be bypassed by LLMs or strategies.

## 17.1 Hard responsibilities

- risk per position;
- portfolio heat;
- margin utilization;
- leverage constraints;
- per-instrument/asset/cluster concentration;
- daily/period loss limits;
- drawdown de-risking;
- 20% hard drawdown stop;
- stale-data protection;
- venue-health protection;
- order-size/precision constraints;
- protective-stop feasibility;
- control-state enforcement.

## 17.2 Capital-preservation mode

A risk state before catastrophic halt that can:

- reduce new position size;
- tighten exposure budgets;
- suspend low-confidence strategies;
- preserve existing protective exits;
- require stronger evidence for new entries.

Triggers must be deterministic/quantitative where possible, not an LLM opinion.

## 17.3 Partial conviction

Valid but weaker opportunities may receive smaller allocation. Position size is a deterministic function of approved confidence/evidence/risk variables.

## 17.4 Risk-limit changes

LUFFY may produce a recommendation with evidence. Hard owner limits are changed only by owner action.

---

# 18. Execution Architecture

Execution converts approved trade intent into venue state safely.

Required capabilities:

- normalized order intent;
- venue-specific precision and minimums;
- idempotency key;
- placement;
- fill confirmation;
- exact commission/fee capture;
- protective stop/order management;
- partial-fill handling;
- cancel/replace;
- restart reconciliation;
- orphan-order detection;
- naked-position detection;
- execution-quality measurement.

## 18.1 Venue truth

When local journal and venue disagree, query the venue.

## 18.2 Protection

Where venue supports it, protective stops should survive LUFFY process failure.

Any live position without required protection is a critical alert.

## 18.3 Idempotency

A retry after timeout/restart must not create duplicate exposure.

## 18.4 Execution-quality model

Track:

- signal timestamp;
- decision timestamp;
- submission timestamp;
- fill timestamp;
- decision price;
- fill price;
- spread;
- slippage;
- fees;
- order size relative to liquidity;
- rejected/partial fills.

This evidence updates future cost/capacity models.

---

# 19. Outcome, Attribution and Replay

## 19.1 Outcome recording

Record both realized and selected counterfactual outcomes:

- executed trades;
- rejected trades;
- missed opportunities;
- strategy signals that risk blocked;
- research predictions where useful.

Counterfactuals must be clearly marked as simulated/unrealized.

## 19.2 Attribution

Attribute results across:

- hypothesis;
- strategy;
- regime/world state;
- portfolio interaction;
- position sizing;
- execution quality;
- costs;
- risk interventions;
- data quality.

## 19.3 Replay / digital twin

For any historical decision, LUFFY should be able to reconstruct:

- data available then;
- world model then;
- strategy version;
- config/risk version;
- portfolio state;
- decision;
- reasons;
- resulting action.

Historical replay must bind exact versions rather than current "latest" artifacts.

---

# 20. Memory Architecture

LUFFY remembers meaningful observations broadly rather than only successful trades.

Memory classes:

1. raw/event memory;
2. episodic memory;
3. semantic knowledge;
4. operating policy/doctrine.

## 20.1 Semantic knowledge

Atomic claims store:

```text
claim
status
confidence
provenance
supporting evidence
contradictory evidence
works_in
fails_in
causes/precedes/derived_from where meaningful
review history
```

Knowledge is not automatically deleted because of age. Confidence and current relevance change with evidence.

## 20.2 Exact retrieval first

For exact relationship questions, structured lookup is authoritative. LLM semantic recall is secondary.

## 20.3 Failed knowledge

Refuted and inconclusive research remains accessible to prevent costly repetition.

---

# 21. Learning Architecture

LUFFY learns from:

- profitable trades;
- losing trades;
- rejected trades;
- false signals;
- missed opportunities;
- strategy decay;
- regime changes;
- execution mistakes;
- portfolio interactions;
- research failures;
- data-quality incidents.

Learning may update within approved bounds:

- confidence calibration;
- strategy allocations;
- attention priorities;
- research priorities;
- evidence weights;
- world-model probabilities;
- strategy state.

Learning must not autonomously expand hard owner risk limits or modify production architecture.

---

# 22. AI / LLM Architecture

LLMs are expensive unstructured-reasoning tools, not the brain for every operation.

## 22.1 Good LLM uses

- interpret research papers/text;
- generate candidate hypotheses;
- summarize conflicting evidence;
- critique a hypothesis;
- transform unstructured idea into a typed research/strategy proposal;
- explain system behavior to the owner;
- synthesize postmortems;
- generate search queries for autonomous research.

## 22.2 Bad LLM uses

Do not use an LLM for:

- risk calculation;
- position sizing arithmetic;
- routine monitoring thresholds;
- scheduling;
- data freshness;
- database queries that deterministic code can answer;
- exchange order placement;
- repeated "should I change my architecture?" polling;
- indicator calculations;
- known statistical formulas.

## 22.3 Cost hierarchy

Use:

1. deterministic filter;
2. metadata/search relevance;
3. small/cheap model where needed;
4. deep reasoning only for high-value questions.

## 22.4 Budgeting

Track cost/token usage by purpose. Research can be paused or degraded when budget is exhausted. Live execution/risk must never depend on LLM availability.

---

# 23. External Systems, MCPs and Tools

The SD defines capability classes, not permanent vendors.

## 23.1 Venue adapters

Required interface categories:

- market metadata;
- market data;
- account/equity;
- positions;
- orders;
- fees;
- financing/funding;
- reconciliation.

Initial/known venue: Binance through CCXT plus direct venue endpoints where CCXT abstractions are incomplete.

## 23.2 Research/data sources

Potential classes:

- exchange APIs;
- derivatives-history providers;
- CoinGecko/market metadata;
- central-bank/FRED/BIS/IMF data;
- company filings;
- academic search/index APIs;
- on-chain providers;
- TradingView/public strategy ideas;
- reputable financial news;
- selected communities as low-trust idea sources.

## 23.3 MCP policy

MCP is appropriate for slow research/tool integration, not the live execution path.

MCP servers must declare:

- purpose;
- trust level;
- latency budget;
- data provenance;
- cost/rate limits;
- fallback behavior.

## 23.4 Engineering tools

Development-agent tooling such as Graphify is an engineering aid, not a runtime trading dependency.

Graphify should remain code-only under normal use to avoid unnecessary semantic indexing cost.

---

# 24. Monitoring and Alerts

Monitoring is deterministic by default.

Required alert classes:

- stale/missing data;
- database failure;
- venue/API failure;
- unprotected position;
- reconciliation mismatch;
- excessive slippage;
- unexpected latency;
- strategy degradation;
- portfolio concentration/correlation spike;
- drawdown/risk-state transition;
- research repeatedly blocked by missing capability/data;
- replay/live mismatch;
- repeated `UNTESTED` research due to data/power constraints;
- LLM/data spending threshold;
- disk/storage/CPU pressure affecting live path.

Persistent capability-gap alerts contain evidence such as count, affected hypotheses, duration, and economic impact.

No periodic LLM is required. Deeper AI analysis is on-demand.

---

# 25. Operator Interface and Explainability

The owner must be able to ask:

- why did you enter?
- why did you not enter?
- why is this position this size?
- why was another position reduced?
- what are you researching?
- what changed in the world model?
- what evidence contradicts your current belief?
- which strategies are weakening?
- what capability/data is blocking research?
- what are the current portfolio factor exposures?
- what did research/LLM/data cost this period?
- what requires my approval?

Responses must be grounded in journal/research/memory records, not invented narrative.

Natural-language control maps into the same typed commands as dashboard/API controls.

---

# 26. Security and Control Boundaries

Minimum requirements:

- secrets outside repository;
- least-privilege exchange keys;
- withdrawals disabled where possible;
- explicit production-mode enablement;
- authenticated remote operator interfaces;
- audit log for state/risk/config changes;
- external code/snippets treated as untrusted;
- research tools cannot directly call live execution;
- runtime LLM cannot obtain a raw unrestricted shell;
- destructive operations use explicit typed commands.

---

# 27. Backup, Recovery and Versioning

Protect:

- operational database;
- strategy definitions;
- world/research/memory state;
- SD/STATE/NEXT;
- configuration snapshots;
- artifact versions;
- evidence needed for replay/audit.

Backups are off-host, integrity-checked, and periodically restore-tested.

After catastrophic restart:

`restore -> query venue truth -> reconcile -> verify protection -> validate data -> start FROZEN -> resume entries only after health passes`

Every material decision must bind an artifact/config/version set sufficient for exact replay.

---

# 28. Testing and Validation Architecture

Required test layers:

## 28.1 Unit tests

Deterministic formulas, parsers, sizing, state transitions, feature functions.

## 28.2 Property/invariant tests

Examples:

- FROZEN/HALTED never opens a new position;
- missing data never becomes zero silently;
- risk cannot be bypassed;
- order retry cannot duplicate exposure;
- point-in-time reads never access future data.

## 28.3 Integration tests

Venue adapter, database, research source, API, strategy compiler, execution/reconcile.

## 28.4 Replay/equivalence

Research/backtest/live evaluator parity.

## 28.5 Failure injection

- network failure;
- exchange timeout;
- DB locked/corrupt;
- partial fill;
- lost process;
- stale feed;
- invalid source data;
- LLM outage;
- protective-order failure.

## 28.6 Statistical calibration tests

Null false-positive rate, power, dependence correction, FDR behavior, strategy admission controls.

## 28.7 Shadow/probation

Any capability affecting real capital progresses through deterministic tests and shadow/demo evidence before activation.

---

# 29. Capability Maturity Model

Each capability has one state:

`DEFINED -> IMPLEMENTED -> TESTED -> SHADOW -> PROVEN -> ACTIVE`

Later:

`DEGRADED | RETIRED`

Code existence never equals completion.

A capability is `PROVEN` only when its specific operational/quantitative acceptance evidence passes.

---

# 30. Step-by-Step Engineering Delivery Plan

This is the build sequence. Agents continue until the target capabilities are satisfied.

## Stage 0 - Establish Official Control Plane

Build/clean:

- `SDD.md` = this document;
- `STATE.yaml` = compact target-vs-current status;
- `NEXT.yaml` = one current work package.

Reduce `CLAUDE.md`/`AGENTS.md` to bootstrap pointers only if tools require them.

Consolidate useful information from old roadmap/plans/reports and exclude them from normal context afterward.

**Exit:** a fresh development agent can identify target, current state, and next task without reading historical planning files.

## Stage 1 - Safety and Truth Foundation

Complete and prove:

- canonical instrument identity;
- data provenance/quality;
- point-in-time correctness;
- operational journal truth;
- control state machine;
- deterministic risk authority;
- execution idempotency;
- protective orders;
- venue reconciliation;
- backup/recovery;
- monitoring/alerts.

**Exit:** LUFFY can fail/restart without losing understanding of real exposure; no trade path bypasses risk.

## Stage 2 - Unified Measurement and World Model

Build:

- common observation schema;
- perception feature/mechanism interface;
- hierarchical multi-horizon world model;
- cross-market relationship state;
- confidence/evidence tracking;
- historical world-state reconstruction for replay/backtesting.

**Exit:** strategies/research can query the same structured current/historical context.

## Stage 3 - Attention and Research Intelligence

Build:

- event-driven attention engine;
- research-question generator;
- source router/registry;
- search broker/retrievers;
- deterministic relevance/dedup pipeline;
- structured Research Bank;
- cost/budget manager;
- failed-research memory.

**Exit:** LUFFY can independently formulate a research question, find appropriate evidence sources, collect evidence cheaply, and store the result without a user-supplied URL list.

## Stage 4 - Quant Research and Falsification Engine

Complete/prove:

- robust cost-aware backtest;
- signal/time null models;
- common-factor/correlation-aware nulls;
- walk-forward/held-out framework;
- power detection / `UNTESTED` state;
- multiple-testing control;
- ablation/sensitivity analysis;
- portfolio-level evidence;
- reproducible experiments.

**Exit:** known no-edge controls are rejected at calibrated rates, known test controls demonstrate power, and candidate mechanisms can be evaluated without false confidence from symbol correlation or data leakage.

## Stage 5 - Strategy Factory and Lifecycle

Build one path from research to executable strategy.

Required:

- typed strategy spec;
- world-model dependencies;
- strategy compiler/evaluator;
- capacity estimate;
- validation evidence;
- shadow/paper probation;
- owner approval state for first real-money use;
- automatic degradation/retirement.

**Exit:** a research-born strategy can reach owner approval through one auditable path and cannot skip evidence stages.

## Stage 6 - Global Portfolio Intelligence

Build:

- common-factor exposure model;
- explicit cash allocation;
- event-driven opportunity set;
- expected-net-profit estimation;
- confidence decomposition;
- capital-efficiency/liquidity measures;
- capacity constraints;
- portfolio optimizer;
- replacement/opportunity-cost logic.

**Exit:** LUFFY selects among competing opportunities based on whole-book expected absolute profit under risk limits rather than taking every independent signal.

## Stage 7 - Learning and Closed Loop

Build feedback from:

- outcomes;
- rejections;
- missed opportunities;
- execution quality;
- portfolio attribution;
- strategy decay;
- research results;
- world-model accuracy.

Allow bounded automatic changes to confidence/allocation/strategy state.

**Exit:** measurable behavior changes in response to evidence and every change is attributable/replayable.

## Stage 8 - Explainability and Owner Control

Build grounded conversation/query system using typed internal tools.

**Exit:** owner can interrogate any important decision/research/portfolio state and get evidence-backed answers; approval-required actions are surfaced clearly.

## Stage 9 - Multi-Market Expansion

Expand one asset class/venue at a time through:

`data -> instrument model -> research -> strategy -> portfolio -> risk -> execution -> reconciliation -> shadow -> active`

Do not add a market merely because an API exists.

## Stage 10 - Advanced Capabilities Only When Earned

Possible future work, not mandatory baseline:

- multi-leg/pair/basket execution;
- options/volatility system;
- deeper microstructure/tick research;
- machine learning;
- alternative data;
- paid institutional datasets;
- additional compute architecture.

Each enters only after a measured problem/opportunity justifies complexity.

---

# 31. Engineering-Agent Operating Loop

Every AI development session follows:

```text
READ SDD.md
READ STATE.yaml
READ NEXT.yaml
        |
        v
Inspect only relevant code/evidence
        |
        v
Implement smallest coherent work package
        |
        v
Test + measure
        |
        v
Compare against acceptance gate
        |
        +-- fail --> fix/retest
        |
        +-- pass --> update STATE.yaml
                         |
                         v
                    write NEXT.yaml
                         |
                         v
                       repeat
```

Do not stop because one feature was coded. Stop when the work package's acceptance gate is met, blocked externally, or owner pauses work.

---

# 32. Three-File Project Control Plane

Only these are normal project-control context:

## `SDD.md`

Target architecture, rules, methods, and delivery sequence.

## `STATE.yaml`

Compact current status. Example:

```yaml
sdd_version: '3.0'
capabilities:
  world_model:
    state: IMPLEMENTED
    evidence:
      - tests/test_world_model.py
    gaps:
      - historical relationship replay not proven
    next_proof: run replay parity over 3 months
```

## `NEXT.yaml`

Exactly one active engineering package.

Historical specs/plans/reports are not default session context.

---

# 33. Target Capability Acceptance Gates

## 33.1 Autonomous market discovery

Pass when LUFFY can identify candidate markets from venue universe + world/attention signals without a fixed owner watchlist and explain why attention was allocated.

## 33.2 Autonomous research

Pass when LUFFY can generate a question, route it to suitable source classes, collect/deduplicate evidence, run or define an experiment, store support/contradictions, and do so within research budget.

## 33.3 World model

Pass when global -> asset class -> group -> instrument -> relationship states are structured, multi-horizon, confidence-bearing, historically replayable, and directly queryable by strategies.

## 33.4 Quant referee

Pass when calibrated simulations demonstrate acceptable false-positive behavior, dependence is handled, power is measured, and `UNTESTED` cannot silently become `FAIL` or `PASS`.

## 33.5 Strategy autonomy

Pass when LUFFY can create/test/retire strategies itself through the one pipeline, with first live deployment blocked pending owner approval.

## 33.6 Portfolio optimizer

Pass when new opportunities are evaluated incrementally against existing book, common-factor concentration, liquidity, capacity, opportunity cost, and cash, and decisions maximize expected absolute profit subject to hard risk.

## 33.7 Risk/execution safety

Pass when failure injection/restart tests prove no bypass, duplicate exposure, silent naked positions, or un-reconciled venue state.

## 33.8 Learning

Pass when outcome evidence measurably updates confidence/allocation/strategy state without crossing hard owner controls.

## 33.9 Explainability

Pass when every material decision can be traced to data, strategy/world state, portfolio/risk decision, and exact artifact versions.

---

# 34. Current Repository vs Target - Migration Section

This section is deliberately subordinate to the target design. AI agents should inspect the codebase directly for implementation details.

Known high-level mapping from prior audits:

| Target capability | Current status known from repo/local evidence | Direction |
|---|---|---|
| Live kernel/execution/risk | substantial implementation | HARDEN/PROVE |
| Data/candles/derivatives | substantial implementation | HARDEN/GENERALIZE |
| Strategy DSL/backtest | substantial implementation | HARDEN/EXTEND |
| Statistical research/null/FDR | substantial implementation | PROVE/INTEGRATE |
| Autonomous research discovery | partial/static-source scraper | BUILD |
| Attention | newer local code exists | RECONCILE/PROVE |
| Hierarchical world model | not established as complete | BUILD |
| Hypothesis/critic | partial concepts, not unified | BUILD/INTEGRATE |
| Global portfolio optimizer | limited compared with target | BUILD |
| Canonical instrument identity | partial symbol normalization only | BUILD |
| Structured semantic memory | partially built locally | HARDEN/INTEGRATE |
| Replay/digital twin | partial/newer local code | RECONCILE/PROVE |
| Monitoring/alerts | partial | EXTEND |
| Grounded owner conversation | partial | EXTEND |
| Multi-market adapters | crypto-heavy current implementation | FUTURE EXPANSION |

The exact status must be written into `STATE.yaml` after the Astra/local audit below.

---

# 35. Astra Deep-Analysis Assignments Before Final Current-State Freeze

Astra is useful where exhaustive local repository reverse engineering is cheaper and more reliable than manually reading every file. Astra does **not** decide the target architecture; this SD does.

## A1 - Local vs GitHub reconciliation - REQUIRED FIRST

Prompt:

> Read the official SDD only for capability names, then audit the full local LUFFY repository. Produce a concise target-to-current matrix. For each target capability, identify exact local files/classes/functions, maturity, tests, known gaps, and whether code is committed to GitHub. Specifically reconcile locally present `trader/cognition/*` and any other modules absent from GitHub. Do not propose architecture; report implementation facts only.

Deliverable: machine-readable matrix suitable for creating `STATE.yaml`.

## A2 - Full quant-method inventory - REQUIRED

> Inventory every quantitative/statistical method currently implemented: indicators, regime calculations, calibration, null models, dependence correction, portfolio evidence, FDR, backtest cost model, position sizing, risk formulas, execution-cost measurements. For each give formula/algorithm, implementation location, inputs, assumptions, tests, and known limitations. Mark duplicated or contradictory implementations.

Purpose: verify this SD's quant design against code and prevent parallel formulas.

## A3 - Research pipeline path audit - REQUIRED

> Trace every path from external/internal idea to research result to strategy creation to admission/paper/active. Identify all queues, states, config gates, background loops, LLM calls, DB tables, duplicate/dead/legacy paths, and current disabled handoffs. State exactly which single path is authoritative today.

Purpose: plan Stage 3-5 migration without restoring hidden second paths.

## A4 - Risk-to-order trace - REQUIRED

> Starting from one candidate trade, trace every value affecting final order quantity and permission: strategy confidence, meta sizing, portfolio/heat limits, margin limits, control state, macro/news gates, stop distance, exchange minimums/precision, execution call, protection and reconcile. Identify any duplicated risk logic or path that can bypass canonical risk.

Purpose: prove deterministic risk authority.

## A5 - Data/schema/concurrency audit - REQUIRED

> Inventory runtime databases/tables/files, their writers/readers, thread/process ownership, WAL/transaction behavior, timestamp semantics, retention, and migrations. Identify where world model, research bank, artifact versions, canonical instruments and alerts can be added with minimum schema complexity.

## A6 - Replay/equivalence audit - REQUIRED

> Trace all existing replay/backtest/live evaluator equivalence mechanisms. List all places where live semantics can differ from research: bar closure, signal age, funding alignment, reference timestamps, resampling, universe context, derivative coverage, execution delay and cost. Identify tests/scripts that prove equivalence and remaining unmeasured gaps.

## A7 - Runtime scheduling/resource audit - RECOMMENDED

> Map all cycles, daemon threads, processes, cron/watchdog jobs and resource-heavy tasks. Show cadence, blocking behavior, CPU/IO/LLM cost, and whether any slow workload can starve the live path. Recommend only minimal scheduling changes required by this SD.

## A8 - Test coverage map - RECOMMENDED

> Map the test suite to SDD capabilities/invariants and report uncovered safety-critical or quantitative behaviors. Do not list every test; produce capability-level coverage and highest-risk gaps.

After A1-A6, generate the initial `STATE.yaml`; only then freeze the current-state migration section. The target architecture in Sections 1-33 does not depend on these audits.

---

# 36. What LUFFY Is Expected to Be Able to Do at Completion

When the mandatory target is complete, LUFFY can:

- discover which markets deserve attention without being given a static watchlist;
- maintain a multi-horizon understanding of global, asset-class, group, instrument and cross-market states;
- generate its own research questions from anomalies, losses, contradictions and knowledge gaps;
- decide where to research instead of only scraping owner-provided URLs;
- read structured market data and selected external literature/news efficiently within cost budgets;
- form multiple competing hypotheses;
- test predictive mechanisms even when their causal explanation is incomplete;
- reject false confidence created by drift, correlated symbols, data leakage or repeated testing;
- invent new strategy approaches within the approved strategy language/system;
- deploy a new strategy only after evidence, shadow/paper validation and owner approval for first real use;
- automatically scale, reduce or retire approved strategies as evidence changes;
- discover where expected profit is highest across the available opportunity set;
- recognize multiple positions as one common-factor exposure;
- allocate capital globally rather than trade every signal;
- keep cash when no opportunity is economically worthwhile;
- protect capital through deterministic risk with a 20% hard owner drawdown stop;
- reconcile with real venue state after faults/restarts;
- learn from trades and non-trades;
- remember failed research so it does not repeatedly waste time and money;
- surface persistent engineering/data capability gaps as deterministic alerts;
- explain what it knows, what it does not know, what it is researching, why it acted, and what requires owner approval;
- expand to new markets/data/compute only when their expected value justifies complexity and cost.

That is the LUFFY target. Existing code is only the starting point.

---

# 37. Freeze Rule

Once approved:

- this file becomes repository root `SDD.md`;
- `STATE.yaml` is generated after Astra A1-A6 reconciliation;
- `NEXT.yaml` begins with the highest-priority unmet prerequisite from Stage 0/1;
- old project-control documents cease to be authority;
- any future architectural change is discussed with the owner and incorporated into a versioned revision of `SDD.md`.

# End
