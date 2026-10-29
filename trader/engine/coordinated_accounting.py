"""One economic outcome from existing verified whole-trade receipts.

No new venue reads, prices or funding estimates. Current scope is distinct
linear USD-M instruments; shared-instrument cashflows need separate ownership
support and must never be counted twice. The durable closed unwind manifest
fixes membership, including a primary whose hedge was never activated.
"""
from copy import deepcopy
from . import trade_accounting as T, entry_authority as A
from .accounting import digest

VERSION = 'coordinated-trade-accounting.v1'


def entry_binding(part, member, group_id):
    """Shared immutable order/version binding for marked and final accounting."""
    capture, identity, request = part['capture'], part['identity'], part['entry_request']
    trade = capture['bookings']['trade']
    group = identity.get('coordinated') or {}
    binding = identity.get('execution_binding') or {}
    intent = request['intent']
    if (A.digest(identity) != member['entry_identity_sha256']
            or group.get('group_id') != group_id or group.get('leg') != member['leg']
            or request['state'] != 'TERMINAL'
            or A.digest(intent) != request['intent_hash']
            or request['logical_id'] != binding.get('logical_id')
            or intent.get('strategy_identity', {}).get('coordinated') != group
            or any(intent.get('strategy_identity', {}).get(k) != identity.get(k)
                   for k in ('status','strategy_id','version_id','spec_sha256','install_id'))
            or intent.get('capability') != binding.get('capability')
            or intent.get('instrument_id') != binding.get('instrument_id')
            or intent.get('symbol') != trade['symbol']
            or intent.get('strategy_id') != trade['strategy_id']
            or request['result'].get('position_id') != trade['id']
            or str(request['order_id']) != str(capture['orders'][0]['orderId'])
            or member['symbol'] != trade['symbol']
            or binding.get('instrument_id') != member['instrument_id']):
        raise ValueError('coordinated_accounting_entry_ownership_differs')
    if capture.get('schema_version')==T.INTERVAL_VERSION:
        proof=capture.get('terminal_flat') or {}
        if (proof.get('instrument_id')!=member['instrument_id']
                or proof.get('account_scope')!=(binding.get('capability') or {}).get('account_scope')):
            raise ValueError('coordinated_accounting_terminal_flat_scope_differs')
    version = (trade['strategy_id'], identity.get('version_id'),
               identity.get('spec_sha256'), identity.get('install_id'),
               binding.get('capability', {}).get('account_scope'))
    return version


def accounting(source):
    if source.get('schema_version') != VERSION:
        raise ValueError('coordinated_accounting_schema_differs')
    close = source['closure']
    plan = close['plan']
    if close['state'] != 'CLOSED' or A.digest(plan) != close['plan_sha256']:
        raise ValueError('coordinated_accounting_close_pending')
    members = plan['members']
    parts = source['components']
    if not 1 <= len(parts) <= 32 or len(parts) != len(members):
        raise ValueError('coordinated_accounting_members_incomplete')
    by_id = {p['capture']['bookings']['trade']['id']: p for p in parts}
    if len(by_id) != len(parts) or set(by_id) != {m['trade_id'] for m in members}:
        raise ValueError('coordinated_accounting_members_differ')
    primary = [m for m in members if m['leg'] == 'primary']
    if len(primary) != 1 or any(m['leg'] not in ('primary', 'hedge') for m in members):
        raise ValueError('coordinated_accounting_primary_required')
    primary_id = primary[0]['trade_id']
    root = by_id[primary_id]['capture']
    primary_confirmed = root['bookings']['receipts'][0]['observed_ms']
    seen_instruments, seen_cashflows, exact, fills, funding = set(), set(), None, [], 0.
    for member in members:
        part = by_id[member['trade_id']]
        capture, identity, request = part['capture'], part['identity'], part['entry_request']
        result = T.replay(capture)
        if not result['learning_eligible']:
            raise ValueError('coordinated_accounting_component_pending')
        trade = capture['bookings']['trade']
        version = entry_binding(part, member, plan['group_id'])
        if not all(version) or identity.get('status') != 'VERIFIED' or (exact and exact != version):
            raise ValueError('coordinated_accounting_version_or_account_differs')
        exact = version
        if member['instrument_id'] in seen_instruments:
            raise ValueError('coordinated_accounting_shared_instrument_unsupported')
        seen_instruments.add(member['instrument_id'])
        cashflow_key=(capture['venue'],capture['environment'],capture['venue_symbol'])
        if cashflow_key in seen_cashflows:
            raise ValueError('coordinated_accounting_shared_instrument_unsupported')
        seen_cashflows.add(cashflow_key)
        if member['leg'] == 'hedge' and capture['start_ms'] < primary_confirmed:
            raise ValueError('coordinated_accounting_primary_not_first')
        for fill in result['accounting']['fills']:
            copied = deepcopy(fill)
            # IDs are venue-instrument scoped. Retain the original full source,
            # namespace the combined view, and attribute it to the economic root.
            copied.update(id=digest([member['instrument_id'], fill['id']]), trade_id=primary_id)
            fills.append(copied)
        funding += result['funding_usdt']
    cut = max(p['capture']['captured_ms'] for p in parts)
    available=source['observed_ms']
    if type(available) is not int or available<cut:
        raise ValueError('coordinated_accounting_observation_clock_differs')
    account = dict(schema_version='execution-accounting.v1', complete=True,
        trade_id=primary_id, symbol=root['bookings']['trade']['symbol'], venue=root['venue'],
        environment=root['environment'], reconciliation_version=VERSION, fills=fills,
        currency='USDT', funding_complete=True, funding_net=funding,
        resolved_ms=cut, available_ms=available)
    return account


def verified_outcome(source, imported_ms):
    from ..cognition.outcomes import verified_execution, _record
    account = accounting(source)
    root = next(p['capture'] for p in source['components']
                if p['capture']['bookings']['trade']['id'] == account['trade_id'])
    registration = dict(trade_id=account['trade_id'], symbol=account['symbol'],
        venue=account['venue'], environment=account['environment'], registered_ms=root['start_ms'])
    base = verified_execution(registration, account, imported_ms)
    return _record(base['kind'], dict(coordinated_trade_capture=source, registration=registration),
        base['registered_ms'], base['resolved_ms'], base['available_ms'], imported_ms,
        actual=base['actual_execution'])
