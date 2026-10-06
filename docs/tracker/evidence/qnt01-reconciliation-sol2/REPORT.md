# QNT-01 canonical reconciliation

Original condition: Pinned input/code/config reproduce returns, costs, expectancy/drawdown and evaluator/backtest behaviour; labels and cost assumptions are explicit.

Result: CLOSED at architecture/offline engineering scope. No implementation changed.

- quant-contract.v1 defines entry/exit labels, return, fees, slippage, funding, expectancy and closed-trade drawdown; run_gauntlet emits the definitions, ESTIMATED_MODEL cost basis and input/spec/config/engine digests.
- Pinned identical data/code/config reproduce identical train/test results and identity; data, config or spec changes change identity. Fees/slippage reduce net P&L; zero-trade expectancy is None; explicit closed-equity drawdown matches simulation.
- Signal prefix tests reject future-bar effects; exited trade labels/P&L reproduce after truncation at exit. walk_table/simulate match exact trades and R; legacy evaluator engine-equivalence test is nonvacuous and passes.
- Existing evidence admission retains required data/coverage, minimum OOS trade count, worst-symbol PF and configured robustness checks; missing derivative inputs stay UNTESTED. No PF-only gate or estimated-to-actual relabelling introduced. Existing DATA-03/DATA-05 source-cut semantics remain unchanged.

Retained validation: 9065cf6 engineering commit and its exact source/test bytes reused; additive contract metadata only, with no changed fill, cost or admission verdict. No standalone prior runtime result is inferred.

Fresh validation: 37 tests passed in 66.75s: tests/test_qnt01_quant_contract.py, tests/test_vector_backtest.py and tests/test_spec_evidence.py; source/test bytes match 9065cf6.

QNT-02 is now dependency-eligible; its concurrent engineering work and own closure condition are preserved. No successor selected.

Exact sources, dependencies, limits and control validation are in reconciliation.json and control-validation.json.
