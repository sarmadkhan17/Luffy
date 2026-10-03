"""Bounded typed owner queries over existing stores. No SQL/path/tool input.

RECORDED is deliberately distinct from VERIFIED and CURRENT. Exact captured
dependencies are followed; time proximity and names never create lineage.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from contextlib import closing

from . import approvals as A

KINDS = ('decision', 'trade', 'research', 'portfolio', 'world', 'strategies',
         'cost', 'approvals', 'learning', 'capability')
LIMIT = 50


def stamp():
    return datetime.now(timezone.utc).isoformat()


def record(kind, identity, value, *, source, at=None, available=None, verified=False):
    if isinstance(value, dict) and 'generated_at' in value:
        value = {k: v for k, v in value.items() if k != 'generated_at'}
    content_hash = A.digest(value)
    if len(A.canonical(value).encode()) > 1048576:
        value = dict(status='UNAVAILABLE', reason='record_detail_exceeds_1MiB_owner_bound',
                     retained_sha256=content_hash)
    return dict(record_id=f'{kind}:{identity}', kind=kind, identity=str(identity),
                source=source, sha256=content_hash, value=value,
                timestamp=at, available_at=available, time_basis='event' if at is not None else 'NOT_RECORDED',
                verification='VERIFIED' if verified else 'RECORDED', freshness='NOT_ASSESSED',
                strategy_version_id=value.get('version_id', value.get('strategy_version_id')) if isinstance(value, dict) else None,
                decision_id=value.get('decision_id') if isinstance(value, dict) else None,
                cycle_id=value.get('cycle_id') if isinstance(value, dict) else None)


def rows(j, table, key, identity=None, *, limit=LIMIT, offset=0):
    if not A.exists(j, table):
        return []
    # Identifiers are constants from the call sites below, never caller SQL.
    return j.query(f'SELECT * FROM {table} ' + (f'WHERE {key}=? ' if identity else '') +
                   'ORDER BY rowid DESC LIMIT ? OFFSET ?', (identity, limit, offset) if identity else (limit, offset))


def captured(journal, decision_id=None):
    """Read the immutable Stage7 decision registrations and exact snapshots."""
    records, missing = [], []
    path = Path(journal.db_path)
    if not path.exists():
        return [], ['capture_store_absent']
    from trader.learning import capture as C
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='learning_registrations'").fetchone():
            return [], ['historical_decision_dependencies_NOT_RECORDED']
        # Exact decision linkage is stored in canonical JSON, never guessed.
        sql = 'SELECT event_key FROM learning_registrations '
        params = ()
        if decision_id:
            sql += "WHERE json_extract(payload,'$.lineage.decision_id')=? "
            params = (decision_id,)
        for (event_key,) in db.execute(sql + 'ORDER BY rowid DESC LIMIT 50', params).fetchall():
            rid, reg = C.registration(db, event_key)
            records.append(record('registration', rid, reg, source='learning_registrations',
                                  at=reg['decision_ms'], available=reg['decision_ms'], verified=True))
            for dep in reg['dependencies']:
                if dep['status'] != 'AVAILABLE':
                    missing.append(f"{dep['role']}:{dep.get('reason', 'UNAVAILABLE')}")
                    continue
                value = C.resolve(db, dep)
                records.append(record(dep['role'], dep['source_id'], value, source=dep,
                                      at=reg['decision_ms'], available=dep['available_ms'], verified=True))
            for action in db.execute('SELECT payload FROM learning_actions WHERE event_key=? LIMIT 50', (event_key,)).fetchall():
                action = json.loads(action[0]); dep = action['dependency']
                if action['registration_id'] != rid:
                    raise ValueError('action_registration_binding_differs')
                records.append(record(dep['role'], dep['source_id'], C.resolve(db, dep), source=dep,
                                      available=dep['available_ms'], verified=True))
            for (payload,) in db.execute("SELECT payload FROM learning_outcome_captures WHERE json_extract(payload,'$.registration_id')=? LIMIT 50", (rid,)).fetchall():
                outcome = json.loads(payload)
                C.existing_outcome(db, outcome['outcome_key'])
                records.append(record('outcome', C.L.digest(outcome), outcome,
                                      source='learning_outcome_captures', verified=True))
    return records, missing


def query(journal, kind, identity=None, *, root=None, cfg=None, offset=0):
    if kind not in KINDS or (identity is not None and (not isinstance(identity, str) or not 1 <= len(identity) <= 128)):
        raise ValueError('unsupported_owner_query')
    if type(offset) is not int or offset < 0 or (offset and (identity or kind not in ('research', 'capability', 'cost', 'approvals'))):
        raise ValueError('unsupported_catalog_offset')
    question_page = None
    root = Path(root) if root is not None else Path(journal.db_path).parent.parent
    cfg = cfg if cfg is not None else {}
    records, missing = [], []
    from trader.dashboard import owner_reads as R
    if kind in ('decision', 'trade'):
        if identity:
            value = R.decision_detail(journal, identity) if kind == 'decision' else R.trade_lineage(journal, identity)
            if value:
                primary = value.get('decision') or value.get('trade') or {}
                at = primary.get('ts', primary.get('opened_at'))
                records.append(record(kind, identity, value, source=value['source'], at=at))
                did = identity if kind == 'decision' else (value.get('trade') or {}).get('decision_id')
                extra, gaps = captured(journal, str(did)) if did else ([], ['decision_link_NOT_RECORDED'])
                records.extend(extra); missing.extend(gaps)
                missing.extend(x['field'] + ':' + x['reason'] for x in value['unavailable'])
        else:
            for row in rows(journal, 'decisions' if kind == 'decision' else 'trades', 'id'):
                records.append(record(kind, row['id'], row, source='journal ' + kind,
                                      at=row.get('ts', row.get('opened_at'))))
    elif kind in ('world', 'learning'):
        records, missing = captured(journal, identity)
        if kind == 'world':
            records = [r for r in records if r['kind'] in ('world', 'context', 'research_evidence')]
        for row in rows(journal, 'learning_target_revisions', 'context_id', identity):
            if kind == 'world' and row['target'] != 'WORLD_MODEL_PROBABILITIES':
                continue
            value = json.loads(row['payload'])
            if A.digest(value) != row['sha256'] or value['revision'] != row['revision'] or value['target'] != row['target']:
                raise ValueError('learned_revision_integrity')
            records.append(record('learned_revision', row['sha256'], dict(value, application_id=row['application_id']),
                                  source='learning_target_revisions; exact retained context/revision, not retrospective WorldModel mutation'))
        if kind == 'learning':
            from trader.learning.application import _receipt
            for row in rows(journal, 'learning_application_receipts', 'application_id', identity):
                value = _receipt(row)
                records.append(record('learning_application', row['application_id'], value,
                                      source='learning_application_receipts', verified=True))
        if not records:
            missing.append('exact_world_or_learning_capture_NOT_RECORDED')
    elif kind in ('research', 'capability', 'cost'):
        for row in rows(journal, 'research_bank_objects', 'bank_object_id', identity):
            if row['schema'] == 'external-evidence-bank-object.v1':
                from trader.research.external_research import verify_bank
                value = verify_bank(journal, row)
                # verifier returns the verified bank object
                if value is None:
                    value = json.loads(row['canonical_json'])
            else:
                from trader.cognition.research_bank_view import build
                value = build(journal, bank_object_id=row['bank_object_id'], max_prior_objects=5)
            records.append(record('research', row['bank_object_id'], value, source='Research Bank verified chain',
                                  at=row['recorded_at_ms'], available=row['recorded_at_ms'], verified=True))
            if row['schema'] != 'external-evidence-bank-object.v1':
                # The existing verified Bank view deliberately exposes evidence
                # identity rather than copying values. Follow that exact link
                # for the owner, without a semantic/time-proximity join.
                for evidence in rows(journal, 'research_evidence', 'evidence_id', row['evidence_id']):
                    records.append(record('research_evidence', evidence['evidence_id'], json.loads(evidence['canonical_json']),
                                          source='research_evidence linked by verified Bank evidence_id',
                                          available=evidence['recorded_at_ms'], verified=True))
        # Questions with no result remain visible, including failed/empty runs.
        questions = rows(journal, 'research_questions', 'question_id', identity, limit=LIMIT+1, offset=offset)
        question_page = dict(offset=offset, limit=LIMIT, has_more=len(questions)>LIMIT,
                             next_offset=offset+LIMIT if len(questions)>LIMIT else None,
                             order='journal insertion order, newest first', scope='research_questions')
        for row in questions[:LIMIT]:
            value = json.loads(row['canonical_json'])
            records.append(record('question', row['question_id'], value, source='research_questions',
                                  at=row['recorded_at_ms'], available=row['recorded_at_ms']))
        if kind == 'research':
            for row in rows(journal, 'research_combos', 'hash', identity):
                value = R.research_item(journal, row['hash'])
                records.append(record('experiment', row['hash'], value,
                                      source='quantitative research ledger; registered referee status only', at=row['created_at']))
        if kind == 'cost':
            for row in rows(journal, 'research_runs', 'run_id', identity):
                value = json.loads(row['telemetry_json'])
                if hashlib.sha256(row['telemetry_json'].encode()).hexdigest() != row['telemetry_sha256']:
                    raise ValueError('cost_telemetry_hash_differs')
                records.append(record('cost', row['run_id'], value, source='research_runs telemetry',
                                      available=row['recorded_at_ms'], verified=True))
            usage = root / 'data/brain_usage.json'
            if identity in (None, 'brain_usage') and usage.is_file():
                raw = usage.read_bytes()
                if len(raw) > 262144:
                    raise ValueError('llm_usage_file_bound')
                value = json.loads(raw)
                if not isinstance(value, dict):
                    raise ValueError('llm_usage_shape')
                records.append(record('llm_tokens', 'brain_usage', value, source='data/brain_usage.json; recorded tokens by day/purpose, not monetary cost'))
            missing.append('total_LLM_data_operating_cost_NOT_ESTABLISHED; no inferred zero')
        missing.extend(['External descriptive evidence is not predictive edge',
                        'Supporting/contradicting classification, experiment/referee and limitations are only those recorded'])
    elif kind == 'strategies':
        from trader.strategy.factory_handoff import load_version
        for row in rows(journal, 'strategy_versions', 'version_id', identity):
            value = load_version(journal, row['version_id'])
            records.append(record('strategy_version', row['version_id'], value, source='Strategy Factory',
                                  available=row['recorded_at_ms'], verified=True))
        for row in rows(journal, 'strategies', 'id', identity):
            value = R.strategy_detail(journal, row['id'])
            records.append(record('strategy', row['id'], value, source='strategy registry and lifecycle'))
        missing.append('weakening_is_only_recorded_decay_or_governor_evidence; no inferred ranking')
    elif kind == 'portfolio':
        path = root / 'data/runtime-portfolio.db'
        if path.exists():
            with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as db:
                sql = 'SELECT cut_id,payload FROM evaluations '
                for cut, raw in db.execute(sql + ('WHERE cut_id=? ' if identity else '') + 'ORDER BY rowid DESC LIMIT 50',
                                           (identity,) if identity else ()).fetchall():
                    value = json.loads(raw)
                    if value['portfolio_cut_id'] != cut:
                        raise ValueError('portfolio_cut_identity_differs')
                    records.append(record('portfolio', cut, value, source='runtime-portfolio evaluations',
                                          at=value['event_receipt']['inputs']['current']['as_of_ms']))
        if identity is None:
            for row in journal.open_trades()[:LIMIT]:
                records.append(record('journal_position', row['id'], row, source='journal; not current venue truth', at=row['opened_at']))
        missing.append('Current factor/capacity/economics are UNKNOWN unless exact retained cut records them; historical cut is not current book')
    elif kind == 'approvals':
        feed = A.items(journal, cfg, identity=identity, offset=offset)
        for item in feed['items']:
            if identity is None or item['item_id'] == identity:
                records.append(record('approval', item['item_id'], item, source=feed['source'],
                                      at=item['created_at_ms'], available=item['available_at_ms']))
        missing.extend(x['reason'] for x in feed['unavailable'])
    if not records:
        missing.append('exact_records_UNAVAILABLE')
    return dict(schema='owner-query.v1', query=dict(kind=kind, identity=identity, offset=offset), questions_page=question_page, generated_at=stamp(),
                status='AVAILABLE' if records else 'UNAVAILABLE', records=records,
                unavailable=[dict(field=kind, reason=m) for m in dict.fromkeys(missing)],
                source='typed internal record readers; read-only', truncated=len(records)>=LIMIT,
                semantics='Historical recorded evidence; no venue verification, invented links, or implicit authority',
                config_sha256=A.digest(cfg), config_basis='query configuration, not substituted for historical decision configuration')
