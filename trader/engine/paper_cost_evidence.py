"""Immutable paper economic evidence.

Source validators are code-owned adapters, not config switches. Two are
registered: a public no-funding-crossing funding adapter, and the labelled
ESTIMATE of commission and slippage that the exact version's Backtest PASS
already charged (`quant_contract.cost_model`). The estimate is the owner's
chosen paper cost source (SOL1 closure); it is never presented as venue
economics. Account fill fees do not prove hypothetical paper fees, reference
fills do not prove execution costs, and the model's flat funding fallback is
deliberately not used: funding comes only from the funding evidence below, so
it cannot be counted twice. Tests alone register fixture authorities.
Digests prove frozen internal consistency, not external venue authenticity.
"""
from datetime import datetime
import json
import math
import sqlite3

from .paper_exit_evidence import canonical, digest
from trader.observability.funding_events import (METHOD as FUNDING_METHOD, validate_no_crossing,
    CROSSING_METHOD as FUNDING_CROSSING_METHOD, validate_crossing)

SCHEMA = 'versioned-paper-cost-receipt.v1'
TABLE = 'versioned_paper_cost_receipts'
SOURCES = 'versioned_paper_cost_sources'
# A future adapter must validate source authority/time scope; execution adapters
# must also establish an already validated method, size coverage and freshness.
ESTIMATE_METHOD = 'LUFFY_BACKTEST_COST_MODEL_ESTIMATE_V1'
ESTIMATE_VERSION = 'paper-cost-model-estimate.v1'
ESTIMATE_LABEL = 'ESTIMATED_MODEL_NOT_VENUE_ACTUAL'


def _estimate_body(bound, kind, model, entry_atr, qualification_id):
    """Pure re-derivation of one estimate source from its declared inputs.

    Backtest definitions reused verbatim (quant_contract.DEFINITIONS):
    slippage = slippage_atr_frac x ATR(signal bar), paid adversely on entry and
    again on exit; fees = taker_fee_pct/100 x executable price x quantity per
    leg. The paper fill is the bare decision reference, so slippage enters once,
    as an adjustment to that fill (see leg_cost); funding is not modelled here.
    """
    if kind not in ('commission', 'slippage') or model.get('basis') != 'ESTIMATED_MODEL':
        raise ValueError('estimate_model_invalid')
    fee_pct, frac = number(model['taker_fee_pct']), number(model['slippage_atr_frac'])
    atr = number(entry_atr)
    if fee_pct < 0 or frac < 0 or atr <= 0 or not qualification_id:
        raise ValueError('estimate_inputs_invalid')
    sign = 1 if bound['side'] == 'long' else -1
    body = dict(method=ESTIMATE_METHOD, kind=kind, currency='USDT',
                version=ESTIMATE_VERSION, captured_ms=bound['exit']['time_ms'],
                binding=bound)
    calc = {}
    for leg, adverse in (('entry', sign), ('exit', -sign)):
        fill = number(bound[leg]['fill'])
        if kind == 'commission':
            body[leg] = dict(rate=fee_pct / 100, classification='taker')
            calc[leg] = dict(rate=fee_pct / 100, fill=fill,
                             formula='rate x executable_price x quantity (executable price from the slippage estimate)')
        else:
            body[leg] = dict(executable_price=fill + adverse * frac * atr)
            calc[leg] = dict(fill=fill, adverse_direction=adverse, atr=atr,
                             slippage_atr_frac=frac,
                             formula='fill + adverse_direction x slippage_atr_frac x ATR')
    body['provenance'] = dict(
        label=ESTIMATE_LABEL, cost_model=dict(model), qualification_id=qualification_id,
        entry_atr=atr, atr_basis='ATR(14) through the signal bar, as frozen in the trade exit state',
        calculation=calc,
        assumptions=['commission is the taker rate on both legs; maker rebates are not assumed',
                     'slippage is adverse on both legs and scales with signal-bar ATR',
                     'the backtest flat funding fallback is not applied; funding is separate evidence',
                     'an estimate of the backtest model, not a venue schedule or fill'])
    body['source_id'] = digest(canonical(body))
    return body


def estimate_source(bound, kind, model, entry_atr, qualification_id):
    return _estimate_body(bound, kind, model, entry_atr, qualification_id)


def validate_estimate(source):
    try:
        p = source['provenance']
        expected = _estimate_body(source['binding'], source['kind'], p['cost_model'],
                                  p['entry_atr'], p['qualification_id'])
        return source == expected
    except (KeyError, ValueError, TypeError, ArithmeticError):
        return False


SOURCE_VALIDATORS = {FUNDING_METHOD: validate_no_crossing,
                     FUNDING_CROSSING_METHOD: validate_crossing,
                     ESTIMATE_METHOD: validate_estimate}
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
                **({'venue_environment': trade['venue_environment']}
                   if trade.get('venue_environment') is not None else {}),
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


def estimate_context(journal, trade, version):
    """(cost_model, entry_atr, qualification_id) for one paper trade, read from
    the exact version's verified Backtest PASS and the trade's own frozen exit
    state. Refuses, never defaults, when either is unavailable (a Referee-sourced
    version carries no backtest cost model, nor does a legacy PASS without one)."""
    from ..strategy import factory_handoff as F, qualification as Q, exit_policy as E
    try:
        q = F.verify_validation(journal, version).get('qualification')
        if not q:
            raise ValueError('cost_model_source_unavailable')
        model = Q.cost_model_of(q)
        policy, _ = E.decode(json.loads(trade['entry_identity_json'])['exit_state'])
        return model, number(policy.entry_atr), q['qualification_id']
    except Q.QualificationRefused as exc:
        raise ValueError(exc.code) from exc
    except F.HandoffRefused as exc:
        raise ValueError('cost_model_validation_unverified') from exc


def estimate_ids(db, bound, context):
    """Freeze the two estimate sources for this trade and return their ids."""
    model, atr, qid = context
    ids = {}
    for kind in ('commission', 'slippage'):
        source = estimate_source(bound, kind, model, atr, qid)
        freeze_source(db, source)
        ids[kind] = source['source_id']
    return ids


def finalize_estimated(db, trade, version, install, context, funding_source_id=None):
    """The one production finalizer: estimated commission and slippage from the
    exact version's PASS cost model, plus funding only from funding evidence.
    Without a usable context the receipt records every dimension UNAVAILABLE."""
    ensure(db)
    ids = {}
    if context is not None:
        ids = estimate_ids(db, binding(trade, version, install), context)
    if funding_source_id:
        ids['funding'] = funding_source_id
    return finalize(db, trade, version, install, ids or None)


def finalize(db, trade, version, install, source_ids=None):
    ensure(db)
    body = build(db,binding(trade,version,install),source_ids)
    insert(db,TABLE,'trade_id',trade['id'],body)
    from trader.learning import capture as lc, capture_runtime as lr
    from time import time_ns
    lc.safely(db, 'decision:'+str(trade.get('decision_id')), lr.paper_cost, trade, body,
              observed_ms=time_ns()//1000000)
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
    for kind in ('commission', 'slippage'):
        sid = (body.get('source_ids') or {}).get(kind)
        src = journal.query(f'SELECT * FROM {SOURCES} WHERE source_id=?', (sid,)) if sid else []
        if src and json.loads(src[0]['canonical_json']).get('method') == ESTIMATE_METHOD:
            # The estimate must be the exact version's own PASS model and this
            # trade's own frozen ATR, not merely internally consistent.
            p = json.loads(src[0]['canonical_json'])['provenance']
            if (p['cost_model'], p['entry_atr'], p['qualification_id']) != estimate_context(journal, trade, version):
                raise ValueError('cost_estimate_inputs_differ')
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
