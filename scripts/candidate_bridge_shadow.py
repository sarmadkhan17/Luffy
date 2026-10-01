#!/usr/bin/env python3
"""Bounded read-only shadow using the existing context reader and allocator."""
import argparse
import json
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.opportunity_context_shadow import run
from trader.portfolio.allocator import canonical


def main():
    import yaml
    p = argparse.ArgumentParser()
    for name in ('journal', 'attention', 'investigation', 'config', 'output'):
        p.add_argument('--' + name, type=Path, required=True)
    a = p.parse_args()
    try:
        # Absent schema is not an observed empty authoritative inventory.
        with sqlite3.connect(a.journal.resolve().as_uri() + '?mode=ro', uri=True) as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='strategy_versions'").fetchone():
                raise ValueError('FACTORY_VERSION_SCHEMA_UNAVAILABLE')
        cfg = yaml.safe_load(a.config.read_text())
        safe = {k: cfg.get(k, {}) for k in ('attention', 'strategies', 'risk')}
        result = run(a.journal, a.attention, a.investigation, safe, a.output,
                     candidate_bridge=True)
    except (ValueError, KeyError, TypeError, sqlite3.Error) as exc:
        result = dict(status='BLOCKED', blocker=str(exc), read_only=True)
    print(canonical(result))
    return 0 if result['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
