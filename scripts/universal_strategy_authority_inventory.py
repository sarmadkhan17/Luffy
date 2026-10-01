"""Read-only current authority inventory plus isolated activation refusal.

SQLite read-only source; Governor probes only a temporary database backup.
No network, venue, grant creation, operational control changes or deployment.
"""
import argparse
import json
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trader.core.config import load_config
from trader.core.journal import Journal
from trader.strategy import factory_handoff as F


def inventory(db_path):
    source=sqlite3.connect(Path(db_path).resolve().as_uri()+'?mode=ro',uri=True)
    source.row_factory=sqlite3.Row
    names={r[0] for r in source.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    tables=(*F._TABLES,'strategy_capacity_receipts','strategy_legacy_authorities')
    counts={t:source.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0] if t in names else 0 for t in tables}
    control=source.execute("SELECT value FROM state_kv WHERE key='control_state'").fetchone()
    result=dict(schema='universal-strategy-authority-inventory.v1',observed_at_ms=int(time.time()*1000),
        source_access='SQLITE_READ_ONLY',counts=counts,control=control[0] if control else 'UNASSERTED',
        current_first_live_ready='NO',real_order_submissions=0,activation_probes=[])
    with tempfile.TemporaryDirectory(prefix='luffy-authority-probe-') as directory:
        target=Path(directory)/'snapshot.db'
        clone=sqlite3.connect(target); source.backup(clone); clone.close()
        journal=Journal(target)
        versions=journal.query('SELECT version_id FROM strategy_versions') if 'strategy_versions' in names else []
        ids=[r['version_id'] for r in versions] or ['NO_EXACT_VERSION_IN_CURRENT_INVENTORY']
        for vid in ids:
            try:
                F.govern_version(journal,load_config(),vid,'ACTIVE',actor='operator',
                    reason_code='READ_ONLY_INVENTORY_ISOLATED_REFUSAL_PROBE',at_ms=int(time.time()*1000),allocation=.1)
                raise AssertionError('unexpected activation without Risk prerequisite')
            except F.HandoffRefused as exc:
                result['activation_probes'].append(dict(version_id=vid,result='REFUSED',reason=exc.code,
                    writes='TEMPORARY_SNAPSHOT_SCHEMA_ONLY'))
        result['governor_events_after_probes']=journal.query('SELECT COUNT(*) n FROM strategy_governor_events')[0]['n']
    source.close()
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--db',default='data/luffy.db')
    args=parser.parse_args()
    print(json.dumps(inventory(args.db),indent=2,sort_keys=True))
