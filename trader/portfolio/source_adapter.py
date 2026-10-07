"""Bounded read-only delivery of existing immutable Stage6 publications.

Paths route the existing opportunity_live/economics stores; this module neither
publishes evidence nor registers economic/allocation authority. No rebinding of
an old receipt to a new context, version, book or cut is permitted.
"""
from pathlib import Path
from itertools import islice
import json
from . import opportunity_live as live, economics
from .allocator import Source, canonical


def directory(config, role, root):
    return Path(config.get('portfolio', {}).get('source_directories', {}).get(role, Path(root) / role))


def records(path):
    if not path.exists():
        return ()
    paths = sorted(islice(path.glob('*.json'), 65))
    if len(paths) > 64:
        raise ValueError('NORMAL_SOURCE_READ_BOUND_EXCEEDED')
    for p in paths:
        if p.stat().st_size > 8 * 1024**2:
            raise ValueError('NORMAL_SOURCE_PAYLOAD_BOUND_EXCEEDED')
    return paths


def contexts(config, root, cut, snapshot):
    requests = []
    for path in records(directory(config, 'contexts', root)):
        receipt = live.load(path)
        raw = json.loads(receipt.payload_json)
        # Exact cuts only. Never carry/relabel an older economic binding.
        if raw['as_of_ms'] > cut:
            continue
        sources = tuple(Source(**s) for s in raw['sources'])
        statuses = raw['source_status']
        required = set(raw['required_roles']) | {'signals', 'strategy_version', 'portfolio'}
        if not all(statuses[r].get('status') == 'AVAILABLE' and
                   (statuses[r].get('valid_until_ms') is None or
                    cut <= statuses[r]['valid_until_ms']) for r in required):
            continue
        book = next((json.loads(s.payload_json)['data'] for s in sources if s.source_id == 'portfolio'), None)
        if book != snapshot:
            raise ValueError('NORMAL_CONTEXT_PORTFOLIO_CUT_DIFFERS')
        live.replay(receipt, sources, cut)
        requests.append(dict(**{k: raw[k] for k in ('as_of_ms', 'symbol', 'instrument_id',
            'cycle_id', 'candidate_id', 'required_roles')}, sources=sources,
            **({'decision_grade': True} if raw.get('decision_grade') else {})))
    latest = max((r['as_of_ms'] for r in requests), default=None)
    return [r for r in requests if r['as_of_ms'] == latest]


def economic_inputs(reader, receipt, authority, config):
    root = getattr(reader, 'source_root', None)
    if root is None:
        return None
    matches = []
    p = json.loads(receipt.payload_json)
    for path in records(directory(config, 'economics', root)):
        text = path.read_text()
        raw = json.loads(text)
        er = economics.from_payload(raw)
        if path.stem != er.receipt_id or text != canonical(raw) + '\n':
            raise ValueError('NORMAL_ECONOMICS_PUBLICATION_ID_DIFFERS')
        ei = economics.from_inputs(raw['inputs'])
        if ei.binding.opportunity_id != p['opportunity_id']:
            continue
        dims = {k: getattr(ei.binding, k) for k in ('units', 'quantity_basis', 'capital_basis',
            'horizon_interpretation', 'cost_treatment', 'uncertainty_treatment', 'freshness_semantics')}
        if ei.binding != live.economic_binding(receipt, **dims):
            raise ValueError('NORMAL_ECONOMICS_EXACT_CUT_DIFFERS')
        if receipt.as_source() not in ei.context or authority not in ei.context:
            raise ValueError('NORMAL_ECONOMICS_AUTHORITY_CUT_DIFFERS')
        if not economics.verify(er, ei, getattr(reader, 'current_time_ms', p['as_of_ms'])):
            raise ValueError('NORMAL_ECONOMICS_REPLAY_REFUSED')
        matches.append(ei)
    if len(matches) > 1:
        raise ValueError('NORMAL_ECONOMICS_AMBIGUOUS')
    return matches[0] if matches else None


def availability(config, root):
    from .candidate_bridge import ALLOCATION_AUTHORITY_ADAPTERS
    return dict(stores={role: dict(path=str(directory(config, role, root)),
                    present=directory(config, role, root).exists(),
                    publication_count=len(records(directory(config, role, root))))
                       for role in ('contexts', 'economics')},
                registered_authorities=dict(gross=len(economics.GROSS_MODELS),
                    cost_scope=len(economics.COST_SCOPE_MODELS),
                    uncertainty=len(economics.RESERVE_MODELS),
                    allocation=len(ALLOCATION_AUTHORITY_ADAPTERS)))
