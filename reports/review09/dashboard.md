# Review09 dashboard backend assessment

Exact existing owner ID: `dashboard`, subcomponents `dashboard.1` REST/GraphQL (19 files), `dashboard.2` WebSocket (shared server.py, 0 additional files), `dashboard.3` truth/economics (14), `dashboard.4` owner gateway (5), `dashboard.5` tracker/history (18). All 56 exact-owned files retained KEEP. This file complements the file-by-file evidence below and dashboard-files.json; it does not replace the original inventory.

Three confirmed defects corrected:

1. **Operations accounting truth** — close events previously chose Won/Lost/Flat from the numeric journal scalar, bypassing the established uncapped retained-receipt classifier. An unpriced UNKNOWN reconciliation with scalar0 appeared Flat while Overview/lineage correctly reported unknown. `operations_log` now reuses `economics.journal_pnl` and `booking_receipts`; UNKNOWN displays UNKNOWN with null monetary value and explicit `pnl_value_class`. Known/estimated amounts retain existing result labels and class evidence; no new calculation/writer or receipt format.
2. **Legacy vault containment** — `/api/vault/file` compared resolved paths by string prefix, so `../knowledge-private/secret.md` and an escaping symlink passed the `knowledge` prefix and could disclose outside-vault markdown to an authenticated LIVE client. Resolved `Path.is_relative_to` and `is_file` preserve legitimate nested notes and reject sibling/symlink/directory targets. Current shipped `config.yaml:432 READ_ONLY_GUI` already blocks this legacy route; remediation fixes its established LIVE contract without enabling it.
3. **GraphQL count consistency** — `decisions_total(symbol, directional_only)` returned early on symbol and ignored the directional flag, unlike the matching `decisions` query. BTC HOLD+BUY and ETH BUY produced BTC directional list1/count2. Both filters now compose, producing list1/count1. No in-repository current page consumes this count; this is a proven public query contract defect, not a claim of a visibly broken current page or a defect inferred from disconnection.

Validation: **44 focused passes**, with exact command/log/XML/JSON retained. In-process baseline source from `ef9fca8` reproduces the three incorrect behaviors; five parametrized regressions fail on baseline, two solely for newly exposed classification metadata (do not count these as extra defects). Current-mode phase tests verify no gateway/provider/control execution and continued GraphQL historical queries. Authenticated in-process ASGI requests only; no service launch/restart, production DB/provider calls, trading activation or full CI. All archived evidence source bodies unchanged, none executed. Root coordinates commit and AST-only Graphify update.

Usability and design assessment:

- Owner projections keep stored control permission, verified process liveness, successful-work age, configuration, evidence freshness and entry authority separate. Missing identity remains UNKNOWN; stale successful evidence remains historical. Account/net economic readiness is never inferred from a current numeric scalar.
- Overview reports current account/risk/news/protection with independent ages and errors; Research distinguishes discovery ranking from admission and displays disabled referee/handoff states; Strategies separates registry rows from exact immutable version/approval/governor/economic receipts; trade lineage joins exact stored IDs and exposes missing inputs. Operations has usable hour rollups that avoid cooldown/scan flooding. The corrected UNKNOWN outcome directly improves day-to-day owner interpretation.
- Current GUI is intentionally read-only: typed kernel IPC remains the sole control path under LIVE; GUI middleware rejects mutation/provider legacy endpoints and WebSocket. Chat/approval actions already use existing contracts and remain disabled here. No disconnected future voice, Evidence/Code lens, disabled world capture or first-live capability was called defective. SDD25 is target architecture, not an assertion of completed phase implementation.
- Existing REST, GraphQL and legacy projections serve distinct compatibility/evidence contracts; no framework, redesign, new approval or unnecessary merging/removal. Empty package marker and frozen closure validators are necessary boundaries/provenance. No backend simplification justified enough to outweigh compatibility/evidence loss.
- Optional performance improvements: batched complete receipt reads in full-history curves/strategy boards, paging/version-board growth, moving remaining legacy async SQLite work off event loop, bounded legacy log/knowledge reads. Current receipt coverage must remain complete; no production measurement or bottleneck claim, and current GUI denies the legacy routes. Keyset history deliberately spends O(consumed range) on membership digests to detect backdated cancellation; replacing with OFFSET/count heuristics would weaken established evidence.
- No owner decision or new policy needed for these narrow corrections. No claim of runtime or visual certification. Current user-facing interface already has explicit unavailable sections; frontend reviewer owns browser/interaction findings separately.

# Exact-owned file evidence


Existing inventory dashboard.1–dashboard.5: 56/56 files. No new inventory. KEEP is architectural retention, not a claim of zero defects; three bounded fixes are detailed below. All archive bodies remain unchanged and were not executed.

## docs/superpowers/reports/2026-09-29-trade-history-paging-r1-evidence/query_breakdown.py — KEEP

Pinned base sha256 `26ec375c0ff733ef99a50432f35328edadd7a77909a874ddec18fd8d8e9b8f98`; reviewed sha256 `26ec375c0ff733ef99a50432f35328edadd7a77909a874ddec18fd8d8e9b8f98`; 38 physical lines; owner `dashboard.5`.

**purpose:** Temporary-journal trade-history page/count/membership latency, memory, response-size and index-plan measurement.

**consumers:** Explicit offline benchmark invocation; trade-history paging report/provenance.

**evidence:** Builds10k/100k synthetic records; query_breakdown profiles40 repeats and10 membership scans. Large full benchmark intentionally not run for bounded remediation.

**design_boundary:** Performance evidence only; no production journal/provider use.

**costs_removal:** No service schedule/storage. Synthetic benchmarking can be expensive, especially deep whole-history traversal; preserved as reproducibility tool.

## docs/tracker/evidence/acc03-economics-sol/verify.py — KEEP

Pinned base sha256 `f848edb56e2cf8014f6d60ae2d8157f3dc2122a70eec1d14c897ae5fadbbd0b8`; reviewed sha256 `f848edb56e2cf8014f6d60ae2d8157f3dc2122a70eec1d14c897ae5fadbbd0b8`; 59 physical lines; owner `dashboard.3`.

**purpose:** Frozen acc03-economics-sol tracker/control/proof reconciliation source.

**consumers:** Retained sibling closure/report/source-manifest and docs/tracker/bundle-manifest.json; explicit archival invocation only.

**evidence:** Exact historical row counts/selections/source hashes/manifest/XML assertions; ROOT=Path(__file__).resolve().parents[4]. Contains output/control writers; not executed against current checkout.

**design_boundary:** Immutable historical proof, not a maintained current validation runner. Scope-limited offline closure never grants trading authority. Hardcoded production path (STR01) preserved as archive, never invoked.

**costs_removal:** Tracked archival bytes only; no runtime schedule or automatic database growth. Removal or adapting historical counts would sever frozen provenance. APPLY flags/control output writers require original historical context.

## docs/tracker/evidence/accounting-truth-followup-sol/verify.py — KEEP

Pinned base sha256 `0dfb92515fcfab4c362bf69f6d0f1b35a3723ef97cd0e9b475159a51eb66ae66`; reviewed sha256 `0dfb92515fcfab4c362bf69f6d0f1b35a3723ef97cd0e9b475159a51eb66ae66`; 33 physical lines; owner `dashboard.3`.

**purpose:** Frozen accounting-truth-followup-sol tracker/control/proof reconciliation source.

**consumers:** Retained sibling closure/report/source-manifest and docs/tracker/bundle-manifest.json; explicit archival invocation only.

**evidence:** Exact historical row counts/selections/source hashes/manifest/XML assertions; ROOT=Path(__file__).resolve().parents[4]. Contains output/control writers; not executed against current checkout.

**design_boundary:** Immutable historical proof, not a maintained current validation runner. Scope-limited offline closure never grants trading authority. Hardcoded production path (STR01) preserved as archive, never invoked.

**costs_removal:** Tracked archival bytes only; no runtime schedule or automatic database growth. Removal or adapting historical counts would sever frozen provenance. APPLY flags/control output writers require original historical context.

## docs/tracker/evidence/dec03-canonical-sol1/control_validation_test.py — KEEP

Pinned base sha256 `b961eb348b2003a6cf17d0c031029db10475ff99498a41d215d43e6c93e09d48`; reviewed sha256 `b961eb348b2003a6cf17d0c031029db10475ff99498a41d215d43e6c93e09d48`; 330 physical lines; owner `dashboard.5`.

**purpose:** Frozen dec03-canonical-sol1 tracker/control/proof reconciliation source.

**consumers:** Retained sibling closure/report/source-manifest and docs/tracker/bundle-manifest.json; explicit archival invocation only.

**evidence:** Exact historical row counts/selections/source hashes/manifest/XML assertions; ROOT=Path(__file__).resolve().parents[4]; BASE=37d210787c448053c563c92436d9ac17ab672e12; ENG=1ec9870a09868d108293b10c3417ffd14368ed2e; VERSION=1.56-dec03-canonical-closed-sol1. Contains output/control writers; not executed against current checkout.

**design_boundary:** Immutable historical proof, not a maintained current validation runner. Scope-limited offline closure never grants trading authority. Hardcoded production path (STR01) preserved as archive, never invoked.

**costs_removal:** Tracked archival bytes only; no runtime schedule or automatic database growth. Removal or adapting historical counts would sever frozen provenance. APPLY flags/control output writers require original historical context.

## docs/tracker/evidence/exe04-canonical-sol1/reconcile.py — KEEP

Pinned base sha256 `7a23040064271d03128f8fd9167eb9a0d93d778edf9a525a79aba131512d28cf`; reviewed sha256 `7a23040064271d03128f8fd9167eb9a0d93d778edf9a525a79aba131512d28cf`; 259 physical lines; owner `dashboard.5`.

**purpose:** Frozen exe04-canonical-sol1 tracker/control/proof reconciliation source.

**consumers:** Retained sibling closure/report/source-manifest and docs/tracker/bundle-manifest.json; explicit archival invocation only.

**evidence:** Exact historical row counts/selections/source hashes/manifest/XML assertions; ROOT=Path(__file__).resolve().parents[4]; BASE=ec2a68ed648c03d34cf99dcd9040ff14844eca92; ENG=916de7322dbc6ad85a66d7c6252f5d672c9bfcc1; VERSION=1.58-exe04-canonical-closed-sol1. Contains output/control writers; not executed against current checkout.

**design_boundary:** Immutable historical proof, not a maintained current validation runner. Scope-limited offline closure never grants trading authority. Hardcoded production path (STR01) preserved as archive, never invoked.

**costs_removal:** Tracked archival bytes only; no runtime schedule or automatic database growth. Removal or adapting historical counts would sever frozen provenance. APPLY flags/control output writers require original historical context.

## docs/tracker/evidence/gui01-overview-sol/verify.py — KEEP

Pinned base sha256 `bd0c8130c2b0b247f57bece764cef5cd4ec8e5378ae9da8d75727be06aec1d7d`; reviewed sha256 `bd0c8130c2b0b247f57bece764cef5cd4ec8e5378ae9da8d75727be06aec1d7d`; 30 physical lines; owner `dashboard.5`.

**purpose:** Frozen gui01-overview-sol tracker/control/proof reconciliation source.

**consumers:** Retained sibling closure/report/source-manifest and docs/tracker/bundle-manifest.json; explicit archival invocation only.

**evidence:** Exact historical row counts/selections/source hashes/manifest/XML assertions; ROOT=Path(__file__).resolve().parents[4]. Contains output/control writers; not executed against current checkout.

**design_boundary:** Immutable historical proof, not a maintained current validation runner. Scope-limited offline closure never grants trading authority. Hardcoded production path (STR01) preserved as archive, never invoked.

**costs_removal:** Tracked archival bytes only; no runtime schedule or automatic database growth. Removal or adapting historical counts would sever frozen provenance. APPLY flags/control output writers require original historical context.

## docs/tracker/evidence/out03-replay-map-sol/verify.py — KEEP

Pinned base sha256 `e0ce8261812b45dd1be37b31708faf411b2b8d978107516d9bd80fc04c1bd9c5`; reviewed sha256 `e0ce8261812b45dd1be37b31708faf411b2b8d978107516d9bd80fc04c1bd9c5`; 45 physical lines; owner `dashboard.5`.

**purpose:** Frozen out03-replay-map-sol tracker/control/proof reconciliation source.

**consumers:** Retained sibling closure/report/source-manifest and docs/tracker/bundle-manifest.json; explicit archival invocation only.

**evidence:** Exact historical row counts/selections/source hashes/manifest/XML assertions; ROOT=Path(__file__).resolve().parents[4]. Contains output/control writers; not executed against current checkout.

**design_boundary:** Immutable historical proof, not a maintained current validation runner. Scope-limited offline closure never grants trading authority. Hardcoded production path (STR01) preserved as archive, never invoked.

**costs_removal:** Tracked archival bytes only; no runtime schedule or automatic database growth. Removal or adapting historical counts would sever frozen provenance. APPLY flags/control output writers require original historical context.

## docs/tracker/evidence/own03-boundary-sol/verify.py — KEEP

Pinned base sha256 `93f04bfd9c6bb97189b67549c629fbd30c51bae6663fc2e5141e7c17cd157cf3`; reviewed sha256 `93f04bfd9c6bb97189b67549c629fbd30c51bae6663fc2e5141e7c17cd157cf3`; 37 physical lines; owner `dashboard.5`.

**purpose:** Frozen own03-boundary-sol tracker/control/proof reconciliation source.

**consumers:** Retained sibling closure/report/source-manifest and docs/tracker/bundle-manifest.json; explicit archival invocation only.

**evidence:** Exact historical row counts/selections/source hashes/manifest/XML assertions; ROOT=Path(__file__).resolve().parents[4]. Contains output/control writers; not executed against current checkout.

**design_boundary:** Immutable historical proof, not a maintained current validation runner. Scope-limited offline closure never grants trading authority. Hardcoded production path (STR01) preserved as archive, never invoked.

**costs_removal:** Tracked archival bytes only; no runtime schedule or automatic database growth. Removal or adapting historical counts would sever frozen provenance. APPLY flags/control output writers require original historical context.

## docs/tracker/evidence/own04-phase-scope-review/verify.py — KEEP

Pinned base sha256 `cfae4a5cd13a7abab49f14f3a2d3c710d6a3b51f8d841d02aac7ff7b2e17a87b`; reviewed sha256 `cfae4a5cd13a7abab49f14f3a2d3c710d6a3b51f8d841d02aac7ff7b2e17a87b`; 37 physical lines; owner `dashboard.5`.

**purpose:** Frozen own04-phase-scope-review tracker/control/proof reconciliation source.

**consumers:** Retained sibling closure/report/source-manifest and docs/tracker/bundle-manifest.json; explicit archival invocation only.

**evidence:** Exact historical row counts/selections/source hashes/manifest/XML assertions; ROOT=Path(__file__).resolve().parents[4]. Contains output/control writers; not executed against current checkout.

**design_boundary:** Immutable historical proof, not a maintained current validation runner. Scope-limited offline closure never grants trading authority. Hardcoded production path (STR01) preserved as archive, never invoked.

**costs_removal:** Tracked archival bytes only; no runtime schedule or automatic database growth. Removal or adapting historical counts would sever frozen provenance. APPLY flags/control output writers require original historical context.

## docs/tracker/evidence/own04-state-sol/verify.py — KEEP

Pinned base sha256 `4c48c31531e1eb68389bd02ad30ad94e098f9bf42ea28c68abb830b08975f29c`; reviewed sha256 `4c48c31531e1eb68389bd02ad30ad94e098f9bf42ea28c68abb830b08975f29c`; 36 physical lines; owner `dashboard.5`.

**purpose:** Frozen own04-state-sol tracker/control/proof reconciliation source.

**consumers:** Retained sibling closure/report/source-manifest and docs/tracker/bundle-manifest.json; explicit archival invocation only.

**evidence:** Exact historical row counts/selections/source hashes/manifest/XML assertions; ROOT=Path(__file__).resolve().parents[4]. Contains output/control writers; not executed against current checkout.

**design_boundary:** Immutable historical proof, not a maintained current validation runner. Scope-limited offline closure never grants trading authority. Hardcoded production path (STR01) preserved as archive, never invoked.

**costs_removal:** Tracked archival bytes only; no runtime schedule or automatic database growth. Removal or adapting historical counts would sever frozen provenance. APPLY flags/control output writers require original historical context.

## docs/tracker/evidence/port01-canonical-sol1/control_validation_test.py — KEEP

Pinned base sha256 `293d7797f88e5dfbeba2b4272d83bfac55ee64f5c738000acc08aa051eb3cc68`; reviewed sha256 `293d7797f88e5dfbeba2b4272d83bfac55ee64f5c738000acc08aa051eb3cc68`; 316 physical lines; owner `dashboard.5`.

**purpose:** Frozen port01-canonical-sol1 tracker/control/proof reconciliation source.

**consumers:** Retained sibling closure/report/source-manifest and docs/tracker/bundle-manifest.json; explicit archival invocation only.

**evidence:** Exact historical row counts/selections/source hashes/manifest/XML assertions; ROOT=Path(__file__).resolve().parents[4]; BASE=2b5d250c418aef3268eb53dc7d1efbd081efc44f; ENG=2ea4a95349062b7ec19cde6263fa8b3e8af45660; VERSION=1.57-port01-canonical-closed-sol1. Contains output/control writers; not executed against current checkout.

**design_boundary:** Immutable historical proof, not a maintained current validation runner. Scope-limited offline closure never grants trading authority. Hardcoded production path (STR01) preserved as archive, never invoked.

**costs_removal:** Tracked archival bytes only; no runtime schedule or automatic database growth. Removal or adapting historical counts would sever frozen provenance. APPLY flags/control output writers require original historical context.

## docs/tracker/evidence/port04-canonical-sol1/reconcile.py — KEEP

Pinned base sha256 `5eb5072f32638746112c14da6626dc6bf2acb139c3172f7154de76f218c500d8`; reviewed sha256 `5eb5072f32638746112c14da6626dc6bf2acb139c3172f7154de76f218c500d8`; 202 physical lines; owner `dashboard.5`.

**purpose:** Frozen port04-canonical-sol1 tracker/control/proof reconciliation source.

**consumers:** Retained sibling closure/report/source-manifest and docs/tracker/bundle-manifest.json; explicit archival invocation only.

**evidence:** Exact historical row counts/selections/source hashes/manifest/XML assertions; ROOT=Path(__file__).resolve().parents[4]; BASE=b6b6e6016f76d0df4869312b1b7c1a4551ba0918; ENG=387d130dedfe259e707ddda77b5eab83aa493aa7; VERSION=1.60-port04-canonical-closed-sol1. Contains output/control writers; not executed against current checkout.

**design_boundary:** Immutable historical proof, not a maintained current validation runner. Scope-limited offline closure never grants trading authority. Hardcoded production path (STR01) preserved as archive, never invoked.

**costs_removal:** Tracked archival bytes only; no runtime schedule or automatic database growth. Removal or adapting historical counts would sever frozen provenance. APPLY flags/control output writers require original historical context.

## docs/tracker/evidence/qnt09-canonical-intake-sol1/validate.py — KEEP

Pinned base sha256 `6315a27ae5d44a2b5e6d595a88b6177d46d736182b281d7892d0728221faca71`; reviewed sha256 `6315a27ae5d44a2b5e6d595a88b6177d46d736182b281d7892d0728221faca71`; 68 physical lines; owner `dashboard.5`.

**purpose:** Frozen qnt09-canonical-intake-sol1 tracker/control/proof reconciliation source.

**consumers:** Retained sibling closure/report/source-manifest and docs/tracker/bundle-manifest.json; explicit archival invocation only.

**evidence:** Exact historical row counts/selections/source hashes/manifest/XML assertions; ROOT=Path(__file__).resolve().parents[4]; BASE=a355486900371f6e5ba9605e2fb84da661139489. Contains output/control writers; not executed against current checkout.

**design_boundary:** Immutable historical proof, not a maintained current validation runner. Scope-limited offline closure never grants trading authority. Hardcoded production path (STR01) preserved as archive, never invoked.

**costs_removal:** Tracked archival bytes only; no runtime schedule or automatic database growth. Removal or adapting historical counts would sever frozen provenance. APPLY flags/control output writers require original historical context.

## docs/tracker/evidence/str01-canonical-sol1/control-validation.py — KEEP

Pinned base sha256 `7cbcb60d99793e9415fa20b9447c739f683ec3b260e2f89fa487f97612fa0294`; reviewed sha256 `7cbcb60d99793e9415fa20b9447c739f683ec3b260e2f89fa487f97612fa0294`; 60 physical lines; owner `dashboard.5`.

**purpose:** Frozen str01-canonical-sol1 tracker/control/proof reconciliation source.

**consumers:** Retained sibling closure/report/source-manifest and docs/tracker/bundle-manifest.json; explicit archival invocation only.

**evidence:** Exact historical row counts/selections/source hashes/manifest/XML assertions; ROOT=Path('/mnt/luffy-data/luffy/production'). Contains output/control writers; not executed against current checkout.

**design_boundary:** Immutable historical proof, not a maintained current validation runner. Scope-limited offline closure never grants trading authority. Hardcoded production path (STR01) preserved as archive, never invoked.

**costs_removal:** Tracked archival bytes only; no runtime schedule or automatic database growth. Removal or adapting historical counts would sever frozen provenance. APPLY flags/control output writers require original historical context.

## docs/tracker/evidence/str02-canonical-sol1/control-validation.py — KEEP

Pinned base sha256 `e486222f528a3f3f5de472e6a908566314ab31565fa91aa750886aed7df8a5c4`; reviewed sha256 `e486222f528a3f3f5de472e6a908566314ab31565fa91aa750886aed7df8a5c4`; 94 physical lines; owner `dashboard.5`.

**purpose:** Frozen str02-canonical-sol1 tracker/control/proof reconciliation source.

**consumers:** Retained sibling closure/report/source-manifest and docs/tracker/bundle-manifest.json; explicit archival invocation only.

**evidence:** Exact historical row counts/selections/source hashes/manifest/XML assertions; ROOT=Path(__file__).resolve().parents[4]. Contains output/control writers; not executed against current checkout.

**design_boundary:** Immutable historical proof, not a maintained current validation runner. Scope-limited offline closure never grants trading authority. Hardcoded production path (STR01) preserved as archive, never invoked.

**costs_removal:** Tracked archival bytes only; no runtime schedule or automatic database growth. Removal or adapting historical counts would sever frozen provenance. APPLY flags/control output writers require original historical context.

## docs/tracker/evidence/str03-canonical-sol1/control-validation.py — KEEP

Pinned base sha256 `509fcd69dc6ee43bbfe4e7919c605459cece230fc5b2ea1d58a9358b91cfdbe3`; reviewed sha256 `509fcd69dc6ee43bbfe4e7919c605459cece230fc5b2ea1d58a9358b91cfdbe3`; 121 physical lines; owner `dashboard.5`.

**purpose:** Frozen str03-canonical-sol1 tracker/control/proof reconciliation source.

**consumers:** Retained sibling closure/report/source-manifest and docs/tracker/bundle-manifest.json; explicit archival invocation only.

**evidence:** Exact historical row counts/selections/source hashes/manifest/XML assertions; ROOT=Path(__file__).resolve().parents[4]. Contains output/control writers; not executed against current checkout.

**design_boundary:** Immutable historical proof, not a maintained current validation runner. Scope-limited offline closure never grants trading authority. Hardcoded production path (STR01) preserved as archive, never invoked.

**costs_removal:** Tracked archival bytes only; no runtime schedule or automatic database growth. Removal or adapting historical counts would sever frozen provenance. APPLY flags/control output writers require original historical context.

## scripts/bench_trade_history.py — KEEP

Pinned base sha256 `4c6b5768719654cef0ecb317b19a403bb67d99c2128560f4277215d3400ef303`; reviewed sha256 `4c6b5768719654cef0ecb317b19a403bb67d99c2128560f4277215d3400ef303`; 184 physical lines; owner `dashboard.5`.

**purpose:** Temporary-journal trade-history page/count/membership latency, memory, response-size and index-plan measurement.

**consumers:** Explicit offline benchmark invocation; trade-history paging report/provenance.

**evidence:** Builds10k/100k synthetic records; query_breakdown profiles40 repeats and10 membership scans. Large full benchmark intentionally not run for bounded remediation.

**design_boundary:** Performance evidence only; no production journal/provider use.

**costs_removal:** No service schedule/storage. Synthetic benchmarking can be expensive, especially deep whole-history traversal; preserved as reproducibility tool.

## tests/owner_frontend_server.py — KEEP

Pinned base sha256 `fac6d8657ed2239f61da1c0bd28cded5af4e5b87360eceb5c3e6e75e703d834a`; reviewed sha256 `fac6d8657ed2239f61da1c0bd28cded5af4e5b87360eceb5c3e6e75e703d834a`; 106 physical lines; owner `dashboard.4`.

**purpose:** Standalone browser fixture server using temporary ROOT, fake gateway/chat and /__test__ scenario controls.

**consumers:** frontend Playwright browser webServer contract; explicit python -m tests.owner_frontend_server only.

**evidence:** Standalone browser fixture server using temporary ROOT, fake gateway/chat and /__test__ scenario controls. All function bodies/assertions inspected; no assertion-only rewrites or current production runs.

**design_boundary:** Test-only fakes/temp journals establish narrow offline contract, never runtime/provider/live evidence.

**costs_removal:** No normal runtime CPU/storage. Keep meaningful negative controls and schema compatibility; remove only when underlying public contract is retired.

## tests/test_acc03_economics.py — KEEP

Pinned base sha256 `4e9f22cb6ab0ff3ea8628e6e6505d5756149be8572ca4e9f348eaf44e77225fe`; reviewed sha256 `4e9f22cb6ab0ff3ea8628e6e6505d5756149be8572ca4e9f348eaf44e77225fe`; 102 physical lines; owner `dashboard.3`.

**purpose:** Separates recorded venue fields, unavailable funding/net total, unknown booking and derived excursion coverage/version; tamper/nonfinite cases.

**consumers:** Explicit focused pytest discovery and supporting fixture imports.

**evidence:** Separates recorded venue fields, unavailable funding/net total, unknown booking and derived excursion coverage/version; tamper/nonfinite cases. All function bodies/assertions inspected; no assertion-only rewrites or current production runs.

**design_boundary:** Test-only fakes/temp journals establish narrow offline contract, never runtime/provider/live evidence.

**costs_removal:** No normal runtime CPU/storage. Keep meaningful negative controls and schema compatibility; remove only when underlying public contract is retired.

## tests/test_accounting_truth_followup.py — KEEP

Pinned base sha256 `01c4a7e1eb15db77fc89a761cfe8e2206a011e8095d69a5ccb126ecf47f68178`; reviewed sha256 `ad51d532a6bdc3743d005c659931c6e418595dbf6814b1b4b4d7bcd1714de9e4`; 70 physical lines; owner `dashboard.3`.

**purpose:** Unknown/estimated receipt markers override scalar, tampered early leg persists beyond50 later legs; new Operations result and evidence classification regressions.

**consumers:** Explicit focused pytest discovery and supporting fixture imports.

**evidence:** Unknown/estimated receipt markers override scalar, tampered early leg persists beyond50 later legs; new Operations result and evidence classification regressions. All function bodies/assertions inspected; no assertion-only rewrites or current production runs.

**design_boundary:** Test-only fakes/temp journals establish narrow offline contract, never runtime/provider/live evidence.

**costs_removal:** No normal runtime CPU/storage. Keep meaningful negative controls and schema compatibility; remove only when underlying public contract is retired.

## tests/test_attention_view.py — KEEP

Pinned base sha256 `0fd34dcde2bd8fa3f2e6dcf163bde0c8a6a160293ae29dc13c663dc6997c939c`; reviewed sha256 `0fd34dcde2bd8fa3f2e6dcf163bde0c8a6a160293ae29dc13c663dc6997c939c`; 80 physical lines; owner `dashboard.1`.

**purpose:** Authenticated local attention/investigation status, disabled/config/error/learning/admission/recovery evidence and no provider lookup.

**consumers:** Explicit focused pytest discovery and supporting fixture imports.

**evidence:** Authenticated local attention/investigation status, disabled/config/error/learning/admission/recovery evidence and no provider lookup. All function bodies/assertions inspected; no assertion-only rewrites or current production runs.

**design_boundary:** Test-only fakes/temp journals establish narrow offline contract, never runtime/provider/live evidence.

**costs_removal:** No normal runtime CPU/storage. Keep meaningful negative controls and schema compatibility; remove only when underlying public contract is retired.

## tests/test_candle_cache_reuse.py — KEEP

Pinned base sha256 `297648d221de9b95db8867530de1f084b2e0c06060daf362fac98899ee7c8e00`; reviewed sha256 `297648d221de9b95db8867530de1f084b2e0c06060daf362fac98899ee7c8e00`; 49 physical lines; owner `dashboard.1`.

**purpose:** Preserves production candle store refresh tail and young-listing reuse using fake exchanges; source contract charts use production public feed.

**consumers:** Explicit focused pytest discovery and supporting fixture imports.

**evidence:** Preserves production candle store refresh tail and young-listing reuse using fake exchanges; source contract charts use production public feed. All function bodies/assertions inspected; no assertion-only rewrites or current production runs.

**design_boundary:** Test-only fakes/temp journals establish narrow offline contract, never runtime/provider/live evidence.

**costs_removal:** No normal runtime CPU/storage. Keep meaningful negative controls and schema compatibility; remove only when underlying public contract is retired.

## tests/test_company.py — KEEP

Pinned base sha256 `bb3191903ccf048c3015838002e8b1e5d435026494e2fede0491d0fb43bd62e5`; reviewed sha256 `bb3191903ccf048c3015838002e8b1e5d435026494e2fede0491d0fb43bd62e5`; 125 physical lines; owner `dashboard.1`.

**purpose:** Stub-journal roster/card shape, report-to/category, accuracy, sparkline and manager fields. Narrow presentation contract, not real liveness proof.

**consumers:** Explicit focused pytest discovery and supporting fixture imports.

**evidence:** Stub-journal roster/card shape, report-to/category, accuracy, sparkline and manager fields. Narrow presentation contract, not real liveness proof. All function bodies/assertions inspected; no assertion-only rewrites or current production runs.

**design_boundary:** Test-only fakes/temp journals establish narrow offline contract, never runtime/provider/live evidence.

**costs_removal:** No normal runtime CPU/storage. Keep meaningful negative controls and schema compatibility; remove only when underlying public contract is retired.

## tests/test_current_truth_completion_clock.py — KEEP

Pinned base sha256 `f0dc06d7c76966504000917fea320a2e9349961bc3910fb8fc6a1e7855cbbbc2`; reviewed sha256 `f0dc06d7c76966504000917fea320a2e9349961bc3910fb8fc6a1e7855cbbbc2`; 250 physical lines; owner `dashboard.3`.

**purpose:** Preserves actual completion time; before-attempt/after-record/missing/malformed never current FRESH or Risk PASS; producer-reader-consumer behavior.

**consumers:** Explicit focused pytest discovery and supporting fixture imports.

**evidence:** Preserves actual completion time; before-attempt/after-record/missing/malformed never current FRESH or Risk PASS; producer-reader-consumer behavior. All function bodies/assertions inspected; no assertion-only rewrites or current production runs.

**design_boundary:** Test-only fakes/temp journals establish narrow offline contract, never runtime/provider/live evidence.

**costs_removal:** No normal runtime CPU/storage. Keep meaningful negative controls and schema compatibility; remove only when underlying public contract is retired.

## tests/test_current_truth_contract.py — KEEP

Pinned base sha256 `505a92a13c771dae0a126c29659c217500d888f01fad0d8989dcdc4fa0313a68`; reviewed sha256 `505a92a13c771dae0a126c29659c217500d888f01fad0d8989dcdc4fa0313a68`; 1095 physical lines; owner `dashboard.3`.

**purpose:** Adversarial freshness/account/valuation/risk/news/latest-index tests; fake kernel objects and temp journals, no boot/providers. Includes250k HOLD rows to prove exact latest beyond lookback.

**consumers:** Explicit focused pytest discovery and supporting fixture imports.

**evidence:** Adversarial freshness/account/valuation/risk/news/latest-index tests; fake kernel objects and temp journals, no boot/providers. Includes250k HOLD rows to prove exact latest beyond lookback. All function bodies/assertions inspected; no assertion-only rewrites or current production runs.

**design_boundary:** Test-only fakes/temp journals establish narrow offline contract, never runtime/provider/live evidence.

**costs_removal:** No normal runtime CPU/storage. Keep meaningful negative controls and schema compatibility; remove only when underlying public contract is retired.

## tests/test_current_truth_corrections.py — KEEP

Pinned base sha256 `e08b80465474132c0f38e8c819c697b0e0fac6ea9c145387e3e20c200978c113`; reviewed sha256 `e08b80465474132c0f38e8c819c697b0e0fac6ea9c145387e3e20c200978c113`; 721 physical lines; owner `dashboard.3`.

**purpose:** Adversarial unknown/future clocks, news damping exactly once, risk reproducibility/policy identity, fallback provenance, duplicate-symbol valuation and cockpit state.

**consumers:** Explicit focused pytest discovery and supporting fixture imports.

**evidence:** Adversarial unknown/future clocks, news damping exactly once, risk reproducibility/policy identity, fallback provenance, duplicate-symbol valuation and cockpit state. All function bodies/assertions inspected; no assertion-only rewrites or current production runs.

**design_boundary:** Test-only fakes/temp journals establish narrow offline contract, never runtime/provider/live evidence.

**costs_removal:** No normal runtime CPU/storage. Keep meaningful negative controls and schema compatibility; remove only when underlying public contract is retired.

## tests/test_current_truth_integration.py — KEEP

Pinned base sha256 `8a107c0089bdcb20eda65279786875eedc81caac66fbd723e3ba3fa8b5501216`; reviewed sha256 `8a107c0089bdcb20eda65279786875eedc81caac66fbd723e3ba3fa8b5501216`; 353 physical lines; owner `dashboard.3`.

**purpose:** Legacy summary/WS/company and pre-migration read-only journal integration; null/partial valuation and old index refusal preserve database bytes.

**consumers:** Explicit focused pytest discovery and supporting fixture imports.

**evidence:** Legacy summary/WS/company and pre-migration read-only journal integration; null/partial valuation and old index refusal preserve database bytes. All function bodies/assertions inspected; no assertion-only rewrites or current production runs.

**design_boundary:** Test-only fakes/temp journals establish narrow offline contract, never runtime/provider/live evidence.

**costs_removal:** No normal runtime CPU/storage. Keep meaningful negative controls and schema compatibility; remove only when underlying public contract is retired.

## tests/test_dashboard_auth.py — KEEP

Pinned base sha256 `582e3d7aa458faaf6e81bb6daffddf311fca4e21a13e36739d8ad193bbe3e2c1`; reviewed sha256 `15c52bf801d2074637814b2b6922447fae9c5de7344bd50d58733af1d4e5ec6f`; 71 physical lines; owner `dashboard.1`.

**purpose:** Default token fail closed, browser origin/session/logout/WebSocket and expiry/rotation; new nested-vault allowed/sibling+symlink+directory denied.

**consumers:** Explicit focused pytest discovery and supporting fixture imports.

**evidence:** Default token fail closed, browser origin/session/logout/WebSocket and expiry/rotation; new nested-vault allowed/sibling+symlink+directory denied. All function bodies/assertions inspected; no assertion-only rewrites or current production runs.

**design_boundary:** Test-only fakes/temp journals establish narrow offline contract, never runtime/provider/live evidence.

**costs_removal:** No normal runtime CPU/storage. Keep meaningful negative controls and schema compatibility; remove only when underlying public contract is retired.

## tests/test_dashboard_phase.py — KEEP

Pinned base sha256 `9dd25202b5355d520e9723e5b51e0bf3dcd1172723d7c1314e4fcf3d7d1fefd7`; reviewed sha256 `9dd25202b5355d520e9723e5b51e0bf3dcd1172723d7c1314e4fcf3d7d1fefd7`; 117 physical lines; owner `dashboard.1`.

**purpose:** Explicit mode, GUI startup with no kernel, LIVE missing-identity refusal, GraphQL query vs mutation and provider/control/WS guards; synthetic tracker fixture.

**consumers:** Explicit focused pytest discovery and supporting fixture imports.

**evidence:** Explicit mode, GUI startup with no kernel, LIVE missing-identity refusal, GraphQL query vs mutation and provider/control/WS guards; synthetic tracker fixture. All function bodies/assertions inspected; no assertion-only rewrites or current production runs.

**design_boundary:** Test-only fakes/temp journals establish narrow offline contract, never runtime/provider/live evidence.

**costs_removal:** No normal runtime CPU/storage. Keep meaningful negative controls and schema compatibility; remove only when underlying public contract is retired.

## tests/test_dashboard_supervisor_unknown.py — KEEP

Pinned base sha256 `30e571638c501d3b567ac60952091c8ced0eb7d359fdc211da2e92b9ff9f5163`; reviewed sha256 `30e571638c501d3b567ac60952091c8ced0eb7d359fdc211da2e92b9ff9f5163`; 33 physical lines; owner `dashboard.1`.

**purpose:** Missing checks remain None, explicit match/mismatch preserved, old pass only last_reported.

**consumers:** Explicit focused pytest discovery and supporting fixture imports.

**evidence:** Missing checks remain None, explicit match/mismatch preserved, old pass only last_reported. All function bodies/assertions inspected; no assertion-only rewrites or current production runs.

**design_boundary:** Test-only fakes/temp journals establish narrow offline contract, never runtime/provider/live evidence.

**costs_removal:** No normal runtime CPU/storage. Keep meaningful negative controls and schema compatibility; remove only when underlying public contract is retired.

## tests/test_data02_decision_lineage.py — KEEP

Pinned base sha256 `fc0b3b863e80723197f35550366a2714082c43f164a096eec0c4d79ad4f44935`; reviewed sha256 `fc0b3b863e80723197f35550366a2714082c43f164a096eec0c4d79ad4f44935`; 167 physical lines; owner `dashboard.1`.

**purpose:** Reopen exact persisted market receipts and reject unavailable/tampered/schema/quality/future/instrument lineage, retain attention admission identity.

**consumers:** Explicit focused pytest discovery and supporting fixture imports.

**evidence:** Reopen exact persisted market receipts and reject unavailable/tampered/schema/quality/future/instrument lineage, retain attention admission identity. All function bodies/assertions inspected; no assertion-only rewrites or current production runs.

**design_boundary:** Test-only fakes/temp journals establish narrow offline contract, never runtime/provider/live evidence.

**costs_removal:** No normal runtime CPU/storage. Keep meaningful negative controls and schema compatibility; remove only when underlying public contract is retired.

## tests/test_glacier_truth.py — KEEP

Pinned base sha256 `1a40a2bdca5c449fd92a68491cb3b4c1fb2905771b340c3025b7a59b393018ae`; reviewed sha256 `1a40a2bdca5c449fd92a68491cb3b4c1fb2905771b340c3025b7a59b393018ae`; 105 physical lines; owner `dashboard.3`.

**purpose:** Verified environment expires, provider disabled/unknown honest, exact version authority read with query_only, mounts and offline browser fixture serialization.

**consumers:** Explicit focused pytest discovery and supporting fixture imports.

**evidence:** Verified environment expires, provider disabled/unknown honest, exact version authority read with query_only, mounts and offline browser fixture serialization. All function bodies/assertions inspected; no assertion-only rewrites or current production runs.

**design_boundary:** Test-only fakes/temp journals establish narrow offline contract, never runtime/provider/live evidence.

**costs_removal:** No normal runtime CPU/storage. Keep meaningful negative controls and schema compatibility; remove only when underlying public contract is retired.

## tests/test_gui01_overview.py — KEEP

Pinned base sha256 `9aa8c0528e580b726c03818f048ce8b411e66fd6274f51c9fe4f40191f263717`; reviewed sha256 `9aa8c0528e580b726c03818f048ce8b411e66fd6274f51c9fe4f40191f263717`; 48 physical lines; owner `dashboard.1`.

**purpose:** Partial journal realized P&L unknown, empty-day real zero, stopped kernel overview readable while chat/providers denied.

**consumers:** Explicit focused pytest discovery and supporting fixture imports.

**evidence:** Partial journal realized P&L unknown, empty-day real zero, stopped kernel overview readable while chat/providers denied. All function bodies/assertions inspected; no assertion-only rewrites or current production runs.

**design_boundary:** Test-only fakes/temp journals establish narrow offline contract, never runtime/provider/live evidence.

**costs_removal:** No normal runtime CPU/storage. Keep meaningful negative controls and schema compatibility; remove only when underlying public contract is retired.

## tests/test_overview_desk.py — KEEP

Pinned base sha256 `5cea539c61673d264c5614873db20f3df65269963726a92d5a222570db6b863f`; reviewed sha256 `6c7c94fb6efe7553c66a9ec54c060b53e71f8c08d61fb568db58f11eb5fe3077`; 126 physical lines; owner `dashboard.1`.

**purpose:** Curve omits unknown, scan newest by time, research missing vs empty, strategy known-value stats, Operations aggregation/HTTP; added composed GraphQL decision count.

**consumers:** Explicit focused pytest discovery and supporting fixture imports.

**evidence:** Curve omits unknown, scan newest by time, research missing vs empty, strategy known-value stats, Operations aggregation/HTTP; added composed GraphQL decision count. All function bodies/assertions inspected; no assertion-only rewrites or current production runs.

**design_boundary:** Test-only fakes/temp journals establish narrow offline contract, never runtime/provider/live evidence.

**costs_removal:** No normal runtime CPU/storage. Keep meaningful negative controls and schema compatibility; remove only when underlying public contract is retired.

## tests/test_own04_state_truth.py — KEEP

Pinned base sha256 `c8ee9ad8a81147f30d6acd057326e3f60049483a308d9a8e8a8b48ae6570434d`; reviewed sha256 `c8ee9ad8a81147f30d6acd057326e3f60049483a308d9a8e8a8b48ae6570434d`; 129 physical lines; owner `dashboard.3`.

**purpose:** Existing real flock/process identity vs ACTIVE control, heartbeat clock/work, collector timestamp and no-write/no-created-artifact tests.

**consumers:** Explicit focused pytest discovery and supporting fixture imports.

**evidence:** Existing real flock/process identity vs ACTIVE control, heartbeat clock/work, collector timestamp and no-write/no-created-artifact tests. All function bodies/assertions inspected; no assertion-only rewrites or current production runs.

**design_boundary:** Test-only fakes/temp journals establish narrow offline contract, never runtime/provider/live evidence.

**costs_removal:** No normal runtime CPU/storage. Keep meaningful negative controls and schema compatibility; remove only when underlying public contract is retired.

## tests/test_owner_read_cache.py — KEEP

Pinned base sha256 `917caec64d9553e0061116472205b9fc9e1abd10da5c68107947719eaaa4c1b8`; reviewed sha256 `917caec64d9553e0061116472205b9fc9e1abd10da5c68107947719eaaa4c1b8`; 101 physical lines; owner `dashboard.3`.

**purpose:** Fingerprint invalidation even same mtime/size, source moving/unreadable refuses cache, bounds/concurrent serial build, each protection verification check required.

**consumers:** Explicit focused pytest discovery and supporting fixture imports.

**evidence:** Fingerprint invalidation even same mtime/size, source moving/unreadable refuses cache, bounds/concurrent serial build, each protection verification check required. All function bodies/assertions inspected; no assertion-only rewrites or current production runs.

**design_boundary:** Test-only fakes/temp journals establish narrow offline contract, never runtime/provider/live evidence.

**costs_removal:** No normal runtime CPU/storage. Keep meaningful negative controls and schema compatibility; remove only when underlying public contract is retired.

## tests/test_owner_reads_m2.py — KEEP

Pinned base sha256 `b126d11b47e10c1cec96523c24976233df79f945cb9acfcfc9157426a5693b42`; reviewed sha256 `b126d11b47e10c1cec96523c24976233df79f945cb9acfcfc9157426a5693b42`; 546 physical lines; owner `dashboard.4`.

**purpose:** Forensic exact id joins, paging, source omissions, per-strategy provenance, observed flows, knowledge/chat lookup caps and worker-thread request responsiveness.

**consumers:** Explicit focused pytest discovery and supporting fixture imports.

**evidence:** Forensic exact id joins, paging, source omissions, per-strategy provenance, observed flows, knowledge/chat lookup caps and worker-thread request responsiveness. All function bodies/assertions inspected; no assertion-only rewrites or current production runs.

**design_boundary:** Test-only fakes/temp journals establish narrow offline contract, never runtime/provider/live evidence.

**costs_removal:** No normal runtime CPU/storage. Keep meaningful negative controls and schema compatibility; remove only when underlying public contract is retired.

## tests/test_rent_wiring.py — KEEP

Pinned base sha256 `1cc70060c70ce8ab60d3059167f82b5a64464c405c88083348e9df6bb497ab29`; reviewed sha256 `1cc70060c70ce8ab60d3059167f82b5a64464c405c88083348e9df6bb497ab29`; 51 physical lines; owner `dashboard.1`.

**purpose:** Recorded rent state/history reaches GraphQL and Manager, config/kernel wiring assertions; no profitability or readiness claim.

**consumers:** Explicit focused pytest discovery and supporting fixture imports.

**evidence:** Recorded rent state/history reaches GraphQL and Manager, config/kernel wiring assertions; no profitability or readiness claim. All function bodies/assertions inspected; no assertion-only rewrites or current production runs.

**design_boundary:** Test-only fakes/temp journals establish narrow offline contract, never runtime/provider/live evidence.

**costs_removal:** No normal runtime CPU/storage. Keep meaningful negative controls and schema compatibility; remove only when underlying public contract is retired.

## tests/test_run01_instance_identity.py — KEEP

Pinned base sha256 `4e4b7225644e115e46fca8c4c3fd2e6babc91948a88301b70b7f360c808e8aea`; reviewed sha256 `4e4b7225644e115e46fca8c4c3fd2e6babc91948a88301b70b7f360c808e8aea`; 314 physical lines; owner `dashboard.1`.

**purpose:** Synthetic child lock holders, stale identity/revision refusal and stop timeout; restart script stub sandbox, plus owner identity projection. Retain but do not execute service/launcher subset in this review.

**consumers:** Explicit focused pytest discovery and supporting fixture imports.

**evidence:** Synthetic child lock holders, stale identity/revision refusal and stop timeout; restart script stub sandbox, plus owner identity projection. Retain but do not execute service/launcher subset in this review. All function bodies/assertions inspected; no assertion-only rewrites or current production runs.

**design_boundary:** Test-only fakes/temp journals establish narrow offline contract, never runtime/provider/live evidence.

**costs_removal:** No normal runtime CPU/storage. Keep meaningful negative controls and schema compatibility; remove only when underlying public contract is retired.

## tests/test_theorist_removed.py — KEEP

Pinned base sha256 `4706f4cb5ef3d6f5dd776951261018417a27849fdcaeee712e61c22ee1f798be`; reviewed sha256 `4706f4cb5ef3d6f5dd776951261018417a27849fdcaeee712e61c22ee1f798be`; 38 physical lines; owner `dashboard.1`.

**purpose:** Preserves removal of obsolete LLM theorist/rules while data-only postmortem and roster/display remain wired.

**consumers:** Explicit focused pytest discovery and supporting fixture imports.

**evidence:** Preserves removal of obsolete LLM theorist/rules while data-only postmortem and roster/display remain wired. All function bodies/assertions inspected; no assertion-only rewrites or current production runs.

**design_boundary:** Test-only fakes/temp journals establish narrow offline contract, never runtime/provider/live evidence.

**costs_removal:** No normal runtime CPU/storage. Keep meaningful negative controls and schema compatibility; remove only when underlying public contract is retired.

## tests/test_trade_history.py — KEEP

Pinned base sha256 `717b3432b176ef3b68a6f24cb7175d3cf5e1cc44f06642976cd072dd08c3a448`; reviewed sha256 `717b3432b176ef3b68a6f24cb7175d3cf5e1cc44f06642976cd072dd08c3a448`; 492 physical lines; owner `dashboard.5`.

**purpose:** Mixed237-trade pages, strict malicious cursors, membership cancellation/backdated insert/revert, read-only SQL and covering-index plans.

**consumers:** Explicit focused pytest discovery and supporting fixture imports.

**evidence:** Mixed237-trade pages, strict malicious cursors, membership cancellation/backdated insert/revert, read-only SQL and covering-index plans. All function bodies/assertions inspected; no assertion-only rewrites or current production runs.

**design_boundary:** Test-only fakes/temp journals establish narrow offline contract, never runtime/provider/live evidence.

**costs_removal:** No normal runtime CPU/storage. Keep meaningful negative controls and schema compatibility; remove only when underlying public contract is retired.

## trader/api/graphql_schema.py — KEEP

Pinned base sha256 `ec5dba753c7b016391ad36c5d220cdacef6dde259ea2aa6e32ed2c88958022c4`; reviewed sha256 `cd298b3733992f6b10371c07528229eeb46a0b969210c60765abb7093be73d73`; 596 physical lines; owner `dashboard.1`.

**purpose:** Typed query/control contract with worker-thread resolvers and batched vote loader.

**consumers:** server router; frontend live control GraphQL; assistant API consumers; backend integration tests.

**evidence:** Fixed decisions_total composed filters: BTC HOLD+BUY and ETH BUY now BTC directional total1 equals returned list1, pre count2. Mutations call kernel gateway only; all queries offload sync work.

**design_boundary:** SDD25.1 allows GraphQL; legacy rich queries and typed authority distinct from REST read contracts; no new writer.

**costs_removal:** 596 lines typed schema, max1000 rows/default bounded queries. Batched votes avoids per-decision scans but still scans per500 cycles; optional index outside ownership deferred.

## trader/dashboard/__init__.py — KEEP

Pinned base sha256 `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`; reviewed sha256 `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`; 0 physical lines; owner `dashboard.1`.

**purpose:** Dashboard Python package marker.

**consumers:** All trader.dashboard imports.

**evidence:** Empty file is correct package boundary; no obsolete behavior.

**design_boundary:** Intentional package marker, not disconnected component.

**costs_removal:** 0 source lines; removal risks package/import semantics for no material gain.

## trader/dashboard/auth.py — KEEP

Pinned base sha256 `6fcb7ffeb9eabdad36359913b97cec765003c564e80c12d7a7a696b35581b7ae`; reviewed sha256 `6fcb7ffeb9eabdad36359913b97cec765003c564e80c12d7a7a696b35581b7ae`; 138 physical lines; owner `dashboard.1`.

**purpose:** Fail-closed token/header/session authentication with HMAC expiry and same-origin controls.

**consumers:** server.create_app installs Guard; owner_api.bootstrap consumes session identity; tests/test_dashboard_auth.py.

**evidence:** Blank/default token denied, cookie secure/HttpOnly/Strict, origin checked for writes/WebSocket; GET query token remains deliberate script compatibility.

**design_boundary:** SDD26 authenticated narrow owner interface; no trading/control writer.

**costs_removal:** Tiny auth middleware per request; sessions stateless. Removing exposes every mounted read/control route.

## trader/dashboard/current_truth.py — KEEP

Pinned base sha256 `416d870dde445bbc4c37cb2923b51953dc0569638538d9553884cf531fc39dd9`; reviewed sha256 `416d870dde445bbc4c37cb2923b51953dc0569638538d9553884cf531fc39dd9`; 593 physical lines; owner `dashboard.3`.

**purpose:** Shared account, risk, news and equity provenance read models.

**consumers:** owner_api overview/risk/news; GraphQL status/equity/news; server summary/company/WS.

**evidence:** Account completion clock and source age preserved; risk verifies policy digest/constraint consistency; news count/time contradictions unavailable; stale historical values retained with explicit state.

**design_boundary:** SDD25 evidence-grounded explanation and deterministic Risk boundary; local journal reads only.

**costs_removal:** 593 source lines centralize three substantive producer schemas. Do not merge them into route code or erase missing/stale distinctions.

## trader/dashboard/economics.py — KEEP

Pinned base sha256 `0ae5124f048b92ccd86f78dc9039112cb03eb5877054392d17015c170df1c560`; reviewed sha256 `0ae5124f048b92ccd86f78dc9039112cb03eb5877054392d17015c170df1c560`; 144 physical lines; owner `dashboard.3`.

**purpose:** Replay booking evidence and separate venue monetary fields, journal values and derived excursion metrics.

**consumers:** owner_reads.trade_lineage; owner_api.read_realized_today; overview_desk curve/strategies/operations.

**evidence:** journal_pnl checks every receipt uncapped; any corrupt/unknown leg suppresses scalar even if later legs numeric; funding/net economic total never fabricated.

**design_boundary:** SDD15/25 accounting evidence; per-leg fill proof never whole-trade/net authority.

**costs_removal:** Receipt history is intentionally complete; truncating can hide unknown early legs. Shared classifier now also used for Operations close rows.

## trader/dashboard/glacier_truth.py — KEEP

Pinned base sha256 `d3344e24ddb930f8636c2c755c03519a301a4c16e6e7f6ff2b700802d3a33875`; reviewed sha256 `d3344e24ddb930f8636c2c755c03519a301a4c16e6e7f6ff2b700802d3a33875`; 121 physical lines; owner `dashboard.3`.

**purpose:** Verified runtime environment, exact strategy-version authority and mount capacity projections.

**consumers:** bootstrap runtime_truth; strategy_board versioned_strategies; diagnostics filesystems.

**evidence:** Verified snapshot expires at60s; config only establishes disabled or unknown provider state. Version validation/install/probation/approval/governor/economics individually report read errors. Recovery mount cannot borrow root capacity.

**design_boundary:** SDD25 immutable version view and source-specific evidence. ACTIVE registry/governor labels are not entry permission.

**costs_removal:** One complete version walk with per-version verification may grow; optional paging needs separate product/coverage design. No added storage or providers.

## trader/dashboard/overview_desk.py — KEEP

Pinned base sha256 `638c1038b84d98b46e72d789358bbbead0e37b630c4e25e58b41eff012debe66`; reviewed sha256 `654cb7034c82fe438810371e9bba9eb593c3d79ef1e67baa5d248a9669a3b435`; 322 physical lines; owner `dashboard.1`.

**purpose:** Owner Overview/Research/Strategies/Operations compact read projections.

**consumers:** owner_api desk/research-board/strategies-board/operations-log; frontend Overview, ResearchDesk, Strategies, OperationsLog, Diagnostics.

**evidence:** Curve excludes unknown and counts omissions; discovery ranks do not admit; log aggregates scans/control repeats per UTC hour. Fixed close result previously used numeric placeholder despite UNKNOWN receipts.

**design_boundary:** SDD25.3 owner tabs; config referee/handoff states remain explicit disabled; no strategy/order mutation.

**costs_removal:** History curve/strategy-board each query receipts per closed trade; Operations reads1..30 days and aggregates in memory. Optional batch/paging improvement, no measured production bottleneck claimed.

## trader/dashboard/owner_api.py — KEEP

Pinned base sha256 `dd99ffff1c8d3a97e2e2ab17f713cc5be6d14b13b3301af665f4e20e472ec749`; reviewed sha256 `dd99ffff1c8d3a97e2e2ab17f713cc5be6d14b13b3301af665f4e20e472ec749`; 1179 physical lines; owner `dashboard.4`.

**purpose:** Authenticated owner gateway reads, bootstrap, cache, chat/approval transport and static Vite assets.

**consumers:** server.create_app; frontend adapters/live.ts and readContracts.ts; owner_reads install.

**evidence:** Overview isolates source failures, reports source-time freshness, complete valuation and verified protection; cache invalidates filesystem identity/change and never stale-fallback on failure. READ_ONLY_GUI disables chat/controls/provider injections.

**design_boundary:** SDD25/26 transport not authority. Approval forwarding uses existing typed kernel gateway; chat is non-operational. Explicit future lenses and unavailable provider data are intentional.

**costs_removal:** 1179 lines and many projections are broad but contracts differ; optional split has no required benefit. Knowledge retains ≤2MiB/20k-file cache and still fingerprints vault per read. Preserve provenance over TTL shortcut.

## trader/dashboard/owner_reads.py — KEEP

Pinned base sha256 `9a2c11001348b208168af4532bf78f719c32954133d07c77bda812ca0465ca9c`; reviewed sha256 `9a2c11001348b208168af4532bf78f719c32954133d07c77bda812ca0465ca9c`; 1582 physical lines; owner `dashboard.4`.

**purpose:** Exact-id trade/decision/research/strategy evidence, activity, diagnostics, knowledge and flow readers.

**consumers:** owner_api.install; frontend forensic/detail/live-system/knowledge/diagnostics routes.

**evidence:** No inferred links: bounded votes window plus exact cycle ID, exact partial-index latest queries, missing index explicitly unavailable, receipt replays bound to trade; counters distinguish display truncation from absence.

**design_boundary:** SDD25 current contract vs target: registry detail is legacy current-row scope; separate versioned strategy board provides immutable authority. Retired questions/future unavailable fields do not establish defects.

**costs_removal:** 1582 lines worker-thread handlers keep event loop responsive; bounded detail payloads/records. Historical strategy raw journal_economics distinct from exact classified board; any future unification must preserve response semantics.

## trader/dashboard/owner_runtime.py — KEEP

Pinned base sha256 `5cd8a2f1681405603e735d22bc607bd625f64c6bc7709d764dbca74aa7d137bb`; reviewed sha256 `5cd8a2f1681405603e735d22bc607bd625f64c6bc7709d764dbca74aa7d137bb`; 30 physical lines; owner `dashboard.4`.

**purpose:** Read existing Kernel flock/process record/start ticks without creating artifacts.

**consumers:** owner_api heartbeat/overview/bootstrap/system; test_own04_state_truth.

**evidence:** RUNNING requires held existing lock and verified /proc ticks; unlocked/no legacy kernel yields STOPPED; absent/contradictory evidence UNKNOWN.

**design_boundary:** Stored ACTIVE trading permission, process liveness and successful work are distinct evidence contracts.

**costs_removal:** 30 lines bounded existing-file/proc read, no migration/control/process launch. Removing loses honest stopped/unknown distinction.

## trader/dashboard/phase.py — KEEP

Pinned base sha256 `78da1f099f427b4bf157abcd9a45fe490a8f0ee23f0dac034f5b592165b15f9a`; reviewed sha256 `78da1f099f427b4bf157abcd9a45fe490a8f0ee23f0dac034f5b592165b15f9a`; 104 physical lines; owner `dashboard.1`.

**purpose:** Explicit READ_ONLY_GUI or LIVE startup and ASGI authority boundary.

**consumers:** server.create_app/main; owner_api.install and owner gateway; CLI startup_check.

**evidence:** GUI bypasses LIVE readiness intentionally, denies provider legacy reads/WebSocket/actions before execution, permits only parsed GraphQL queries and auth login/logout. LIVE requires readiness/preflight.

**design_boundary:** Current config.yaml432 is READ_ONLY_GUI. Future LIVE prerequisites mandatory, not missing wiring defect.

**costs_removal:** 104 lines single central boundary prevents route-by-route gates. No new approval mechanism needed.

## trader/dashboard/server.py — KEEP

Pinned base sha256 `b9891a7a5d1e1a769f901c30d83ecb9cdf8c5eb4dd3c8d304ddfa26dc8a07e97`; reviewed sha256 `47c30dcddddbc7246e72947f04d566800221ca1a0f7831d8db29fa729ec14e8e`; 1133 physical lines; owner `dashboard.1`.

**purpose:** FastAPI composition, legacy read compatibility, provider enrichment adapters and company cards.

**consumers:** python -m trader.dashboard.server main; existing API/GraphQL/WS clients; current Vite owner surface mounted by owner_api.

**evidence:** Fixed vault-file string prefix accepting knowledge-private sibling or symlink escape; resolved Path containment + is_file now enforce exact vault. All cases authenticated; current GUI mode already blocks this legacy route.

**design_boundary:** SDD25.1 FastAPI serves built Vite; controls typed kernel IPC only. Legacy LIVE/provider paths intentional compatibility and prohibited during this review.

**costs_removal:** 1133 lines; legacy async SQLite routes and org12s cache could block under LIVE load; optional offloading needs measured demand. No removal because public compatibility contracts/tests remain.

## trader/dashboard/tracker.py — KEEP

Pinned base sha256 `9575aa8b7af8d9e34621d215f67619d99dc5103cb4decda2b5a887cfd2843cc7`; reviewed sha256 `9575aa8b7af8d9e34621d215f67619d99dc5103cb4decda2b5a887cfd2843cc7`; 223 physical lines; owner `dashboard.5`.

**purpose:** Strict read-only engineering tracker/NEXT reconciliation.

**consumers:** owner_api installs /api/tracker and /owner-api/v1/tracker; Tracker frontend; retained closure validators.

**evidence:** Unique YAML keys, bounded2MiB reads, Pydantic contracts, exact row/status/dependency/gate counts, single selected work package; changing source read refuses rather than mixed snapshot.

**design_boundary:** SDD25.3 Tracker counts are workflow status, not runtime/trading readiness; no mutation API.

**costs_removal:** 223 lines schema and validation necessary for durable read contract. git revision timeout2s; no DB migration/write.

## trader/dashboard/trade_history.py — KEEP

Pinned base sha256 `36e979ec8de7717ced09a62eb311a2bb55cdc39042bab991c3c71cc2de3d4228`; reviewed sha256 `36e979ec8de7717ced09a62eb311a2bb55cdc39042bab991c3c71cc2de3d4228`; 215 physical lines; owner `dashboard.5`.

**purpose:** Bounded read-only keyset trade pages with membership change detection.

**consumers:** owner_api /trades; frontend Trades pager; bench_trade_history and test_trade_history.

**evidence:** Strict canonical cursors, filter-bound identity, anchor/digest and one read-only transaction preserve no-gap traversal and detect cancellation/backdated membership changes; legacy cursor marked unverifiable.

**design_boundary:** Raw journal columns intentionally passed through and source-labelled, detailed lineage supplies classification/provenance. No economic inference or fill authority.

**costs_removal:** ≤200 rows page, omitted bulky excursion, index-stream digest. Deep membership and counts scale with consumed range; complete traversal quadratic by design, documented benchmark evidence. Do not remove checks for superficially simpler OFFSET.

## trader/dashboard/valuation.py — KEEP

Pinned base sha256 `8fac9194df2eca66ce0432c73989fc8fde18735084bb55d34f4883adc6e70d43`; reviewed sha256 `8fac9194df2eca66ce0432c73989fc8fde18735084bb55d34f4883adc6e70d43`; 156 physical lines; owner `dashboard.1`.

**purpose:** Pure per-trade USDT ticker-based unrealized P&L estimate.

**consumers:** owner_api valuation/marks; server summary valuation.

**evidence:** Every open position finite/fresh/settlement-compatible else total unavailable; zero quantity real0; ticker source vs receipt times checked separately; duplicate symbols stay per trade; overflow unavailable.

**design_boundary:** Explicit estimate excludes unproven fees/funding and does not call venue, price source last/close not exchange mark.

**costs_removal:** 156 lines pure O(positions) validation; shared across legacy/current interfaces. No storage writes. Removing creates duplicated weaker valuation.
