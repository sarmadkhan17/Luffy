# Unified Track A, Track B and Quant Pipeline — 2026-11-03

Working tree: `/home/sarmad/luffy-unified`, branch `work/unified-track-ab-20261103`.
Base: `09d5593726acfb8f33ee8808c428fcc1e6517d4a`; includes `743d8ef1` and the full preceding Quant Pipeline history. Track A and Track B changes remain uncommitted. No deployment, service startup, cron changes or live database operations were performed.

## Preservation and integration

Inspected both named commits and the preceding branch changes. The sole collision with Track A was Overview.tsx's classifier. The shared server/frontend classifier now preserves Claude's general `ineligible_*` treatment. All 31 original Track A files remain byte-identical in `/mnt/luffy-data/LUFFY`; their healthy-disk backup is `/home/sarmad/luffy-track-a-backup-20261103`. In the unified tree, 27 remain byte-identical; four extend the vector engine or classification contract. Every existing Task 2 function/class has an identical AST. Claude's original worktree remains clean.

`unified.patch` contains all 46 changed/new source and test files relative to the base, including originally untracked Track A files. Reverse-application checking passed. `manifest.json` records the base, patch digest and every changed-file digest. No new commit or push was made.

## Track B implementation

- DerivFeed applies connection settings before the initialized-schema shortcut: writable opens require WAL, foreign_keys=ON, synchronous=FULL, temp_store=MEMORY, cache_size=-8192 (8 MiB), busy_timeout=1000 ms. Read-only opens retain their existing journal mode and enforce query_only; tests verify no file changes.
- Declared fetches use a standalone stdlib-only script instead of importing the Attention/NumPy graph in every child. Four concurrent fetches, six-second total parent deadlines, four-second socket timeouts and 64 KiB response bounds are retained. The compatibility --fetch command remains available.
- observe.sh retains Claude's FD 9 closure and adds the requested five-second forced-kill grace for attention learning and investigation. Declared/accounting retain their existing 15-second grace.
- Investigation retention and capacity maintenance commit before ingestion. Expired cases still protected by retained outcome, pending counterfactual or population references are preserved. Added case_inputs_input_v1, cases_terminal_time_v1 and allocation_decisions_time_v1.
- Frozen memory source archives use compressed, SHA-256-verified shared content plus per-context references. Legacy contexts compact in bounded batches of 16. Reads, reasoning, predictive consumers and exports expand the exact public context; hashes/context IDs and self-contained export semantics are preserved. Archive garbage collection follows retained context references.
- Referee consistency and null_dependence use modular signal indexing with fused compiled PF/count accumulation. No per-draw rolled signal arrays, masks or Python trade lists. fastmath=False; separate walker/PF equity rounding paths, first-bar masking, long precedence, dust, censoring and NaN behavior are preserved.
- Claude already corrected CLAUDE.md to the owner's 25 Oct referee=true/handoff=true decision; retained unchanged. httpx 0.28.1 and Numba 0.68.1 were already installed. No installation was necessary.

## Validation

1,235 DISTINCT selected Python cases passed across initial groups and targeted reruns, not a single monolithic run. `test-groups.json` lists the exact 62 files; collection independently confirmed the total in `collection.txt`.

| Covered group | Distinct cases |
| --- | ---: |
| Track A | 596 |
| Track B, memory, derivatives and statistics | 247 |
| Quant Pipeline, owner_reads and acc03 | 351 |
| Additional research-control fixtures | 14 |
| Additional memory and predictive-receipt consumers | 27 |

Initial failures were repaired without weakening product policy: a stale watermark test expected immediate backlog draining despite the existing one-batch/minute throttle; research runner fixtures lacked temporary database paths; a discovery-receipt fixture assumed the owner's runtime research flags were disabled. Tests now use local policies and temporary paths. Final watermark file: 17 passed; final runner file: 30 passed; corrected control/integration cases and additional consumers passed. Earlier successful cases were retained rather than repeatedly rerunning unchanged suites.

Frontend: 265 tests across 25 files passed; TypeScript typecheck and bash syntax checks passed (exit 0). Frontend dependencies and execution were isolated under /tmp.

The optional full predictive_research_bridge.py expansion was stopped after its three completed parameterized acceptance cases passed, because each case reconstructs a large retained candle fixture. Remaining cases of that optional file and the full repository suite are not claimed as validated. Changed context expansion also passed the memory, export/replay and predictive-receipt consumer tests above.

Every SQLite connection was guarded against paths outside /tmp, including child Python processes. Legacy :memory: test fixtures were redirected to private temporary files. The guard blocked accidental default-path attempts before SQLite opened anything. Each pytest directory was removed after its run, including interrupted validation. /tmp has approximately 13 GB available again.

## Synthetic benchmark

12,000 bars, 400 rotations; five timed repetitions after JIT warmup. All 400 PF results exactly equal.

| Implementation | Median seconds | Peak traced Python bytes |
| --- | ---: | ---: |
| Rolled arrays + compiled walk + PF | 0.142210085 | 132208 |
| Modular fused walk/PF | 0.019879658 | 10912 |

Measured speedup: 7.1535x; traced Python peak reduction: 91.75%. This is a synthetic rotation benchmark, not a production-cycle latency claim. Script and full numbers accompany this report.

## Reproduction

Use the existing virtualenv to run `validate.py` with selected test paths from `test-groups.json`. The driver creates a temporary SQLite guard and fixture directory under /tmp and cleans them afterward. If needed, it temporarily links the existing virtualenv into the healthy working tree for tests that launch child Python commands, then removes that link. No live database access is permitted by the driver.
