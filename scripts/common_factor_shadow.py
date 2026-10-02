#!/usr/bin/env python3
"""Bounded read-only current-book context, using the existing candidate reader."""
import argparse
from contextlib import ExitStack
from dataclasses import asdict
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.opportunity_context_shadow import run as candidate_run, _read, _json
from trader.portfolio.allocator import Source, allocate, canonical, inputs_from_payload, persist, verify
from trader.portfolio.common_factor import attach
from trader.portfolio.candidate_bridge import freeze_lineage

PACKAGE = 'LUFFY-STAGE6-PORTFOLIO-COMMON-FACTOR-CONCENTRATION-AUTHORITY-R1'


def run(journal, attention, investigation, config, output):
    detail = candidate_run(journal, attention, investigation, config, output / 'candidate-chain', candidate_bridge=True)
    if detail['status'] != 'PASS':
        raise ValueError('CURRENT_CANDIDATE_INVENTORY_REFUSED:' + canonical(detail['refused']))
    raw = _json(Path(detail['proposal_path']).read_text())
    inputs = inputs_from_payload(raw['inputs'])
    venue = next(s for s in inputs.sources if s.source_id == 'venue_position_snapshot')
    inventory = json.loads(next(s for s in inputs.sources if s.source_id == 'current-shadow-inventory').payload_json)
    if inventory['versions'] and not inputs.candidates:
        raise ValueError('NONEMPTY_VERSION_INVENTORY_WITHOUT_CURRENT_CANDIDATES')
    policy = Source.freeze('owner-risk-policy', {'risk': config.get('risk', {})})
    scan = inventory['scan']
    relations = []
    if scan and scan.get('world_model', {}).get('status') == 'ok':
        relations.append(Source.freeze('current-world-relationships', dict(
            record_json=scan['world_model']['record_json'],
            valid_until_ms=scan['as_of_ms'] + int(config['attention']['stale_seconds'] * 1000))))
    if scan and 'correlation_input' in scan:
        from trader.observability.investigation import source_snapshot
        joined = source_snapshot(attention)
        if joined is None or joined[0]['scan_id'] != scan['scan_id'] or joined[0] != scan:
            raise ValueError('RELATIONSHIP_SCAN_CHANGED_DURING_SHADOW')
        relations.append(Source.freeze('current-attention-relationships',dict(
            schema='attention-relationship-input.v1', scan=joined[0], bars=joined[1],
            valid_until_ms=scan['as_of_ms'] + int(config['attention']['stale_seconds'] * 1000))))
    lineages = []
    with ExitStack() as reads:
        db = _read(reads, journal, time.monotonic() + 5)
        class Reader:
            def query(self, sql, params=()):
                return [dict(r) for r in db.execute(sql, params)]
        # Candidate reader already refuses absent current available-input assertions.
        for c in inputs.candidates:
            if c.valid_until_ms is None:
                raise ValueError('CANDIDATE_LINEAGE_FRESHNESS_UNAVAILABLE')
            lineages.append(freeze_lineage(Reader(), c.version_id,
                as_of_ms=inputs.as_of_ms, valid_until_ms=c.valid_until_ms))
        names = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'strategy_versions' not in names:
            raise ValueError('VERSION_INVENTORY_SCHEMA_UNAVAILABLE')
        observed_count = db.execute('SELECT COUNT(*) FROM (SELECT version_id FROM strategy_versions LIMIT 65)').fetchone()[0]
        if observed_count != detail['version_count']:
            raise ValueError('VERSION_INVENTORY_CHANGED_DURING_SHADOW')
    inputs, receipt = attach(inputs, venue, policy, tuple(lineages), tuple(relations))
    proposal = allocate(inputs)
    if not verify(proposal, inputs) or allocate(inputs_from_payload(json.loads(proposal.inputs_json))) != proposal:
        raise ValueError('COMMON_FACTOR_ALLOCATOR_REPLAY_REFUSED')
    path = persist(proposal, output / 'proposals')
    result = json.loads(proposal.result_json)
    context = json.loads(receipt.result_json)
    audit = dict(package=PACKAGE, status='PASS', as_of_ms=inputs.as_of_ms,
        candidate_count=len(inputs.candidates), context_count=detail['context_count'],
        holdings=context['portfolio']['positions'], portfolio=context['portfolio'],
        current_portfolio_relationships=context['pairs'], measured_relationships=context['measured_relationships'],
        concentration=context['concentration'], duplicate_confidence=context['duplicate_confidence'],
        factor_model=context['factor_model'], decision=result['decision'], reason=result['reason'],
        global_blockers=result['global_blockers'], control_state=inputs.control_state,
        receipt=receipt.payload(), proposal_id=proposal.proposal_id, proposal_path=str(path),
        replay='PASS', read_only=True, authenticated_requests=0, production_mutations=0,
        trading_behavior_changed=False)
    output.mkdir(parents=True, exist_ok=True)
    (output / ('shadow-' + str(inputs.as_of_ms) + '.json')).write_text(canonical(audit) + '\n')
    return audit


def main():
    import sqlite3
    import yaml
    p = argparse.ArgumentParser()
    for name in ('journal', 'attention', 'investigation', 'config', 'output'):
        p.add_argument('--' + name, type=Path, required=True)
    a = p.parse_args()
    cfg = yaml.safe_load(a.config.read_text())
    safe = {k: cfg.get(k, {}) for k in ('attention', 'risk', 'strategies')}
    try:
        audit = run(a.journal, a.attention, a.investigation, safe, a.output)
    except (ValueError, TypeError, KeyError, sqlite3.Error) as exc:
        audit = dict(package=PACKAGE, status='BLOCKED', blocker=str(exc), read_only=True,
                     production_mutations=0, authenticated_requests=0)
    print(canonical(audit))
    return 0 if audit['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
