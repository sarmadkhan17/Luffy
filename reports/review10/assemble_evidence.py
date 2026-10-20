"""Reconcile Review10 exact ownership and assessed source identities offline."""
import collections
import csv
import hashlib
import io
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'reports/review10'
BASE = '4deca5c275c30c2c59c227b5170bab651cf777fe'


def sha(blob):
    return hashlib.sha256(blob).hexdigest() if blob is not None else None


def committed(rev, path):
    result = subprocess.run(['git', 'show', rev + ':' + path], cwd=ROOT, capture_output=True)
    return result.stdout if result.returncode == 0 else None


def main():
    ownership = json.loads((OUT / 'ownership.json').read_text())
    expected = {p: s['id'] for s in ownership['subsystems'] for p in s['files']}
    csv_blob = Path(ownership['ownership_csv']).read_bytes()
    assert sha(csv_blob) == ownership['ownership_csv_sha256']
    assert sha(Path(ownership['inventory_source']).read_bytes()) == ownership['inventory_sha256']
    csv_owned = {r['path']: r['primary_subsystem'] for r in csv.DictReader(io.StringIO(csv_blob.decode()))
                 if r['primary_subsystem'] in {'knowledge', 'legacy', 'chat', 'notify'}}
    assert expected == csv_owned
    rows = sum((json.loads((OUT / name).read_text()) for name in
                ('knowledge-files.json', 'legacy-files.json', 'chat-notify-files.json')), [])
    assert len(rows) == len(expected) == 201
    assert collections.Counter(r['path'] for r in rows) == collections.Counter(expected.keys())
    for row in rows:
        path = row['path']
        assert row['subsystem'] == expected[path]
        assert row['classification'] in {'KEEP', 'SIMPLIFY', 'MERGE', 'REMOVE'}
        for key in ('purpose', 'consumers', 'evidence', 'design_boundary', 'costs_removal', 'inspection'):
            assert row.get(key), (path, key)
        baseline = committed(BASE, path)
        current = (ROOT / path).read_bytes() if (ROOT / path).exists() else None
        if baseline is None:
            assert row.get('presence') == 'removed_before_review10'
            historic = committed(ownership['inventory_revision'], path)
            assert row['baseline_sha256'] == sha(historic)
            row['historical_sha256'] = sha(historic)
            row['historical_lines'] = len(historic.splitlines())
            row['historical_revision'] = ownership['inventory_revision']
            row['baseline_sha256'], row['baseline_lines'] = None, 0
        else:
            assert row['baseline_sha256'] == sha(baseline), path
            assert row['baseline_lines'] == len(baseline.splitlines()), path
        assert row['reviewed_sha256'] == sha(current), path
        assert row['reviewed_lines'] == (len(current.splitlines()) if current is not None else 0), path
        row['baseline_revision'] = BASE
        row['classification_note'] = ('Preserve prior removal and git history; not a Review10 deletion.'
                                      if baseline is None else 'Required file retained; SIMPLIFY does not imply removal.')
    rows.sort(key=lambda r: r['path'])
    (OUT / 'files.json').write_text(json.dumps(rows, indent=2, ensure_ascii=False) + '\n')
    with (OUT / 'files.csv').open('w', newline='') as handle:
        fields = ('path', 'subsystem', 'classification', 'baseline_lines', 'reviewed_lines',
                  'baseline_sha256', 'reviewed_sha256', 'purpose', 'consumers', 'design_boundary', 'costs_removal')
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)
    report = ['# Review10 file-by-file assessment', '',
              'Exact assigned coverage: 201/201 (200 current files; one prior removal assessed from retained source).', '']
    for row in rows:
        report.extend([f"## {row['path']} — {row['classification']}", '',
                       f"Owner `{row['subsystem']}`; committed baseline {row['baseline_lines']} lines, reviewed {row['reviewed_lines']} lines. "
                       f"Baseline SHA256 `{row['baseline_sha256']}`; reviewed `{row['reviewed_sha256']}`.", ''])
        if row.get('historical_sha256'):
            report.extend([f"Prior removed source: {row['historical_lines']} lines, SHA256 `{row['historical_sha256']}`, "
                           f"revision `{row['historical_revision']}`. Preserve removal and historical git evidence.", ''])
        for key in ('purpose', 'consumers', 'evidence', 'design_boundary', 'costs_removal', 'inspection'):
            value = row[key]
            if not isinstance(value, str):
                value = '\n```json\n' + json.dumps(value, indent=2, ensure_ascii=False) + '\n```'
            report.extend([f'**{key}:** {value}', ''])
    (OUT / 'FILE_REPORT.md').write_text('\n'.join(report))
    validation = dict(source_revision=BASE, assigned_files=201, current_files=200,
                      historical_removed_files=1, exact_inventory_csv_match=True, duplicate_assessments=0,
                      classifications=dict(collections.Counter(r['classification'] for r in rows)),
                      subsystems=dict(collections.Counter(r['subsystem'] for r in rows)),
                      baseline_physical_lines=sum(r['baseline_lines'] for r in rows),
                      historical_source_lines=sum(r.get('historical_lines', 0) for r in rows),
                      final_physical_lines=sum(r['reviewed_lines'] for r in rows),
                      hashes_and_required_evidence_fields_verified=True)
    (OUT / 'coverage-validation.json').write_text(json.dumps(validation, indent=2) + '\n')
    print(json.dumps(validation, indent=2))


if __name__ == '__main__':
    main()
