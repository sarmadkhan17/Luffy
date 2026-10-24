"""Seal retained World receipts without inventing historical observations.

Only detached snapshots may enter research. The final evaluator uses the same
WorldContext reader as development and strategy inference. This step checks
presence/integrity, not economic results, before reserving a protected look.
"""
from contextlib import closing
from pathlib import Path
import json
import sqlite3

from ...world.context import load_context
from ...core.types import TF_MS
from ..closure import ClosureRefused, digest
from ..closure.manifest import refuse_live, snapshot_identity, require_unchanged


def seal_world(source, destination, env):
    if source is None:
        raise ClosureRefused('world_snapshot_not_supplied')
    source = refuse_live(source)
    destination = Path(destination)
    if destination.resolve() == source:
        raise ClosureRefused('world_snapshot_destination_is_source')
    identity = snapshot_identity(source)
    end = env['split']['end_ms'] + TF_MS[env['tf']]
    # Start at the protected context window (includes its existing warmup).
    start = env['split']['start_ms']
    with closing(sqlite3.connect(source.as_uri() + '?mode=ro&immutable=1', uri=True)) as db:
        try:
            rows = db.execute('SELECT as_of_ms,payload FROM scans WHERE as_of_ms>=? AND as_of_ms<=? '
                              'AND payload IS NOT NULL ORDER BY as_of_ms', (start, end)).fetchall()
        except sqlite3.Error as exc:
            raise ClosureRefused('world_snapshot_schema', type(exc).__name__) from exc
    try:
        retro = any(json.loads(raw).get('evidence_scope') == 'retrospective_observation' for _, raw in rows)
    except (ValueError, TypeError, AttributeError) as exc:
        raise ClosureRefused('world_snapshot_invalid') from exc
    if retro:                      # reconstructed World is never a protected look input
        raise ClosureRefused('world_retrospective_not_protected_authority')
    # Keep only the World record: unrelated Attention evidence is not an input.
    receipts = []
    try:
        for cut, raw in rows:
            item = json.loads(raw)
            receipt = item.get('world_model')
            if receipt is not None:
                if item.get('as_of_ms') != cut:
                    raise ValueError('source scan cut mismatch')
                receipts.append((cut, json.dumps(dict(as_of_ms=item['as_of_ms'], world_model=receipt), sort_keys=True)))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ClosureRefused('world_snapshot_invalid') from exc
    require_unchanged(source, identity)
    if not receipts:
        raise ClosureRefused('world_protected_history_missing')
    tmp = destination.with_suffix('.pending')
    try:
        if tmp.exists():
            tmp.unlink()
        with closing(sqlite3.connect(tmp)) as db:
            db.execute('CREATE TABLE scans(as_of_ms INTEGER NOT NULL, payload TEXT NOT NULL)')
            db.executemany('INSERT INTO scans VALUES (?,?)', receipts)
            db.commit()
        context = load_context(tmp, as_of_ms=end)
        if context.source_status != 'RETAINED' or not context.history.cuts:
            raise ClosureRefused('world_protected_history_invalid')
        receipt = dict(history_id=context.history.history_id, cuts=len(context.history.cuts),
                       first_cut=min(context.history.cuts), last_cut=max(context.history.cuts))
        if destination.exists():
            old = load_context(destination, as_of_ms=end)
            if old.context_id != context.context_id:
                raise ClosureRefused('sealed_world_changed')
        else:
            tmp.replace(destination)
        return dict(receipt, fingerprint=digest(receipt))
    finally:
        if tmp.exists():
            tmp.unlink()
