"""Reproduce only the authorized offline Stage1 campaign and baseline comparison.

Run with the repository venv. Each pytest process has a bounded selection and
independent filesystem/network guards. Base imports use an exact git archive,
never selected module substitution into candidate code.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import resource
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time

BASE = 'fcfe9e0359874b18d65a0b1e1ccfd156d18115e1'
CONTROL = ['tests/test_supervisor.py', 'tests/test_control_state_recovery.py',
           'tests/test_stage1_entry_authority.py', 'tests/test_entry_recovery.py']
RECOVERY = ['tests/test_kernel_boot_recovery.py', 'tests/test_owner_recovery.py',
            'tests/test_owner_recovery_risk_guard.py', 'tests/test_owner_recovery_concurrency.py']
RISK = ['tests/test_activation_risk_baseline.py', 'tests/test_activation_risk_baseline_v2.py',
        'tests/test_risk_state_persistence.py',
        'tests/test_owner_interface.py::test_a_status_and_health_are_read_only_and_audited',
        'tests/test_stage8_owner_os.py']
LEGACY = ['tests/test_activation_risk_baseline.py::test_previously_approved_entry_submits_nothing[' + v + ']'
          for v in ('macro', 'spot_resume', 'owner_unhalt')]
BATCHES = {'acceptance': ['tests/test_stage1_recovery_monitoring.py'], 'control-entry': CONTROL,
           'recovery-owner': RECOVERY, 'risk-health': RISK + ['-k', 'not test_previously_approved_entry_submits_nothing'],
           'legacy-candidate': LEGACY, 'legacy-base': LEGACY,
           'original-campaign-collection': CONTROL + RECOVERY + RISK + ['--collect-only'],
           'pit-regression': ['tests/test_pit_population.py', 'tests/test_population_rollforward.py',
                              'tests/test_population_cohort.py', 'tests/test_declared_population.py']}


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('batch', choices=BATCHES)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    evidence = root / 'docs/superpowers/evidence/stage1-recovery-monitoring-blocker-fix-r1'
    evidence.mkdir(parents=True, exist_ok=True)
    def git(*argv):
        return subprocess.check_output(['git', *argv], cwd=root)
    source = root
    snapshot = None
    meta = {'batch': args.batch, 'branch': git('branch', '--show-current').decode().strip(),
            'HEAD': git('rev-parse', 'HEAD').decode().strip(), 'required_base': BASE,
            'fd_limits': list(resource.getrlimit(resource.RLIMIT_NOFILE)),
            'selected_arguments': BATCHES[args.batch], 'fixture_mode': 'temp SQLite; fake venues/providers; no runtime startup',
            'runner_command': shlex.join([sys.executable, str(Path(__file__).resolve()), args.batch]),
            'runner_cwd': str(root), 'runner_sha256': sha(Path(__file__).read_bytes()),
            'guard_sha256': sha((root / 'scripts/stage1_recovery_monitoring_guard.py').read_bytes())}
    if args.batch == 'legacy-base':
        snapshot = Path(tempfile.mkdtemp(prefix='.stage1-base-snapshot-', dir=root))
        source = snapshot
        # Explicit source-only tree selection: no data, secrets or runtime files.
        archive_command = ['git', 'archive', '--format=tar', BASE, 'trader', 'tests', 'scripts',
                           'config.yaml', 'org.yaml', 'requirements.txt', 'SDD.md', 'STATE.yaml', 'NEXT.yaml']
        raw = subprocess.check_output(archive_command, cwd=root)
        archive = snapshot / 'base-source.tar'; archive.write_bytes(raw)
        with tarfile.open(archive) as bundle:
            bundle.extractall(snapshot, filter='data')
        meta['base_snapshot'] = {'command': shlex.join(archive_command), 'cwd': str(root),
            'archive_sha256': sha(raw), 'extraction': "tarfile.open(archive).extractall(snapshot, filter='data')",
            'source_commit': BASE, 'tree': git('rev-parse', BASE + '^{tree}').decode().strip(),
            'snapshot': str(snapshot), 'removed_after_run': True}
    source_hashes = {str(p.relative_to(source)): sha(p.read_bytes())
                     for top in ('trader', 'tests') for p in (source / top).rglob('*.py')
                     if '__pycache__' not in p.parts}
    meta['source_files_sha256'] = source_hashes
    fixtures = Path(tempfile.mkdtemp(prefix='.stage1-evidence-fixtures-', dir=root))
    result = evidence / (args.batch + '.pytest.json')
    command = [sys.executable, '-m', 'pytest', '-p', 'stage1_recovery_monitoring_guard',
               *BATCHES[args.batch], '-q', '--tb=long', '--basetemp', str(fixtures)]
    environment = dict(os.environ, PYTHONPATH=str(root / 'scripts'), PYTEST_DISABLE_PLUGIN_AUTOLOAD='1',
        STAGE1_EVIDENCE_FIXTURES=str(fixtures), STAGE1_EVIDENCE_SOURCE=str(source),
        STAGE1_EVIDENCE_RESULT=str(result))
    meta.update(command=shlex.join(command), argv=command, cwd=str(source),
                environment={k: environment[k] for k in ('PYTHONPATH', 'PYTEST_DISABLE_PLUGIN_AUTOLOAD',
                    'STAGE1_EVIDENCE_FIXTURES', 'STAGE1_EVIDENCE_SOURCE', 'STAGE1_EVIDENCE_RESULT')})
    meta['start'] = datetime.now(timezone.utc).isoformat(); started = time.monotonic()
    try:
        completed = subprocess.run(command, cwd=source, env=environment, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, text=True)
        (evidence / (args.batch + '.txt')).write_text(completed.stdout)
        meta.update(exit_code=completed.returncode, end=datetime.now(timezone.utc).isoformat(),
                    duration_seconds=time.monotonic() - started, output_file=args.batch + '.txt')
        if result.exists():
            report = json.loads(result.read_text())
            meta.update(counts=report['counts'], network_guard_active=report['network_guard_active'],
                        production_store_guard_active=report['production_store_guard_active'],
                        network_attempts=report['network_attempts'], production_store_attempts=report['production_store_attempts'],
                        wrong_source_imports=report.get('wrong_source_imports', []))
        (evidence / (args.batch + '.run.json')).write_text(json.dumps(meta, indent=2) + '\n')
        print(json.dumps({k: meta.get(k) for k in ('batch', 'exit_code', 'counts', 'duration_seconds',
             'network_attempts', 'production_store_attempts', 'wrong_source_imports')}))
        print(completed.stdout[-1800:])
    finally:
        shutil.rmtree(fixtures)
        if snapshot is not None:
            shutil.rmtree(snapshot)


if __name__ == '__main__':
    main()
