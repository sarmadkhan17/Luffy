"""Evidence/chronology/code lenses over the existing vault and record readers.

No Graphify imports, generated graph input, new database or inferred edges.
"""
from pathlib import Path
import hashlib
from datetime import datetime, timezone

from .queries import query, record


def enrich(base, journal, root, vault, cfg):
    nodes, edges = base['nodes'], base['edges']
    for node in nodes:
        node['lenses'] = ['Knowledge', 'Timeline']
    for kind in ('research', 'decision', 'strategies'):
        try:
            result = query(journal, kind, root=root, cfg=cfg)
        except (ValueError, KeyError, TypeError, OSError):
            base['limits'].append(kind + ' evidence source unavailable or invalid')
            continue
        for r in result['records'][:50]:
            nodes.append(dict(id=r['record_id'], label=r['record_id'], kind=r['kind'],
                              lenses=['Evidence', 'Timeline'], record=r,
                              provenance=dict(id=r['record_id'], source=str(r['source']),
                                observed_at=_time(r['timestamp']), time_basis=r['time_basis'],
                                summary=str(r['value']), classification=r['verification'] + ' · historical')))
            # Connections only when exact stored reference fields name a
            # returned record. No semantic or timestamp joins.
    ids = {n['id'] for n in nodes}
    for n in list(nodes):
        r = n.get('record')
        if not r:
            continue
        for field, target_kind in (('decision_id', 'decision'), ('version_id', 'strategy_version')):
            val = r['value'].get(field) if isinstance(r['value'], dict) else None
            target = f'{target_kind}:{val}'
            if val and target in ids and target != n['id']:
                edges.append(dict(id=f"exact:{n['id']}:{field}", source=n['id'], target=target,
                                  relation=field, kind='typed', resolved=True, evidence=r['record_id']))
    # Reuse note source_paths. Allow only repository code, never arbitrary
    # files, data stores, secrets, graph output, or paths supplied by a browser.
    from trader.dashboard.owner_api import _frontmatter
    for node in list(nodes):
        if 'Knowledge' not in node['lenses']:
            continue
        path = (Path(vault) / node['id']).resolve()
        if not path.is_relative_to(Path(vault).resolve()):
            continue
        fm, _ = _frontmatter(path.read_text(errors='replace'))
        for ref in fm.get('source_paths', [])[:50] if isinstance(fm.get('source_paths'), list) else []:
            ref = str(ref)
            p = (Path(root) / ref).resolve()
            if (not p.is_relative_to(Path(root).resolve()) or not ref.startswith(('trader/', 'frontend/src/', 'scripts/'))
                    or p.suffix not in ('.py', '.ts', '.tsx', '.sh') or not p.is_file()):
                continue
            code_id = 'code:' + ref
            if code_id not in ids:
                ids.add(code_id)
                sha = hashlib.sha256(p.read_bytes()).hexdigest()
                nodes.append(dict(id=code_id, label=ref, kind='Code', lenses=['Code'],
                                  provenance=dict(id=code_id, source=ref, observed_at=None,
                                                  time_basis='NOT_RECORDED', summary='Content SHA256 ' + sha,
                                                  classification='read-only file metadata; linkage declared in note')))
            edges.append(dict(id='code-ref:' + node['id'] + ':' + ref, source=node['id'], target=code_id,
                              relation='source_path', kind='typed', resolved=True,
                              evidence='vault:' + node['id'] + '#source_paths'))
    base['lenses'].update({
        'Evidence': dict(available=True, note='Exact internal records; RECORDED is not VERIFIED. Missing links are UNAVAILABLE.'),
        'Code': dict(available=True, note='Read-only source_paths metadata; no source link means UNLINKED. Graphify is not a runtime dependency.'),
        'Timeline': dict(available=True, note='Event chronology for stored records; file modification time for notes. Missing event time is NOT_RECORDED.')})
    base['limits'].append('No inferred trace edges. Evidence catalogs are bounded; exact owner queries provide details.')
    base.update(total_nodes=len(nodes), returned_nodes=len(nodes))
    return base


def _time(value):
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value/1000, timezone.utc).isoformat()
    return value
