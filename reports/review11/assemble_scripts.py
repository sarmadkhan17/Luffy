"""Rebuild Review 11 executable per-file evidence from the reviewed source revision.

No source is executed.  Caller references distinguish exact path/hash, sibling
imports and directory bindings; absence is never itself a removal decision.
"""
from pathlib import Path
import ast
import hashlib
import json
import subprocess

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'reports/review11'
all_paths = (OUT / 'scripts-paths.txt').read_text().splitlines()
delegated = {
    'scripts/m32_scheme_d_host_qualification_v4_20260923/qualify.py',
    'scripts/m32_scheme_d_qualification_20260922/qualify.py',
    'scripts/m32_scheme_d_qualification_v4_20260923/qualify_v4.py',
    'scripts/m32_categorical_arb_batch_20260920/certify_batch.py',
    'scripts/m32_categorical_arb_batch_20260920/replay_integrator.py',
    'scripts/m32_categorical_arb_box_canary_20260920/certify_box_canaries.py',
    'scripts/m32_p4_c3_c4_taylor_20260921/certify_p4_reduced_taylor.py',
    'scripts/m32_p5_c3_taylor_canary_20260920/certify_hit_miss_taylor.py',
    'scripts/m32_p5_c3_taylor_canary_20260920/certify_population_taylor.py',
    'scripts/m32_p5_c4_taylor_20260921/certify_p5_c4_taylor.py',
    'scripts/m32_p5c3_p6c4_remaining_20260921/certify_remaining.py',
    'scripts/m32_p6_c3_taylor_20260921/certify_p6_c3_taylor.py',
    'scripts/m32_p6_c4_taylor_20260921/certify_p6_c4_taylor.py',
    'scripts/m32_p7c4_arb_spike_20260920/certify_p7c4_h24.py',
    'scripts/m32_p7c4_arb_spike_20260920/replay_p7c4_h24.py',
    'scripts/m32_p7c4_persistence_20260921/certify_p7c4_persistence.py',
}
paths = [p for p in all_paths if p not in delegated]

def source(path):
    p = ROOT / path
    return p.read_bytes() if p.exists() else subprocess.check_output(['git', 'show', 'HEAD:' + path], cwd=ROOT)

corpus = {}
for f in subprocess.check_output(['git', 'ls-files', '-z'], cwd=ROOT).decode().split('\0'):
    if not f or f.startswith(('graphify-out/', 'reports/', '.git/')):
        continue
    p = ROOT / f
    try:
        content = p.read_text()
        corpus[f] = (content, content.splitlines())
    except (UnicodeError, OSError):
        continue

def refs(path, digest):
    p = Path(path)
    hits = []
    for f, (content, lines) in corpus.items():
        if f == path:
            continue
        sibling = Path(f).parent == p.parent
        if not (path in content or digest in content or str(p.parent) in content
                or (sibling and (p.name in content or ('import ' + p.stem) in content
                                 or ('from ' + p.stem) in content))):
            continue
        for n, line in enumerate(lines, 1):
            category = None
            if path in line:
                category = 'exact_path'
            elif digest in line:
                category = 'exact_sha256'
            elif sibling and (p.name in line or ('import ' + p.stem) in line or ('from ' + p.stem) in line):
                category = 'sibling_filename_or_import'
            elif str(p.parent) in line:
                category = 'directory_binding_requires_filename_resolution'
            if category:
                hits.append({'path': f, 'line': n, 'kind': category, 'excerpt': line[:260]})
    return hits

def meaning(path, doc):
    p = Path(path); name = p.name
    if name == '_scratch_env_check.py':
        return ('Standalone import-time Python/flint capability and finite Gaussian-integral diagnostic.',
                'Prints environment/capabilities, changes process-local flint precision, optional small integral; no stored evidence or recovery output.',
                'Zero functional, dynamic-literal, hash-manifest or recovery consumers established by full tracked scan; formal environment locks/qualification cover the useful responsibility.',
                'Unused scratch executable; no entry point contract, certificate output or frozen manifest binding.', 'REMOVE', 'IMPLEMENTED_OFFLINE')
    purpose = doc.split('\n')[0] if doc else 'Deterministic driver for the nine frozen remaining categorical kernels.'
    if name == 'run_all.sh':
        return (purpose, 'Explicit invocation launches 9 expensive Arb subprocesses with bounded xargs worker count, writes primary/replay/checkpoints/logs.',
                'Frozen driver hashed in freeze_manifest.json; preserve exact reproduction lineage.',
                'Only explicit engineering driver; no automatic runtime caller. Arbitrary mode falls through to replay config, but retained driver has documented primary/replay invocation and cannot justify provenance rewrite.', 'KEEP', 'NO_CHANGE_REQUIRED')
    if name == 'record_refusal.py':
        return (purpose, 'Writes historical FAIL_CLOSED certificate with all 27 kernel refusals and measured resource bounds; no RNG.',
                'Preserves retained negative evidence after bounded unsuccessful enclosure attempt; manifest hashes the producer.',
                'Historical fixed counts/resource facts are a record of the attempt, not a reusable inferential framework or live measurement.', 'KEEP', 'NO_CHANGE_REQUIRED')
    if 'conditioning_comparison' in path:
        return (purpose, 'Prespec/check scripts exclusive-create local JSON; sim creates paired synthetic worlds/permutations and exclusive results on explicit CLI. Import reads local frozen prespec.',
                'Frozen exploratory comparison plus seed-bound replication consumer; surrogate statistic and oracle assumptions deliberately labelled.',
                'Surrogate covariance and synthetic oracle guards do not establish actual-statistic or empirical exchangeability validity. No current real-market admission consumer.', 'KEEP', 'NO_CHANGE_REQUIRED')
    if 'replication' in path:
        return (purpose, 'Validates prespec/hash/git/env before RNG; exclusive attempt directory, one seed override, 10,000 prescribed worlds only on explicit CLI; fsynced world log, failure receipt and manifest.',
                'Historical single-attempt replication keeps original comparison separate; precommitted controls and negative evidence must remain.',
                'Consumed attempt cannot be rerun or repackaged as untouched evidence; review never invoked main or constructed worlds.', 'KEEP', 'NO_CHANGE_REQUIRED')
    if 'fast_metric' in path:
        return (purpose, 'Explicit equivalence suite creates synthetic fixture-only RNG; benchmark times reference/fast and optionally writes output. Import defines functions only.',
                'Hash-bound FastMetric gate pins suite/results/reference. Recorded bit identity remains evidence; bounded current synthetic consumer tests are distinct.',
                'Full suite/benchmark is computationally expensive; neither is required for offline archival cleanup and neither was rerun.', 'KEEP', 'NO_CHANGE_REQUIRED')
    if 'pilot_v4' in path:
        return (purpose, 'Launcher verifies owner/prespec/request/source/host/benchmark hashes before runner registry, delegates shard work under network block. Evaluator reads existing receipts/truth only and exclusive-creates verdict only in CLI.',
                'Retained 80-world pilot authorization, result and exact-evidence evaluator; synthetic tests exercise refusal/precedence. Stored read-only evaluate() replay equals original verdict.',
                'GO is explicitly pilot_only_non_final, not validation/Gate 2/search/referee/handoff/trading authorization; cross-family effect diagnostic is descriptive. No new run.', 'KEEP', 'NO_CHANGE_REQUIRED')
    if 'pilot_' in path:
        return (purpose, 'Explicit historic v3 pilot wrapper checks bindings and builds four-cell context; delegates existing shard layer. Registry RNG used only during explicitly invoked work.',
                'Historical producer links owner authorization and retained v3 pilot evidence; v4 supplies corrected classification/stronger binding.',
                'Superseded v3 pilot is evidence lineage. Preserve exact bytes; do not treat its P1 power role as current v4 authority.', 'KEEP', 'NO_CHANGE_REQUIRED')
    if name == 'rng_guard.py':
        return (purpose, 'Narrow context temporarily patches numpy/std/OS/secrets random construction/output primitives, counts attempts, restores bindings on exit.',
                'Direct qualification/tests and v4 fixture consumers verify no accidental RNG in preparation; defense in depth.',
                'Scope guard is not a global security boundary and does not rewrite SeedRegistry or permit validation.', 'KEEP', 'NO_CHANGE_REQUIRED')
    if name == 'shard_fixture.py':
        return (purpose, 'ArithmeticToken/StubRegistry provide deterministic API-shaped inputs; context temporarily patches BH/world refusal and restores runner state.',
                'Consumed by shard/qualification tests; makes resume/merge mechanics testable without protocol RNG.',
                'Fixtures marked non_inferential, including stub BH rejections, are not research evidence.', 'KEEP', 'NO_CHANGE_REQUIRED')
    if name == 'shard_layer.py':
        return (purpose, 'Pure deterministic plan/seed identity; exclusive fsynced writes, locks, partial resume, canonical receipt/hash audit, no-replace staged merge; production authorization gates delegate RNG to frozen runner.',
                'Recovery and distributed duplicate protection path consumed by pilot/qualification/tests; cannot merge archived v3/v4 hashes without refreezing.',
                'v3 bare module caching superseded by v4 isolated loader; v4 injection dispatch scenario-based preserves P1/P3. Filesystem/cross-host qualification is environment-specific. No current run authorized.', 'KEEP', 'NO_CHANGE_REQUIRED')
    if name in ('bench_sharding.py', 'project_full_run.py') or 'phase90' in path:
        return (purpose, 'Explicit CLI either launches authorized benchmark/fixture subprocesses with receipts or computes deterministic projections from retained measurement JSON; writes report outputs.',
                'Explains operating-cost feasibility before authorization; hash-bound benchmark receipt is downstream pilot authorization input.',
                'Projected scaling assumes measured host efficiency/ideal linear workers; not a current spend authorization or observed full-run runtime.', 'KEEP', 'NO_CHANGE_REQUIRED')
    if 'validation_bundle' in path:
        version = 'v4' if '_v4_' in path else 'v3' if '_v3_' in path else 'v2' if '_v2_' in path else 'v1'
        purpose = {'build_bundle.py':'Build protocol-derived config/tables, exact covariance/PSD records, environment lock and source-hash manifest.',
                   'verify_bundle.py':'Independent protocol/table/hash/git/environment verifier with isolated deterministic probes and optional expensive replay.',
                   'acceptance.py':'Pure exact/finite-sample acceptance gates for Type I, calibration, FDR, power, refusal and overall aggregation.',
                   'preflight.py':'Registered deterministic preflight checks and arithmetic stubs under constructor RNG guard.',
                   'scheme_d_runner.py':'Owner-authorized protocol worlds, synchronized block permutations, fixed-family exact BH and canonical evidence receipts.'}[name] + ' Frozen ' + version + '.'
        side = {'build_bundle.py':'Explicit command overwrites bundle preparation files/manifest; no RNG or operational imports.',
                'verify_bundle.py':'Reads pinned evidence/git/environment, launches deterministic subprocess probes; main writes verification_result.json. v3 can rerun entire fixture equivalence suite, v4 reuses pinned full suite.',
                'acceptance.py':'No I/O or RNG construction. Bootstrap accepts explicitly injected integer source; gate parameters frozen.',
                'preflight.py':'Import patches numpy/random constructors globally for standalone process; registered checks create temporary synthetic files/subprocesses; optional output report only.',
                'scheme_d_runner.py':'Import definitions only; manifest/environment/isolation/authorization gates precede RNG mode; explicit work writes exclusive JSON/JSONL receipts under blocked network.'}[name]
        issue = ('Current v4 corrects P1/P3 to perturbed nulls, preserving injection and Type-I/refusal guarantees; 75,000-world/767,999-draw protocol remains unexecuted by review.'
                 if version == 'v4' else 'Superseded historical version: v1 float class edges/I3/I6 fixed in v2; v3 exact-equivalent FastMetric; v4 fixes P1/P3 roles. Archived bytes are intentionally retained, not silently migrated.')
        return purpose, side, 'Frozen source and manifest lineage plus exact external hashes/tests; preserve independent verifier rather than merge away independence.', issue, 'KEEP', 'NO_CHANGE_REQUIRED'
    if 'pre_rng_truth' in path:
        return (purpose, 'Deterministic exact rational/retained dyadic certificate assembly or verification; explicit main writes package/replay/freeze files. Freeze --check reads and compares; no RNG.',
                'Provenance chain from negative/accepted family evidence through 21,120 truth rows into hash-bound bundle; generator/verifier independence intentional.',
                'PASS means consistency of retained proofs, not a new statistical validation. Historical partial package intentionally keeps unclassifiable rows; complete package retains source/manifests and old resolved-row regression.', 'KEEP', 'NO_CHANGE_REQUIRED')
    return (purpose, 'Deterministic independent higher-precision replay/check of retained categorical or persistence evidence; main writes local replay/certificate/manifest, never called by review.',
            'Frozen evidence producer/checker retained for source provenance and exact dyadic interval comparisons; independent arithmetic formulations are intentional.',
            'Historical output overwrite defaults are not authority to rerun preparation in-place; no quadrature or frozen rewrite performed.', 'KEEP', 'NO_CHANGE_REQUIRED')

rows = []
for path in paths:
    raw = source(path); text = raw.decode(); digest = hashlib.sha256(raw).hexdigest()
    tree = ast.parse(text) if path.endswith('.py') else None
    doc = ast.get_docstring(tree) or '' if tree else ''
    functions = []
    if tree:
        def collect(node, prefix=''):
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    functions.append({'name':prefix + child.name,'line':child.lineno,'end_line':child.end_lineno})
                    collect(child,prefix + child.name + '.')
                else: collect(child,prefix)
        collect(tree)
    else:
        functions = [{'name':'module-level shell driver','line':1,'end_line':len(text.splitlines())}]
    purpose, side, necessity, issue, decision, status = meaning(path,doc)
    hits = refs(path,digest)
    rows.append({'path':path,'lines':len(text.splitlines()),'sha256':digest,
                 'purpose':purpose,'functions':functions,'callers':hits,
                 'consumers':'; '.join(sorted({h['path'] for h in hits})) or 'No functional consumer; see removal evidence.',
                 'side_effects':side,'necessity':necessity,'issue':issue,'decision':decision,'fix_status':status,
                 'claim_basis':'source contract; bounded verification separately recorded in scripts-tests.xml and scripts-pilot-replay.json',
                 'review_method':'Full source read. Frozen bundle families: latest full source plus every predecessor-only removed source line in exact adjacent-version diffs; identical spans inherited only after exact diff comparison.'})
assert len(rows) == 55
(OUT/'scripts-files.json').write_text(json.dumps(rows,indent=2)+'\n')
(OUT/'scripts-source-verification.json').write_text(json.dumps({'python_files_ast_parse_passed':sum(p.endswith('.py') for p in paths),'shell_files_bash_n':'run separately','source_bytes':sum(len(source(p)) for p in paths),'lines':sum(r['lines'] for r in rows),'source_changed_only':[r['path'] for r in rows if r['fix_status']=='IMPLEMENTED_OFFLINE']},indent=2)+'\n')
print('wrote',len(rows),'rows',sum(r['lines'] for r in rows),'source lines')
