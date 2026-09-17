# Candidate dossier and thesis contract — report

Spec: `docs/superpowers/specs/2026-09-15-demo-first-intelligence.md`, Priority 1,
"First deliverable". Discovery-only research tooling. No runtime, config, gate or
ledger change; no commit; no network; no held-out or gate payload read.

## Files

| file | sha256 |
|---|---|
| `trader/research/thesis.py` | `2d1e7648a0d73e95c1ead964bbd7abb868aefab5b4184602ab6f3e8620d95636` |
| `scripts/candidate_dossier.py` | `11abadf830e5061cb96ccda1c41224f777b81988a65167e060335073de1319c0` |
| `tests/test_candidate_dossier.py` | `8ab3dda33f589a334489f8de5e99dd4374987288eae3b211c731fbf193c3acc7` |

- `thesis.py`: pure thesis contract (schema, premises, rivals, 2–4 predictions in
  the existing grammar, tautology/restatement/duplicate/availability refusals,
  verdict limited to `research_only` / `waiting_for_evidence`).
- `candidate_dossier.py`: reads one survivor through a `mode=ro` URI,
  `PRAGMA query_only=ON`, one rolled-back read transaction and a SQLite
  authorizer that permits only an explicit discovery column allowlist. It denies
  `research_tests`, `research_candidates`, `research_combos.result`,
  `research_controls.detail` and held-out slice counts. Output goes to an empty
  directory with exclusive-create writes and a hashed manifest.

## Astra review finding: prose promoted by a true number (fixed)

**Before:** an `observed` premise was `supported_observation` when its numeric
claim held and its free `text` did not match the causal/actor regexes. So any
unrelated sentence, or a causal paraphrase without the listed words ("since",
"make", "let"), became "supported".

**After** (`thesis.py:_premise`, `canonical_observation`):

- The structured claim `{evidence_id, op, value}` is checked mechanically and
  recorded as `observation = {evidence_id, op, value, recorded_value, verified}`.
- When it holds, the validator writes `supported_statement` itself,
  `"<evidence_id> <op> <json value> (recorded: <json recorded value>)"`.
  This is the only text the contract vouches for.
- The author's `text` is now optional for observed premises. It is reported
  separately as `narrative = {text, status, flags}`. Its status is `unverified`
  unless the stripped text exactly equals `supported_statement` (`canonical`).
  For proposed premises the status is `proposed_unverified`.
- A premise is `supported_observation` only when its claim is verified, all
  cited ids are recognized, and the narrative is absent or exactly canonical.
  If the claim is true but the narrative differs, the premise is `unsupported`.
  The verified `observation` and `supported_statement` stay recorded, so the
  structured fact is still usable.
- The causal/actor regexes are now only advisory `narrative.flags`. They never
  change a status. A canonical statement whose evidence id contains a regex word
  (`E.combo.reason`) is still supported, and its flags are cleared.
- `dossier.md` prints the validator's statement and the narrative separately
  ("narrative (unverified, never verified as prose)"). The scaffold's observed
  text placeholder now says to leave it out or use the canonical statement.
  `TOOL_VERSION` 1 → 2. The identity and evidence hashes are unchanged.

New or tightened tests:

- `test_true_claim_never_promotes_its_narrative`, 6 cases. Each has a true
  `scored_symbols == 19` claim plus one of: an unrelated non-causal sentence;
  two causal paraphrases with no regex term; a faithful-sounding paraphrase; the
  canonical text with extra words; or canonical-looking text with another
  operator. Each case must be `unsupported`, with a verified observation and the
  canonical statement kept, validated by `validate` and rendered through the CLI.
- `test_exact_canonical_text_is_the_only_supported_wording`.
- `test_supported_premise` now checks the observation structure.
- The earlier `good_thesis` P2 paraphrase is now structure-only, and that same
  paraphrase is one of the adversarial cases. This makes the test stricter, not
  weaker.

## Tests

- `tests/test_candidate_dossier.py`: **49 passed** (42 before the fix).
- Existing cognition suite (attention, contracts, coverage audit, history,
  replay), run once after the final code edits: **114 passed**.

## Worked discovery artifact (candidate `55243573515adc3b`)

- **Current:** `/tmp/luffy-candidate-dossier-20260915-v2` (tool_version 2, read
  from `data/luffy.db` read-only)
  - `dossier.json` `167dc85f75a855332047ad3e4689169ebaa43a52cc174d676faccc21e44675aa`
  - `dossier.md` `9c44e136a4682558253c4818ce20317b8f3a421c18ff6a85e621df3f6a55c6dc`
  - `thesis.scaffold.json` `c8fd36fbece8e264a55e8eb66af95529fafb2269287d84dcac5aadc069d53091`
  - `manifest.json` `360940e9c7b7a1e64972b657c7597c78c72d74da7ddfd0ba594d116427e0488f`
  - identity_sha256 `37f56f51282ba5f7cf0dc818320812a72b524955ce617a4084f08db67b6114e5`,
    evidence_sha256 `eabe391f0463b73ac0773288bba8dfe09c4ba68efb188f5014691a5824e4fdfa`
- **Superseded, kept unmodified:** `/tmp/luffy-candidate-dossier-20260915`
  (tool_version 1, before the fix). Its identity and evidence hashes are the
  same, and its manifest file hashes still verify. It differs only in
  `tool_version` and the scaffold's observed-text placeholder.
- Discovery-only checks: manifest hashes verify; 18 queries, all on allowlisted
  columns. No held-out count key appears in the output. `research_tests` and
  `reason_passed` appear only in the `not_read` list and the gate description.

Discovery facts, all read from the ledger: 4h/trail, parts `r_alts_z96>p75` +
`r_vix_ret30>p75`. There are 758 trades on 19 symbols, with discovery
consistency_p 5.16e-17 (pooled, not dependence-corrected). Median PF is 2.273,
total 215.66%, max DD 12.413%. Removing a part gives p 1.45e-3 (alts) or
3.73e-6 (vix). The ledger has 2520 prune / 57 grow / 117 survivor combinations
at 4h/trail. The `ref:alts|ref:vix` window control is powered (p 0.0024).

## Verdict and admission blockers

**research_only**, because no thesis was supplied. Even a well-formed thesis
could only reach `waiting_for_evidence`. Blockers:

1. Referee stop: `research.referee=false` pending the gate decision.
2. Gate 2 (thesis prediction test) is not implemented.
3. No thesis prediction is accepted for testing yet. The discovery ablation
   already answers the with/without comparison, so it is not a novel consequence.
4. The shared-market-regime rival applies strongly: both parts are market-wide
   references, so one episode is counted once per symbol. Provenance rivals also
   apply: the `alts` index membership is unrecorded, and one daily VIX value
   spans several 4h bars.

`demo_eligible=false`, `gate_writes=none`. No held-out number was calculated or
read.
