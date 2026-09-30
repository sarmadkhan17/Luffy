# LUFFY-INVESTIGATION-RESEARCH-FAMILY-R1

Date: 2026-09-30 · Branch: `intelligence-spine-r1` (uncommitted, on top of INTELLIGENCE-SPINE-R1)
Verdict: **PASS** in deterministic tests · Runtime: **NOT_DEPLOYED** · Trading behaviour changed: **NO**
Predictive edge established: **NO**

## The completed spine (one tested case, every edge a stored id)

```
closed-bar candle versions (Attention scan input_versions)
 → WorldModel (model_id, record_id; volume_anomaly observation_id)
 → Attention scan row (scan_id, rank, selection reason)
 → allocation decision (decision_id)
 → investigation.v2 volume_anomaly case (investigation_id, episode_id, state_id, catalog_id)
 → registration (initial update id, opportunity-context id)            [intelligence-trace.v1]
 → question  investigation-volume-anomaly-question.v1  (binds trace_id + registration context id)
 → plan      …-plan.v1       (question_id + question sha256)
 → evidence  …-evidence.v1   (plan_id + plan sha256; update_event_id; outcome_evidence_id)
 → result    …-result.v1     (evidence_id + evidence sha256; predicates_id)
 → run       …-run.v1        (ids of Q/P/E/R)
 → Research Bank  …-bank-object.v1 (id + sha256 of Q/P/E/R/run; trace_id)
```

`intelligence_trace.view()` returns all 11 stages `AVAILABLE`, resolved from the family's
own re-verified records, which bind back to the immutable registration-time trace by
`trace_id` and registration-context id
(`test_full_trace_source_to_research_bank_uses_actual_stored_ids`).

## Family: `investigation_volume_anomaly` (`trader/observability/investigation_research.py`)

| Layer | What it does |
|---|---|
| Source | Existing persisted investigation.v2 `volume_anomaly` case only. Reuses its frozen protocol: `catalog_id`, threshold 2.0 (`I.CATALOG`), N=20, H=5, alternatives same_direction / normalization / opposite_direction. Other investigation families are refused. |
| Question | The case question verbatim: "Does unusual volume persist, return toward baseline, or reverse?" Bound to investigation_id, state_id, catalog_id, case payload sha256, registration update and context ids/sha256, WorldModel model/record ids and Attention scan/allocation ids (copied from the verified context), and trace_id. |
| Plan | Per registered path: routes only the verified update chain (terminal OutcomeEvidence, assessment) and the exact target inputs. `participant_cause` is UNAVAILABLE (`participant_taker_buy_source_not_acquired`); the ACQUIRE recommendation stays future-only, with no network. |
| Evidence | Verifies before freezing: the investigation, episode and state ids are recomputed; the target keys, deadline and sign follow the protocol; **every** stored update is replayed through `investigation.measure` + `advance` from retained inputs and must match exactly; the terminal state must match; the registration context must replay (`replay_context`); the trace must verify. It freezes the case, the full update chain and the exact target input payloads, so the record verifies on its own. |
| Falsifier | `PREDICATES` (this family only, frozen, `predicates_id`). signed = score × sign; same_direction ≥ +2.0, normalization strictly between, opposite_direction ≤ −2.0. The outcome must agree with the stored terminal assessment (compatible → SUPPORTED, contradicted → REFUTED), otherwise the run is refused. No new test or threshold. |
| Result | Measured: result SUPPORTED with `supported_path`; the other two paths are REFUTED. Unresolved / not_testable: INCONCLUSIVE, every path NOT_ASSESSED. Every result carries: "SUPPORTED means only that the frozen registered observable path was supported by its exact forward measurement…", `predictive_edge_established: false`, `trading_authority: NONE`. |
| Research Bank | research-bank-object.v1 field layout (question, sources, extracted_claims, supporting/contradictory evidence, experiments, result status/reason, limitations, next_questions, cost), plus exact links and trace_id. Supporting/contradictory evidence is CLASSIFIED only when measured. Recall is context_only; no suppression, cooldown or usefulness. |
| Run | One transaction for Q → P → E → R → run → bank. Identity excludes recording time, so a retry or restart returns duplicates; a conflicting record is refused; a refusal writes nothing. `research_pass(max_cases)` is the bounded shadow entry point, and it runs from the CLI only with `--research-max-cases N` (default off). |

### Deviation (ACCEPTED by owner 2026-09-30)

The owner asked for "the existing Research Bank object… no parallel bank/object type". The
records use the existing record roles, the research-bank-object.v1 field layout and the
identity / insert / duplicate / conflict semantics. They are stored under family-scoped
schema names in one append-only table (`investigation_research_records`, with no-update and
no-delete triggers) in the investigation ledger. I did not put them in the decay research
tables, for two reasons:

1. The decay verifiers read `research_runs`, `research_evidence` and
   `research_bank_objects` without a kind filter, so foreign rows would enter the decay
   chains.
2. `research_questions` keys by an integer journal `source_event_id` that an investigation
   does not have.

The accepted `strategy_health_unreadable` family set the same precedent. The
`research_families` health dispatch is unchanged.

## Tests (focused)

`tests/test_investigation_research_family.py`: 15 passed.
- Measured cases: same_direction, normalization, opposite_direction.
- Question/plan bindings to exact ids.
- Unresolved → INCONCLUSIVE; not_testable (`missing_data_expired`) → INCONCLUSIVE. Neither is REFUTED.
- Refused and nothing written: tampered investigation (`state_identity_mismatch`), tampered terminal update and wrong target version (`outcome_evidence_mismatch`), another investigation family.
- Stored records are immutable, and a forged stored record is refused on read.
- Retry, restart and `research_pass` are idempotent.
- Research Bank links match the exact stored records by id and sha256.
- Full 11-stage trace.
- Attention output and trading imports are unchanged.

`tests/test_intelligence_spine.py`: 26 passed (updated for the new `registration` stage and
the PENDING research stages).

Focused regression (investigation, opportunity context, research bank / dispatch / shadow,
research cost, attention/world): 942 passed, 1 failed. The failure
(`test_research_shadow_isolation.py::test_harness_modules_satisfy_the_cognition_import_contract`)
is one of the 11 failures already present at base commit `1eb00e1`.

## Limits

- Tests only: nothing has run on production data. Runtime and ledger growth are unmeasured
  (each evidence record freezes the case, the update chain and the target inputs; it is
  bounded by MAX_CASE_UPDATES but not otherwise capped).
- Only volume_anomaly cases have a research family.
- Research records are not removed when the investigation ledger prunes cases after 30 days.
  They verify from their frozen content, but storage policy is still an open owner item.
- One case's SUPPORTED is one frozen observation's outcome. It is not evidence of an edge
  across cases.
