# Offline retrospective market history

Retrospective access uses the existing `DataFeed`, candle SQLite projection,
receipt/revision provenance, compiler, research evaluator, and backtest engines.
It describes stored observations of historical markets. It does not establish
what LUFFY knew at a historical bar close, untouched validation evidence,
executable fills, liquidity, or Factory admission.

## Selecting a dataset

Construct the existing feed with an explicit path and descriptor:

```python
feed = DataFeed(db_path="/frozen/candles.db", retrospective={
    "dataset_id": "frozen-study-v1",
    "cut_ms": 1741452480000,
    "paths": {"candles": "/frozen/candles.db",
              "derivs": "/frozen/derivs.db"},
})
frame = feed.cached_ohlcv("BTC/USDT", "4h", limit=200000)
```

Optional `start_ms` is inclusive and `end_ms` is exclusive. Every returned
candle closes at or before `cut_ms`. The SQL query excludes later prices
before loading them. The connection uses SQLite `mode=ro` and `query_only`;
there is no schema initialization, database creation, exchange construction,
or acquisition. Explicit `as_of_ms` cannot be combined with retrospective
cached reads. `fetch_ohlcv` refuses acquisition in this mode.

The projection is audited for finite, positive, internally consistent OHLC
and nonnegative volume. Invalid candles are ineligible both for discovery
coverage and loading. Original receipt metadata is attached only when a
verified retained receipt exactly matches the projection. Otherwise source,
instrument and acquisition clocks remain unknown. Auxiliary `taker_buy` is
unavailable unless a valid, known-source retained receipt confirms that field;
legacy stored flow proxies are never treated as observed aggressor volume. `observation_quality`
records numerical audit separately from receipt `quality`. No acquisition
clock is inferred from a candle timestamp. Matching receipt hashes and their
indexed clocks are verified; corrupted retained records refuse.

Frames carry dataset, cut, source limitation, retrospective descriptor and
content input identity. Feature contexts require matching dataset descriptors
for candles, peers, derivatives and references. Higher timeframe and peer
alignment use historical event close for retrospective analysis. Receipt
availability is retained unchanged and continues to govern ordinary as-of
and replay modes. Temporal caches include retrospective input identities.

## Existing research pipeline

Set `research.paths.candles` and `research.retrospective` explicitly. In
addition to the feed descriptor, discovery requires:

```yaml
research:
  referee: false
  handoff: false
  paths:
    candles: /frozen/candles.db
    derivs: /frozen/derivs.db
  retrospective:
    dataset_id: frozen-study-v1
    cut_ms: 1741452480000
    discovery_cut_source: /frozen/luffy.db
    discovery_cut_source_sha256: <SHA-256 of the preserved source>
```

The preserved `research_slices` row for the requested timeframe authenticates
the discovery cut. A mismatched source hash, missing cut or changed cut
refuses. A spent-cut cap can only tighten that boundary. Retrospective
ResearchRunner output requires a separate isolated output journal, bound to
the same configuration; the original study ledger cannot be reused. Matching
restarts retain that binding. Referee and handoff remain disabled.

ResearchRunner coverage and the loader use the same audit and close boundary,
with the existing total inventory minimum of 500 bars and discovery slice
minimum of 420 bars, 16-symbol discovery floor and separate control floor. The child retains RP-07 typed admission failures;
deterministic retry suppression includes dataset/contract identity. Held-out
markets and the later discovery era contribute timestamp/count metadata only.
Metadata coverage is inventory, not a claim of validation eligibility.

Results include dataset, input hashes, source limitations, authenticated cut,
configuration, compiler/evaluator identity, combination, seed and draw count.
Retrospective result identities are append-only: identical retries are no-ops;
conflicting replacement refuses. Original results, statistical looks and
learning receipts are not relabeled. Discovery remains discovery and produces
no Factory validation or activation.

## Strategy consumers and dependencies

Pass the same feed to existing `spec_evidence.load_frames` and related
consumers. Native timeframe and universe selection, `compile_spec`, feature
contexts, vector simulation/walk-forward, rolling evaluation and portfolio
accounting retain their distinct boundaries and statistical questions. No
engine, trade definition, seeds, thresholds or statistical budgets are added.

Offline research programs can select a JSON descriptor with
`--dataset-manifest PATH` (or configuration `offline_dataset`). Supported
programs include authored gauntlet, mechanism screen, universe selection and
extension, spec variants, analyst grid and deployment frontier. The mechanism
screen excludes protected held-out markets in retrospective mode and reports
discovery only; protected validation is unavailable through this option.
Historical journal-dependent programs also require explicit `paths.journal`.

Derivative and reference readers reuse existing stores in read-only mode.
Every dependency must have a selected dataset path; no default production
store is used. Retrospective dependent observations require valid retained
source/instrument receipts and original acquisition metadata. Missing records,
unknown sources, insufficient coverage or incompatible bindings refuse.
References align at their declared close and retain stale-value checks.
Legacy plain derivative/reference projections cannot prove trustworthy source
binding and remain unavailable. Dataset-bound world reconstruction is not
established by candle history; requests lacking it refuse.

The existing fee, slippage and flat funding configuration describes modeled
costs. It does not establish actual funding, liquidity or fills. With
`real_funding: true`, retrospective funding must come from the selected
trustworthy settlement history with complete required coverage; silent flat
substitution is forbidden. An OHLCV-only comparison may explicitly use
`real_funding: false` and report configured cost assumptions. Actual and null
runs retain the same cost model. Live snapshot evaluation refuses retrospective
frames, and no result is automatically admitted to trading.

## Verification

Synthetic tests exercise 16-symbol discovery, evaluator and backtest
reproducibility, late-acquisition replay refusal, source corruption, protected
price boundaries, immutable result identities, dataset isolation and dependency
refusals. Existing research, strategy, provenance and safety suites provide
compatibility checks. `scripts/verify_retrospective_history.py` verifies an
explicitly authorized frozen archive against its preserved cut, evaluates only
discovery, backtests an existing seed specification, and checks repeat identities
and before/after archive hashes. It does not run Referee, spend alpha or activate
Factory. Its SQL guard requires bounded discovery predicates for every loaded
price projection and retained candle receipt.

Implementation is based on verified production `fc6c0b2` plus the preserved
prerequisite history through RP-07 `4cf0a96`. Only the task commits are new.
Integration must carry those prerequisites or reconcile equivalent contracts,
review the retained test evidence and supply explicit dataset manifests.
There is no automatic production merge, deployment or runtime start.
