"""Explicit, evidence-only Git checkpoint; never operates runtime services."""
import collections
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

E = Path('/mnt/luffy-recovery/recovery/current-work-remote-checkpoint-r1')
P = Path('/mnt/luffy-data/luffy/production')
STAMP = '20261005T131532Z'
PREFIX = 'luffy-checkpoint-' + STAMP
TRACK = 'docs/checkpoints/' + STAMP
MESSAGE = 'Checkpoint current runtime engineering work before assessment'
inventory = json.loads((E / 'inventory.json').read_text())

def git(p, *args, env=None, check=True):
    return subprocess.run(['git', '-C', str(p), *args], env=env,
                          check=check, capture_output=True, text=True)

def digest(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()

def scan_text(text):
    patterns = [r'-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----',
                r'\b(?:ghp|gho|github_pat)_[A-Za-z0-9_]{20,}',
                r'\bAKIA[A-Z0-9]{16}\b',
                r'(?i)(?:api[_-]?key|api[_-]?secret|access[_-]?token|password)\s*[=:]\s*[\"\x27][A-Za-z0-9+/=_-]{24,}[\"\x27]']
    return any(re.search(pattern, text) for pattern in patterns)

def verify_inputs():
    changed = []
    for r in inventory['worktrees']:
        p = Path(r['path'])
        if not r['exists']:
            continue
        if git(p, 'rev-parse', 'HEAD').stdout.strip() != r['head']:
            changed.append(str(p) + ': HEAD changed')
        for d in r.get('changes', []):
            if d['classification'] in ('PENDING_CAPTURE', 'CAPTURED', 'ALREADY_REMOTE'):
                f = p / d['file']
                if not f.is_file() or digest(f) != d['sha256']:
                    changed.append(str(f))
        raw = git(p, 'status', '--porcelain=v1', '-z', '--untracked-files=all').stdout
        known = {d['file'] for d in r.get('changes', [])}
        for entry in raw.split('\0'):
            if not entry:
                continue
            name = entry[3:]
            if name.startswith(('trader/', 'tests/', 'scripts/')) and name not in known:
                if '__pycache__' not in name and not name.endswith('.pyc'):
                    changed.append(str(p / name) + ': new change')
    if changed:
        (E / 'source-instability.json').write_text(json.dumps(changed, indent=2))
        raise RuntimeError('Source changed during checkpoint: see source-instability.json')

def checkpoint(r, files, evidence_note=True):
    p = Path(r['path'])
    suffix = re.sub('[^a-zA-Z0-9-]', '-', p.name)
    branch = PREFIX if p == P else PREFIX + '-' + suffix
    git(P, 'branch', branch, r['head'])
    if not files and not evidence_note:
        return {'branch': branch, 'sha': r['head'], 'base': r['head'],
                'checkout': str(p), 'files': [], 'new_commit': False}
    index = E / ('index-' + suffix)
    env = dict(os.environ, GIT_INDEX_FILE=str(index))
    git(p, 'read-tree', r['head'], env=env)
    # Explicit pathspecs only; the checkout's real index is never changed.
    if files:
        for name in files:
            assert not scan_text((p / name).read_text(errors='replace')), 'Secret pattern in ' + name
        git(p, 'add', '--', *files, env=env)
    note = {'status': 'UNVERIFIED', 'checkout': str(p), 'original_branch': r['branch'],
            'original_head': r['head'], 'checkpoint_branch': branch,
            'captured_files': files,
            'tests': 'Existing results only. No new tests or independent assessment. '
                     'Historical results do not prove this combined dirty variant.',
            'excluded_files': 'Remain local; database/runtime/environment/cache/raw-log/full-payload updates excluded.',
            'control_plane': 'STATE.yaml/NEXT.yaml preserved verbatim where present; no reconciliation performed.'}
    note_path = TRACK + '/checkout.json'
    note_data = json.dumps(note, indent=2) + '\n'
    blob = subprocess.check_output(['git', '-C', str(p), 'hash-object', '-w', '--stdin'],
                                   input=note_data, text=True).strip()
    git(p, 'update-index', '--add', '--cacheinfo', '100644,' + blob + ',' + note_path, env=env)
    check = git(p, 'diff', '--cached', '--check', env=env, check=False)
    (E / ('whitespace-' + suffix + '.txt')).write_text(check.stdout + check.stderr)
    # Existing whitespace is recorded, never silently edited.
    tree = git(p, 'write-tree', env=env).stdout.strip()
    sha = git(p, 'commit-tree', tree, '-p', r['head'], '-m', MESSAGE).stdout.strip()
    git(P, 'update-ref', 'refs/heads/' + branch, sha, r['head'])
    return {'branch': branch, 'sha': sha, 'base': r['head'], 'checkout': str(p),
            'files': files + [note_path], 'new_commit': True,
            'whitespace_check_exit': check.returncode}

verify_inputs()
results = []
for r in inventory['worktrees']:
    if r['path'] == str(P):
        continue
    selected = [d['file'] for d in r.get('changes', []) if d['classification'] == 'PENDING_CAPTURE']
    if selected or not r.get('head_already_remote', True):
        result = checkpoint(r, selected, evidence_note=bool(selected))
        results.append(result)
        for d in r.get('changes', []):
            if d['file'] in selected:
                d['classification'] = 'CAPTURED'
                d['checkpoint_branch'] = result['branch']
        print(result['branch'], result['sha'], 'files', len(selected), flush=True)

# Copy explicitly chosen bounded, sanitized reports and diagnostics only.
target = P / TRACK
target.mkdir(parents=True, exist_ok=True)
evidence_map = []
chosen = {
    'runtime-observer-attention-repair-r1': [
        'final-report.md', 'final-report.json', 'measured-summary.json',
        'native-regressions.json', 'native-regressions-1.txt', 'native-regressions-2.txt',
        'native-regressions-3.txt', 'observer-latency-summary.json',
        'storage-growth-summary.json', 'storage-failure-reproductions.json',
        'attention-original-reproductions.json', 'attention-fixed-reproductions.json',
        'forward-fixed-reproductions.json', 'nan-target-reproduction.json',
        'final-source-reader-proof.json', 'final-verification.json',
        'preflight-integrity.json', 'window-result.json',
        'observer_tick.py', 'storage_faults.py', 'io_fault.c',
        'reproduce_attention_fixed.py', 'reproduce_forward_fixed.py',
        'collect_window.py', 'run_native_regressions.py'],
    'runtime-bounded-evidence-latency-r1': [
        'final-report.md', 'final-report.json', 'native-regressions.json',
        'native-regressions-1.txt', 'native-regressions-2.txt', 'native-regressions-3.txt',
        'retained-source-reproduction.json', 'retained-source-final.json',
        'installed-consumer-final-proof.json', 'disabled-provider-final.txt'],
    'continued-frozen-observation-r1': ['final-report.md', 'final-report.json',
        'window-result.json', 'session-safety-incidents.json', 'exit-protection-metadata.json']}
for task, names in chosen.items():
    for name in names:
        src = Path('/mnt/luffy-recovery/recovery') / task / 'evidence' / name
        assert src.stat().st_size < 100000, str(src)
        dest = target / 'evidence' / task / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
        evidence_map.append({'source': str(src), 'captured_path': str(dest.relative_to(P)),
                             'sha256': digest(src), 'bytes': src.stat().st_size})
for name in ['runtime-readonly.json', 'push-scope-check.json', 'candidate-secret-scan.json', 'capture_checkpoint.py']:
    dest = target / name
    shutil.copyfile(E / name, dest)
    evidence_map.append({'source': str(E / name), 'captured_path': str(dest.relative_to(P)),
                         'sha256': digest(dest), 'bytes': dest.stat().st_size})

manifest = {'checkpoint_branch': PREFIX, 'original_primary_head': inventory['worktrees'][0]['head'],
    'remote_main_at_start': next(x.split()[0] for x in inventory['remote_heads'] if x.endswith('refs/heads/main')),
    'coordination': 'No live team sub-agents. Other host Codex sessions were not killed/interrupted; '
                    'pause requested through user channel; source hashes and HEADs verified.',
    'status': 'CHECKPOINT_ONLY; no independent engineering PASS',
    'in_progress': ['One-hour FROZEN observation blocked after first 266.7s cycle exceeded existing 240s threshold.',
                    'Original journal-write SQLite cause remains UNCONFIRMED.',
                    'First-cycle cost not fully attributed; capture-budget overruns persist.',
                    'Historical dirty checkout variants UNVERIFIED; no full tests rerun.'],
    'control_plane_discrepancies': [
        'Production STATE.yaml still records production_main_head=1bed3958740ec2d0d575aabc817978086d31d390 and reviewed_head=UNCOMMITTED.',
        'STATE/NEXT retain undeployed/startup-not-performed/first-startup-pending claims predating deployed 958c9eb and attempted observation.',
        'STATE/NEXT contain historical closure claims and prior BLOCKED entries; preserved unchanged, not independently reconciled.',
        'Older checkout STATE/NEXT versions differ; captured on own branches verbatim, not merged.'],
    'test_identity': {'repair_source_commit': '958c9eb2f0b10993ad240b85f88c626b8d0e2ee6',
        'reported_focused_tests': 906,
        'qualification': 'Retained repair report attributes focused tests to final repair sources. '
                         'Tests predate this evidence-only checkpoint. No independent PASS or new test run. '
                         'Older dirty variants remain UNVERIFIED; retained historical tests may predate their final changes.'},
    'additional_checkpoints': results, 'evidence': evidence_map, 'worktrees': []}
for r in inventory['worktrees']:
    kept = [d for d in r.get('changes', []) if d['classification'] != 'EXCLUDED_LOCAL']
    excluded = [d for d in r.get('changes', []) if d['classification'] == 'EXCLUDED_LOCAL']
    manifest['worktrees'].append({'path': r['path'], 'original_head': r['head'],
        'original_branch': r['branch'], 'exists': r['exists'],
        'head_already_remote': r.get('head_already_remote'), 'staged': r.get('staged', []),
        'changes': kept, 'excluded_counts': dict(collections.Counter(d['reason'] for d in excluded)),
        'excluded_examples': [d['file'] for d in excluded[:5]]})
(target / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
(target / 'REPORT.md').write_text('''# Current engineering checkpoint — no assessment

This checkpoint preserves existing history, unique working files and bounded retained evidence.
Production source is already remote at 958c9eb2f0b10993ad240b85f88c626b8d0e2ee6.
The primary checkpoint adds reports/manifest only; implementation is not duplicated.
All new historical dirty variants are UNVERIFIED. Existing test/report PASS claims
are retained as attributed claims, not upgraded to independent assessment.

STATE.yaml and NEXT.yaml are retained verbatim in base history and, where changed,
their respective checkpoint branches. Their older deployment/startup/review identities
disagree with current deployed source and the failed controlled observation.
See manifest.json for exact checkout → original HEAD → file → checkpoint mappings.
ALREADY_REMOTE file blobs are reachable from the recorded remote branch heads;
they are not recommitted merely to duplicate remote work.

The latest runtime task remains BLOCKED: 266.7s first cycle exceeded 240s;
one-hour observation is incomplete. Original SQLite probe cause is UNCONFIRMED.
No Kernel/Dashboard is running; FROZEN, hold, recovery latch and watchdog.off remain.
No implementation, assessment, deployment, service or venue actions occurred here.

Excluded updates remain local: databases/copies/WAL/SHM, environments, browser
profiles, graphify/caches, generated test artifacts, raw logs and large full payloads.
Inherited committed history is preserved; excluded working-file updates are not added.
''')
primary_files = [str(f.relative_to(P)) for f in target.rglob('*') if f.is_file()]
verify_inputs()
results.append(checkpoint(inventory['worktrees'][0], primary_files))
(E / 'checkpoints.json').write_text(json.dumps(results, indent=2) + '\n')
(E / 'captured-inventory.json').write_text(json.dumps(inventory, indent=2) + '\n')
verify_inputs()
(E / 'source-stability.json').write_text(json.dumps({'changed': False, 'verified_heads_and_changed_files': True}, indent=2))
print('READY:', len(results), 'explicit branches; no push performed yet', flush=True)
