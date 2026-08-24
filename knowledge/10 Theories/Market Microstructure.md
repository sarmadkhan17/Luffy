---
type: theory
family: flow
status: core-belief
---
# Market Microstructure

Price moves when aggressive orders consume passive liquidity.

## What we measure
- Order-book imbalance within ±2% of mid.
- Taker buy ratio (aggressor share) on 15m klines.
- Funding crowding: ≥0.06%/interval = crowded longs → fade fuel.
- Absorption: heavy volume, zero progress.

## Open questions
- [ ] Does demo-platform order-book depth mirror production? (verify before trusting flow on demo)
