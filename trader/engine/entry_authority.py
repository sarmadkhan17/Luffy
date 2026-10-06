"""Final deterministic entry authority over the existing registry and recovery ledger.

No venue acquisition, owner approval, strategy promotion or LLM capability.
Capabilities are exact observations supplied by the existing registry producer;
UNKNOWN permission/leverage/freshness cannot authorize exposure.
"""
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
import time
import weakref

from ..core.instrument_registry import RegistrySnapshot, Eligibility, AccountTrading, Capability, Presence, SCHEMA_VERSION as REGISTRY_SCHEMA
from ..core.types import Action, ControlState
from .control_fence import control_fence, persisted_state, entry_block, latest_intent_event_id

CAP_KEY = 'entry_capability:'
SCHEMA = '''
CREATE TABLE IF NOT EXISTS execution_requests (
 logical_id TEXT PRIMARY KEY, decision_id TEXT NOT NULL UNIQUE,
 intent_hash TEXT NOT NULL, intent_json TEXT NOT NULL,
 client_order_id TEXT NOT NULL UNIQUE, state TEXT NOT NULL,
 recovery_json TEXT, order_id TEXT, result_json TEXT
);
'''


def canonical(x):
    return json.dumps(x, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(x):
    return hashlib.sha256(canonical(x).encode()).hexdigest()


def positive(value, *, zero=False):
    if isinstance(value, bool) or value is None:
        raise ValueError('entry_numeric_invalid')
    try:
        v = Decimal(str(value))
        f = float(v)
    except (ValueError, TypeError, InvalidOperation, OverflowError):
        raise ValueError('entry_numeric_invalid') from None
    if not v.is_finite() or not math.isfinite(f) or v < 0 or (not zero and (v == 0 or f == 0)):
        raise ValueError('entry_numeric_invalid')
    return v


def exact_number(value, *, zero=False):
    text = format(positive(value, zero=zero), 'f')
    return text.rstrip('0').rstrip('.') if '.' in text else text


def register_capability(journal, snapshot, instrument_id, *, execution_symbol,
                        leverage_evidence, valid_until_ms, venue_market=None):
    """Record an existing RegistrySnapshot's exact capability, never infer it.

    valid_until_ms must come from a registered source/policy freshness basis.
    leverage_evidence is a retained account observation with source/receipt
    identity, actual and allowable leverage, account and instrument binding.
    No private endpoint is called here. Missing evidence stays unexecutable.
    """
    if not isinstance(snapshot, RegistrySnapshot):
        raise ValueError('canonical_registry_required')
    record = next((r for r in snapshot.records if r.instrument_id.value == instrument_id), None)
    if record is None or not execution_symbol or type(valid_until_ms) is not int or valid_until_ms < snapshot.as_of_ms:
        raise ValueError('capability_binding_invalid')
    raw = dict(schema='entry-capability.v1', snapshot_id=snapshot.snapshot_id,
               registry=json.loads(snapshot.canonical_json()), instrument_id=instrument_id,
               record=asdict(record), execution_symbol=execution_symbol,
               account_scope=snapshot.account_scope, observed_at_ms=snapshot.as_of_ms,
               venue_market_sha256=digest(venue_market) if isinstance(venue_market, dict) else None,
               valid_until_ms=valid_until_ms, leverage=leverage_evidence)
    raw['receipt_id'] = digest(raw)
    journal.kv_set(CAP_KEY + execution_symbol, canonical(raw))
    return raw


def observe_leverage(*, instrument_id, account_scope, snapshot_id, symbol_config,
                     leverage_brackets, observed_at_ms, valid_until_ms, source):
    """Replayable projection of already-observed private capability responses.

    No network or configured desired leverage is used to construct facts.
    Missing/contradictory responses cannot become a verified allowance.
    """
    venue_symbol = instrument_id.split(':')[-1]
    if (not source or not account_scope or not snapshot_id
            or symbol_config.get('symbol') != venue_symbol
            or leverage_brackets.get('symbol') != venue_symbol
            or symbol_config.get('marginType') != 'CROSSED'
            or type(observed_at_ms) is not int or type(valid_until_ms) is not int
            or observed_at_ms < 0 or valid_until_ms < observed_at_ms):
        raise ValueError('leverage_observation_unverified')
    actual = positive(symbol_config.get('leverage'))
    if actual != actual.to_integral_value():
        raise ValueError('leverage_observation_invalid')
    ranges = []
    for row in leverage_brackets.get('brackets', []):
        floor = positive(row.get('notionalFloor'), zero=True)
        ceiling = positive(row.get('notionalCap'))
        maximum = positive(row.get('initialLeverage'))
        if floor >= ceiling or maximum < 1 or maximum != maximum.to_integral_value():
            raise ValueError('leverage_brackets_invalid')
        ranges.append(dict(notional_floor=exact_number(floor, zero=True),
                           notional_cap=exact_number(ceiling), maximum=exact_number(maximum)))
    ranges.sort(key=lambda r: positive(r['notional_floor'],zero=True))
    if not ranges or any(positive(a['notional_cap']) != positive(b['notional_floor'],zero=True)
                         for a,b in zip(ranges,ranges[1:])):
        raise ValueError('leverage_brackets_invalid')
    raw = dict(schema='venue-leverage-capability.v1', status='VERIFIED', source=source,
               instrument_id=instrument_id, account_scope=account_scope, snapshot_id=snapshot_id,
               observed_at_ms=observed_at_ms, valid_until_ms=valid_until_ms,
               symbol_config=symbol_config, leverage_brackets=leverage_brackets,
               actual=exact_number(actual), maximum=exact_number(max(positive(r['maximum']) for r in ranges)),
               brackets=ranges, margin_type=symbol_config['marginType'])
    raw['receipt_id'] = digest(raw)
    return raw


def capability(journal, symbol, now_ms, leverage, direction):
    raw = json.loads(journal.kv_get(CAP_KEY + symbol, 'null'))
    if not isinstance(raw, dict) or raw.get('receipt_id') != digest({k:v for k,v in raw.items() if k!='receipt_id'}):
        raise ValueError('canonical_capability_unavailable')
    from ..observability.attention import MAX_SNAPSHOT_AGE_MS
    from ..dashboard.current_truth import ACCOUNT_STALE_S
    reg = raw['registry']; r = raw['record']
    if (raw.get('schema') != 'entry-capability.v1' or reg.get('schema_version') != REGISTRY_SCHEMA
            or r.get('schema_version') != REGISTRY_SCHEMA):
        raise ValueError('capability_revision_incompatible')
    if (digest(reg) != raw['snapshot_id'] or r not in reg['records']
            or raw['execution_symbol'] != symbol or raw['account_scope'] != reg['account_scope']
            or raw['observed_at_ms'] != reg['as_of_ms']
            or not raw['observed_at_ms'] <= now_ms <= min(raw['valid_until_ms'], raw['observed_at_ms'] + MAX_SNAPSHOT_AGE_MS)):
        raise ValueError('capability_stale_or_mismatched')
    iid = r['instrument_id']
    if raw['instrument_id'] != ':'.join((iid['venue'], iid['market_type'], iid['venue_symbol'])):
        raise ValueError('canonical_identity_mismatch')
    if reg['account_trading'] != AccountTrading.ENABLED or r['account_eligibility'] != Eligibility.ELIGIBLE:
        raise ValueError('account_eligibility_unverified')
    basis = r.get('eligibility_basis')
    if not basis or basis.get('kind') != 'ACCOUNT_SYMBOL_PERMISSION' or basis['instrument_id'] != iid or not basis.get('evidence_ref'):
        raise ValueError('account_eligibility_unverified')
    if r['venue_status'] != 'TRADING' or not all(r.get(k) for k in ('base_asset','quote_asset','settlement_asset','contract_type')):
        raise ValueError('instrument_capability_unverified')
    if direction == 'SHORT' and r['shortability'] != Capability.PROVEN:
        raise ValueError('shortability_unverified')
    if positive(r['contract_multiplier']) != 1:
        raise ValueError('contract_multiplier_unsupported')
    if r['quote_asset'] != 'USDT' or r['settlement_asset'] != 'USDT' or iid['market_type'] != 'futures' or iid['venue'] != 'binance_usdm':
        raise ValueError('entry_exposure_model_unsupported')
    for field in ('price_tick','market_quantity_step','market_minimum_quantity','market_maximum_quantity','minimum_notional'):
        positive(r['constraints'].get(field))
    lev = raw.get('leverage') or {}
    if (r['symbol_config'] != Presence.PRESENT or r['leverage_bracket'] != Presence.PRESENT
            or lev.get('schema') != 'venue-leverage-capability.v1' or lev.get('status') != 'VERIFIED'):
        raise ValueError('leverage_unverified')
    rebuilt = observe_leverage(instrument_id=lev['instrument_id'], account_scope=lev['account_scope'],
        snapshot_id=lev['snapshot_id'],symbol_config=lev['symbol_config'],leverage_brackets=lev['leverage_brackets'],
        observed_at_ms=lev['observed_at_ms'], valid_until_ms=lev['valid_until_ms'], source=lev['source'])
    if lev != rebuilt or lev['snapshot_id'] != raw['snapshot_id']:
        raise ValueError('leverage_receipt_mismatch')
    brackets = lev.get('brackets')
    if not isinstance(brackets, list) or not brackets:
        raise ValueError('leverage_brackets_unverified')
    for bracket in brackets:
        if (positive(bracket.get('notional_floor'), zero=True) >= positive(bracket.get('notional_cap'))
                or positive(bracket.get('maximum')) < 1):
            raise ValueError('leverage_brackets_invalid')
    if (lev.get('status') != 'VERIFIED' or not lev.get('source') or not lev.get('receipt_id')
            or lev.get('instrument_id') != raw['instrument_id'] or lev.get('account_scope') != raw['account_scope']
            or type(lev.get('observed_at_ms')) is not int or type(lev.get('valid_until_ms')) is not int
            or not lev['observed_at_ms'] <= now_ms <= min(lev['valid_until_ms'], lev['observed_at_ms'] + int(ACCOUNT_STALE_S*1000))
            or positive(lev.get('actual')) != positive(leverage)
            or positive(lev.get('maximum')) < positive(leverage)):
        raise ValueError('leverage_unverified')
    return raw


def check_bracket(cap, amount, price, leverage):
    notional = positive(amount) * positive(price)
    matches = [b for b in cap['leverage']['brackets']
               if positive(b['notional_floor'], zero=True) <= notional < positive(b['notional_cap'])]
    if len(matches) != 1 or positive(leverage) > positive(matches[0]['maximum']):
        raise ValueError('leverage_bracket_not_permitted')


def account(journal):
    from ..dashboard.current_truth import read_account
    view, err = read_account(journal, datetime.now(timezone.utc))
    if err or view['freshness'] != 'fresh' or not view['authoritative']:
        raise ValueError('risk_account_not_fresh_authoritative')
    positive(view['equity'])
    return view, digest(json.loads(journal.kv_get('account_observation')))


def book(journal):
    return [dict(r) for r in journal.open_trades()]


def authoritative_inputs(journal, cap):
    """Reuse existing account/venue receipt validators and freshness policies."""
    from ..portfolio.common_factor import snapshot
    from ..portfolio.allocator import Source
    from .evidence_capture import verify_margin
    from ..dashboard.current_truth import ACCOUNT_STALE_S
    view, equity_id = account(journal)
    observation = json.loads(journal.kv_get('account_observation', 'null'))
    if observation.get('account_scope') != cap['account_scope']:
        raise ValueError('risk_account_scope_unverified')
    now = int(time.time()*1000)
    venue = json.loads(journal.kv_get('venue_position_snapshot', 'null'))
    snapshot(Source.freeze('venue_position_snapshot', venue), now)
    if venue.get('account_scope') != cap['account_scope']:
        raise ValueError('risk_venue_account_scope_unverified')
    margin = json.loads(journal.kv_get('account_margin_observation', 'null'))
    if (verify_margin(margin) is None or margin['status'] != 'AVAILABLE'
            or type(margin.get('observed_at_ms')) is not int
            or not margin['observed_at_ms'] <= now <= margin['observed_at_ms'] + int(ACCOUNT_STALE_S*1000)
            or margin['environment'] != venue['environment'] or margin.get('account_scope') != cap['account_scope']
            or positive(margin['equity_value_text']) != positive(view['equity'])):
        raise ValueError('risk_margin_unverified')
    available = positive(margin['value_text'], zero=True)
    rows = book(journal)
    if len(rows) != len(venue['positions']):
        raise ValueError('risk_book_not_reconciled')
    for v in venue['positions']:
        matches = []
        for row in rows:
            recorded = json.loads(row.get('entry_identity_json') or '{}')
            binding = recorded.get('execution_binding') or {}
            if (binding.get('instrument_id') == v['instrument_id']
                    and binding.get('capability', {}).get('account_scope') == cap['account_scope']):
                matches.append(row)
        if (len(matches) != 1 or matches[0]['side'] != v['side']
                or positive(matches[0]['amount']) != positive(v['quantity'])):
            raise ValueError('risk_book_identity_not_reconciled')
    return view, digest(dict(equity=equity_id, margin=margin, venue=venue)), rows, available


@dataclass(frozen=True)
class EntryPermission:
    payload_json: str
    release: object

    def payload(self):
        return json.loads(self.payload_json)


def proposal_binding(journal, symbol):
    """Freeze provenance before proposal/strategy evaluation; no permission."""
    try:
        raw = json.loads(journal.kv_get(CAP_KEY + symbol, 'null'))
    except Exception:
        return None
    if (not isinstance(raw, dict) or raw.get('execution_symbol') != symbol
            or raw.get('receipt_id') != digest({k:v for k,v in raw.items() if k!='receipt_id'})):
        return None
    return canonical(raw)


def intent(decision, requested, atr, stop, target, strategy_id, identity, reference, cap, trade_intent=None):
    price = positive((reference or {}).get('price'))
    qty = positive(requested); positive(atr); positive(stop); positive(target, zero=True)
    positive(qty * price)
    if (decision.action not in (Action.BUY, Action.SELL) or not isinstance(decision.id, str) or not decision.id.strip()
            or not isinstance(decision.cycle_id, str) or not decision.cycle_id.strip()):
        raise ValueError('entry_intent_invalid')
    direction = 'LONG' if decision.action == Action.BUY else 'SHORT'
    if (direction == 'LONG' and positive(stop) >= price) or (direction == 'SHORT' and positive(stop) <= price):
        raise ValueError('entry_stop_geometry_invalid')
    lineage = trade_intent.payload() if trade_intent else None
    binding = getattr(decision, 'instrument_binding_json', None)
    proposed = json.loads(binding) if binding else (lineage or {}).get('capability')
    if proposed is None:
        raise ValueError('proposal_canonical_capability_required')
    if proposed != cap:
        raise ValueError('proposal_capability_changed')
    if lineage and (lineage['instrument'] != cap['instrument_id'] or lineage['strategy_id'] != strategy_id
                    or lineage['direction'] != direction or lineage.get('capability_receipt_id') != cap['receipt_id'] or lineage.get('capability') != cap):
        raise ValueError('trade_intent_binding_mismatch')
    if lineage and (positive(lineage['requested_size']) < qty or lineage['size_unit'] != 'base_quantity'
                    or lineage['requested_action'] != 'OPEN' or lineage['status'] != 'COMPLETE'
                    or (identity or {}).get('version_id') != lineage['version_id']
                    or (identity or {}).get('spec_hash') != lineage['spec_hash']):
        raise ValueError('trade_intent_lineage_mismatch')
    return dict(schema='execution-intent.v1', logical_id=trade_intent.intent_id if trade_intent else decision.id, decision_id=decision.id,
                cycle_id=decision.cycle_id, strategy_id=strategy_id, strategy_identity=identity,
                symbol=decision.symbol, instrument_id=cap['instrument_id'], capability=cap,
                direction=direction, requested=exact_number(qty), atr=exact_number(atr), stop=exact_number(stop),
                target=exact_number(target, zero=True), reference=reference, trade_intent=lineage)


def authorize(manager, decision, requested, atr, stop, target, strategy_id, *, identity=None, reference=None, trade_intent=None):
    from ..observability.safety import entry_refusal
    refusal = entry_refusal(manager.journal, probe=True)
    if refusal:
        raise ValueError(refusal)
    with control_fence(manager.journal):
        control_id = latest_intent_event_id(manager.journal)
        if manager.journal.kv_get('control_state') != ControlState.ACTIVE.value:
            raise ValueError('control_entry_blocked')
    with manager._lock:
        cap = capability(manager.journal, decision.symbol, int(time.time()*1000), manager.leverage,
                         'LONG' if decision.action == Action.BUY else 'SHORT')
        raw = intent(decision, requested, atr, stop, target, strategy_id, identity, reference, cap, trade_intent)
        if trade_intent:
            if not manager.journal.query("SELECT 1 FROM sqlite_master WHERE type='table' AND name='strategy_versions'"):
                raise ValueError('trade_intent_version_unverified')
            lineage = raw['trade_intent']
            versions = manager.journal.query('SELECT strategy_id,spec_hash FROM strategy_versions WHERE version_id=?',
                                              (lineage['version_id'],))
            if len(versions) != 1 or versions[0]['strategy_id'] != strategy_id or versions[0]['spec_hash'] != lineage['spec_hash']:
                raise ValueError('trade_intent_version_unverified')
        view, account_id, positions, available = authoritative_inputs(manager.journal, cap)
        for row in positions:
            binding = json.loads(row.get('entry_identity_json') or '{}').get('execution_binding', {})
            if binding.get('instrument_id') == cap['instrument_id']:
                raise ValueError('risk_entry_refused:already_exposed_canonical_instrument')
        from ..core.types import Position, Side
        held = []
        for row in positions:
            for name in ('amount','entry_price','notional_usdt','stop_loss'):
                positive(row[name])
            fields = {k:row[k] for k in Position.__dataclass_fields__ if k in row}
            fields['side'] = Side(row['side']); held.append(Position(**fields))
        state = persisted_state(manager.journal)
        if (manager.journal.kv_get('control_state') != ControlState.ACTIVE.value or state != ControlState.ACTIVE
                or latest_intent_event_id(manager.journal) != control_id):
            raise ValueError('control_entry_blocked')
        price = float(positive(reference['price']))
        result = manager.check_entry(state, decision.symbol, price, float(positive(atr)),
                    abs(price-float(positive(stop)))/price, held, view['equity'],
                    int(manager.journal.query("SELECT COUNT(*) n FROM trades WHERE status='closed'")[0]['n']), 'futures')
        if not result.ok:
            raise ValueError('risk_entry_refused:' + result.reason)
        allowed = min(positive(requested), positive(result.amount))
        check_bracket(cap, allowed, reference['price'], manager.leverage)
        if allowed * positive(reference['price']) / positive(manager.leverage) > available:
            raise ValueError('risk_available_margin_exceeded')
        release = manager.release_check(view['equity'], authoritative=True)
        if not release.allowed:
            raise ValueError(release.reason)
        raw.update(allowed=str(allowed), risk_policy_id=manager.policy()['digest'],
                   account_id=account_id, book_id=digest(positions), risk_issuer=manager._identity,
                   control_intent_id=control_id,
                   risk_result='RESIZE' if allowed < positive(requested) else 'ALLOW',
                   decision_time_ms=int(time.time()*1000),
                   risk_release=release.as_dict(),
                   freshness_basis=dict(release_max_age_s=manager.release_max_age_s,
                       release_read_monotonic=release.read_at, account_stale_s=view['stale_after_s']))
        permission = EntryPermission(canonical(raw), release)
        if not hasattr(manager, '_entry_permissions'):
            manager._entry_permissions = weakref.WeakValueDictionary()
        manager._entry_permissions[digest(raw)] = permission
        return permission


def validate(executor, permission, decision, amount, atr, stop, target, strategy_id, identity, reference, conn=None):
    from ..observability.safety import entry_refusal
    refusal = entry_refusal(executor.journal)
    if refusal:
        raise ValueError(refusal)
    manager = executor.risk_manager
    if manager is None or not isinstance(permission, EntryPermission):
        raise ValueError('exact_risk_permission_required')
    raw = permission.payload()
    if getattr(manager, '_entry_permissions', {}).get(digest(raw)) is not permission:
        raise ValueError('risk_permission_not_issued')
    if raw['risk_issuer'] != manager._identity or manager.journal.db_path != executor.journal.db_path:
        raise ValueError('risk_issuer_mismatch')
    if latest_intent_event_id(executor.journal) != raw['control_intent_id']:
        raise ValueError('control_intent_superseded')
    cap = capability(executor.journal, decision.symbol, int(time.time()*1000), executor.leverage,
                     'LONG' if decision.action == Action.BUY else 'SHORT')
    trade_intent = None
    if raw['trade_intent']:
        from ..portfolio.trade_intent import TradeIntent
        lineage = raw['trade_intent']
        trade_intent = TradeIntent(lineage['intent_id'], canonical({k:v for k,v in lineage.items() if k!='intent_id'}))
    current = intent(decision, raw['requested'], atr, stop, target, strategy_id, identity, reference, cap, trade_intent)
    for k in current:
        if current[k] != raw[k]:
            raise ValueError('risk_intent_or_capability_mismatch')
    if positive(amount) > positive(raw['allowed']):
        raise ValueError('risk_size_exceeded')
    q = positive(amount); price = positive(reference['price']); positive(q*price)
    check_bracket(cap, q, price, executor.leverage)
    constraints = cap['record']['constraints']
    if (q % positive(constraints['market_quantity_step']) or q < positive(constraints['market_minimum_quantity'])
            or q > positive(constraints['market_maximum_quantity']) or q*price < positive(constraints['minimum_notional'])):
        raise ValueError('venue_order_constraints_invalid')
    if manager.policy()['digest'] != raw['risk_policy_id'] or executor.policy_id != raw['risk_policy_id']:
        raise ValueError('risk_policy_changed')
    _, account_id, _, _ = authoritative_inputs(executor.journal, cap)
    if account_id != raw['account_id'] or digest(book(executor.journal)) != raw['book_id']:
        raise ValueError('risk_inputs_changed')
    reason = manager._revalidate(permission.release, conn)
    if reason:
        raise ValueError(reason)
    # No network market lookup: use the exact already-loaded venue record.
    from ..data.feed import execution_account_scope
    if (getattr(executor.ex, 'id', None) != 'binanceusdm'
            or execution_account_scope(executor.ex) != cap['account_scope']):
        raise ValueError('venue_account_binding_unverified')
    from ..observability.portfolio_observation import trading_source
    environment, _ = trading_source(executor.ex)
    venue = json.loads(executor.journal.kv_get('venue_position_snapshot'))
    if venue['environment'] != environment:
        raise ValueError('venue_environment_mismatch')
    market = executor.ex.market(decision.symbol)
    r = cap['record']; iid = r['instrument_id']
    if not cap.get('venue_market_sha256') or digest(market) != cap['venue_market_sha256']:
        raise ValueError('venue_capability_record_changed')
    if (market.get('id') != iid['venue_symbol'] or market.get('base') != r['base_asset']
            or market.get('quote') != r['quote_asset'] or market.get('settle') != r['settlement_asset']
            or not market.get('linear') or not market.get('contract')
            or positive(market.get('contractSize')) != positive(r['contract_multiplier'])):
        raise ValueError('venue_instrument_mismatch')
    return raw


def submit(executor, permission, decision, amount, atr, stop, target,
           strategy_id, identity, reference, position):
    """Reserve durably before submission; ambiguous attempts are never replayed.

    Control flock serializes containment. Risk's existing IMMEDIATE transaction
    holds exact policy/account/capability/book truth through the venue call.
    The separately committed reservation survives failures of that transaction.
    """
    from dataclasses import replace
    from ccxt import InvalidOrder, InsufficientFunds
    from ..strategy import factory_handoff
    from .recovery import KEY
    from . import partial_intent
    from ..observability.safety import entry_refusal
    refusal = entry_refusal(executor.journal)
    if refusal:
        raise ValueError(refusal)
    manager = executor.risk_manager
    if manager is None or not isinstance(permission, EntryPermission):
        raise ValueError('exact_risk_permission_required')
    logical_id = permission.payload()['logical_id']
    from ..observability.safety import entry_refusal
    refusal = entry_refusal(executor.journal, probe=True)
    if refusal:
        raise ValueError(refusal)
    with control_fence(executor.journal):
        blocked, _ = entry_block(persisted_state(executor.journal))
        if blocked:
            raise ValueError(blocked)
        if executor.journal.kv_get('control_state') != ControlState.ACTIVE.value:
            raise ValueError('control_state_unverified')
        if executor.market_type.value != 'futures':
            raise ValueError('entry_exposure_model_unsupported')
        if factory_handoff.live_entry_block(executor.journal, strategy_id):
            raise ValueError('version_not_live_authorized')
        with manager._durable_hold() as conn:
            if conn is None:
                raise ValueError('durable_journal_required')
            if (conn.execute('SELECT 1 FROM trades WHERE decision_id=? LIMIT 1',(decision.id,)).fetchone()
                    or conn.execute("SELECT 1 FROM control_events WHERE event='execution_recovery' "
                        "AND CASE WHEN json_valid(detail) THEN json_extract(detail,'$.decision_id') END=? LIMIT 1",
                        (decision.id,)).fetchone()):
                raise ValueError('logical_action_already_reserved')
            if conn.execute('SELECT 1 FROM execution_requests WHERE logical_id=? OR decision_id=?',
                            (logical_id, decision.id)).fetchone():
                raise ValueError('logical_action_already_reserved')
            if executor.recovery.pending() or partial_intent.pending(executor.journal):
                raise ValueError('execution_recovery_pending')
            raw = validate(executor, permission, decision, amount, atr, stop, target,
                           strategy_id, identity, reference, conn)
            binding = {k:raw[k] for k in ('logical_id','instrument_id','capability',
                       'risk_policy_id','account_id','book_id','strategy_identity','trade_intent')}
            position = replace(position, entry_identity=dict(identity or {}, execution_binding=binding))
            client_id = 'lr_' + hashlib.sha256(logical_id.encode()).hexdigest()[:28]
            conn.execute('INSERT INTO execution_requests(logical_id,decision_id,intent_hash,intent_json,client_order_id,state) VALUES (?,?,?,?,?,?)',
                         (logical_id,decision.id,digest(raw),canonical(raw),client_id,'RISK_AUTHORIZED'))
            recovery = executor.recovery.begin(position, logical_id=logical_id, conn=conn)
        # Reservation is committed. A crash from here onward must reconcile.
        submitted_ms = int(time.time()*1000)
        order = error = None
        deferred = False
        with manager._durable_hold() as conn:
            if partial_intent.pending(executor.journal):
                # No submission has been attempted in this call. Record that
                # exact refusal durably; do not manufacture an ambiguous entry
                # alongside the genuinely unresolved partial exit.
                deferred = True
                conn.execute("UPDATE execution_requests SET state='REFUSED',result_json=? WHERE logical_id=?",
                    (canonical(dict(reason='execution_recovery_pending', submitted=False)), logical_id))
                conn.execute("INSERT OR REPLACE INTO state_kv(key,value) VALUES (?, 'null')", (KEY,))
            else:
                validate(executor, permission, decision, amount, atr, stop, target,
                         strategy_id, identity, reference, conn)
                if factory_handoff.live_entry_block(executor.journal, strategy_id):
                    raise ValueError('version_not_live_authorized')
                try:
                    order = executor.ex.create_order(decision.symbol, 'market',
                        'buy' if decision.action == Action.BUY else 'sell', amount,
                        params={'reduceOnly':False, 'newClientOrderId':client_id})
                except Exception as exc:
                    error = exc
                    if isinstance(exc, (InvalidOrder, InsufficientFunds)):
                        executor.recovery.save(recovery, 'entry_submission_rejected', conn=conn)
                        conn.execute("UPDATE execution_requests SET state='REFUSED',result_json=? WHERE logical_id=?",
                            (canonical(dict(reason=type(exc).__name__, submitted=True, submitted_ms=submitted_ms)), logical_id))
                        conn.execute("INSERT OR REPLACE INTO state_kv(key,value) VALUES (?, 'null')",(KEY,))
                    else:
                        executor.recovery.save(recovery, 'entry_submission_ambiguous', conn=conn)
                else:
                    recovery['order_id'] = str(order.get('id') or '')
                    executor.recovery.save(recovery, 'entry_submitted', conn=conn)
        if deferred:
            raise ValueError('execution_recovery_pending')
        return order, recovery, error, submitted_ms, binding
