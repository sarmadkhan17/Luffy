# Branch integration — September 17, 2026

Merged all existing local branch tips into `main` at the owner's request.

- `fix/market-data-truth` (`214947a`) carried current delivery work on rewritten history. Its pre-delivery tree matched main except for the README; merged with unrelated histories explicitly allowed, retaining main's README addition.
- `docs/claude-md-refresh` (`d3b640c`) contributed prior-history backtest warmup, production fee reading, the injectable MacroGuard clock, and removal of obsolete planning artifacts.
- `master` (`5b262d4`) and `feat/strategy-foundation` (`43d1fd3`) were already ancestors of the merged history.
- Resolved the guide conflict by retaining the current condensed guidance and documenting warm_window. Resolved MacroGuard by using one injectable clock for both cache age and event horizon.

Validation on the combined source:

- Full suite: **2015 passed, 5 failed**, 312.11 seconds.
- All five failures reproduced on an isolated archive of `214947a`: cognition import-boundary check plus four partial-close notional tests. These existing failures were not changed as part of branch integration.
- Backtest engine equivalence: **PASS**.
- Vector backtest benchmark: **PASS**, **373.9x** (25 runs).
- Second merge staged whitespace check: passed; no unresolved conflicts.

Logs are local temporary files: `/tmp/trader-branch-merge-tests.txt`, `/tmp/trader-merge-baseline-tests.txt`, `/tmp/trader-merge-equivalence.txt`, and `/tmp/trader-merge-benchmark.txt`.

Uncommitted changes in the separate Claude worktree were left untouched. Branches were retained; no push or process restart was issued. This integrates source, not runtime deployment or strategy-performance validation. M8.1/M3.1 acceptance and the existing next-session queue are unchanged.
