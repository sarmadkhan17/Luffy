"""Bounded metadata from the configured research engine; never opens for write."""
from contextlib import closing
from pathlib import Path
import sqlite3
import time


def closure_summary(cfg):
    section = ((cfg or {}).get('research') or {}).get('closure') or {}
    if not section.get('enabled'):
        return None
    out = dict(engine='compositional', available=False, counts=None, studies=[],
               source='configured compositional research ledger', authority='RESEARCH_ONLY')
    path = section.get('store_path')
    if not isinstance(path, str) or not path.strip():
        return dict(out, reason='research_store_not_configured')
    try:
        with closing(sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro', uri=True, timeout=1)) as c:
            c.row_factory = sqlite3.Row
            c.execute('PRAGMA query_only=ON')
            deadline = time.monotonic()+2
            c.set_progress_handler(lambda: int(time.monotonic()>deadline), 10000)
            c.execute('BEGIN')
            counts = {t: c.execute('SELECT count(*) FROM '+t).fetchone()[0]
                      for t in ('studies', 'candidates', 'runs', 'experiment_plans', 'looks', 'qualifications')}
            studies = [dict(r) for r in c.execute('''SELECT s.study_id,s.status,s.stop_reason,
                s.next_work,s.last_progress_ms,
                (SELECT count(*) FROM units u WHERE u.study_id=s.study_id AND u.status='FAILED') failed_units
                FROM studies s ORDER BY s.rowid DESC LIMIT 5''')]
            latest = c.execute('SELECT at_ms,status,study_id,stop_reason,units_run FROM schedule_events ORDER BY seq DESC LIMIT 1').fetchone()
        return dict(out, available=True, counts=counts, studies=studies,
                    last_schedule=dict(latest) if latest else None)
    except (OSError, ValueError, sqlite3.Error) as exc:
        return dict(out, reason='research_store_unreadable:'+type(exc).__name__)
