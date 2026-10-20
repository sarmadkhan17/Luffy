"""Build the per-file retained-evidence ledger from the read-only byte audit."""
from collections import Counter, defaultdict
import json
from pathlib import Path

OUT = Path('reports/review11')

BOUNDARIES = {
    'categorical_arb_batch': 'Fail-closed 27-kernel attempt: zero certified kernels; PASS replay of zero accepted kernels is vacuous, not completion.',
    'categorical_arb_box_canary': 'Negative canary evidence: P5/C3 majority unresolved; P6/C4 radius/zero-exclusion failed. Checkpoints preserve deterministic refinement and cost; neither grants a truth certificate.',
    'categorical_arb_completion': 'Partial extension certified 20 of 47 previously refused kernels; 27 remained. Later Taylor families supply the remaining certificates without rewriting this attempt.',
    'categorical_equivalence': 'Frozen macro-F1 estimand and initial partial interval certificates; possible ties refuse. Later supplements retain the same estimand. Analytic truth does not validate exchangeability.',
    'conditioning_comparison': 'Exploratory synthetic comparison; oracle DGP guards are unavailable on empirical data. Discarded pre-RNG prespec identities are intentional; original ignored outputs remain separately hash-pinned.',
    'fast_metric': 'Historical exact-equivalence/benchmark evidence for fixed 384-hypothesis geometry. Speed and bit identity establish computational properties, not statistical validity or executable-strategy quality.',
    'p4_c3_c4_taylor': 'Two representative true-null families with strict common majority and exact zero lift; 224-bit primary/384-bit replay overlap. Frozen report next-task text is historical sequencing.',
    'p5_c3_taylor_canary': 'Separate population-only majority and full hit/miss macro-F1 certificates. Replay overlap is required, not interval containment; later evidence cannot erase the earlier narrower scope.',
    'p5_c4_taylor': 'Six kernels, two exact nulls and four positive non-nulls, strict majorities and <=1e-12 radii. Frozen scope excludes P6/C3.',
    'p5c3_p6c4_remaining': 'Nine explicitly enumerated kernels plus two already-certified canaries, separate primary/replay partitions. Logs and checkpoints retain deterministically stopped work, not extra sampling budget.',
    'p6_c3_taylor': 'Eight positive non-null kernels certified with strict classes and guarded Taylor enclosures; higher-precision replay retained. Completing categorical truth does not admit search.',
    'p6_c4_taylor': 'Single representative hit/miss/population certificate under preserved bivariate reduction; primary/replay overlap, radius and zero-separation contracts retained.',
    'p7c4_arb_spike': 'Historical categorical spike with limited proof surface. Later independent replay dyadics and consolidated package are separate evidence; spike acceptance alone is not full validation.',
    'p7c4_persistence': 'P7/C4 persistence-only Hermite/Arb interval truth; primary and independent replay preserved. It neither replaces categorical truth nor supplies empirical trading outcomes.',
    'scheme_d_host_qualification_v4': 'Single-host RNG-free qualification, cross-host locking unexercised. Combined execution readiness remains false; projected full validation ~190 days on qualified workers. Six temporary canary outputs are represented by hashes, not retained here.',
    'scheme_d_phase90_fast_benchmark_v4': 'One historically authorized phase-90 v4 benchmark, not inference. Measured full-workload projection ~16406.78 CPU-hours is historical hardware evidence, not a current measured runtime or authorization.',
    'scheme_d_phase90_fast_benchmark': 'Historically authorized v3 optimized phase-90 benchmark; revision-bound runtime evidence cannot authorize v4 or establish market efficacy.',
    'scheme_d_phase90_benchmark': 'Historically authorized reference phase-90 benchmark. World receipt self-hash predates added runtime field; timing is protected by the file hash. This is not validation evidence.',
    'scheme_d_pilot_v4': '80-world non-final pilot, exact stored verdict replayed without RNG. GO means encouraging P6 diagnostic only; full acceptance remains false for all four cells, and P1 rejections are false discoveries.',
    'scheme_d_pilot': 'Historical v3 pilot GO is non-final. Exact derivation of recorded P6 lift is unavailable; later v4 evaluator expressly disclaims reproduction. Original record must not be rewritten.',
    'scheme_d_pre_rng_truth_20260920': 'Superseded fail-closed 35-cell truth attempt with 16 unresolved cells. PASS verifies its honest refusal record, not freeze readiness.',
    'scheme_d_pre_rng_truth_20260921': 'Immutable 55-cell x384-row analytic truth package, 21120 rows and no unresolved truth. Freeze binds source/authority/family hashes; no RNG, empirical null validity, full validation or admission follows from truth completion.',
    'scheme_d_qualification_v4': 'Deterministic binding-only qualification distinct from full host qualification. Current-file enumeration misses deleted historical v3 files (retained defect witness); independent manifest and owner gates still apply.',
    'scheme_d_qualification': 'Historical v3 single-host qualification, no cross-host proof or full execution readiness. Six deterministic canary outputs are temporary; receipt hashes remain. Not transferable to v4.',
    'scheme_d_replication': 'One fixed independent-seed replication of the exploratory coverage-confounding scenario. Retained original and replicated evidence stay separate, cannot be pooled or rerun as a retry.',
    'scheme_d_sharding_v4': 'v4-bound integration preserves perturbed-null injection and import isolation. Existing frozen production_authorization=false is historical evidence; later bounded pilot authority is separate.',
    'scheme_d_sharding': 'v3 hardened sharding lineage: exclusive staging, no-replace publication, fsync and manifest-last recovery. Copied v4 lineage cannot be merged without breaking separately bound provenance.',
    'scheme_d_validation_bundle_v4': 'Frozen v4 bundle: P1/P3 are perturbed nulls with injection, exact support/BH/zero-lift rules retained. Preparation exception is recorded and historically owner-accepted; historical status is not a fresh authorization.',
    'scheme_d_validation_bundle_v3': 'Frozen superseded optimized v3 bundle, separately hash-bound; retained for old benchmark/pilot/qualification provenance. Its world dispatch must not be used for v4 perturbed nulls.',
    'scheme_d_validation_bundle_v2': 'Frozen superseded v2 conformance/owner-clarification bundle. Historical reference and refusal corrections preserve provenance; not the current v4 runner.',
    'scheme_d_validation_bundle': 'Frozen superseded first runner/acceptance/environment bundle. Later revisions supersede semantics without editing old evidence or spending another unregistered look.',
}


def boundary(name):
    # Longest match avoids prefix collisions among retained generations.
    matches = [(k, v) for k, v in BOUNDARIES.items() if ('m32_' + k) in name]
    return max(matches, key=lambda kv: len(kv[0]))[1]


def main():
    audit = json.loads(OUT.joinpath('artifact-audit.json').read_text())
    summaries = {x['path']: x['summary'] for x in json.loads(OUT.joinpath('artifact-summaries.json').read_text())}
    owned = OUT.joinpath('owned-paths.txt').read_text().splitlines()
    code_paths = [p for p in owned if Path(p).suffix in ('.py', '.sh') and Path(p).exists()]
    code_paths += [str(p) for p in Path('tests').glob('test_m32*.py')]
    code = {p: Path(p).read_text().splitlines() for p in sorted(set(code_paths))}
    inbound = defaultdict(list)
    for link in audit['links']:
        target = link['target']
        for path in target if isinstance(target, list) else [target]:
            inbound[str(Path(path))].append(dict(path=link['source'], key=link['key'], relation=link['status']))
    by_table = {r['path']: r for r in audit['truth_tables']}
    rows = []
    for item in audit['files']:
        name = item['path']
        p = Path(name)
        summary = summaries.get(name, {})
        refs = []
        for source, lines in code.items():
            same_generation = Path(source).parent == p.parent
            for line_number, line in enumerate(lines, 1):
                # Full path, or same-generation basename only; avoid global 'primary.json' name collisions.
                if name in line or (same_generation and p.name in line):
                    refs.append(dict(path=source, line=line_number, text=line.strip()[:220]))
        data = json.loads(p.read_text()) if p.suffix == '.json' else None
        facts = {k: v for k, v in (data or {}).items() if isinstance(v, (str, int, float, bool)) or v is None}
        # Source scalars remain source claims; numbers are not promoted to validation assertions.
        if '/tables/' in name:
            detail = by_table[name]
            purpose = f"{detail['cell']} analytic truth table: 384 unique hypotheses; {detail['counts']}; role={detail['role']}."
        elif p.suffix == '.jsonl':
            purpose = f"{p.name}: complete {item['parsed_records']}-record " + ('world receipts' if 'receipts' in p.name else 'worker/merge event history' if '/events/' in name else 'deterministic refinement checkpoint') + '.'
        elif p.suffix == '.sha256':
            purpose = 'Pre-RNG discarded-prespec identity (intentional mismatch with current prespec).' if 'discarded' in p.name else 'Exact-byte manifest for this generation and its named certificate/source dependencies.'
        elif p.suffix == '.md':
            purpose = p.read_text().splitlines()[0].lstrip('# ') + '; immutable acceptance rationale and historical next-task wording.'
        elif p.suffix == '.lock':
            purpose = 'Pinned historical arithmetic/runtime environment; consumed by generation-specific builder/verifier, not a package installation request.'
        else:
            schema = summary.get('schema', summary.get('spec_id', p.stem))
            purpose = f"{p.name}: {schema}; "
            for key in ('scope', 'status', 'classification', 'verdict', 'result', 'reason'):
                if key in facts:
                    purpose += f"{key}={str(facts[key])[:180]}; "
            purpose = purpose.rstrip('; ')
        row = dict(path=name, lines=item['lines'], bytes=item['bytes'], sha256=item['sha256'], baseline_sha256=item['sha256'],
                   purpose=purpose, functions='N/A: retained data/manifest/lock/report; no callable implementation.',
                   callers=refs, consumers=inbound[name],
                   consumer_contract='Generation-specific certifier/replayer, truth-package generator/verifier, or bundle/pilot audit loads these bytes directly or enumerates the parent directory. No runtime Strategy Factory/Learning reader established.',
                   side_effects='Stored evidence only; no import execution, scheduler, database, network or mutation.',
                   necessity='KEEP immutable research provenance, negative/refused work, exact source binding or replay/recovery chain; apparent duplicate generations have different scope/identity.',
                   issue=boundary(name), decision='KEEP', fix_status='NO_CHANGE_REQUIRED',
                   source_contract=facts,
                   verified_by_test=['artifact-audit.json: complete strict parse/byte identity and referenced-path hash checks; ' + ('all truth rows checked for unique 0..383 hypothesis identity and classification contracts' if name in by_table else 'not a new statistical validation')],
                   review_method='Full file bytes inspected via strict structured parsing or full text; schema, retained claims, source callers, hash consumers and generation boundary assessed. No frozen evidence edited.')
        if p.suffix == '.jsonl':
            row['source_contract'] = dict(records=item['parsed_records'], keys=item['keys'], terminal_scalars=item['terminal_scalars'])
        rows.append(row)
    OUT.joinpath('artifact-files.json').write_text(json.dumps(dict(files_reviewed=len(rows), decisions=dict(Counter(r['decision'] for r in rows)), rows=rows), indent=2) + '\n')
    print('artifact ledger', len(rows), 'rows;', sum(r['bytes'] for r in rows), 'bytes')


if __name__ == '__main__':
    main()
