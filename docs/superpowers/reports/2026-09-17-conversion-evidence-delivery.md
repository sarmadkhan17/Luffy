# M8.1 actual conversion evidence: bounded capture and explicit attribution limit

Owner approved the actual-conversion receipt design in this session. The manual
evidence capture/replay foundation is implemented and tested. **Accepted non-USDT
conversion accounting is NOT implemented:** the inspected provider endpoint lacks
the required cashflow attribution. M8.1 remains partial, count **12/55**.

## Delivered

[Conversion evidence](../../../trader/engine/conversion_evidence.py) embeds the
unchanged whole-trade capture and identifies the exact commission/funding row by
kind, ID and source digest. Signed native decimal amounts are preserved without
binary-float conversion or decimal-context rounding. Operator-selected conversion
order IDs are explicitly proposals, never venue-proven joins.

[Manual CLI](../../../scripts/capture_trade_conversions.py) queries only the signed
read-only futures conversion order-status endpoint, with a five-second request
timeout and at most eight unique requests. It creates a new exclusive artifact;
retries require new files. Unknown/malformed cashflow IDs, incomplete histories,
reused cashflows/conversion IDs, noncanonical IDs and request overflow fail before
network calls. Response fields are allowlisted, invalid numeric values remain
missing, and failures record exception types rather than credential-bearing text.
No quote request, acceptance, conversion order or trading order is submitted.

```bash
# KIND is commission (fill ID) or funding (income transaction ID).
# Use actual existing IDs; the relationship remains an unverified proposal.
./venv/bin/python -m scripts.capture_trade_conversions --whole-trade /path/to/whole.json --request commission:FILL_ID:CONVERT_ORDER_ID --output /path/to/NEW-conversion.json
./venv/bin/python -m scripts.capture_trade_conversions --replay /path/to/NEW-conversion.json
```

The receipt validates observed status, order ID, currencies, direction, exact
native quantity and clocks. Native cashflows and raw conversion amounts remain
separate. Replay repeats the source verification and assessment; rehashed
assessment promotion is rejected. The assessment always retains null attributed
USDT/net, learning_eligible=false and explicit retries for unsupported attribution,
account linkage and complete conversion-cost evidence. It cannot be imported as
a complete verified trade. Existing whole-trade v1 semantics are unchanged;
there was no need to introduce an accepting v2 format for unsupported evidence.

## Provider limit (verified documentation, not a live transaction)

Binance's [Futures Convert order-status contract](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/convert)
returns order status, source/destination amounts/assets, ratios and create time.
It does not document an originating commission/funding ID, account identity in
this response, or independent complete-cost evidence. Inference: it can support
an observed conversion transaction but cannot prove the approved one-to-one
cashflow attribution by itself. A supplied mapping, amount/time match or market
price cannot fill that gap. No alternate valuation policy was introduced.

No known natural conversion order IDs were available; no live conversion was
captured and demo endpoint availability is unverified. A future authoritative
statement/endpoint that links these records must be verified before an accepting
adapter is built. This is a concrete external evidence limitation, not evidence
that non-USDT cashflows have zero cost.

## Validation and runtime

**95 final-source tests passed in 3.84 seconds** across conversion evidence,
whole-trade accounting, accounting worker and typed outcomes. Tests include
positive/negative native flows, multiple currencies, exact decimals, missing and
malformed values, duplicate IDs, inconsistent currencies, future timestamps,
failed requests, redaction, tampering, source-free CLI replay and overwrite refusal.
Successful synthetic conversion responses remain unverified for attribution;
there is deliberately no synthetic-only provider that grants live acceptance.
Five earlier frozen booking/emergency/whole-trade artifacts replayed unchanged.
[Verification/hashes](../artifacts/execution-accounting/2026-09-17-conversion-verification.json)
and [synthetic receipt](../artifacts/execution-accounting/2026-09-17-conversion-synthetic.json)
are retained. These are engineering checks, not observed profitability.

[Read-only runtime](../artifacts/execution-accounting/2026-09-16-worker-runtime-002128.json),
actual **September 17 00:21:25 UTC**: ACTIVE/demo, unchanged kernel PID 300976;
XRP short 68.9, LINK short 36.24 and AAVE short 0.9 match journal and native
protective stops. The inherited filename's September 16 prefix is historical;
embedded UTC is authoritative. Zero natural booking/emergency accounting receipts.
Scheduled worker at 00:20:03 UTC: ok, empty queue/attempts, auto-import false.
Frozen hashes match, watchdog and population collection remain enabled.
Investigation health remains degraded by the earlier exact-source refusals.

No worker, kernel, dashboard, memory importer, trading/risk config or research
control was changed/restarted. Manual CLI is available now; automatic capture
integration was intentionally outside the approved initial manual slice.
Claude remained session-limited; existing direct implementation authorization was
used. Sandbox namespace remains unavailable; approved external execution used.

## Exact next work

Continue natural booking export/replay and scheduled immutable accounting attempts;
when complete natural evidence exists, verify explicit forward-only memory import.
For conversion acceptance, obtain an authoritative venue linkage to the specific
cashflow/account plus complete-cost evidence; do not infer attribution or add a
valuation policy. Emergency/native/ambiguous exit provenance and separately
reviewed reporting migration remain dependency-ready but require their own design
review before changes. Do not rebuild delivered accounting or conversion capture.

Preserve original exact-version retries. Next forecast maturities remain September
17 04:00 UTC (two) and 08:00 UTC (15), investigations 16:00 UTC; full population
review September 18 00:00 UTC. No new maturity boundary occurred during this slice;
reuse the 00:12:59 maturity review. No M3.2, Gate 2, forced trades, new candidate,
admission or performance claim. Referee/handoff remain false.

## Subsequent owner-authorized endpoint checks — September 17 00:31 UTC

The owner separately authorized read-only production diagnostics with supplied
credentials; this does not authorize real-money trading or changing demo scope.
[Sanitized endpoint evidence](../artifacts/execution-accounting/2026-09-17-conversion-endpoint-checks.json):
demo public conversion pairs returned HTTP 500 / -1000; production public pairs
returned HTTP 200 and BNB/USDT. Signed production feeBurn returned HTTP 401 /
-2015; signed Convert tradeFlow (30-day window) returned HTTP 400 / -2008.
No private conversion records were obtained. Public success does not validate
these credentials or prove cashflow attribution; demo failure does not conclusively
establish endpoint absence. Credentials were supplied transiently with terminal
echo disabled, not saved to project files. No orders/conversions/configuration
changes. The owner was advised to revoke the credentials exposed in chat.
