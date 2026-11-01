"""§3A — hedging as a research capability: declaration, state machine, replay,
stress and comparison. One `HedgeBook` decides every instruction; the replay
driver and the incremental `RuntimeAdapter` both call it, so replay and the
instruction stream a runtime would receive cannot diverge (tested bar-by-bar).

Scope and limits (recorded, not hidden)
  * Instructions are INTENTS. The coordinated caller supports distinct linear
    USDT-M instruments in netting mode with an explicit management/stop policy.
    Same-instrument hedge-mode and COIN-M execution are not wired. Code support
    does not establish qualification, deployment, account state or authority.
  * Accounting is explicit per contract kind (linear USDT-M, inverse COIN-M
    with coin collateral) and per account mode (hedge vs netting). In netting
    mode an opposite order on the SAME symbol closes exposure, so a same-symbol
    "hedge" is refused (`NETTING_COLLAPSES_HEDGE`).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..closure import ClosureRefused, digest
from ...strategy.exit_policy import EXIT_SEMANTICS_ID as PRIMARY_EXIT_RULE
from . import capital as K

SCHEMA = 'hedge-spec.v1'
MAX_QUOTE_INCREASE_AGE_MS=60_000
BOOK_EXIT_CONTRACT=dict(version='coordinated-book-exits.v5',primary_rule=PRIMARY_EXIT_RULE,capital_policy=K.POLICY_ID,
    rule='primary first; main stop/target/trail/time/signal OR combined net-PnL exit closes the whole group',
    hedge_rule='v2 strategy freezes own hedge stop, trail and close_group/close_hedge response before testing; no numerical defaults',
    fill='pending instructions at causal next-bar open or subsequent received bid/ask under declared costs; actual execution uses venue receipts',
    controls='unhedged/reduced use the identical exit decision procedure',
    missing='unknown declared exit evidence requests unwind and makes economics incomplete')
BOOK_EXIT_SEMANTICS_ID=BOOK_EXIT_CONTRACT['version']+':'+digest(BOOK_EXIT_CONTRACT)
PURPOSES = ('protective_beta', 'protective_directional', 'stress_loss', 'relative_value')
RUNTIME_SUPPORT = dict(
    account_modes_in_executor=['netting(positionSide=BOTH)'], contracts_in_executor=['linear USDT-M'],
    multi_leg_orders=True, coin_m=False, hedge_mode=False, same_instrument=False,
    owned_delta_reductions=True, owned_delta_increases=True, whole_group_unwind=True,
    note='coordinated primary/conditional hedge entry, owned deltas, combined venue valuation and group close exist for distinct linear USDT instruments in netting mode; qualification and current owner/Risk authority remain separate')


def execution_support_block(spec: dict) -> str | None:
    """The exact code capability shared by research reporting and the caller.

    This predicate checks no venue or owner state and grants no authority.
    """
    if (spec.get('account_mode') != 'netting'
            or spec['primary']['symbol'] == spec['hedge']['symbol']
            or any(spec[leg].get('contract', 'linear') != 'linear' for leg in ('primary', 'hedge'))):
        return 'coordinated_account_or_contract_unsupported'
    from . import managed_hedge as M
    try:
        M.execution_policy(spec)
    except (ValueError, TypeError, KeyError):
        return 'coordinated_strategy_stop_unavailable'
    return None


def deployment_contract(spec: dict, verified: dict | None = None) -> dict:
    """Report code capability, not qualification or current deployment readiness.

    A supplied verification record may narrow the installed capability, but
    cannot claim support for an account/contract or stop policy the caller
    does not implement. No venue, control or account setting is changed.
    """
    v = dict(RUNTIME_SUPPORT, **(verified or {}))
    reasons = []
    if not v.get('multi_leg_orders'):
        reasons.append('multi_leg_orders_not_wired: supplied runtime record has no coordinated caller')
    mode = 'netting(positionSide=BOTH)' if spec['account_mode'] == 'netting' else 'hedge(positionSide=LONG/SHORT)'
    if mode not in v['account_modes_in_executor']:
        reasons.append(f'account_mode_unverified: {mode}')
    for leg in ('primary', 'hedge'):
        kind = 'linear USDT-M' if spec[leg].get('contract', 'linear') == 'linear' else 'inverse COIN-M'
        if kind not in v['contracts_in_executor']:
            reasons.append(f'contract_unverified: {leg} leg is {kind}')
    blocked = execution_support_block(spec)
    if blocked:
        reasons.append(blocked)
    return dict(status='DEPLOYABLE' if not reasons else 'REFUSED_UNSUPPORTED_VENUE_MODE', reasons=reasons,
                replay_only=bool(reasons), spec_id=spec.get('spec_id'),
                execution_authority='NONE', readiness='NOT_ASSESSED',
                claim='no live multi-leg support for this declared contract' if reasons else
                      'supported coordinated code path only; qualification, installation, owner, venue and Risk checks still required')


class HedgeRefused(ClosureRefused):
    pass


# Public import retained for frozen callers and historical replay code.
from ...core.contract_values import Contract  # noqa: E402,F401


def capital_enabled(spec):
    from .managed_hedge import CURRENT_VERSION,PAIR_VERSION
    return (spec.get('management') or {}).get('version') in (CURRENT_VERSION,PAIR_VERSION)


def pair_increase_quantity(spec,positions,prices,leg,quantity,ratio):
    """A partial primary fill cannot open an oversized second pair leg."""
    from .managed_hedge import is_pair
    if not is_pair(spec.get('management')) or leg!='hedge':return quantity
    if ratio is None or not np.isfinite(ratio) or ratio<0:return 0.
    if any(not np.isfinite(prices.get(k,np.nan)) or prices[k]<=0 for k in ('primary','hedge')):return 0.
    pc=Contract(spec['primary']['contract'],spec['primary']['multiplier'])
    hc=Contract(spec['hedge']['contract'],spec['hedge']['multiplier'])
    target=pc.notional(positions['primary'],prices['primary'])*ratio
    have=hc.notional(positions['hedge'],prices['hedge'])
    return min(quantity,hc.qty_for(max(0.,target-have),prices['hedge']))


def affordable_fill(spec,positions,prices,equity,leg,side,quantity,cost_rate,*,ratio=None):
    """Cap a simulated increase at its fill price; old v1 replay is retained."""
    if not capital_enabled(spec) or positions[leg]*side<0:return quantity
    quantity=pair_increase_quantity(spec,positions,prices,leg,quantity,ratio)
    contracts={k:Contract(spec[k]['contract'],spec[k]['multiplier']) for k in ('primary','hedge')}
    gross=0.
    for k,c in contracts.items():
        if positions[k]:
            if not np.isfinite(prices[k]) or prices[k]<=0:return 0.
            gross+=c.notional(positions[k],prices[k])
    if not np.isfinite(prices[leg]) or prices[leg]<=0:return 0.
    cap=K.capacity(equity=float(equity),gross=float(gross),leverage=spec['leverage'],
        unit_notional=contracts[leg].notional(1.,prices[leg]),requested=float(quantity),cost_rate=cost_rate)
    allowed=cap['quantity']
    return float(np.floor(allowed)) if contracts[leg].kind=='inverse' else allowed


def validate(spec: dict) -> dict:
    if spec.get('schema') != SCHEMA:
        raise HedgeRefused('hedge_schema')
    if spec['purpose'] not in PURPOSES:
        raise HedgeRefused('hedge_purpose', str(spec['purpose']))
    def number(value, name, *, positive=False):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not np.isfinite(value) or value < 0 or (positive and value == 0):
            raise HedgeRefused('hedge_numeric_invalid', name)
    for key in ('fee_bps', 'slip_bps', 'maint_margin'):
        number(spec[key], key)
    number(spec['leverage'], 'leverage', positive=True)
    number(spec['sizing']['primary_notional_pct'], 'primary_notional_pct')  # zero is the valid reduced-to-cash comparator
    constraint=spec.get('primary_exposure_constraint')
    if constraint is not None:
        if not isinstance(constraint,dict) or set(constraint)!={'min_net_exposure'}:
            raise HedgeRefused('hedge_exposure_constraint_invalid')
        minimum=constraint['min_net_exposure']
        number(minimum,'min_net_exposure',positive=True)
        if minimum>1:
            raise HedgeRefused('hedge_exposure_constraint_invalid')
    if spec['maint_margin'] >= 1:
        raise HedgeRefused('hedge_numeric_invalid', 'maint_margin')
    for leg in ('primary', 'hedge'):
        L = spec[leg]
        if not L.get('symbol') or L.get('contract', 'linear') not in ('linear', 'inverse'):
            raise HedgeRefused('hedge_leg', leg)
        if L.get('contract') == 'inverse' and not L.get('multiplier'):
            raise HedgeRefused('inverse_needs_multiplier', leg)
        number(L.get('multiplier', 1.0), leg + '.multiplier', positive=True)
    if spec['account_mode'] not in ('hedge', 'netting'):
        raise HedgeRefused('account_mode')
    if spec['account_mode'] == 'netting' and spec['primary']['symbol'] == spec['hedge']['symbol']:
        raise HedgeRefused('NETTING_COLLAPSES_HEDGE', 'opposite order on one symbol closes exposure in a netting account')
    r = spec['ratio']
    if r['method'] not in ('fixed', 'ols_beta', 'rolling_beta'):
        raise HedgeRefused('ratio_method')
    number(r.get('value', 1.0), 'ratio.value')
    number(r.get('max_ratio', 2.0), 'ratio.max_ratio')
    if type(r.get('lookback', 96)) is not int or not 1 <= r.get('lookback', 96) <= 6000:
        raise HedgeRefused('hedge_numeric_invalid', 'ratio.lookback')
    if spec['rebalance'].get('trigger') != 'tolerance':
        raise HedgeRefused('hedge_rebalance_trigger_unsupported')
    for k in ('tolerance', 'min_interval_bars', 'max_hold_bars', 'leg_timeout_bars', 'missing_data_bars'):
        if spec['rebalance'].get(k, spec['unwind'].get(k)) is None:
            raise HedgeRefused('hedge_missing_field', k)
        value = spec['rebalance'].get(k, spec['unwind'].get(k))
        number(value, k)
        if k != 'tolerance' and (type(value) is not int or value < (0 if k == 'min_interval_bars' else 1)):
            raise HedgeRefused('hedge_numeric_invalid', k)
    corr = spec['unwind'].get('min_corr')
    if corr is not None and (isinstance(corr, bool) or not isinstance(corr, (int, float)) or not np.isfinite(corr) or not -1 <= corr <= 1):
        raise HedgeRefused('hedge_numeric_invalid', 'min_corr')
    allowed = ('net_return_pct',) if spec['purpose'] == 'relative_value' else ('max_drawdown_pct', 'cvar5_pct', 'worst_bar_pct', 'stress_loss_pct')
    if spec['objective']['metric'] not in allowed:
        raise HedgeRefused('risk_objective')
    if spec.get('management') is not None:
        from .managed_hedge import validate as validate_management, is_pair
        validate_management(spec['management'])
        if is_pair(spec['management']) and spec['purpose']!='relative_value':
            raise HedgeRefused('position_purpose_entry_policy_mismatch')
        if is_pair(spec['management']) and spec['primary']['symbol']==spec['hedge']['symbol']:
            raise HedgeRefused('pair_distinct_instruments_required')
    return spec


def make_spec(**kw) -> dict:
    base = dict(schema=SCHEMA, account_mode='hedge', leverage=3.0, maint_margin=0.01, fee_bps=4.0, slip_bps=2.0,
                sizing=dict(primary_notional_pct=100.0),
                ratio=dict(method='ols_beta', value=1.0, lookback=96, max_ratio=2.0),
                rebalance=dict(trigger='tolerance', tolerance=0.1, min_interval_bars=6),
                unwind=dict(max_hold_bars=48, min_corr=None, leg_timeout_bars=3, missing_data_bars=3),
                objective=dict(metric='max_drawdown_pct'), reduced='match_net_exposure',
                comparators=['unhedged', 'reduced', 'cash'])
    for k, v in kw.items():
        base[k] = dict(base.get(k, {}), **v) if isinstance(v, dict) and isinstance(base.get(k), dict) else v
    if base.get('purpose') == 'relative_value' and 'objective' not in kw:
        base['objective'] = dict(metric='net_return_pct')
    for leg in ('primary', 'hedge'):
        base[leg] = dict(dict(contract='linear', multiplier=None), **base[leg])
        if base[leg]['contract'] == 'linear' and base[leg]['multiplier'] is None:
            base[leg]['multiplier'] = 1.0
    validate(base)
    base['spec_id'] = digest({k: v for k, v in base.items() if k != 'spec_id'})
    return base


def estimate_ratio(spec: dict, prim_ret: np.ndarray, hedge_ret: np.ndarray, t: int) -> float:
    """Hedge ratio at bar t from returns up to t INCLUSIVE of closed bars only.
    ols_beta uses the declared estimation prefix (`fit_end`), rolling_beta the trailing lookback."""
    r = spec['ratio']
    if r['method'] == 'fixed':
        return float(r['value'])
    if r['method'] == 'ols_beta':
        hi = min(t + 1, int(r.get('fit_end', t + 1)))
        lo = 0
    else:
        hi, lo = t + 1, max(0, t + 1 - int(r['lookback']))
    a, b = prim_ret[lo:hi], hedge_ret[lo:hi]
    ok = np.isfinite(a) & np.isfinite(b)
    if ok.sum() < 20 or b[ok].var() == 0:
        return float('nan')              # UNKNOWN, never a fabricated zero hedge: callers refuse entry / keep the held ratio
    beta = float(np.cov(a[ok], b[ok])[0, 1] / b[ok].var())
    return float(np.clip(beta, 0.0, r['max_ratio']))


@dataclass
class Instruction:
    kind: str                # ENTER | REBALANCE | REDUCE_PRIMARY | UNWIND | ALERT
    leg: str                 # primary | hedge | both
    side: int                # +1 buy / -1 sell
    qty: float
    reason: str
    position_side: str       # BOTH (netting) | LONG | SHORT (hedge mode)
    bar: int
    oid: str = ''


@dataclass
class HedgeBook:
    spec: dict
    state: str = 'FLAT'
    pos: dict = field(default_factory=lambda: dict(primary=0.0, hedge=0.0))     # signed contracts/units
    pending: dict = field(default_factory=dict)                                  # oid -> (leg, side, qty, bar)
    entry_bar: int = -1
    last_rebalance: int = -10**9
    direction: int = 0
    leg_wait: int = 0
    missing: int = 0
    seq: int = 0
    log: list = field(default_factory=list)
    last_ratio: float | None = None      # the last KNOWN ratio; an unknown one never becomes zero
    ratio_unknown: int = 0
    primary_exit_definition: dict | None = None
    primary_exit_state: str | None = None
    exit_input_gaps: int = 0
    primary_quote_range: dict | None = None
    hedge_exit_state: str | None = None
    hedge_quote_range: dict | None = None
    management_entry_equity: float | None = None
    management_activated: bool = False
    last_clock_bar: int = -1

    def _pside(self, leg_side):
        return 'BOTH' if self.spec['account_mode'] == 'netting' else ('LONG' if leg_side > 0 else 'SHORT')

    def _emit(self, out, kind, leg, side, qty, reason, bar):
        if not np.isfinite(qty):
            raise HedgeRefused('hedge_instruction_quantity_nonfinite')
        if qty <= 0 and kind != 'ALERT':
            return
        self.seq += 1
        position_direction = self.direction if leg == 'primary' else -self.direction
        ins = Instruction(kind, leg, side, qty, reason, self._pside(position_direction), bar, f'{kind[:3]}{self.seq}')
        if kind != 'ALERT':
            self.pending[ins.oid] = (leg, side, qty, bar)
        self.log.append(ins)
        out.append(ins)

    def apply_fills(self, fills: dict):
        """fills: oid -> filled quantity (<= ordered). Unfilled remainder is dropped (IOC semantics)."""
        for oid, filled in fills.items():
            if oid not in self.pending:
                raise HedgeRefused('hedge_fill_has_no_pending_instruction')
            qty = self.pending[oid][2]
            if not isinstance(filled, (int, float, np.number)) or not np.isfinite(filled) or not 0 <= float(filled) <= qty:
                raise HedgeRefused('hedge_fill_outside_authorized_quantity')
        for oid, filled in fills.items():
            leg, side, qty, _ = self.pending.pop(oid)
            self.pos[leg] += side * float(filled)
        for oid in [o for o in self.pending if o not in fills]:
            self.pending.pop(oid)

    def _primary_exit(self,bar):
        if self.primary_exit_definition is None:return bar
        from ...strategy import exit_policy as E
        from ...strategy.spec import ExitSpec
        bar=dict(bar)
        observation=bar.get('primary_exit_observation')
        try:
            if self.state=='FLAT':
                if not bar['signal'] or bar.get('exit'):return bar
                if observation is None:raise ValueError('primary_exit_observation_missing')
                ex=ExitSpec(**self.primary_exit_definition)
                policy,state=E.initialize(ex,float(observation['close']),float(observation['atr']),
                    'long' if bar['signal']>0 else 'short',int(observation['bar_ms']),int(observation['step_ms']))
                self.primary_exit_state=E.encode(policy,state)
                self.primary_quote_range=None
            elif self.primary_exit_state and abs(self.pos['primary'])>0:
                if observation is None:raise ValueError('primary_exit_observation_missing')
                policy,state=E.decode(self.primary_exit_state)
                quote_mode=bool(bar.get('paper_quote'))
                # Completed candle ranges may predate the actual quote fill.
                # Use only post-fill sampled prices for prospective paper.
                prices=self.primary_quote_range if quote_mode else observation
                if prices is None:
                    prices={key:float(bar['p']) for key in ('open','high','low','close')}
                transition=E.advance(policy,state,E.Observation(bar_ms=int(observation['bar_ms']),
                    open=float(prices['open']),high=float(prices['high']),low=float(prices['low']),
                    close=float(prices['close']),atr=observation.get('atr'),signal_exit=bool(bar.get('exit'))),
                    abs(self.pos['primary']))
                if quote_mode:
                    if transition.state.last_bar_ms>state.last_bar_ms:
                        self.primary_quote_range=None
                    state=transition.state
                    price=float(bar['p'])
                    if not np.isfinite(price) or price<=0:raise ValueError('primary_quote_unavailable')
                    stop=state.active_stop if state.active_stop is not None else policy.stop
                    long=policy.side=='long'
                    hit=(price<=stop if long else price>=stop) or bool(policy.target) and (price>=policy.target if long else price<=policy.target)
                    if hit and not transition.due:
                        transition=E.execute_open(policy,state,state.last_bar_ms+policy.step_ms,price,abs(self.pos['primary']))
                    previous=self.primary_quote_range
                    self.primary_quote_range=dict(open=previous['open'] if previous else price,
                        high=max(previous['high'],price) if previous else price,
                        low=min(previous['low'],price) if previous else price,close=price)
                self.primary_exit_state=E.encode(policy,transition.state)
                bar['exit']=bool(bar.get('exit') or transition.due)
        except (ValueError,TypeError,KeyError):
            self.exit_input_gaps+=1
            bar.update(signal=0,exit=True)
        return bar

    def _hedge_stop(self,bar,*,prepare=False):
        """Use the same pure exit transitions with this leg's own frozen rule."""
        from dataclasses import asdict,replace
        from . import managed_hedge as M
        policy=self.spec.get('management') or {}
        if policy.get('version') not in (M.CURRENT_VERSION,M.PAIR_VERSION):return False
        ex=M.hedge_exit_spec(policy)
        proxy=dict(bar,p=bar['h'],signal=-self.direction if prepare else 0,exit=False,
            primary_exit_observation=(bar.get('primary_exit_observation') or {}).get('hedge'))
        shadow=replace(self,state='FLAT' if prepare else 'HEDGED',pos=dict(primary=self.pos['hedge']),
            primary_exit_definition=asdict(ex),primary_exit_state=None if prepare else self.hedge_exit_state,
            primary_quote_range=None if prepare else self.hedge_quote_range,exit_input_gaps=0)
        result=shadow._primary_exit(proxy)
        self.hedge_exit_state=shadow.primary_exit_state;self.hedge_quote_range=shadow.primary_quote_range
        self.exit_input_gaps+=shadow.exit_input_gaps
        return bool(result.get('exit'))


    def step(self, t: int, bar: dict) -> list:
        """bar: p (primary px), h (hedge px, NaN = unavailable), signal (+1/-1/0), exit (bool),
        ratio (current hedge ratio >=0), corr (rolling corr or None), prim_c / hedge_c (Contract)."""
        if type(t) is not int or t<0 or t<self.last_clock_bar:
            raise HedgeRefused('hedge_native_clock_invalid')
        new_bar=t>self.last_clock_bar
        self.last_clock_bar=t
        out, sp = [], self.spec
        managed=sp.get('management') is not None
        from .managed_hedge import is_pair
        joint_pair=is_pair(sp.get('management'))
        conditional=managed and not joint_pair
        if managed and self.state=='FLAT':
            self.management_entry_equity=None
            self.management_activated=False
        # Main strategy exits and combined risk exits both close the group.
        bar=self._primary_exit(bar)
        combined_exit=None
        if managed and self.management_entry_equity is not None:
            from .managed_hedge import assess
            gross=sum(bar[key].notional(self.pos[leg],bar[price]) if self.pos[leg] else 0.
                      for leg,key,price in (('primary','prim_c','p'),('hedge','hedge_c','h')))
            if np.isfinite(gross):
                result=assess(sp['management'],entry_equity=self.management_entry_equity,
                    equity=bar['equity'],gross=gross,maintenance_fraction=sp['maint_margin'])
                combined_exit=result['exit_reason']
                if any(self.pos.values()):self.management_activated |= result['activate']
                bar['exit']=bool(bar.get('exit') or combined_exit)
        if bar['signal'] not in (-1, 0, 1):
            raise HedgeRefused('hedge_signal_invalid')
        if not np.isfinite(bar['ratio']):
            # UNKNOWN ratio (too few paired observations): no new entry; a held book keeps its last known ratio. One rule for the replay
            # and the runtime adapter, because both go through this method.
            self.ratio_unknown += int(new_bar)
            bar = dict(bar, ratio=self.last_ratio if self.last_ratio is not None else 0.0,
                       signal=0 if self.state == 'FLAT' else bar['signal'])
        elif bar['ratio'] >= 0:
            self.last_ratio = float(bar['ratio'])
        if not np.isfinite(bar['equity']) or bar['equity'] <= 0 or not np.isfinite(bar['ratio']) or bar['ratio'] < 0:
            raise HedgeRefused('hedge_equity_or_ratio_invalid')
        pc, hc = bar['prim_c'], bar['hedge_c']
        ok = np.isfinite(bar['p']) and np.isfinite(bar['h']) and bar['p'] > 0 and bar['h'] > 0
        self.missing = 0 if ok else self.missing + int(new_bar or self.missing==0)
        if self.state == 'FLAT':
            if bar['signal'] != 0 and not bar.get('exit') and ok and not self.pending:
                self.direction = int(bar['signal'])
                notional = bar['equity'] * sp['sizing']['primary_notional_pct'] / 100.0
                pq = pc.qty_for(notional, bar['p'])
                hq = hc.qty_for(notional * bar['ratio'], bar['h'])
                if joint_pair and (hq<=0 or self._hedge_stop(bar,prepare=True)):
                    return out
                self._emit(out, 'ENTER', 'primary', self.direction, pq, 'signal', t)
                if not conditional:
                    self._emit(out, 'ENTER', 'hedge', -self.direction, hq, 'joint_entry', t)
                if managed:
                    self.management_entry_equity=float(bar['equity'])
                    self.management_activated=joint_pair
                self.state, self.entry_bar, self.last_rebalance, self.leg_wait = 'OPENING', t, t, 0
                if conditional:self.last_rebalance=-10**9
            return out
        held = abs(self.pos['primary']) > 0 or abs(self.pos['hedge']) > 0
        if self.pending:
            return out                                    # one working order set at a time
        if self.state == 'OPENING':
            self.state = 'PRIMARY_OPEN' if conditional else 'HEDGED'
        un = sp['unwind']
        if self.state == 'UNWINDING':
            if not held:
                self.state = 'FLAT'
            else:                                         # a partial unwind fill leaves a remainder: keep closing
                for leg in ('primary', 'hedge'):
                    q = self.pos[leg]
                    self._emit(out, 'UNWIND', leg, -int(np.sign(q)), abs(q), 'unwind_remainder', t)
            return out
        reason = None
        gaps_before=self.exit_input_gaps
        if not bar.get('exit') and self.pos['hedge'] and self._hedge_stop(bar):
            unknown=self.exit_input_gaps>gaps_before
            if not unknown and sp['management']['hedge_stop']['on_hit']=='close_hedge':
                self._emit(out,'UNWIND','hedge',-int(np.sign(self.pos['hedge'])),abs(self.pos['hedge']),'strategy_hedge_stop',t)
                self.state='PRIMARY_OPEN';self.management_activated=False;self.last_rebalance=t
                return out
            bar=dict(bar,exit=True);combined_exit='hedge_stop_evidence_unavailable' if unknown else 'strategy_hedge_stop'
        if bar.get('exit'):
            reason = combined_exit or 'strategy_exit' if managed else 'primary_exit'
        elif t - self.entry_bar >= un['max_hold_bars']:
            reason = 'max_hold'
        elif un.get('min_corr') is not None and bar.get('corr') is not None and bar['corr'] < un['min_corr']:
            reason = 'ratio_break_corr'
        elif self.missing >= un['missing_data_bars']:
            reason = 'leg_data_unavailable'
            self._emit(out, 'ALERT', 'both', 0, 0.0, 'LEG_DATA_UNAVAILABLE', t)
        if reason:
            for leg in ('primary', 'hedge'):
                q = self.pos[leg]
                self._emit(out, 'UNWIND', leg, -int(np.sign(q)), abs(q), reason, t)
            self.state = 'FLAT' if not held else 'UNWINDING'
            return out
        if not ok:
            return out
        prim_n = pc.notional(self.pos['primary'], bar['p'])
        want = prim_n * bar['ratio'] if not conditional or self.management_activated else 0.0
        have = hc.notional(self.pos['hedge'], bar['h'])
        side = -self.direction
        if prim_n == 0:
            self._emit(out, 'UNWIND', 'hedge', -int(np.sign(self.pos['hedge'])), abs(self.pos['hedge']), 'primary_never_filled', t)
            self.state = 'UNWINDING'
            return out
        drift = abs(want - have) > sp['rebalance']['tolerance'] * prim_n
        if not drift:
            self.leg_wait = 0
        elif t - self.last_rebalance >= sp['rebalance']['min_interval_bars']:
            if want > have:
                first_management=conditional and have==0
                if first_management and self._hedge_stop(bar,prepare=True):
                    self._emit(out,'UNWIND','primary',-self.direction,abs(self.pos['primary']),'hedge_stop_evidence_unavailable',t)
                    self.state='UNWINDING'
                    return out
                self._emit(out, 'ENTER' if first_management else 'REBALANCE', 'hedge', side,
                           hc.qty_for(want - have, bar['h']),
                           'risk_management_activation' if first_management else 'ratio_drift', t)
            else:
                self._emit(out, 'REBALANCE', 'hedge', -side, hc.qty_for(have - want, bar['h']), 'ratio_drift', t)
            self.last_rebalance = t
            self.leg_wait += int(new_bar or self.leg_wait==0)
            if self.leg_wait > un['leg_timeout_bars'] and have < 0.5 * want:
                # the hedge cannot be completed: cover only what is hedged, never carry the naked remainder
                covered = have / bar['ratio'] if bar['ratio'] else 0.0
                excess = prim_n - covered
                self._emit(out, 'REDUCE_PRIMARY', 'primary', -self.direction, pc.qty_for(excess, bar['p']),
                           'hedge_leg_incomplete', t)
        return out


@dataclass
class Venue:
    """Deterministic paper venue: IOC fills at next-bar open with a declared fill fraction per leg."""
    fill_frac: dict = field(default_factory=lambda: dict(primary=1.0, hedge=1.0))
    hedge_fills_after: int = 0

    def fill(self, instructions, t):
        return {i.oid: i.qty * (self.fill_frac[i.leg] if t >= self.hedge_fills_after or i.leg != 'hedge' else 0.0)
                for i in instructions if i.kind != 'ALERT'}


def _bar(spec, t, p, h, sig, exit_, ratio, corr, equity, pc, hc):
    return dict(p=p, h=h, signal=sig, exit=exit_, ratio=ratio, corr=corr, equity=equity, prim_c=pc, hedge_c=hc)


def replay(spec: dict, p: np.ndarray, h: np.ndarray, signals: np.ndarray, exits: np.ndarray, *, equity: float = 10000.0,
           venue: Venue | None = None, funding: tuple | None = None, ratio_override: float | None = None,
           slip_mult: float = 1.0, opens: tuple | None = None, flat_funding_rate: float = 0.0,
           ratios=None, correlations=None, entry_allowed=None,primary_exit=None,decision_bars=None,
           native_bars=None,bid_ask=None,funding_time_weights=None,quote_times=None) -> dict:
    """Bar loop: fills execute at the NEXT bar's price (signal age = 1), costs per fill, funding per bar,
    mark-to-market equity every bar (not only at closes), liquidation test with maintenance margin."""
    validate(spec)
    venue = venue or Venue()
    pc, hc = (Contract(spec[k]['contract'], spec[k]['multiplier']) for k in ('primary', 'hedge'))
    book = HedgeBook(spec,primary_exit_definition=primary_exit)
    n = len(p)
    if any(a is not None and len(a)!=n for a in (ratios,correlations,entry_allowed,decision_bars,native_bars,funding_time_weights,quote_times)):
        raise HedgeRefused('hedge_recorded_decision_shape')
    if native_bars is not None:
        if (any(type(t) not in (int,np.int64,np.int32) or t<0 for t in native_bars)
                or any(b<a for a,b in zip(native_bars,native_bars[1:]))
                or ratios is None or correlations is None):
            raise HedgeRefused('hedge_native_decisions_required')
    if funding_time_weights is not None and (not np.isfinite(funding_time_weights).all() or (np.asarray(funding_time_weights)<0).any()):
        raise HedgeRefused('hedge_funding_clock_invalid')
    if quote_times is not None and (not np.issubdtype(np.asarray(quote_times).dtype,np.integer)
            or (np.asarray(quote_times)<0).any() or (np.diff(quote_times)<=0).any()):
        raise HedgeRefused('hedge_quote_clock_invalid')
    if bid_ask is not None and (set(bid_ask)!={'primary','hedge'} or any(
            len(pair)!=2 or any(len(a)!=n for a in pair) for pair in bid_ask.values())):
        raise HedgeRefused('hedge_quote_price_shape')
    if opens is not None and (len(opens) != 2 or any(len(a) != n for a in opens)):
        raise HedgeRefused('hedge_open_price_shape')
    pr = np.r_[np.nan, np.diff(p) / p[:-1]]
    hr = np.r_[np.nan, np.diff(h) / h[:-1]]
    cash, eq_curve, gross, net, instr = float(equity), np.full(n, np.nan), np.zeros(n), np.zeros(n), []
    primary_active=np.zeros(n,dtype=bool)
    retained_net_fraction=np.full(n,np.nan)
    cost = dict(fees=0.0, slippage=0.0, funding=0.0)
    last_px = dict(primary=p[0], hedge=h[0])
    liquidated, pending_ins = False, []
    observation_gaps=False
    fee, slip = spec['fee_bps'] / 1e4, spec['slip_bps'] / 1e4 * slip_mult
    for t in range(n):
        expired_increase=bool(quote_times is not None and t and quote_times[t]-quote_times[t-1]>MAX_QUOTE_INCREASE_AGE_MS)
        if expired_increase and any(book.pos.values()):observation_gaps=True
        px = dict(primary=p[t], hedge=h[t])
        fill_px = dict(primary=opens[0][t], hedge=opens[1][t]) if opens is not None else px
        if bid_ask is not None:
            fill_px=dict(px)
            for i in pending_ins:
                if i.kind!='ALERT':fill_px[i.leg]=bid_ask[i.leg][1 if i.side>0 else 0][t]
        # 1. mark open legs from the previous bar's mark to this bar's price
        for leg, c in (('primary', pc), ('hedge', hc)):
            if book.pos[leg] != 0 and np.isfinite(fill_px[leg]):
                cash += c.pnl_usd(book.pos[leg], last_px[leg], fill_px[leg])
                last_px[leg] = fill_px[leg]
            elif np.isfinite(fill_px[leg]):
                last_px[leg] = fill_px[leg]
        if funding is not None:
            for leg, c, rate in (('primary', pc, funding[0]), ('hedge', hc, funding[1])):
                if book.pos[leg] != 0 and np.isfinite(fill_px[leg]):
                    f = -np.sign(book.pos[leg]) * rate[t] * c.notional(book.pos[leg], fill_px[leg])
                    cash += f
                    cost['funding'] += -f
        elif flat_funding_rate:
            for leg, c in (('primary', pc), ('hedge', hc)):
                if book.pos[leg] != 0 and np.isfinite(fill_px[leg]):
                    charge = abs(flat_funding_rate) * c.notional(book.pos[leg], fill_px[leg])
                    cash -= charge
                    cost['funding'] += charge
        # 2. fills for orders issued on the previous bar execute at this bar's price
        if pending_ins:
            fills = venue.fill(pending_ins, t)
            if expired_increase or (entry_allowed is not None and not entry_allowed[t]):
                for i in pending_ins:
                    if i.kind!='ALERT' and (book.pos[i.leg]==0 or book.pos[i.leg]*i.side>0):
                        fills[i.oid]=0.0
            filling=dict(book.pos)
            for i in pending_ins:
                if i.kind == 'ALERT':
                    continue
                c = pc if i.leg == 'primary' else hc
                f = fills.get(i.oid, 0.0)
                if not np.isfinite(f) or not 0<=f<=i.qty:
                    raise HedgeRefused('hedge_fill_outside_authorized_quantity')
                if f > 0 and np.isfinite(fill_px[i.leg]) and fill_px[i.leg] > 0:
                    f=affordable_fill(spec,filling,fill_px,cash,i.leg,i.side,f,fee+slip,ratio=book.last_ratio)
                    fills[i.oid]=f
                    notional = c.notional(f, fill_px[i.leg])
                    cash -= notional * (fee + slip)
                    cost['fees'] += notional * fee
                    cost['slippage'] += notional * slip
                    filling[i.leg]+=i.side*f
                else:
                    fills[i.oid] = 0.0
            book.apply_fills({k: v for k, v in fills.items() if k in book.pending})
            pending_ins = []
        # Existing legs first cross the overnight gap. Newly filled (or remaining) legs then
        # earn only the open-to-close move; they cannot receive yesterday's price change.
        for leg, c in (('primary', pc), ('hedge', hc)):
            if book.pos[leg] != 0 and np.isfinite(px[leg]):
                cash += c.pnl_usd(book.pos[leg], last_px[leg], px[leg])
                last_px[leg] = px[leg]
        # 3. mark-to-market exposure, margin, liquidation
        gp = pc.notional(book.pos['primary'], px['primary']) if np.isfinite(px['primary']) else 0.0
        gh = hc.notional(book.pos['hedge'], px['hedge']) if np.isfinite(px['hedge']) else 0.0
        gross[t] = gp + gh
        net[t] = np.sign(book.pos['primary']) * gp + np.sign(book.pos['hedge']) * gh
        eq_curve[t] = cash
        if any(book.pos[k] != 0 and not np.isfinite(px[k]) for k in ('primary', 'hedge')):
            eq_curve[t] = gross[t] = net[t] = np.nan   # a held leg's missing mark is not zero exposure
        primary_active[t]=book.pos['primary']!=0
        if primary_active[t] and cash>0 and np.isfinite(net[t]):
            retained_net_fraction[t]=np.sign(book.pos['primary'])*net[t]/cash
        if gross[t] > 0 and cash < spec['maint_margin'] * gross[t] and not liquidated:
            liquidated = True
            book.pos = dict(primary=0.0, hedge=0.0)
            book.state = 'FLAT'
            cash -= 0.005 * gross[t]            # liquidation penalty (declared)
            eq_curve[t] = cash
            continue
        # 4. decisions on this bar's close
        if not liquidated and t < n - 1:
            ratio = ratio_override if ratio_override is not None else (ratios[t] if ratios is not None else estimate_ratio(spec, pr, hr, t))
            sig_t = int(signals[t]) if entry_allowed is None or entry_allowed[t] else 0
            corr = None
            lo = max(0, t - 95)
            a, b = pr[lo:t + 1], hr[lo:t + 1]
            ok = np.isfinite(a) & np.isfinite(b)
            if ok.sum() > 20 and a[ok].std() > 0 and b[ok].std() > 0:
                corr = float(np.corrcoef(a[ok], b[ok])[0, 1])
            if correlations is not None:
                corr=correlations[t]
            bar=_bar(spec,t,p[t],h[t],sig_t,bool(exits[t]),ratio,corr,cash,pc,hc)
            if decision_bars is not None:bar['primary_exit_observation']=decision_bars[t]
            if quote_times is not None:bar['paper_quote']=True
            ins = book.step(int(native_bars[t]) if native_bars is not None else t,bar)
            instr += ins
            pending_ins = ins
    if book.pos['primary'] != 0 or book.pos['hedge'] != 0:
        status = 'OPEN_AT_END'
    else:
        status = 'FLAT_AT_END'
    inverse_legs = [k for k in ('primary', 'hedge') if spec[k]['contract'] == 'inverse']
    return dict(equity=eq_curve, gross=gross, net=net, cost=cost,
                capital_policy_id=K.POLICY_ID if capital_enabled(spec) else None,
                primary_active=primary_active,retained_net_fraction=retained_net_fraction,
                native_bars=native_bars,observation_gaps=observation_gaps,
                fill_price_basis='SUBSEQUENT_BID_ASK_MODEL_ONLY' if bid_ask is not None else
                                 'NEXT_BAR_OPEN' if opens is not None else 'NEXT_OBSERVATION_PRICE_MODEL_ONLY',
                coin_collateral_exposure_usd=None if inverse_legs else 0.0, inverse_legs=inverse_legs,
                collateral_nav_basis='NOT_ESTABLISHED_FOR_INVERSE: contract payoff only' if inverse_legs else 'quote_currency',
                instructions=instr, liquidated=liquidated, ratio_unknown_bars=book.ratio_unknown,
                exit_input_gaps=book.exit_input_gaps,status=status, final_equity=float(eq_curve[-1]) if np.isfinite(eq_curve[-1]) else None, start_equity=float(equity),
                entries=sum(1 for i in instr if i.kind == 'ENTER' and i.leg == 'primary'),
                rebalances=sum(1 for i in instr if i.kind == 'REBALANCE'), book=book)


class RuntimeAdapter:
    """What a runtime would run: one bar at a time, venue fills reported back.
    Same `HedgeBook`; emits intents only (no order placement, no authority)."""

    def __init__(self, spec):
        self.book = HedgeBook(validate(spec))

    def on_bar(self, t, bar, fills=None):
        if fills:
            self.book.apply_fills({k: v for k, v in fills.items() if k in self.book.pending})
        return self.book.step(t, bar)


# ── risk metrics, stress, comparison ─────────────────────────────────────
def risk_metrics(res: dict) -> dict:
    eq = res['equity']
    unknown = int((~np.isfinite(eq)).sum())
    eq = eq[np.isfinite(eq)]
    peak = np.maximum.accumulate(eq)
    dd = (eq - peak) / np.where(peak > 0, peak, 1.0) * 100.0
    native=res.get('native_bars')
    sampled=eq
    if native is not None and not unknown:
        ids=np.asarray(native)
        last=np.r_[np.flatnonzero(ids[1:]!=ids[:-1]),len(ids)-1]
        sampled=np.r_[res['start_equity'],eq[last]]
    ret = np.diff(sampled) / np.where(sampled[:-1] > 0, sampled[:-1], 1.0) * 100.0
    k = max(1, int(np.ceil(0.05 * len(ret))))
    active=res.get('primary_active',np.zeros(len(res['equity']),dtype=bool))
    retained=res.get('retained_net_fraction',np.full(len(active),np.nan))[active]
    complete_exposure=bool(len(retained)) and bool(np.isfinite(retained).all())
    collateral_known=not bool(res.get('inverse_legs'))
    return dict(max_drawdown_pct=float(-dd.min()) if len(dd) else 0.0, worst_bar_pct=float(-ret.min()) if len(ret) else 0.0,
                cvar5_pct=float(-np.sort(ret)[:k].mean()) if len(ret) else 0.0,
                net_return_pct=float((eq[-1] / res['start_equity'] - 1) * 100) if not unknown else None, costs=res['cost'],
                mean_gross=float(np.nanmean(res['gross'])), mean_abs_net=float(np.nanmean(np.abs(res['net']))),
                primary_active_bars=(len(set(np.asarray(native)[active])) if native is not None else int(np.count_nonzero(active))),
                minimum_retained_net_fraction=float(retained.min()) if complete_exposure else None,
                retained_exposure_basis='PRIMARY_DIRECTION_NET_NOTIONAL_OVER_CURRENT_COMBINED_EQUITY',
                unknown_mark_bars=unknown,exit_input_gaps=res.get('exit_input_gaps',0),
                collateral_nav_established=collateral_known,
                risk_complete=collateral_known and unknown == 0 and not res.get('exit_input_gaps',0) and not res.get('observation_gaps',False),
                liquidated=res['liquidated'], entries=res['entries'], rebalances=res['rebalances'])


def stress_cases(spec, p, h, signals, exits, base_kwargs, *, episode_bars=None) -> dict:
    """Named perturbations of the hedge leg / venue; each returns the same risk metrics."""
    out = {}
    n = len(h)
    # correlation break: hedge leg decouples (returns reversed in the second half)
    h2 = h.copy()
    r = np.diff(h) / h[:-1]
    r[n // 2:] = -r[n // 2:]
    h2 = h[0] * np.cumprod(np.r_[1.0, 1.0 + r])
    def scenario_prices(new_h):
        kw = dict(base_kwargs)
        if kw.get('opens') is not None:
            kw['opens'] = (kw['opens'][0], np.asarray(kw['opens'][1]) * new_h / h)
        if kw.get('bid_ask') is not None:
            kw['bid_ask']=dict(kw['bid_ask'],hedge=tuple(np.asarray(a)*new_h/h for a in kw['bid_ask']['hedge']))
        elif kw.get('decision_bars') is not None and any((b or {}).get('hedge') for b in kw['decision_bars']):
            from copy import deepcopy
            import pandas as pd
            from ...strategy.exit_policy import trail_atr_series
            bars=deepcopy(kw['decision_bars']);factor=new_h/h
            changed=np.flatnonzero(~np.isclose(factor,1.,rtol=0.,atol=1e-14))
            if len(changed):
                rows=[]
                for i,bar in enumerate(bars):
                    hedge=(bar or {}).get('hedge') or {}
                    for key in ('open','high','low','close'):
                        if key in hedge:hedge[key]*=float(factor[i])
                    rows.append(dict(ts=hedge.get('bar_ms',i*(bars[0] or {}).get('step_ms',1)),
                        **{key:hedge.get(key,float('nan')) for key in ('open','high','low','close')}))
                atr=trail_atr_series(pd.DataFrame(rows),bars[0]['step_ms']).to_numpy(float)
                for i in range(int(changed[0]),len(bars)):
                    if (bars[i] or {}).get('hedge') is not None:
                        bars[i]['hedge']['atr']=float(atr[i]) if np.isfinite(atr[i]) else None
                kw['decision_bars']=bars
        return kw
    out['correlation_break'] = replay(spec, p, h2, signals, exits, **scenario_prices(h2))
    # basis divergence: hedge price drifts away by 5% over the second half
    h3 = h * np.r_[np.ones(n // 2), np.linspace(1.0, 1.05, n - n // 2)]
    out['basis_divergence'] = replay(spec, p, h3, signals, exits, **scenario_prices(h3))
    # funding spike on both legs
    spike = np.full(n, 0.0005)
    if base_kwargs.get('funding_time_weights') is not None:
        spike*=np.asarray(base_kwargs['funding_time_weights'])
    out['funding_spike'] = replay(spec, p, h, signals, exits, **dict(base_kwargs, funding=(spike, -spike)))
    # liquidity loss: slippage x10
    out['liquidity_loss'] = replay(spec, p, h, signals, exits, **dict(base_kwargs, slip_mult=10.0))
    # one-leg fill: hedge leg never fills
    out['one_leg_fill'] = replay(spec, p, h, signals, exits, **dict(base_kwargs, venue=Venue(fill_frac=dict(primary=1.0, hedge=0.0))))
    # margin pressure: leverage raised until maintenance margin binds
    out['margin_pressure'] = replay(dict(spec, maint_margin=0.5), p, h, signals, exits, **base_kwargs)
    metrics={k:dict(risk_metrics(v),status=v['status']) for k,v in out.items()}
    if episode_bars:
        from ..closure.objective_admission import episode_values
        for key,value in out.items():
            metrics[key]['objective_episode_values']=episode_values(value['equity'],start_equity=value['start_equity'],
                gross=value['gross'],metric='worst_bar_pct',episode_bars=episode_bars,
                native_bars=base_kwargs.get('native_bars')) if metrics[key]['risk_complete'] and (value['equity']>0).all() else None
    return metrics


def evaluate(spec: dict, p, h, signals, exits, *, equity=10000.0, fit_end=None, folds=4, funding=None, opens=None,
             flat_funding_rate=0.0, assessment_episode_bars=None, reduced_fraction=None,
             ratios=None,correlations=None,entry_allowed=None,venue=None,continuous_folds=False,
             primary_exit=None,decision_bars=None,native_bars=None,bid_ask=None,funding_time_weights=None,quote_times=None) -> dict:
    """Combined-portfolio comparison under ONE capital/margin convention:
    hedged vs unhedged vs reduced-size vs cash on the declared objective."""
    validate(spec)
    spec = dict(spec, ratio=dict(spec['ratio'], **({'fit_end': fit_end} if fit_end is not None else {})))
    kw = dict(equity=equity, funding=funding, opens=opens, flat_funding_rate=flat_funding_rate,
              ratios=ratios,correlations=correlations,entry_allowed=entry_allowed,primary_exit=primary_exit,decision_bars=decision_bars,
              native_bars=native_bars,bid_ask=bid_ask,funding_time_weights=funding_time_weights,quote_times=quote_times)
    hedged = replay(spec, p, h, signals, exits, **dict(kw,**({"venue":venue} if venue is not None else {})))
    unhedged = replay(spec, p, h, signals, exits, ratio_override=0.0, **kw)
    mh, mu = risk_metrics(hedged), risk_metrics(unhedged)
    # reduced-size: same average NET directional exposure as the hedged run (exposure-matched, outcome-blind)
    if reduced_fraction is not None:
        if isinstance(reduced_fraction,bool) or not np.isfinite(reduced_fraction) or not 0<=reduced_fraction<=1:
            raise ValueError('reduced_fraction_invalid')
        frac=float(reduced_fraction)
    else:
        frac = float(np.clip(mh['mean_abs_net'] / mu['mean_gross'], 0.0, 1.0)) if mu['mean_gross'] > 0 else 0.0
    reduced_spec = dict(spec, sizing=dict(spec['sizing'], primary_notional_pct=spec['sizing']['primary_notional_pct'] * frac))
    reduced = replay(reduced_spec, p, h, signals, exits, ratio_override=0.0, **kw)
    mr = risk_metrics(reduced)
    metric = spec['objective']['metric']
    bounds = np.linspace(0, len(p), folds + 1).astype(int)
    fold_rows = []
    for a, b in (zip(bounds[:-1], bounds[1:]) if not continuous_folds else ()):
        sub = lambda arr: arr[a:b]                      # noqa: E731
        if signals[a:b].any():
            sub_kw = dict(equity=equity, opens=None if opens is None else (sub(opens[0]), sub(opens[1])),
                          funding=None if funding is None else (sub(funding[0]), sub(funding[1])), flat_funding_rate=flat_funding_rate,
                          ratios=None if ratios is None else sub(ratios),correlations=None if correlations is None else sub(correlations),
                          entry_allowed=None if entry_allowed is None else sub(entry_allowed),primary_exit=primary_exit,
                          decision_bars=None if decision_bars is None else decision_bars[a:b],
                          native_bars=None if native_bars is None else sub(native_bars),
                          bid_ask=None if bid_ask is None else {leg:tuple(sub(x) for x in pair) for leg,pair in bid_ask.items()},
                          funding_time_weights=None if funding_time_weights is None else sub(funding_time_weights),
                          quote_times=None if quote_times is None else sub(quote_times))
            rh = risk_metrics(replay(spec, sub(p), sub(h), sub(signals), sub(exits), **sub_kw))
            ru = risk_metrics(replay(spec, sub(p), sub(h), sub(signals), sub(exits), ratio_override=0.0, **sub_kw))
            if metric == 'stress_loss_pct':
                rh[metric] = max(v['worst_bar_pct'] for v in stress_cases(spec, sub(p), sub(h), sub(signals), sub(exits), sub_kw).values())
                ru[metric] = max(v['worst_bar_pct'] for v in stress_cases(spec, sub(p), sub(h), sub(signals), sub(exits), dict(sub_kw, ratio_override=0.0)).values())
            if spec['purpose'] == 'relative_value':
                rr = risk_metrics(replay(reduced_spec, sub(p), sub(h), sub(signals), sub(exits), ratio_override=0.0, **sub_kw))
                known = all(x['risk_complete'] and x[metric] is not None for x in (rh, ru, rr))
                better = known and rh[metric] > max(0.0, ru[metric], rr[metric])
                fold_rows.append(dict(bars=[int(a), int(b)], hedged=rh[metric], unhedged=ru[metric], reduced=rr[metric], cash=0.0, better=better))
            else:
                fold_rows.append(dict(bars=[int(a), int(b)], hedged=rh[metric], unhedged=ru[metric], better=rh[metric] < ru[metric]))
    native_count=len(set(native_bars)) if native_bars is not None else len(p)
    stress_episode_bars=(assessment_episode_bars or max(1,native_count//folds)) if metric=='stress_loss_pct' and (assessment_episode_bars or continuous_folds) else None
    stress = stress_cases(spec, p, h, signals, exits, kw,episode_bars=stress_episode_bars)
    if metric == 'stress_loss_pct':
        mh['stress_loss_pct'] = max(v['worst_bar_pct'] for v in stress.values())
        stress_unhedged=stress_cases(spec,p,h,signals,exits,dict(kw,ratio_override=0.0),episode_bars=stress_episode_bars)
        stress_reduced=stress_cases(reduced_spec,p,h,signals,exits,dict(kw,ratio_override=0.0),episode_bars=stress_episode_bars)
        mu['stress_loss_pct']=max(v['worst_bar_pct'] for v in stress_unhedged.values())
        mr['stress_loss_pct']=max(v['worst_bar_pct'] for v in stress_reduced.values())
    if continuous_folds:
        from ..closure.objective_admission import episode_values
        size=max(1,(len(set(native_bars)) if native_bars is not None else len(p))//folds)
        paths={name:episode_values(run['equity'],start_equity=equity,gross=run['gross'],
                metric='worst_bar_pct' if metric=='stress_loss_pct' else metric,episode_bars=size,native_bars=native_bars)
               for name,run in (('hedged',hedged),('unhedged',unhedged),('reduced',reduced))}
        fold_rows=[]
        for index,ep in enumerate(paths['hedged']):
            if not any(rows[index]['active'] for rows in paths.values()):continue
            vals={name:rows[index]['value'] for name,rows in paths.items()}
            if metric=='stress_loss_pct':
                vals={name:max(c['objective_episode_values'][index]['value'] for c in cases.values())
                      for name,cases in (('hedged',stress),('unhedged',stress_unhedged),('reduced',stress_reduced))}
            better=(vals['hedged']>max(0.,vals['unhedged'],vals['reduced'])) if spec['purpose']=='relative_value' else vals['hedged']<vals['unhedged']
            fold_rows.append(dict(bars=[ep['start'],ep['end']],**vals,cash=0.,better=better))
    relative = spec['purpose'] == 'relative_value'
    complete = all(m['risk_complete'] and m.get(metric) is not None for m in (mh, mu, mr))
    better_u = complete and (mh[metric] > mu[metric] if relative else mh[metric] < mu[metric])
    better_r = complete and (mh[metric] > mr[metric] if relative else mh[metric] < mr[metric])
    wins = sum(f['better'] for f in fold_rows)
    majority = bool(fold_rows) and wins > len(fold_rows) / 2
    if not complete:
        verdict='INSUFFICIENT_EVIDENCE'
        why=('native coin collateral NAV is not established; contract payoff alone is not complete portfolio risk'
             if any(not m.get('collateral_nav_established',True) for m in (mh,mu,mr)) else
             'held-leg marks or declared exit inputs are unknown; risk benefit cannot be established')
    elif mh['entries'] < 3:
        verdict, why = 'INSUFFICIENT_EVIDENCE', f'{mh["entries"]} hedged entries'
    elif mh['liquidated']:
        verdict, why = 'ECONOMIC_UNSUPPORTED', 'hedged account liquidated despite low net exposure'
    elif better_u and better_r and majority and (not relative or mh[metric] > 0.0):
        op = '>' if relative else '<'
        verdict, why = 'HEDGE_SUPPORTED', f'{metric}: hedged {mh[metric]:.3f} {op} unhedged {mu[metric]:.3f} and {op} reduced {mr[metric]:.3f}; folds {wins}/{len(fold_rows)}'
    else:
        verdict, why = 'ECONOMIC_UNSUPPORTED', (f'{metric}: hedged {mh[metric]:.3f} unhedged {mu[metric]:.3f} reduced {mr[metric]:.3f}; '
                                                 f'folds {wins}/{len(fold_rows)}')
    result = dict(spec_id=spec['spec_id'], verdict=verdict, reason=why, objective=metric,
                capital_policy_id=K.POLICY_ID if capital_enabled(spec) else None,
                comparators=dict(hedged=mh, unhedged=mu, reduced=dict(mr, fraction=frac), cash=dict(net_return_pct=0.0, max_drawdown_pct=0.0)),
                return_sacrificed_pct=mu['net_return_pct'] - mh['net_return_pct'] if mu['net_return_pct'] is not None and mh['net_return_pct'] is not None else None,
                carry_cost=hedged['cost'], rebalances=mh['rebalances'], residual_exposure=dict(
                    mean_abs_net=mh['mean_abs_net'], mean_gross=mh['mean_gross']),
                folds=fold_rows, stress=stress, runtime_support=RUNTIME_SUPPORT)
    if assessment_episode_bars:
        from ..closure import objective_admission as OA
        if assessment_episode_bars!=OA.PROTOCOL['episode_bars']:
            raise ValueError('objective_episode_protocol_differs')
        episodes=[]
        try:
            paths={name:OA.episode_values(run['equity'],start_equity=equity,gross=run['gross'],
                    metric='worst_bar_pct' if metric=='stress_loss_pct' else metric,episode_bars=assessment_episode_bars,native_bars=native_bars)
                   for name,run in (('hedged',hedged),('unhedged',unhedged),('reduced',reduced))}
            if len(paths['hedged'])>OA.PROTOCOL['max_episodes']:
                raise ValueError('objective_episode_budget_exceeded')
            for index,episode in enumerate(paths['hedged']):
                values={name:rows[index]['value'] for name,rows in paths.items()}
                if metric=='stress_loss_pct':
                    values={name:max(case['objective_episode_values'][index]['value'] for case in cases.values())
                            for name,cases in (('hedged',stress),('unhedged',stress_unhedged),('reduced',stress_reduced))}
                episodes.append(dict(start=episode['start'],end=episode['end'],**values,cash=0.,
                                     active=any(rows[index]['active'] for rows in paths.values())))
        except (ValueError,TypeError,IndexError):
            episodes=[] # unknown/bankrupt paths cannot provide inferential support
        result.update(objective_episodes=episodes,admission_protocol_id=OA.protocol_id(),
                      inference_scope=OA.PROTOCOL['claim'],unassessed_tail_bars=native_count%assessment_episode_bars)
    return result


MAX_GRID_SLOTS = 400_000          # hard bound on the aligned clock (about 130 years of 4h bars); a request beyond it is refused


def align_pair(df, hdf, lo, sh, step_ms: int, *, p_window=None, h_window=None, max_slots=MAX_GRID_SLOTS) -> dict:
    """Both legs on ONE full UTC clock, never the intersection of timestamps, and an entry only when it is CAUSALLY executable.

    * Timestamps are validated before any arithmetic: non-empty, strictly increasing (no duplicates that could overwrite a row),
      every stamp an exact multiple of `step_ms` from the first (no row floored into a neighbouring slot), and the grid is bounded by
      `max_slots` BEFORE it is allocated. Anything else is refused with ValueError, never repaired.
    * A missing bar of a held leg stays an unknown (NaN) mark; a signal may not fill after a gap on a later bar.
    * Executability reuses fill_timing for BOTH legs with their own consumed window (`p_window`: the rule's actual dependency;
      `h_window`: only what the ratio procedure needs, NOT the 15-bar price-indicator default): the next open must be the very next
      UTC slot, the decision may not precede any consumed receipt, quality/finality must hold, and the signal must be fresh. A frame
      without receipts (or an explicitly retrospective one) decides at close under its own declared scope, as fill_timing does.
    * Signals fire on an EDGE of the raw signal series: a new episode or a DIRECTION FLIP. Consecutive repeats of the same direction
      do not re-enter; a true opposite signal is never deleted. The edge is taken on the RAW series, so a refused edge is not replaced
      by a later bar of the same run (no delayed fill).
    Returns grid arrays, the row->slot maps used to place per-leg series (e.g. funding) on the same clock and the refusal counts."""
    from ...data import market_provenance as mp
    from ...strategy import fill_timing
    for name, fr in (('primary', df), ('hedge', hdf)):
        if fr is None or not len(fr):
            raise ValueError(f'hedge_align_{name}_empty')
    pts, hts = np.asarray(mp.ms(df['ts']), dtype='int64'), np.asarray(mp.ms(hdf['ts']), dtype='int64')
    for name, t in (('primary', pts), ('hedge', hts)):
        if (np.diff(t) <= 0).any():
            raise ValueError(f'hedge_align_{name}_timestamps_duplicate_or_unordered')
    start, end = int(min(pts[0], hts[0])), int(max(pts[-1], hts[-1]))
    for name, t in (('primary', pts), ('hedge', hts)):
        if ((t - start) % step_ms).any():
            raise ValueError(f'hedge_align_{name}_timestamps_off_grid')
    n = (end - start) // step_ms + 1
    if n > max_slots:
        raise ValueError(f'hedge_align_grid_exceeds_bound:{n}>{max_slots}')
    grid = start + np.arange(n, dtype='int64') * step_ms
    ip, ih = ((pts - start) // step_ms).astype(int), ((hts - start) // step_ms).astype(int)

    def put(idx, arr):
        out = np.full(n, np.nan)
        out[idx] = np.asarray(arr, dtype=float)
        return out
    def usable(fr):
        """Rows whose observation may be used: VALID quality (retrospective frames carry observation_quality) and FINAL bars."""
        ok = np.ones(len(fr), dtype=bool)
        qcol = 'observation_quality' if fr.attrs.get('read_mode') == 'retrospective' and 'observation_quality' in fr else 'quality'
        if qcol in fr:
            ok &= fr[qcol].eq('VALID').to_numpy()
        if 'bar_state' in fr:
            ok &= fr['bar_state'].eq('FINAL').to_numpy()
        return ok
    up, uh = usable(df), usable(hdf)               # an unusable row is an UNKNOWN price, exactly like a missing one
    p, h = put(ip, np.where(up, df['close'].to_numpy(float), np.nan)), put(ih, np.where(uh, hdf['close'].to_numpy(float), np.nan))
    po, ho = put(ip, np.where(up, df['open'].to_numpy(float), np.nan)), put(ih, np.where(uh, hdf['open'].to_numpy(float), np.nan))
    tf_name = next((k for k, v in fill_timing.TF_MS.items() if v == step_ms), None)

    def fills_for(fr, window):
        f = fr.copy()
        f.attrs.update(fr.attrs)
        if tf_name:
            f.attrs['timeframe'] = tf_name
        if window:
            f.attrs['signal_lookback_bars'] = int(window)
        return fill_timing.fills(f)
    fp, whyp = fills_for(df, p_window)
    fh, whyh = fills_for(hdf, h_window if h_window else 1)
    # per-slot executability of an entry signalled on that slot's close
    ok_p, ok_h = np.zeros(n, bool), np.zeros(n, bool)
    why_p, why_h = np.full(n, 'row_missing', dtype=object), np.full(n, 'row_missing', dtype=object)
    for rows, fill, why, ok, wmap, idx in ((len(df), fp, whyp, ok_p, why_p, ip), (len(hdf), fh, whyh, ok_h, why_h, ih)):
        for r in range(rows):
            slot = idx[r]
            nxt = r + 1
            if fill[r] != nxt or nxt >= rows or idx[nxt] != slot + 1:
                wmap[slot] = why[r] or 'fill_not_on_next_utc_slot'
            else:
                ok[slot], wmap[slot] = True, ''
    raw = np.zeros(n, int)
    raw[ip] = (np.asarray(lo, bool) & ~np.asarray(sh, bool)).astype(int) - (np.asarray(sh, bool) & ~np.asarray(lo, bool)).astype(int)
    prev = np.r_[0, raw[:-1]]
    edge = (raw != 0) & (raw != prev)                       # new episode or direction flip, judged on the RAW series
    refused = dict(leg_close_missing_at_signal=0, leg_open_missing_at_fill=0, no_next_slot=0, primary_not_executable=0,
                   hedge_not_executable=0, primary_reasons={}, hedge_reasons={})
    sig = np.zeros(n, int)
    for t in np.flatnonzero(edge):
        if t + 1 >= n:
            refused['no_next_slot'] += 1
        elif not (np.isfinite(p[t]) and np.isfinite(h[t])):
            refused['leg_close_missing_at_signal'] += 1
        elif not (np.isfinite(po[t + 1]) and np.isfinite(ho[t + 1])):
            refused['leg_open_missing_at_fill'] += 1
        elif not ok_p[t]:
            refused['primary_not_executable'] += 1
            refused['primary_reasons'][why_p[t]] = refused['primary_reasons'].get(why_p[t], 0) + 1
        elif not ok_h[t]:
            refused['hedge_not_executable'] += 1
            refused['hedge_reasons'][why_h[t]] = refused['hedge_reasons'].get(why_h[t], 0) + 1
        else:
            sig[t] = raw[t]
    return dict(grid_ms=grid, p=p, h=h, po=po, ho=ho, sig=sig, idx_p=ip, idx_h=ih, refused=refused, bars=int(n),
                missing_primary=int(np.isnan(p).sum()), missing_hedge=int(np.isnan(h).sum()))


def place(idx, arr, n: int):
    """Put a per-row series on the aligned clock; slots without a row are NaN (unknown), never zero."""
    out = np.full(n, np.nan)
    out[idx] = np.asarray(arr, dtype=float)
    return out


def aligned_exits(compiled, frames, alignment, **context):
    """Declared closed-bar exit on the same two-leg clock as entries.

    Unknown declared-exit evidence requests unwind and prevents new entry;
    it must not silently become a hold. No declared exit means no signal exit.
    """
    out = np.zeros(alignment['bars'], dtype=bool)
    if compiled.spec.exit.signal_exit.strip():
        signal, available = compiled.exit_signal_detail(frames, **context)
        out[:] = True
        out[alignment['idx_p']] = np.asarray(signal, bool) | ~np.asarray(available, bool)
    return out


def primary_exit_inputs(compiled,frame,alignment,hedge_frame=None):
    """Version-bound geometry and observations on the same complete pair clock."""
    from dataclasses import asdict
    from ...strategy.exit_policy import trail_atr_series
    from ...core.types import TF_MS as native_steps
    n=alignment['bars'];step=native_steps[compiled.spec.timeframe]
    values={key:place(alignment['idx_p'],frame[key],n) for key in ('open','high','low','close')}
    atr=place(alignment['idx_p'],trail_atr_series(frame,step),n)
    bars=[dict(bar_ms=int(stamp),step_ms=step,**{key:float(value[i]) for key,value in values.items()},
               atr=float(atr[i]) if np.isfinite(atr[i]) else None) for i,stamp in enumerate(alignment['grid_ms'])]
    if hedge_frame is not None:
        values={key:place(alignment['idx_h'],hedge_frame[key],n) for key in ('open','high','low','close')}
        atr=place(alignment['idx_h'],trail_atr_series(hedge_frame,step),n)
        for i,bar in enumerate(bars):
            bar['hedge']=dict(bar_ms=bar['bar_ms'],step_ms=step,
                **{key:float(value[i]) for key,value in values.items()},
                atr=float(atr[i]) if np.isfinite(atr[i]) else None)
    return asdict(compiled.spec.exit),bars
