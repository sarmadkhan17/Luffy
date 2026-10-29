"""Current combined equity from the same owned receipts as final accounting.

No account-wide profit, rate estimates, orders, or final learning outcomes.
This is the valuation/decision input for the existing position-management clock.
"""
import math
from . import trade_accounting as T
from .coordinated_accounting import entry_binding,claim_interval
from ..research.compose import managed_hedge as M

VERSION='coordinated-marked-equity.v1'
MAX_MEMBERS=32


def assess(source, *, now_ms):
    if source.get('schema_version')!=VERSION:
        raise ValueError('coordinated_marked_schema')
    members,parts=source['members'],source['components']
    if not 1<=len(parts)<=MAX_MEMBERS or len(members)!=len(parts):
        raise ValueError('coordinated_marked_members_incomplete')
    by_id={p['capture']['bookings']['trade']['id']:p for p in parts}
    if len(by_id)!=len(parts) or set(by_id)!={m['trade_id'] for m in members}:
        raise ValueError('coordinated_marked_members_differ')
    primary=[m for m in members if m['leg']=='primary']
    if len(primary)!=1 or any(m['leg'] not in ('primary','hedge') for m in members):
        raise ValueError('coordinated_marked_primary_required')
    if sum(m['leg']=='hedge' and by_id[m['trade_id']]['capture']['bookings']['trade']['status']=='open' for m in members)>1:
        raise ValueError('coordinated_marked_multiple_open_hedges')
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
    exact=None;intervals=[];net=0.;gross=0.;marks=[]
    for member in members:
        part=by_id[member['trade_id']];capture=part['capture'];identity=part['identity']
        current=entry_binding(part,member,source['group_id'])
        if not all(current) or identity.get('status')!='VERIFIED' or (exact and exact!=current):
            raise ValueError('coordinated_marked_version_or_account_differs')
        exact=current
        actual=identity['coordinated']
        if any(actual.get(k)!=group.get(k) for k in ('allocated_equity_usdt','management_policy','maintenance_fraction')):
            raise ValueError('coordinated_marked_policy_differs')
        result=T.replay(capture)
        closed=(capture['bookings']['trade']['status']=='closed' and capture['schema_version']==T.INTERVAL_VERSION
                and result['status']=='complete_as_of_venue_history')
        claim_interval(intervals,member,capture,'coordinated_marked_shared_instrument_unsupported')
        if not closed and result['status']!='marked_as_of_venue_history':
            raise ValueError('coordinated_marked_component_incomplete')
        observed=capture['captured_ms'] if closed else result['mark_observed_ms']
        if (type(now_ms) is not int or not capture['captured_ms']<=now_ms
                or (not closed and now_ms-observed>30_000)):
            raise ValueError('coordinated_marked_component_stale')
        if member['leg']=='hedge' and capture['start_ms']<first_confirmed:
            raise ValueError('coordinated_marked_primary_not_first')
        if member['leg']=='hedge' and capture['bookings']['trade']['side']==root['capture']['bookings']['trade']['side']:
            raise ValueError('coordinated_marked_protection_direction_differs')
        net+=result['net_trade_pnl_usdt'] if closed else result['marked_net_pnl_usdt']
        gross+=0. if closed else result['marked_notional_usdt']
        if not closed:marks.append(observed)
    equity=capital+net
    decision=M.assess(policy,entry_equity=capital,equity=equity,gross=gross,maintenance_fraction=maintenance)
    if root['capture']['bookings']['trade']['status']=='closed' and any(
            p['capture']['bookings']['trade']['status']=='open' for p in parts):
        decision['exit_reason']='primary_closed'
    return dict(group_id=source['group_id'],marked_equity_usdt=equity,marked_net_pnl_usdt=net,
        allocated_equity_usdt=capital,gross_notional_usdt=gross,decision=decision,
        hedge_present=any(m['leg']=='hedge' and by_id[m['trade_id']]['capture']['bookings']['trade']['status']=='open'
                          for m in members),mark_observed_ms=min(marks) if marks else max(p['capture']['captured_ms'] for p in parts),
        basis='OWNED_COMBINED_MARKS_AND_OBSERVED_FEES_AND_SIGNED_FUNDING',
        learning_eligible=False,final_realized_outcome=False)
