"""Review11 read-only structural/hash audit; never imports or runs laboratory code.

Run from the checkout root. Writes only reports/review11/artifact-audit.json.
This verifies persisted bytes and arithmetic, not new statistical validation.
"""
from collections import Counter, defaultdict
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import re

OUT = Path('reports/review11')
HEX = re.compile(r'^[a-f0-9]{64}$')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError('duplicate JSON key: ' + key)
        result[key] = value
    return result


def load(text):
    return json.loads(text, object_pairs_hook=pairs,
                      parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)))


def main():
    files = OUT.joinpath('artifact-paths.txt').read_text().splitlines()
    tracked = __import__('subprocess').check_output(['git', 'ls-files', '-z']).decode().split('\0')
    by_hash = defaultdict(list)
    for name in tracked:
        p = Path(name)
        if name and p.is_file():
            by_hash[sha(p)].append(name)
    parsed, records, links, truth = {}, [], [], []
    self_hash_checks = []
    for name in files:
        p = Path(name)
        raw = p.read_text()
        record = dict(path=name, bytes=p.stat().st_size, lines=len(raw.splitlines()), sha256=sha(p))
        if p.suffix in ('.json', '.jsonl'):
            data = load(raw) if p.suffix == '.json' else [load(s) for s in raw.splitlines() if s.strip()]
            parsed[name] = data
            for obj in data if isinstance(data, list) else [data]:
                if isinstance(obj, dict) and isinstance(obj.get('sha256'), str):
                    body = {k: v for k, v in obj.items() if k != 'sha256'}
                    if obj.get('cell') == 'BENCH/C0/N1' and obj.get('schema') == 'm3.2-scheme-d-world-receipt.v1':
                        # Original reference benchmark adds timing after world_receipt seals its body.
                        body.pop('runtime_seconds', None)
                    canonical = json.dumps(body, sort_keys=True, separators=(',', ':'), allow_nan=False)
                    assert hashlib.sha256(canonical.encode()).hexdigest() == obj['sha256'], name
                    self_hash_checks.append(name)
            record['parsed_records'] = len(data) if isinstance(data, list) else 1
            if isinstance(data, dict):
                record['schema'] = data.get('schema', data.get('schema_version'))
                record['keys'] = list(data)
            elif data:
                record['keys'] = sorted({k for r in data for k in r})
                record['terminal_scalars'] = {k: v for k, v in data[-1].items() if not isinstance(v, (list, dict))}
            if '/tables/' in name:
                rows = data['records']
                assert len(rows) == 384 and sorted(r['hypothesis'] for r in rows) == list(range(384)), name
                counts = Counter(r['classification'] for r in rows)
                nulls = [r for r in rows if r['classification'] == 'true_null']
                assert all(r['sign'] is None for r in nulls), name
                if '/package/' in name:
                    assert not data['unresolved_hypotheses'], name
                    assert not any('refus' in k or 'unclass' in k for k in counts), name
                truth.append(dict(path=name, cell=data.get('cell', p.stem.replace('_', '/')), counts=dict(counts), role=data.get('role')))
        elif p.suffix == '.sha256':
            for line in raw.splitlines():
                digest, target = line.split(maxsplit=1)
                assert HEX.fullmatch(digest), name
                target_path = p.parent / target
                intentional = 'discarded' in p.name
                ok = target_path.is_file() and sha(target_path) == digest
                links.append(dict(source=name, key=target, target=str(target_path), expected=digest,
                                  status='SUPERSEDED_BEFORE_RNG' if intentional else 'MATCH' if ok else 'MISMATCH'))
        records.append(record)

    def visit(value, source, trail=''):
        if isinstance(value, dict):
            # Path-addressed hashes are checked against that path, never a same-hash substitute.
            for key, val in value.items():
                loc = trail + '/' + key
                if isinstance(val, str) and HEX.fullmatch(val):
                    candidates = []
                    if '/' in key or '.' in key:
                        candidates = [Path(key), Path(source).parent / key]
                        if trail == '/package_files':
                            candidates.insert(0, Path(source).parent / 'package' / key)
                    if key == 'sha256' and isinstance(value.get('path', value.get('file')), str):
                        target = value.get('path', value.get('file'))
                        candidates = [Path(target), Path(source).parent / target]
                    existing = next((p for p in candidates if p.is_file()), None)
                    if existing:
                        links.append(dict(source=source, key=loc, target=str(existing), expected=val,
                                          status='MATCH' if sha(existing) == val else 'MISMATCH'))
                    elif candidates:
                        links.append(dict(source=source, key=loc, target=str(candidates[0]), expected=val,
                                          status='EXTERNAL_OR_MISSING'))
                    elif by_hash.get(val):
                        links.append(dict(source=source, key=loc, target=by_hash[val], expected=val,
                                          status='DIGEST_PRESENT_ALIAS_NOT_PATH_PROOF'))
                visit(val, source, loc)
        elif isinstance(value, list):
            for i, val in enumerate(value):
                visit(val, source, trail + '/' + str(i))

    for name, data in parsed.items():
        visit(data, name)

    # Reconcile the existing 80-world pilot without generating or permuting a world.
    pilot = Path('scripts/m32_scheme_d_pilot_v4_20260923/out')
    run = parsed[str(pilot / 'RUN_MANIFEST.json')]
    merge = parsed[str(pilot / 'merged/MERGE_MANIFEST.json')]
    worlds = defaultdict(list)
    pvalue_checks = 0
    for spec in run['shards']:
        base = pilot / 'shards' / ('shard_%06d' % spec['id'])
        completion = parsed[str(base.with_suffix('.complete.json'))]
        receipts_path = base.with_suffix('.receipts.jsonl')
        rows = parsed[str(receipts_path)]
        assert completion['receipts_sha256'] == sha(receipts_path)
        assert completion['receipts_bytes'] == receipts_path.stat().st_size
        assert completion['worlds'] == len(rows) == spec['hi'] - spec['lo']
        assert completion['bindings'] == run['bindings']
        assert completion['run_manifest_sha256'] == sha(pilot / 'RUN_MANIFEST.json')
        assert [r['world'] for r in rows] == list(range(spec['lo'], spec['hi']))
        for row in rows:
            assert row['cell'] == spec['cell']
            assert len(row['p']) == len(row['exceedances']) == len(row['signed_hex']) == 384
            for index, (p, n) in enumerate(zip(row['p'], row['exceedances'])):
                if p is not None:
                    assert Fraction(p) == Fraction(1 + n, 1 + row['draws'])
                    pvalue_checks += 1
            # Fixed m=384 and q=1/20, including exact equality, from the frozen contract.
            ordered = sorted((Fraction(p), i) for i, p in enumerate(row['p']) if p is not None)
            eligible = [rank for rank, (p, _) in enumerate(ordered, 1) if p <= Fraction(rank, 384 * 20)]
            rejected = sorted(i for _, i in ordered[:max(eligible, default=0)])
            assert rejected == sorted(row['rejected'])
            assert row['R'] == len(rejected)
            table = parsed['scripts/m32_scheme_d_pre_rng_truth_20260921/package/tables/' + row['cell'].replace('/', '_') + '.json']
            false_count = sum(table['records'][i]['classification'] == 'true_null' for i in rejected)
            assert row['V'] == false_count
            worlds[spec['cell']].append(row)
    for cell, rows in worlds.items():
        stored = parsed[str(pilot / 'merged' / (cell.replace('/', '_') + '.receipts.jsonl'))]
        assert sorted(rows, key=lambda r: r['world']) == stored
        assert len(stored) == 20 and len({r['world'] for r in stored}) == 20
        assert merge['cells'][cell]['sha256'] == sha(pilot / 'merged' / merge['cells'][cell]['file'])
    verdict = parsed[str(pilot.parent / 'PILOT_VERDICT.json')]
    assert verdict['scope'] == 'pilot_only_non_final'
    assert not verdict['full_protocol_acceptance_output']['overall']['passes']
    result = dict(method='Full-byte strict parsing; resolvable declared path hashes; all truth rows; pilot receipt p/exceedance fractions, fixed-m exact BH/rejection/false-discovery arithmetic and shard/merge equality. Alias/external references separately disclosed; no laboratory code executed',
                  files=records, links=links, link_counts=dict(Counter(x['status'] for x in links)),
                  canonical_self_hash_checks=len(self_hash_checks),
                  truth_tables=truth, pilot=dict(worlds=sum(map(len, worlds.values())), cells=len(worlds),
                                               pvalue_checks=pvalue_checks, shard_merge_equal=True,
                                               full_protocol_pass=False, scope=verdict['scope']))
    OUT.joinpath('artifact-audit.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({k: v for k, v in result.items() if k not in ('files', 'links', 'truth_tables')}, indent=2))


if __name__ == '__main__':
    main()
