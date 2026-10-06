# QNT-02 canonical reconciliation

Original condition: Predeclared mechanism-appropriate controls preserve irrelevant structure; positive/no-edge controls and finite-draw p-values are reproduced.

Result: CLOSED at architecture/offline engineering scope. No implementation changed.

- Existing null contracts declare circular signal rotation for timing claims and one common offset across every portfolio leg for market-wide claims. Market paths, exit geometry, costs/funding and shared cross-symbol alignment are held fixed; rotations change entry/market alignment rather than independently reshuffling symbols.
- Pinned regression controls specify geometry, risk/cost assumptions, synthetic generation, seeds and draw counts. Thirty no-edge per-symbol controls do not fabricate significance; six planted timing controls are detected. Planted oracle signals are synthetic test-only positive controls, not tradable future-informed features or admitted strategies.
- Common-rotation output repeats exactly for fixed inputs/seed; finite-draw p-values follow (1 + null outcomes at least actual)/(draws + 1), remain on the exact grid and never become zero. Shared-market synthetic checks demonstrate independent percentile votes cannot substitute for dependence-preserving common rotation.
- Dependence deflation is monotone in rho and agrees with the independent model only at zero dependence. No-edge consistency tests, ties, empty/short nulls and insufficient symbol counts remain nonsignificant or None/UNTESTED. Existing cost-symmetry and portfolio-null regressions pass. These are architecture/offline contract controls, not crypto-market calibration or held-out mechanism admission.

Retained validation: f866996 immutable engineering test package retained with exact source hashes: tests/test_qnt02_null_controls.py plus existing null_baseline/common_rotation contracts and related regression suites. No implementation was changed in that engineering commit or this reconciliation.

Fresh validation: 34 tests passed in 51.19s: tests/test_qnt02_null_controls.py, tests/test_null_baseline.py, tests/test_null_dependence.py, tests/test_null_cost_symmetry.py and tests/test_research_portfolio_null.py; exact source/test bytes match f866996.

QNT-06 is now dependency-eligible; its declared-experiment power and UNTESTED/FAIL/PASS proof remains its own work. No successor selected.

Exact sources, dependencies, limits and control validation are in reconciliation.json and control-validation.json.
