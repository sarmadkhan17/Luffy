"""Exact owner decisions, through OwnerService only; never executes a proposal.

Engineering/research producers register proposals locally, not through chat or
browser input. This is the existing owner contract's governance ledger, not a
second control executor. Policy/capability adoption remains an explicit workflow.
"""
import hashlib
import json
import time
from pathlib import Path

CLASSES = ('first_live', 'capability', 'paid_spend', 'risk_boundary',
           'serious_recovery', 'production_code')
SOURCES = {
    'strategy_version': ('strategy_versions', 'version_id', 'canonical_json'),
    'validation': ('strategy_validation_receipts', 'receipt_id', 'canonical_json'),
    'probation': ('strategy_probation_receipts', 'receipt_id', 'canonical_json'),
    'research_bank': ('research_bank_objects', 'bank_object_id', 'canonical_json'),
    'context': ('state_kv', 'key', 'value'),
}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                      allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def ensure(journal):
    with journal._tx() as c:
        c.execute('CREATE TABLE IF NOT EXISTS owner_approval_items '
                  '(item_id TEXT PRIMARY KEY, payload TEXT NOT NULL)')
        c.execute('CREATE TABLE IF NOT EXISTS owner_approval_receipts '
                  '(item_id TEXT PRIMARY KEY, payload TEXT NOT NULL)')
        for table in ('owner_approval_items', 'owner_approval_receipts'):
            for action in ('UPDATE', 'DELETE'):
                c.execute(f"CREATE TRIGGER IF NOT EXISTS {table}_{action.lower()} BEFORE {action} "
                          f"ON {table} BEGIN SELECT RAISE(ABORT,'approval immutable'); END")


def exists(journal, table):
    return bool(journal.query("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)))


def source(journal, kind, identity):
    if kind == 'artifact':
        root = Path(journal.db_path).resolve().parent.parent
        path = (root / identity).resolve()
        if (not path.is_relative_to(root) or not identity.startswith(('trader/', 'frontend/src/', 'scripts/', 'docs/superpowers/'))
                or path.suffix not in ('.py', '.ts', '.tsx', '.sh', '.md', '.json', '.yaml')):
            raise ValueError('unregistered_artifact')
        if not path.is_file():
            return None
        return dict(path=identity, content_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    table, key, col = SOURCES[kind]
    if kind == 'context' and not identity.startswith('owner_proposal_context:'):
        raise ValueError('unregistered_context')
    if not exists(journal, table):
        return None
    rows = journal.query(f'SELECT {col} FROM {table} WHERE {key}=?', (identity,))
    return json.loads(rows[0][col]) if rows else None


def binding(journal, kind, identity):
    value = source(journal, kind, identity)
    if value is None:
        raise ValueError('binding_source_missing')
    return dict(kind=kind, identity=identity, sha256=digest(value))


def receipt_from(text, item_id):
    receipt = json.loads(text)
    if receipt['item_id'] != item_id or receipt['receipt_id'] != digest({k: v for k, v in receipt.items() if k != 'receipt_id'}):
        raise ValueError('approval_receipt_integrity')
    return receipt


def propose(journal, cfg, *, action_type, affected_object, reason, required_action,
            bindings, context, created_at_ms, valid_until_ms):
    """Explicit local producer seam. No runtime-LLM or HTTP proposal writer.

    Changing scope/cost/safety requires a new context source and proposal. A
    newer proposal for the same class/object supersedes the old approval.
    """
    if action_type not in CLASSES or action_type == 'first_live':
        raise ValueError('first_live_uses_factory_request')
    if not all(isinstance(x, str) and x for x in (affected_object, reason, required_action)):
        raise ValueError('proposal_description_required')
    if not bindings or len(bindings) > 16 or not isinstance(context, dict) or not context:
        raise ValueError('exact_context_and_bindings_required')
    if type(created_at_ms) is not int or type(valid_until_ms) is not int or valid_until_ms <= created_at_ms:
        raise ValueError('proposal_clock')
    for b in bindings:
        if b != binding(journal, b['kind'], b['identity']):
            raise ValueError('binding_stale')
    # Context (scope/cost/safety/config) must itself be retained in a source.
    if not any(b['kind'] == 'context' and source(journal, b['kind'], b['identity']) == context for b in bindings):
        raise ValueError('retained_context_required')
    body = dict(schema='owner-approval-item.v1', action_type=action_type,
                affected_object=affected_object, reason=reason, required_owner_action=required_action,
                bindings=bindings, context=context, config_sha256=digest(cfg),
                created_at_ms=created_at_ms, available_at_ms=created_at_ms,
                valid_until_ms=valid_until_ms)
    item = dict(body, item_id=digest(body))
    ensure(journal)
    with journal._tx() as c:
        c.execute('INSERT OR IGNORE INTO owner_approval_items VALUES (?,?)',
                  (item['item_id'], canonical(item)))
    return item


def validity(journal, cfg, item, now_ms):
    reasons = []
    if digest({k: v for k, v in item.items() if k != 'item_id'}) != item['item_id']:
        return 'INVALID', ['item_hash_mismatch']
    if now_ms < item['available_at_ms']:
        reasons.append('not_yet_available')
    if now_ms >= item['valid_until_ms']:
        reasons.append('expired')
    if digest(cfg) != item['config_sha256']:
        reasons.append('configuration_changed')
    for b in item['bindings']:
        try:
            value = source(journal, b['kind'], b['identity'])
            if value is None or digest(value) != b['sha256']:
                reasons.append('source_missing_or_changed:' + b['identity'])
        except (ValueError, KeyError, TypeError):
            reasons.append('source_unreadable:' + str(b.get('identity')))
    newer = journal.query('SELECT payload FROM owner_approval_items WHERE item_id!=?', (item['item_id'],))
    for row in newer:
        other = json.loads(row['payload'])
        if (other['action_type'], other['affected_object']) == (item['action_type'], item['affected_object']) \
                and other['created_at_ms'] >= item['created_at_ms']:
            reasons.append('superseded_context')
            break
    return ('STALE' if reasons else 'VALID'), reasons


def items(journal, cfg, *, now_ms=None, identity=None, offset=0):
    if type(offset) is not int or offset < 0 or (identity and offset):
        raise ValueError("invalid_approval_offset")
    now_ms = int(time.time()*1000) if now_ms is None else now_ms
    out, unavailable = [], []
    if exists(journal, 'owner_approval_items'):
        for row in journal.query('SELECT payload FROM owner_approval_items ' + ('WHERE item_id=? ' if identity else '') +
                                 'ORDER BY rowid DESC', (identity,) if identity else ()):
            try:
                item = json.loads(row['payload'])
                state, reasons = validity(journal, cfg, item, now_ms)
                receipts = journal.query('SELECT payload FROM owner_approval_receipts WHERE item_id=?', (item['item_id'],))
                receipt = receipt_from(receipts[0]['payload'], item['item_id']) if receipts else None
                out.append(dict(item, validity=state, invalid_reasons=reasons,
                                binding_hash=digest(item), receipt=receipt,
                                status=receipt['decision'] if receipt and state == 'VALID' else state,
                                decision_operation='approval_decision' if item['action_type'] != 'serious_recovery' else 'resume'))
            except (ValueError, KeyError, TypeError):
                unavailable.append('invalid_governance_record')
    else:
        unavailable.append('governance_proposal_ledger_not_recorded')
    # Reuse Stage5 first-live requests and their strict evidence verifier.
    if exists(journal, 'strategy_approval_requests'):
        from trader.strategy import factory_handoff as f
        for row in journal.query('SELECT * FROM strategy_approval_requests ' + ('WHERE request_id=? ' if identity else '') +
                                 'ORDER BY recorded_at_ms DESC', (identity,) if identity else ()):
            request = None
            try:
                request = f._load(row)
                v = f.load_version(journal, request['version_id'])
                val = f.verify_validation(journal, v)
                if request['spec_hash'] != v['spec_hash'] or request['validation_receipt_id'] != val['receipt_id']:
                    raise ValueError('changed_version_or_evidence')
                f._verify_probation(journal, cfg, v, request['probation_receipt_id'])
                f.verify_install(journal, v, current=True)
                reasons, state = [], 'VALID'
            except (ValueError, KeyError, TypeError) as e:
                reasons, state = [str(e)], 'STALE'
            if request is None:
                unavailable.append('invalid_factory_request')
                continue
            receipts = journal.query('SELECT * FROM strategy_approval_decisions WHERE request_id=?', (request['request_id'],))
            receipt = f._load(receipts[0]) if receipts else None
            bound_hash = digest(dict(request=request, config_sha256=digest(cfg)))
            if receipt:
                try:
                    f.verify_owner_configuration(journal, cfg, request, receipt)
                except f.HandoffRefused as e:
                    reasons.append(e.code)
                    state = 'UNKNOWN' if e.code == 'owner_approval_configuration_not_recorded' else 'STALE'
            out.append(dict(item_id=request['request_id'], action_type='first_live',
                            affected_object=request['version_id'], reason='First real-money use requires exact owner decision',
                            required_owner_action='APPROVED or REJECTED; does not activate',
                            created_at_ms=request['requested_at_ms'], available_at_ms=request['requested_at_ms'],
                            binding_hash=bound_hash,
                            bindings=request, config_sha256=digest(cfg), validity=state,
                            invalid_reasons=reasons, receipt=receipt,
                            status=receipt['decision'] if receipt and state == 'VALID' else state,
                            decision_operation='approval_decision'))
    else:
        unavailable.append('first_live_request_ledger_not_recorded')
    from datetime import datetime, timezone
    from trader.dashboard.owner_api import read_supervisor
    supervisor, error = read_supervisor(journal, datetime.fromtimestamp(now_ms/1000, timezone.utc))
    if error:
        unavailable.append(error)
    elif supervisor['needs_owner']:
        # This item grants nothing. Recovery remains the existing guarded path
        # which fences current safety/control context and records its audit.
        raw = json.loads(journal.kv_get('supervisor_status'))
        item_id = 'recovery:' + digest(raw)[:48]
        if identity is None or identity == item_id:
            at = supervisor['observed_at']
            ms = int(datetime.fromisoformat(at).timestamp()*1000) if at else None
            out.append(dict(item_id=item_id, action_type='serious_recovery', affected_object='supervisor',
                            reason='; '.join(supervisor['reasons']) or 'safe autonomous recovery not proven',
                            required_owner_action='Inspect evidence and submit guarded resume in Operations',
                            created_at_ms=ms, available_at_ms=ms, config_sha256=digest(cfg),
                            binding_hash=digest(dict(supervisor=raw, config_sha256=digest(cfg))),
                            bindings=raw, validity='VALID' if supervisor['freshness']=='fresh' else 'STALE',
                            invalid_reasons=[] if supervisor['freshness']=='fresh' else ['supervisor_evidence_stale_current_need_unknown'],
                            status='PENDING', receipt=None, decision_operation='resume'))
    # Pending/stale mandatory actions precede completed history across all ledgers.
    # Slice only after ordering, and expose every later page to the owner.
    pending = [i for i in out if not i['receipt'] or i['validity'] != 'VALID']
    complete = [i for i in out if i['receipt'] and i['validity'] == 'VALID']
    ordered = pending + complete
    has_more = len(ordered) > offset+100
    return dict(generated_at=datetime.now(timezone.utc).isoformat(),
                schema='owner-needs-you.v1', items=ordered[offset:offset+100], truncated=has_more,
                pending_total=len(pending), total=len(ordered),
                page=dict(offset=offset, limit=100, has_more=has_more,
                          next_offset=offset+100 if has_more else None, order='pending/stale before completed history'),
                unavailable=[dict(field='approvals', reason=x) for x in unavailable],
                source='exact owner governance and Strategy Factory request ledgers',
                classes=list(CLASSES), grants='Decisions only. No activation, spend, risk/config mutation or order.')


def decide(journal, cfg, args, *, actor, request_id, now_ms):
    matches = [i for i in items(journal, cfg, now_ms=now_ms, identity=args['item_id'])['items'] if i['item_id'] == args['item_id']]
    if len(matches) != 1:
        raise ValueError('approval_missing_or_outside_bounded_window')
    item = matches[0]
    if item['binding_hash'] != args['binding_hash'] or item['validity'] != 'VALID':
        raise ValueError('approval_stale_or_hash_changed')
    if item['action_type'] == 'serious_recovery':
        raise ValueError('recovery_requires_existing_guarded_resume')
    if item['action_type'] == 'first_live':
        from trader.strategy.factory_handoff import record_owner_decision
        result = record_owner_decision(journal, cfg, item['item_id'], args['decision'], actor=actor, decided_at_ms=now_ms)
        ensure(journal)
        body = dict(item_id=item['item_id'], binding_hash=item['binding_hash'], decision=args['decision'],
                    actor=actor, owner_request_id=request_id, decided_at_ms=now_ms, factory_result=result)
        body = dict(body, receipt_id=digest(body))
        with journal._tx() as c:
            c.execute('INSERT INTO owner_approval_receipts VALUES (?,?)', (item['item_id'], canonical(body)))
        return result
    if item['receipt']:
        if item['receipt']['decision'] != args['decision']:
            raise ValueError('decision_already_recorded')
        return item['receipt']
    body = dict(item_id=item['item_id'], binding_hash=item['binding_hash'], decision=args['decision'],
                actor=actor, owner_request_id=request_id, decided_at_ms=now_ms, grants='owner decision only')
    receipt = dict(body, receipt_id=digest(body))
    with journal._tx() as c:
        c.execute('BEGIN IMMEDIATE')
        # Re-read validity under the write lock, so source edits cannot race
        # the approval commit. This does not mutate referenced policy objects.
        fresh_item = json.loads(c.execute('SELECT payload FROM owner_approval_items WHERE item_id=?', (item['item_id'],)).fetchone()[0])
        current, _ = validity(journal, cfg, fresh_item, now_ms)
        if current != 'VALID' or digest(fresh_item) != args['binding_hash']:
            raise ValueError('approval_context_changed_before_commit')
        c.execute('INSERT INTO owner_approval_receipts VALUES (?,?)', (item['item_id'], canonical(receipt)))
    return receipt
