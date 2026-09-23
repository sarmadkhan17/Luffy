# LUFFY Official Solution Design (SDD) v3.2

**Status:** AUTHORITATIVE TARGET ARCHITECTURE  
**Purpose:** Complete single build authority for human and AI development agents  
**Target:** Personal autonomous multi-market trading OS / trading intelligence and execution system  
**Date:** 2026-09-23

> This document defines the LUFFY we want to build. It is the single source of truth for finalized product, architecture, component-boundary, technology, safety, operator-interface, and delivery decisions. A developer must not need this chat or historical planning documents to understand the intended system.
>
> STATE.yaml describes what the repository currently proves. NEXT.yaml describes the single active work package. They do not redefine the target.
>
> If code, an old document, an agent suggestion, or a new technology conflicts with this SDD, the SDD wins until the owner explicitly approves a versioned SDD revision. Do not silently reopen or redesign a locked decision.

---

## v3.2 reconciliation scope

v3.2 reconciles the finalized LUFFY design into one developer-complete authority. It freezes the named runtime components and their boundaries, kernel role, analyst/decision contracts, strategy/version ownership, opportunity lifecycle, storage/concurrency model, owner approvals, frontend/dashboard technology, operator tabs, recovery authority, initial resource envelope, remote-gateway security, and the rule that already-locked architecture is verified rather than repeatedly redesigned.

No separate architecture-decision ledger is authoritative. Finalized design decisions belong here.

---

# 1. System Mission / End Goal

LUFFY is an autonomous multi-market trading intelligence system whose primary economic objective is to **maximize sustainable net account growth and capital efficiency within owner-defined survival constraints**. Cash/no-trade is a valid outcome; LUFFY has no trade-count or daily-profit quota.

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

Primary optimization objective: **maximize sustainable expected net account growth and capital efficiency** subject to owner-defined survival constraints.

Risk is a constraint on growth, not a substitute objective. Risk-adjusted measures are diagnostics and decision inputs; they must not create a hidden mandate to maximize Sharpe or force trading. Cash/no-trade is valid whenever expected net economic value is insufficient.

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

Availability is discovered dynamically from the connected venue/account capability and eligibility data, never assumed from symbol names, static watchlists, or marketing materials. Every instrument actually available to the connected account remains part of the eligible universe unless a deterministic safety/data/account constraint makes it temporarily non-executable. Attention prioritizes compute; it does not redefine the tradable universe.

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

A newly validated strategy requires **owner approval before its first real-money deployment**. Approval binds to the exact immutable strategy version/spec hash and its evidence receipt; a materially changed version requires a new first-live approval. After approval, LUFFY may manage allocation, pause, reactivate, and retire that approved version automatically within owner limits.

## 2.5 Architecture and production-code changes

LUFFY does not run a periodic LLM that asks whether it should rewrite itself.

Instead, deterministic monitoring detects persistent capability gaps such as repeated missing data, untestable hypotheses, replay/live mismatch, unacceptable latency, execution faults, or unsupported exposure models. These become alerts with evidence.

Architecture/code changes are then designed by development agents and approved by the owner. LUFFY itself does not silently modify production architecture.

## 2.6 Risk authority

Risk-limit changes require owner approval. LUFFY may recommend changes but cannot expand hard risk boundaries itself.

Exact hard percentages and catastrophe/drawdown thresholds belong to a **versioned owner risk policy**, not to the architecture itself. The active policy is authoritative and must be bound to decisions for replay.

Capital-preservation mode is required before the active catastrophe boundary is reached when evidence indicates abnormal risk or system uncertainty.

## 2.7 Research/data spending

Paid research/data budget target: **$100-$300/month only when LUFFY earns enough to justify it**.

A paid source must justify:

- what capability or hypothesis it unlocks;
- why existing sources are insufficient;
- expected economic/research value;
- ongoing cost.

## 2.8 Machine learning

Advanced machine-learning systems are **not a required initial architecture**. Statistical/quantitative methods and deterministic models are preferred first. ML may be added later through an approved SDD revision when a measurable problem warrants it.

A learned System-One/decision model such as Jev is not part of the target architecture merely because it can classify or score decisions. LUFFY already has structured strategy/orchestrator/risk logic. Such a model is considered only after a concrete measured deficiency exists and shadow evaluation shows material benefit over the deterministic/statistical design and simpler local ML alternatives.

## 2.9 Personal-system operating profile

LUFFY is initially a **single-owner personal trading OS**, not an institutional multi-tenant platform.

Owner operating preferences guide engineering but are not trade quotas:

- LUFFY may trade long or short when supported;
- portfolio hedging is valid where the required instruments and risk model exist;
- cash/no-trade is always valid;
- a normal successful style may produce roughly 10–15 trades in an active day, but LUFFY must never force that count;
- positions may last hours or days when the strategy horizon requires it;
- leverage, including values around 5x where permitted, is a risk/expression tool rather than a target and remains subject to the active owner risk policy;
- example daily-profit figures discussed during design are illustrations, never a quota or decision trigger.

LUFFY is also evaluated on **economic self-sufficiency**: over an owner-defined rolling period, report whether net trading contribution covers approved recurring system/data/LLM operating cost (earns its keep). Failure to cover cost creates evidence and a Needs You/operating-economics signal; it must never cause LUFFY to manufacture trades or increase risk to hit a weekly number.

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

## 4.1 Canonical component map

The target names below define responsibility boundaries. Existing repository classes may map into them during migration; do not create duplicate subsystems merely to match a name.

### Trading Core components

- **Kernel** — scheduler/orchestrator only.
- **Instrument Registry** — connected venue/account universe and instrument capabilities.
- **Data Feed / Market State** — point-in-time observations and data quality.
- **Feature / State Engine** — derived features, regime classification and world-state updates.
- **Attention Agent** — prioritizes where compute/investigation goes; never decides trades.
- **Technical Analyst** — price structure/trend/value/volatility technical evidence.
- **Order Flow Analyst** — order-book/trade/liquidity/flow evidence.
- **Derivatives Analyst** — funding/OI/basis/liquidation/positioning evidence.
- **Macro Analyst** — macro/event/rates/risk-regime evidence.
- **On-chain Analyst** — on-chain evidence only where valid data exists.
- **Cross-Market Analyst** — relative strength, factors, correlations, breadth and cross-asset evidence.
- **Strategy Runtime Engine** — evaluates immutable approved StrategySpecs against point-in-time context.
- **Opportunity Context Builder** — freezes one coherent decision snapshot.
- **Decision Orchestrator** — deterministic candidate comparison/decision.
- **Portfolio Allocator** — whole-book capital allocation and opportunity-cost decisions.
- **Risk Agent / Risk Authority** — deterministic hard permission/sizing/risk veto.
- **Execution Agent** — venue order execution and fill management.
- **Exit Agent** — approved exit-plan/protection lifecycle.
- **Accounting Agent** — exchange-authoritative monetary truth plus derived attribution.
- **Supervisor** — health, containment, recovery-state orchestration and safe resume logic.

### Research/Learning components

- **Researcher** — question/evidence/source investigation.
- **Experimenter** — reproducible experiment execution.
- **Validator / Referee** — statistical admission/falsification gates; no promotion by narrative.
- **Strategy Builder** — converts validated hypotheses into typed StrategySpecs.
- **Strategy Governor** — active strategy lifecycle/allocation authority within approved bounds.
- **Learning Agent** — conservative evidence updates to reliability/confidence/allocation/research priority; no hard-risk expansion and no production-code rewrite.

### Owner/UI/knowledge components

- **Journal / Operational Store** — durable operational truth/provenance.
- **Research Bank** — structured research objects and negative results.
- **Knowledge/Evidence Store** — semantic/evidence/timeline/code-linked knowledge.
- **Owner Interface API** — typed authenticated query/control/approval interface.
- **LUFFY Conversation** — grounded partner conversation over typed tools/evidence.
- **Dashboard** — browser-rendered operational interface.
- **Remote Gateway** — optional Telegram/Clawd-style narrow gateway with no trading secrets.

## 4.2 Canonical live decision path

```text
Instrument Registry
      -> Data / Feature / World State
      -> Attention
      -> Strategies + required Analyst evidence
      -> frozen Opportunity Context
      -> Decision Orchestrator
      -> Portfolio Allocator
      -> deterministic Risk Authority
      -> Execution
      -> Exit / Protection
      -> Accounting / Venue Reconciliation
      -> Outcome / Attribution
      -> Learning / Research / Knowledge
```

Normal live trading requires **zero LLM calls**.

Analysts provide evidence. Strategies propose executable logic. Decision compares candidates. Portfolio decides book expression. Risk decides permission and bounded size. Execution/Exit interact with the venue. Accounting records truth. Supervisor can freeze/recover the system. These authorities must not be collapsed into an opaque “AI brain.”

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

Begin with the smallest reliable topology and scale out only when measured resource contention, data volume, availability, or security boundaries justify it.

The target runtime separates four logical process groups:

1. **Trading Core** — live market state, Attention, analysts, Decision, Risk, Execution, Exit, reconciliation, supervisor, hot state.
2. **Research/Learning** — research, experiments, validation, strategy building, learning, scraping/crawling; lower resource priority than live trading.
3. **UI/API** — dashboard, grounded LUFFY conversation, read/query interfaces.
4. **Remote Gateway** — Telegram and optional future gateways such as Clawd; no trading credentials.

These are logical isolation boundaries, not a mandate for microservices. Components may initially coexist where safe. Research/scraping/crawling must never starve or crash the Trading Core.

No analyst gets its own process merely because it is called an “agent.” In LUFFY, an agent is a responsibility/contract. Most agents are ordinary Python modules or objects. Process boundaries exist for isolation, reliability, resource control, and security—not branding.

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
- Parquet for analytical bulk history;
- DuckDB for local analytical querying over Parquet/history where useful;
- PostgreSQL only after measured concurrency/availability/scale need justifies migration;
- YAML/Markdown for structured human-readable semantic memory where human-readable artifacts are useful;
- pytest for deterministic tests.

A future language transition is not prescribed. If Python becomes a measured blocker, a separate transition design is created at that time.

## 5.4 Initial deployment/resource envelope

The initial deployment target is the owner's laptop/VM environment, not a permanent architectural ceiling:

- Windows host, Linux VM;
- current VM class: approximately 4 vCPU and 8 GB RAM;
- host GPU is not required by the trading core;
- normal live-core target: approximately <=2 CPU cores sustained and 4–6 GB RAM where practical;
- research may use remaining local CPU opportunistically but yields to live trading;
- browser-side graphics use the owner's browser/Windows GPU rather than backend trading compute;
- additional RAM/compute/cloud is added only when measurement justifies it and paid/cloud use follows owner approval.

Research workload must back off when live-cycle latency or system pressure rises. The live trading loop always wins resource contention.

## 5.5 Kernel contract

The **Kernel** is the runtime coordinator/scheduler for the Trading Core. It is not LUFFY's “brain,” not a super-agent, and not allowed to bypass component contracts.

The Kernel owns or coordinates:

- boot/shutdown;
- control-state enforcement;
- live cycle timing;
- canonical market/universe refresh;
- invoking Attention, perception/state, strategy evaluation, analyst measurement, Decision, Portfolio, Risk, Execution, Exit, Accounting, Supervisor/reconciliation;
- heartbeats and health;
- safe background-task scheduling;
- publishing typed events and recording durable cycle lineage.

The Kernel must not:

- invent strategy logic;
- make discretionary LLM trade calls;
- override Risk;
- place orders except through Execution;
- silently mutate strategy versions;
- hide component failures;
- run research workloads that can starve the live cycle.

The target is a thin orchestration kernel with explicit component contracts, even if the current implementation is a larger monolith that must be migrated incrementally.

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

## 6.5 Events, commands and provenance

LUFFY distinguishes **events** (facts that happened) from **commands** (requests to do something). Cross-component communication uses typed, versioned contracts rather than arbitrary internal calls.

Where relevant, envelopes carry:

`event_id | type | event_time | source | symbol/instrument_id | cycle/decision/opportunity reference | schema_version | payload`

Existing `cycle_id -> decision_id -> trade/outcome` lineage remains canonical where it already provides the required trace. A higher-level opportunity identifier may group several cycles that belong to the same evolving setup; it references evidence rather than duplicating it.

Material decisions must be reconstructible from persisted provenance.

## 6.6 Live bus, journal and storage ownership

Use a lightweight **in-process typed live event bus** for low-latency coordination and a **durable append-oriented journal** for facts that must survive restart/replay. Kafka, Redis, or another distributed broker is not an initial requirement.

SQLite/WAL is the initial operational database. Preserve disciplined write ownership:

- one logical authority owns each mutable truth domain;
- do not introduce uncontrolled multi-process writers;
- operator/UI components may submit typed control intents through the API, but they do not write monetary/trade truth directly;
- the Trading Core/Accounting path is authoritative for durable trade/accounting state after venue reconciliation;
- readers may be many; writes must remain serialized/transactional and measurable.

Historical analytical volume should move to Parquet and be queried locally with DuckDB when that reduces SQLite pressure. PostgreSQL is a measured future migration, not a default upgrade.

## 6.7 Hot state versus durable truth

Hot in-memory state exists for speed and may be rebuilt. Durable journal/venue truth exists for recovery.

A restart must reconstruct hot state from durable records plus current venue truth. No safety-critical state may exist only in an unpersisted Python object.

## 6.8 Instrument Registry

The Instrument Registry is the canonical source for discovered venue/account capabilities and instrument metadata. It refreshes from connected venue/account truth, preserves capability/version history, and feeds Attention, Strategy, Portfolio, Risk and Execution.

There is no permanent hardcoded top-N trading list. Attention may narrow compute focus, but the eligible universe comes from connected capability and deterministic account/data constraints.

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

Hard catastrophe/drawdown boundaries come from the active versioned owner risk policy. When a hard boundary is reached, no new risk is permitted and LUFFY enters the policy-defined halt/capital-preservation behavior.

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
strategy_family_id
strategy_version_id
parent_version_id
spec_hash
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

A strategy version is immutable. Material changes to entry, exit, required features, timeframe/horizon, or invalidation create a new version rather than silently rewriting the deployed artifact.

## 14.2 Single creation path

There is one authoritative path:

`research/idea -> strategy specification -> quantitative validation/referee -> freeze immutable spec + evidence receipt -> shadow/paper -> approval required -> owner approval for exact version/hash -> active`

Research never writes directly into live execution authority. No scraper, LLM, dashboard, or legacy component may directly inject an unvalidated strategy into live eligibility. Live activation must load the exact approved strategy hash; if it cannot reproduce that artifact, activation is refused.

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

## 14.6 Strategy Governor authority

The Strategy Governor is the single lifecycle authority after a strategy version has passed research/validation.

It may, within owner-approved risk/capital boundaries:

- change allocation;
- pause;
- reactivate;
- degrade;
- retire an already-approved strategy version.

It may not silently mutate the strategy specification. Any material change to entry, exit, required features, timeframe/horizon or invalidation creates a **new immutable version** requiring the first-live approval path again.

Duplicate/near-identical strategy variants must not multiply confidence merely by voting multiple times. Evidence correlation and common lineage must be recognized.

## 14.7 Capability lifecycle

New optional capabilities/data/tools follow:

`PROPOSED -> TRIAL/RESEARCH_ONLY -> PROVEN_USEFUL -> PRODUCTION -> DEPRECATED`

A capability that cannot yet be supported must become a structured owner-facing capability request, not an invented or silently emulated feature.

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

Before Decision, LUFFY builds one coherent **Opportunity Context** for the asset/setup. It contains the current market/feature state, relevant timeframes, strategy candidates, required and available analyst evidence, regime, costs, supporting/opposing evidence, freshness/quality, and portfolio/risk context. Strategies declare required analysts/features, optional analysts/features, and freshness limits. Missing optional evidence does not block; missing required evidence does.

The context is frozen with the decision/rejection so LUFFY can later answer what it knew at that time.

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

Analyst reliability is contextual rather than one global score: where evidence supports it, reliability is tracked by asset/asset class, timeframe, regime, signal type, direction, and strategy family. Reliability changes evidence weight; it does not turn an analyst into ground truth. Updates are conservative and require sufficient independent evidence.

A calibrated composite score may be produced for sizing/ranking, but components remain visible.

## 15.3 Position expression

Current target supports directional single-instrument positions as the primary unit.

For the connected personal account, **one asset/instrument has one real account position**. Multiple same-direction strategies strengthen supporting evidence; they do not create independently stacked strategy-owned positions. Opposing strategies remain recorded as conflict/learning evidence.

The winning strategy supplies the direction, proposed size, and approved exit plan for a new position. After entry, changing confidence does not continuously resize the position. Exposure changes occur only through the approved trade plan or safety/risk actions such as planned scale-in, partial exit, stop/trail/invalidation, or emergency protection.

Multi-leg/pair/basket expressions are allowed as a future capability only when they clearly add value and the dedicated execution/risk architecture is implemented. They must not complicate the initial system merely because they are theoretically attractive.

## 15.4 Cash

Cash/no-trade is an explicit candidate. It wins whenever no opportunity offers sufficiently positive expected value after costs and uncertainty.

## 15.5 Analyst evidence contract

Analysts **measure and explain evidence; they never decide trades**.

Every analyst packet is typed and should contain, where relevant:

```text
analyst_id
instrument/opportunity reference
as_of timestamp
horizon/timeframe
features/observations used
directional implication if measurable
strength/score
uncertainty/confidence
data quality/freshness
supporting evidence
contradicting evidence
known limitations
schema/version
```

Strategies declare which analyst/features are required versus optional and their freshness limits. The system must not wait for irrelevant analysts merely to collect a complete panel.

Analyst reliability is contextual and updated slowly from sufficient evidence. It changes evidence weight; it never converts an analyst into truth.

## 15.6 Decision Orchestrator contract

The Decision Orchestrator is **deterministic and auditable**. It is not an LLM and not a simple majority-vote machine.

It compares:

- strategy eligibility and expected economics;
- required evidence completeness/freshness;
- analyst support/conflict and contextual reliability;
- uncertainty;
- regime/world state;
- costs/liquidity;
- current position;
- portfolio context;
- cash/no-trade.

Its output is a typed decision proposal/rejection with exact reasons and references. Risk remains a separate final authority.

## 15.7 Multi-cycle Opportunity registry

Existing `cycle_id -> decision_id -> trade/outcome` lineage remains canonical. A lightweight deterministic Opportunity registry may group repeated cycles that represent the same evolving setup.

Minimum fields:

```text
opportunity_id
instrument
direction/horizon
opened_at
status
related cycle/candidate ids
optional investigation_id
resolution
```

Resolution values include:

`TRADED | SKIPPED | EXPIRED | INVALIDATED`

The registry references existing evidence; it must not create a duplicate parallel trace system.

## 15.8 Direction, hedging and relative value

Primary live expression is one directional position per instrument: long, short, or flat.

Portfolio hedges using distinct supported instruments are allowed when their strategy/risk/cost model is explicit. Coordinated multi-leg pair/basket/relative-value execution is a later capability requiring dedicated execution, atomicity/failure handling and portfolio risk support. Do not fake a multi-leg strategy by independently opening unrelated legs.

---

# 16. Global Capital Allocation and Portfolio Optimization

LUFFY optimizes the whole portfolio, not individual trades independently.

Objective: maximize sustainable expected net account growth and capital efficiency subject to owner survival constraints, capacity, liquidity, margin, and evidence quality.

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

## 16.7 Portfolio Allocator authority

The Portfolio Allocator decides **how much account capital/margin should be assigned across competing approved opportunities and existing positions**. It operates on the whole book, includes cash as an allocation, and cannot bypass hard Risk limits.

It may recommend replacement/hedging/reduction when opportunity cost changes, but it does not directly place orders; changes become typed trade intents routed through Risk and Execution.

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
- owner-defined catastrophe/drawdown boundary from the active versioned risk policy;
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

When local journal and venue disagree, the venue is authoritative for monetary and position truth where the venue exposes that truth: fills, executed quantity/price, commissions/fees, funding, open position quantity, order/stop state, balance/equity, and realized P&L where available. Raw venue receipts are preserved. LUFFY derives only metrics the venue does not provide, such as R, MFE/MAE, entry-edge quality, exit efficiency, strategy attribution, counterfactuals, and decision lineage. Discrepancies produce explicit reconciliation evidence; they are never silently overwritten.

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

## 18.5 Exit Agent

The Exit Agent owns the approved position exit geometry after entry:

- native protective stop state;
- planned scale-ins only if the approved plan permits them;
- partial profit-taking;
- trailing-stop logic;
- deterministic invalidation;
- emergency protective exits.

A change in analyst confidence alone does not continuously resize or rewrite a live position.

## 18.6 Accounting Agent

Accounting treats venue/exchange receipts as authoritative for monetary/position facts where available and preserves the raw receipt. Derived metrics are versioned calculations layered on top.

Accounting never “fixes” a discrepancy by silently overwriting history. Reconciliation records what differed, which source won for each field, and what corrective action occurred.

---

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

The Learning Agent updates evidence weights conservatively. It must learn from executed, rejected, missed and avoided outcomes, but must distinguish observation from causal proof. Statistical demotion/promotion requires adequate sample/evidence and must remain reproducible.

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

## 22.3 LLM router and cost hierarchy

Before every LLM call ask: **can structured data, arithmetic, statistics, or deterministic rules answer this correctly?** If yes, do not call an LLM.

Use three levels:

1. **Deterministic/no LLM** — live trading, risk, execution, accounting, routine factual owner queries, known calculations and structured reasons.
2. **Fast/bounded model** — concise explanation, unstructured extraction, hypothesis wording, or synthesis where deterministic tools are insufficient.
3. **Deep reasoning model** — only for genuinely difficult research/reasoning tasks or when the owner explicitly requests deeper analysis.

Normal live trading requires zero LLM calls. LLM/provider outage must not stop safe live operation.

## 22.4 Budgeting

Track cost/token usage by purpose. Research can be paused or degraded when budget is exhausted. Live execution/risk must never depend on LLM availability.

## 22.5 Provider and model policy

LLM access is behind a provider adapter; the architecture must not depend on one vendor. OpenAI-compatible provider interfaces are acceptable where useful.

A new model/classifier is not added because it is fashionable. It must solve a measured gap, run in shadow/research first, and demonstrate incremental value, reliability and cost benefit over existing deterministic/statistical logic. This includes System-One decision models such as Jev.

The LLM does not own routine `LONG/SHORT/SKIP` decisions. Those come from strategies, structured analyst evidence, deterministic Decision/Portfolio logic and Risk.

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

Graphify should remain code-only under normal use to avoid unnecessary semantic indexing cost. The runtime owner-facing Knowledge system is a separate domain knowledge/evidence model, not Graphify itself.

## 23.5 Remote gateway role

Telegram and any future Clawd/Clawdbot-style product are **remote owner gateways only**. They are not trading intelligence and never become a hidden decision authority.

A gateway may:

- deliver alerts;
- display Needs You items;
- relay authenticated owner queries/commands through the Owner Interface API.

It must not:

- receive Binance/execution credentials;
- read LUFFY secret directories;
- gain arbitrary repository/database access;
- gain Docker socket/host escape capability;
- place venue orders directly.

Product-specific capabilities of an external gateway must be re-verified at implementation time; do not design security around remembered marketing claims.

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

## 24.1 Supervisor

The Supervisor is deterministic health/recovery authority. It watches heartbeat, venue/data/accounting/protection/reconciliation health and can transition the control system into containment states.

It does not decide market direction.

Required recovery flow:

`Detect -> Contain/FROZEN -> RECOVERY -> query venue truth -> reconcile -> restore/rebuild -> verify every position/protection -> health checks -> ACTIVE or Needs You`

Minor failures auto-recover only when safety can be proved. Serious or uncertain venue/protection/reconciliation/state failures remain blocked until owner resume.

---

# 25. Operator Interface, Dashboard and Explainability

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

Primary operator navigation target:

`Overview | Trades | Research | Strategies | LUFFY | Operations | Live System | Knowledge | Diagnostics`

**Needs You** appears on Overview whenever owner action is pending. Full approval discussion/evidence lives in LUFFY; serious incidents and owner-decision alerts are also sent through Telegram when configured. LUFFY chat stays concise by default and links to evidence/details rather than dumping them into conversation: **LUFFY speaks; the dashboard proves.**

Natural-language control maps into the same typed commands as dashboard/API controls. Deterministic/simple emergency commands may be available conversationally, but configuration/governance changes use explicit structured owner actions.

## 25.1 Frontend technology lock

Target frontend stack:

- React + TypeScript + Vite;
- Three.js + React Three Fiber + Drei for selective 3D/system visualization;
- Motion for ordinary UI animation;
- GSAP for cinematic/3D choreography where warranted;
- Radix primitives + custom LUFFY design system;
- Tailwind only selectively;
- TanStack Query for server-state fetching/cache;
- Zustand for small client/UI state;
- Lightweight Charts for trading/time-series charts;
- D3 where graph/layout/data-visualization logic benefits from it;
- FastAPI serves the built Vite static assets and operator APIs;
- GraphQL may remain the rich query/control contract where it fits;
- WebSocket/live stream for real-time updates.

Production does **not** require a Node server. Do not introduce Next.js, Electron, Redux, microfrontends, Redis merely for UI state, or Grafana as the primary LUFFY interface without an approved SDD revision.

## 25.2 Rendering/resource rule

Cinematic visuals are browser work. Three.js/WebGL runs on the user's browser/host GPU. The trading backend sends state/data; it does not render 3D.

3D/motion is selective and must degrade gracefully:

- adaptive quality;
- reduced-motion support;
- no trading-core GPU dependency;
- visual failure must never affect trading.

## 25.3 Navigation contracts

Authoritative navigation:

`Overview | Trades | Research | Strategies | LUFFY | Operations | Live System | Knowledge | Diagnostics`

### Overview
Account/equity/P&L, exposure, positions, risk/control state, highest-priority global signals, and **Needs You**. Overview is not a wall of every internal metric.

### Trades
Forensic trade/decision timeline: opportunity context, strategy, analyst evidence, portfolio/risk reasoning, orders/fills/exits, accounting, outcomes and replay links.

### Research
Investigations, hypotheses, experiments, falsification, validation status, evidence and research cost.

### Strategies
Strategy families/immutable versions, exact spec/evidence hashes, state, economics, reliability, capacity, allocation and Governor actions.

### LUFFY
Owner-partner conversation. Concise by default, grounded in typed tools and evidence. Contextual actions may include `[Evidence]`, `[Explain deeper]`, and `[Open trade/strategy]`. Voice is a later optional surface. **LUFFY speaks; the dashboard proves.**

### Operations
What is happening now: instruments scanned/prioritized, signals/candidates, decisions/rejections, orders, risk blocks, background research and operational activity.

### Live System
Actual runtime nodes/agents/components/connections/data movement and health (`active | idle | degraded | failing`). This view represents the system, not strategy cards. Kernel/raw internals are hidden by default and exposed only in advanced/debug detail.

### Knowledge
Layered hybrid knowledge graph with **Evidence/Provenance as the foundation**. Required lenses:

`Knowledge | Evidence | Timeline | Code`

The UI should be able to trace:

`Observation -> Hypothesis -> Experiment -> Evidence -> Conclusion -> Strategy/Decision -> Outcome -> Learning`

### Diagnostics
Health/failures/resource/data/API/storage/recovery diagnostics. Raw internals are advanced detail, not the normal owner interface.

## 25.4 Needs You / approvals

`Needs You` is the single owner-action surface. Full context lives in LUFFY; Overview shows the concise pending item; Telegram can alert when the owner is away.

Approval objects bind exact evidence/version/config hashes. An approval becomes stale/invalid when the referenced strategy version, evidence, capability scope, cost, or safety context materially changes.

Owner approval is required for:

1. first real-money activation of a new strategy/version;
2. new capability/data/API/MCP access that exceeds already approved scope;
3. paid spend;
4. owner survival/risk-boundary changes;
5. resuming after a serious recovery condition that cannot prove safe autonomous recovery;
6. architecture/production-code design changes through the development workflow.

Owner approval is **not** required for ordinary trade/skip decisions, instrument selection, position size within approved policy, research questions, strategy pause/reactivation/retirement, or allocation changes within approved boundaries.

---

# 26. Security and Control Boundaries

Minimum requirements:

- secrets outside repository and isolated by service/process privilege;
- least-privilege exchange keys;
- withdrawals disabled where possible;
- explicit production-mode enablement;
- authenticated remote operator interfaces;
- audit log for state/risk/config changes;
- external code/snippets treated as untrusted;
- research tools cannot directly call live execution;
- runtime LLM cannot obtain a raw unrestricted shell;
- destructive operations use explicit typed commands;
- production secrets are least-privilege per service/process rather than one shared global environment;
- a remote gateway such as Clawd must not receive Binance/execution credentials, read LUFFY secret files/directories, access the LUFFY repository/database arbitrarily, or obtain Docker/host-level escape paths;
- remote gateways communicate through a narrow authenticated Owner Interface API with only their required scopes.

Compromise of the remote gateway must not imply compromise of exchange credentials.

The exact secret-storage mechanism is an implementation decision constrained by least privilege; do not hardcode a fictitious path into architecture. The current repository-level `.env` approach is a migration concern recorded in STATE, not the target security model.

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

The deterministic control-state model includes:

`ACTIVE | FROZEN | HALTED | RECOVERY`

`RECOVERY` means LUFFY is rebuilding/reconciling and cannot accept new entries. A process restart never implies that trading is safe.

After a serious fault or catastrophic restart:

`detect -> contain/freeze -> RECOVERY -> query venue truth -> reconcile -> rebuild state -> verify every open position/protection -> validate data/health -> ACTIVE or Needs You`

Minor/transient failures may auto-recover when safety is provable. Uncertain venue state, unrecoverable protection, repeated execution/reconciliation failure, critical-state corruption, or recovery that cannot prove safety remains FROZEN/RECOVERY and requires owner action before returning ACTIVE.

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

Any new capability affecting real capital progresses through deterministic tests and appropriate shadow/paper/probation evidence before activation. Demo is not a universal substitute for venue-realistic evidence; the exact probation mode must match the capability and preserve execution/risk semantics.

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

**Exit:** LUFFY selects among competing opportunities based on whole-book sustainable expected net growth/capital efficiency under owner survival limits rather than taking every independent signal.

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

## 31.1 Locked-decision rule for development agents

Development agents must distinguish:

- **LOCKED TARGET:** defined by this SDD; implement/verify it, do not redesign it;
- **CURRENT REPO BEHAVIOR:** evidence in code/tests/STATE; preserve correct behavior while migrating;
- **OPEN IMPLEMENTATION DETAIL:** may be chosen locally when the SDD intentionally leaves freedom.

When a proposed implementation appears better but conflicts with an SDD decision, stop and raise a proposed SDD revision to the owner. Do not create a second architecture document, ADR hierarchy, hidden design file, or prompt-only rule that competes with this SDD.

Before introducing a new agent/service/model/database/framework, prove the existing architecture cannot solve the measured problem with lower complexity.

---

# 32. Three-File Project Control Plane

Only these are normal project-control context:

## `SDD.md`

Target architecture, rules, methods, and delivery sequence.

## `STATE.yaml`

Compact current status. Example:

```yaml
sdd_version: '3.2'
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

Historical specs/plans/reports are evidence/reference only and are not default architecture authority. Experiment-specific hashes, benchmark receipts, pilot results and current blockers belong in reports/STATE/NEXT, not as permanent architecture doctrine in this SDD.

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

Pass when new opportunities are evaluated incrementally against the existing book, common-factor concentration, liquidity, capacity, opportunity cost, and cash, and decisions maximize sustainable expected net growth/capital efficiency subject to owner survival constraints.

## 33.7 Risk/execution safety

Pass when failure injection/restart tests prove no bypass, duplicate exposure, silent naked positions, or un-reconciled venue state.

## 33.8 Learning

Pass when outcome evidence measurably updates confidence/allocation/strategy state without crossing hard owner controls.

## 33.9 Explainability

Pass when every material decision can be traced to data, strategy/world state, portfolio/risk decision, and exact artifact versions.

---

# 34. Current Repository vs Target

This SDD intentionally does not freeze a stale snapshot of current implementation.

Authoritative current-state evidence lives in `STATE.yaml`, with exact code/tests/artifacts. The active next package lives in `NEXT.yaml`.

A new developer must therefore read in this order:

1. `SDD.md` — what LUFFY must become and what decisions are locked;
2. `STATE.yaml` — what is currently implemented/tested/proven and the known gaps;
3. `NEXT.yaml` — the one active work package;
4. only then inspect the code/evidence relevant to that package.

Do not rerun old Astra planning audits merely because they appear in historical documents. Perform a targeted repository audit only when STATE evidence is missing/stale or the active work package requires it.

---

# 35. Developer Component Contracts

The following contracts are mandatory even if current filenames/classes differ.

## 35.1 Attention
Input: eligible instrument universe + current world/data/portfolio/research signals.  
Output: prioritized attention/investigation candidates with reason/cost/urgency.  
Forbidden: trade decision, position size, order placement.

## 35.2 Analysts
Input: Opportunity Context inputs within declared scope.  
Output: typed evidence packet.  
Forbidden: order/risk authority, strategy activation, majority-vote control.

## 35.3 Strategy Runtime
Input: immutable StrategySpec version + point-in-time data/world state.  
Output: strategy candidate with direction, economic estimate inputs, proposed size/exit plan and evidence references.  
Forbidden: silent spec mutation or bypass of Decision/Portfolio/Risk.

## 35.4 Decision Orchestrator
Input: frozen Opportunity Context and eligible strategy candidates.  
Output: selected candidate or rejection with deterministic reasons.  
Forbidden: venue calls, risk override, opaque LLM decision.

## 35.5 Portfolio Allocator
Input: decision candidates + book/equity/margin/factor/capacity/cash state.  
Output: portfolio-level desired exposure/allocation intents.  
Forbidden: venue calls or hard-risk override.

## 35.6 Risk Authority
Input: typed proposed exposure/action + active versioned owner risk policy + venue/account state.  
Output: allow/resize/block plus exact reason.  
Forbidden: LLM judgment or self-expansion of hard limits.

## 35.7 Execution
Input: risk-approved order intent.  
Output: order/fill/cancel/retry/protection events and raw venue receipts.  
Forbidden: changing strategy thesis or opening unapproved exposure.

## 35.8 Exit
Input: live position + approved immutable exit plan + safety state.  
Output: stop/trail/partial/invalidation/emergency exit intents.  
Forbidden: continuous confidence-driven resizing.

## 35.9 Accounting
Input: venue receipts/account state + execution/trade lineage.  
Output: authoritative monetary/position state and derived attribution.  
Forbidden: silent reconciliation overwrite.

## 35.10 Supervisor
Input: component/venue/data/storage/heartbeat/protection health.  
Output: health state, containment/recovery transitions, Needs You.  
Forbidden: trade-direction selection.

## 35.11 Research / Experiment / Validator
Researcher asks/collects; Experimenter runs reproducible tests; Validator/Referee decides evidence gates. These roles remain separated from production activation so narrative enthusiasm cannot promote a strategy.

## 35.12 Strategy Builder / Governor
Builder creates immutable candidate specs from validated evidence. Governor manages lifecycle/allocation of already-approved versions. Neither can bypass first-live owner approval for a new version.

## 35.13 Learning
Updates evidence/reliability/allocation/research priorities conservatively and reproducibly. It does not rewrite production code, invent risk limits or erase negative evidence.

## 35.14 Owner Interface
All dashboard/chat/remote controls resolve to typed authenticated commands. A browser or Telegram/remote gateway never directly places exchange orders or edits trading truth.

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
- protect capital through deterministic risk and the active owner-defined catastrophe/drawdown policy;
- reconcile with real venue state after faults/restarts;
- learn from trades and non-trades;
- remember failed research so it does not repeatedly waste time and money;
- surface persistent engineering/data capability gaps as deterministic alerts;
- explain what it knows, what it does not know, what it is researching, why it acted, and what requires owner approval;
- expand to new markets/data/compute only when their expected value justifies complexity and cost.

That is the LUFFY target. Existing code is only the starting point.

---

# 37. Authority and Revision Rule

This file is repository-root `SDD.md` and is the sole architecture authority.

- `STATE.yaml` must declare the SDD version it is measuring against.
- `NEXT.yaml` must not request work that contradicts this SDD.
- old requirements/roadmaps/design copies/reports are non-authoritative evidence unless this SDD explicitly incorporates them;
- development agents must not reopen a locked decision under a new name;
- any genuine architecture change is discussed with the owner first, then incorporated into a versioned SDD revision;
- implementation detail may evolve without an SDD revision only when it does not change a locked product/component/technology/safety contract;
- after an SDD revision, STATE must be reconciled to the new target and NEXT updated if needed.

# End
