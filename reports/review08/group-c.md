# Review08 Portfolio — group C evidence

Source: `ef9fca839c23d48198c5f2361bbf3623d94266fa`. Every assigned file was read in full: **10 files, 2,478 physical lines; 8 KEEP / 2 SIMPLIFY / 0 MERGE / 0 REMOVE**. Exact hashes, bytes, purpose, necessity, consumers, replay, duplication, cost, SDD assessment and removal consequences are in [group-c.json](group-c.json). The abandoned research assignment contributes no evidence or classification to this review.

No source correction, removal or merge was justified in this group. SIMPLIFY recommendations preserve existing contracts and do not assert unused code or measured performance savings.

## Actual capabilities

| Component | Capability and connection | Practical limit |
|---|---|---|
| `opportunity_live` | **IMPLEMENTED AND CONNECTED:** normal `current.freeze` produces exact immutable context sources and final decision envelopes; allocator/economics verify their bindings. Source chronology, version/fingerprint/compiler identity, analyst evidence, book projection and required freshness are checked. | Synthetic success proves boundary mechanics. Missing costs or evidence legitimately block; current futures directional scope is narrower than the multi-asset objective. A source hash is integrity, not venue authenticity. |
| `candidate_bridge` | **IMPLEMENTED AND CONNECTED:** reads existing Factory validation/probation/capacity without granting first-live authority; produces allocator candidates; freezes/replays lineage for common-factor consumers. | **MISSING** calibrated allocation maturity: `ALLOCATION_AUTHORITY_ADAPTERS` is empty. Account/capacity/size/Risk permission cannot be inferred from a validation label or synthetic adapter. |
| `comparison` | **IMPLEMENTED BUT DISCONNECTED from normal display delivery:** useful pure explanation of allocator reasons, CASH, component confidence and analyst support/opposition; R2 historical ties and R3 tie-CASH preserved. | No normal runtime/dashboard caller found in scoped consumer searches. The allocator itself remains connected through the checkpoint path. The adapter trusts its frozen Proposal caller; it is not a second authentication or selection authority. |
| `opportunity_context_shadow.capture` | **IMPLEMENTED AND CONNECTED:** `portfolio.current` imports this bounded current-evidence reader. It also supports a manual shadow CLI and immutable publication reader. | CLI preview and runtime calls have different scopes/configuration. Removing the script as an unused tool would break runtime imports. |
| Context/economics publication | Pure content-addressed `persist`/`load` and source-adapter readers exist and have replay checks. | No normal application caller of `opportunity_live.persist` found; manual shadow publishes files, while normal `current.freeze` constructs in-memory sources. Reader presence alone does not demonstrate an autonomous external publication producer. |
| DEC02 historical programs | **OBSOLETE as current operational executables; KEEP as retained provenance.** Bundle manifest explicitly owns their bytes. | `reconcile.py` is a one-time tracker/STATE/NEXT/manifest writer pinned to old HEAD. `control-validation.py` asserts old snapshots and rewrites its proof output. `replay-proof.py` writes historical proof JSON. None was executed. |

The real normal connection is `Kernel._portfolio_checkpoint` → `current.checkpoint/freeze` → bounded source capture + `opportunity_live.produce` → existing Factory/economics bridge → allocator/event-gated Consumer. Kernel explicitly reports `execution_routed=False`; this group does not establish autonomous allocation execution. The retained input sources and allocator result remain useful even where the optional comparison display is not published.

## Findings and simplifications

No unmistakable defect requiring a safe source correction was established in these ten files. Two simplification recommendations have concrete boundaries:

1. `candidate_bridge.py`: preserve the public facade, recorded query answers, current reread versus historical replay distinction, exact source hashes and independent authorities. Reduce repeated authority reads/serialized evidence only through existing representations and after byte/replay compatibility is demonstrated. Its maximum recorded read is 1,000 rows / 2 MiB and bridge body is bounded to 8 MiB; lineage is bounded to 64 versions. No production size or CPU measurements justify a claimed saving.
2. `opportunity_context_shadow.py`: keep CLI behavior, but consider relocating its runtime capture/read helpers into the existing Portfolio owner so normal `current` no longer imports a script. Existing `current` and `source_adapter` must agree on source cut/receipt compatibility. This is not permission to add another reader service, scheduler or schema.

The shadow summary hardcodes `expected_economics='UNAVAILABLE'`, while individual context entries retain their actual status. This matches the current empty production calibration registry but cannot serve as a future calibration/aggregate authority. It is a reporting limitation, not evidence that a positive calibrated candidate is currently being suppressed. No economic authority or receipt format was changed.

SDD §§14.5–14.6 and 15–16 justify the exact-version/evidence/economic/lineage boundaries. Context, outer source envelope, economics receipt and final decision envelope are related but serve distinct replay and dependency roles. Merging their persisted schemas would risk historical identity and circular economics input. The three test suites cover distinct authority, context and explanation contracts; consolidation has no demonstrated value.

## Verification and deferred dependencies

Command:

```text
luffy-pytest -q tests/test_candidate_bridge.py tests/test_opportunity_live_integration.py tests/test_dec02_comparison.py --junitxml=reports/review08/group-c-pytest.xml
```

**75 passed in 20.51 seconds**, exit 0. [Log](group-c-pytest.log), [JUnit](group-c-pytest.xml). Managed runner receipt: `/mnt/luffy-recovery/recovery/managed-pytest-sarmad/pytest-run-y9yh8t0v`. These are synthetic temporary database and test-only model fixtures, including subprocess replay and source-write-denial checks. No new test infrastructure, real-data profitability run, provider/venue call or historical proof execution occurred.

Read and reused `/mnt/luffy-data/sol1-operational-data-lifecycle/reports/operational-lifecycle-complete/REPORT.md`. Its private Consumer codec/lifecycle evidence is an offline integration package, not deployed behavior at this source. Owner-supplied private lifecycle branch `147fae0` was not changed or re-reviewed. Retained evaluation/checkpoint/financial/recovery evidence must remain intact; integrate lifecycle reader/codec changes together after the authorized later integration gate. No production database was read or modified here.

Source call connectivity is not an observation of a running profitable capability. No owner decision is needed to retain the reviewed contracts; choices to connect the display, relocate the capture helper or register calibrated allocation/economic authority remain separately scoped work. No protection/integrity urgency was identified in this group.
