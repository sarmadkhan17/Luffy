"""Current combined equity from the same owned receipts as final accounting.

No account-wide profit, rate estimates, orders, or final learning outcomes.
This is the valuation/decision input for the existing position-management clock.
"""
import math
from . import trade_accounting as T
from .coordinated_accounting import entry_binding
from ..research.compose import managed_hedge as M

VERSION='coordinated-marked-equity.v1'


def assess(source, *, now_ms):
    if source.get('schema_version')!=VERSION:
        raise ValueError('coordinated_marked_schema')
    members,parts=source['members'],source['components']
    if not 1<=len(parts)<=2 or len(members)!=len(parts):
        raise ValueError('coordinated_marked_members_incomplete')
    by_id={p['capture']['bookings']['trade']['id']:p for p in parts}
    if len(by_id)!=len(parts) or set(by_id)!={m['trade_id'] for m in members}:
        raise ValueError('coordinated_marked_members_differ')
    primary=[m for m in members if m['leg']=='primary']
    if len(primary)!=1 or any(m['leg'] not in ('primary','hedge') for m in members):
        raise ValueError('coordinated_marked_primary_required')
    root=by_id[primary[0]['trade_id']]
    group=root['identity']['coordinated']
    capital=group.get('allocated_equity_usdt')
    if type(capital) not in (int,float) or not math.isfinite(capital) or capital<=0:
        raise ValueError('coordinated_marked_allocated_equity_missing')
    policy=group.get('management_policy')
    M.validate(policy)
    maintenance=group.get('maintenance_fraction')
    if type(maintenance) not in (int,float) or not math.isfinite(maintenance) or not 0<maintenance<1:
        raise ValueError('coordinated_marked_maintenance_missing')
    first_confirmed=root['capture']['bookings']['receipts'][0]['observed_ms']
    exact=None;instruments=set();cashflows=set();net=0.;gross=0.;marks=[]
    for member in members:
        part=by_id[member['trade_id']];capture=part['capture'];identity=part['identity']
        current=entry_binding(part,member,source['group_id'])
        if not all(current) or identity.get('status')!='VERIFIED' or (exact and exact!=current):
            raise ValueError('coordinated_marked_version_or_account_differs')
        exact=current
        actual=identity['coordinated']
        if any(actual.get(k)!=group.get(k) for k in ('allocated_equity_usdt','management_policy','maintenance_fraction')):
            raise ValueError('coordinated_marked_policy_differs')
        cashflow=(capture['venue'],capture['environment'],capture['venue_symbol'])
        if member['instrument_id'] in instruments or cashflow in cashflows:
            raise ValueError('coordinated_marked_shared_instrument_unsupported')
        instruments.add(member['instrument_id']);cashflows.add(cashflow)
        result=T.replay(capture)
        if result['status']!='marked_as_of_venue_history':
            raise ValueError('coordinated_marked_component_incomplete')
        if (type(now_ms) is not int or not capture['captured_ms']<=now_ms
                or now_ms-result['mark_observed_ms']>30_000):
            raise ValueError('coordinated_marked_component_stale')
        if member['leg']=='hedge' and capture['start_ms']<first_confirmed:
            raise ValueError('coordinated_marked_primary_not_first')
        if member['leg']=='hedge' and capture['bookings']['trade']['side']==root['capture']['bookings']['trade']['side']:
            raise ValueError('coordinated_marked_protection_direction_differs')
        net+=result['marked_net_pnl_usdt'];gross+=result['marked_notional_usdt']
        marks.append(result['mark_observed_ms'])
    equity=capital+net
    decision=M.assess(policy,entry_equity=capital,equity=equity,gross=gross,maintenance_fraction=maintenance)
    if root['capture']['bookings']['trade']['status']=='closed' and any(
            p['capture']['bookings']['trade']['status']=='open' for p in parts):
        decision['exit_reason']='primary_closed'
    return dict(group_id=source['group_id'],marked_equity_usdt=equity,marked_net_pnl_usdt=net,
        allocated_equity_usdt=capital,gross_notional_usdt=gross,decision=decision,
        hedge_present=any(m['leg']=='hedge' and by_id[m['trade_id']]['capture']['bookings']['trade']['status']=='open'
                          for m in members),mark_observed_ms=min(marks),
        basis='OWNED_COMBINED_MARKS_AND_OBSERVED_FEES_AND_SIGNED_FUNDING',
        learning_eligible=False,final_realized_outcome=False)
