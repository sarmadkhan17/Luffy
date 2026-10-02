#!/usr/bin/env python3
"""One bounded read-only Stage-5 inventory; proposal artifacts only.

There is no persisted canonical opportunity registry / expected-net-value
producer in this lineage. With no frozen versions the truthful exact-version
candidate set is empty. If versions appear, refuse rather than manufacture
opportunities from research, confidence, PF, or old decisions.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sqlite3
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from trader.portfolio.allocator import (
    Evidence, Inputs, Portfolio, Position, Source, Status, allocate, canonical,
    persist, verify,
)
from trader.engine.evidence_capture import verify_snapshot
from trader.engine.protection_snapshot import STALE_AFTER_S

TABLES = ('strategy_versions', 'strategy_validation_receipts',
          'strategy_version_installs', 'strategy_probation_receipts',
          'strategy_approval_requests', 'strategy_approval_decisions',
          'strategy_version_events', 'strategy_governor_events', 'strategy_capacity_receipts')


def gather(db_path: Path, risk_policy: dict, now_ms: int) -> tuple[Inputs, dict]:
    # No Journal constructor or schema creation, immutable snapshot transaction.
    with sqlite3.connect(db_path.resolve().as_uri() + '?mode=ro', uri=True, timeout=3) as db:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not set(TABLES).issubset(tables):
            raise ValueError('STAGE5_SCHEMA_UNAVAILABLE')
        inventory = {}
        for table in TABLES:
            # Fixed allowlist; bounded rows/read, no unbounded history scanning.
            rows = [dict(r) for r in db.execute('SELECT * FROM ' + table + ' LIMIT 1001')]
            if len(rows) > 1000:
                raise ValueError('SHADOW_READ_BOUND_EXCEEDED')
            inventory[table] = rows
        kv = dict(db.execute("SELECT key,value FROM state_kv WHERE key IN "
                            "('venue_position_snapshot','control_state','risk_assessment','risk_state','account_margin_observation')"))
        if inventory['strategy_versions']:
            raise ValueError('CURRENT_EXACT_VERSION_OPPORTUNITY_ADAPTER_UNAVAILABLE')
        snapshot = json.loads(kv.get('venue_position_snapshot', 'null'))
        if verify_snapshot(snapshot) is None or snapshot['completeness'] != 'COMPLETE':
            raise ValueError('PORTFOLIO_SNAPSHOT_UNVERIFIED_OR_INCOMPLETE')
        sources = (Source.freeze('stage5_inventory', inventory),
                   Source.freeze('venue_position_snapshot', snapshot),
                   Source.freeze('current_authority_state', kv),
                   Source.freeze('risk_policy_config', risk_policy),
                   Source.freeze('portfolio_freshness_policy', {'source': 'trader.engine.protection_snapshot.STALE_AFTER_S', 'seconds': STALE_AFTER_S}))
        portfolio = Portfolio(snapshot['snapshot_id'], snapshot['observed_at_ms'],
                              snapshot['observed_at_ms'] + int(STALE_AFTER_S * 1000),
                              Evidence(Status.ESTABLISHED, ('venue_position_snapshot', 'portfolio_freshness_policy')),
                              tuple(Position(p['instrument_id'], snapshot['market_type'], p['side'].upper(),
                                             str(p['quantity']), None) for p in snapshot['positions']),
                              ('venue_position_snapshot',))
        i = Inputs(now_ms, (), portfolio, Evidence(Status.UNKNOWN, (),
                   canonical({'reason': 'NO_CURRENT_REGISTERED_PORTFOLIO_RELATIONSHIP_EVIDENCE'})),
                   Evidence(Status.ESTABLISHED if risk_policy else Status.UNAVAILABLE,
                            ('risk_policy_config',)), kv.get('control_state', 'UNKNOWN'), sources)
        details = dict(version_count=0, current_exact_version_candidate_count=0,
                       candidate_basis='NO_FROZEN_STRATEGY_VERSIONS; NO_OPPORTUNITIES_FABRICATED',
                       source_counts={t: len(v) for t, v in inventory.items()},
                       portfolio_position_count=len(portfolio.positions),
                       portfolio_fresh=portfolio.as_of_ms <= now_ms <= portfolio.valid_until_ms,
                       control_state=i.control_state, expected_economics='UNAVAILABLE',
                       expected_economics_basis='No validated forward net-value estimator; historical WR/PF and realized net P&L are not expected value',
                       position_plan_identity='UNAVAILABLE_LEGACY_HOLDINGS',
                       read_only=True, authenticated_requests=0, production_mutations=0)
        return i, details


def main():
    import yaml
    parser = argparse.ArgumentParser()
    parser.add_argument('--db', type=Path, default=Path('data/luffy.db'))
    parser.add_argument('--config', type=Path, default=Path('config.yaml'))
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    now = time.time_ns() // 1_000_000
    try:
        # Retain only existing Risk policy, never credentials/config environment.
        config = yaml.safe_load(args.config.read_text())
        inputs, details = gather(args.db, config.get('risk', {}), now)
        proposal = allocate(inputs)
        if not verify(proposal, inputs):
            raise ValueError('SHADOW_REPLAY_REFUSED')
        path = persist(proposal, args.output / 'proposals')
        result = json.loads(proposal.result_json)
        receipt = dict(package='LUFFY-PORTFOLIO-ALLOCATOR-R1', as_of_ms=now,
                       status='PASS' if details['portfolio_fresh'] else 'BLOCKED',
                       result=result['decision'], reason=result['reason'],
                       proposal_id=proposal.proposal_id, proposal_path=str(path),
                       replay='PASS', **details)
        if not details['portfolio_fresh']:
            receipt['blocker'] = 'PORTFOLIO_EVIDENCE_STALE'
    except (ValueError, KeyError, sqlite3.Error) as exc:
        receipt = dict(package='LUFFY-PORTFOLIO-ALLOCATOR-R1', as_of_ms=now,
                       status='BLOCKED', blocker=str(exc), read_only=True)
    args.output.mkdir(parents=True, exist_ok=True)
    # Each run publishes an immutable receipt, never overwrites a prior run.
    target = args.output / ('shadow-' + str(now) + '.json')
    with target.open('x') as f:
        f.write(canonical(receipt) + '\n')
    print(canonical(receipt))


if __name__ == '__main__':
    main()
