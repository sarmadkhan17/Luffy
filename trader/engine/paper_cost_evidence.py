"""Immutable paper economic evidence, never a synthetic cost model.

Source validators are code-owned adapters, not config switches. None is currently
registered: account fill fees do not prove hypothetical paper fees and reference
fills do not prove execution costs. Tests alone register fixture authorities.
Digests prove frozen internal consistency, not external venue authenticity.
"""
from datetime import datetime
import json
import math
import sqlite3

from .paper_exit_evidence import canonical, digest

SCHEMA = 'versioned-paper-cost-receipt.v1'
TABLE = 'versioned_paper_cost_receipts'
SOURCES = 'versioned_paper_cost_sources'
# A future adapter must validate source authority/time scope; execution adapters
# must also establish an already validated method, size coverage and freshness.
SOURCE_VALIDATORS = {}
WIN_RATE_POLICY = {'basis': 'NET', 'source':
    'versioned probation _assess at 1ab0da0; versioned-paper-probation-truth-fix-r1'}


def ensure(db):
    for table, key in ((TABLE, 'trade_id'), (SOURCES, 'source_id')):
        db.execute(f'CREATE TABLE IF NOT EXISTS {table}({key} TEXT PRIMARY KEY, '
                   'canonical_json TEXT NOT NULL, canonical_sha256 TEXT NOT NULL)')
        # REPLACE can bypass DELETE triggers when recursive_triggers is off.
        db.execute(f"CREATE TRIGGER IF NOT EXISTS {table}_insert_immutable "
                   f"BEFORE INSERT ON {table} WHEN EXISTS(SELECT 1 FROM {table} WHERE {key}=NEW.{key}) "
                   "BEGIN SELECT RAISE(ABORT,'finalized cost evidence immutable'); END")
        for action in ('UPDATE', 'DELETE'):
            db.execute(f"CREATE TRIGGER IF NOT EXISTS {table}_{action.lower()}_immutable "
                       f"BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT,'finalized cost evidence immutable'); END")


def number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError('cost_number_unavailable')
    return value


def ms(value):
    d = datetime.fromisoformat(value)
    if d.tzinfo is None:
        raise ValueError('cost_clock_timezone_unavailable')
    return int(d.timestamp()*1000)


def binding(trade, version, install):
    identity = json.loads(trade['entry_identity_json'])
    exact = dict(strategy_id=version['strategy_id'], version_id=version['version_id'],
                 install_id=install['install_id'], spec_hash=version['spec_hash'])
    for key, value in exact.items():
        if (key in trade and trade[key] != value) or identity.get('spec_sha256' if key == 'spec_hash' else key) != value:
            raise ValueError('cost_trade_identity_differs')
    semantics = install['exit_semantics_id']
    if identity.get('exit_semantics_id') != semantics or trade['status'] != 'closed' or trade['exec_mode'] != 'paper':
        raise ValueError('cost_trade_semantics_differs')
    qty = number(trade['amount'])
    entry, exit = number(trade['entry_price']), number(trade['exit_price'])
    if min(qty, entry, exit) <= 0 or trade['side'] not in ('long', 'short'):
        raise ValueError('cost_trade_geometry_invalid')
    opened, closed = ms(trade['opened_at']), ms(trade['closed_at'])
    if closed < opened:
        raise ValueError('cost_trade_clock_invalid')
    # Legacy records lack market/reference provenance; retain null, never infer
    # the historical market from a current registry or symbol spelling.
    return dict(trade_id=trade['id'], **exact, exit_semantics_id=semantics,
                entry_identity_sha256=digest(trade['entry_identity_json']),
                instrument=trade['symbol'], market_type=trade.get('market_type'),
                side=trade['side'], entry=dict(time_ms=opened, quantity=qty,
                    reference=trade.get('reference_price'), fill=entry,
                    fill_basis=trade.get('fill_basis')),
                exit=dict(time_ms=closed, quantity=qty,
                    reference=trade.get('exit_reference_price'), fill=exit,
                    fill_basis=trade.get('exit_fill_basis')))


def unavailable(reason, provenance=None):
    return dict(status='UNAVAILABLE', amount=None, reason=reason, provenance=provenance)


def insert(db, table, key, value, body):
    text = canonical(body)
    existing = db.execute(f'SELECT canonical_json,canonical_sha256 FROM {table} WHERE {key}=?', (value,)).fetchone()
    if existing:
        if tuple(existing) != (text, digest(text)):
            raise ValueError('finalized_cost_evidence_conflict')
        return digest(text)
    db.execute(f'INSERT INTO {table} VALUES(?,?,?)', (value,text,digest(text)))
    return digest(text)


def freeze_source(db, source):
    """Persist only evidence accepted by a registered authoritative adapter."""
    validator = SOURCE_VALIDATORS.get(source.get('method'))
    if not validator or validator(source) is not True:
        raise ValueError('cost_source_authority_unavailable')
    ensure(db)
    return insert(db, SOURCES, 'source_id', source['source_id'], source)


def load_source(db, source_id, bound, kind):
    row = db.execute(f'SELECT * FROM {SOURCES} WHERE source_id=?', (source_id,)).fetchone()
    if row is None:
        raise ValueError('cost_source_missing')
    raw = json.loads(row['canonical_json'])
    if canonical(raw) != row['canonical_json'] or digest(row['canonical_json']) != row['canonical_sha256']:
        raise ValueError('cost_source_digest_differs')
    validator = SOURCE_VALIDATORS.get(raw.get('method'))
    if not validator or validator(raw) is not True:
        raise ValueError('cost_source_authority_unavailable')
    if raw.get('binding') != bound or raw.get('kind') != kind or raw.get('currency') != 'USDT':
        raise ValueError('cost_source_binding_differs')
    if not raw.get('provenance') or not raw.get('version') or type(raw.get('captured_ms')) is not int:
        raise ValueError('cost_source_provenance_missing')
    return raw, dict(source_id=source_id, sha256=row['canonical_sha256'],
                     method=raw['method'], provenance=raw['provenance'],
                     version=raw['version'], captured_ms=raw['captured_ms'])


def leg_cost(db, bound, kind, leg, source_id, execution=None):
    if not source_id:
        return unavailable('NO_AUTHORITATIVE_COMMISSION_SOURCE' if kind == 'commission'
                           else 'NOT_VALIDATED_EXECUTION_COST')
    raw, provenance = load_source(db, source_id, bound, kind)
    data = raw[leg]
    if kind == 'commission':
        # Unknown maker/taker is never inferred; a rate source must explicitly
        # prove its applicability, including a classification-independent rate.
        classification = data['classification']
        if classification not in ('maker', 'taker', 'classification_independent'):
            raise ValueError('commission_classification_unavailable')
        rate = number(data['rate'])
        if rate < 0:
            raise ValueError('commission_rate_invalid')
        # Commission must share the proven executable economics used by net
        # P&L. A known rate alone never licenses a paper-price fallback.
        execution_inputs = (execution or {}).get('inputs', {})
        execution_provenance = (execution or {}).get('provenance')
        if (not execution or execution.get('status') != 'ESTABLISHED'
                or not execution_provenance
                or execution_inputs.get('quantity') != bound[leg]['quantity']):
            item = unavailable('EXECUTABLE_COMMISSION_NOTIONAL_UNAVAILABLE',
                               {'commission_rate': provenance,
                                'execution': execution_provenance})
            item['inputs'] = dict(rate=rate, classification=classification,
                                  executable_price=None, quantity=bound[leg]['quantity'],
                                  notional=None, executable_price_evidence=execution_provenance)
            return item
        px = number(execution_inputs['executable_price'])
        quantity = number(execution_inputs['quantity'])
        if px <= 0 or quantity <= 0:
            raise ValueError('commission_executable_notional_invalid')
        basis = number(px * quantity)
        amount = basis * rate
        inputs = dict(rate=rate, classification=classification, notional=basis,
                      executable_price=px, quantity=quantity,
                      executable_price_evidence=execution_provenance)
    else:
        # Only a registered validated adapter may attest executable prices.
        px = number(data['executable_price'])
        if px <= 0:
            raise ValueError('execution_price_invalid')
        ref = number(bound[leg]['reference'])
        direction = (1 if bound['side'] == 'long' else -1) * (1 if leg == 'entry' else -1)
        # Gross paper P&L already uses paper fills. Adjust from that baseline;
        # subtracting the full decision-reference difference would count any
        # simulated reference-to-fill movement twice.
        baseline = bound[leg]['fill']
        amount = direction * (px-baseline) * bound[leg]['quantity']
        inputs = dict(executable_price=px, reference=ref, paper_fill=baseline,
                      basis='EXECUTABLE_ADJUSTMENT_TO_GROSS_PAPER_FILL',
                      quantity=bound[leg]['quantity'])
    number(amount)
    return dict(status='ESTABLISHED', amount=amount, reason='AUTHORITATIVE_REGISTERED_SOURCE',
                provenance=provenance, inputs=inputs)


def funding_cost(db, bound, source_id):
    if bound['market_type'] == 'spot':
        return dict(status='NOT_APPLICABLE', amount=None,
                    reason='SPOT_HAS_NO_POSITION_FUNDING', provenance={'market_type':'spot'})
    if bound['market_type'] != 'futures':
        return unavailable('MARKET_TYPE_FUNDING_APPLICABILITY_UNAVAILABLE')
    if not source_id:
        return unavailable('FUNDING_EVENT_RATE_AND_POSITION_BASIS_UNAVAILABLE')
    raw, provenance = load_source(db, source_id, bound, 'funding')
    # An adapter must prove complete actual event coverage and boundary timing;
    # an empty list alone is never evidence of no events.
    if raw.get('interval_complete') is not True or not raw.get('timing_semantics'):
        raise ValueError('funding_interval_unproven')
    events = raw['events']
    total, seen = 0.0, set()
    for event in events:
        ts = event['timestamp_ms']
        if (type(ts) is not int or not bound['entry']['time_ms'] < ts < bound['exit']['time_ms']
                or ts in seen or event['instrument'] != bound['instrument']):
            raise ValueError('funding_event_timestamp_or_instrument_invalid')
        seen.add(ts)
        # Boundary events are deliberately refused pending venue-specific proof.
        rate, px = number(event['rate']), number(event['position_basis_price'])
        if px <= 0 or event['quantity'] != bound['entry']['quantity'] or not event.get('basis_provenance'):
            raise ValueError('funding_position_basis_unproven')
        total += (1 if bound['side']=='long' else -1) * rate * px * event['quantity']
    number(total)
    return dict(status='ESTABLISHED' if events else 'NOT_APPLICABLE',
                amount=total if events else None,
                reason='ACTUAL_FUNDING_EVENTS' if events else 'PROVEN_NO_FUNDING_EVENT_IN_INTERVAL',
                provenance=provenance, events=events, timing_semantics=raw['timing_semantics'])


def build(db, bound, source_ids=None):
    ids = source_ids or {}
    if set(ids) - {'commission','slippage','funding'}:
        raise ValueError('unregistered_cost_dimension')
    dimensions = {}
    for kind in ('slippage','commission'):
        legs = {leg:leg_cost(db,bound,kind,leg,ids.get(kind),
                            dimensions['slippage']['legs'][leg] if kind == 'commission' else None)
                for leg in ('entry','exit')}
        complete = all(v['status']=='ESTABLISHED' for v in legs.values())
        dimensions[kind] = dict(status='ESTABLISHED' if complete else 'UNAVAILABLE',
            amount=sum(v['amount'] for v in legs.values()) if complete else None,
            reason='BOTH_LEGS_ESTABLISHED' if complete else 'ENTRY_AND_EXIT_COST_EVIDENCE_REQUIRED',
            provenance={leg:v['provenance'] for leg,v in legs.items()}, legs=legs)
    dimensions['funding'] = funding_cost(db,bound,ids.get('funding'))
    gross = (bound['exit']['fill']-bound['entry']['fill']) * bound['entry']['quantity'] * (1 if bound['side']=='long' else -1)
    number(gross)
    complete = all(v['status'] in ('ESTABLISHED','NOT_APPLICABLE') for v in dimensions.values())
    known = sum(v['amount'] for v in dimensions.values() if v['status']=='ESTABLISHED')
    diagnostics = {}
    for leg in ('entry','exit'):
        ref = bound[leg]['reference']
        diagnostics[leg] = dict(label='SIMULATED_PRICE_DIFFERENCE',
            qualification='NOT_VALIDATED_EXECUTION_COST',
            difference=bound[leg]['fill']-number(ref) if ref is not None else None)
    return dict(schema=SCHEMA, binding=bound, source_ids=ids, dimensions=dimensions,
                diagnostics=diagnostics, currency='USDT', gross_pnl=gross,
                known_costs=known if complete else None, known_costs_complete=complete,
                net_pnl=gross-known if complete else None,
                net_pnl_status='ESTABLISHED' if complete else 'UNAVAILABLE')


def finalize(db, trade, version, install, source_ids=None):
    ensure(db)
    body = build(db,binding(trade,version,install),source_ids)
    insert(db,TABLE,'trade_id',trade['id'],body)
    return body


def verify(journal, trade, version, install):
    # Verification is read-only. Legacy receipts are never fabricated here.
    if not journal.query("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (TABLE,)):
        raise ValueError('cost_receipt_missing')
    rows = journal.query(f'SELECT * FROM {TABLE} WHERE trade_id=?',(trade['id'],))
    if len(rows)!=1:
        raise ValueError('cost_receipt_missing')
    row = rows[0]
    body = json.loads(row['canonical_json'])
    if canonical(body)!=row['canonical_json'] or digest(row['canonical_json'])!=row['canonical_sha256']:
        raise ValueError('cost_receipt_digest_differs')
    if body.get('source_ids') and not journal.query(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (SOURCES,)):
        raise ValueError('cost_source_missing')
    bound = binding(trade,version,install)
    if body['binding'] != bound:
        raise ValueError('cost_receipt_binding_differs')
    # Access the Journal's existing connection through the read query protocol.
    class Reader:
        def execute(self, sql, params):
            class Result:
                def fetchone(self):
                    try:
                        rows = journal.query(sql,params)
                    except sqlite3.DatabaseError as exc:
                        raise ValueError('cost_source_storage_unavailable') from exc
                    return rows[0] if rows else None
            return Result()
    if build(Reader(),bound,body['source_ids']) != body:
        raise ValueError('cost_receipt_replay_differs')
    gross = number(trade['realized_pnl'])
    if not math.isclose(gross,body['gross_pnl'],rel_tol=1e-10,abs_tol=1e-10):
        raise ValueError('cost_gross_pnl_differs')
    return {**body,'receipt_sha256':row['canonical_sha256']}
