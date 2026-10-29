"""Combined-position decisions on the existing position clock.

Only already-owned demo groups are observed. Combined exits reuse durable
reduce-only recovery. A protection activation is an explicit entry request
gap until the separate exact-version/Risk coordinator can admit that leg.
"""
from contextlib import closing
import json
import time

from . import booking, coordinated_marked as M, coordinated_unwind as U,coordinated_close as C
from . import trade_accounting as T
from ..research.closure import canonical, digest

MAX_GROUPS = 2
MAX_EVIDENCE_BYTES = 8 * 1024 * 1024


def pending_exit(journal, group_id):
    if not journal.query("SELECT 1 FROM sqlite_master WHERE type='table' AND name='coordinated_management_exits'"):
        return None
    rows = journal.query('SELECT payload,sha256 FROM coordinated_management_exits WHERE group_id=?', (group_id,))
    if not rows:
        return None
    saved = json.loads(rows[0]['payload'])
    reason = saved['assessment']['decision']['exit_reason']
    if (digest(saved) != rows[0]['sha256'] or saved['source']['group_id'] != group_id
            or saved['assessment']['group_id'] != group_id or reason not in ('combined_stop', 'combined_target', 'primary_closed')):
        raise ValueError('coordinated_management_exit_evidence_corrupt')
    return reason


def source(executor, group_id):
    """Fetch only the group's owned orders/cashflows and current positions."""
    from .protection_snapshot import ro_connect
    from ..observability.portfolio_observation import trading_source
    if trading_source(executor.ex)[0] != 'demo':
        raise ValueError('coordinated_management_demo_only')
    members = U._members(executor, group_id)
    if not 1 <= len(members) <= 2:
        raise ValueError('coordinated_management_members_unsupported')
    parts = []
    for member in members:
        with closing(ro_connect(executor.journal.db_path)) as db:
            exported = booking.export(db, member['trade_id'])
            row = db.execute('SELECT entry_identity_json FROM trades WHERE id=?', (member['trade_id'],)).fetchone()
            identity = json.loads(row[0])
            if exported['trade'].get('exec_mode') != 'live' or exported['trade'].get('market_type') != 'futures':
                raise ValueError('coordinated_management_trade_scope')
            request = db.execute('SELECT * FROM execution_requests WHERE logical_id=?',
                                 (identity['execution_binding']['logical_id'],)).fetchone()
            if request is None:
                raise ValueError('coordinated_management_entry_missing')
            overlaps = [r[0] for r in db.execute(
                'SELECT id FROM trades WHERE symbol=? AND id<>? AND (closed_at IS NULL OR closed_at>=?) '
                'AND (? IS NULL OR opened_at<=?)',
                (member['symbol'], member['trade_id'], exported['trade']['opened_at'],
                 exported['trade']['closed_at'],exported['trade']['closed_at']))]
            entry = dict(logical_id=request['logical_id'], state=request['state'], order_id=request['order_id'],
                         intent_hash=request['intent_hash'], intent=json.loads(request['intent_json']),
                         result=json.loads(request['result_json'] or '{}'))
        flat=(C.retained_terminal_flat(executor.journal,member['trade_id'],group_id,
                T.legs(exported)[-1]['order_id']) if exported['trade']['status']=='closed' else None)
        parts.append(dict(capture=T.capture(exported, executor.ex, overlaps,marked=flat is None,terminal_flat=flat),
                          identity=identity, entry_request=entry))
    return dict(schema_version=M.VERSION, group_id=group_id, members=members, components=parts)


def _retain_exit(journal, group_id, evidence, result):
    payload = canonical(dict(source=evidence, assessment=result))
    if len(payload.encode()) > MAX_EVIDENCE_BYTES:
        raise ValueError('coordinated_management_evidence_bound')
    with journal._tx() as db:
        db.execute('CREATE TABLE IF NOT EXISTS coordinated_management_exits('
                   'group_id TEXT PRIMARY KEY,payload TEXT NOT NULL,sha256 TEXT NOT NULL)')
        db.execute('INSERT OR IGNORE INTO coordinated_management_exits VALUES(?,?,?)',
                   (group_id, payload, digest(json.loads(payload))))
        row = db.execute('SELECT payload,sha256 FROM coordinated_management_exits WHERE group_id=?', (group_id,)).fetchone()
    saved = json.loads(row[0])
    if digest(saved) != row[1]:
        raise ValueError('coordinated_management_exit_evidence_corrupt')
    return saved['assessment']['decision']['exit_reason']


def run_once(executor):
    """Caller holds the position-worker execution lock and exit authority.

    Empty books perform no venue reads. Current summaries overwrite one
    bounded key; full evidence is retained once when a group exit is issued.
    """
    groups = sorted({g for t in executor.journal.open_trades() if (g := U.owned_group(t))})
    if not groups:
        return []
    results = []
    if len(groups) > MAX_GROUPS:
        results.append(dict(group_id=None, status='UNAVAILABLE', reason='coordinated_management_group_bound'))
    for group_id in groups[:MAX_GROUPS]:
        try:
            if U.active(executor.journal, group_id):
                results.append(dict(group_id=group_id, status='UNWINDING'))
                continue
            reason = pending_exit(executor.journal, group_id)
            if reason:
                results.append(dict(group_id=group_id, status='EXIT_REQUESTED', reduction=U.close_group_once(
                    executor, group_id, digest(['combined-management-exit', group_id]), reason=reason)))
                continue
            evidence = source(executor, group_id)
            result = M.assess(evidence, now_ms=int(time.time()*1000))
            summary = dict(result, status='OBSERVED')
            if result['decision']['exit_reason']:
                reason = _retain_exit(executor.journal, group_id, evidence, result)
                summary.update(status='EXIT_REQUESTED', reduction=U.close_group_once(
                    executor, group_id, digest(['combined-management-exit', group_id]), reason=reason))
            elif result['decision']['activate'] and not result['hedge_present']:
                summary.update(status='HEDGE_ENTRY_REQUIRED', reason='exact_risk_admitted_hedge_entry_not_integrated')
            results.append(summary)
        except Exception as exc:
            # Messages can contain private HTTP details. Persist only a code/type.
            reason = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
            if not reason or len(reason) > 100 or any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789_' for c in reason):
                reason = type(exc).__name__
            results.append(dict(group_id=group_id, status='UNAVAILABLE', reason=reason))
    executor.journal.kv_set('coordinated_management_latest', canonical(dict(
        observed_ms=int(time.time()*1000), results=results)))
    return results
