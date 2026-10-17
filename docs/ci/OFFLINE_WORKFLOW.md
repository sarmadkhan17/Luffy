# Offline development workflow

One canonical offline branch: `offline-integration`, held by the offline CI integration repository.

1. Start a task from the canonical branch: `git clone --single-branch --branch offline-integration <CI state>/integration.git`.
2. Finish the task on a task branch and create a bundle: `git bundle create NAME.bundle <task-branch>`.
3. Submit it: `luffy-ci submit --sha256 $(sha256sum NAME.bundle | cut -d' ' -f1) NAME.bundle@refs/heads/<task-branch>`.
4. The CI integrates it onto a candidate, runs the lean core and every affected test offline, and updates
   `offline-integration` only when the verdict is PASS (or MANUAL_REVIEW with every finding explicitly reviewed
   and `luffy-ci approve` run). A BLOCKED or failed submission never moves the branch.
5. The next task starts again from step 1 and therefore from the updated head.

Operational checks (three service/store-dependent tests) are never part of this flow and never counted as passes.
No submission authorizes deployment, activation or trading.
