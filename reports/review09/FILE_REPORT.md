# Review09 file-by-file assessment

Exact inventory coverage206/206; classifications {'KEEP': 197, 'SIMPLIFY': 9}. KEEP means retain necessary function, not zero defects. SIMPLIFY is bounded disposition, optional proposals remain explicit.

## docs/superpowers/reports/2026-09-29-trade-history-paging-r1-evidence/query_breakdown.py — KEEP

Owner `dashboard`; baseline38 lines, final38. Baseline SHA256 `26ec375c0ff733ef99a50432f35328edadd7a77909a874ddec18fd8d8e9b8f98`; final `26ec375c0ff733ef99a50432f35328edadd7a77909a874ddec18fd8d8e9b8f98`.

**purpose:** Temporary-journal trade-history page/count/membership latency, memory, response-size and index-plan measurement.

**consumers:** Explicit offline benchmark invocation; trade-history paging report/provenance.

**evidence:** Builds10k/100k synthetic records; query_breakdown profiles40 repeats and10 membership scans. Large full benchmark intentionally not run for bounded remediation.

**design_boundary:** Performance evidence only; no production journal/provider use.

**costs_removal:** No service schedule/storage. Synthetic benchmarking can be expensive, especially deep whole-history traversal; preserved as reproducibility tool.

## docs/tracker/evidence/acc03-economics-sol/verify.py — KEEP

Owner `dashboard`; baseline59 lines, final59. Baseline SHA256 `f848edb56e2cf8014f6d60ae2d8157f3dc2122a70eec1d14c897ae5fadbbd0b8`; final `f848edb56e2cf8014f6d60ae2d8157f3dc2122a70eec1d14c897ae5fadbbd0b8`.

**purpose:** Frozen acc03-economics-sol tracker/control/proof reconciliation source.

**consumers:** Retained sibling closure/report/source-manifest and docs/tracker/bundle-manifest.json; explicit archival invocation only.

**evidence:** Exact historical row counts/selections/source hashes/manifest/XML assertions; ROOT=Path(__file__).resolve().parents[4]. Contains output/control writers; not executed against current checkout.

**design_boundary:** Immutable historical proof, not a maintained current validation runner. Scope-limited offline closure never grants trading authority. Hardcoded production path (STR01) preserved as archive, never invoked.

**costs_removal:** Tracked archival bytes only; no runtime schedule or automatic database growth. Removal or adapting historical counts would sever frozen provenance. APPLY flags/control output writers require original historical context.

## docs/tracker/evidence/accounting-truth-followup-sol/verify.py — KEEP

Owner `dashboard`; baseline33 lines, final33. Baseline SHA256 `0dfb92515fcfab4c362bf69f6d0f1b35a3723ef97cd0e9b475159a51eb66ae66`; final `0dfb92515fcfab4c362bf69f6d0f1b35a3723ef97cd0e9b475159a51eb66ae66`.

**purpose:** Frozen accounting-truth-followup-sol tracker/control/proof reconciliation source.

**consumers:** Retained sibling closure/report/source-manifest and docs/tracker/bundle-manifest.json; explicit archival invocation only.

**evidence:** Exact historical row counts/selections/source hashes/manifest/XML assertions; ROOT=Path(__file__).resolve().parents[4]. Contains output/control writers; not executed against current checkout.

**design_boundary:** Immutable historical proof, not a maintained current validation runner. Scope-limited offline closure never grants trading authority. Hardcoded production path (STR01) preserved as archive, never invoked.

**costs_removal:** Tracked archival bytes only; no runtime schedule or automatic database growth. Removal or adapting historical counts would sever frozen provenance. APPLY flags/control output writers require original historical context.

## docs/tracker/evidence/dec03-canonical-sol1/control_validation_test.py — KEEP

Owner `dashboard`; baseline330 lines, final330. Baseline SHA256 `b961eb348b2003a6cf17d0c031029db10475ff99498a41d215d43e6c93e09d48`; final `b961eb348b2003a6cf17d0c031029db10475ff99498a41d215d43e6c93e09d48`.

**purpose:** Frozen dec03-canonical-sol1 tracker/control/proof reconciliation source.

**consumers:** Retained sibling closure/report/source-manifest and docs/tracker/bundle-manifest.json; explicit archival invocation only.

**evidence:** Exact historical row counts/selections/source hashes/manifest/XML assertions; ROOT=Path(__file__).resolve().parents[4]; BASE=37d210787c448053c563c92436d9ac17ab672e12; ENG=1ec9870a09868d108293b10c3417ffd14368ed2e; VERSION=1.56-dec03-canonical-closed-sol1. Contains output/control writers; not executed against current checkout.

**design_boundary:** Immutable historical proof, not a maintained current validation runner. Scope-limited offline closure never grants trading authority. Hardcoded production path (STR01) preserved as archive, never invoked.

**costs_removal:** Tracked archival bytes only; no runtime schedule or automatic database growth. Removal or adapting historical counts would sever frozen provenance. APPLY flags/control output writers require original historical context.

## docs/tracker/evidence/exe04-canonical-sol1/reconcile.py — KEEP

Owner `dashboard`; baseline259 lines, final259. Baseline SHA256 `7a23040064271d03128f8fd9167eb9a0d93d778edf9a525a79aba131512d28cf`; final `7a23040064271d03128f8fd9167eb9a0d93d778edf9a525a79aba131512d28cf`.

**purpose:** Frozen exe04-canonical-sol1 tracker/control/proof reconciliation source.

**consumers:** Retained sibling closure/report/source-manifest and docs/tracker/bundle-manifest.json; explicit archival invocation only.

**evidence:** Exact historical row counts/selections/source hashes/manifest/XML assertions; ROOT=Path(__file__).resolve().parents[4]; BASE=ec2a68ed648c03d34cf99dcd9040ff14844eca92; ENG=916de7322dbc6ad85a66d7c6252f5d672c9bfcc1; VERSION=1.58-exe04-canonical-closed-sol1. Contains output/control writers; not executed against current checkout.

**design_boundary:** Immutable historical proof, not a maintained current validation runner. Scope-limited offline closure never grants trading authority. Hardcoded production path (STR01) preserved as archive, never invoked.

**costs_removal:** Tracked archival bytes only; no runtime schedule or automatic database growth. Removal or adapting historical counts would sever frozen provenance. APPLY flags/control output writers require original historical context.

## docs/tracker/evidence/gui01-overview-sol/verify.py — KEEP

Owner `dashboard`; baseline30 lines, final30. Baseline SHA256 `bd0c8130c2b0b247f57bece764cef5cd4ec8e5378ae9da8d75727be06aec1d7d`; final `bd0c8130c2b0b247f57bece764cef5cd4ec8e5378ae9da8d75727be06aec1d7d`.

**purpose:** Frozen gui01-overview-sol tracker/control/proof reconciliation source.

**consumers:** Retained sibling closure/report/source-manifest and docs/tracker/bundle-manifest.json; explicit archival invocation only.

**evidence:** Exact historical row counts/selections/source hashes/manifest/XML assertions; ROOT=Path(__file__).resolve().parents[4]. Contains output/control writers; not executed against current checkout.

**design_boundary:** Immutable historical proof, not a maintained current validation runner. Scope-limited offline closure never grants trading authority. Hardcoded production path (STR01) preserved as archive, never invoked.

**costs_removal:** Tracked archival bytes only; no runtime schedule or automatic database growth. Removal or adapting historical counts would sever frozen provenance. APPLY flags/control output writers require original historical context.

## docs/tracker/evidence/out03-replay-map-sol/verify.py — KEEP

Owner `dashboard`; baseline45 lines, final45. Baseline SHA256 `e0ce8261812b45dd1be37b31708faf411b2b8d978107516d9bd80fc04c1bd9c5`; final `e0ce8261812b45dd1be37b31708faf411b2b8d978107516d9bd80fc04c1bd9c5`.

**purpose:** Frozen out03-replay-map-sol tracker/control/proof reconciliation source.

**consumers:** Retained sibling closure/report/source-manifest and docs/tracker/bundle-manifest.json; explicit archival invocation only.

**evidence:** Exact historical row counts/selections/source hashes/manifest/XML assertions; ROOT=Path(__file__).resolve().parents[4]. Contains output/control writers; not executed against current checkout.

**design_boundary:** Immutable historical proof, not a maintained current validation runner. Scope-limited offline closure never grants trading authority. Hardcoded production path (STR01) preserved as archive, never invoked.

**costs_removal:** Tracked archival bytes only; no runtime schedule or automatic database growth. Removal or adapting historical counts would sever frozen provenance. APPLY flags/control output writers require original historical context.

## docs/tracker/evidence/own03-boundary-sol/verify.py — KEEP

Owner `dashboard`; baseline37 lines, final37. Baseline SHA256 `93f04bfd9c6bb97189b67549c629fbd30c51bae6663fc2e5141e7c17cd157cf3`; final `93f04bfd9c6bb97189b67549c629fbd30c51bae6663fc2e5141e7c17cd157cf3`.

**purpose:** Frozen own03-boundary-sol tracker/control/proof reconciliation source.

**consumers:** Retained sibling closure/report/source-manifest and docs/tracker/bundle-manifest.json; explicit archival invocation only.

**evidence:** Exact historical row counts/selections/source hashes/manifest/XML assertions; ROOT=Path(__file__).resolve().parents[4]. Contains output/control writers; not executed against current checkout.

**design_boundary:** Immutable historical proof, not a maintained current validation runner. Scope-limited offline closure never grants trading authority. Hardcoded production path (STR01) preserved as archive, never invoked.

**costs_removal:** Tracked archival bytes only; no runtime schedule or automatic database growth. Removal or adapting historical counts would sever frozen provenance. APPLY flags/control output writers require original historical context.

## docs/tracker/evidence/own04-phase-scope-review/verify.py — KEEP

Owner `dashboard`; baseline37 lines, final37. Baseline SHA256 `cfae4a5cd13a7abab49f14f3a2d3c710d6a3b51f8d841d02aac7ff7b2e17a87b`; final `cfae4a5cd13a7abab49f14f3a2d3c710d6a3b51f8d841d02aac7ff7b2e17a87b`.

**purpose:** Frozen own04-phase-scope-review tracker/control/proof reconciliation source.

**consumers:** Retained sibling closure/report/source-manifest and docs/tracker/bundle-manifest.json; explicit archival invocation only.

**evidence:** Exact historical row counts/selections/source hashes/manifest/XML assertions; ROOT=Path(__file__).resolve().parents[4]. Contains output/control writers; not executed against current checkout.

**design_boundary:** Immutable historical proof, not a maintained current validation runner. Scope-limited offline closure never grants trading authority. Hardcoded production path (STR01) preserved as archive, never invoked.

**costs_removal:** Tracked archival bytes only; no runtime schedule or automatic database growth. Removal or adapting historical counts would sever frozen provenance. APPLY flags/control output writers require original historical context.

## docs/tracker/evidence/own04-state-sol/verify.py — KEEP

Owner `dashboard`; baseline36 lines, final36. Baseline SHA256 `4c48c31531e1eb68389bd02ad30ad94e098f9bf42ea28c68abb830b08975f29c`; final `4c48c31531e1eb68389bd02ad30ad94e098f9bf42ea28c68abb830b08975f29c`.

**purpose:** Frozen own04-state-sol tracker/control/proof reconciliation source.

**consumers:** Retained sibling closure/report/source-manifest and docs/tracker/bundle-manifest.json; explicit archival invocation only.

**evidence:** Exact historical row counts/selections/source hashes/manifest/XML assertions; ROOT=Path(__file__).resolve().parents[4]. Contains output/control writers; not executed against current checkout.

**design_boundary:** Immutable historical proof, not a maintained current validation runner. Scope-limited offline closure never grants trading authority. Hardcoded production path (STR01) preserved as archive, never invoked.

**costs_removal:** Tracked archival bytes only; no runtime schedule or automatic database growth. Removal or adapting historical counts would sever frozen provenance. APPLY flags/control output writers require original historical context.

## docs/tracker/evidence/port01-canonical-sol1/control_validation_test.py — KEEP

Owner `dashboard`; baseline316 lines, final316. Baseline SHA256 `293d7797f88e5dfbeba2b4272d83bfac55ee64f5c738000acc08aa051eb3cc68`; final `293d7797f88e5dfbeba2b4272d83bfac55ee64f5c738000acc08aa051eb3cc68`.

**purpose:** Frozen port01-canonical-sol1 tracker/control/proof reconciliation source.

**consumers:** Retained sibling closure/report/source-manifest and docs/tracker/bundle-manifest.json; explicit archival invocation only.

**evidence:** Exact historical row counts/selections/source hashes/manifest/XML assertions; ROOT=Path(__file__).resolve().parents[4]; BASE=2b5d250c418aef3268eb53dc7d1efbd081efc44f; ENG=2ea4a95349062b7ec19cde6263fa8b3e8af45660; VERSION=1.57-port01-canonical-closed-sol1. Contains output/control writers; not executed against current checkout.

**design_boundary:** Immutable historical proof, not a maintained current validation runner. Scope-limited offline closure never grants trading authority. Hardcoded production path (STR01) preserved as archive, never invoked.

**costs_removal:** Tracked archival bytes only; no runtime schedule or automatic database growth. Removal or adapting historical counts would sever frozen provenance. APPLY flags/control output writers require original historical context.

## docs/tracker/evidence/port04-canonical-sol1/reconcile.py — KEEP

Owner `dashboard`; baseline202 lines, final202. Baseline SHA256 `5eb5072f32638746112c14da6626dc6bf2acb139c3172f7154de76f218c500d8`; final `5eb5072f32638746112c14da6626dc6bf2acb139c3172f7154de76f218c500d8`.

**purpose:** Frozen port04-canonical-sol1 tracker/control/proof reconciliation source.

**consumers:** Retained sibling closure/report/source-manifest and docs/tracker/bundle-manifest.json; explicit archival invocation only.

**evidence:** Exact historical row counts/selections/source hashes/manifest/XML assertions; ROOT=Path(__file__).resolve().parents[4]; BASE=b6b6e6016f76d0df4869312b1b7c1a4551ba0918; ENG=387d130dedfe259e707ddda77b5eab83aa493aa7; VERSION=1.60-port04-canonical-closed-sol1. Contains output/control writers; not executed against current checkout.

**design_boundary:** Immutable historical proof, not a maintained current validation runner. Scope-limited offline closure never grants trading authority. Hardcoded production path (STR01) preserved as archive, never invoked.

**costs_removal:** Tracked archival bytes only; no runtime schedule or automatic database growth. Removal or adapting historical counts would sever frozen provenance. APPLY flags/control output writers require original historical context.

## docs/tracker/evidence/qnt09-canonical-intake-sol1/validate.py — KEEP

Owner `dashboard`; baseline68 lines, final68. Baseline SHA256 `6315a27ae5d44a2b5e6d595a88b6177d46d736182b281d7892d0728221faca71`; final `6315a27ae5d44a2b5e6d595a88b6177d46d736182b281d7892d0728221faca71`.

**purpose:** Frozen qnt09-canonical-intake-sol1 tracker/control/proof reconciliation source.

**consumers:** Retained sibling closure/report/source-manifest and docs/tracker/bundle-manifest.json; explicit archival invocation only.

**evidence:** Exact historical row counts/selections/source hashes/manifest/XML assertions; ROOT=Path(__file__).resolve().parents[4]; BASE=a355486900371f6e5ba9605e2fb84da661139489. Contains output/control writers; not executed against current checkout.

**design_boundary:** Immutable historical proof, not a maintained current validation runner. Scope-limited offline closure never grants trading authority. Hardcoded production path (STR01) preserved as archive, never invoked.

**costs_removal:** Tracked archival bytes only; no runtime schedule or automatic database growth. Removal or adapting historical counts would sever frozen provenance. APPLY flags/control output writers require original historical context.

## docs/tracker/evidence/str01-canonical-sol1/control-validation.py — KEEP

Owner `dashboard`; baseline60 lines, final60. Baseline SHA256 `7cbcb60d99793e9415fa20b9447c739f683ec3b260e2f89fa487f97612fa0294`; final `7cbcb60d99793e9415fa20b9447c739f683ec3b260e2f89fa487f97612fa0294`.

**purpose:** Frozen str01-canonical-sol1 tracker/control/proof reconciliation source.

**consumers:** Retained sibling closure/report/source-manifest and docs/tracker/bundle-manifest.json; explicit archival invocation only.

**evidence:** Exact historical row counts/selections/source hashes/manifest/XML assertions; ROOT=Path('/mnt/luffy-data/luffy/production'). Contains output/control writers; not executed against current checkout.

**design_boundary:** Immutable historical proof, not a maintained current validation runner. Scope-limited offline closure never grants trading authority. Hardcoded production path (STR01) preserved as archive, never invoked.

**costs_removal:** Tracked archival bytes only; no runtime schedule or automatic database growth. Removal or adapting historical counts would sever frozen provenance. APPLY flags/control output writers require original historical context.

## docs/tracker/evidence/str02-canonical-sol1/control-validation.py — KEEP

Owner `dashboard`; baseline94 lines, final94. Baseline SHA256 `e486222f528a3f3f5de472e6a908566314ab31565fa91aa750886aed7df8a5c4`; final `e486222f528a3f3f5de472e6a908566314ab31565fa91aa750886aed7df8a5c4`.

**purpose:** Frozen str02-canonical-sol1 tracker/control/proof reconciliation source.

**consumers:** Retained sibling closure/report/source-manifest and docs/tracker/bundle-manifest.json; explicit archival invocation only.

**evidence:** Exact historical row counts/selections/source hashes/manifest/XML assertions; ROOT=Path(__file__).resolve().parents[4]. Contains output/control writers; not executed against current checkout.

**design_boundary:** Immutable historical proof, not a maintained current validation runner. Scope-limited offline closure never grants trading authority. Hardcoded production path (STR01) preserved as archive, never invoked.

**costs_removal:** Tracked archival bytes only; no runtime schedule or automatic database growth. Removal or adapting historical counts would sever frozen provenance. APPLY flags/control output writers require original historical context.

## docs/tracker/evidence/str03-canonical-sol1/control-validation.py — KEEP

Owner `dashboard`; baseline121 lines, final121. Baseline SHA256 `509fcd69dc6ee43bbfe4e7919c605459cece230fc5b2ea1d58a9358b91cfdbe3`; final `509fcd69dc6ee43bbfe4e7919c605459cece230fc5b2ea1d58a9358b91cfdbe3`.

**purpose:** Frozen str03-canonical-sol1 tracker/control/proof reconciliation source.

**consumers:** Retained sibling closure/report/source-manifest and docs/tracker/bundle-manifest.json; explicit archival invocation only.

**evidence:** Exact historical row counts/selections/source hashes/manifest/XML assertions; ROOT=Path(__file__).resolve().parents[4]. Contains output/control writers; not executed against current checkout.

**design_boundary:** Immutable historical proof, not a maintained current validation runner. Scope-limited offline closure never grants trading authority. Hardcoded production path (STR01) preserved as archive, never invoked.

**costs_removal:** Tracked archival bytes only; no runtime schedule or automatic database growth. Removal or adapting historical counts would sever frozen provenance. APPLY flags/control output writers require original historical context.

## frontend/.gitignore — KEEP

Owner `frontend`; baseline9 lines, final9. Baseline SHA256 `d947e50a2b6fcc8918cbcb93f6813f4f00cf60f600531989b0ddc370e9a55ff7`; final `d947e50a2b6fcc8918cbcb93f6813f4f00cf60f600531989b0ddc370e9a55ff7`.

**purpose:** Exclude dependency trees, generated builds/browser results, and preserved owner materials from commits.

**consumers:** npm/Vite/browser build command or developer/operator documentation

**evidence:** frontend/.gitignore:1; SDD.md:1768 (operator evidence), SDD.md:1795 (technology), SDD.md:1822 (routes), SDD.md:2367 (typed owner interface)

**design_boundary:** Presentation/read contract only; no direct venue order, provider credential or production-store write authority. Missing implementation connectivity alone is not a defect.

**costs_removal:** 122 bytes; 9 lines. No new dependency or storage model. Removal would break consumers or intentionally retained historical/test contract; no file merge/removal justified.

## frontend/HANDOFF.md — SIMPLIFY

Owner `frontend`; baseline213 lines, final218. Baseline SHA256 `df47487a83eb8249bca34747f73adb07200e9f7cced69a473f783393c9fceef5`; final `6401e334f8f4e94d42e58ad0d05bd033094507ba3401b2b581d40b50ce1078ef`.

**purpose:** Retain original V1 routing review, fixture performance measurements, preservation manifest and historical integration boundary.

**consumers:** npm/Vite/browser build command or developer/operator documentation

**evidence:** frontend/HANDOFF.md:1; SDD.md:1768 (operator evidence), SDD.md:1795 (technology), SDD.md:1822 (routes), SDD.md:2367 (typed owner interface); Dated status note distinguishes historical V1 preview claims from current explicit live/demo entry; original milestone evidence retained.

**design_boundary:** Presentation/read contract only; no direct venue order, provider credential or production-store write authority. Missing implementation connectivity alone is not a defect.

**costs_removal:** 14746 bytes; 218 lines. No new dependency or storage model. Removal would break consumers or intentionally retained historical/test contract; no file merge/removal justified.

## frontend/README.md — SIMPLIFY

Owner `frontend`; baseline91 lines, final100. Baseline SHA256 `2015c78763280a60ca095d4336d2d838b4e37cfc023c7d0adf9965f3ec4bc497`; final `bf152b05aa6d5a3b42176a423f7644e04cca7f700dbc0ff46cfffe547a1508fe`.

**purpose:** Explain frontend entry/build modes and preserve original preview milestone notes.

**consumers:** npm/Vite/browser build command or developer/operator documentation

**evidence:** frontend/README.md:1; SDD.md:1768 (operator evidence), SDD.md:1795 (technology), SDD.md:1822 (routes), SDD.md:2367 (typed owner interface); Dated status note distinguishes historical V1 preview claims from current explicit live/demo entry; original milestone evidence retained.

**design_boundary:** Presentation/read contract only; no direct venue order, provider credential or production-store write authority. Missing implementation connectivity alone is not a defect.

**costs_removal:** 6379 bytes; 100 lines. No new dependency or storage model. Removal would break consumers or intentionally retained historical/test contract; no file merge/removal justified.

## frontend/index.html — KEEP

Owner `frontend`; baseline17 lines, final17. Baseline SHA256 `e1ad3783bddc3615fda8525c58f5cc8ba4dcda450558d1be994b01eb3ccf0a8f`; final `e1ad3783bddc3615fda8525c58f5cc8ba4dcda450558d1be994b01eb3ccf0a8f`.

**purpose:** Browser HTML root with restrictive self-only CSP and Vite module entry.

**consumers:** npm/Vite/browser build command or developer/operator documentation

**evidence:** frontend/index.html:1; SDD.md:1768 (operator evidence), SDD.md:1795 (technology), SDD.md:1822 (routes), SDD.md:2367 (typed owner interface)

**design_boundary:** Presentation/read contract only; no direct venue order, provider credential or production-store write authority. Missing implementation connectivity alone is not a defect.

**costs_removal:** 624 bytes; 17 lines. No new dependency or storage model. Removal would break consumers or intentionally retained historical/test contract; no file merge/removal justified.

## frontend/package-lock.json — KEEP

Owner `frontend`; baseline3366 lines, final3366. Baseline SHA256 `06e58a94fafc69363ec9e56c5adec574315b1b767cc458ea26e6824bc3ade16d`; final `06e58a94fafc69363ec9e56c5adec574315b1b767cc458ea26e6824bc3ade16d`.

**purpose:** Pin the complete npm dependency graph for reproducible frontend builds.

**consumers:** npm/Vite/browser build command or developer/operator documentation

**evidence:** frontend/package-lock.json:1; SDD.md:1768 (operator evidence), SDD.md:1795 (technology), SDD.md:1822 (routes), SDD.md:2367 (typed owner interface)

**design_boundary:** Presentation/read contract only; no direct venue order, provider credential or production-store write authority. Missing implementation connectivity alone is not a defect.

**costs_removal:** 115070 bytes; 3366 lines. 215 locked packages; manifest root dependency maps verified equal. Removal sacrifices reproducibility; no install/update performed.

## frontend/package.json — KEEP

Owner `frontend`; baseline45 lines, final45. Baseline SHA256 `b8adfa67eea537db3f1027c4774ab650b8eb0ed233d6bc8577cc15ee8649b158`; final `b8adfa67eea537db3f1027c4774ab650b8eb0ed233d6bc8577cc15ee8649b158`.

**purpose:** Declare reviewed React/TypeScript/Vite/query/chart/motion dependencies and explicit live/demo/test commands.

**consumers:** npm/Vite/browser build command or developer/operator documentation

**evidence:** frontend/package.json:1; SDD.md:1768 (operator evidence), SDD.md:1795 (technology), SDD.md:1822 (routes), SDD.md:2367 (typed owner interface)

**design_boundary:** Presentation/read contract only; no direct venue order, provider credential or production-store write authority. Missing implementation connectivity alone is not a defect.

**costs_removal:** 1481 bytes; 45 lines. No new dependency or storage model. Removal would break consumers or intentionally retained historical/test contract; no file merge/removal justified.

## frontend/playwright.config.ts — KEEP

Owner `frontend`; baseline56 lines, final56. Baseline SHA256 `94b6a6dc941a316396db19adfa3d73d995be0e2c09c306f1e11c28234bf504b0`; final `94b6a6dc941a316396db19adfa3d73d995be0e2c09c306f1e11c28234bf504b0`.

**purpose:** Configure demo, fixture harness and temporary FastAPI fixture-server regression targets.

**consumers:** frontend/playwright.m3.config.ts

**evidence:** frontend/playwright.config.ts:1; SDD.md:1768 (operator evidence), SDD.md:1795 (technology), SDD.md:1822 (routes), SDD.md:2367 (typed owner interface)

**design_boundary:** Presentation/read contract only; no direct venue order, provider credential or production-store write authority. Missing implementation connectivity alone is not a defect.

**costs_removal:** 1877 bytes; 56 lines. No new dependency or storage model. Removal would break consumers or intentionally retained historical/test contract; no file merge/removal justified.

## frontend/playwright.glacier-v2.config.ts — KEEP

Owner `frontend`; baseline11 lines, final11. Baseline SHA256 `9f024d266852c77fac6042bfe28e932ea866759717b7904965a26ed08586ad0d`; final `9f024d266852c77fac6042bfe28e932ea866759717b7904965a26ed08586ad0d`.

**purpose:** Configure intercepted-fixture static production-assets Glacier v2 browser regression.

**consumers:** npm/Vite/browser build command or developer/operator documentation

**evidence:** frontend/playwright.glacier-v2.config.ts:1; SDD.md:1768 (operator evidence), SDD.md:1795 (technology), SDD.md:1822 (routes), SDD.md:2367 (typed owner interface)

**design_boundary:** Presentation/read contract only; no direct venue order, provider credential or production-store write authority. Missing implementation connectivity alone is not a defect.

**costs_removal:** 896 bytes; 11 lines. No new dependency or storage model. Removal would break consumers or intentionally retained historical/test contract; no file merge/removal justified.

## frontend/playwright.glacier.config.ts — KEEP

Owner `frontend`; baseline11 lines, final11. Baseline SHA256 `d1b97e97d38b80a06873ab65567e40c6a3b06889e54cbbddfab66f51bf4513b1`; final `d1b97e97d38b80a06873ab65567e40c6a3b06889e54cbbddfab66f51bf4513b1`.

**purpose:** Configure intercepted-fixture static Glacier correction browser regression.

**consumers:** npm/Vite/browser build command or developer/operator documentation

**evidence:** frontend/playwright.glacier.config.ts:1; SDD.md:1768 (operator evidence), SDD.md:1795 (technology), SDD.md:1822 (routes), SDD.md:2367 (typed owner interface)

**design_boundary:** Presentation/read contract only; no direct venue order, provider credential or production-store write authority. Missing implementation connectivity alone is not a defect.

**costs_removal:** 862 bytes; 11 lines. No new dependency or storage model. Removal would break consumers or intentionally retained historical/test contract; no file merge/removal justified.

## frontend/playwright.gui01.config.ts — KEEP

Owner `frontend`; baseline5 lines, final5. Baseline SHA256 `cc5ed300f396942c71d8899214c530f169976ee7c4f85973cacde30a6f608c4a`; final `cc5ed300f396942c71d8899214c530f169976ee7c4f85973cacde30a6f608c4a`.

**purpose:** Configure read-only GUI fixture-server browser regression.

**consumers:** npm/Vite/browser build command or developer/operator documentation

**evidence:** frontend/playwright.gui01.config.ts:1; SDD.md:1768 (operator evidence), SDD.md:1795 (technology), SDD.md:1822 (routes), SDD.md:2367 (typed owner interface)

**design_boundary:** Presentation/read contract only; no direct venue order, provider credential or production-store write authority. Missing implementation connectivity alone is not a defect.

**costs_removal:** 679 bytes; 5 lines. No new dependency or storage model. Removal would break consumers or intentionally retained historical/test contract; no file merge/removal justified.

## frontend/playwright.m3.config.ts — KEEP

Owner `frontend`; baseline23 lines, final23. Baseline SHA256 `a10a65459556f64d35d9e70e65a98cee16e8bd56b21e1d51212294a83a2ae6e2`; final `a10a65459556f64d35d9e70e65a98cee16e8bd56b21e1d51212294a83a2ae6e2`.

**purpose:** Select M3 capture tests and only the temporary fixture backend from shared browser configuration.

**consumers:** npm/Vite/browser build command or developer/operator documentation

**evidence:** frontend/playwright.m3.config.ts:1; SDD.md:1768 (operator evidence), SDD.md:1795 (technology), SDD.md:1822 (routes), SDD.md:2367 (typed owner interface)

**design_boundary:** Presentation/read contract only; no direct venue order, provider credential or production-store write authority. Missing implementation connectivity alone is not a defect.

**costs_removal:** 662 bytes; 23 lines. No new dependency or storage model. Removal would break consumers or intentionally retained historical/test contract; no file merge/removal justified.

## frontend/playwright.stage8.config.ts — KEEP

Owner `frontend`; baseline7 lines, final7. Baseline SHA256 `d12ee3f925f490e377c58ce9e8bd3cf99b2db83133d36085f7d3787e3e8313bb`; final `d12ee3f925f490e377c58ce9e8bd3cf99b2db83133d36085f7d3787e3e8313bb`.

**purpose:** Configure stage8/live fixture backend regressions with explicit isolated port/output.

**consumers:** npm/Vite/browser build command or developer/operator documentation

**evidence:** frontend/playwright.stage8.config.ts:1; SDD.md:1768 (operator evidence), SDD.md:1795 (technology), SDD.md:1822 (routes), SDD.md:2367 (typed owner interface)

**design_boundary:** Presentation/read contract only; no direct venue order, provider credential or production-store write authority. Missing implementation connectivity alone is not a defect.

**costs_removal:** 790 bytes; 7 lines. No new dependency or storage model. Removal would break consumers or intentionally retained historical/test contract; no file merge/removal justified.

## frontend/src/Shell.tsx — SIMPLIFY

Owner `frontend`; baseline285 lines, final275. Baseline SHA256 `97c789cad241b93660d4fad75fa9d75d92ef7625a6f0247d333a1b326b6fe091`; final `4bdd07cee0e3e8819fd2c364a02f4790c65840ce1ee3aa0e4b17b765f2a1818b`.

**purpose:** Dispatch ten owner routes, keyboard rail navigation, lazy-load failure recovery, mode banner and shell chrome.

**consumers:** frontend/src/demo.tsx; frontend/src/liveRoot.tsx; frontend/src/main.tsx; frontend/tests/review09.test.tsx

**evidence:** frontend/src/Shell.tsx:1; SDD.md:1768 (operator evidence), SDD.md:1795 (technology), SDD.md:1822 (routes), SDD.md:2367 (typed owner interface); Removed unsupported demo-environment literal; runtime_truth/accountLabel remains source of environment evidence. Removed shadowed Routes diagnostics branch and unreachable live-SystemObserved child.

**design_boundary:** Presentation/read contract only; no direct venue order, provider credential or production-store write authority. Missing implementation connectivity alone is not a defect.

**costs_removal:** 12652 bytes; 275 lines. No new dependency or storage model. Removal would break consumers or intentionally retained historical/test contract; no file merge/removal justified.

## frontend/src/adapters/contracts.ts — KEEP

Owner `frontend`; baseline285 lines, final285. Baseline SHA256 `4aa918fbf6c5321853eec54c886153dbe7da3fb42d1b32a57b374881e9128811`; final `4aa918fbf6c5321853eec54c886153dbe7da3fb42d1b32a57b374881e9128811`.

**purpose:** Define explicit live/demo, provenance, protection, graph, control, history and owner-read TypeScript contracts.

**consumers:** frontend/src/adapters/fixture.ts; frontend/src/adapters/live.ts; frontend/src/adapters/pending.ts; frontend/src/components/HealthStatus.tsx; frontend/src/components/OwnerControls.tsx; frontend/src/components/PathTrace.tsx; frontend/src/components/records.tsx; frontend/src/components/ui.tsx; frontend/src/context.tsx; frontend/src/protectionExpiry.ts; frontend/src/telemetryExpiry.ts; frontend/src/useOwnerTelemetry.ts; frontend/src/views/GraphView.tsx; frontend/src/views/Operations.tsx; frontend/src/views/Overview.tsx; frontend/src/views/Routes.tsx; frontend/src/views/Trades.tsx; frontend/src/views/graphGeometry.ts; frontend/src/views/graphPath.ts; frontend/src/views/graphScope.ts; frontend/tests/closeTradeDeferred.test.tsx; frontend/tests/components.test.tsx; frontend/tests/graphPath.test.ts; frontend/tests/harness/main.tsx; frontend/tests/live.test.tsx; frontend/tests/m1.test.tsx; frontend/tests/m2.test.tsx; frontend/tests/own04.test.tsx; frontend/tests/protectionSnapshot.test.tsx; frontend/tests/review09.test.tsx; frontend/tests/stage8.test.tsx; frontend/tests/tradeHistory.test.tsx; frontend/tests/v21.test.tsx

**evidence:** frontend/src/adapters/contracts.ts:1; SDD.md:1768 (operator evidence), SDD.md:1795 (technology), SDD.md:1822 (routes), SDD.md:2367 (typed owner interface)

**design_boundary:** Presentation/read contract only; no direct venue order, provider credential or production-store write authority. Missing implementation connectivity alone is not a defect.

**costs_removal:** 9050 bytes; 285 lines. No new dependency or storage model. Removal would break consumers or intentionally retained historical/test contract; no file merge/removal justified.

## frontend/src/adapters/fixture.ts — KEEP

Owner `frontend`; baseline286 lines, final286. Baseline SHA256 `8cb14b0d537923f04971835780f1d966505e6a1d81958e0fb0882fb04f6e7bee`; final `8cb14b0d537923f04971835780f1d966505e6a1d81958e0fb0882fb04f6e7bee`.

**purpose:** Provide cancellable synthetic account/graph/chat scenarios for explicit demo/tests only.

**consumers:** frontend/src/demo.tsx; frontend/tests/components.test.tsx; frontend/tests/contracts.test.ts; frontend/tests/glacierTruth.test.tsx; frontend/tests/harness/main.tsx; frontend/tests/health.test.tsx; frontend/tests/review09.test.tsx

**evidence:** frontend/src/adapters/fixture.ts:1; SDD.md:1768 (operator evidence), SDD.md:1795 (technology), SDD.md:1822 (routes), SDD.md:2367 (typed owner interface)

**design_boundary:** Intentional isolated synthetic demo/test boundary. Production main selects live; live adapter has no fixture import or fallback.

**costs_removal:** 7991 bytes; 286 lines. No new dependency or storage model. Removal would break consumers or intentionally retained historical/test contract; no file merge/removal justified.

## frontend/src/adapters/live.ts — SIMPLIFY

Owner `frontend`; baseline816 lines, final817. Baseline SHA256 `c3bbc49fac974fb3595cb6b43b4447610af6dcce73c45551df9eb38ec09b3619`; final `4681d4ea03a8830c7eb76340521bc03ff1698c7acb6eb5c8f0faa845ce7fd002`.

**purpose:** Bind authenticated no-store transport and validated backend reads; map account/protection/graph/mention/history evidence; retain typed owner control idempotency.

**consumers:** frontend/src/context.tsx; frontend/src/liveRoot.tsx; frontend/tests/closeTradeDeferred.test.tsx; frontend/tests/glacierTruth.test.tsx; frontend/tests/live.test.tsx; frontend/tests/m1.test.tsx; frontend/tests/m2.test.tsx; frontend/tests/own04.test.tsx; frontend/tests/protectionSnapshot.test.tsx; frontend/tests/review09.test.tsx; frontend/tests/stage8.test.tsx; frontend/tests/tracker-phase.test.ts; frontend/tests/tradeHistory.test.tsx; frontend/tests/v21.test.tsx; frontend/tests/workspace.test.tsx

**evidence:** frontend/src/adapters/live.ts:1; SDD.md:1768 (operator evidence), SDD.md:1795 (technology), SDD.md:1822 (routes), SDD.md:2367 (typed owner interface); ReadOnlyBoundary rejects POST; adapter now omits approvalDecision alongside control so NeedsYou cannot offer guaranteed-refused writes.

**design_boundary:** Typed Owner Interface is sole mutation authority; retries preserve request identity; read-only backend blocks writes. Frontend optional write methods are omitted in read-only adapter.

**costs_removal:** 30772 bytes; 817 lines. Bounded HTTP read polling/cache or mappings; no UI database writer. Polling/validation are intentional correctness boundaries, not redundant services.

## frontend/src/adapters/pending.ts — KEEP

Owner `frontend`; baseline81 lines, final81. Baseline SHA256 `9fead5a2cc0e8a652f4d883c5a3cb2fa06b5c647ec0cfbde1dd63605e1e4a7f4`; final `9fead5a2cc0e8a652f4d883c5a3cb2fa06b5c647ec0cfbde1dd63605e1e4a7f4`.

**purpose:** Persist each unresolved typed owner request separately and retry the oldest same action with the same id.

**consumers:** frontend/src/components/OwnerControls.tsx; frontend/src/components/OwnerEvidence.tsx; frontend/src/views/Operations.tsx; frontend/tests/live.test.tsx; frontend/tests/stage8.test.tsx

**evidence:** frontend/src/adapters/pending.ts:1; SDD.md:1768 (operator evidence), SDD.md:1795 (technology), SDD.md:1822 (routes), SDD.md:2367 (typed owner interface)

**design_boundary:** Typed Owner Interface is sole mutation authority; retries preserve request identity; read-only backend blocks writes. Frontend optional write methods are omitted in read-only adapter.

**costs_removal:** 2457 bytes; 81 lines. Per-request localStorage/in-memory state only; no production database access. Removal loses retry/session conversation contract.

## frontend/src/adapters/readContracts.ts — KEEP

Owner `frontend`; baseline338 lines, final338. Baseline SHA256 `1086541fffa650617f3fef9bc798bb445148433492999668c1b02efb55a11ec3`; final `1086541fffa650617f3fef9bc798bb445148433492999668c1b02efb55a11ec3`.

**purpose:** Validate read response shapes and completeness invariants; reject malformed evidence and incomplete graph proofs.

**consumers:** frontend/src/adapters/live.ts; frontend/src/views/Evidence.tsx; frontend/tests/acc03.test.tsx; frontend/tests/glacierTruth.test.tsx; frontend/tests/stage8.test.tsx

**evidence:** frontend/src/adapters/readContracts.ts:1; SDD.md:1768 (operator evidence), SDD.md:1795 (technology), SDD.md:1822 (routes), SDD.md:2367 (typed owner interface)

**design_boundary:** Presentation/read contract only; no direct venue order, provider credential or production-store write authority. Missing implementation connectivity alone is not a defect.

**costs_removal:** 11767 bytes; 338 lines. No new dependency or storage model. Removal would break consumers or intentionally retained historical/test contract; no file merge/removal justified.

## frontend/src/adapters/tracker.ts — KEEP

Owner `frontend`; baseline41 lines, final41. Baseline SHA256 `d78d15e13870ab2a69e8aaab5ec6c5f80cf050cb437e46f833b039e37f916fac`; final `d78d15e13870ab2a69e8aaab5ec6c5f80cf050cb437e46f833b039e37f916fac`.

**purpose:** Validate canonical read-only tracker rows, selected package, references, status counts and seven gate identities.

**consumers:** frontend/src/views/Tracker.tsx; frontend/tests/tracker-phase.test.ts

**evidence:** frontend/src/adapters/tracker.ts:1; SDD.md:1768 (operator evidence), SDD.md:1795 (technology), SDD.md:1822 (routes), SDD.md:2367 (typed owner interface)

**design_boundary:** Presentation/read contract only; no direct venue order, provider credential or production-store write authority. Missing implementation connectivity alone is not a defect.

**costs_removal:** 4333 bytes; 41 lines. No new dependency or storage model. Removal would break consumers or intentionally retained historical/test contract; no file merge/removal justified.

## frontend/src/assets/chart-room.svg — KEEP

Owner `frontend`; baseline1 lines, final1. Baseline SHA256 `f22d2d899cae2972a6708ee1e2b108156174a3b8c55b62259cc022aece5656ed`; final `f22d2d899cae2972a6708ee1e2b108156174a3b8c55b62259cc022aece5656ed`.

**purpose:** Supply a small static decorative geometric chart-room asset to retained CSS themes.

**consumers:** frontend/src/m4.css; frontend/src/product.css; frontend/src/styles.css

**evidence:** frontend/src/assets/chart-room.svg:1; SDD.md:1768 (operator evidence), SDD.md:1795 (technology), SDD.md:1822 (routes), SDD.md:2367 (typed owner interface)

**design_boundary:** Presentation/read contract only; no direct venue order, provider credential or production-store write authority. Missing implementation connectivity alone is not a defect.

**costs_removal:** 514 bytes; 1 lines. No new dependency or storage model. Removal would break consumers or intentionally retained historical/test contract; no file merge/removal justified.

## frontend/src/components/Avatar.tsx — KEEP

Owner `frontend`; baseline164 lines, final164. Baseline SHA256 `17ee72da95c440d1f6f28dd2fadacb401164795143025bc118553acfaf14cc32`; final `17ee72da95c440d1f6f28dd2fadacb401164795143025bc118553acfaf14cc32`.

**purpose:** Render cancellable fixture-era mechanical SVG avatar; animate only active visible requests and respect reduced motion.

**consumers:** frontend/src/views/Luffy.tsx

**evidence:** frontend/src/components/Avatar.tsx:1; SDD.md:1768 (operator evidence), SDD.md:1795 (technology), SDD.md:1822 (routes), SDD.md:2367 (typed owner interface)

**design_boundary:** Presentation/read contract only; no direct venue order, provider credential or production-store write authority. Missing implementation connectivity alone is not a defect.

**costs_removal:** 5049 bytes; 164 lines. No new dependency or storage model. Removal would break consumers or intentionally retained historical/test contract; no file merge/removal justified.

## frontend/src/components/EquityChart.tsx — KEEP

Owner `frontend`; baseline54 lines, final54. Baseline SHA256 `5280310f32a680fd47afdf2dfc21ce5b73502ab43d39e393d0f89e0348873aaa`; final `5280310f32a680fd47afdf2dfc21ce5b73502ab43d39e393d0f89e0348873aaa`.

**purpose:** Render supplied equity points with Lightweight Charts and dispose chart on update/unmount.

**consumers:** Retained explicit module contract; direct frontend import scan recorded no current importer

**evidence:** frontend/src/components/EquityChart.tsx:1; SDD.md:1768 (operator evidence), SDD.md:1795 (technology), SDD.md:1822 (routes), SDD.md:2367 (typed owner interface)

**design_boundary:** Presentation/read contract only; no direct venue order, provider credential or production-store write authority. Missing implementation connectivity alone is not a defect.

**costs_removal:** 1405 bytes; 54 lines. No new dependency or storage model. Removal would break consumers or intentionally retained historical/test contract; no file merge/removal justified.

## frontend/src/components/HealthStatus.tsx — KEEP

Owner `frontend`; baseline37 lines, final37. Baseline SHA256 `24b125cf8bba4c4f4dbd12bcedd473cbf9d8e8c7ec2428b7462ecc3eed333e16`; final `24b125cf8bba4c4f4dbd12bcedd473cbf9d8e8c7ec2428b7462ecc3eed333e16`.

**purpose:** Present no-telemetry, unknown, stale, stopped and current observed component labels without topology-as-health inference.

**consumers:** frontend/src/views/Activity.tsx; frontend/src/views/GraphView.tsx; frontend/tests/health.test.tsx; frontend/tests/own04.test.tsx; frontend/tests/v21.test.tsx

**evidence:** frontend/src/components/HealthStatus.tsx:1; SDD.md:1768 (operator evidence), SDD.md:1795 (technology), SDD.md:1822 (routes), SDD.md:2367 (typed owner interface)

**design_boundary:** Presentation/read contract only; no direct venue order, provider credential or production-store write authority. Missing implementation connectivity alone is not a defect.

**costs_removal:** 1572 bytes; 37 lines. No new dependency or storage model. Removal would break consumers or intentionally retained historical/test contract; no file merge/removal justified.

## frontend/src/components/LuffyCore.tsx — KEEP

Owner `frontend`; baseline34 lines, final34. Baseline SHA256 `007154c35cb1148647d83327f72565d01217de8e48c101eb61079711c47969cd`; final `007154c35cb1148647d83327f72565d01217de8e48c101eb61079711c47969cd`.

**purpose:** Render modern deterministic conversation/evidence/provider state icon without generating trading activity.

**consumers:** frontend/src/views/Luffy.tsx; frontend/tests/glacierV2.test.tsx

**evidence:** frontend/src/components/LuffyCore.tsx:1; SDD.md:1768 (operator evidence), SDD.md:1795 (technology), SDD.md:1822 (routes), SDD.md:2367 (typed owner interface)

**design_boundary:** Presentation/read contract only; no direct venue order, provider credential or production-store write authority. Missing implementation connectivity alone is not a defect.

**costs_removal:** 3176 bytes; 34 lines. No new dependency or storage model. Removal would break consumers or intentionally retained historical/test contract; no file merge/removal justified.

## frontend/src/components/OwnerControls.tsx — SIMPLIFY

Owner `frontend`; baseline206 lines, final206. Baseline SHA256 `670fdcdfbc35a9c39e1206401e2449169c0578c11a48fd384df4c28cc41a1c46`; final `b580b1d41561e0261df811340d4f0554485408ee778cadd7bd4a7445fc1dd976`.

**purpose:** Present typed freeze/halt/resume/panic requests with bounded confirmation, live health gating and stable request-id replay.

**consumers:** frontend/src/views/Trades.tsx; frontend/tests/glacierTruth.test.tsx; frontend/tests/review09.test.tsx

**evidence:** frontend/src/components/OwnerControls.tsx:1; SDD.md:1768 (operator evidence), SDD.md:1795 (technology), SDD.md:1822 (routes), SDD.md:2367 (typed owner interface); Baseline available checks cached health.data and isFetching but not health.error; red cached-AVAILABLE refresh-failure regression reproduced. Added !health.error.

**design_boundary:** Typed Owner Interface is sole mutation authority; retries preserve request identity; read-only backend blocks writes. Frontend optional write methods are omitted in read-only adapter.

**costs_removal:** 10836 bytes; 206 lines. No new dependency or storage model. Removal would break consumers or intentionally retained historical/test contract; no file merge/removal justified.

## frontend/src/components/OwnerEvidence.tsx — SIMPLIFY

Owner `frontend`; baseline88 lines, final88. Baseline SHA256 `68ef40406949d2c314c11c6252f2839eb8d6e57c8b27bfb9d11984f9184349f0`; final `ceeac33a2652f78e899dc350c8293164870f2abeb01d06a79269940ef83b20f0`.

**purpose:** Render compact/full exact Needs You decisions and bounded typed evidence lookup with research paging.

**consumers:** frontend/src/views/Luffy.tsx; frontend/tests/stage8.test.tsx

**evidence:** frontend/src/components/OwnerEvidence.tsx:1; SDD.md:1768 (operator evidence), SDD.md:1795 (technology), SDD.md:1822 (routes), SDD.md:2367 (typed owner interface); Recovery href/live greeting now target Trades, where Shell renders actual OwnerControls; OperationsLog is read-only.

**design_boundary:** Typed Owner Interface is sole mutation authority; retries preserve request identity; read-only backend blocks writes. Frontend optional write methods are omitted in read-only adapter.

**costs_removal:** 6727 bytes; 88 lines. No new dependency or storage model. Removal would break consumers or intentionally retained historical/test contract; no file merge/removal justified.

## frontend/src/components/PageHeader.tsx — KEEP

Owner `frontend`; baseline37 lines, final37. Baseline SHA256 `b77eafa09788a3412854ac9a8231c6c80a96976020a8e18489ccc436ff915ae7`; final `b77eafa09788a3412854ac9a8231c6c80a96976020a8e18489ccc436ff915ae7`.

**purpose:** Render page title, UTC clock, live/fixture/read-only chip and authenticated sign-out action.

**consumers:** frontend/src/Shell.tsx; frontend/src/views/Diagnostics.tsx; frontend/src/views/Knowledge.tsx; frontend/src/views/LiveSystem.tsx; frontend/src/views/Luffy.tsx; frontend/src/views/OperationsLog.tsx; frontend/src/views/Overview.tsx; frontend/src/views/Research.tsx; frontend/src/views/Strategies.tsx; frontend/src/views/Trades.tsx

**evidence:** frontend/src/components/PageHeader.tsx:1; SDD.md:1768 (operator evidence), SDD.md:1795 (technology), SDD.md:1822 (routes), SDD.md:2367 (typed owner interface)

**design_boundary:** Presentation/read contract only; no direct venue order, provider credential or production-store write authority. Missing implementation connectivity alone is not a defect.

**costs_removal:** 1762 bytes; 37 lines. No new dependency or storage model. Removal would break consumers or intentionally retained historical/test contract; no file merge/removal justified.

## frontend/src/components/PathTrace.tsx — KEEP

Owner `frontend`; baseline78 lines, final78. Baseline SHA256 `33ca76e6175a4a39bcdf0eeec82fe3876d91bb6f9141bb8fb976000742aef350`; final `33ca76e6175a4a39bcdf0eeec82fe3876d91bb6f9141bb8fb976000742aef350`.

**purpose:** Trace directed paths only over returned graph edges, with explicit noncausal and incomplete-store caveats.

**consumers:** frontend/src/views/GraphView.tsx

**evidence:** frontend/src/components/PathTrace.tsx:1; SDD.md:1768 (operator evidence), SDD.md:1795 (technology), SDD.md:1822 (routes), SDD.md:2367 (typed owner interface)

**design_boundary:** Presentation/read contract only; no direct venue order, provider credential or production-store write authority. Missing implementation connectivity alone is not a defect.

**costs_removal:** 2324 bytes; 78 lines. No new dependency or storage model. Removal would break consumers or intentionally retained historical/test contract; no file merge/removal justified.

## frontend/src/components/RuntimeTruth.tsx — KEEP

Owner `frontend`; baseline35 lines, final35. Baseline SHA256 `0d8e9e962a0e5f70a022d8d6f1fb53136b2e261330f93cf1df4a3f35861f5b10`; final `0d8e9e962a0e5f70a022d8d6f1fb53136b2e261330f93cf1df4a3f35861f5b10`.

**purpose:** Poll bootstrap truth, invalidate failed polls and expire account environment observations after existing 60-second display contract.

**consumers:** frontend/src/views/Luffy.tsx; frontend/src/views/Overview.tsx; frontend/src/views/Trades.tsx; frontend/tests/glacierTruth.test.tsx

**evidence:** frontend/src/components/RuntimeTruth.tsx:1; SDD.md:1768 (operator evidence), SDD.md:1795 (technology), SDD.md:1822 (routes), SDD.md:2367 (typed owner interface)

**design_boundary:** Presentation/read contract only; no direct venue order, provider credential or production-store write authority. Missing implementation connectivity alone is not a defect.

**costs_removal:** 2654 bytes; 35 lines. Bounded HTTP read polling/cache or mappings; no UI database writer. Polling/validation are intentional correctness boundaries, not redundant services.

## frontend/src/components/StorageTruth.tsx — KEEP

Owner `frontend`; baseline30 lines, final30. Baseline SHA256 `d12d5faebd9bd0e7fc6b9c23352e2359623900b05e40cc8f7dfe482c57843a66`; final `d12d5faebd9bd0e7fc6b9c23352e2359623900b05e40cc8f7dfe482c57843a66`.

**purpose:** Render recorded filesystem capacity/database sizes and distinguish missing growth baselines from measured growth.

**consumers:** frontend/src/views/Diagnostics.tsx; frontend/tests/diagnosticCorrections.test.tsx; frontend/tests/glacierTruth.test.tsx

**evidence:** frontend/src/components/StorageTruth.tsx:1; SDD.md:1768 (operator evidence), SDD.md:1795 (technology), SDD.md:1822 (routes), SDD.md:2367 (typed owner interface)

**design_boundary:** Presentation/read contract only; no direct venue order, provider credential or production-store write authority. Missing implementation connectivity alone is not a defect.

**costs_removal:** 3937 bytes; 30 lines. No new dependency or storage model. Removal would break consumers or intentionally retained historical/test contract; no file merge/removal justified.

## frontend/src/components/StrategyAuthority.tsx — KEEP

Owner `frontend`; baseline74 lines, final74. Baseline SHA256 `64f540236a4ab7bc9fb8837d6b9641f81672730739e8206938ad81888e59eabc`; final `64f540236a4ab7bc9fb8837d6b9641f81672730739e8206938ad81888e59eabc`.

**purpose:** Show exact StrategyVersion validation/install/probation/approval/Governor records separately from economics and execution eligibility.

**consumers:** frontend/src/views/Strategies.tsx; frontend/tests/glacierTruth.test.tsx

**evidence:** frontend/src/components/StrategyAuthority.tsx:1; SDD.md:1768 (operator evidence), SDD.md:1795 (technology), SDD.md:1822 (routes), SDD.md:2367 (typed owner interface)

**design_boundary:** Presentation/read contract only; no direct venue order, provider credential or production-store write authority. Missing implementation connectivity alone is not a defect.

**costs_removal:** 6848 bytes; 74 lines. No new dependency or storage model. Removal would break consumers or intentionally retained historical/test contract; no file merge/removal justified.

## frontend/src/components/glass.tsx — KEEP

Owner `frontend`; baseline329 lines, final329. Baseline SHA256 `f6624bb1c4b06be4f35ba3686b2277ea68268a015044af7cf904d1d41c8c6425`; final `f6624bb1c4b06be4f35ba3686b2277ea68268a015044af7cf904d1d41c8c6425`.

**purpose:** Supply modern cards, accessible Radix detail dialogs and bounded local sort/filter/paging tables.

**consumers:** frontend/src/components/OwnerControls.tsx; frontend/src/components/StorageTruth.tsx; frontend/src/components/StrategyAuthority.tsx; frontend/src/views/Diagnostics.tsx; frontend/src/views/Knowledge.tsx; frontend/src/views/LiveSystem.tsx; frontend/src/views/Luffy.tsx; frontend/src/views/OperationsLog.tsx; frontend/src/views/Overview.tsx; frontend/src/views/Research.tsx; frontend/src/views/Strategies.tsx; frontend/src/views/Trades.tsx; frontend/tests/glacierV2.test.tsx

**evidence:** frontend/src/components/glass.tsx:1; SDD.md:1768 (operator evidence), SDD.md:1795 (technology), SDD.md:1822 (routes), SDD.md:2367 (typed owner interface)

**design_boundary:** Presentation/read contract only; no direct venue order, provider credential or production-store write authority. Missing implementation connectivity alone is not a defect.

**costs_removal:** 13971 bytes; 329 lines. No new dependency or storage model. Removal would break consumers or intentionally retained historical/test contract; no file merge/removal justified.

## frontend/src/components/motion.tsx — KEEP

Owner `frontend`; baseline38 lines, final38. Baseline SHA256 `5f3f8fc84b0c05d8f8c4cb7248b9fd5d266088508ae4ab3bcae352b506658e7e`; final `5f3f8fc84b0c05d8f8c4cb7248b9fd5d266088508ae4ab3bcae352b506658e7e`.

**purpose:** Supply shared reduced-motion-aware animations; changed primitives update immediately without synthetic interpolated financial values.

**consumers:** frontend/src/Shell.tsx; frontend/src/components/LuffyCore.tsx; frontend/src/components/StorageTruth.tsx; frontend/src/views/Diagnostics.tsx; frontend/src/views/OperationsLog.tsx; frontend/src/views/Overview.tsx; frontend/src/views/Research.tsx; frontend/src/views/Trades.tsx; frontend/tests/glacierV2.test.tsx

**evidence:** frontend/src/components/motion.tsx:1; SDD.md:1768 (operator evidence), SDD.md:1795 (technology), SDD.md:1822 (routes), SDD.md:2367 (typed owner interface)

**design_boundary:** Presentation/read contract only; no direct venue order, provider credential or production-store write authority. Missing implementation connectivity alone is not a defect.

**costs_removal:** 2249 bytes; 38 lines. No new dependency or storage model. Removal would break consumers or intentionally retained historical/test contract; no file merge/removal justified.

## frontend/src/components/product.tsx — KEEP

Owner `frontend`; baseline129 lines, final129. Baseline SHA256 `7b734fe1ab0851d8eb23c4d8e6bece9b479b5e511828f785dbf8fa6193c7fdf4`; final `7b734fe1ab0851d8eb23c4d8e6bece9b479b5e511828f785dbf8fa6193c7fdf4`.

**purpose:** Supply evidence figures, exact truth labels, lineage strips and recorded proportional distributions.

**consumers:** frontend/src/views/Evidence.tsx; frontend/src/views/LiveReads.tsx

**evidence:** frontend/src/components/product.tsx:1; SDD.md:1768 (operator evidence), SDD.md:1795 (technology), SDD.md:1822 (routes), SDD.md:2367 (typed owner interface)

**design_boundary:** Presentation/read contract only; no direct venue order, provider credential or production-store write authority. Missing implementation connectivity alone is not a defect.

**costs_removal:** 3710 bytes; 129 lines. No new dependency or storage model. Removal would break consumers or intentionally retained historical/test contract; no file merge/removal justified.

## frontend/src/components/records.tsx — KEEP

Owner `frontend`; baseline212 lines, final212. Baseline SHA256 `3cb8441ca59d0c6130a79dc7c7324a49087ce6447a39e874cef5a21f9c54ecfd`; final `3cb8441ca59d0c6130a79dc7c7324a49087ce6447a39e874cef5a21f9c54ecfd`.

**purpose:** Supply shared path-keyed cancellable reads, fail-closed Loaded presentation, unavailable-field lists and stored-id links.

**consumers:** frontend/src/components/OwnerEvidence.tsx; frontend/src/components/RuntimeTruth.tsx; frontend/src/components/StorageTruth.tsx; frontend/src/components/StrategyAuthority.tsx; frontend/src/views/Activity.tsx; frontend/src/views/Diagnostics.tsx; frontend/src/views/Evidence.tsx; frontend/src/views/Knowledge.tsx; frontend/src/views/LiveReads.tsx; frontend/src/views/LiveSystem.tsx; frontend/src/views/Luffy.tsx; frontend/src/views/OperationsLog.tsx; frontend/src/views/Overview.tsx; frontend/src/views/Research.tsx; frontend/src/views/Routes.tsx; frontend/src/views/Strategies.tsx; frontend/src/views/Trades.tsx

**evidence:** frontend/src/components/records.tsx:1; SDD.md:1768 (operator evidence), SDD.md:1795 (technology), SDD.md:1822 (routes), SDD.md:2367 (typed owner interface)

**design_boundary:** Presentation/read contract only; no direct venue order, provider credential or production-store write authority. Missing implementation connectivity alone is not a defect.

**costs_removal:** 5706 bytes; 212 lines. Bounded HTTP read polling/cache or mappings; no UI database writer. Polling/validation are intentional correctness boundaries, not redundant services.

## frontend/src/components/ui.tsx — KEEP

Owner `frontend`; baseline225 lines, final225. Baseline SHA256 `24a178954383ba3b081c93caeb07395784bbb3a6ff9edc5b2c3636c46ae613d0`; final `24a178954383ba3b081c93caeb07395784bbb3a6ff9edc5b2c3636c46ae613d0`.

**purpose:** Supply common provenance/evidence dialogs, query failures, visual boundaries, reduced-motion and visibility hooks.

**consumers:** frontend/src/Shell.tsx; frontend/src/components/Avatar.tsx; frontend/src/components/LuffyCore.tsx; frontend/src/components/motion.tsx; frontend/src/components/product.tsx; frontend/src/components/records.tsx; frontend/src/components/workspace.tsx; frontend/src/views/Activity.tsx; frontend/src/views/Evidence.tsx; frontend/src/views/GraphView.tsx; frontend/src/views/LiveReads.tsx; frontend/src/views/LiveSystem.tsx; frontend/src/views/Luffy.tsx; frontend/src/views/Operations.tsx; frontend/src/views/Research.tsx; frontend/src/views/Routes.tsx; frontend/src/views/Tracker.tsx; frontend/src/views/Trades.tsx; frontend/tests/components.test.tsx; frontend/tests/m1.test.tsx

**evidence:** frontend/src/components/ui.tsx:1; SDD.md:1768 (operator evidence), SDD.md:1795 (technology), SDD.md:1822 (routes), SDD.md:2367 (typed owner interface)

**design_boundary:** Presentation/read contract only; no direct venue order, provider credential or production-store write authority. Missing implementation connectivity alone is not a defect.

**costs_removal:** 6679 bytes; 225 lines. No new dependency or storage model. Removal would break consumers or intentionally retained historical/test contract; no file merge/removal justified.

## frontend/src/components/workspace.tsx — KEEP

Owner `frontend`; baseline302 lines, final302. Baseline SHA256 `2000ee2a3727a4d1d176045277868488687f5835c7b98ebcfc865128cef462e2`; final `2000ee2a3727a4d1d176045277868488687f5835c7b98ebcfc865128cef462e2`.

**purpose:** Supply historical/shared evidence drawers, recorded-field tables, UTC presentation and explicit bounded paging caveats.

**consumers:** frontend/src/components/OwnerEvidence.tsx; frontend/src/components/records.tsx; frontend/src/views/Activity.tsx; frontend/src/views/Evidence.tsx; frontend/src/views/LiveReads.tsx; frontend/src/views/Routes.tsx; frontend/tests/workspace.test.tsx

**evidence:** frontend/src/components/workspace.tsx:1; SDD.md:1768 (operator evidence), SDD.md:1795 (technology), SDD.md:1822 (routes), SDD.md:2367 (typed owner interface)

**design_boundary:** Presentation/read contract only; no direct venue order, provider credential or production-store write authority. Missing implementation connectivity alone is not a defect.

**costs_removal:** 8720 bytes; 302 lines. No new dependency or storage model. Removal would break consumers or intentionally retained historical/test contract; no file merge/removal justified.

## frontend/src/context.tsx — SIMPLIFY

Owner `frontend`; baseline91 lines, final91. Baseline SHA256 `bb25235c563d1639b9b6297dbe489ecf83a948ec4183418f9221a587104d3492`; final `429fcb5fe188265ad04ee2ad42ff51bafb85e31cf52a03e2b72e02a7921e52ce`.

**purpose:** Require explicit adapter, scope session and in-memory conversation state, and label fixture vs live greetings.

**consumers:** frontend/src/Shell.tsx; frontend/src/components/OwnerControls.tsx; frontend/src/components/OwnerEvidence.tsx; frontend/src/components/PageHeader.tsx; frontend/src/components/RuntimeTruth.tsx; frontend/src/components/records.tsx; frontend/src/components/ui.tsx; frontend/src/demo.tsx; frontend/src/liveRoot.tsx; frontend/src/useOwnerTelemetry.ts; frontend/src/views/Activity.tsx; frontend/src/views/Diagnostics.tsx; frontend/src/views/Evidence.tsx; frontend/src/views/GraphView.tsx; frontend/src/views/Knowledge.tsx; frontend/src/views/LiveReads.tsx; frontend/src/views/LiveSystem.tsx; frontend/src/views/Luffy.tsx; frontend/src/views/Operations.tsx; frontend/src/views/OperationsLog.tsx; frontend/src/views/Overview.tsx; frontend/src/views/Research.tsx; frontend/src/views/Routes.tsx; frontend/src/views/Strategies.tsx; frontend/src/views/Tracker.tsx; frontend/src/views/Trades.tsx; frontend/tests/closeTradeDeferred.test.tsx; frontend/tests/components.test.tsx; frontend/tests/glacierTruth.test.tsx; frontend/tests/harness/main.tsx; frontend/tests/live.test.tsx; frontend/tests/m1.test.tsx; frontend/tests/m2.test.tsx; frontend/tests/own04.test.tsx; frontend/tests/protectionSnapshot.test.tsx; frontend/tests/review09.test.tsx; frontend/tests/stage8.test.tsx; frontend/tests/tradeHistory.test.tsx

**evidence:** frontend/src/context.tsx:1; SDD.md:1768 (operator evidence), SDD.md:1795 (technology), SDD.md:1822 (routes), SDD.md:2367 (typed owner interface); Recovery href/live greeting now target Trades, where Shell renders actual OwnerControls; OperationsLog is read-only.

**design_boundary:** Presentation/read contract only; no direct venue order, provider credential or production-store write authority. Missing implementation connectivity alone is not a defect.

**costs_removal:** 2550 bytes; 91 lines. Per-request localStorage/in-memory state only; no production database access. Removal loses retry/session conversation contract.

## frontend/src/demo.tsx — KEEP

Owner `frontend`; baseline32 lines, final32. Baseline SHA256 `0e2390161c33eb06a4679d65bb51035aa4318b8352eb9e212cb482f254d40c8d`; final `0e2390161c33eb06a4679d65bb51035aa4318b8352eb9e212cb482f254d40c8d`.

**purpose:** Mount isolated fixture provider only for explicit demo build; never a fallback from live root.

**consumers:** frontend/src/main.tsx

**evidence:** frontend/src/demo.tsx:1; SDD.md:1768 (operator evidence), SDD.md:1795 (technology), SDD.md:1822 (routes), SDD.md:2367 (typed owner interface)

**design_boundary:** Intentional isolated synthetic demo/test boundary. Production main selects live; live adapter has no fixture import or fallback.

**costs_removal:** 1039 bytes; 32 lines. No new dependency or storage model. Removal would break consumers or intentionally retained historical/test contract; no file merge/removal justified.

## frontend/src/diagnosticTruth.ts — KEEP

Owner `frontend`; baseline34 lines, final34. Baseline SHA256 `44e708074dbad354b66c0a52280f2cb1efde231d6f7462aced3ea36143b53a90`; final `44e708074dbad354b66c0a52280f2cb1efde231d6f7462aced3ea36143b53a90`.

**purpose:** Classify recorded Supervisor recovery/reconciliation and collector evidence with freshness and current-proof requirements.

**consumers:** frontend/src/views/Diagnostics.tsx; frontend/src/views/Overview.tsx; frontend/tests/diagnosticCorrections.test.tsx

**evidence:** frontend/src/diagnosticTruth.ts:1; SDD.md:1768 (operator evidence), SDD.md:1795 (technology), SDD.md:1822 (routes), SDD.md:2367 (typed owner interface)

**design_boundary:** Presentation/read contract only; no direct venue order, provider credential or production-store write authority. Missing implementation connectivity alone is not a defect.

**costs_removal:** 2237 bytes; 34 lines. No new dependency or storage model. Removal would break consumers or intentionally retained historical/test contract; no file merge/removal justified.

## frontend/src/fonts.ts — KEEP

Owner `frontend`; baseline11 lines, final11. Baseline SHA256 `8fc03e849f3a267870c037238a7d9231aa1610ddfdc6778f4c6a7110e34a5812`; final `8fc03e849f3a267870c037238a7d9231aa1610ddfdc6778f4c6a7110e34a5812`.

**purpose:** Import self-hosted local font weights compatible with self-only CSP.

**consumers:** frontend/src/Shell.tsx; frontend/src/liveRoot.tsx

**evidence:** frontend/src/fonts.ts:1; SDD.md:1768 (operator evidence), SDD.md:1795 (technology), SDD.md:1822 (routes), SDD.md:2367 (typed owner interface)

**design_boundary:** Presentation/read contract only; no direct venue order, provider credential or production-store write authority. Missing implementation connectivity alone is not a defect.

**costs_removal:** 508 bytes; 11 lines. No new dependency or storage model. Removal would break consumers or intentionally retained historical/test contract; no file merge/removal justified.

## frontend/src/glacier.css — KEEP

Owner `frontend`; baseline594 lines, final594. Baseline SHA256 `7354e969ca67653f4ce8cfde42519efacb6accb1a96c37b2571348ce9e023b50`; final `7354e969ca67653f4ce8cfde42519efacb6accb1a96c37b2571348ce9e023b50`.

**purpose:** Define approved modern visual tokens, route/card/table layouts, responsive controls, focus states and reduced-motion overrides.

**consumers:** frontend/src/Shell.tsx; frontend/src/demo.tsx; frontend/src/liveRoot.tsx

**evidence:** frontend/src/glacier.css:1; SDD.md:1768 (operator evidence), SDD.md:1795 (technology), SDD.md:1822 (routes), SDD.md:2367 (typed owner interface)

**design_boundary:** Presentation/read contract only; no direct venue order, provider credential or production-store write authority. Missing implementation connectivity alone is not a defect.

**costs_removal:** 49584 bytes; 594 lines. 507 declaration/rule blocks; 13 media guards; only local URL assets. Consolidating theme layers requires visual evidence and is optional; removing breaks documented consumers.

## frontend/src/lazyChunk.ts — KEEP

Owner `frontend`; baseline69 lines, final69. Baseline SHA256 `3052e25a13210e18ab1981bd4655250686e4f7f725bfb5e47b659e1bfa43f1d7`; final `3052e25a13210e18ab1981bd4655250686e4f7f725bfb5e47b659e1bfa43f1d7`.

**purpose:** Bound dynamic import at 20 seconds, announce session check, isolate visual failure and recover poisoned imports by reload.

**consumers:** frontend/src/Shell.tsx; frontend/src/components/ui.tsx; frontend/src/liveRoot.tsx; frontend/tests/m1.test.tsx

**evidence:** frontend/src/lazyChunk.ts:1; SDD.md:1768 (operator evidence), SDD.md:1795 (technology), SDD.md:1822 (routes), SDD.md:2367 (typed owner interface)

**design_boundary:** Presentation/read contract only; no direct venue order, provider credential or production-store write authority. Missing implementation connectivity alone is not a defect.

**costs_removal:** 2432 bytes; 69 lines. No new dependency or storage model. Removal would break consumers or intentionally retained historical/test contract; no file merge/removal justified.

## frontend/src/legacy.css — KEEP

Owner `frontend`; baseline4 lines, final4. Baseline SHA256 `11657822fd8308726e9d7810bb10bfc00c909acdfcbb496c1304e15e58d9b91b`; final `11657822fd8308726e9d7810bb10bfc00c909acdfcbb496c1304e15e58d9b91b`.

**purpose:** Aggregate previous styles as inline route-local Tracker theme; retains required legacy primitives.

**consumers:** frontend/src/views/Tracker.tsx

**evidence:** frontend/src/legacy.css:1; SDD.md:1768 (operator evidence), SDD.md:1795 (technology), SDD.md:1822 (routes), SDD.md:2367 (typed owner interface)

**design_boundary:** Intentional prior-theme retention: Tracker imports legacy.css?inline and inserts/removes it on mount/unmount; harness imports prior theme. Not dead merely because modern routes use Glacier.

**costs_removal:** 91 bytes; 4 lines. 0 declaration/rule blocks; 0 media guards; only local URL assets. Consolidating theme layers requires visual evidence and is optional; removing breaks documented consumers.

## frontend/src/links.ts — KEEP

Owner `frontend`; baseline54 lines, final54. Baseline SHA256 `19a94e49ce7b0b6098c2f5bd01ce7053f2a6d7c057a3b5a0c278d20c4b6ddf5f`; final `19a94e49ce7b0b6098c2f5bd01ce7053f2a6d7c057a3b5a0c278d20c4b6ddf5f`.

**purpose:** Generate encoded stored-id hash links and react to hash-query destination changes.

**consumers:** frontend/src/Shell.tsx; frontend/src/components/records.tsx; frontend/src/views/GraphView.tsx; frontend/src/views/Knowledge.tsx; frontend/src/views/Luffy.tsx; frontend/src/views/Operations.tsx; frontend/src/views/OperationsLog.tsx; frontend/src/views/Research.tsx; frontend/src/views/Routes.tsx; frontend/src/views/Strategies.tsx; frontend/src/views/Trades.tsx; frontend/tests/m2.test.tsx

**evidence:** frontend/src/links.ts:1; SDD.md:1768 (operator evidence), SDD.md:1795 (technology), SDD.md:1822 (routes), SDD.md:2367 (typed owner interface)

**design_boundary:** Presentation/read contract only; no direct venue order, provider credential or production-store write authority. Missing implementation connectivity alone is not a defect.

**costs_removal:** 1835 bytes; 54 lines. No new dependency or storage model. Removal would break consumers or intentionally retained historical/test contract; no file merge/removal justified.

## frontend/src/live.css — KEEP

Owner `frontend`; baseline650 lines, final650. Baseline SHA256 `85464bcee6113246e07dd44c91f06e6184dbff4c9e991d74f73d5aab0b14a0f1`; final `85464bcee6113246e07dd44c91f06e6184dbff4c9e991d74f73d5aab0b14a0f1`.

**purpose:** Style retained live evidence/records/forensic primitives inside Tracker legacy theme and test harness.

**consumers:** frontend/src/legacy.css

**evidence:** frontend/src/live.css:1; SDD.md:1768 (operator evidence), SDD.md:1795 (technology), SDD.md:1822 (routes), SDD.md:2367 (typed owner interface)

**design_boundary:** Intentional prior-theme retention: Tracker imports legacy.css?inline and inserts/removes it on mount/unmount; harness imports prior theme. Not dead merely because modern routes use Glacier.

**costs_removal:** 12874 bytes; 650 lines. 128 declaration/rule blocks; 8 media guards; only local URL assets. Consolidating theme layers requires visual evidence and is optional; removing breaks documented consumers.

## frontend/src/liveRoot.tsx — KEEP

Owner `frontend`; baseline196 lines, final196. Baseline SHA256 `f257829f4b831f3ea284f39707340acaac3c3c01a525b07209fe2d80c44b745a`; final `f257829f4b831f3ea284f39707340acaac3c3c01a525b07209fe2d80c44b745a`.

**purpose:** Bootstrap authenticated live state, retry unavailable backend, expire and clear sessions/query cache, and forbid fixture fallback.

**consumers:** frontend/src/main.tsx

**evidence:** frontend/src/liveRoot.tsx:1; SDD.md:1768 (operator evidence), SDD.md:1795 (technology), SDD.md:1822 (routes), SDD.md:2367 (typed owner interface)

**design_boundary:** Presentation/read contract only; no direct venue order, provider credential or production-store write authority. Missing implementation connectivity alone is not a defect.

**costs_removal:** 5928 bytes; 196 lines. Bounded HTTP read polling/cache or mappings; no UI database writer. Polling/validation are intentional correctness boundaries, not redundant services.

## frontend/src/m4.css — KEEP

Owner `frontend`; baseline283 lines, final283. Baseline SHA256 `529dd27112cc242986349ea30a21602aec7d0eef383be058cf2948489877e81f`; final `529dd27112cc242986349ea30a21602aec7d0eef383be058cf2948489877e81f`.

**purpose:** Provide retained prior workstation layouts and responsive/reduced-motion overrides for Tracker legacy theme.

**consumers:** frontend/src/legacy.css

**evidence:** frontend/src/m4.css:1; SDD.md:1768 (operator evidence), SDD.md:1795 (technology), SDD.md:1822 (routes), SDD.md:2367 (typed owner interface)

**design_boundary:** Intentional prior-theme retention: Tracker imports legacy.css?inline and inserts/removes it on mount/unmount; harness imports prior theme. Not dead merely because modern routes use Glacier.

**costs_removal:** 20615 bytes; 283 lines. 196 declaration/rule blocks; 5 media guards; only local URL assets. Consolidating theme layers requires visual evidence and is optional; removing breaks documented consumers.

## frontend/src/main.tsx — KEEP

Owner `frontend`; baseline6 lines, final6. Baseline SHA256 `b98da76fc85de15b5c9f4411ef71645b5bf0d26483524af59069973c4f683ec6`; final `b98da76fc85de15b5c9f4411ef71645b5bf0d26483524af59069973c4f683ec6`.

**purpose:** Select live production entry or explicit build-time demo import; export route contract.

**consumers:** Retained explicit module contract; direct frontend import scan recorded no current importer

**evidence:** frontend/src/main.tsx:1; SDD.md:1768 (operator evidence), SDD.md:1795 (technology), SDD.md:1822 (routes), SDD.md:2367 (typed owner interface)

**design_boundary:** Presentation/read contract only; no direct venue order, provider credential or production-store write authority. Missing implementation connectivity alone is not a defect.

**costs_removal:** 339 bytes; 6 lines. No new dependency or storage model. Removal would break consumers or intentionally retained historical/test contract; no file merge/removal justified.

## frontend/src/product.css — KEEP

Owner `frontend`; baseline2242 lines, final2242. Baseline SHA256 `b5762eede6306811a96a742c6e588f2f584a752fb4774f54eb6851df498d94d0`; final `b5762eede6306811a96a742c6e588f2f584a752fb4774f54eb6851df498d94d0`.

**purpose:** Provide retained M3 workstation tokens/layouts and local asset styling required by legacy Tracker/harness.

**consumers:** frontend/src/legacy.css; frontend/tests/harness/main.tsx

**evidence:** frontend/src/product.css:1; SDD.md:1768 (operator evidence), SDD.md:1795 (technology), SDD.md:1822 (routes), SDD.md:2367 (typed owner interface)

**design_boundary:** Intentional prior-theme retention: Tracker imports legacy.css?inline and inserts/removes it on mount/unmount; harness imports prior theme. Not dead merely because modern routes use Glacier.

**costs_removal:** 46955 bytes; 2242 lines. 442 declaration/rule blocks; 17 media guards; only local URL assets. Consolidating theme layers requires visual evidence and is optional; removing breaks documented consumers.

## frontend/src/protectionExpiry.ts — KEEP

Owner `frontend`; baseline41 lines, final41. Baseline SHA256 `c009d6edb4d9dcf29cd3b21d0fd13a4c124ba8ecff4c45df73b7d35825b6d63f`; final `c009d6edb4d9dcf29cd3b21d0fd13a4c124ba8ecff4c45df73b7d35825b6d63f`.

**purpose:** Reclassify expired kernel protection snapshot and dependent position claims as STALE independent of failed polling.

**consumers:** frontend/src/views/Overview.tsx; frontend/tests/protectionSnapshot.test.tsx

**evidence:** frontend/src/protectionExpiry.ts:1; SDD.md:1768 (operator evidence), SDD.md:1795 (technology), SDD.md:1822 (routes), SDD.md:2367 (typed owner interface)

**design_boundary:** Presentation/read contract only; no direct venue order, provider credential or production-store write authority. Missing implementation connectivity alone is not a defect.

**costs_removal:** 1345 bytes; 41 lines. No new dependency or storage model. Removal would break consumers or intentionally retained historical/test contract; no file merge/removal justified.

## frontend/src/styles.css — KEEP

Owner `frontend`; baseline1521 lines, final1521. Baseline SHA256 `d0c2905cae7eeaf5ecb972650543579fcee884c86316fbcdbc6153cc7c2b3ca7`; final `d0c2905cae7eeaf5ecb972650543579fcee884c86316fbcdbc6153cc7c2b3ca7`.

**purpose:** Provide base theme, shared recorded evidence/graph/table/dialog layout, responsive and reduced-motion styles in retained legacy bundle.

**consumers:** frontend/src/legacy.css; frontend/tests/harness/main.tsx

**evidence:** frontend/src/styles.css:1; SDD.md:1768 (operator evidence), SDD.md:1795 (technology), SDD.md:1822 (routes), SDD.md:2367 (typed owner interface)

**design_boundary:** Intentional prior-theme retention: Tracker imports legacy.css?inline and inserts/removes it on mount/unmount; harness imports prior theme. Not dead merely because modern routes use Glacier.

**costs_removal:** 26673 bytes; 1521 lines. 324 declaration/rule blocks; 9 media guards; only local URL assets. Consolidating theme layers requires visual evidence and is optional; removing breaks documented consumers.

## frontend/src/telemetryExpiry.ts — KEEP

Owner `frontend`; baseline17 lines, final17. Baseline SHA256 `b322001c8515d0ae8fd67e9667a96989f85fe93302ef4bc21c0ca5d6b4cb2886`; final `b322001c8515d0ae8fd67e9667a96989f85fe93302ef4bc21c0ca5d6b4cb2886`.

**purpose:** Expire account/heartbeat/Supervisor evidence locally and mark disconnected telemetry unavailable without fabricating health.

**consumers:** frontend/src/views/Overview.tsx; frontend/tests/own04.test.tsx

**evidence:** frontend/src/telemetryExpiry.ts:1; SDD.md:1768 (operator evidence), SDD.md:1795 (technology), SDD.md:1822 (routes), SDD.md:2367 (typed owner interface)

**design_boundary:** Presentation/read contract only; no direct venue order, provider credential or production-store write authority. Missing implementation connectivity alone is not a defect.

**costs_removal:** 949 bytes; 17 lines. No new dependency or storage model. Removal would break consumers or intentionally retained historical/test contract; no file merge/removal justified.

## frontend/src/time.ts — KEEP

Owner `frontend`; baseline17 lines, final17. Baseline SHA256 `1d087f47000eb8eb53d651b64962c475c2dec4542754a632a1565837f9f3de49`; final `1d087f47000eb8eb53d651b64962c475c2dec4542754a632a1565837f9f3de49`.

**purpose:** Format valid source timestamps consistently in UTC; invalid input never becomes current time.

**consumers:** frontend/src/Shell.tsx; frontend/src/components/StorageTruth.tsx; frontend/src/components/ui.tsx; frontend/src/components/workspace.tsx; frontend/src/views/Diagnostics.tsx; frontend/src/views/GraphView.tsx; frontend/src/views/Knowledge.tsx; frontend/src/views/Operations.tsx; frontend/src/views/OperationsLog.tsx; frontend/src/views/Overview.tsx; frontend/src/views/Research.tsx; frontend/src/views/Strategies.tsx; frontend/src/views/Trades.tsx; frontend/tests/v21.test.tsx

**evidence:** frontend/src/time.ts:1; SDD.md:1768 (operator evidence), SDD.md:1795 (technology), SDD.md:1822 (routes), SDD.md:2367 (typed owner interface)

**design_boundary:** Presentation/read contract only; no direct venue order, provider credential or production-store write authority. Missing implementation connectivity alone is not a defect.

**costs_removal:** 772 bytes; 17 lines. No new dependency or storage model. Removal would break consumers or intentionally retained historical/test contract; no file merge/removal justified.

## frontend/src/tokens.css — KEEP

Owner `frontend`; baseline13 lines, final13. Baseline SHA256 `f8cf9373929b79a12495c701578c458e0f60fbd41345a055dfbc7e625ca166e0`; final `f8cf9373929b79a12495c701578c458e0f60fbd41345a055dfbc7e625ca166e0`.

**purpose:** Define base legacy colors for styles.css/Tracker without runtime dependencies.

**consumers:** frontend/src/styles.css

**evidence:** frontend/src/tokens.css:1; SDD.md:1768 (operator evidence), SDD.md:1795 (technology), SDD.md:1822 (routes), SDD.md:2367 (typed owner interface)

**design_boundary:** Intentional prior-theme retention: Tracker imports legacy.css?inline and inserts/removes it on mount/unmount; harness imports prior theme. Not dead merely because modern routes use Glacier.

**costs_removal:** 235 bytes; 13 lines. 1 declaration/rule blocks; 0 media guards; only local URL assets. Consolidating theme layers requires visual evidence and is optional; removing breaks documented consumers.

## frontend/src/useOwnerTelemetry.ts — KEEP

Owner `frontend`; baseline74 lines, final74. Baseline SHA256 `a79c8e9c24c303e80c5b56151a3c5d1a239eba3755ca5b82887e25e56b05756a`; final `a79c8e9c24c303e80c5b56151a3c5d1a239eba3755ca5b82887e25e56b05756a`.

**purpose:** Share owner-interface health query and locally age graph telemetry; topology never establishes IPC availability.

**consumers:** frontend/src/views/Activity.tsx; frontend/src/views/GraphView.tsx; frontend/tests/own04.test.tsx

**evidence:** frontend/src/useOwnerTelemetry.ts:1; SDD.md:1768 (operator evidence), SDD.md:1795 (technology), SDD.md:1822 (routes), SDD.md:2367 (typed owner interface)

**design_boundary:** Presentation/read contract only; no direct venue order, provider credential or production-store write authority. Missing implementation connectivity alone is not a defect.

**costs_removal:** 2949 bytes; 74 lines. Bounded HTTP read polling/cache or mappings; no UI database writer. Polling/validation are intentional correctness boundaries, not redundant services.

## frontend/src/views/Activity.tsx — KEEP

Owner `frontend`; baseline234 lines, final234. Baseline SHA256 `a9b544aa68468badb45d7d5a17e48cfb808ca52ea20a9cc1bc1d134cc510d069`; final `a9b544aa68468badb45d7d5a17e48cfb808ca52ea20a9cc1bc1d134cc510d069`.

**purpose:** Recorded decision pages, investigation dossiers and runtime-status summary. Direct consumers: legacy Operations/Routes and LiveReads.

**consumers:** Recorded decision pages, investigation dossiers and runtime-status summary. Direct consumers: legacy Operations/Routes and LiveReads.

**evidence:** Source imports, state/query/render branches and consumers inspected; key exact-id and authority paths cross-checked against links.ts, Shell.tsx, current adapters and focused regressions.

**design_boundary:** SDD25 evidence-grounded operator navigation; unknown/disabled/future capabilities are not inferred successful or defects. SDD14.5 immutable versions and19 final Risk authority remain separate.

**costs_removal:** Browser state/rendering and bounded/polled reads only; no added backend storage or trading/provider authority. Removal would break listed view/evidence consumers.

## frontend/src/views/Diagnostics.tsx — KEEP

Owner `frontend`; baseline297 lines, final297. Baseline SHA256 `abe2573676ebcff42ff81331e2f20bb8fdeff2bbb94397a592ceeeb464dfbd14`; final `abe2573676ebcff42ff81331e2f20bb8fdeff2bbb94397a592ceeeb464dfbd14`.

**purpose:** Current owner diagnostics: safety checks, reconciliation, recovery, storage, collector reports, timeline and evidence dialogs. Shell live Diagnostics.

**consumers:** Current owner diagnostics: safety checks, reconciliation, recovery, storage, collector reports, timeline and evidence dialogs. Shell live Diagnostics.

**evidence:** Source imports, state/query/render branches and consumers inspected; key exact-id and authority paths cross-checked against links.ts, Shell.tsx, current adapters and focused regressions.

**design_boundary:** SDD25 evidence-grounded operator navigation; unknown/disabled/future capabilities are not inferred successful or defects. SDD14.5 immutable versions and19 final Risk authority remain separate.

**costs_removal:** Browser state/rendering and bounded/polled reads only; no added backend storage or trading/provider authority. Removal would break listed view/evidence consumers.

## frontend/src/views/Evidence.tsx — KEEP

Owner `frontend`; baseline1225 lines, final1225. Baseline SHA256 `378a2c3661261e785afc67ffdaba95c477d0ce9828e7cc080545e3b6d21decd2`; final `378a2c3661261e785afc67ffdaba95c477d0ce9828e7cc080545e3b6d21decd2`.

**purpose:** Shared recorded-evidence rendering: trade chain, decision/research linked panels, strategy family, operations timeline, observed-edge completeness, note references and diagnostics. Consumers: LiveReads, OperationsLog, modern record deep links and compatibility Routes.

**consumers:**  LiveReads, OperationsLog, modern record deep links and compatibility Routes.

**evidence:** Source imports, state/query/render branches and consumers inspected; key exact-id and authority paths cross-checked against links.ts, Shell.tsx, current adapters and focused regressions.

**design_boundary:** SDD25 evidence-grounded operator navigation; unknown/disabled/future capabilities are not inferred successful or defects. SDD14.5 immutable versions and19 final Risk authority remain separate.

**costs_removal:** Browser state/rendering and bounded/polled reads only; no added backend storage or trading/provider authority. Removal would break listed view/evidence consumers.

## frontend/src/views/GraphView.tsx — KEEP

Owner `frontend`; baseline906 lines, final906. Baseline SHA256 `cd94a075a7d7401396d12f59c82ee887d696de5390df5ac4e1142f64caecd8c3`; final `cd94a075a7d7401396d12f59c82ee887d696de5390df5ac4e1142f64caecd8c3`.

**purpose:** Bounded ReactFlow knowledge/system visualization with lens membership, selected record paths, explicit undrawn edges, list fallback and telemetry expiry. Shell fixture surfaces and regression harness; current live views are separate.

**consumers:** Bounded ReactFlow knowledge/system visualization with lens membership, selected record paths, explicit undrawn edges, list fallback and telemetry expiry. Shell fixture surfaces and regression harness; current live views are separate.

**evidence:** Source imports, state/query/render branches and consumers inspected; key exact-id and authority paths cross-checked against links.ts, Shell.tsx, current adapters and focused regressions.

**design_boundary:** SDD25 evidence-grounded operator navigation; unknown/disabled/future capabilities are not inferred successful or defects. SDD14.5 immutable versions and19 final Risk authority remain separate.

**costs_removal:** Browser state/rendering and bounded/polled reads only; no added backend storage or trading/provider authority. Removal would break listed view/evidence consumers. Graph rendering caps 40 visible nodes; geometry is browser work, with list fallback/explicit undrawn links.

## frontend/src/views/Knowledge.tsx — KEEP

Owner `frontend`; baseline266 lines, final273. Baseline SHA256 `d84582146875f36b976e0c3c1801e76cd263c299a111e4241fbe9ace4aae717f`; final `0d110f21b11e2a6f83f09a4d5e7b80c2afebd669a46420c9020b549f6359ab4c`.

**purpose:** Current live vault map/list with bounded visible neighbors and complete accessible neighbor list, written-relation filters and note summaries. Shell Knowledge; now also exact linked NoteDetail.

**consumers:** Current live vault map/list with bounded visible neighbors and complete accessible neighbor list, written-relation filters and note summaries. Shell Knowledge; now also exact linked NoteDetail.

**evidence:** Source imports, state/query/render branches and consumers inspected; key exact-id and authority paths cross-checked against links.ts, Shell.tsx, current adapters and focused regressions.

**design_boundary:** SDD25 evidence-grounded operator navigation; unknown/disabled/future capabilities are not inferred successful or defects. SDD14.5 immutable versions and19 final Risk authority remain separate.

**costs_removal:** Browser state/rendering and bounded/polled reads only; no added backend storage or trading/provider authority. Removal would break listed view/evidence consumers. Current map shows up to 12 immediate neighbors and 60 contextual nodes; complete neighbor list remains available.

## frontend/src/views/LiveReads.tsx — KEEP

Owner `frontend`; baseline1278 lines, final1278. Baseline SHA256 `39e215cf29c9fe996f426e54d88eb6e6de092a7e34f7e830a37ac539408b6fc1`; final `39e215cf29c9fe996f426e54d88eb6e6de092a7e34f7e830a37ac539408b6fc1`.

**purpose:** Reusable journal trade lineage, economics, strategy evidence, research ledger sections, operations observations and note body/provenance detail. Consumers Evidence graph inspectors, restored modern deep links, compatibility Routes and tests.

**consumers:** Reusable journal trade lineage, economics, strategy evidence, research ledger sections, operations observations and note body/provenance detail. Consumers Evidence graph inspectors, restored modern deep links, compatibility Routes and tests.

**evidence:** Source imports, state/query/render branches and consumers inspected; key exact-id and authority paths cross-checked against links.ts, Shell.tsx, current adapters and focused regressions.

**design_boundary:** SDD25 evidence-grounded operator navigation; unknown/disabled/future capabilities are not inferred successful or defects. SDD14.5 immutable versions and19 final Risk authority remain separate.

**costs_removal:** Browser state/rendering and bounded/polled reads only; no added backend storage or trading/provider authority. Removal would break listed view/evidence consumers.

## frontend/src/views/LiveSystem.tsx — KEEP

Owner `frontend`; baseline263 lines, final263. Baseline SHA256 `20baa805c1fa437705ed6323f92869d92249bcf3bdfbd1095f47fba3a9464c68`; final `20baa805c1fa437705ed6323f92869d92249bcf3bdfbd1095f47fba3a9464c68`.

**purpose:** Declared architecture diagram with selectable explanations/walkthrough and separately identified system/control telemetry. Shell live Live System.

**consumers:** Declared architecture diagram with selectable explanations/walkthrough and separately identified system/control telemetry. Shell live Live System.

**evidence:** Source imports, state/query/render branches and consumers inspected; key exact-id and authority paths cross-checked against links.ts, Shell.tsx, current adapters and focused regressions.

**design_boundary:** SDD25 evidence-grounded operator navigation; unknown/disabled/future capabilities are not inferred successful or defects. SDD14.5 immutable versions and19 final Risk authority remain separate.

**costs_removal:** Browser state/rendering and bounded/polled reads only; no added backend storage or trading/provider authority. Removal would break listed view/evidence consumers.

## frontend/src/views/Luffy.tsx — KEEP

Owner `frontend`; baseline370 lines, final370. Baseline SHA256 `1f1549a035d9e39ea15eb63f9ac3f1f7ec2105eb9d6976160f0c9393a88829ac`; final `4e6fb2a8d4cdd8df121658d53af12dbbe34b8e0fde93a487113b1ebc1c7455c9`.

**purpose:** Owner conversation, cancellation/error transcript, exact recorded-evidence cards, Needs You and typed internal query panels. Shell and demo/test harness.

**consumers:** Owner conversation, cancellation/error transcript, exact recorded-evidence cards, Needs You and typed internal query panels. Shell and demo/test harness.

**evidence:** Source imports, state/query/render branches and consumers inspected; key exact-id and authority paths cross-checked against links.ts, Shell.tsx, current adapters and focused regressions.

**design_boundary:** SDD25 evidence-grounded operator navigation; unknown/disabled/future capabilities are not inferred successful or defects. SDD14.5 immutable versions and19 final Risk authority remain separate.

**costs_removal:** Browser state/rendering and bounded/polled reads only; no added backend storage or trading/provider authority. Removal would break listed view/evidence consumers.

## frontend/src/views/Operations.tsx — KEEP

Owner `frontend`; baseline339 lines, final339. Baseline SHA256 `34bed1517329d943eb50c0a4ab5ffb0cb4dbbd4aa9e097077c6f6666248f0328`; final `34bed1517329d943eb50c0a4ab5ffb0cb4dbbd4aa9e097077c6f6666248f0328`.

**purpose:** Earlier typed Owner Interface control surface with pending-id reuse, explicit confirmations, guarded recovery and evidence activity. Retained direct unit consumers; current production controls reside in OwnerControls on Trades.

**consumers:** Earlier typed Owner Interface control surface with pending-id reuse, explicit confirmations, guarded recovery and evidence activity. Retained direct unit consumers; current production controls reside in OwnerControls on Trades.

**evidence:** Source imports, state/query/render branches and consumers inspected; key exact-id and authority paths cross-checked against links.ts, Shell.tsx, current adapters and focused regressions.

**design_boundary:** SDD25 evidence-grounded operator navigation; unknown/disabled/future capabilities are not inferred successful or defects. SDD14.5 immutable versions and19 final Risk authority remain separate. Compatibility/test consumers are intentional retention, not proof of current production route availability; no removal of established adapter tests in this bounded fix.

**costs_removal:** Browser state/rendering and bounded/polled reads only; no added backend storage or trading/provider authority. Removal would break listed view/evidence consumers.

## frontend/src/views/OperationsLog.tsx — KEEP

Owner `frontend`; baseline180 lines, final180. Baseline SHA256 `80cb41d8140209e8e1ad3d78e135d44e753c28d54beb9fe1bacf564b8d4c2898`; final `80cb41d8140209e8e1ad3d78e135d44e753c28d54beb9fe1bacf564b8d4c2898`.

**purpose:** Current read-only activity chart/log, UTC day/hour and type/search filters, rollup/truncation disclosure and decision deep-link panel. Shell live Operations.

**consumers:** Current read-only activity chart/log, UTC day/hour and type/search filters, rollup/truncation disclosure and decision deep-link panel. Shell live Operations.

**evidence:** Source imports, state/query/render branches and consumers inspected; key exact-id and authority paths cross-checked against links.ts, Shell.tsx, current adapters and focused regressions.

**design_boundary:** SDD25 evidence-grounded operator navigation; unknown/disabled/future capabilities are not inferred successful or defects. SDD14.5 immutable versions and19 final Risk authority remain separate.

**costs_removal:** Browser state/rendering and bounded/polled reads only; no added backend storage or trading/provider authority. Removal would break listed view/evidence consumers.

## frontend/src/views/Overview.tsx — KEEP

Owner `frontend`; baseline634 lines, final634. Baseline SHA256 `3d969ca07df87e8265fbac6dcf99cac4d78a7966877bee0f87747f6f24cd9c3b`; final `fa5d537892fff7b2e3fe41f6dd51d4d2b55420ee6a949608945be1dfa7b3a1eb`.

**purpose:** Current owner desk: exact account/control/Kernel/protection evidence, fresh mark estimates, journal P&L, positions, research/activity summaries and Needs You. Shell direct initial view.

**consumers:** Current owner desk: exact account/control/Kernel/protection evidence, fresh mark estimates, journal P&L, positions, research/activity summaries and Needs You. Shell direct initial view.

**evidence:** Source imports, state/query/render branches and consumers inspected; key exact-id and authority paths cross-checked against links.ts, Shell.tsx, current adapters and focused regressions.

**design_boundary:** SDD25 evidence-grounded operator navigation; unknown/disabled/future capabilities are not inferred successful or defects. SDD14.5 immutable versions and19 final Risk authority remain separate.

**costs_removal:** Browser state/rendering and bounded/polled reads only; no added backend storage or trading/provider authority. Removal would break listed view/evidence consumers.

## frontend/src/views/Research.tsx — KEEP

Owner `frontend`; baseline268 lines, final272. Baseline SHA256 `c4d969d31e8b6ca12e2ceb48333b92c02a920e3a0ffaa79e6ebc38f8779ecef1`; final `8a53e1e441cdab37aaab5d943bf7ccacc3b480c6a9c9122c9449b5e46329a692`.

**purpose:** Current discovery-board funnel, recorded results and detail, separate referee/handoff/held-out registration state. Shell Research; now exact combo links reuse existing ResearchItem.

**consumers:** Current discovery-board funnel, recorded results and detail, separate referee/handoff/held-out registration state. Shell Research; now exact combo links reuse existing ResearchItem.

**evidence:** Source imports, state/query/render branches and consumers inspected; key exact-id and authority paths cross-checked against links.ts, Shell.tsx, current adapters and focused regressions.

**design_boundary:** SDD25 evidence-grounded operator navigation; unknown/disabled/future capabilities are not inferred successful or defects. SDD14.5 immutable versions and19 final Risk authority remain separate.

**costs_removal:** Browser state/rendering and bounded/polled reads only; no added backend storage or trading/provider authority. Removal would break listed view/evidence consumers.

## frontend/src/views/Routes.tsx — KEEP

Owner `frontend`; baseline573 lines, final573. Baseline SHA256 `2c6c90d0b9bb80e02972eedff16735c6af5209418ba37be6a5c4606cd03cc766`; final `2c6c90d0b9bb80e02972eedff16735c6af5209418ba37be6a5c4606cd03cc766`.

**purpose:** Earlier trade-history keyset traversal, strategy workspace, research and diagnostics route composition. Direct tradeHistory/closeTradeDeferred unit consumers retain compatibility contracts; current Shell no longer dispatches this module.

**consumers:** Earlier trade-history keyset traversal, strategy workspace, research and diagnostics route composition. Direct tradeHistory/closeTradeDeferred unit consumers retain compatibility contracts; current Shell no longer dispatches this module.

**evidence:** Source imports, state/query/render branches and consumers inspected; key exact-id and authority paths cross-checked against links.ts, Shell.tsx, current adapters and focused regressions.

**design_boundary:** SDD25 evidence-grounded operator navigation; unknown/disabled/future capabilities are not inferred successful or defects. SDD14.5 immutable versions and19 final Risk authority remain separate. Compatibility/test consumers are intentional retention, not proof of current production route availability; no removal of established adapter tests in this bounded fix.

**costs_removal:** Browser state/rendering and bounded/polled reads only; no added backend storage or trading/provider authority. Removal would break listed view/evidence consumers.

## frontend/src/views/Strategies.tsx — KEEP

Owner `frontend`; baseline152 lines, final161. Baseline SHA256 `44f427af37252c09e3f143fdf207e769ae31139bd8e6c94d21597dfcb4dfcb69`; final `be9185327de2b16a618d2ab6f4b9da8b6b25481cef558e89187c0faec09b611d`.

**purpose:** Current strategy board: exact-version authority separate from legacy journal performance, searchable/sortable registry/detail. Shell Strategies; now family filtering and exact record workspace.

**consumers:** Current strategy board: exact-version authority separate from legacy journal performance, searchable/sortable registry/detail. Shell Strategies; now family filtering and exact record workspace.

**evidence:** Source imports, state/query/render branches and consumers inspected; key exact-id and authority paths cross-checked against links.ts, Shell.tsx, current adapters and focused regressions.

**design_boundary:** SDD25 evidence-grounded operator navigation; unknown/disabled/future capabilities are not inferred successful or defects. SDD14.5 immutable versions and19 final Risk authority remain separate.

**costs_removal:** Browser state/rendering and bounded/polled reads only; no added backend storage or trading/provider authority. Removal would break listed view/evidence consumers.

## frontend/src/views/Tracker.tsx — KEEP

Owner `frontend`; baseline101 lines, final101. Baseline SHA256 `ec522b9b9af31905a79d601c80375ce5351896597c1269cbfbf3cb7b8c47c979`; final `ec522b9b9af31905a79d601c80375ce5351896597c1269cbfbf3cb7b8c47c979`.

**purpose:** Read-only canonical engineering tracker: exact source hashes/NEXT, row scope and evidence gates, workflow counts, filters/detail. Shell tenth tab.

**consumers:** Read-only canonical engineering tracker: exact source hashes/NEXT, row scope and evidence gates, workflow counts, filters/detail. Shell tenth tab.

**evidence:** Source imports, state/query/render branches and consumers inspected; key exact-id and authority paths cross-checked against links.ts, Shell.tsx, current adapters and focused regressions.

**design_boundary:** SDD25 evidence-grounded operator navigation; unknown/disabled/future capabilities are not inferred successful or defects. SDD14.5 immutable versions and19 final Risk authority remain separate.

**costs_removal:** Browser state/rendering and bounded/polled reads only; no added backend storage or trading/provider authority. Removal would break listed view/evidence consumers.

## frontend/src/views/Trades.tsx — KEEP

Owner `frontend`; baseline200 lines, final208. Baseline SHA256 `2cf1614b0de30eda9c57fb50064158432b3b45d3d4bd5aa281c0ff10d334759d`; final `c92d38c9c5f63dd1a754cc4ddb0aa213532432abf3a10bd578636129b3acac58`.

**purpose:** Current bounded journal trade log, risk multiples, forensic row story and typed OwnerControls. Shell Trades; now recorded trade links use existing TradeLineage.

**consumers:** Current bounded journal trade log, risk multiples, forensic row story and typed OwnerControls. Shell Trades; now recorded trade links use existing TradeLineage.

**evidence:** Source imports, state/query/render branches and consumers inspected; key exact-id and authority paths cross-checked against links.ts, Shell.tsx, current adapters and focused regressions.

**design_boundary:** SDD25 evidence-grounded operator navigation; unknown/disabled/future capabilities are not inferred successful or defects. SDD14.5 immutable versions and19 final Risk authority remain separate.

**costs_removal:** Browser state/rendering and bounded/polled reads only; no added backend storage or trading/provider authority. Removal would break listed view/evidence consumers. Existing loadAll caps traversal at 10 pages of 200 rows each minute; not measured as excessive. Local DataTable paging is a different presentation from retained keyset test surface.

## frontend/src/views/graphGeometry.ts — KEEP

Owner `frontend`; baseline339 lines, final339. Baseline SHA256 `4ee344d331bcefa8bbadde9d610950d98090979171d61bc14d1b46ee3207eac3`; final `4ee344d331bcefa8bbadde9d610950d98090979171d61bc14d1b46ee3207eac3`.

**purpose:** Deterministic system/grid/cluster layout and reserved-track orthogonal edge routing; GraphView consumer.

**consumers:** Deterministic system/grid/cluster layout and reserved-track orthogonal edge routing; GraphView consumer.

**evidence:** Source imports, state/query/render branches and consumers inspected; key exact-id and authority paths cross-checked against links.ts, Shell.tsx, current adapters and focused regressions.

**design_boundary:** SDD25 evidence-grounded operator navigation; unknown/disabled/future capabilities are not inferred successful or defects. SDD14.5 immutable versions and19 final Risk authority remain separate.

**costs_removal:** Browser state/rendering and bounded/polled reads only; no added backend storage or trading/provider authority. Removal would break listed view/evidence consumers. Graph rendering caps 40 visible nodes; geometry is browser work, with list fallback/explicit undrawn links.

## frontend/src/views/graphPath.ts — KEEP

Owner `frontend`; baseline36 lines, final36. Baseline SHA256 `e4b6d2707e673a361735515867744aa5b44aa4b909d5753d18f8f1b872a4397c`; final `e4b6d2707e673a361735515867744aa5b44aa4b909d5753d18f8f1b872a4397c`.

**purpose:** Directed BFS over returned graph records without missing-node bridges; PathTrace and graphPath tests.

**consumers:** Directed BFS over returned graph records without missing-node bridges; PathTrace and graphPath tests.

**evidence:** Source imports, state/query/render branches and consumers inspected; key exact-id and authority paths cross-checked against links.ts, Shell.tsx, current adapters and focused regressions.

**design_boundary:** SDD25 evidence-grounded operator navigation; unknown/disabled/future capabilities are not inferred successful or defects. SDD14.5 immutable versions and19 final Risk authority remain separate.

**costs_removal:** Browser state/rendering and bounded/polled reads only; no added backend storage or trading/provider authority. Removal would break listed view/evidence consumers. Graph rendering caps 40 visible nodes; geometry is browser work, with list fallback/explicit undrawn links.

## frontend/src/views/graphScope.ts — KEEP

Owner `frontend`; baseline35 lines, final35. Baseline SHA256 `b08806d14f0b2b326770a81d94708899b0b84a783c01337d01ba4a8d8895231a`; final `b08806d14f0b2b326770a81d94708899b0b84a783c01337d01ba4a8d8895231a`.

**purpose:** Lens/search/immediate-neighbor filter, timeline chronology and 40-node rendering bound; GraphView and contracts tests.

**consumers:** Lens/search/immediate-neighbor filter, timeline chronology and 40-node rendering bound; GraphView and contracts tests.

**evidence:** Source imports, state/query/render branches and consumers inspected; key exact-id and authority paths cross-checked against links.ts, Shell.tsx, current adapters and focused regressions.

**design_boundary:** SDD25 evidence-grounded operator navigation; unknown/disabled/future capabilities are not inferred successful or defects. SDD14.5 immutable versions and19 final Risk authority remain separate.

**costs_removal:** Browser state/rendering and bounded/polled reads only; no added backend storage or trading/provider authority. Removal would break listed view/evidence consumers. Graph rendering caps 40 visible nodes; geometry is browser work, with list fallback/explicit undrawn links.

## frontend/src/views/tracker.css — KEEP

Owner `frontend`; baseline15 lines, final15. Baseline SHA256 `463b61f84b52e185c2de1f46e5de1a7a6f523ff2fdea94a9a2d0580b250e7007`; final `463b61f84b52e185c2de1f46e5de1a7a6f523ff2fdea94a9a2d0580b250e7007`.

**purpose:** Tracker-scoped scrollable wide ledger, sticky headers and evidence detail styling; imported by Tracker.tsx.

**consumers:** Tracker-scoped scrollable wide ledger, sticky headers and evidence detail styling; imported by Tracker.tsx.

**evidence:** Source imports, state/query/render branches and consumers inspected; key exact-id and authority paths cross-checked against links.ts, Shell.tsx, current adapters and focused regressions.

**design_boundary:** SDD25 evidence-grounded operator navigation; unknown/disabled/future capabilities are not inferred successful or defects. SDD14.5 immutable versions and19 final Risk authority remain separate.

**costs_removal:** Browser state/rendering and bounded/polled reads only; no added backend storage or trading/provider authority. Removal would break listed view/evidence consumers.

## frontend/src/widgets.css — KEEP

Owner `frontend`; baseline34 lines, final34. Baseline SHA256 `bede91d9d960acb6fb257bbfbc1814e9777c58299eecae0bc215b5382c1c460f`; final `bede91d9d960acb6fb257bbfbc1814e9777c58299eecae0bc215b5382c1c460f`.

**purpose:** Bridge retained evidence/query/drawer widgets into modern Glacier presentation.

**consumers:** frontend/src/demo.tsx; frontend/src/liveRoot.tsx

**evidence:** frontend/src/widgets.css:1; SDD.md:1768 (operator evidence), SDD.md:1795 (technology), SDD.md:1822 (routes), SDD.md:2367 (typed owner interface)

**design_boundary:** Presentation/read contract only; no direct venue order, provider credential or production-store write authority. Missing implementation connectivity alone is not a defect.

**costs_removal:** 4350 bytes; 34 lines. 33 declaration/rule blocks; 0 media guards; only local URL assets. Consolidating theme layers requires visual evidence and is optional; removing breaks documented consumers.

## frontend/tests/acc03.test.tsx — KEEP

Owner `frontend`; baseline53 lines, final53. Baseline SHA256 `54ef6fd9ee1ce27d7133b19ec13a26deab706578d74c724a192e3de7300b1fe2`; final `54ef6fd9ee1ce27d7133b19ec13a26deab706578d74c724a192e3de7300b1fe2`.

**purpose:** Economics evidence distinguishes venue/journal/derived status and coverage; rejects missing coverage and unknown placeholder zero.

**consumers:** Vitest include tests/**/*.test.tsx and tests/**/*.test.ts; imports @testing-library/react, vitest, ../src/views/LiveReads, ../src/adapters/readContracts

**evidence:** Source imports, test cases, assertions and fixture construction inspected; test labels: renders journal, venue and derived statuses and source coverage separately; rejects economics without coverage at the adapter boundary; UNKNOWN and DERIVED_ESTIMATE cannot look like actual venue zero

**design_boundary:** Offline component/adapter evidence only. Tests retaining older composed Routes/Operations contracts do not prove current Shell usability; new Review09 cases cover current modern views.

**costs_removal:** Development-only test fixtures/mocks; no runtime storage or performance overhead. Removal loses distinct regression contracts despite milestone overlap.

## frontend/tests/browser/accounting-truth.spec.ts — KEEP

Owner `frontend`; baseline34 lines, final34. Baseline SHA256 `06b785677af578fe384afaec94abcf438c5beeafe857a1d4a912b79abedf8e1a`; final `06b785677af578fe384afaec94abcf438c5beeafe857a1d4a912b79abedf8e1a`.

**purpose:** Browser regression accounting-truth.spec.ts: Overview UNKNOWN journal placeholder never becomes flat P&L; Overview derived P&L remains visibly estimated

**consumers:** Explicit Playwright test selection/configs and historical evidence reproduction; not imported by production frontend.

**evidence:** Test names, imports, navigation/API call patterns, asserted behavior and fixture dependency inspected. Historical selectors/route composition may target earlier UI milestones; not executed during Review09.

**design_boundary:** Synthetic/isolated test evidence is not current runtime, venue, admission or trading readiness. Legacy browser suites require explicit fixture environment; default configs were not launched.

**costs_removal:** Development-only browser/fixture/test artifacts. Some suites write historical evidence paths or call fixture state APIs; removal discards reviewed regression history. No production storage/performance overhead.

## frontend/tests/browser/close-trade-deferred.spec.ts — KEEP

Owner `frontend`; baseline47 lines, final47. Baseline SHA256 `8d5f7b4ad21bf72eafbd4df8601456e899a5b2f466fa3f4a85e61442411f71c0`; final `8d5f7b4ad21bf72eafbd4df8601456e899a5b2f466fa3f4a85e61442411f71c0`.

**purpose:** Browser regression close-trade-deferred.spec.ts: an open trade

**consumers:** Explicit Playwright test selection/configs and historical evidence reproduction; not imported by production frontend.

**evidence:** Test names, imports, navigation/API call patterns, asserted behavior and fixture dependency inspected. Historical selectors/route composition may target earlier UI milestones; not executed during Review09.

**design_boundary:** Synthetic/isolated test evidence is not current runtime, venue, admission or trading readiness. Legacy browser suites require explicit fixture environment; default configs were not launched.

**costs_removal:** Development-only browser/fixture/test artifacts. Some suites write historical evidence paths or call fixture state APIs; removal discards reviewed regression history. No production storage/performance overhead.

## frontend/tests/browser/corrections.spec.ts — KEEP

Owner `frontend`; baseline536 lines, final536. Baseline SHA256 `55e6fa2c6a4be2b09ebb95cba9498841ca674ad8cc45c67edfc22822c95a7f3f`; final `55e6fa2c6a4be2b09ebb95cba9498841ca674ad8cc45c67edfc22822c95a7f3f`.

**purpose:** Browser regression corrections.spec.ts: F1 spacing, unrelated ports, exact directions, filtering and 40-node fan-in/back-edges; F2 stale primary status is consistent on nodes, inspector and list; unknown stays distinct; cancel markers survive button, scenario, route exit and subsequent messages; streaming respects reading position and jump to latest; dangling refs are unavailable; 500-input scope and search reuse graph, hidden replay cleanup, mobile core and opt-in graph; record 500-input / 40-visible real graph performance

**consumers:** Explicit Playwright test selection/configs and historical evidence reproduction; not imported by production frontend.

**evidence:** Test names, imports, navigation/API call patterns, asserted behavior and fixture dependency inspected. Historical selectors/route composition may target earlier UI milestones; not executed during Review09.

**design_boundary:** Synthetic/isolated test evidence is not current runtime, venue, admission or trading readiness. Legacy browser suites require explicit fixture environment; default configs were not launched.

**costs_removal:** Development-only browser/fixture/test artifacts. Some suites write historical evidence paths or call fixture state APIs; removal discards reviewed regression history. No production storage/performance overhead.

## frontend/tests/browser/evidencePath.ts — KEEP

Owner `frontend`; baseline12 lines, final12. Baseline SHA256 `1b600f65d76af7abf8b136db7175d7636487a41202ac3947a0b86417a1073d0a`; final `1b600f65d76af7abf8b136db7175d7636487a41202ac3947a0b86417a1073d0a`.

**purpose:** Browser regression evidencePath.ts: shared browser evidence/fixture helper

**consumers:** Explicit Playwright test selection/configs and historical evidence reproduction; not imported by production frontend.

**evidence:** Test names, imports, navigation/API call patterns, asserted behavior and fixture dependency inspected. Historical selectors/route composition may target earlier UI milestones; not executed during Review09.

**design_boundary:** Synthetic/isolated test evidence is not current runtime, venue, admission or trading readiness. Legacy browser suites require explicit fixture environment; default configs were not launched.

**costs_removal:** Development-only browser/fixture/test artifacts. Some suites write historical evidence paths or call fixture state APIs; removal discards reviewed regression history. No production storage/performance overhead.

## frontend/tests/browser/glacier-corrections.spec.ts — KEEP

Owner `frontend`; baseline128 lines, final128. Baseline SHA256 `407a053ae4582ef621a4faddfd54a8298aa48c4e1a3cfb3e49a687a17353ce1c`; final `407a053ae4582ef621a4faddfd54a8298aa48c4e1a3cfb3e49a687a17353ce1c`.

**purpose:** Browser regression glacier-corrections.spec.ts: ${name}: permanent route renders approved Glacier with isolated read-only truth; dynamic ${environment} account labels require fresh backend evidence; research bank count and registered look are independent of disabled referee; exact version shows stale approval and unavailable economics separately from legacy; correction layouts at ${width}px; Needs You ${state} remains truthful; Operations filtered emptiness differs from an empty window

**consumers:** Explicit Playwright test selection/configs and historical evidence reproduction; not imported by production frontend.

**evidence:** Test names, imports, navigation/API call patterns, asserted behavior and fixture dependency inspected. Historical selectors/route composition may target earlier UI milestones; not executed during Review09.

**design_boundary:** Synthetic/isolated test evidence is not current runtime, venue, admission or trading readiness. Legacy browser suites require explicit fixture environment; default configs were not launched.

**costs_removal:** Development-only browser/fixture/test artifacts. Some suites write historical evidence paths or call fixture state APIs; removal discards reviewed regression history. No production storage/performance overhead.

## frontend/tests/browser/glacier-v2.spec.ts — KEEP

Owner `frontend`; baseline135 lines, final135. Baseline SHA256 `580a3fd839c4edfe6c66031a12307e0f9c7316c0e2794fe8566cc6a1dc598859`; final `580a3fd839c4edfe6c66031a12307e0f9c7316c0e2794fe8566cc6a1dc598859`.

**purpose:** Browser regression glacier-v2.spec.ts: V2 all permanent routes, responsive ${width}; V2 rail keyboard navigation keeps shell and lands at content; V2 detail trap, Escape, selected origin and focus return; V2 reduced motion is immediate and stops walkthrough dynamically; V2 declared paths never show live flow; subsystem isolation; V2 read-only controls and machine are locked; V2 graph stable slots, written relation filter, complete neighbor list; V2 hour selection narrows only listed events and keeps disclosure

**consumers:** Explicit Playwright test selection/configs and historical evidence reproduction; not imported by production frontend.

**evidence:** Test names, imports, navigation/API call patterns, asserted behavior and fixture dependency inspected. Historical selectors/route composition may target earlier UI milestones; not executed during Review09.

**design_boundary:** Synthetic/isolated test evidence is not current runtime, venue, admission or trading readiness. Legacy browser suites require explicit fixture environment; default configs were not launched.

**costs_removal:** Development-only browser/fixture/test artifacts. Some suites write historical evidence paths or call fixture state APIs; removal discards reviewed regression history. No production storage/performance overhead.

## frontend/tests/browser/gui01-readonly.spec.ts — KEEP

Owner `frontend`; baseline14 lines, final14. Baseline SHA256 `c242c3e7a44c73bd4f82467fd7a0312dd3af4862e30472512ec93e31b9f5ec47`; final `c242c3e7a44c73bd4f82467fd7a0312dd3af4862e30472512ec93e31b9f5ec47`.

**purpose:** Browser regression gui01-readonly.spec.ts: GUI-01 real read-only server renders Overview with Kernel STOPPED

**consumers:** Explicit Playwright test selection/configs and historical evidence reproduction; not imported by production frontend.

**evidence:** Test names, imports, navigation/API call patterns, asserted behavior and fixture dependency inspected. Historical selectors/route composition may target earlier UI milestones; not executed during Review09.

**design_boundary:** Synthetic/isolated test evidence is not current runtime, venue, admission or trading readiness. Legacy browser suites require explicit fixture environment; default configs were not launched.

**costs_removal:** Development-only browser/fixture/test artifacts. Some suites write historical evidence paths or call fixture state APIs; removal discards reviewed regression history. No production storage/performance overhead.

## frontend/tests/browser/gui01.spec.ts — KEEP

Owner `frontend`; baseline53 lines, final53. Baseline SHA256 `5ee7e01d1d4ffd2c260be823b461b263379b68d1aa5dc99850fcdec704319e0f`; final `5ee7e01d1d4ffd2c260be823b461b263379b68d1aa5dc99850fcdec704319e0f`.

**purpose:** Browser regression gui01.spec.ts: GUI-01 stopped kernel, figures and exact owner evidence remain readable; GUI-01 partial realized stays unavailable with explicit coverage; GUI-01 missing observations do not become zero

**consumers:** Explicit Playwright test selection/configs and historical evidence reproduction; not imported by production frontend.

**evidence:** Test names, imports, navigation/API call patterns, asserted behavior and fixture dependency inspected. Historical selectors/route composition may target earlier UI milestones; not executed during Review09.

**design_boundary:** Synthetic/isolated test evidence is not current runtime, venue, admission or trading readiness. Legacy browser suites require explicit fixture environment; default configs were not launched.

**costs_removal:** Development-only browser/fixture/test artifacts. Some suites write historical evidence paths or call fixture state APIs; removal discards reviewed regression history. No production storage/performance overhead.

## frontend/tests/browser/live-perf.spec.ts — KEEP

Owner `frontend`; baseline250 lines, final250. Baseline SHA256 `b680bcf9b5939434a7c9e88267f24b008fd700980fcc5acb8e39368c5dd96900`; final `b680bcf9b5939434a7c9e88267f24b008fd700980fcc5acb8e39368c5dd96900`.

**purpose:** Browser regression live-perf.spec.ts: cold load, cached routes and lazy surfaces; chat and navigation stay responsive while a heavy graph loads; route churn, graph pages, websocket use and server RSS

**consumers:** Explicit Playwright test selection/configs and historical evidence reproduction; not imported by production frontend.

**evidence:** Test names, imports, navigation/API call patterns, asserted behavior and fixture dependency inspected. Historical selectors/route composition may target earlier UI milestones; not executed during Review09.

**design_boundary:** Synthetic/isolated test evidence is not current runtime, venue, admission or trading readiness. Legacy browser suites require explicit fixture environment; default configs were not launched.

**costs_removal:** Development-only browser/fixture/test artifacts. Some suites write historical evidence paths or call fixture state APIs; removal discards reviewed regression history. No production storage/performance overhead.

## frontend/tests/browser/live.spec.ts — KEEP

Owner `frontend`; baseline432 lines, final432. Baseline SHA256 `cdb117a2850d77e978d74f53f4c16e0d7b3beeb6f549e4cc288a319a4a4b6e86`; final `cdb117a2850d77e978d74f53f4c16e0d7b3beeb6f549e4cc288a319a4a4b6e86`.

**purpose:** Browser regression live.spec.ts: LIVE overview binds real backend values, never fixtures; stale protection can never appear healthy; missing data is shown missing, not zero; LIVE backend failure cannot fall back to DEMO fixtures; unreachable backend shows no data, then reconnects; guarded Resume goes through the Owner Interface; Owner Interface unavailable: controls fail closed; unknown outcome keeps the id; retry re-sends the same request

**consumers:** Explicit Playwright test selection/configs and historical evidence reproduction; not imported by production frontend.

**evidence:** Test names, imports, navigation/API call patterns, asserted behavior and fixture dependency inspected. Historical selectors/route composition may target earlier UI milestones; not executed during Review09.

**design_boundary:** Synthetic/isolated test evidence is not current runtime, venue, admission or trading readiness. Legacy browser suites require explicit fixture environment; default configs were not launched.

**costs_removal:** Development-only browser/fixture/test artifacts. Some suites write historical evidence paths or call fixture state APIs; removal discards reviewed regression history. No production storage/performance overhead.

## frontend/tests/browser/liveHelpers.ts — KEEP

Owner `frontend`; baseline19 lines, final19. Baseline SHA256 `65eff69b4229db827106b8917a04d2e27e8a80721335fdcb4064051b52b580c6`; final `65eff69b4229db827106b8917a04d2e27e8a80721335fdcb4064051b52b580c6`.

**purpose:** Browser regression liveHelpers.ts: shared browser evidence/fixture helper

**consumers:** Explicit Playwright test selection/configs and historical evidence reproduction; not imported by production frontend.

**evidence:** Test names, imports, navigation/API call patterns, asserted behavior and fixture dependency inspected. Historical selectors/route composition may target earlier UI milestones; not executed during Review09.

**design_boundary:** Synthetic/isolated test evidence is not current runtime, venue, admission or trading readiness. Legacy browser suites require explicit fixture environment; default configs were not launched.

**costs_removal:** Development-only browser/fixture/test artifacts. Some suites write historical evidence paths or call fixture state APIs; removal discards reviewed regression history. No production storage/performance overhead.

## frontend/tests/browser/m1-routes.spec.ts — KEEP

Owner `frontend`; baseline294 lines, final294. Baseline SHA256 `9d4904a3274fefbe32ae3f5f9b4da26bccfe515e79c4bd8b9a1c5a3560f73076`; final `9d4904a3274fefbe32ae3f5f9b4da26bccfe515e79c4bd8b9a1c5a3560f73076`.

**purpose:** Browser regression m1-routes.spec.ts: all nine routes render while decisions reads are slow; chunks and other reads stay responsive during slow decisions and paging; failed chunk: error state, then Retry makes a real new request and renders; failed chunk: navigating away and back recovers without a manual reload; trade and strategy drawers show recorded lineage and explicit gaps

**consumers:** Explicit Playwright test selection/configs and historical evidence reproduction; not imported by production frontend.

**evidence:** Test names, imports, navigation/API call patterns, asserted behavior and fixture dependency inspected. Historical selectors/route composition may target earlier UI milestones; not executed during Review09.

**design_boundary:** Synthetic/isolated test evidence is not current runtime, venue, admission or trading readiness. Legacy browser suites require explicit fixture environment; default configs were not launched.

**costs_removal:** Development-only browser/fixture/test artifacts. Some suites write historical evidence paths or call fixture state APIs; removal discards reviewed regression history. No production storage/performance overhead.

## frontend/tests/browser/m2-routes.spec.ts — KEEP

Owner `frontend`; baseline416 lines, final416. Baseline SHA256 `055a765f78b93158976c08c821342901bbf5d8aed885dc10530e802c7ab3d871`; final `055a765f78b93158976c08c821342901bbf5d8aed885dc10530e802c7ab3d871`.

**purpose:** Browser regression m2-routes.spec.ts: all nine routes render their M2 evidence; connected workflow: overview → decision → trade → strategy → research → knowledge; drawer links close the drawer and open the record; LUFFY links only exact record mentions and stays non-operational; a failed record read is shown as a failure; navigation remains; unknown ids are reported as not found, not substituted; a session that ends while on a deep link signs out cleanly; record reads stay responsive while decisions reads are slow

**consumers:** Explicit Playwright test selection/configs and historical evidence reproduction; not imported by production frontend.

**evidence:** Test names, imports, navigation/API call patterns, asserted behavior and fixture dependency inspected. Historical selectors/route composition may target earlier UI milestones; not executed during Review09.

**design_boundary:** Synthetic/isolated test evidence is not current runtime, venue, admission or trading readiness. Legacy browser suites require explicit fixture environment; default configs were not launched.

**costs_removal:** Development-only browser/fixture/test artifacts. Some suites write historical evidence paths or call fixture state APIs; removal discards reviewed regression history. No production storage/performance overhead.

## frontend/tests/browser/m3-capture.spec.ts — KEEP

Owner `frontend`; baseline335 lines, final335. Baseline SHA256 `70c697002442a2e81f047e5ec8aa2e5ab64a3d7cddb3f8a6ccf554544dc47668`; final `70c697002442a2e81f047e5ec8aa2e5ab64a3d7cddb3f8a6ccf554544dc47668`.

**purpose:** Browser regression m3-capture.spec.ts: M3 desktop: all nine routes and connected detail workflows; M3 mobile: representative routes are usable at 390 px; M3 accessibility: axe WCAG 2.1 AA on all nine LIVE routes and key details; M3 LUFFY conversation is usable at 390, 759, 768, 1024 and 1440 px

**consumers:** Explicit Playwright test selection/configs and historical evidence reproduction; not imported by production frontend.

**evidence:** Test names, imports, navigation/API call patterns, asserted behavior and fixture dependency inspected. Historical selectors/route composition may target earlier UI milestones; not executed during Review09.

**design_boundary:** Synthetic/isolated test evidence is not current runtime, venue, admission or trading readiness. Legacy browser suites require explicit fixture environment; default configs were not launched.

**costs_removal:** Development-only browser/fixture/test artifacts. Some suites write historical evidence paths or call fixture state APIs; removal discards reviewed regression history. No production storage/performance overhead.

## frontend/tests/browser/own04.spec.ts — KEEP

Owner `frontend`; baseline36 lines, final36. Baseline SHA256 `d7bfe91fd7079cf765ee4a762ae7dc34ff850eb818672a1dbb6797b3f815b73f`; final `d7bfe91fd7079cf765ee4a762ae7dc34ff850eb818672a1dbb6797b3f815b73f`.

**purpose:** Browser regression own04.spec.ts: OWN-04 overview labels permission separately from unknown process evidence; OWN-04 cached activity ages locally and disconnect clears current activity

**consumers:** Explicit Playwright test selection/configs and historical evidence reproduction; not imported by production frontend.

**evidence:** Test names, imports, navigation/API call patterns, asserted behavior and fixture dependency inspected. Historical selectors/route composition may target earlier UI milestones; not executed during Review09.

**design_boundary:** Synthetic/isolated test evidence is not current runtime, venue, admission or trading readiness. Legacy browser suites require explicit fixture environment; default configs were not launched.

**costs_removal:** Development-only browser/fixture/test artifacts. Some suites write historical evidence paths or call fixture state APIs; removal discards reviewed regression history. No production storage/performance overhead.

## frontend/tests/browser/performance.spec.ts — KEEP

Owner `frontend`; baseline205 lines, final205. Baseline SHA256 `948adfb153f206c0a5e26acebdd32dfa902fa4d5b37a46fd49cc1fa31a089325`; final `948adfb153f206c0a5e26acebdd32dfa902fa4d5b37a46fd49cc1fa31a089325`.

**purpose:** Browser regression performance.spec.ts: record fixture rendering performance and browser resources

**consumers:** Explicit Playwright test selection/configs and historical evidence reproduction; not imported by production frontend.

**evidence:** Test names, imports, navigation/API call patterns, asserted behavior and fixture dependency inspected. Historical selectors/route composition may target earlier UI milestones; not executed during Review09.

**design_boundary:** Synthetic/isolated test evidence is not current runtime, venue, admission or trading readiness. Legacy browser suites require explicit fixture environment; default configs were not launched.

**costs_removal:** Development-only browser/fixture/test artifacts. Some suites write historical evidence paths or call fixture state APIs; removal discards reviewed regression history. No production storage/performance overhead.

## frontend/tests/browser/preview.spec.ts — KEEP

Owner `frontend`; baseline417 lines, final417. Baseline SHA256 `1137ad7c25f1ce3b750af5ba0304e075f3a868cfe494ca896c034831e8b9bbe5`; final `1137ad7c25f1ce3b750af5ba0304e075f3a868cfe494ca896c034831e8b9bbe5`.

**purpose:** Browser regression preview.spec.ts: navigation, deep links, lazy loading and all four actual screens; chat streams, inspects evidence, cancels, handles errors and cannot execute commands; graph search, keyboard selection, zoom/pan, neighbours, lenses and list inspector; system activity requires a supplied event, reduced motion stays static; missing/error states clear old data on all query surfaces; no WebGL needed, mobile layout and keyboard evidence dialog; axe WCAG checks on four rendered surfaces and evidence drawer; slow avatar and graph chunks cannot block navigation or the composer

**consumers:** Explicit Playwright test selection/configs and historical evidence reproduction; not imported by production frontend.

**evidence:** Test names, imports, navigation/API call patterns, asserted behavior and fixture dependency inspected. Historical selectors/route composition may target earlier UI milestones; not executed during Review09.

**design_boundary:** Synthetic/isolated test evidence is not current runtime, venue, admission or trading readiness. Legacy browser suites require explicit fixture environment; default configs were not launched.

**costs_removal:** Development-only browser/fixture/test artifacts. Some suites write historical evidence paths or call fixture state APIs; removal discards reviewed regression history. No production storage/performance overhead.

## frontend/tests/browser/protection-r1.spec.ts — KEEP

Owner `frontend`; baseline58 lines, final58. Baseline SHA256 `cae7b5588e8bcd4104a013bf8e91e438429113d1af75d60d741b50ab67e30438`; final `cae7b5588e8bcd4104a013bf8e91e438429113d1af75d60d741b50ab67e30438`.

**purpose:** Browser regression protection-r1.spec.ts: fresh complete snapshot reads VERIFIED; snapshot ${scenario} is never shown healthy; browser traffic never reaches a venue

**consumers:** Explicit Playwright test selection/configs and historical evidence reproduction; not imported by production frontend.

**evidence:** Test names, imports, navigation/API call patterns, asserted behavior and fixture dependency inspected. Historical selectors/route composition may target earlier UI milestones; not executed during Review09.

**design_boundary:** Synthetic/isolated test evidence is not current runtime, venue, admission or trading readiness. Legacy browser suites require explicit fixture environment; default configs were not launched.

**costs_removal:** Development-only browser/fixture/test artifacts. Some suites write historical evidence paths or call fixture state APIs; removal discards reviewed regression history. No production storage/performance overhead.

## frontend/tests/browser/protection-r2.spec.ts — KEEP

Owner `frontend`; baseline68 lines, final68. Baseline SHA256 `a8c76d581a5705a9650f9caa839c355d51a4b8800a1810b7c61b74030a241ea6`; final `a8c76d581a5705a9650f9caa839c355d51a4b8800a1810b7c61b74030a241ea6`.

**purpose:** Browser regression protection-r2.spec.ts: VERIFIED → API failures → clock passes expiry → STALE; VERIFIED → UNREADABLE when the next poll succeeds with a failed check; missing → first snapshot → VERIFIED; PARTIAL → VERIFIED

**consumers:** Explicit Playwright test selection/configs and historical evidence reproduction; not imported by production frontend.

**evidence:** Test names, imports, navigation/API call patterns, asserted behavior and fixture dependency inspected. Historical selectors/route composition may target earlier UI milestones; not executed during Review09.

**design_boundary:** Synthetic/isolated test evidence is not current runtime, venue, admission or trading readiness. Legacy browser suites require explicit fixture environment; default configs were not launched.

**costs_removal:** Development-only browser/fixture/test artifacts. Some suites write historical evidence paths or call fixture state APIs; removal discards reviewed regression history. No production storage/performance overhead.

## frontend/tests/browser/root-cutover.spec.ts — KEEP

Owner `frontend`; baseline82 lines, final82. Baseline SHA256 `898074f8e85a59f93df241bd3d5fdada4bd51ed96c8b75ee53f8cd22a5481acf`; final `898074f8e85a59f93df241bd3d5fdada4bd51ed96c8b75ee53f8cd22a5481acf`.

**purpose:** Browser regression root-cutover.spec.ts: all nine root hash routes direct-load and refresh; deferred features keep their reviewed truth at /; root timing: cold / and warm Overview

**consumers:** Explicit Playwright test selection/configs and historical evidence reproduction; not imported by production frontend.

**evidence:** Test names, imports, navigation/API call patterns, asserted behavior and fixture dependency inspected. Historical selectors/route composition may target earlier UI milestones; not executed during Review09.

**design_boundary:** Synthetic/isolated test evidence is not current runtime, venue, admission or trading readiness. Legacy browser suites require explicit fixture environment; default configs were not launched.

**costs_removal:** Development-only browser/fixture/test artifacts. Some suites write historical evidence paths or call fixture state APIs; removal discards reviewed regression history. No production storage/performance overhead.

## frontend/tests/browser/stage8.spec.ts — KEEP

Owner `frontend`; baseline54 lines, final54. Baseline SHA256 `c40faaf564e6c27b20afaf0e52f873b9e4ab4869077f67fae720ab97d6d81064`; final `c40faaf564e6c27b20afaf0e52f873b9e4ab4869077f67fae720ab97d6d81064`.

**purpose:** Browser regression stage8.spec.ts: typed owner interrogations and Needs You are available without fixture substitution; Knowledge lenses distinguish event chronology from file modification and absent code; failed Needs You endpoint is explicit and cannot enable approvals; pending owner actions survive completed history and later pages are reachable; Research route renders stored questions rather than false absence

**consumers:** Explicit Playwright test selection/configs and historical evidence reproduction; not imported by production frontend.

**evidence:** Test names, imports, navigation/API call patterns, asserted behavior and fixture dependency inspected. Historical selectors/route composition may target earlier UI milestones; not executed during Review09.

**design_boundary:** Synthetic/isolated test evidence is not current runtime, venue, admission or trading readiness. Legacy browser suites require explicit fixture environment; default configs were not launched.

**costs_removal:** Development-only browser/fixture/test artifacts. Some suites write historical evidence paths or call fixture state APIs; removal discards reviewed regression history. No production storage/performance overhead.

## frontend/tests/browser/trade-history.spec.ts — KEEP

Owner `frontend`; baseline325 lines, final325. Baseline SHA256 `ee04d4217e813d89d7008ae3955b4e1d25d017449aa5d38d86b7e9d37a5669e8`; final `ee04d4217e813d89d7008ae3955b4e1d25d017449aa5d38d86b7e9d37a5669e8`.

**purpose:** Browser regression trade-history.spec.ts: pages through the full history with exact ranges and legacy parity; a trade inserted after page 1 is neither duplicated nor silently absorbed; a backdated trade behind the cursor warns on every later page until Newest; Astra cancellation: a late insert plus a departure still warns with equal counts; an unchanged traversal never warns; a close during paging updates the row once and keeps positions; a late page response never overwrites the page on screen; changing the filter while a request is pending shows only the latest filter

**consumers:** Explicit Playwright test selection/configs and historical evidence reproduction; not imported by production frontend.

**evidence:** Test names, imports, navigation/API call patterns, asserted behavior and fixture dependency inspected. Historical selectors/route composition may target earlier UI milestones; not executed during Review09.

**design_boundary:** Synthetic/isolated test evidence is not current runtime, venue, admission or trading readiness. Legacy browser suites require explicit fixture environment; default configs were not launched.

**costs_removal:** Development-only browser/fixture/test artifacts. Some suites write historical evidence paths or call fixture state APIs; removal discards reviewed regression history. No production storage/performance overhead.

## frontend/tests/browser/v2-controls.spec.ts — KEEP

Owner `frontend`; baseline78 lines, final78. Baseline SHA256 `b2ba9c6231188987ad5d2edc51274740eb5d768b6a9e02bd77a69833b5cf528f`; final `b2ba9c6231188987ad5d2edc51274740eb5d768b6a9e02bd77a69833b5cf528f`.

**purpose:** Browser regression v2-controls.spec.ts: all five controls use confirmations and the Owner Interface; IN_PROGRESS retains the same id and time across route exit and retry

**consumers:** Explicit Playwright test selection/configs and historical evidence reproduction; not imported by production frontend.

**evidence:** Test names, imports, navigation/API call patterns, asserted behavior and fixture dependency inspected. Historical selectors/route composition may target earlier UI milestones; not executed during Review09.

**design_boundary:** Synthetic/isolated test evidence is not current runtime, venue, admission or trading readiness. Legacy browser suites require explicit fixture environment; default configs were not launched.

**costs_removal:** Development-only browser/fixture/test artifacts. Some suites write historical evidence paths or call fixture state APIs; removal discards reviewed regression history. No production storage/performance overhead.

## frontend/tests/browser/v2.spec.ts — KEEP

Owner `frontend`; baseline179 lines, final179. Baseline SHA256 `4415aede1e6fb3f8a0376004c352227fd1fb695d589f421c2d0432a66493aa04`; final `4415aede1e6fb3f8a0376004c352227fd1fb695d589f421c2d0432a66493aa04`.

**purpose:** Browser regression v2.spec.ts: V2 all nine desktop and mobile surfaces, accessibility and screenshots; V2 trade search, filters, drawer, mobile details and empty results; V2 unavailable and error sources never become successful activity; V2 reduced motion and mobile core remain responsive; Knowledge traces real directed paths and preserves missing-path uncertainty

**consumers:** Explicit Playwright test selection/configs and historical evidence reproduction; not imported by production frontend.

**evidence:** Test names, imports, navigation/API call patterns, asserted behavior and fixture dependency inspected. Historical selectors/route composition may target earlier UI milestones; not executed during Review09.

**design_boundary:** Synthetic/isolated test evidence is not current runtime, venue, admission or trading readiness. Legacy browser suites require explicit fixture environment; default configs were not launched.

**costs_removal:** Development-only browser/fixture/test artifacts. Some suites write historical evidence paths or call fixture state APIs; removal discards reviewed regression history. No production storage/performance overhead.

## frontend/tests/browser/v21.spec.ts — KEEP

Owner `frontend`; baseline333 lines, final333. Baseline SHA256 `72ea7d094c0327d7c80e14f0c2fb2d4d45641da565edddf05e85d8523435e03f`; final `72ea7d094c0327d7c80e14f0c2fb2d4d45641da565edddf05e85d8523435e03f`.

**purpose:** Browser regression v21.spec.ts: V2.1 cancel with draft never submits via ${activation}; V2.1 all mobile routes are visible without horizontal scrolling and keyboard reachable; V2.1 topology uses real Owner Interface health and distinguishes absent telemetry; V2.1 knowledge shows modification age without asserting freshness; no legacy fallback; V2.1 open trade truth, bounded trade history and UTC presentation; V2.1 realized today ${amount} has sign-aware styling; V2.1 login preserves requested slash hash safely; V2.1 Decisions binds offset paging with lookahead and never claims a frozen complete history

**consumers:** Explicit Playwright test selection/configs and historical evidence reproduction; not imported by production frontend.

**evidence:** Test names, imports, navigation/API call patterns, asserted behavior and fixture dependency inspected. Historical selectors/route composition may target earlier UI milestones; not executed during Review09.

**design_boundary:** Synthetic/isolated test evidence is not current runtime, venue, admission or trading readiness. Legacy browser suites require explicit fixture environment; default configs were not launched.

**costs_removal:** Development-only browser/fixture/test artifacts. Some suites write historical evidence paths or call fixture state APIs; removal discards reviewed regression history. No production storage/performance overhead.

## frontend/tests/closeTradeDeferred.test.tsx — KEEP

Owner `frontend`; baseline90 lines, final90. Baseline SHA256 `649dd269f7c5f770b5c228cc6f69feb96edb5c52283bb353d26d806d05d30959`; final `649dd269f7c5f770b5c228cc6f69feb96edb5c52283bb353d26d806d05d30959`.

**purpose:** Deferred individual close-trade contract: no mutation method, no source close_trade call, no control in open trade record.

**consumers:** Vitest include tests/**/*.test.tsx and tests/**/*.test.ts; imports vitest, node:fs, node:path, @testing-library/react, @testing-library/user-event, @tanstack/react-query, ../src/adapters/live, ../src/context

**evidence:** Source imports, test cases, assertions and fixture construction inspected; test labels: Close Trade is deferred (safety-blocked); an open trade; the LIVE adapter exposes no close-trade action; no React source sends the close_trade mutation

**design_boundary:** Offline component/adapter evidence only. Tests retaining older composed Routes/Operations contracts do not prove current Shell usability; new Review09 cases cover current modern views.

**costs_removal:** Development-only test fixtures/mocks; no runtime storage or performance overhead. Removal loses distinct regression contracts despite milestone overlap.

## frontend/tests/components.test.tsx — KEEP

Owner `frontend`; baseline62 lines, final62. Baseline SHA256 `b4b67cdc16b862973aa4f379b90046747e15dbce0343db8c714ccad0b8c7053f`; final `b4b67cdc16b862973aa4f379b90046747e15dbce0343db8c714ccad0b8c7053f`.

**purpose:** Conversation whitespace/failure/cancellation behavior and evidence-dialog Escape/focus return.

**consumers:** Vitest include tests/**/*.test.tsx and tests/**/*.test.ts; imports @testing-library/react, @testing-library/user-event, vitest, @tanstack/react-query, ../src/context, ../src/views/Luffy, ../src/components/ui, ../src/adapters/fixture

**evidence:** Source imports, test cases, assertions and fixture construction inspected; test labels: conversation and evidence; blocks whitespace and preserves owner text on adapter failure; cancels a pending reply without appending late output; opens an accessible evidence dialog and returns focus on Escape

**design_boundary:** Offline component/adapter evidence only. Tests retaining older composed Routes/Operations contracts do not prove current Shell usability; new Review09 cases cover current modern views.

**costs_removal:** Development-only test fixtures/mocks; no runtime storage or performance overhead. Removal loses distinct regression contracts despite milestone overlap.

## frontend/tests/contracts.test.ts — KEEP

Owner `frontend`; baseline74 lines, final74. Baseline SHA256 `d78fe9a7f67222471fb18292f8f8ffa1e763957b5ecb650b564940e2e44be3f2`; final `d78fe9a7f67222471fb18292f8f8ffa1e763957b5ecb650b564940e2e44be3f2`.

**purpose:** Fixture error/null/abort contracts and bounded graph scope/lens/neighbors.

**consumers:** Vitest include tests/**/*.test.tsx and tests/**/*.test.ts; imports vitest, ../src/adapters/fixture, ../src/views/graphScope

**evidence:** Source imports, test cases, assertions and fixture construction inspected; test labels: preview boundaries; rejects errors without substituting data; keeps missing financial values null and protection unavailable; does not supply activity for stale or missing topology; bounds large scopes and excludes edges outside the scope; filters lenses and finds only immediate neighbours; aborts already cancelled requests

**design_boundary:** Offline component/adapter evidence only. Tests retaining older composed Routes/Operations contracts do not prove current Shell usability; new Review09 cases cover current modern views.

**costs_removal:** Development-only test fixtures/mocks; no runtime storage or performance overhead. Removal loses distinct regression contracts despite milestone overlap.

## frontend/tests/diagnosticCorrections.test.tsx — KEEP

Owner `frontend`; baseline43 lines, final43. Baseline SHA256 `5f9909cbf9cf0215386d50ed1eaf93660ca7a94dff71e815552e600a95959924`; final `5f9909cbf9cf0215386d50ed1eaf93660ca7a94dff71e815552e600a95959924`.

**purpose:** Recovery/reconciliation freshness, collector status/freshness separation, Needs You count and filesystem summaries.

**consumers:** Vitest include tests/**/*.test.tsx and tests/**/*.test.ts; imports vitest, @testing-library/react, ../src/diagnosticTruth, ../src/components/StorageTruth

**evidence:** Source imports, test cases, assertions and fixture construction inspected; test labels: Supervisor evidence controls display, never historical counts; shows a 46-hour-old recovery report as STALE, with no active recovery claim; does not make recent activity without proof healthy; distinguishes pending, failed/unavailable, empty and populated owner-action counts; separates last recorded collector error from report freshness; keeps all filesystem free-space summaries and missing baseline explicit

**design_boundary:** Offline component/adapter evidence only. Tests retaining older composed Routes/Operations contracts do not prove current Shell usability; new Review09 cases cover current modern views.

**costs_removal:** Development-only test fixtures/mocks; no runtime storage or performance overhead. Removal loses distinct regression contracts despite milestone overlap.

## frontend/tests/fixtures/glacier-readonly.json — KEEP

Owner `frontend`; baseline2436 lines, final2436. Baseline SHA256 `f602aacfe1a7c4c8544a13b944cd0e8807188ad46fa47bef1d71f0991414a6c5`; final `f602aacfe1a7c4c8544a13b944cd0e8807188ad46fa47bef1d71f0991414a6c5`.

**purpose:** Shared static read-only browser responses for bootstrap, overview, desk, board/log/storage/knowledge/system, Needs You, trades and Owner Interface.

**consumers:** glacier-corrections.spec.ts and glacier-v2.spec.ts read fixture explicitly; no production imports.

**evidence:** JSON structure and each endpoint/top-level response shape inspected, including explicit missing/disabled states and fixture description;75685 bytes frozen test input.

**design_boundary:** Static fixture records remain synthetic test inputs, never production fallback or fresh runtime truth.

**costs_removal:** 75KB tracked test fixture; no production database or browser bundle cost. Removing breaks isolated browser tests.

## frontend/tests/glacierTruth.test.tsx — KEEP

Owner `frontend`; baseline133 lines, final133. Baseline SHA256 `bebe1295240083dcd262ba971cc6e4fa3d2b571dab6f9e3b7341653aa6b87e9c`; final `bebe1295240083dcd262ba971cc6e4fa3d2b571dab6f9e3b7341653aa6b87e9c`.

**purpose:** Modern runtime/account/provider/read-only truth, discovery versus admission, exact strategy versions, operation disclosure and diagnostics.

**consumers:** Vitest include tests/**/*.test.tsx and tests/**/*.test.ts; imports vitest, @testing-library/react, @tanstack/react-query, ../src/context, ../src/adapters/fixture, ../src/adapters/live, ../src/components/RuntimeTruth, ../src/components/StorageTruth

**evidence:** Source imports, test cases, assertions and fixture construction inspected; test labels: runtime truth; never derives trading/account mode from adapter LIVE; renders provider-disabled local evidence without claiming external availability; shows independent Kernel and control states; disables owner controls even if a health response claims AVAILABLE; research truth; distinguishes disabled, enabled without look, registered and unavailable; renders a nonempty bank without none-yet wording; renders exact-version lifecycle and keeps unknown economics unavailable; shows root/data/recovery separately and cannot turn missing growth into zero

**design_boundary:** Offline component/adapter evidence only. Tests retaining older composed Routes/Operations contracts do not prove current Shell usability; new Review09 cases cover current modern views.

**costs_removal:** Development-only test fixtures/mocks; no runtime storage or performance overhead. Removal loses distinct regression contracts despite milestone overlap.

## frontend/tests/glacierV2.test.tsx — KEEP

Owner `frontend`; baseline36 lines, final36. Baseline SHA256 `75576715d1e6febc24a5609a05d92791d21dd9992df9493ad016dac4a18cec95`; final `75576715d1e6febc24a5609a05d92791d21dd9992df9493ad016dac4a18cec95`.

**purpose:** Motion/core-state accessible geometry, immediate factual value changes and selected-row identity across sorting.

**consumers:** Vitest include tests/**/*.test.tsx and tests/**/*.test.ts; imports vitest, @testing-library/react, ../src/components/motion, ../src/components/LuffyCore, ../src/components/glass

**evidence:** Source imports, test cases, assertions and fixture construction inspected; test labels: Glacier V2 truth-preserving presentation; distinguishes every core state by geometry and accessible text; renders changed values and unavailable immediately without intermediate numbers; keeps selected row identity after sorting and exposes exact active sort

**design_boundary:** Offline component/adapter evidence only. Tests retaining older composed Routes/Operations contracts do not prove current Shell usability; new Review09 cases cover current modern views.

**costs_removal:** Development-only test fixtures/mocks; no runtime storage or performance overhead. Removal loses distinct regression contracts despite milestone overlap.

## frontend/tests/graphPath.test.ts — KEEP

Owner `frontend`; baseline31 lines, final31. Baseline SHA256 `75cfb575a06560cceac0bc2bb34d4a6cffe2aa0915700dd9aaf2be78f47a33ea`; final `75cfb575a06560cceac0bc2bb34d4a6cffe2aa0915700dd9aaf2be78f47a33ea`.

**purpose:** Directed/cyclic BFS behavior with missing endpoints and identity paths.

**consumers:** Vitest include tests/**/*.test.tsx and tests/**/*.test.ts; imports vitest, ../src/adapters/contracts, ../src/views/graphPath

**evidence:** Source imports, test cases, assertions and fixture construction inspected; test labels: follows directed edges and terminates on cycles; does not invent missing-reference bridges or a reversed connection; handles an identity path without claiming an edge

**design_boundary:** Offline component/adapter evidence only. Tests retaining older composed Routes/Operations contracts do not prove current Shell usability; new Review09 cases cover current modern views.

**costs_removal:** Development-only test fixtures/mocks; no runtime storage or performance overhead. Removal loses distinct regression contracts despite milestone overlap.

## frontend/tests/harness/index.html — KEEP

Owner `frontend`; baseline12 lines, final12. Baseline SHA256 `f65c5c09e0aa330d7e8ba98d27fa8a2a7f45c05fc6160ca0db61978fdb3b97ab`; final `f65c5c09e0aa330d7e8ba98d27fa8a2a7f45c05fc6160ca0db61978fdb3b97ab`.

**purpose:** Isolated test-fixtures HTML/React entry:500-input/40-node graph cases, dangling references and cancellable long-chat streaming; no service connection.

**consumers:** Vite test-fixtures rollup entry and preview/corrections/performance browser suites.

**evidence:** Full harness HTML and React adapter override/render/lifecycle body inspected.

**design_boundary:** Dedicated build mode; fixture actions do not authorize production/providers/trading.

**costs_removal:** Development-only generated graph/message input with bounded rendering; deleting removes adversarial graph/cancellation verification.

## frontend/tests/harness/main.tsx — KEEP

Owner `frontend`; baseline140 lines, final140. Baseline SHA256 `e8997fe36ed09ec456405d9c5fbb153562246c817f998bfa82f65eb60065399e`; final `e8997fe36ed09ec456405d9c5fbb153562246c817f998bfa82f65eb60065399e`.

**purpose:** Isolated test-fixtures HTML/React entry:500-input/40-node graph cases, dangling references and cancellable long-chat streaming; no service connection.

**consumers:** Vite test-fixtures rollup entry and preview/corrections/performance browser suites.

**evidence:** Full harness HTML and React adapter override/render/lifecycle body inspected.

**design_boundary:** Dedicated build mode; fixture actions do not authorize production/providers/trading.

**costs_removal:** Development-only generated graph/message input with bounded rendering; deleting removes adversarial graph/cancellation verification.

## frontend/tests/health.test.tsx — KEEP

Owner `frontend`; baseline35 lines, final35. Baseline SHA256 `8dfd08d7e92d905437a5ceec9687344170ca56d3ea20679b3a295a502bee878c`; final `8dfd08d7e92d905437a5ceec9687344170ca56d3ea20679b3a295a502bee878c`.

**purpose:** Historical stale component status versus unknown current health and absent telemetry.

**consumers:** Vitest include tests/**/*.test.tsx and tests/**/*.test.ts; imports @testing-library/react, vitest, ../src/components/HealthStatus, ../src/adapters/fixture

**evidence:** Source imports, test cases, assertions and fixture construction inspected; test labels: stale health can only be historical, not current active styling; missing telemetry differs from unknown current health

**design_boundary:** Offline component/adapter evidence only. Tests retaining older composed Routes/Operations contracts do not prove current Shell usability; new Review09 cases cover current modern views.

**costs_removal:** Development-only test fixtures/mocks; no runtime storage or performance overhead. Removal loses distinct regression contracts despite milestone overlap.

## frontend/tests/live.test.tsx — KEEP

Owner `frontend`; baseline320 lines, final320. Baseline SHA256 `eb8db3a3aa797e5552744d92db830cc7e7a995a11b99590c70e465da7144c42f`; final `eb8db3a3aa797e5552744d92db830cc7e7a995a11b99590c70e465da7144c42f`.

**purpose:** Live adapter failure/null/protection/mark/GraphQL controls and request-id replay, historical chat cancellation and bounded logs.

**consumers:** Vitest include tests/**/*.test.tsx and tests/**/*.test.ts; imports @testing-library/react, @testing-library/user-event, @tanstack/react-query, vitest, ../src/adapters/live, ../src/adapters/pending, ../src/context, ../src/views/Operations

**evidence:** Source imports, test cases, assertions and fixture construction inspected; test labels: live adapter; unrealized estimates use exact trade quotes and expire through failed polling; keeps STALE protection stale and never marks it healthy; maps missing sections to null, not zero; retains per-figure coverage and global signal evidence without filling unknowns; refuses a response that is not the LIVE contract; 401 ends the session; network failure is BackendUnavailable; control: lost answer resolves null (keep the id), never throws a fallback; chat posts only to the chat endpoint; pending owner requests

**design_boundary:** Offline component/adapter evidence only. Tests retaining older composed Routes/Operations contracts do not prove current Shell usability; new Review09 cases cover current modern views.

**costs_removal:** Development-only test fixtures/mocks; no runtime storage or performance overhead. Removal loses distinct regression contracts despite milestone overlap.

## frontend/tests/m1.test.tsx — KEEP

Owner `frontend`; baseline238 lines, final238. Baseline SHA256 `05a757697818e9a8d63f0be3bafdbacecdcb43f7eb5f9baad1307ac7e75ec746`; final `05a757697818e9a8d63f0be3bafdbacecdcb43f7eb5f9baad1307ac7e75ec746`.

**purpose:** Bounded lazy-loading failure/retry and recorded lineage/research unavailable-field contracts.

**consumers:** Vitest include tests/**/*.test.tsx and tests/**/*.test.ts; imports vitest, react, @testing-library/react, @testing-library/user-event, @tanstack/react-query, ../src/lazyChunk, ../src/components/ui, ../src/context

**evidence:** Source imports, test cases, assertions and fixture construction inspected; test labels: bounded lazy route loading; reports a chunk that never arrives instead of loading forever; Retry after a failed chunk reloads the page; it does not re-render the poisoned import; Retry of a render error with no failed chunk resets in place, no reload; LIVE read contracts; shows backend-declared gaps as UNAVAILABLE with their reasons; renders a trade; Research lists recorded results and backend-declared missing sections; rejects a read without generated_at or with a malformed unavailable list; surfaces a failed read as an error, not an empty ledger

**design_boundary:** Offline component/adapter evidence only. Tests retaining older composed Routes/Operations contracts do not prove current Shell usability; new Review09 cases cover current modern views.

**costs_removal:** Development-only test fixtures/mocks; no runtime storage or performance overhead. Removal loses distinct regression contracts despite milestone overlap.

## frontend/tests/m2.test.tsx — KEEP

Owner `frontend`; baseline858 lines, final858. Baseline SHA256 `cb47af67f4337e1ef89533813aa8783b0f0d46a3a34c5634eae0434220fcf5ba`; final `cb47af67f4337e1ef89533813aa8783b0f0d46a3a34c5634eae0434220fcf5ba`.

**purpose:** Connected exact-id evidence links, malformed evidence fail-closed behavior, completeness claims, capped mentions and detailed read contracts.

**consumers:** Vitest include tests/**/*.test.tsx and tests/**/*.test.ts; imports vitest, @testing-library/react, @tanstack/react-query, ../src/context, ../src/adapters/live, ../src/views/LiveReads, ../src/views/Luffy, ../src/adapters/contracts

**evidence:** Source imports, test cases, assertions and fixture construction inspected; test labels: deep links; builds and parses record routes; trade story; links only recorded refs and states unavailable steps; decision detail; links a registered strategy and marks an unregistered id; shows a missing decision as an error, not an empty record; research item; navigates parent, children and seed by recorded hash/id; strategy workspace

**design_boundary:** Offline component/adapter evidence only. Tests retaining older composed Routes/Operations contracts do not prove current Shell usability; new Review09 cases cover current modern views.

**costs_removal:** Development-only test fixtures/mocks; no runtime storage or performance overhead. Removal loses distinct regression contracts despite milestone overlap.

## frontend/tests/own04.test.tsx — KEEP

Owner `frontend`; baseline97 lines, final97. Baseline SHA256 `8e5376c5c630ec3b7b919d3e3684d894ac52265ba32dea072548661a2cdb18c4`; final `8e5376c5c630ec3b7b919d3e3684d894ac52265ba32dea072548661a2cdb18c4`.

**purpose:** Observed process/control separation and browser-clock expiry independent of polling.

**consumers:** Vitest include tests/**/*.test.tsx and tests/**/*.test.ts; imports @testing-library/react, vitest, @tanstack/react-query, ../src/context, ../src/useOwnerTelemetry, ../src/adapters/live, ../src/components/HealthStatus, ../src/adapters/contracts

**evidence:** Source imports, test cases, assertions and fixture construction inspected; test labels: cached observed activity expires on the browser clock without a poll; missing/future time and UNKNOWN health never become ACTIVE; IPC availability expires while polling fails to return, then fails closed on error; cached Overview account, work and owner-attention evidence expire independently of permission

**design_boundary:** Offline component/adapter evidence only. Tests retaining older composed Routes/Operations contracts do not prove current Shell usability; new Review09 cases cover current modern views.

**costs_removal:** Development-only test fixtures/mocks; no runtime storage or performance overhead. Removal loses distinct regression contracts despite milestone overlap.

## frontend/tests/protectionSnapshot.test.tsx — KEEP

Owner `frontend`; baseline257 lines, final257. Baseline SHA256 `4518c655f7ce860e11ce5accf6afe5d5475bf2f0c9ffffdb7fe8e9926765975b`; final `4518c655f7ce860e11ce5accf6afe5d5475bf2f0c9ffffdb7fe8e9926765975b`.

**purpose:** Venue protection snapshot age/expiry/partial/future/unreadable states and Overview failed-poll expiry.

**consumers:** Vitest include tests/**/*.test.tsx and tests/**/*.test.ts; imports vitest, @testing-library/react, @tanstack/react-query, ../src/adapters/live, ../src/protectionExpiry, ../src/context, ../src/views/Overview, ../src/adapters/contracts

**evidence:** Source imports, test cases, assertions and fixture construction inspected; test labels: protection snapshot read semantics; fresh all-true snapshot reads VERIFIED; aged snapshot reads STALE even if the backend said VERIFIED; invalid (future) snapshot time is never fresh; no computable expiry is never VERIFIED; missing check reads UNAVAILABLE / not verified; failed snapshot reads UNREADABLE with its reasons; partial snapshot reads PARTIAL; no single check alone gives VERIFIED; backend UNREADABLE per-position status is preserved; server clock offset is applied to the local expiry

**design_boundary:** Offline component/adapter evidence only. Tests retaining older composed Routes/Operations contracts do not prove current Shell usability; new Review09 cases cover current modern views.

**costs_removal:** Development-only test fixtures/mocks; no runtime storage or performance overhead. Removal loses distinct regression contracts despite milestone overlap.

## frontend/tests/setup.ts — KEEP

Owner `frontend`; baseline14 lines, final14. Baseline SHA256 `a0da3078a93c5dece41ae61d24accf6e9a9936cfeaecac20f9f9768cd0cd00d6`; final `a0da3078a93c5dece41ae61d24accf6e9a9936cfeaecac20f9f9768cd0cd00d6`.

**purpose:** Vitest DOM matchers/cleanup and matchMedia/scrollTo stubs.

**consumers:** vite.config.ts test.setupFiles; all DOM component tests.

**evidence:** Full setup body inspected; cleanup after each test and browser-only method stubs.

**design_boundary:** Test-only stubs; cannot establish actual browser layout/accessibility.

**costs_removal:** Small development-only setup; removing breaks predictable DOM tests.

## frontend/tests/stage8.test.tsx — KEEP

Owner `frontend`; baseline87 lines, final87. Baseline SHA256 `e8a7e5c9abfcfe16a09857135ebc98c8df5a486756b03f0d63d7a110df5e2337`; final `e8a7e5c9abfcfe16a09857135ebc98c8df5a486756b03f0d63d7a110df5e2337`.

**purpose:** Exact-bound owner approvals, stale/expired disabling, typed question paging and record evidence/lens provenance.

**consumers:** Vitest include tests/**/*.test.tsx and tests/**/*.test.ts; imports @testing-library/react, @testing-library/user-event, @tanstack/react-query, vitest, ../src/context, ../src/components/OwnerEvidence, ../src/adapters/live, ../src/adapters/contracts

**evidence:** Source imports, test cases, assertions and fixture construction inspected; test labels: Stage8 owner evidence; sends exact binding through adapter and keeps same id after unknown outcome; stale approvals cannot be submitted; an expired validity window disables a cached valid approval; missing query evidence is unavailable, without fixtures; Evidence, Code and Timeline retain distinct membership and chronology; LIVE chat preserves the queried record hashes instead of inventing provenance; Needs You reports full pending count and exposes pages beyond completed history; typed research catalog navigates to the oldest question page; questions-only Research responses are typed partial evidence; malformed records fail closed

**design_boundary:** Offline component/adapter evidence only. Tests retaining older composed Routes/Operations contracts do not prove current Shell usability; new Review09 cases cover current modern views.

**costs_removal:** Development-only test fixtures/mocks; no runtime storage or performance overhead. Removal loses distinct regression contracts despite milestone overlap.

## frontend/tests/tracker-phase.test.ts — KEEP

Owner `frontend`; baseline29 lines, final29. Baseline SHA256 `85ad1d1739bc08281515990ca3d5b4758b5e7ee125df56bab4f2f7b5689e73c7`; final `85ad1d1739bc08281515990ca3d5b4758b5e7ee125df56bab4f2f7b5689e73c7`.

**purpose:** Terminal Tracker selection consistency and read-only control capability.

**consumers:** Vitest include tests/**/*.test.tsx and tests/**/*.test.ts; imports vitest, ../src/adapters/tracker, ../src/adapters/live

**evidence:** Source imports, test cases, assertions and fixture construction inspected; test labels: Dashboard phase contracts; rejects a terminal selection conflicting with row truth; read-only adapter exposes no control capability

**design_boundary:** Offline component/adapter evidence only. Tests retaining older composed Routes/Operations contracts do not prove current Shell usability; new Review09 cases cover current modern views.

**costs_removal:** Development-only test fixtures/mocks; no runtime storage or performance overhead. Removal loses distinct regression contracts despite milestone overlap.

## frontend/tests/tradeHistory.test.tsx — KEEP

Owner `frontend`; baseline519 lines, final519. Baseline SHA256 `440414e48cfa6e1c2c5e280ed98389d617561aa9408ef83b537aa364c8b89091`; final `440414e48cfa6e1c2c5e280ed98389d617561aa9408ef83b537aa364c8b89091`.

**purpose:** Keyset trade adapter contract, traversal history-change warning, paging races/search/filters, stable inspected record identity and invalid cursor recovery.

**consumers:** Vitest include tests/**/*.test.tsx and tests/**/*.test.ts; imports vitest, @testing-library/react, @testing-library/user-event, @tanstack/react-query, ../src/adapters/live, ../src/context, ../src/views/Routes, ../src/adapters/contracts

**evidence:** Source imports, test cases, assertions and fixture construction inspected; test labels: trade history adapter contract; requests keyset pages and maps the page contract; surfaces an invalid cursor as an explicit error code; signals session expiry; Trades page paging; pages forward and back with exact positions and keeps the search; a late response for a page no longer on screen never overwrites it; a filter change while a request is pending shows only the new filter and restarts paging; a newer trade is reported as newer, not as a history change; a history change warns even when counts match, persists, and clears only at Newest

**design_boundary:** Offline component/adapter evidence only. Tests retaining older composed Routes/Operations contracts do not prove current Shell usability; new Review09 cases cover current modern views.

**costs_removal:** Development-only test fixtures/mocks; no runtime storage or performance overhead. Removal loses distinct regression contracts despite milestone overlap.

## frontend/tests/v21.test.tsx — KEEP

Owner `frontend`; baseline116 lines, final116. Baseline SHA256 `d409986b7838bfa8c065155fd9d2d852cae6a5c90f1fb378b2051071e5e807ba`; final `d409986b7838bfa8c065155fd9d2d852cae6a5c90f1fb378b2051071e5e807ba`.

**purpose:** Modification age versus event freshness, partial protection and offset/lookahead contract.

**consumers:** Vitest include tests/**/*.test.tsx and tests/**/*.test.ts; imports vitest, ../src/adapters/live, ../src/components/HealthStatus, ../src/time, ../src/adapters/contracts

**evidence:** Source imports, test cases, assertions and fixture construction inspected; test labels: V2.1 truthful presentation and contracts; does not equate missing telemetry with unavailable runtime; file modification is age, never a freshness contract; binds offset and lookahead without including lookahead row

**design_boundary:** Offline component/adapter evidence only. Tests retaining older composed Routes/Operations contracts do not prove current Shell usability; new Review09 cases cover current modern views.

**costs_removal:** Development-only test fixtures/mocks; no runtime storage or performance overhead. Removal loses distinct regression contracts despite milestone overlap.

## frontend/tests/workspace.test.tsx — KEEP

Owner `frontend`; baseline71 lines, final71. Baseline SHA256 `0233d48d8368c41f9f87dac20750047c41d18fe870dbf341b45e3c2df3ca0396`; final `0233d48d8368c41f9f87dac20750047c41d18fe870dbf341b45e3c2df3ca0396`.

**purpose:** Missing versus zero and UTC timestamps, keyboard record drawer, malformed GraphQL/investigation rejection.

**consumers:** Vitest include tests/**/*.test.tsx and tests/**/*.test.ts; imports vitest, @testing-library/react, @testing-library/user-event, ../src/components/workspace, ../src/adapters/live, vitest

**evidence:** Source imports, test cases, assertions and fixture construction inspected; test labels: Owner V2 record workspaces; keeps missing values distinct from numeric zero and invalid source times; filters records and exposes original fields through a keyboard accessible drawer; rejects GraphQL partial/error results instead of treating them as empty decisions; rejects malformed investigation contracts

**design_boundary:** Offline component/adapter evidence only. Tests retaining older composed Routes/Operations contracts do not prove current Shell usability; new Review09 cases cover current modern views.

**costs_removal:** Development-only test fixtures/mocks; no runtime storage or performance overhead. Removal loses distinct regression contracts despite milestone overlap.

## frontend/tsconfig.json — KEEP

Owner `frontend`; baseline21 lines, final21. Baseline SHA256 `1c9cd3bd14e83b63deef3e437b7f0da270f98b8a7d95a7ba5bb7a6f963038f97`; final `1c9cd3bd14e83b63deef3e437b7f0da270f98b8a7d95a7ba5bb7a6f963038f97`.

**purpose:** Enable strict no-emit TypeScript checking over source/tests/config with browser and test types.

**consumers:** npm/Vite/browser build command or developer/operator documentation

**evidence:** frontend/tsconfig.json:1; SDD.md:1768 (operator evidence), SDD.md:1795 (technology), SDD.md:1822 (routes), SDD.md:2367 (typed owner interface)

**design_boundary:** Presentation/read contract only; no direct venue order, provider credential or production-store write authority. Missing implementation connectivity alone is not a defect.

**costs_removal:** 463 bytes; 21 lines. No new dependency or storage model. Removal would break consumers or intentionally retained historical/test contract; no file merge/removal justified.

## frontend/vite.config.ts — KEEP

Owner `frontend`; baseline20 lines, final20. Baseline SHA256 `6e0acab705d450b7fd60270f8e1935da6a1d5d2e6041ffc9c7d1e8691fd3aec1`; final `6e0acab705d450b7fd60270f8e1935da6a1d5d2e6041ffc9c7d1e8691fd3aec1`.

**purpose:** Select live/demo/harness build inputs, local React transform, jsdom focused tests and production sourcemap policy.

**consumers:** npm/Vite/browser build command or developer/operator documentation

**evidence:** frontend/vite.config.ts:1; SDD.md:1768 (operator evidence), SDD.md:1795 (technology), SDD.md:1822 (routes), SDD.md:2367 (typed owner interface)

**design_boundary:** Presentation/read contract only; no direct venue order, provider credential or production-store write authority. Missing implementation connectivity alone is not a defect.

**costs_removal:** 676 bytes; 20 lines. No new dependency or storage model. Removal would break consumers or intentionally retained historical/test contract; no file merge/removal justified.

## scripts/bench_owner_interface.py — KEEP

Owner `owner`; baseline140 lines, final140. Baseline SHA256 `3228b41aaaa8332b9fa65fb41aed6fd9808d7cee23ce4eae97f2a4157cd7adf1`; final `3228b41aaaa8332b9fa65fb41aed6fd9808d7cee23ce4eae97f2a4157cd7adf1`.

**purpose:** Manual synthetic IPC latency, idle CPU and read contention benchmark.

**consumers:** Manual CLI; creates a disposable Journal/socket and exercises OwnerService.

**evidence:** Not executed: correctness tests cover the IPC contract; timing assertions would add machine noise. 500 status/200 controls/replays and matrix workload are bounded. Retain manual perf probe.

**design_boundary:** SDD25/26/35.14: owner reads prove records; typed commands remain Kernel-authorized. Intentional transport/future boundaries are not defects.

**costs_removal:** Not executed: correctness tests cover the IPC contract; timing assertions would add machine noise. 500 status/200 controls/replays and matrix workload are bounded. Retain manual perf probe.

## scripts/bench_trade_history.py — KEEP

Owner `dashboard`; baseline184 lines, final184. Baseline SHA256 `4c6b5768719654cef0ecb317b19a403bb67d99c2128560f4277215d3400ef303`; final `4c6b5768719654cef0ecb317b19a403bb67d99c2128560f4277215d3400ef303`.

**purpose:** Temporary-journal trade-history page/count/membership latency, memory, response-size and index-plan measurement.

**consumers:** Explicit offline benchmark invocation; trade-history paging report/provenance.

**evidence:** Builds10k/100k synthetic records; query_breakdown profiles40 repeats and10 membership scans. Large full benchmark intentionally not run for bounded remediation.

**design_boundary:** Performance evidence only; no production journal/provider use.

**costs_removal:** No service schedule/storage. Synthetic benchmarking can be expensive, especially deep whole-history traversal; preserved as reproducibility tool.

## tests/owner_frontend_server.py — KEEP

Owner `dashboard`; baseline106 lines, final106. Baseline SHA256 `fac6d8657ed2239f61da1c0bd28cded5af4e5b87360eceb5c3e6e75e703d834a`; final `fac6d8657ed2239f61da1c0bd28cded5af4e5b87360eceb5c3e6e75e703d834a`.

**purpose:** Standalone browser fixture server using temporary ROOT, fake gateway/chat and /__test__ scenario controls.

**consumers:** frontend Playwright browser webServer contract; explicit python -m tests.owner_frontend_server only.

**evidence:** Standalone browser fixture server using temporary ROOT, fake gateway/chat and /__test__ scenario controls. All function bodies/assertions inspected; no assertion-only rewrites or current production runs.

**design_boundary:** Test-only fakes/temp journals establish narrow offline contract, never runtime/provider/live evidence.

**costs_removal:** No normal runtime CPU/storage. Keep meaningful negative controls and schema compatibility; remove only when underlying public contract is retired.

## tests/test_acc03_economics.py — KEEP

Owner `dashboard`; baseline102 lines, final102. Baseline SHA256 `4e9f22cb6ab0ff3ea8628e6e6505d5756149be8572ca4e9f348eaf44e77225fe`; final `4e9f22cb6ab0ff3ea8628e6e6505d5756149be8572ca4e9f348eaf44e77225fe`.

**purpose:** Separates recorded venue fields, unavailable funding/net total, unknown booking and derived excursion coverage/version; tamper/nonfinite cases.

**consumers:** Explicit focused pytest discovery and supporting fixture imports.

**evidence:** Separates recorded venue fields, unavailable funding/net total, unknown booking and derived excursion coverage/version; tamper/nonfinite cases. All function bodies/assertions inspected; no assertion-only rewrites or current production runs.

**design_boundary:** Test-only fakes/temp journals establish narrow offline contract, never runtime/provider/live evidence.

**costs_removal:** No normal runtime CPU/storage. Keep meaningful negative controls and schema compatibility; remove only when underlying public contract is retired.

## tests/test_accounting_truth_followup.py — KEEP

Owner `dashboard`; baseline52 lines, final70. Baseline SHA256 `01c4a7e1eb15db77fc89a761cfe8e2206a011e8095d69a5ccb126ecf47f68178`; final `ad51d532a6bdc3743d005c659931c6e418595dbf6814b1b4b4d7bcd1714de9e4`.

**purpose:** Unknown/estimated receipt markers override scalar, tampered early leg persists beyond50 later legs; new Operations result and evidence classification regressions.

**consumers:** Explicit focused pytest discovery and supporting fixture imports.

**evidence:** Unknown/estimated receipt markers override scalar, tampered early leg persists beyond50 later legs; new Operations result and evidence classification regressions. All function bodies/assertions inspected; no assertion-only rewrites or current production runs.

**design_boundary:** Test-only fakes/temp journals establish narrow offline contract, never runtime/provider/live evidence.

**costs_removal:** No normal runtime CPU/storage. Keep meaningful negative controls and schema compatibility; remove only when underlying public contract is retired.

## tests/test_attention_view.py — KEEP

Owner `dashboard`; baseline80 lines, final80. Baseline SHA256 `0fd34dcde2bd8fa3f2e6dcf163bde0c8a6a160293ae29dc13c663dc6997c939c`; final `0fd34dcde2bd8fa3f2e6dcf163bde0c8a6a160293ae29dc13c663dc6997c939c`.

**purpose:** Authenticated local attention/investigation status, disabled/config/error/learning/admission/recovery evidence and no provider lookup.

**consumers:** Explicit focused pytest discovery and supporting fixture imports.

**evidence:** Authenticated local attention/investigation status, disabled/config/error/learning/admission/recovery evidence and no provider lookup. All function bodies/assertions inspected; no assertion-only rewrites or current production runs.

**design_boundary:** Test-only fakes/temp journals establish narrow offline contract, never runtime/provider/live evidence.

**costs_removal:** No normal runtime CPU/storage. Keep meaningful negative controls and schema compatibility; remove only when underlying public contract is retired.

## tests/test_candle_cache_reuse.py — KEEP

Owner `dashboard`; baseline49 lines, final49. Baseline SHA256 `297648d221de9b95db8867530de1f084b2e0c06060daf362fac98899ee7c8e00`; final `297648d221de9b95db8867530de1f084b2e0c06060daf362fac98899ee7c8e00`.

**purpose:** Preserves production candle store refresh tail and young-listing reuse using fake exchanges; source contract charts use production public feed.

**consumers:** Explicit focused pytest discovery and supporting fixture imports.

**evidence:** Preserves production candle store refresh tail and young-listing reuse using fake exchanges; source contract charts use production public feed. All function bodies/assertions inspected; no assertion-only rewrites or current production runs.

**design_boundary:** Test-only fakes/temp journals establish narrow offline contract, never runtime/provider/live evidence.

**costs_removal:** No normal runtime CPU/storage. Keep meaningful negative controls and schema compatibility; remove only when underlying public contract is retired.

## tests/test_company.py — KEEP

Owner `dashboard`; baseline125 lines, final125. Baseline SHA256 `bb3191903ccf048c3015838002e8b1e5d435026494e2fede0491d0fb43bd62e5`; final `bb3191903ccf048c3015838002e8b1e5d435026494e2fede0491d0fb43bd62e5`.

**purpose:** Stub-journal roster/card shape, report-to/category, accuracy, sparkline and manager fields. Narrow presentation contract, not real liveness proof.

**consumers:** Explicit focused pytest discovery and supporting fixture imports.

**evidence:** Stub-journal roster/card shape, report-to/category, accuracy, sparkline and manager fields. Narrow presentation contract, not real liveness proof. All function bodies/assertions inspected; no assertion-only rewrites or current production runs.

**design_boundary:** Test-only fakes/temp journals establish narrow offline contract, never runtime/provider/live evidence.

**costs_removal:** No normal runtime CPU/storage. Keep meaningful negative controls and schema compatibility; remove only when underlying public contract is retired.

## tests/test_current_truth_completion_clock.py — KEEP

Owner `dashboard`; baseline250 lines, final250. Baseline SHA256 `f0dc06d7c76966504000917fea320a2e9349961bc3910fb8fc6a1e7855cbbbc2`; final `f0dc06d7c76966504000917fea320a2e9349961bc3910fb8fc6a1e7855cbbbc2`.

**purpose:** Preserves actual completion time; before-attempt/after-record/missing/malformed never current FRESH or Risk PASS; producer-reader-consumer behavior.

**consumers:** Explicit focused pytest discovery and supporting fixture imports.

**evidence:** Preserves actual completion time; before-attempt/after-record/missing/malformed never current FRESH or Risk PASS; producer-reader-consumer behavior. All function bodies/assertions inspected; no assertion-only rewrites or current production runs.

**design_boundary:** Test-only fakes/temp journals establish narrow offline contract, never runtime/provider/live evidence.

**costs_removal:** No normal runtime CPU/storage. Keep meaningful negative controls and schema compatibility; remove only when underlying public contract is retired.

## tests/test_current_truth_contract.py — KEEP

Owner `dashboard`; baseline1095 lines, final1095. Baseline SHA256 `505a92a13c771dae0a126c29659c217500d888f01fad0d8989dcdc4fa0313a68`; final `505a92a13c771dae0a126c29659c217500d888f01fad0d8989dcdc4fa0313a68`.

**purpose:** Adversarial freshness/account/valuation/risk/news/latest-index tests; fake kernel objects and temp journals, no boot/providers. Includes250k HOLD rows to prove exact latest beyond lookback.

**consumers:** Explicit focused pytest discovery and supporting fixture imports.

**evidence:** Adversarial freshness/account/valuation/risk/news/latest-index tests; fake kernel objects and temp journals, no boot/providers. Includes250k HOLD rows to prove exact latest beyond lookback. All function bodies/assertions inspected; no assertion-only rewrites or current production runs.

**design_boundary:** Test-only fakes/temp journals establish narrow offline contract, never runtime/provider/live evidence.

**costs_removal:** No normal runtime CPU/storage. Keep meaningful negative controls and schema compatibility; remove only when underlying public contract is retired.

## tests/test_current_truth_corrections.py — KEEP

Owner `dashboard`; baseline721 lines, final721. Baseline SHA256 `e08b80465474132c0f38e8c819c697b0e0fac6ea9c145387e3e20c200978c113`; final `e08b80465474132c0f38e8c819c697b0e0fac6ea9c145387e3e20c200978c113`.

**purpose:** Adversarial unknown/future clocks, news damping exactly once, risk reproducibility/policy identity, fallback provenance, duplicate-symbol valuation and cockpit state.

**consumers:** Explicit focused pytest discovery and supporting fixture imports.

**evidence:** Adversarial unknown/future clocks, news damping exactly once, risk reproducibility/policy identity, fallback provenance, duplicate-symbol valuation and cockpit state. All function bodies/assertions inspected; no assertion-only rewrites or current production runs.

**design_boundary:** Test-only fakes/temp journals establish narrow offline contract, never runtime/provider/live evidence.

**costs_removal:** No normal runtime CPU/storage. Keep meaningful negative controls and schema compatibility; remove only when underlying public contract is retired.

## tests/test_current_truth_integration.py — KEEP

Owner `dashboard`; baseline353 lines, final353. Baseline SHA256 `8a107c0089bdcb20eda65279786875eedc81caac66fbd723e3ba3fa8b5501216`; final `8a107c0089bdcb20eda65279786875eedc81caac66fbd723e3ba3fa8b5501216`.

**purpose:** Legacy summary/WS/company and pre-migration read-only journal integration; null/partial valuation and old index refusal preserve database bytes.

**consumers:** Explicit focused pytest discovery and supporting fixture imports.

**evidence:** Legacy summary/WS/company and pre-migration read-only journal integration; null/partial valuation and old index refusal preserve database bytes. All function bodies/assertions inspected; no assertion-only rewrites or current production runs.

**design_boundary:** Test-only fakes/temp journals establish narrow offline contract, never runtime/provider/live evidence.

**costs_removal:** No normal runtime CPU/storage. Keep meaningful negative controls and schema compatibility; remove only when underlying public contract is retired.

## tests/test_dashboard_auth.py — KEEP

Owner `dashboard`; baseline51 lines, final71. Baseline SHA256 `582e3d7aa458faaf6e81bb6daffddf311fca4e21a13e36739d8ad193bbe3e2c1`; final `15c52bf801d2074637814b2b6922447fae9c5de7344bd50d58733af1d4e5ec6f`.

**purpose:** Default token fail closed, browser origin/session/logout/WebSocket and expiry/rotation; new nested-vault allowed/sibling+symlink+directory denied.

**consumers:** Explicit focused pytest discovery and supporting fixture imports.

**evidence:** Default token fail closed, browser origin/session/logout/WebSocket and expiry/rotation; new nested-vault allowed/sibling+symlink+directory denied. All function bodies/assertions inspected; no assertion-only rewrites or current production runs.

**design_boundary:** Test-only fakes/temp journals establish narrow offline contract, never runtime/provider/live evidence.

**costs_removal:** No normal runtime CPU/storage. Keep meaningful negative controls and schema compatibility; remove only when underlying public contract is retired.

## tests/test_dashboard_phase.py — KEEP

Owner `dashboard`; baseline117 lines, final117. Baseline SHA256 `9dd25202b5355d520e9723e5b51e0bf3dcd1172723d7c1314e4fcf3d7d1fefd7`; final `9dd25202b5355d520e9723e5b51e0bf3dcd1172723d7c1314e4fcf3d7d1fefd7`.

**purpose:** Explicit mode, GUI startup with no kernel, LIVE missing-identity refusal, GraphQL query vs mutation and provider/control/WS guards; synthetic tracker fixture.

**consumers:** Explicit focused pytest discovery and supporting fixture imports.

**evidence:** Explicit mode, GUI startup with no kernel, LIVE missing-identity refusal, GraphQL query vs mutation and provider/control/WS guards; synthetic tracker fixture. All function bodies/assertions inspected; no assertion-only rewrites or current production runs.

**design_boundary:** Test-only fakes/temp journals establish narrow offline contract, never runtime/provider/live evidence.

**costs_removal:** No normal runtime CPU/storage. Keep meaningful negative controls and schema compatibility; remove only when underlying public contract is retired.

## tests/test_dashboard_supervisor_unknown.py — KEEP

Owner `dashboard`; baseline33 lines, final33. Baseline SHA256 `30e571638c501d3b567ac60952091c8ced0eb7d359fdc211da2e92b9ff9f5163`; final `30e571638c501d3b567ac60952091c8ced0eb7d359fdc211da2e92b9ff9f5163`.

**purpose:** Missing checks remain None, explicit match/mismatch preserved, old pass only last_reported.

**consumers:** Explicit focused pytest discovery and supporting fixture imports.

**evidence:** Missing checks remain None, explicit match/mismatch preserved, old pass only last_reported. All function bodies/assertions inspected; no assertion-only rewrites or current production runs.

**design_boundary:** Test-only fakes/temp journals establish narrow offline contract, never runtime/provider/live evidence.

**costs_removal:** No normal runtime CPU/storage. Keep meaningful negative controls and schema compatibility; remove only when underlying public contract is retired.

## tests/test_data02_decision_lineage.py — KEEP

Owner `dashboard`; baseline167 lines, final167. Baseline SHA256 `fc0b3b863e80723197f35550366a2714082c43f164a096eec0c4d79ad4f44935`; final `fc0b3b863e80723197f35550366a2714082c43f164a096eec0c4d79ad4f44935`.

**purpose:** Reopen exact persisted market receipts and reject unavailable/tampered/schema/quality/future/instrument lineage, retain attention admission identity.

**consumers:** Explicit focused pytest discovery and supporting fixture imports.

**evidence:** Reopen exact persisted market receipts and reject unavailable/tampered/schema/quality/future/instrument lineage, retain attention admission identity. All function bodies/assertions inspected; no assertion-only rewrites or current production runs.

**design_boundary:** Test-only fakes/temp journals establish narrow offline contract, never runtime/provider/live evidence.

**costs_removal:** No normal runtime CPU/storage. Keep meaningful negative controls and schema compatibility; remove only when underlying public contract is retired.

## tests/test_glacier_truth.py — KEEP

Owner `dashboard`; baseline105 lines, final105. Baseline SHA256 `1a40a2bdca5c449fd92a68491cb3b4c1fb2905771b340c3025b7a59b393018ae`; final `1a40a2bdca5c449fd92a68491cb3b4c1fb2905771b340c3025b7a59b393018ae`.

**purpose:** Verified environment expires, provider disabled/unknown honest, exact version authority read with query_only, mounts and offline browser fixture serialization.

**consumers:** Explicit focused pytest discovery and supporting fixture imports.

**evidence:** Verified environment expires, provider disabled/unknown honest, exact version authority read with query_only, mounts and offline browser fixture serialization. All function bodies/assertions inspected; no assertion-only rewrites or current production runs.

**design_boundary:** Test-only fakes/temp journals establish narrow offline contract, never runtime/provider/live evidence.

**costs_removal:** No normal runtime CPU/storage. Keep meaningful negative controls and schema compatibility; remove only when underlying public contract is retired.

## tests/test_gui01_overview.py — KEEP

Owner `dashboard`; baseline48 lines, final48. Baseline SHA256 `9aa8c0528e580b726c03818f048ce8b411e66fd6274f51c9fe4f40191f263717`; final `9aa8c0528e580b726c03818f048ce8b411e66fd6274f51c9fe4f40191f263717`.

**purpose:** Partial journal realized P&L unknown, empty-day real zero, stopped kernel overview readable while chat/providers denied.

**consumers:** Explicit focused pytest discovery and supporting fixture imports.

**evidence:** Partial journal realized P&L unknown, empty-day real zero, stopped kernel overview readable while chat/providers denied. All function bodies/assertions inspected; no assertion-only rewrites or current production runs.

**design_boundary:** Test-only fakes/temp journals establish narrow offline contract, never runtime/provider/live evidence.

**costs_removal:** No normal runtime CPU/storage. Keep meaningful negative controls and schema compatibility; remove only when underlying public contract is retired.

## tests/test_manual_close.py — KEEP

Owner `owner`; baseline194 lines, final194. Baseline SHA256 `8e4fbfae241ba39d2cc1d0c2ee43069a89b1121b7b7653d5b6522d6d3c07875b`; final `8e4fbfae241ba39d2cc1d0c2ee43069a89b1121b7b7653d5b6522d6d3c07875b`.

**purpose:** Single-position typed intent queue and Kernel-drain regressions.

**consumers:** Focused pytest; real Journal synthetic Position/Executor.

**evidence:** Checks duplicate queue, closed/unknown trades, rotated-out symbols, no blind retry, corruption and notice. Does not call venue. Complements recovery tests rather than duplicating them.

**design_boundary:** SDD25/26/35.14: owner reads prove records; typed commands remain Kernel-authorized. Intentional transport/future boundaries are not defects.

**costs_removal:** Checks duplicate queue, closed/unknown trades, rotated-out symbols, no blind retry, corruption and notice. Does not call venue. Complements recovery tests rather than duplicating them.

## tests/test_overview_desk.py — KEEP

Owner `dashboard`; baseline109 lines, final126. Baseline SHA256 `5cea539c61673d264c5614873db20f3df65269963726a92d5a222570db6b863f`; final `6c7c94fb6efe7553c66a9ec54c060b53e71f8c08d61fb568db58f11eb5fe3077`.

**purpose:** Curve omits unknown, scan newest by time, research missing vs empty, strategy known-value stats, Operations aggregation/HTTP; added composed GraphQL decision count.

**consumers:** Explicit focused pytest discovery and supporting fixture imports.

**evidence:** Curve omits unknown, scan newest by time, research missing vs empty, strategy known-value stats, Operations aggregation/HTTP; added composed GraphQL decision count. All function bodies/assertions inspected; no assertion-only rewrites or current production runs.

**design_boundary:** Test-only fakes/temp journals establish narrow offline contract, never runtime/provider/live evidence.

**costs_removal:** No normal runtime CPU/storage. Keep meaningful negative controls and schema compatibility; remove only when underlying public contract is retired.

## tests/test_own03_approval_boundary.py — KEEP

Owner `owner`; baseline71 lines, final85. Baseline SHA256 `fd8681750ff0bb3190fa07f3c294103e67e22fffcbae826b7f35df13d6557cd6`; final `8f558cb8a20c5c0d1fbd1f391388b25618c525524dd2b5d5d2ee0eec73805a10`.

**purpose:** Routine questions/decisions cannot generate blanket approval loops.

**consumers:** Focused pytest; Stage8/Factory synthetic fixture imports.

**evidence:** Checks ledger change counts, exact first-live missing approval, expiry and immutable rejection. SDD25.4 distinction is intentional and must stay. Added read-only synthetic supervisor projection regression for actual recovery control destination; red before /green after.

**design_boundary:** SDD25/26/35.14: owner reads prove records; typed commands remain Kernel-authorized. Intentional transport/future boundaries are not defects.

**costs_removal:** Checks ledger change counts, exact first-live missing approval, expiry and immutable rejection. SDD25.4 distinction is intentional and must stay.

## tests/test_own04_state_truth.py — KEEP

Owner `dashboard`; baseline129 lines, final129. Baseline SHA256 `c8ee9ad8a81147f30d6acd057326e3f60049483a308d9a8e8a8b48ae6570434d`; final `c8ee9ad8a81147f30d6acd057326e3f60049483a308d9a8e8a8b48ae6570434d`.

**purpose:** Existing real flock/process identity vs ACTIVE control, heartbeat clock/work, collector timestamp and no-write/no-created-artifact tests.

**consumers:** Explicit focused pytest discovery and supporting fixture imports.

**evidence:** Existing real flock/process identity vs ACTIVE control, heartbeat clock/work, collector timestamp and no-write/no-created-artifact tests. All function bodies/assertions inspected; no assertion-only rewrites or current production runs.

**design_boundary:** Test-only fakes/temp journals establish narrow offline contract, never runtime/provider/live evidence.

**costs_removal:** No normal runtime CPU/storage. Keep meaningful negative controls and schema compatibility; remove only when underlying public contract is retired.

## tests/test_owner_interface.py — KEEP

Owner `owner`; baseline1048 lines, final1048. Baseline SHA256 `4c9d4bbcfdeecfec40201e15c0941573386912f0d66a3319c581d6abc19c397f`; final `4c9d4bbcfdeecfec40201e15c0941573386912f0d66a3319c581d6abc19c397f`.

**purpose:** End-to-end typed owner gateway matrix including MACs, auth, replay, GraphQL and simulated recovery.

**consumers:** pytest; helper imports by R3-R6 and boundary tests.

**evidence:** Real local IPC and temporary stores, recording venue doubles; requests.post patched in Telegram cases. Removing helper-heavy matrix breaks later regression imports. Signed channel and no-provider proof do not establish deployed service health.

**design_boundary:** SDD25/26/35.14: owner reads prove records; typed commands remain Kernel-authorized. Intentional transport/future boundaries are not defects.

**costs_removal:** Real local IPC and temporary stores, recording venue doubles; requests.post patched in Telegram cases. Removing helper-heavy matrix breaks later regression imports. Signed channel and no-provider proof do not establish deployed service health.

## tests/test_owner_interface_boundary.py — KEEP

Owner `owner`; baseline488 lines, final488. Baseline SHA256 `d181662387e9d2f6e7c9598e8eed06f880299ff3c5f06b717dd436b2a9d5ab77`; final `d181662387e9d2f6e7c9598e8eed06f880299ff3c5f06b717dd436b2a9d5ab77`.

**purpose:** Cross-process authority/chat/secrets boundaries and negative controls.

**consumers:** pytest; AST/import/record audits and mutated fixture invocations.

**evidence:** Checks absence of executor imports, immutable owner routing and untrusted free text. Mutants test observable safety violations rather than adding production verification machinery. Keep despite test-only duplication.

**design_boundary:** SDD25/26/35.14: owner reads prove records; typed commands remain Kernel-authorized. Intentional transport/future boundaries are not defects.

**costs_removal:** Checks absence of executor imports, immutable owner routing and untrusted free text. Mutants test observable safety violations rather than adding production verification machinery. Keep despite test-only duplication.

## tests/test_owner_interface_r3.py — KEEP

Owner `owner`; baseline398 lines, final398. Baseline SHA256 `f0ad58e1e0fb740437bb070bd052ab22ca596819e10866d58f0fc7ec30c26874`; final `f0ad58e1e0fb740437bb070bd052ab22ca596819e10866d58f0fc7ec30c26874`.

**purpose:** Adversarial ordering, panic draining and rolled-back audit buffer regressions.

**consumers:** pytest; R4/R5 import gated concurrency helpers.

**evidence:** Event/Thread recording fixtures isolate stale recovery, blocked replies and lost audit writes. Retired inline-JS cases explicitly migrated to React tests, not dead production code.

**design_boundary:** SDD25/26/35.14: owner reads prove records; typed commands remain Kernel-authorized. Intentional transport/future boundaries are not defects.

**costs_removal:** Event/Thread recording fixtures isolate stale recovery, blocked replies and lost audit writes. Retired inline-JS cases explicitly migrated to React tests, not dead production code.

## tests/test_owner_interface_r4.py — KEEP

Owner `owner`; baseline335 lines, final335. Baseline SHA256 `d5ae1921f517ab7e6d82868684f84dcae79fea5181d6502d7b9d3275f76ac225`; final `d5ae1921f517ab7e6d82868684f84dcae79fea5181d6502d7b9d3275f76ac225`.

**purpose:** Admission watermark, replay-aware refusal and truthful overflow regressions.

**consumers:** pytest; R3 helper imports and real temporary IPC.

**evidence:** New owner halt supersedes admitted old unhalt; duplicate admission consults durable record, in-flight rows never counted as loss. Negative controls remain useful distinct interleavings.

**design_boundary:** SDD25/26/35.14: owner reads prove records; typed commands remain Kernel-authorized. Intentional transport/future boundaries are not defects.

**costs_removal:** New owner halt supersedes admitted old unhalt; duplicate admission consults durable record, in-flight rows never counted as loss. Negative controls remain useful distinct interleavings.

## tests/test_owner_interface_r5.py — KEEP

Owner `owner`; baseline587 lines, final587. Baseline SHA256 `b7d971a386a64b769b58b45dcc5a2fb5ef5634ca10c4cbe36263a266d4f5fcb1`; final `b7d971a386a64b769b58b45dcc5a2fb5ef5634ca10c4cbe36263a266d4f5fcb1`.

**purpose:** Panic acceptance fence, preclaim reservation and exact audit-cap regressions.

**consumers:** pytest; R6 imports _svc; Kernel fixture callbacks.

**evidence:** Tests5 concrete panic races, restart RESERVED/PENDING semantics, IPC/Telegram duplicates and cap under failed concurrent flush. Apparent repetitions enforce different crash windows; merging unproven saves no owner complexity.

**design_boundary:** SDD25/26/35.14: owner reads prove records; typed commands remain Kernel-authorized. Intentional transport/future boundaries are not defects.

**costs_removal:** Tests5 concrete panic races, restart RESERVED/PENDING semantics, IPC/Telegram duplicates and cap under failed concurrent flush. Apparent repetitions enforce different crash windows; merging unproven saves no owner complexity.

## tests/test_owner_interface_r6.py — KEEP

Owner `owner`; baseline227 lines, final227. Baseline SHA256 `8ac7966ab183c1998516df7702d5be85c2187eb6fcf02b45db141fb05591a521`; final `8ac7966ab183c1998516df7702d5be85c2187eb6fcf02b45db141fb05591a521`.

**purpose:** Failed reservation reconciliation cannot return a false terminal refusal.

**consumers:** pytest; R5 _svc, original matrix and Telegram render helpers.

**evidence:** Event-gated absence/reserved/claimed/completed/refused race matrix and signed IPC prove same-ID retry semantics. Unknown rendering never says nothing changed; failure/no-provider scope explicit.

**design_boundary:** SDD25/26/35.14: owner reads prove records; typed commands remain Kernel-authorized. Intentional transport/future boundaries are not defects.

**costs_removal:** Event-gated absence/reserved/claimed/completed/refused race matrix and signed IPC prove same-ID retry semantics. Unknown rendering never says nothing changed; failure/no-provider scope explicit.

## tests/test_owner_read_cache.py — KEEP

Owner `dashboard`; baseline101 lines, final101. Baseline SHA256 `917caec64d9553e0061116472205b9fc9e1abd10da5c68107947719eaaa4c1b8`; final `917caec64d9553e0061116472205b9fc9e1abd10da5c68107947719eaaa4c1b8`.

**purpose:** Fingerprint invalidation even same mtime/size, source moving/unreadable refuses cache, bounds/concurrent serial build, each protection verification check required.

**consumers:** Explicit focused pytest discovery and supporting fixture imports.

**evidence:** Fingerprint invalidation even same mtime/size, source moving/unreadable refuses cache, bounds/concurrent serial build, each protection verification check required. All function bodies/assertions inspected; no assertion-only rewrites or current production runs.

**design_boundary:** Test-only fakes/temp journals establish narrow offline contract, never runtime/provider/live evidence.

**costs_removal:** No normal runtime CPU/storage. Keep meaningful negative controls and schema compatibility; remove only when underlying public contract is retired.

## tests/test_owner_reads_m2.py — KEEP

Owner `dashboard`; baseline546 lines, final546. Baseline SHA256 `b126d11b47e10c1cec96523c24976233df79f945cb9acfcfc9157426a5693b42`; final `b126d11b47e10c1cec96523c24976233df79f945cb9acfcfc9157426a5693b42`.

**purpose:** Forensic exact id joins, paging, source omissions, per-strategy provenance, observed flows, knowledge/chat lookup caps and worker-thread request responsiveness.

**consumers:** Explicit focused pytest discovery and supporting fixture imports.

**evidence:** Forensic exact id joins, paging, source omissions, per-strategy provenance, observed flows, knowledge/chat lookup caps and worker-thread request responsiveness. All function bodies/assertions inspected; no assertion-only rewrites or current production runs.

**design_boundary:** Test-only fakes/temp journals establish narrow offline contract, never runtime/provider/live evidence.

**costs_removal:** No normal runtime CPU/storage. Keep meaningful negative controls and schema compatibility; remove only when underlying public contract is retired.

## tests/test_rent_wiring.py — KEEP

Owner `dashboard`; baseline51 lines, final51. Baseline SHA256 `1cc70060c70ce8ab60d3059167f82b5a64464c405c88083348e9df6bb497ab29`; final `1cc70060c70ce8ab60d3059167f82b5a64464c405c88083348e9df6bb497ab29`.

**purpose:** Recorded rent state/history reaches GraphQL and Manager, config/kernel wiring assertions; no profitability or readiness claim.

**consumers:** Explicit focused pytest discovery and supporting fixture imports.

**evidence:** Recorded rent state/history reaches GraphQL and Manager, config/kernel wiring assertions; no profitability or readiness claim. All function bodies/assertions inspected; no assertion-only rewrites or current production runs.

**design_boundary:** Test-only fakes/temp journals establish narrow offline contract, never runtime/provider/live evidence.

**costs_removal:** No normal runtime CPU/storage. Keep meaningful negative controls and schema compatibility; remove only when underlying public contract is retired.

## tests/test_run01_instance_identity.py — KEEP

Owner `dashboard`; baseline314 lines, final314. Baseline SHA256 `4e4b7225644e115e46fca8c4c3fd2e6babc91948a88301b70b7f360c808e8aea`; final `4e4b7225644e115e46fca8c4c3fd2e6babc91948a88301b70b7f360c808e8aea`.

**purpose:** Synthetic child lock holders, stale identity/revision refusal and stop timeout; restart script stub sandbox, plus owner identity projection. Retain but do not execute service/launcher subset in this review.

**consumers:** Explicit focused pytest discovery and supporting fixture imports.

**evidence:** Synthetic child lock holders, stale identity/revision refusal and stop timeout; restart script stub sandbox, plus owner identity projection. Retain but do not execute service/launcher subset in this review. All function bodies/assertions inspected; no assertion-only rewrites or current production runs.

**design_boundary:** Test-only fakes/temp journals establish narrow offline contract, never runtime/provider/live evidence.

**costs_removal:** No normal runtime CPU/storage. Keep meaningful negative controls and schema compatibility; remove only when underlying public contract is retired.

## tests/test_theorist_removed.py — KEEP

Owner `dashboard`; baseline38 lines, final38. Baseline SHA256 `4706f4cb5ef3d6f5dd776951261018417a27849fdcaeee712e61c22ee1f798be`; final `4706f4cb5ef3d6f5dd776951261018417a27849fdcaeee712e61c22ee1f798be`.

**purpose:** Preserves removal of obsolete LLM theorist/rules while data-only postmortem and roster/display remain wired.

**consumers:** Explicit focused pytest discovery and supporting fixture imports.

**evidence:** Preserves removal of obsolete LLM theorist/rules while data-only postmortem and roster/display remain wired. All function bodies/assertions inspected; no assertion-only rewrites or current production runs.

**design_boundary:** Test-only fakes/temp journals establish narrow offline contract, never runtime/provider/live evidence.

**costs_removal:** No normal runtime CPU/storage. Keep meaningful negative controls and schema compatibility; remove only when underlying public contract is retired.

## tests/test_trade_history.py — KEEP

Owner `dashboard`; baseline492 lines, final492. Baseline SHA256 `717b3432b176ef3b68a6f24cb7175d3cf5e1cc44f06642976cd072dd08c3a448`; final `717b3432b176ef3b68a6f24cb7175d3cf5e1cc44f06642976cd072dd08c3a448`.

**purpose:** Mixed237-trade pages, strict malicious cursors, membership cancellation/backdated insert/revert, read-only SQL and covering-index plans.

**consumers:** Explicit focused pytest discovery and supporting fixture imports.

**evidence:** Mixed237-trade pages, strict malicious cursors, membership cancellation/backdated insert/revert, read-only SQL and covering-index plans. All function bodies/assertions inspected; no assertion-only rewrites or current production runs.

**design_boundary:** Test-only fakes/temp journals establish narrow offline contract, never runtime/provider/live evidence.

**costs_removal:** No normal runtime CPU/storage. Keep meaningful negative controls and schema compatibility; remove only when underlying public contract is retired.

## trader/api/graphql_schema.py — KEEP

Owner `dashboard`; baseline596 lines, final596. Baseline SHA256 `ec5dba753c7b016391ad36c5d220cdacef6dde259ea2aa6e32ed2c88958022c4`; final `cd298b3733992f6b10371c07528229eeb46a0b969210c60765abb7093be73d73`.

**purpose:** Typed query/control contract with worker-thread resolvers and batched vote loader.

**consumers:** server router; frontend live control GraphQL; assistant API consumers; backend integration tests.

**evidence:** Fixed decisions_total composed filters: BTC HOLD+BUY and ETH BUY now BTC directional total1 equals returned list1, pre count2. Mutations call kernel gateway only; all queries offload sync work.

**design_boundary:** SDD25.1 allows GraphQL; legacy rich queries and typed authority distinct from REST read contracts; no new writer.

**costs_removal:** 596 lines typed schema, max1000 rows/default bounded queries. Batched votes avoids per-decision scans but still scans per500 cycles; optional index outside ownership deferred.

## trader/dashboard/__init__.py — KEEP

Owner `dashboard`; baseline0 lines, final0. Baseline SHA256 `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`; final `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`.

**purpose:** Dashboard Python package marker.

**consumers:** All trader.dashboard imports.

**evidence:** Empty file is correct package boundary; no obsolete behavior.

**design_boundary:** Intentional package marker, not disconnected component.

**costs_removal:** 0 source lines; removal risks package/import semantics for no material gain.

## trader/dashboard/auth.py — KEEP

Owner `dashboard`; baseline138 lines, final138. Baseline SHA256 `6fcb7ffeb9eabdad36359913b97cec765003c564e80c12d7a7a696b35581b7ae`; final `6fcb7ffeb9eabdad36359913b97cec765003c564e80c12d7a7a696b35581b7ae`.

**purpose:** Fail-closed token/header/session authentication with HMAC expiry and same-origin controls.

**consumers:** server.create_app installs Guard; owner_api.bootstrap consumes session identity; tests/test_dashboard_auth.py.

**evidence:** Blank/default token denied, cookie secure/HttpOnly/Strict, origin checked for writes/WebSocket; GET query token remains deliberate script compatibility.

**design_boundary:** SDD26 authenticated narrow owner interface; no trading/control writer.

**costs_removal:** Tiny auth middleware per request; sessions stateless. Removing exposes every mounted read/control route.

## trader/dashboard/current_truth.py — KEEP

Owner `dashboard`; baseline593 lines, final593. Baseline SHA256 `416d870dde445bbc4c37cb2923b51953dc0569638538d9553884cf531fc39dd9`; final `416d870dde445bbc4c37cb2923b51953dc0569638538d9553884cf531fc39dd9`.

**purpose:** Shared account, risk, news and equity provenance read models.

**consumers:** owner_api overview/risk/news; GraphQL status/equity/news; server summary/company/WS.

**evidence:** Account completion clock and source age preserved; risk verifies policy digest/constraint consistency; news count/time contradictions unavailable; stale historical values retained with explicit state.

**design_boundary:** SDD25 evidence-grounded explanation and deterministic Risk boundary; local journal reads only.

**costs_removal:** 593 source lines centralize three substantive producer schemas. Do not merge them into route code or erase missing/stale distinctions.

## trader/dashboard/economics.py — KEEP

Owner `dashboard`; baseline144 lines, final144. Baseline SHA256 `0ae5124f048b92ccd86f78dc9039112cb03eb5877054392d17015c170df1c560`; final `0ae5124f048b92ccd86f78dc9039112cb03eb5877054392d17015c170df1c560`.

**purpose:** Replay booking evidence and separate venue monetary fields, journal values and derived excursion metrics.

**consumers:** owner_reads.trade_lineage; owner_api.read_realized_today; overview_desk curve/strategies/operations.

**evidence:** journal_pnl checks every receipt uncapped; any corrupt/unknown leg suppresses scalar even if later legs numeric; funding/net economic total never fabricated.

**design_boundary:** SDD15/25 accounting evidence; per-leg fill proof never whole-trade/net authority.

**costs_removal:** Receipt history is intentionally complete; truncating can hide unknown early legs. Shared classifier now also used for Operations close rows.

## trader/dashboard/glacier_truth.py — KEEP

Owner `dashboard`; baseline121 lines, final121. Baseline SHA256 `d3344e24ddb930f8636c2c755c03519a301a4c16e6e7f6ff2b700802d3a33875`; final `d3344e24ddb930f8636c2c755c03519a301a4c16e6e7f6ff2b700802d3a33875`.

**purpose:** Verified runtime environment, exact strategy-version authority and mount capacity projections.

**consumers:** bootstrap runtime_truth; strategy_board versioned_strategies; diagnostics filesystems.

**evidence:** Verified snapshot expires at60s; config only establishes disabled or unknown provider state. Version validation/install/probation/approval/governor/economics individually report read errors. Recovery mount cannot borrow root capacity.

**design_boundary:** SDD25 immutable version view and source-specific evidence. ACTIVE registry/governor labels are not entry permission.

**costs_removal:** One complete version walk with per-version verification may grow; optional paging needs separate product/coverage design. No added storage or providers.

## trader/dashboard/overview_desk.py — KEEP

Owner `dashboard`; baseline320 lines, final322. Baseline SHA256 `638c1038b84d98b46e72d789358bbbead0e37b630c4e25e58b41eff012debe66`; final `654cb7034c82fe438810371e9bba9eb593c3d79ef1e67baa5d248a9669a3b435`.

**purpose:** Owner Overview/Research/Strategies/Operations compact read projections.

**consumers:** owner_api desk/research-board/strategies-board/operations-log; frontend Overview, ResearchDesk, Strategies, OperationsLog, Diagnostics.

**evidence:** Curve excludes unknown and counts omissions; discovery ranks do not admit; log aggregates scans/control repeats per UTC hour. Fixed close result previously used numeric placeholder despite UNKNOWN receipts.

**design_boundary:** SDD25.3 owner tabs; config referee/handoff states remain explicit disabled; no strategy/order mutation.

**costs_removal:** History curve/strategy-board each query receipts per closed trade; Operations reads1..30 days and aggregates in memory. Optional batch/paging improvement, no measured production bottleneck claimed.

## trader/dashboard/owner_api.py — KEEP

Owner `dashboard`; baseline1179 lines, final1179. Baseline SHA256 `dd99ffff1c8d3a97e2e2ab17f713cc5be6d14b13b3301af665f4e20e472ec749`; final `dd99ffff1c8d3a97e2e2ab17f713cc5be6d14b13b3301af665f4e20e472ec749`.

**purpose:** Authenticated owner gateway reads, bootstrap, cache, chat/approval transport and static Vite assets.

**consumers:** server.create_app; frontend adapters/live.ts and readContracts.ts; owner_reads install.

**evidence:** Overview isolates source failures, reports source-time freshness, complete valuation and verified protection; cache invalidates filesystem identity/change and never stale-fallback on failure. READ_ONLY_GUI disables chat/controls/provider injections.

**design_boundary:** SDD25/26 transport not authority. Approval forwarding uses existing typed kernel gateway; chat is non-operational. Explicit future lenses and unavailable provider data are intentional.

**costs_removal:** 1179 lines and many projections are broad but contracts differ; optional split has no required benefit. Knowledge retains ≤2MiB/20k-file cache and still fingerprints vault per read. Preserve provenance over TTL shortcut.

## trader/dashboard/owner_reads.py — KEEP

Owner `dashboard`; baseline1582 lines, final1582. Baseline SHA256 `9a2c11001348b208168af4532bf78f719c32954133d07c77bda812ca0465ca9c`; final `9a2c11001348b208168af4532bf78f719c32954133d07c77bda812ca0465ca9c`.

**purpose:** Exact-id trade/decision/research/strategy evidence, activity, diagnostics, knowledge and flow readers.

**consumers:** owner_api.install; frontend forensic/detail/live-system/knowledge/diagnostics routes.

**evidence:** No inferred links: bounded votes window plus exact cycle ID, exact partial-index latest queries, missing index explicitly unavailable, receipt replays bound to trade; counters distinguish display truncation from absence.

**design_boundary:** SDD25 current contract vs target: registry detail is legacy current-row scope; separate versioned strategy board provides immutable authority. Retired questions/future unavailable fields do not establish defects.

**costs_removal:** 1582 lines worker-thread handlers keep event loop responsive; bounded detail payloads/records. Historical strategy raw journal_economics distinct from exact classified board; any future unification must preserve response semantics.

## trader/dashboard/owner_runtime.py — KEEP

Owner `dashboard`; baseline30 lines, final30. Baseline SHA256 `5cd8a2f1681405603e735d22bc607bd625f64c6bc7709d764dbca74aa7d137bb`; final `5cd8a2f1681405603e735d22bc607bd625f64c6bc7709d764dbca74aa7d137bb`.

**purpose:** Read existing Kernel flock/process record/start ticks without creating artifacts.

**consumers:** owner_api heartbeat/overview/bootstrap/system; test_own04_state_truth.

**evidence:** RUNNING requires held existing lock and verified /proc ticks; unlocked/no legacy kernel yields STOPPED; absent/contradictory evidence UNKNOWN.

**design_boundary:** Stored ACTIVE trading permission, process liveness and successful work are distinct evidence contracts.

**costs_removal:** 30 lines bounded existing-file/proc read, no migration/control/process launch. Removing loses honest stopped/unknown distinction.

## trader/dashboard/phase.py — KEEP

Owner `dashboard`; baseline104 lines, final104. Baseline SHA256 `78da1f099f427b4bf157abcd9a45fe490a8f0ee23f0dac034f5b592165b15f9a`; final `78da1f099f427b4bf157abcd9a45fe490a8f0ee23f0dac034f5b592165b15f9a`.

**purpose:** Explicit READ_ONLY_GUI or LIVE startup and ASGI authority boundary.

**consumers:** server.create_app/main; owner_api.install and owner gateway; CLI startup_check.

**evidence:** GUI bypasses LIVE readiness intentionally, denies provider legacy reads/WebSocket/actions before execution, permits only parsed GraphQL queries and auth login/logout. LIVE requires readiness/preflight.

**design_boundary:** Current config.yaml432 is READ_ONLY_GUI. Future LIVE prerequisites mandatory, not missing wiring defect.

**costs_removal:** 104 lines single central boundary prevents route-by-route gates. No new approval mechanism needed.

## trader/dashboard/server.py — KEEP

Owner `dashboard`; baseline1132 lines, final1133. Baseline SHA256 `b9891a7a5d1e1a769f901c30d83ecb9cdf8c5eb4dd3c8d304ddfa26dc8a07e97`; final `47c30dcddddbc7246e72947f04d566800221ca1a0f7831d8db29fa729ec14e8e`.

**purpose:** FastAPI composition, legacy read compatibility, provider enrichment adapters and company cards.

**consumers:** python -m trader.dashboard.server main; existing API/GraphQL/WS clients; current Vite owner surface mounted by owner_api.

**evidence:** Fixed vault-file string prefix accepting knowledge-private sibling or symlink escape; resolved Path containment + is_file now enforce exact vault. All cases authenticated; current GUI mode already blocks this legacy route.

**design_boundary:** SDD25.1 FastAPI serves built Vite; controls typed kernel IPC only. Legacy LIVE/provider paths intentional compatibility and prohibited during this review.

**costs_removal:** 1133 lines; legacy async SQLite routes and org12s cache could block under LIVE load; optional offloading needs measured demand. No removal because public compatibility contracts/tests remain.

## trader/dashboard/tracker.py — KEEP

Owner `dashboard`; baseline223 lines, final223. Baseline SHA256 `9575aa8b7af8d9e34621d215f67619d99dc5103cb4decda2b5a887cfd2843cc7`; final `9575aa8b7af8d9e34621d215f67619d99dc5103cb4decda2b5a887cfd2843cc7`.

**purpose:** Strict read-only engineering tracker/NEXT reconciliation.

**consumers:** owner_api installs /api/tracker and /owner-api/v1/tracker; Tracker frontend; retained closure validators.

**evidence:** Unique YAML keys, bounded2MiB reads, Pydantic contracts, exact row/status/dependency/gate counts, single selected work package; changing source read refuses rather than mixed snapshot.

**design_boundary:** SDD25.3 Tracker counts are workflow status, not runtime/trading readiness; no mutation API.

**costs_removal:** 223 lines schema and validation necessary for durable read contract. git revision timeout2s; no DB migration/write.

## trader/dashboard/trade_history.py — KEEP

Owner `dashboard`; baseline215 lines, final215. Baseline SHA256 `36e979ec8de7717ced09a62eb311a2bb55cdc39042bab991c3c71cc2de3d4228`; final `36e979ec8de7717ced09a62eb311a2bb55cdc39042bab991c3c71cc2de3d4228`.

**purpose:** Bounded read-only keyset trade pages with membership change detection.

**consumers:** owner_api /trades; frontend Trades pager; bench_trade_history and test_trade_history.

**evidence:** Strict canonical cursors, filter-bound identity, anchor/digest and one read-only transaction preserve no-gap traversal and detect cancellation/backdated membership changes; legacy cursor marked unverifiable.

**design_boundary:** Raw journal columns intentionally passed through and source-labelled, detailed lineage supplies classification/provenance. No economic inference or fill authority.

**costs_removal:** ≤200 rows page, omitted bulky excursion, index-stream digest. Deep membership and counts scale with consumed range; complete traversal quadratic by design, documented benchmark evidence. Do not remove checks for superficially simpler OFFSET.

## trader/dashboard/valuation.py — KEEP

Owner `dashboard`; baseline156 lines, final156. Baseline SHA256 `8fac9194df2eca66ce0432c73989fc8fde18735084bb55d34f4883adc6e70d43`; final `8fac9194df2eca66ce0432c73989fc8fde18735084bb55d34f4883adc6e70d43`.

**purpose:** Pure per-trade USDT ticker-based unrealized P&L estimate.

**consumers:** owner_api valuation/marks; server summary valuation.

**evidence:** Every open position finite/fresh/settlement-compatible else total unavailable; zero quantity real0; ticker source vs receipt times checked separately; duplicate symbols stay per trade; overflow unavailable.

**design_boundary:** Explicit estimate excludes unproven fees/funding and does not call venue, price source last/close not exchange mark.

**costs_removal:** 156 lines pure O(positions) validation; shared across legacy/current interfaces. No storage writes. Removing creates duplicated weaker valuation.

## trader/owner/__init__.py — KEEP

Owner `owner`; baseline19 lines, final19. Baseline SHA256 `01eb0e9f97720584b299767973b4f1c536acfe269a2df2873475bd46a2fb9193`; final `01eb0e9f97720584b299767973b4f1c536acfe269a2df2873475bd46a2fb9193`.

**purpose:** Public typed owner exports and architecture documentation.

**consumers:** Kernel/adapters and package imports.

**evidence:** No side effects, clients or execution authority. Removing package marker/documented exports disrupts public imports.

**design_boundary:** SDD25/26/35.14: owner reads prove records; typed commands remain Kernel-authorized. Intentional transport/future boundaries are not defects.

**costs_removal:** No side effects, clients or execution authority. Removing package marker/documented exports disrupts public imports.

## trader/owner/adapters/__init__.py — KEEP

Owner `owner`; baseline5 lines, final5. Baseline SHA256 `57e91a27496138fdbcc710ee2b81ddf7f95b6893791e863699a006cd951d3432`; final `57e91a27496138fdbcc710ee2b81ddf7f95b6893791e863699a006cd951d3432`.

**purpose:** Documents thin-adapter import boundary.

**consumers:** Python package/import resolution; boundary test scans.

**evidence:** Five-line package marker is inexpensive and intentionally contains no executor.

**design_boundary:** SDD25/26/35.14: owner reads prove records; typed commands remain Kernel-authorized. Intentional transport/future boundaries are not defects.

**costs_removal:** Five-line package marker is inexpensive and intentionally contains no executor.

## trader/owner/adapters/dashboard.py — KEEP

Owner `owner`; baseline35 lines, final35. Baseline SHA256 `c18f174119455eb611b836667f9cd7d3955225ab66f6990073a2206d78e0053c`; final `c18f174119455eb611b836667f9cd7d3955225ab66f6990073a2206d78e0053c`.

**purpose:** Maps authenticated session clicks and original request identity/time to OwnerRequest.

**consumers:** server._owner_gateway; GraphQL and owner_api control routes.

**evidence:** No state setter; stable client id/time survive retry. Semantic authentication remains DashboardAuth, authorization Kernel Authorizer. No local fallback.

**design_boundary:** SDD25/26/35.14: owner reads prove records; typed commands remain Kernel-authorized. Intentional transport/future boundaries are not defects.

**costs_removal:** No state setter; stable client id/time survive retry. Semantic authentication remains DashboardAuth, authorization Kernel Authorizer. No local fallback.

## trader/owner/adapters/openclaw.py — KEEP

Owner `owner`; baseline193 lines, final193. Baseline SHA256 `d077abf4276fa5dde28b64b42139c2549616fca6328a49ad5495a57fdfd09695`; final `d077abf4276fa5dde28b64b42139c2549616fca6328a49ad5495a57fdfd09695`.

**purpose:** Provider-neutral exact commands and sender-bound expiring confirmations.

**consumers:** Optional provider wrapper; unit/integration tests. Shipped identity bindings empty and live provider NOT_CONFIGURED.

**evidence:** NOT_CONFIGURED is intentional. Keeps at most32 pending proposals; same id resubmits safely, code collisions refuse. Removing on absence of live calls would remove approved transport contract.

**design_boundary:** SDD25/26/35.14: owner reads prove records; typed commands remain Kernel-authorized. Intentional transport/future boundaries are not defects.

**costs_removal:** NOT_CONFIGURED is intentional. Keeps at most32 pending proposals; same id resubmits safely, code collisions refuse. Removing on absence of live calls would remove approved transport contract.

## trader/owner/adapters/render.py — KEEP

Owner `owner`; baseline51 lines, final51. Baseline SHA256 `333ef784e4c8a3a0b647fdc8810ec539251d559c8e34c1e6477a53b122b3c529`; final `333ef784e4c8a3a0b647fdc8810ec539251d559c8e34c1e6477a53b122b3c529`.

**purpose:** Human-readable typed outcomes shared by conversational transports and GraphQL.

**consumers:** telegram.reply_text, OpenClawAdapter._send, GraphQL results.

**evidence:** Distinguishes never-delivered from outcome-unknown and exact same-id retry; renders accepted queue intent separately from actual flatten. Pure formatting; no authority.

**design_boundary:** SDD25/26/35.14: owner reads prove records; typed commands remain Kernel-authorized. Intentional transport/future boundaries are not defects.

**costs_removal:** Distinguishes never-delivered from outcome-unknown and exact same-id retry; renders accepted queue intent separately from actual flatten. Pure formatting; no authority.

## trader/owner/adapters/telegram.py — KEEP

Owner `owner`; baseline62 lines, final62. Baseline SHA256 `793f7b1895ea7cc69d7f4775961b73ab62125779a07fb50de92c30429c1af631`; final `793f7b1895ea7cc69d7f4775961b73ab62125779a07fb50de92c30429c1af631`.

**purpose:** Maps exact slash tokens and authenticated sender/update timestamp to typed request.

**consumers:** Kernel Telegram dispatch; tests.

**evidence:** Chat-id transport filter is separate from sender authorization. Derives stable id from update/message refs, no guessed time; free text never acquires control authority.

**design_boundary:** SDD25/26/35.14: owner reads prove records; typed commands remain Kernel-authorized. Intentional transport/future boundaries are not defects.

**costs_removal:** Chat-id transport filter is separate from sender authorization. Derives stable id from update/message refs, no guessed time; free text never acquires control authority.

## trader/owner/approvals.py — SIMPLIFY

Owner `owner`; baseline292 lines, final292. Baseline SHA256 `99a7ee035a141715d9f20992c997fb215758a9b120509baee917b9edee5cc35e`; final `33ecb7c4e5c332a22116b18420958dbead7027ea10c5527c770a940dc0f16190`.

**purpose:** Exact evidence/config/context-bound Needs You proposals, decisions and first-live ledger adapter.

**consumers:** OwnerService approval_decision; dashboard NeedsYou; owner query approvals.

**evidence:** Keep append-only item/receipt identities and source checks. Optional improvement: items calls validity for each retained item and validity rescans all proposals for supersession (quadratic history work), then slices100; batch same-object latest contexts without changing ordering/identities only after focused compatibility proof. No change here. Serious recovery deliberately routes guarded resume; decision never activates/spends. Corrected projected serious-recovery guidance from Operations to Trades, matching existing control location. Persisted proposal IDs/receipts unchanged.

**design_boundary:** SDD25/26/35.14: owner reads prove records; typed commands remain Kernel-authorized. Intentional transport/future boundaries are not defects.

**costs_removal:** Keep append-only item/receipt identities and source checks. Optional improvement: items calls validity for each retained item and validity rescans all proposals for supersession (quadratic history work), then slices100; batch same-object latest contexts without changing ordering/identities only after focused compatibility proof. No change here. Serious recovery deliberately routes guarded resume; decision never activates/spends.

## trader/owner/authz.py — KEEP

Owner `owner`; baseline98 lines, final98. Baseline SHA256 `30c4240856554de08ed3e73d0468a7f86bcb990715f788605c3106e0cffd2753`; final `30c4240856554de08ed3e73d0468a7f86bcb990715f788605c3106e0cffd2753`.

**purpose:** Maps channel-authenticated identity to principal operation/channel grants.

**consumers:** OwnerService; Kernel config; optional OpenClaw wrapper.

**evidence:** Defaults are explicit local session/CLI bindings; remote senders unbound. Actor class independent from client principal. Config extends grants explicitly; transport authentication remains IPC/channel layer. Tiny pure policy module.

**design_boundary:** SDD25/26/35.14: owner reads prove records; typed commands remain Kernel-authorized. Intentional transport/future boundaries are not defects.

**costs_removal:** Defaults are explicit local session/CLI bindings; remote senders unbound. Actor class independent from client principal. Config extends grants explicitly; transport authentication remains IPC/channel layer. Tiny pure policy module.

## trader/owner/cli.py — KEEP

Owner `owner`; baseline44 lines, final44. Baseline SHA256 `d04c575e3e49a0f76fe76b833be5711ceda1dce509cc2243b7706734e53d6e0c`; final `d04c575e3e49a0f76fe76b833be5711ceda1dce509cc2243b7706734e53d6e0c`.

**purpose:** Local status/health/panic command through authenticated IPC.

**consumers:** Manual python -m trader.owner.cli; local operator.

**evidence:** No local direct control fallback. CLI principal permits only reads plus existing panic; missing configuration returns UNAVAILABLE. No runtime polling process introduced.

**design_boundary:** SDD25/26/35.14: owner reads prove records; typed commands remain Kernel-authorized. Intentional transport/future boundaries are not defects.

**costs_removal:** No local direct control fallback. CLI principal permits only reads plus existing panic; missing configuration returns UNAVAILABLE. No runtime polling process introduced.

## trader/owner/contract.py — KEEP

Owner `owner`; baseline250 lines, final250. Baseline SHA256 `073fa1bd3ad1fbc4fc970c05dd8f86db63bdf2202b0a7b4295c468cd02bd7d87`; final `073fa1bd3ad1fbc4fc970c05dd8f86db63bdf2202b0a7b4295c468cd02bd7d87`.

**purpose:** Transport-neutral typed requests/results, status/disposition and immutable request fingerprints.

**consumers:** All owner adapters, service, IPC and dashboard GraphQL/REST.

**evidence:** Retains same-request replay semantics, strict operation-specific args, finite click time, bounded identifiers and metadata. Compatibility aliases PENDING/ERROR share values, not a second state machine. No authority or storage side effects.

**design_boundary:** SDD25/26/35.14: owner reads prove records; typed commands remain Kernel-authorized. Intentional transport/future boundaries are not defects.

**costs_removal:** Retains same-request replay semantics, strict operation-specific args, finite click time, bounded identifiers and metadata. Compatibility aliases PENDING/ERROR share values, not a second state machine. No authority or storage side effects.

## trader/owner/ipc.py — KEEP

Owner `owner`; baseline430 lines, final430. Baseline SHA256 `b0e38043cdf31a3d096ca3c573243792f0731ccaaf3e06bff24e173a0b406fec`; final `b0e38043cdf31a3d096ca3c573243792f0731ccaaf3e06bff24e173a0b406fec`.

**purpose:** Bounded private Unix socket framing and per-channel signed request/response transport.

**consumers:** Kernel OwnerIPCServer; Dashboard/CLI OwnerClient.

**evidence:** 0700 ancestry/0600 files, same UID and nonce/MAC; MAX_FRAME64KiB,32 connections with independent containment/read/recovery slots. Same-UID limitation explicit; separate OS identities is deployment boundary, not incidental redesign. Initial TMPDIR failures correctly enforce trust boundary.

**design_boundary:** SDD25/26/35.14: owner reads prove records; typed commands remain Kernel-authorized. Intentional transport/future boundaries are not defects.

**costs_removal:** 0700 ancestry/0600 files, same UID and nonce/MAC; MAX_FRAME64KiB,32 connections with independent containment/read/recovery slots. Same-UID limitation explicit; separate OS identities is deployment boundary, not incidental redesign. Initial TMPDIR failures correctly enforce trust boundary.

## trader/owner/knowledge.py — KEEP

Owner `owner`; baseline80 lines, final80. Baseline SHA256 `0d9f3833c4003bd91760de267ffb63d4180bf53d77a205b14bb8405dd0234fd0`; final `0d9f3833c4003bd91760de267ffb63d4180bf53d77a205b14bb8405dd0234fd0`.

**purpose:** Enriches existing vault graph with exact internal evidence and declared code-path provenance.

**consumers:** dashboard.owner_api Knowledge route.

**evidence:** No semantic inferred edge, no Graphify runtime import, no raw file source exposure. Resolves paths within vault/repo; bounded50 returned records per source. Missing link stays unlinked. File mtime is not event time.

**design_boundary:** SDD25/26/35.14: owner reads prove records; typed commands remain Kernel-authorized. Intentional transport/future boundaries are not defects.

**costs_removal:** No semantic inferred edge, no Graphify runtime import, no raw file source exposure. Resolves paths within vault/repo; bounded50 returned records per source. Missing link stays unlinked. File mtime is not event time.

## trader/owner/queries.py — SIMPLIFY

Owner `owner`; baseline194 lines, final194. Baseline SHA256 `610b471cb1bd4335a97eef2cdcf97ea347272a20d4a11494ad0515beb959f210`; final `610b471cb1bd4335a97eef2cdcf97ea347272a20d4a11494ad0515beb959f210`.

**purpose:** Bounded typed read-only evidence queries over journal and exact retained Learning/Portfolio cuts.

**consumers:** owner_api typed query, Knowledge enrichment, owner evidence frontend.

**evidence:** Keep dependency verification and lossless Portfolio evaluation reader. Optional simplification: imports obsolete hashlib and captured can open a second read-only Journal connection; removing import alone trivial, broader sharing requires preserving exact-cut consistency. Catalog LIMIT50 vs approvals100 differ by contract; no invented SQL/path input. Large payload becomes hash-labelled UNAVAILABLE; retained result is not current venue book.

**design_boundary:** SDD25/26/35.14: owner reads prove records; typed commands remain Kernel-authorized. Intentional transport/future boundaries are not defects.

**costs_removal:** Keep dependency verification and lossless Portfolio evaluation reader. Optional simplification: imports obsolete hashlib and captured can open a second read-only Journal connection; removing import alone trivial, broader sharing requires preserving exact-cut consistency. Catalog LIMIT50 vs approvals100 differ by contract; no invented SQL/path input. Large payload becomes hash-labelled UNAVAILABLE; retained result is not current venue book.

## trader/owner/service.py — KEEP

Owner `owner`; baseline787 lines, final787. Baseline SHA256 `c0dcaae8f333eb62301fa1516dcd26a944b2ab72d490a8338997ea1700356591`; final `c0dcaae8f333eb62301fa1516dcd26a944b2ab72d490a8338997ea1700356591`.

**purpose:** Single Kernel-owned authenticated intent executor and durable request/audit lifecycle.

**consumers:** Kernel._owner; in-process Telegram and signed IPC.

**evidence:** Reservation/claim/result is necessary at-most-once crash contract, not accidental complexity. Separate recovery lock/containment fence prevents stale activation; audit batches64/~2s with hard10000 cap and counted loss; controls sync. DONE/request and audit retention bounded; no approval or state projection moved into browser.

**design_boundary:** SDD25/26/35.14: owner reads prove records; typed commands remain Kernel-authorized. Intentional transport/future boundaries are not defects.

**costs_removal:** Reservation/claim/result is necessary at-most-once crash contract, not accidental complexity. Separate recovery lock/containment fence prevents stale activation; audit batches64/~2s with hard10000 cap and counted loss; controls sync. DONE/request and audit retention bounded; no approval or state projection moved into browser.
