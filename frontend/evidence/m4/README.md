# LUFFY M4 review evidence

All captures use the temporary seeded LIVE-contract test server. Values, times, and records in screenshots are synthetic test data. No production service was changed.

## Visual sources

- `/home/sarmad/trader-owner-frontend/LUFFY-approved-visual-references/01-luffy-approved-reference.png`
- `/home/sarmad/trader-owner-frontend/LUFFY-approved-visual-references/02-knowledge-approved-reference.png`
- `/home/sarmad/trader-owner-frontend/LUFFY-approved-visual-references/03-live-system-approved-reference.png`
- `/home/sarmad/trader-owner-frontend/SDD.md` and this M4 brief

The approved Overview and Trades landscape images and a separate mechanical humanoid ocean image described in the brief are absent from the local approved-reference folder and its ZIP. The owner-supplied `/home/sarmad/trader/.claude/worktrees/owner-frontend-m3` was also inspected: its images are earlier implementation evidence, not the missing approved references. The board marks the missing Overview and Trades image cells; it does not substitute M3 screenshots. The three available mockups contain invented activity and copy, which were not imported into the UI.

## Captures

The nine `desktop-*.png` files are 1440 × 1000 viewport captures at the same size. Detail and mobile captures are `trade-story.png`, `strategy-workspace.png`, `research-detail.png`, `knowledge-selected.png`, `live-system-selected.png`, `luffy-active.png`, and `mobile-*.png` at 390 × 844. `side-by-side-board.webp` compares the available approved images with the M4 captures.

`motion-observations.json` records UI state and computed animation behavior for LUFFY, Knowledge path selection, fresh observed flow, static declared flow, trade stages, route entry, and reduced motion. `motion-*.png` contains companion frames. The demo adapter emits actual partial reply chunks for the idle → pending → responding → idle state check. The LIVE chat adapter currently returns a complete reply in one response; no production streaming state was invented.

## Checks and limits

- `m3-regression.json`: M3 capture/accessibility/mobile regression, 4/4 passed after the trade-chain overflow fix.
- `browser-results.json`: M4 fixed-size capture and motion tests, 3/3 passed.
- `full-browser-results.json`: first complete browser run, 102/104 passed. The two failures were a keyboard-inaccessible horizontally scrollable Trade strip and a decorative heading pseudo-element.
- `targeted-results.json` and `v21-targeted.json`: both failures passed after fixes.
- `final-browser-results.json`: final complete browser run after all source changes, **104/104 passed**. This includes accessibility, M1 concurrency/routing, M2 workflow, M3 functional and mobile, and topology regression checks.
- `browser-results.json`: final M4 capture and motion rerun, **3/3 passed**.
- Frontend unit suite: 139/139 passed. Production typecheck/build passed.
- `performance-comparison.json`: seeded test-server timing versus the prior M3 capture. Several cold/cached measures are slower; the comparison is affected by separate run conditions. M1 routing/concurrency checks passed, but this performance result does not establish parity.

Visual review remains blocked: the side-by-side board still shows a material sophistication gap in the LUFFY, graph, topology, and overall product composition, and two named approved route images are unavailable. Functional checks do not close this visual criterion. The measured latency increases also prevent a performance-parity claim.
