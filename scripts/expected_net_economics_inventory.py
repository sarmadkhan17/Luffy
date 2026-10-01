#!/usr/bin/env python3
"""Bounded read-only real evidence inventory; no opportunities manufactured."""
import argparse
from pathlib import Path
import sqlite3
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from trader.portfolio.economics import VERSION, GROSS_MODELS, RESERVE_MODELS, COST_SCOPE_MODELS
from trader.portfolio.allocator import canonical, digest

TABLES = ('strategy_versions', 'strategy_validation_receipts', 'strategy_probation_receipts',
          'strategy_capacity_receipts', 'versioned_paper_cost_receipts',
          'versioned_paper_cost_sources', 'research_candidates', 'research_tests')
FILES = ('trader/strategy/spec_evidence.py', 'trader/strategy/portfolio_evidence.py',
         'trader/strategy/factory_handoff.py', 'trader/research/referee.py',
         'trader/research/evaluate.py', 'trader/observability/execution_calibration.py',
         'trader/engine/paper_cost_evidence.py', 'trader/portfolio/economics.py',
         'trader/portfolio/allocator.py')
MISSING = (
    'Exact-version conditional forward outcome distribution, horizon/context, sample period, gross units, cost baseline/exclusion and independently validated calibration method/version.',
    'Calibrated economic uncertainty reserve with measured coverage/error, units, sample period, context and binding to gross/cost evidence; no fixed haircut or generic confidence mapping exists.',
    'Authoritative commission and validated forward execution/slippage evidence plus funding/borrow applicability for the opportunity horizon; calibrated scope bridge to the existing verified cost protocol.',
    'Authoritative event-driven exact-version opportunity registry and frozen Opportunity Context; current versions/evidence cannot be manufactured from research or legacy decisions.',
)


def inspect(db_path, now_ms, root=Path('.')):
    if GROSS_MODELS or RESERVE_MODELS or COST_SCOPE_MODELS:
        raise ValueError('REGISTERED_MODELS_REQUIRE_NEW_AUTHORITY_AUDIT')
    with sqlite3.connect(db_path.resolve().as_uri() + '?mode=ro', uri=True, timeout=3) as db:
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        counts, hashes = {}, {}
        db.row_factory = sqlite3.Row
        for table in TABLES:
            if table not in tables:
                counts[table] = 'UNAVAILABLE'
                continue
            # Fixed allowlist, bounded facts. A large inventory is refused.
            rows = [dict(r) for r in db.execute('SELECT * FROM ' + table + ' LIMIT 1001')]
            if len(rows) > 1000:
                raise ValueError('ECONOMICS_INVENTORY_READ_BOUND_EXCEEDED:' + table)
            counts[table], hashes[table] = len(rows), digest(sorted(rows, key=canonical))
        state = db.execute("SELECT value FROM state_kv WHERE key='control_state'").fetchone() if 'state_kv' in tables else None
    return dict(package=VERSION, as_of_ms=now_ms, source_counts=counts, source_set_hashes=hashes,
                inspected_file_hashes={f: __import__('hashlib').sha256((root / f).read_bytes()).hexdigest() for f in FILES},
                real_economic_value='UNAVAILABLE', economic_model_status='INSUFFICIENT_EVIDENCE',
                established_current_expected_net_receipts=0,
                current_opportunity_count=0 if counts.get('strategy_versions') == 0 else 'UNAVAILABLE',
                opportunity_basis='NO_FABRICATED_CANDIDATES; NO_CURRENT_OPPORTUNITY_REGISTRY_ADAPTER',
                gross_model_registry=[], reserve_model_registry=[], cost_scope_registry=[],
                missing_real_evidence=list(MISSING), control_state=state[0] if state else 'UNKNOWN',
                allocator_outcome='CASH / NO_ALLOCATION',
                allocator_basis='No complete expected economics; detached contract tests only, no live allocation call',
                read_only=True, production_mutations=0, authenticated_requests=0,
                limitations=['Historical WR/PF, research support, referee and realized paper P&L do not establish forward EV.',
                             'No future evidence collection/evaluation or further economics formula is authorized by this inventory.'])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--db', type=Path, default=Path('data/luffy.db'))
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    now = time.time_ns() // 1000000
    receipt = inspect(args.db, now)
    args.output.mkdir(parents=True, exist_ok=True)
    target = args.output / ('real-evidence-' + str(now) + '.json')
    with target.open('x') as f:
        f.write(canonical(receipt) + '\n')
    print(canonical(receipt))


if __name__ == '__main__':
    main()
