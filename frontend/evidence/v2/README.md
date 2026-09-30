# Owner frontend V2 evidence

These screenshots show the **production LIVE frontend build against the isolated test server**, not production account state. The test server uses the real FastAPI app, temporary seeded journal/vault and fake Owner Interface/chat. No LIVE adapter uses the DEMO fixture adapter.

| Route | Desktop, 1440×1080 | Mobile, 390×844 |
|---|---|---|
| Overview | [Desktop](overview.png) | [Mobile](mobile-overview.png) |
| Trades | [Desktop](trades.png) | [Mobile](mobile-trades.png) |
| Research | [Desktop](research.png) | [Mobile](mobile-research.png) |
| Strategies | [Desktop](strategies.png) | [Mobile](mobile-strategies.png) |
| LUFFY | [Desktop](luffy.png) | [Mobile](mobile-luffy.png) |
| Operations | [Desktop](operations.png) | [Mobile](mobile-operations.png) |
| Live System | [Desktop](live-system.png) | [Mobile](mobile-live-system.png) |
| Knowledge | [Desktop](knowledge.png) | [Mobile](mobile-knowledge.png) |
| Diagnostics | [Desktop](diagnostics.png) | [Mobile](mobile-diagnostics.png) |

Additional interaction: [Knowledge path trace](mobile-path-trace.png).

- `browser-results-final.json`: final full suite results, supersedes earlier iteration results.
- `accessibility.json`: all nine LIVE routes × desktop/mobile; path-trace accessibility is additionally asserted by its browser test.
- `performance-live.json`: cold/warm/churn/heap measurements against the isolated test server.
- `demo-regression/`: separate existing DEMO fixture regression evidence, not LIVE or production evidence.
- `baseline-sha256.json`: start-of-turn source/test/backend fingerprints.
- `source-sha256.json`: final frontend source/test/build fingerprints.
- `preservation.json`: byte comparison of pre-existing dashboard backend files.
- `changed-files.txt`: exact V2 implementation, report and evidence manifest.
