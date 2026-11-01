"""Primary-first protective management of ONE combined economic position.

Thresholds belong to the frozen strategy, never a fixed research default.
Equity supplied here is the isolated combined book after both legs' costs and
funding, not unrelated account profit. Margin triggers model pressure; they
are not proof that an exchange liquidation can always be prevented.
"""
import math
from ..closure import ClosureRefused

VERSION='primary-first-combined-position.v1'
CURRENT_VERSION='primary-first-combined-position.v2'
PAIR_VERSION='joint-pair-combined-position.v1'
PROTECTIVE=('protective_beta','protective_directional','stress_loss')


def is_pair(policy):
    return isinstance(policy,dict) and policy.get('version')==PAIR_VERSION


def validate(policy):
    pair=is_pair(policy)
    if pair:
        if policy.get('entry')!='JOINT' or 'activate' in policy:
            raise ClosureRefused('pair_joint_entry_policy_required')
    elif not isinstance(policy,dict) or policy.get('version') not in (VERSION,CURRENT_VERSION) or policy.get('entry')!='PRIMARY_FIRST':
        raise ClosureRefused('hedge_primary_first_policy_required')
    if policy.get('pnl_basis')!='COMBINED_NET_EQUITY':
        raise ClosureRefused('hedge_combined_net_pnl_required')
    trigger=policy.get('activate') or {}
    if not pair and trigger.get('kind') not in ('combined_loss_pct','maintenance_buffer_multiple'):
        raise ClosureRefused('hedge_management_trigger_required')
    thresholds=[('stop_loss_pct',policy.get('stop_loss_pct')),('take_profit_pct',policy.get('take_profit_pct'))]
    if not pair:thresholds.append(('activate',trigger.get('value')))
    for name,value in thresholds:
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value<=0:
            raise ClosureRefused('hedge_management_threshold_invalid',name)
    if not pair and trigger['kind']=='maintenance_buffer_multiple' and trigger['value']<=1:
        raise ClosureRefused('hedge_management_must_trigger_before_model_liquidation')
    if not pair and trigger['kind']=='combined_loss_pct' and trigger['value']>=policy['stop_loss_pct']:
        raise ClosureRefused('hedge_management_trigger_after_combined_stop')
    if policy['version'] in (CURRENT_VERSION,PAIR_VERSION):
        hedge_exit_spec(policy)
    if pair and policy['hedge_stop']['on_hit']!='close_group':
        raise ClosureRefused('pair_leg_stop_must_close_group')
    return policy


def hedge_exit_spec(policy):
    """Strategy-declared hedge protection; no invented numerical stop."""
    from ...strategy.spec import ExitSpec
    from ...strategy import exit_policy as E
    if not isinstance(policy,dict) or policy.get('version') not in (CURRENT_VERSION,PAIR_VERSION):
        raise ClosureRefused('hedge_strategy_stop_not_declared')
    value=policy.get('hedge_stop')
    if not isinstance(value,dict) or set(value)!={'stop','trail','on_hit'} or value['on_hit'] not in ('close_group','close_hedge'):
        raise ClosureRefused('hedge_strategy_stop_not_declared')
    ex=ExitSpec(stop=value['stop'],target={'kind':'none'},trail=value['trail'],
                time={'max_bars':E.MAX_HOLD_BARS},signal_exit='')
    bad=E.unsupported(ex)
    if bad or ex.stop['kind']=='pct' and ex.stop['v']>=1:
        raise ClosureRefused('hedge_strategy_stop_unsupported',bad or 'nonpositive_long_stop')
    return ex


def require_current(spec):
    if spec['purpose'] in PROTECTIVE:
        validate(spec.get('management'))
    return spec


def execution_policy(spec):
    """Pair entry and a conditional protective hedge are different contracts."""
    policy=validate(spec.get('management'))
    if is_pair(policy)!=(spec['purpose']=='relative_value'):
        raise ClosureRefused('position_purpose_entry_policy_mismatch')
    hedge_exit_spec(policy)
    return policy


def assess(policy,*,entry_equity,equity,gross,maintenance_fraction):
    validate(policy)
    if any(isinstance(x,bool) or not isinstance(x,(int,float)) or not math.isfinite(x)
           for x in (entry_equity,equity,gross,maintenance_fraction)) or entry_equity<=0 or gross<0:
        raise ClosureRefused('hedge_combined_position_valuation_unavailable')
    net_pct=(equity-entry_equity)/entry_equity*100.
    trigger=policy.get('activate') or {}
    activate=(True if is_pair(policy) else
              net_pct<=-trigger['value'] if trigger['kind']=='combined_loss_pct' else
              gross>0 and equity<=maintenance_fraction*gross*trigger['value'])
    exit_reason=('combined_stop' if net_pct<=-policy['stop_loss_pct'] else
                 'combined_target' if net_pct>=policy['take_profit_pct'] else None)
    return dict(net_return_pct=net_pct,activate=bool(activate),exit_reason=exit_reason,
                basis='COMBINED_NET_EQUITY',liquidation_prevention_guaranteed=False)
