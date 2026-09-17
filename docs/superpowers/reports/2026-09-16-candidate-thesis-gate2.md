# Candidate thesis + gate-2 design — final offline report

Written 2026-09-16. Research only. **No DB read, no network, no held-out
number, no runtime/config/ledger change, no commit.** No error budget was
spent and no gate was written. The frozen dossier
`/tmp/luffy-candidate-dossier-20260915-v2` and the design brief
`/tmp/luffy-thesis-brief-20260916.md` are unmodified.

## 1. What was produced

| path | sha256 |
|---|---|
| `docs/superpowers/artifacts/2026-09-16-candidate-55243573515adc3b-v2/thesis.input.json` | `b3a242d0e05285e94feea1b06a347341310113830628181aef8e6683edd80123` |
| `docs/superpowers/artifacts/2026-09-16-candidate-55243573515adc3b/validation.json` | `8811794958a4ea0597b9ce9d4b12536f557bb210aeb7300b16d7270bc5119877` |
| `docs/superpowers/artifacts/2026-09-16-candidate-55243573515adc3b/dossier.validated.md` | `c79430b4a7c215c097a62c6ceb63e17afad6f7de97d8920761ab5f2f977ea1d9` |
| `docs/superpowers/artifacts/2026-09-16-candidate-55243573515adc3b-v2/manifest.json` | `cd72298726604f93f8cd5c43b742e8208afd121ca05869d751a4403eaab7e186` |
| `docs/superpowers/specs/2026-09-16-gate2-prediction-test.md` | `a3e5190b0ecc03aeae6af5e8bbeeeeabe7d15cc39624d95a7663dfd55082d6e7` |
| `scripts/thesis_validate_frozen.py` (corrected offline helper) | `934931277e660bea56db1f824e0e9d901423c5aafe284901dd9497dd5e793004` |

Pinned identity: `candidate_hash 55243573515adc3b`,
`identity_sha256 37f56f51282ba5f7cf0dc818320812a72b524955ce617a4084f08db67b6114e5`,
`evidence_sha256 eabe391f0463b73ac0773288bba8dfe09c4ba68efb188f5014691a5824e4fdfa`.
`thesis_sha256` (canonical JSON, computed by the contract)
`25fe401f6be653b1dfb355f74091ff179237b743517f2d11cfc33144c1357b4d`;
`thesis_input_sha256` (the bytes, hashed before validation)
`b3a242d0e05285e94feea1b06a347341310113830628181aef8e6683edd80123`.

## 2. Validation status

**`well_formed_untested` → verdict `waiting_for_evidence` → certified.**
Zero errors, zero review items. Both predictions `accepted_for_testing`; all
nine observed premises `supported_observation`; all three proposed premises
`proposed_unverified`, as the contract requires.

`waiting_for_evidence` is the **best status this contract can reach**. It
means the thesis is well formed, not that anything about it is true.

### The thesis, in one line each

- mechanism `local_trend_pullback`, **proposed and unverified**: the
  market-wide alts/VIX gate may mark intervals of persistent directional
  crypto drift under a divergent volatility backdrop; within that unchanged
  trade population two per-symbol entry-bar states may separately mark a
  higher mean R.
- **H1** `ret(6) * ret(24) < 0` — mean_r `>` complement.
- **H2** `pct_rank(efficiency_ratio(30), 200) > 0.5` — mean_r `>` complement.
- rivals R1 `shared_market_regime`, R2 `selection_from_many_variants`,
  R3/R4 `data_provenance` (alts, vix), R5 `other` — the local-trend-only rival.

### Stated limitations, carried in the thesis itself

- **H1 does not establish a signal-aligned pullback.** `ret(6) * ret(24) < 0`
  at 4h says the last 24h return has the opposite sign to the last 4d return.
  It is an **unsigned** shape: it is not compared with the trade's own long or
  short leg, and no direction alignment is claimed. P2 says so explicitly.
- **No causal VIX contribution is asserted.** The mechanism statement says so
  in terms. Nothing here separates "VIX divergence contributes" from "the
  gate's population happens to be these bars".
- **H2 measures only** whether prior 5-day trend efficiency stands above its
  own ~33-day median (`pct_rank(·, 200)` at 4h).
- **R5 is distinguished by nothing in this thesis.** Both conditions may mark
  a mean-R difference that holds across the whole 4h population regardless of
  the gate. A contrast taken inside this rule's trades cannot separate that,
  and R5's own text says so rather than implying a test exists.

### Novelty — what is and is not claimed

Both predictions are **conditional-outcome comparisons within the unchanged
trade population**. Neither is an entry tautology (the validator's `_relate`
check returns `unknown` on both legs for both conditions — neither touches
`ref('alts', zscore(close, 96))` or `ref('vix', ret(30))`), and neither
restates an ablation: the dossier's `already_seen` list covers each part's
`p`, `total_pct`, `drop_p` and `drop_pct`, which are *rule-level* with/without
comparisons, not outcome partitions.

**Novelty is limited to the supplied discovery dossier.** No claim is made
that these conditions are unseen across every search this repo has run. The
contract itself says so: *"not proven: only syntactic restatements,
tautologies, duplicates and unavailable observables are detected"*. R2 records
that both conditions were written after reading the dossier.

**No outcome was tuned or evaluated.** No held-out bar, no trade, no R
multiple and no p-value was computed for either prediction.

## 3. How it was validated — and why a new helper exists

`scripts/candidate_dossier.py` builds its thesis context from a read-only
SQLite read and requires `--db`. This task forbids a ledger read, so
`scripts/thesis_validate_frozen.py` rebuilds the same context **from the
frozen dossier directory** and hands it to the unchanged contract. It does not
re-implement or weaken anything:

- `trader.research.thesis.validate` / `.verdict` are called as they are.
- the thesis bytes are parsed by `candidate_dossier.load_thesis` — the same
  strict loader (duplicate JSON keys and non-finite numbers are refusals).
- no database is opened and no DB is spoofed; there is no sqlite import.

**Freeze order.** The thesis bytes were hashed first, copied verbatim to
`thesis.input.json`, then validated. Output goes to a directory that must be
**empty**, every file written with `open(..., "x")`. Nothing is overwritten.
A rejected thesis would be preserved and reported, never patched.

**Six context proofs, all passing** (in `validation.json`):

1. `sha256_json(frozen identity)` reproduces the recorded `identity_sha256`.
2. `sha256_json(frozen evidence list)` reproduces the recorded
   `evidence_sha256`.
3. the frozen rule's parts re-parse in the live DSL.
4. re-derived `data_requires` equals the frozen `['ohlcv','ref:alts','ref:vix']`.
5. the derived window key equals the frozen `ref:alts|ref:vix`.
6. a window control is recorded for that window.

Proofs 1 and 2 are the load-bearing ones: those two hashes are exactly what a
thesis pins, so reproducing them is what makes the rebuilt context the frozen
context rather than a lookalike.

**Where the offline context is thinner, it fails closed.** A frozen dossier
records the candidate's own window control only; a live context holds every
window at the horizon, where a recorded-but-unpowered window is a hard refusal
and an unrecorded one is only a review item. So the helper refuses to
*certify* any run in which a prediction's derived window key is absent from
the frozen map. Both conditions here are ohlcv-only, so both land on
`ref:alts|ref:vix` and the run is certified.

## 4. Tests actually run in the final pass

| suite | result |
|---|---|
| `tests/test_candidate_dossier.py tests/test_thesis_validate_frozen.py` | **72 passed** |

The earlier **101 passed** result predates the final helper edits and is not
claimed as final validation evidence. No broader suite was run in this pass.

Offline artifact checks on the new helper (all in `/tmp/tvf-neg`, nothing in
the repo touched):

| probe | result |
|---|---|
| one evidence value edited in a **copy** of the frozen dossier | `refused: frozen dossier does not verify: ['dossier.json']`, exit 2, no output written |
| thesis with a wrong `identity_sha256` and a false claim (`trades == 757`) | `rejected` / `research_only`; errors `identity_mismatch` and `observation_contradicts_evidence` (`E.combo.trades is 758, not == 757`); input preserved verbatim |
| re-running into the populated artifact directory | `refused: output directory is not empty`, exit 2 |
| H2 swapped for `ref("dxy", ret(30)) > 0.02` | window `ref:alts|ref:dxy|ref:vix`, **`certified: False`**, `offline_context_insufficient` recorded, status drops to `needs_review` |

LORD++ levels quoted in the spec were recomputed from `trader/research/fdr.py`
offline (no ledger read): t=1 `2.502e-03`, t=2 `5.442e-04`, t=10 `1.823e-04`.

## 5. Correction to the brief: config membership is not historical membership

The brief (`/tmp/luffy-thesis-brief-20260916.md` §1) says the `alts` provenance
rival is *"answered by source: yes, 18/19"*, from `config.yaml
references.alts_members`. **That is an overclaim and this report corrects it.**
The brief itself is preserved unedited.

Two separate problems, both read from source, not from config:

1. **Current membership says nothing about historical membership.** The list
   read today is the list in force today. Nothing records what it was during
   the discovery span (which ends 2025-03-08T16:48Z), and `research_combos`
   stores no membership. So "18 of 19 discovery symbols are members" is a
   statement about 2026-09, not about the bars the rule was scored on.

2. **Worse: the stored series is not point-in-time at all.**
   `ref_sources.refresh_all` runs
   `alts_index({s: feed.cached_ohlcv(s, "4h", limit=40000) for s in members})`
   — it recomputes the **whole history** from the member list in force at that
   run — and `RefStore.save` writes it with `INSERT OR REPLACE`. Every
   historical `alts` bar in `data/candles.db` therefore reflects the *latest*
   membership, applied backwards. The index is a today's-list reconstruction
   of the past, not a contemporaneous index.

The thesis's R3 rival is written to this corrected standard: it says the
member list is unrecorded in the ledger, that the recorder rebuilds the series
from whatever list is in force at its last run, and that neither the
membership nor its history is established by this dossier. It does **not**
claim 18/19.

(No `config.yaml` read was performed for this report; the correction rests on
`trader/data/ref_sources.py:194`, `trader/data/references.py:104`, and on what
the brief itself states its source to be.)

## 6. Gate-2 design — what the spec settles

`docs/superpowers/specs/2026-09-16-gate2-prediction-test.md`, **design only**.
Highlights, each of which is a decision the code does not currently make:

- **Post-partition (P1)**: split `walk_table`'s realized trades by the
  condition at `entry_i`. Re-entry (P2) is rejected — it silently becomes a
  different strategy.
- **`entry_i` timing confirmed from source**: `_trade` fills at `closes[i]`,
  so a condition evaluated at bar `i` uses exactly what had closed when the
  fill price was set. No fill-bar leakage.
- **Unknown is a third group.** `dsl.evaluate_bool` ends in
  `v.fillna(False)` — an undefined bar reads as `False` and lands in the
  complement. For H2 that would file 230 warmup bars per symbol as
  "below-median efficiency". Gate 2 must use a `mask_with_unknown` that tracks
  definedness separately, strict (every leaf finite) in the first
  implementation.
- **Signed mean-R contrast** oriented by `relation`; a positive signed
  difference means `delta_hat > 0` strictly, and is a precondition of a pass.
- **Null**: common-calendar moving-block bootstrap over the complete trade
  cohort of each block, **30-day blocks fixed a priori**, null-centred
  (`delta_b - delta_hat >= delta_hat`), exact empirical one-sided p. 60/90-day
  readings are **diagnostic only** and never substituted — no picking the best p.
- **Gate 1's common entry rotation does not test this null**, because rotating
  entries destroys the trade population the label is attached to.
- **Floors**: ≥12 non-overlapping 30-day blocks with both arms represented,
  ≥60 trades per slice, ≥20 per arm. These are **design choices requiring
  synthetic calibration, not a proof of independence**, and the spec says so.
- **Boundary-crossing exits** belong wholly to the **entry** block, identically
  in both arms and every draw; cross-symbol overlap is preserved deliberately;
  incomplete trades are excluded and counted.
- **Leakage firewall** (code / time / hashes), **evidence-access audit of A and
  B separately** with *unknown exposure ⇒ unavailable* and inspected history as
  development-only, and a **prospective cut** after registration plus a 230-bar
  closed-data warmup.
- **Charging**: each of H1/H2 is one ordered `gate2:H<n>` row in the existing
  LORD++ sequence; `p = max` over **all required slices**, with every
  ineligible required slice contributing `p=1.0`, and eligibility sealed before
  any contrast so a slice can never be dropped for reading badly;
  insufficient evidence after the read is `p = 1.0`, charged, no refund;
  precision (`ceil(1/alpha_t)-1 <= B_cap`) is checked **before** the read, and
  an unreachable level defers with no read and no charge.
- **Pass rule** `ceil(2m/3)`; at m=2 that is 2, so **both** H1 and H2 must
  reject.
- **Durable reserve-before-read**: `reserve_test` writes `p=NULL` before any
  price is loaded; a crash leaves a row that spends budget and earns nothing;
  replay resumes only against an **identical sealed bundle hash**, otherwise it
  is a new charged look.
- **Fail closed**: only `condition` + `mean_r` + `complement` are supported;
  `null_pctile`, `hit_rate`, every `subset` kind and `baseline: all` are
  `unsupported_target`, refused offline with no read and no charge.

## 7. Blockers — nothing can pass today

1. **Calibration not done.** The false-pass rate of the block bootstrap under
   a shared factor and under long memory is unmeasured. Until it is, inference
   from gate 2 is experimental and the 12/60/20 floors are unvalidated choices.
2. **No family error guarantee.** Per-row LORD++ does not establish a
   predictive-family or all-admission error rate for a "k of m must reject"
   conjunction over predictions computed from the *same* trades. Explicit
   calibration + human approval blocker before `reason_passed` may ever be
   written.
3. **Evidence access.** Held-out A and B are both **`unknown`** exposure ⇒
   **unavailable**. The first admissible slice is prospective: ≥360 days of
   forward 4h data after a 230-bar warmup that has not started.
4. **Gate 2 is not implemented or wired.** No gate2 evaluator or reservation
   path was added in this pass. No ledger state is asserted because no ledger
   was read. Gate2 wiring, held-out reads, reservations, and gate writes remain
   out of scope.
5. **Three gates required.** `reason_passed` needs gate 1 **and** gate 3
   **and** gate 2. None of the three is satisfied for `55243573515adc3b`.
6. Pre-existing and untouched: `test_macro_guard::test_a_restart_reuses_the_cached_calendar`
   still fails on the wall clock.

## 8. What changed on disk

New or updated: the separate `-v2` artifact directory, gate2 spec, this report,
the session handoff, and the corrected offline helper/tests. The original v1
artifact and both `/tmp` dossier directories were preserved. No runtime,
config, ledger, database, held-out data, network, or commit was touched.
