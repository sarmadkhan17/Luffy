"""Owner data-access requests raised by a research study.

A request exists only for ONE attempted study/candidate that cannot be
developed because a REQUIRED external reference is missing and only the owner
can supply it (a TradingView CRYPTOCAP export needs a signed-in session). It
is an alert, not an approval: it grants nothing, carries no credentials, and is
resolved only when the candidate is actually re-evaluated on eligible data.

Requests live in the closure Store (`data_requests`) and are projected,
read-only, into the existing Needs You surface by `project()`. Nothing here
logs in, scrapes, or contacts a provider; source acquisition is the owner's.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from . import canonical, digest

SCHEMA = 'research-data-request.v1'
MAX_OPEN_PER_STUDY = 16
MAX_TEXT = 320
MAX_OPEN_PROJECTED = 1000      # active alerts are always listed first; beyond this the projection says so
MAX_HISTORY_PROJECTED = 100   # resolved history is paged out explicitly, never silently
#: request fields excluded from the identity hash (presentation only)
PRESENTATION = ('request_id', 'missing_reason', 'owner_action', 'effect', 'span_utc')

#: provider-level acquisition guidance for references only the owner can supply
_TV = 'TradingView'


def _access(ref_key):
    """(provider, series, tf) when the owner can supply this reference, else None."""
    from ...data.references import REFS
    ref = REFS.get(ref_key)
    if ref is None or ref.source != 'tradingview-export':
        return None
    return _TV, ref.symbol, ref.tf


def _clip(text, n=MAX_TEXT):
    return str(text)[:n]


def utc_date(ms) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(int(ms) / 1000, timezone.utc).strftime('%Y-%m-%d')


def _finish(body, gap_reason, provider, series, tf, span):
    request_id = digest(body)
    return dict(body, request_id=request_id, span_utc=span, missing_reason=_clip(gap_reason),
                owner_action=_clip(f'Open {provider} and sign in, then provide the official chart export (CSV: time, open, high, '
                                   f'low, close) for {series} at {tf} covering {span}, or confirm it cannot be supplied. '
                                   'Import it into the references store named here; the next study step re-checks '
                                   'automatically.', 480),
                effect='Only this candidate waits. Other candidates continue. No economics were judged; this is not a failure.')


def build(study_id, study, env, cand, gap) -> dict | None:
    """The request for one `context_gaps` entry (missing, stale or insufficient coverage), or None when the
    dependency is not something the owner supplies (or the study cannot use a later capture)."""
    req = str(gap.get('requirement', ''))
    if not req.startswith('ref:') or env['development_mode'] != 'RETROSPECTIVE':
        return None          # a REPLAY study needs original-time receipts; a new capture cannot provide them
    key = req.split(':', 1)[1]
    found = _access(key)
    if found is None:
        return None
    provider, series, tf = found
    lo, hi = int(env['fit_window'][0]), int(env['dev_end_ms'])
    body = dict(schema=SCHEMA, study_id=study_id, candidate_id=cand['candidate_id'],
                question_id=digest(cand.get('question', ''))[:16], question=_clip(cand.get('question', ''), 160),
                requirement=req, reference_key=key, provider=provider, series=series, tf=tf,
                span_start_ms=lo, span_end_ms=hi, development_mode=env['development_mode'],
                references_store=_clip(study.get('references_path') or study['snapshot_path'], 240))
    return _finish(body, gap.get('reason', 'not available'), provider, series, tf, f'{utc_date(lo)} to {utc_date(hi)} (UTC)')


def _aggregate(study_id, study, env, cand, rest) -> dict:
    """One request naming EVERY requirement that did not fit under the per-study cap, so none is dropped."""
    lo, hi = int(env['fit_window'][0]), int(env['dev_end_ms'])
    reqs = sorted(r['requirement'] for r in rest)
    series = sorted({r['series'] for r in rest})
    body = dict(schema=SCHEMA, study_id=study_id, candidate_id=cand['candidate_id'],
                question_id=digest(cand.get('question', ''))[:16], question=_clip(cand.get('question', ''), 160),
                requirement='multiple', requirements=reqs, reference_key='*', provider=_TV, series=_clip(', '.join(series)),
                tf=', '.join(sorted({r['tf'] for r in rest})), span_start_ms=lo, span_end_ms=hi,
                development_mode=env['development_mode'],
                references_store=_clip(study.get('references_path') or study['snapshot_path'], 240))
    reason = 'per-study request cap reached; every listed series is required: ' + '; '.join(
        f"{r['requirement']} ({r['missing_reason']})" for r in sorted(rest, key=lambda r: r['requirement']))
    return _finish(body, reason, _TV, body['series'], body['tf'], f'{utc_date(lo)} to {utc_date(hi)} (UTC)')


def required_of(request) -> list:
    return list(request.get('requirements') or [request['requirement']])


def record(store, study_id, study, env, cand, hit, now_ms) -> tuple:
    """Reconcile this candidate's requests with a development result. Returns (request ids awaited, every
    requirement awaited). Requests no longer needed are resolved ONLY here, after the candidate was actually
    evaluated again; never by a click. If the per-study cap cannot hold every gap, the remainder is persisted
    as ONE aggregate request naming each requirement: no required gap is dropped."""
    gaps = (hit.get('context_gaps') or []) if hit.get('outcome') == 'BLOCKED_DATA' else []
    wanted = sorted((r for r in (build(study_id, study, env, cand, g) for g in gaps) if r is not None),
                    key=lambda r: r['requirement'])
    mine = {r['request_id'] for r in store.data_requests(study_id=study_id, candidate_id=cand['candidate_id'], status='OPEN')}
    capacity = MAX_OPEN_PER_STUDY - (store.open_count(study_id) - len(mine))
    if len(wanted) <= capacity:
        persist = wanted
    else:
        keep_n = max(capacity - 1, 0)
        persist = wanted[:keep_n] + [_aggregate(study_id, study, env, cand, wanted[keep_n:])]
    keep = [r['request_id'] for r in persist]
    for row_id in mine - set(keep):
        store.resolve_data_request(row_id, now_ms, dict(
            result_id=hit.get('result_id'), outcome=hit.get('outcome'), data_id=hit.get('data_id'),
            reason=_clip(hit.get('reason', ''), 200)))
    for r in persist:
        store.open_data_request(r, now_ms)
    return keep, [r['requirement'] for r in wanted]


def eligible_now(store, study, env, cand, requirements) -> bool:
    """True when every awaited reference is now served by the retrospective store, with coverage (the same
    loader and preflight the development uses). Read-only; no state change."""
    from . import ClosureRefused, development
    need = set(requirements or ())
    if not need:
        return False
    try:
        ext = development._external(cand, study['snapshot_path'], study.get('derivs_path'), env['discovery_symbols'], env,
                                    study.get('world_path'), study.get('references_path'))
    except (ClosureRefused, ValueError, OSError, KeyError):
        return False
    return not any(g['requirement'] in need for g in ext['gaps'])


# ── read-only projection for Needs You ─────────────────────────────────────
def project(store_path, identity=None) -> dict:
    """{requests, reason, open_total, history_total, history_shown}. Opens the file read-only, never creates
    it or any table. Active (OPEN) requests are listed first and uncapped up to MAX_OPEN_PROJECTED; resolved
    history is capped at MAX_HISTORY_PROJECTED and the hidden count is reported. An exact identity is found
    whatever the history size. An unreadable store is reported, not treated as empty."""
    out = dict(requests=[], reason=None, open_total=0, history_total=0, history_shown=0)
    p = Path(store_path)
    if not p.is_file():
        out['reason'] = 'closure_store_not_found'
        return out
    cols = 'payload,status,created_ms,updated_ms,resolution'
    try:
        con = sqlite3.connect(p.resolve().as_uri() + '?mode=ro', uri=True, timeout=5)
        try:
            con.execute('PRAGMA query_only=ON')
            if not con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='data_requests'").fetchone():
                return out
            if identity:
                rows = con.execute(f'SELECT {cols} FROM data_requests WHERE request_id=?', (identity,)).fetchall()
            else:
                out['open_total'], out['history_total'] = (con.execute(
                    "SELECT COALESCE(SUM(status='OPEN'),0), COALESCE(SUM(status!='OPEN'),0) FROM data_requests").fetchone())
                rows = con.execute(f"SELECT {cols} FROM data_requests WHERE status='OPEN' ORDER BY rowid DESC LIMIT ?",
                                   (MAX_OPEN_PROJECTED,)).fetchall()
                if out['open_total'] > MAX_OPEN_PROJECTED:
                    out['reason'] = 'closure_data_requests_open_truncated'
                rows += con.execute(f"SELECT {cols} FROM data_requests WHERE status!='OPEN' ORDER BY rowid DESC LIMIT ?",
                                    (MAX_HISTORY_PROJECTED,)).fetchall()
        finally:
            con.close()
        for payload, status, created, updated, resolution in rows:
            body = json.loads(payload)
            if body.get('request_id') != digest({k: v for k, v in body.items() if k not in PRESENTATION}):
                return dict(out, requests=[], reason='closure_data_request_integrity')
            out['requests'].append(dict(body, status=status, created_ms=created, updated_ms=updated,
                                        resolution=json.loads(resolution) if resolution else None))
        out['history_shown'] = sum(r['status'] != 'OPEN' for r in out['requests'])
        return out
    except (sqlite3.Error, ValueError, KeyError, TypeError, OSError):
        return dict(out, requests=[], reason='closure_store_unreadable')


__all__ = ['build', 'record', 'eligible_now', 'project', 'utc_date', 'required_of', 'canonical']
