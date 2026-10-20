"""Reconcile canonical Review11 coverage and successful focused test identities."""
from collections import Counter
import csv
import hashlib
import json
from pathlib import Path
import re

OUT = Path('reports/review11')


def read(name):
    return json.loads(OUT.joinpath(name).read_text())


def main():
    partitions = ['artifact-files.json', 'core-files.json', 'persistence-files.json',
                  'scripts-files.json', 'scripts-math-files.json', 'scripts-qualification-files.json']
    rows = []
    for name in partitions:
        data = read(name)
        group = data if isinstance(data, list) else data['rows']
        for row in group:
            row = dict(row)
            row['review_evidence'] = 'reports/review11/' + name
            rows.append(row)
    baseline = {r['path']: r for r in read('baseline-validation.json')['files']}
    counts = Counter(r['path'] for r in rows)
    assert set(counts) == set(baseline), (set(baseline) - set(counts), set(counts) - set(baseline))
    assert all(n == 1 for n in counts.values()), counts
    changed, removed = [], []
    for row in rows:
        name = row['path']
        p = Path(name)
        row['baseline_sha256'] = baseline[name]['source_sha256']
        row['baseline_lines'] = baseline[name]['source_lines']
        row['final_sha256'] = hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None
        row['final_lines'] = len(p.read_bytes().splitlines()) if p.exists() else 0
        row['subsystem'] = 'persistence' if name.startswith('trader/persistence/') else 'm32'
        if not p.exists():
            assert row['decision'] == 'REMOVE' and row['fix_status'] == 'IMPLEMENTED_OFFLINE', name
            removed.append(name)
        elif row['final_sha256'] != row['baseline_sha256']:
            changed.append(name)
        for key in ('path', 'lines', 'purpose', 'functions', 'callers', 'consumers', 'side_effects',
                    'necessity', 'issue', 'decision', 'fix_status'):
            assert key in row, (name, key)
    assert changed == ['trader/persistence/backup.py'], changed
    assert removed == ['scripts/m32_p7c4_persistence_truth_20260921/_scratch_env_check.py'], removed
    rows.sort(key=lambda r: r['path'])
    OUT.joinpath('files.json').write_text(json.dumps(rows, indent=2) + '\n')
    fields = ['path', 'lines', 'purpose', 'functions', 'callers', 'consumers', 'side_effects',
              'necessity', 'issue', 'decision', 'fix_status', 'baseline_sha256', 'final_sha256', 'review_evidence']

    def plain(value):
        return json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value)

    with OUT.joinpath('files.csv').open('w', newline='') as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: plain(row[k]) for k in fields})
    md = ['# Review11 exact per-file evidence', '',
          'Source contracts are distinguished from executed checks in the authoritative [files.json](files.json). '
          'Each original owned path occurs exactly once; deletion retains its complete source in [removed-scratch.txt](removed-scratch.txt). '
          'Frozen data received full-byte structured inspection, including every stored record; this is not new statistical validation.', '',
          '| PATH | LINES | PURPOSE | FUNCTIONS | CALLERS | CONSUMERS | SIDE_EFFECTS | NECESSITY | ISSUE | DECISION | FIX_STATUS |',
          '|---|---:|---|---|---|---|---|---|---|---|---|']
    for row in rows:
        values = [row[k] for k in fields[:11]]
        values[0] = '[' + row['path'] + '](../../' + row['path'] + ')' if row['final_sha256'] else row['path'] + ' (removed)'
        md.append('| ' + ' | '.join(plain(v).replace('|', '\\|').replace('\n', ' ') for v in values) + ' |')
    OUT.joinpath('FILE_REPORT.md').write_text('\n'.join(md) + '\n')
    core = read('core-verification-nodeids.json')
    nodes = {n for group in core['nodeids'].values() for n in group}
    persistence = read('persistence-tested-nodeids.json')
    nodes.update(persistence['initial_passed_nodeids'])
    script_nodes = re.findall(r'^(tests/[^\s]+) PASSED', OUT.joinpath('scripts-tests.txt').read_text(), re.M)
    assert len(script_nodes) == 105, len(script_nodes)
    nodes.update(script_nodes)
    assert len(nodes) == 306, len(nodes)
    OUT.joinpath('tested-nodeids.json').write_text(json.dumps(sorted(nodes), indent=2) + '\n')
    result = dict(canonical_files=len(rows), subsystem_counts=dict(Counter(r['subsystem'] for r in rows)),
                  decisions=dict(Counter(r['decision'] for r in rows)), exact_once=True,
                  original_lines=sum(r['source_lines'] for r in baseline.values()),
                  final_owned_lines=sum(r['final_lines'] for r in rows), current_files=sum(r['final_sha256'] is not None for r in rows),
                  original_owned_bytes=sum(r['source_bytes'] for r in baseline.values()),
                  changed_owned_paths=changed, removed_owned_paths=removed,
                  all_retained_research_artifacts_unchanged=True, unique_focused_tests_passed=len(nodes),
                  note='Passing retained-defect characterizations are included as three diagnostic cases, not remediation/qualification.')
    OUT.joinpath('coverage-validation.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
