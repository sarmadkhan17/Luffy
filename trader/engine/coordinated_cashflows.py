"""Verify native-side legs and count actual symbol funding once per group.

This is a pure projection of complete retained histories, never a per-leg
funding estimate or new execution authority.
"""
from copy import deepcopy
import math

from . import trade_accounting as T, position_mode as PM
from .accounting import digest, number


def native_group(parts, members, group_id):
    from .coordinated_accounting import entry_binding
    by_id = {p['capture']['bookings']['trade']['id']:p for p in parts}
    ids = {m['trade_id'] for m in members}
    if len(by_id) != len(parts) or set(by_id) != ids or len(ids) != len(members):
        raise ValueError('native_group_members_differ')
    exact = None
    fills, cash, events, intervals, outputs = {}, {}, {}, [], []
    net, gross = 0., 0.
    for member in members:
        part = by_id[member['trade_id']]
        capture = part['capture']; trade = capture['bookings']['trade']
        version = entry_binding(part, member, group_id)
        if (not all(version) or (exact and version != exact)
                or part['identity'].get('status') != 'VERIFIED'):
            raise ValueError('native_group_version_or_account_differs')
        exact = version
        side = PM.position_side(part['identity'])
        if (side not in ('LONG', 'SHORT') or side != trade['side'].upper()
                or member.get('position_side') != side or capture.get('position_side') != side
                or capture.get('schema_version') != T.HEDGE_LEG_VERSION
                or part['entry_request']['intent'].get('position_side') != side
                or set(capture['overlapping_trade_ids']) - ids):
            raise ValueError('native_group_side_or_ownership_differs')
        result = T.replay(capture)
        if result.get('leg_verified') is not True:
            raise ValueError('native_group_component_pending')
        symbol, lo, hi = capture['venue_symbol'], capture['start_ms'], capture['end_ms']
        for old_symbol, old_side, start, end, _, _, _ in intervals:
            if symbol == old_symbol and side == old_side and not (hi < start or end < lo):
                raise ValueError('native_group_overlapping_side_ownership')
        raw_fills = T.history_rows(capture['fill_history'], 'fills', symbol, lo, hi, capture['observed_ms'])
        raw_cash = T.history_rows(capture['funding_history'], 'income', symbol, lo, hi, capture['observed_ms'])
        raw_events = T.history_rows(capture['funding_events'], 'funding_events', symbol,
            max(0, lo-6*T.DAY), capture['observed_ms'] if capture.get('terminal_flat') else hi,
            capture['observed_ms'])
        intervals.append((symbol, side, lo, hi, raw_fills, raw_cash, raw_events))
        for fill in raw_fills:
            if fill.get('positionSide') != side:
                continue
            key = symbol, str(fill['id'])
            if key in fills:
                raise ValueError('native_group_duplicate_fill')
            fills[key] = fill
        for row in raw_cash:
            key = symbol, str(row['tranId'])
            if key in cash and cash[key] != row:
                raise ValueError('native_group_funding_history_changed')
            cash[key] = row
        for event in raw_events:
            key = symbol, number(event['fundingTime'])
            if key in events and events[key] != event:
                raise ValueError('native_group_funding_events_changed')
            events[key] = event
        net += result['net_excluding_funding_usdt']
        gross += result['marked_notional_usdt']
        for fill in result['fills']:
            copied = deepcopy(fill)
            copied['id'] = digest([member['instrument_id'], fill['id']])
            outputs.append(copied)
    # Every raw fill seen on this symbol belongs to a retained group order.
    # Completeness cannot be obtained by merely filtering out another owner.
    for symbol, side, lo, hi, raw_fills, raw_cash, _ in intervals:
        if any(fills.get((symbol, str(f['id']))) != f for f in raw_fills):
            raise ValueError('native_group_unowned_fill')
        observed = {str(r['tranId']):r for r in raw_cash}
        for (s, cid), row in cash.items():
            if s == symbol and lo <= number(row['time']) <= hi and observed.get(cid) != row:
                raise ValueError('native_group_funding_history_changed')
    funding = 0.
    for (symbol, _), row in cash.items():
        ts = number(row['time'])
        if row.get('incomeType') != 'FUNDING_FEE' or row.get('asset') != 'USDT':
            raise ValueError('native_group_funding_currency_or_type_differs')
        if (symbol, ts) not in events:
            raise ValueError('native_group_funding_event_missing')
        funding += number(row['income'])
    for (symbol, ts), event in events.items():
        relevant = [f for (s, _), f in fills.items() if s == symbol]
        if any(number(f['time']) == ts for f in relevant):
            raise ValueError('native_group_funding_fill_boundary_ambiguous')
        balances = {side:sum(number(f['qty']) * (1 if f['side'] ==
                    ('BUY' if side == 'LONG' else 'SELL') else -1)
                    for f in relevant if f['positionSide'] == side and number(f['time']) < ts)
                    for side in ('LONG', 'SHORT')}
        active = any(lo <= ts <= hi for s, _, lo, hi, _, _, _ in intervals if s == symbol)
        payments = [r for (s, _), r in cash.items() if s == symbol and number(r['time']) == ts]
        if payments and (not active or max(balances.values()) <= 1e-8):
            raise ValueError('native_group_funding_without_exposure')
        if (active and not T.close(balances['LONG'], balances['SHORT'])
                and number(event['fundingRate']) != 0 and not payments):
            raise ValueError('native_group_funding_publication_pending')
    if not all(math.isfinite(v) for v in (funding, net, gross)):
        raise ValueError('native_group_nonfinite_aggregate')
    if len({f['id'] for f in outputs}) != len(outputs):
        raise ValueError('native_group_duplicate_fill')
    return dict(fills=outputs, funding_usdt=funding, net_pnl_usdt=net+funding,
                gross_notional_usdt=gross, funding_basis='ACTUAL_SYMBOL_CASHFLOWS_COUNTED_ONCE')
