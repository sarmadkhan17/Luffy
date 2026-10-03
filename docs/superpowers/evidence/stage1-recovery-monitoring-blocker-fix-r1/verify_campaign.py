"""Offline verifier for recorded node coverage and exact-base failure equality."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BASE = 'fcfe9e0359874b18d65a0b1e1ccfd156d18115e1'

def read(name, kind='pytest'):
    return json.loads((ROOT / (name + '.' + kind + '.json')).read_text())


def main():
    batches = ['acceptance', 'control-entry', 'recovery-owner', 'risk-health', 'pit-regression',
               'legacy-base', 'legacy-candidate', 'original-campaign-collection']
    for batch in batches:
        run, report = read(batch, 'run'), read(batch)
        assert run['required_base'] == BASE and run['HEAD'] == BASE
        assert run['branch'] == 'luffy-stage1-recovery-monitoring-foundation-r1'
        assert run['counts'] == report['counts']
        assert run['exit_code'] == (1 if batch.startswith('legacy-') else 0)
        assert run['network_guard_active'] and run['production_store_guard_active']
        assert not run['network_attempts'] and not run['production_store_attempts'] and not run['wrong_source_imports']
        assert report['counts']['errors'] == 0 and report['counts']['skipped'] == 0
        assert run['start'] <= run['end'] and run['duration_seconds'] > 0
        assert run['argv'] and run['cwd'] and run['selected_arguments']
        assert (ROOT / run['output_file']).is_file()
    base, candidate = read('legacy-base'), read('legacy-candidate')
    def normalize(report):
        return sorted([{'nodeid': f['nodeid'], 'when': f['when'],
                        'path': 'tests/' + f['path'].split('/tests/', 1)[1],
                        'line': f['line'], 'message': f['message']} for f in report['failures']], key=lambda v: v['nodeid'])
    identities = normalize(base)
    assert identities == normalize(candidate) and len(identities) == 3
    assert all(f['line'] == 628 and "assert [] == ['set_leverage']" in f['message'] for f in identities)
    assert read('legacy-base', 'run')['base_snapshot']['source_commit'] == BASE
    assert all(f['full_traceback'] for f in base['failures'] + candidate['failures'])
    comparison = {'result': 'PASS', 'base': BASE, 'base_run': 'legacy-base.run.json',
                  'candidate_run': 'legacy-candidate.run.json', 'normalization': 'Only source-root prefix removed from failure path; nodeid, phase, line and entire crash message retained.',
                  'same_failures': identities, 'same_test_arguments': read('legacy-base', 'run')['selected_arguments'] == read('legacy-candidate', 'run')['selected_arguments'],
                  'source_test_sha256_equal': read('legacy-base', 'run')['source_files_sha256']['tests/test_activation_risk_baseline.py'] == read('legacy-candidate', 'run')['source_files_sha256']['tests/test_activation_risk_baseline.py']}
    assert comparison['same_test_arguments'] and comparison['source_test_sha256_equal']
    (ROOT / 'baseline-comparison.json').write_text(json.dumps(comparison, indent=2) + '\n')
    intended = set(read('original-campaign-collection')['selected'])
    assert len(intended) == 563
    mapping = {}
    for batch in ['control-entry', 'recovery-owner', 'risk-health', 'legacy-candidate']:
        report = read(batch)
        calls = [r for r in report['reports'] if r['when'] == 'call']
        assert {r['nodeid'] for r in calls} == set(report['selected'])
        assert len(calls) == len(report['selected'])
        for call in calls:
            assert call['nodeid'] not in mapping
            mapping[call['nodeid']] = {'batch': batch, 'outcome': call['outcome'],
                                     'baseline_equal': batch == 'legacy-candidate'}
    assert set(mapping) == intended
    assert set(read('risk-health')['deselected']) == set(candidate['selected'])
    coverage = {'result': 'PASS', 'original_campaign_nodes': 563, 'rerun_nodes': len(mapping),
                'missing': [], 'extra': [], 'pass': sum(r['outcome'] == 'passed' for r in mapping.values()),
                'baseline_equal_failure': sum(r['baseline_equal'] for r in mapping.values()),
                'original_failure_evidence': '../stage1-recovery-monitoring-foundation-r1/focused-tests.txt',
                'historical_limitation': 'Original file preserves progress only, no terminal traceback/argv/exit metadata. The old report attributes exhaustion to a 1024 FD ceiling. This is not retroactively presented as a complete recorded test batch. The intended 563-node selection is reconstructed and independently collected; every node maps to a bounded recorded rerun.',
                'node_mapping': mapping}
    original = ROOT.parent / 'stage1-recovery-monitoring-foundation-r1/focused-tests.txt'
    coverage['original_failure_sha256'] = hashlib.sha256(original.read_bytes()).hexdigest()
    (ROOT / 'descriptor-rerun-coverage.json').write_text(json.dumps(coverage, indent=2) + '\n')
    print('PASS: exact-base comparison; 563/563 mapped; 560 passed + 3 identical base failures; guards active and zero attempts.')


if __name__ == '__main__':
    main()
