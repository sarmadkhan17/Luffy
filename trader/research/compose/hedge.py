"""§3A — hedging as a research capability: declaration, state machine, replay,
stress and comparison. One `HedgeBook` decides every instruction; the replay
driver and the incremental `RuntimeAdapter` both call it, so replay and the
instruction stream a runtime would receive cannot diverge (tested bar-by-bar).

Scope and limits (recorded, not hidden)
  * Instructions are INTENTS. The production executor handles positionSide
    BOTH and linear USDT-M only, with no multi-leg book: hedge-mode and COIN-M
    execution, and joint-leg order management, are NOT wired. `RUNTIME_SUPPORT`
    states this; nothing here can place an order or grants authority.
  * Accounting is explicit per contract kind (linear USDT-M, inverse COIN-M
    with coin collateral) and per account mode (hedge vs netting). In netting
    mode an opposite order on the SAME symbol closes exposure, so a same-symbol
    "hedge" is refused (`NETTING_COLLAPSES_HEDGE`).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..closure import ClosureRefused, digest

SCHEMA = 'hedge-spec.v1'
PURPOSES = ('protective_beta', 'protective_directional', 'stress_loss', 'relative_value')
RUNTIME_SUPPORT = dict(
    account_modes_in_executor=['netting(positionSide=BOTH)'], contracts_in_executor=['linear USDT-M'],
    multi_leg_orders=False, coin_m=False, hedge_mode=False,
    note='replay + instruction adapter only; executor integration is an open delivery gap')


def deployment_contract(spec: dict, verified: dict | None = None) -> dict:
    """Can this hedge be EXECUTED, in the account and contract modes it declares? Replay agreement is not execution
    support: every mode the executor does not verifiably handle is an explicit refusal of the candidate (never a
    silent downgrade to single-leg or linear). `verified` defaults to RUNTIME_SUPPORT; a mode becomes deployable only
    when an owner-verified record names it. This function grants no authority and places nothing."""
    v = dict(RUNTIME_SUPPORT, **(verified or {}))
    reasons = []
    if not v.get('multi_leg_orders'):
        reasons.append('multi_leg_orders_not_wired: joint entry/adjust/unwind of two legs has no executor')
    mode = 'netting(positionSide=BOTH)' if spec['account_mode'] == 'netting' else 'hedge(positionSide=LONG/SHORT)'
    if mode not in v['account_modes_in_executor']:
        reasons.append(f'account_mode_unverified: {mode}')
    for leg in ('primary', 'hedge'):
        kind = 'linear USDT-M' if spec[leg].get('contract', 'linear') == 'linear' else 'inverse COIN-M'
        if kind not in v['contracts_in_executor']:
            reasons.append(f'contract_unverified: {leg} leg is {kind}')
    return dict(status='DEPLOYABLE' if not reasons else 'REFUSED_UNSUPPORTED_VENUE_MODE', reasons=reasons,
                replay_only=bool(reasons), spec_id=spec.get('spec_id'),
                claim='replay and instruction adapters only; no live multi-leg support is claimed' if reasons else
                      'every declared mode is in the verified executor record')


class HedgeRefused(ClosureRefused):
    pass


@dataclass(frozen=True)
class Contract:
    kind: str = 'linear'          # linear (USDT-M) | inverse (COIN-M)
    multiplier: float = 1.0       # inverse: USD per contract

    def qty_for(self, notional_usd: float, price: float) -> float:
        return notional_usd / price if self.kind == 'linear' else float(np.floor(notional_usd / self.multiplier))

    def notional(self, qty: float, price: float) -> float:
        return abs(qty) * price if self.kind == 'linear' else abs(qty) * self.multiplier

    def pnl_usd(self, qty: float, p0: float, p1: float) -> float:
        """Signed qty (+long). Inverse P&L accrues in the base coin and is marked to USD at p1."""
        if self.kind == 'linear':
            return qty * (p1 - p0)
        return qty * self.multiplier * (1.0 / p0 - 1.0 / p1) * p1


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

    def step(self, t: int, bar: dict) -> list:
        """bar: p (primary px), h (hedge px, NaN = unavailable), signal (+1/-1/0), exit (bool),
        ratio (current hedge ratio >=0), corr (rolling corr or None), prim_c / hedge_c (Contract)."""
        out, sp = [], self.spec
        if bar['signal'] not in (-1, 0, 1):
            raise HedgeRefused('hedge_signal_invalid')
        if not np.isfinite(bar['ratio']):
            # UNKNOWN ratio (too few paired observations): no new entry; a held book keeps its last known ratio. One rule for the replay
            # and the runtime adapter, because both go through this method.
            self.ratio_unknown += 1
            bar = dict(bar, ratio=self.last_ratio if self.last_ratio is not None else 0.0,
                       signal=0 if self.state == 'FLAT' else bar['signal'])
        elif bar['ratio'] >= 0:
            self.last_ratio = float(bar['ratio'])
        if not np.isfinite(bar['equity']) or bar['equity'] <= 0 or not np.isfinite(bar['ratio']) or bar['ratio'] < 0:
            raise HedgeRefused('hedge_equity_or_ratio_invalid')
        pc, hc = bar['prim_c'], bar['hedge_c']
        ok = np.isfinite(bar['p']) and np.isfinite(bar['h']) and bar['p'] > 0 and bar['h'] > 0
        self.missing = 0 if ok else self.missing + 1
        if self.state == 'FLAT':
            if bar['signal'] != 0 and not bar.get('exit') and ok and not self.pending:
                self.direction = int(bar['signal'])
                notional = bar['equity'] * sp['sizing']['primary_notional_pct'] / 100.0
                pq = pc.qty_for(notional, bar['p'])
                hq = hc.qty_for(notional * bar['ratio'], bar['h'])
                self._emit(out, 'ENTER', 'primary', self.direction, pq, 'signal', t)
                self._emit(out, 'ENTER', 'hedge', -self.direction, hq, 'joint_entry', t)
                self.state, self.entry_bar, self.last_rebalance, self.leg_wait = 'OPENING', t, t, 0
            return out
        held = abs(self.pos['primary']) > 0 or abs(self.pos['hedge']) > 0
        if self.pending:
            return out                                    # one working order set at a time
        if self.state == 'OPENING':
            self.state = 'HEDGED'
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
        if bar.get('exit'):
            reason = 'primary_exit'
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
        want = prim_n * bar['ratio']
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
                self._emit(out, 'REBALANCE', 'hedge', side, hc.qty_for(want - have, bar['h']), 'ratio_drift', t)
            else:
                self._emit(out, 'REBALANCE', 'hedge', -side, hc.qty_for(have - want, bar['h']), 'ratio_drift', t)
            self.last_rebalance = t
            self.leg_wait += 1
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
           slip_mult: float = 1.0, opens: tuple | None = None, flat_funding_rate: float = 0.0) -> dict:
    """Bar loop: fills execute at the NEXT bar's price (signal age = 1), costs per fill, funding per bar,
    mark-to-market equity every bar (not only at closes), liquidation test with maintenance margin."""
    validate(spec)
    venue = venue or Venue()
    pc, hc = (Contract(spec[k]['contract'], spec[k]['multiplier']) for k in ('primary', 'hedge'))
    book = HedgeBook(spec)
    n = len(p)
    if opens is not None and (len(opens) != 2 or any(len(a) != n for a in opens)):
        raise HedgeRefused('hedge_open_price_shape')
    pr = np.r_[np.nan, np.diff(p) / p[:-1]]
    hr = np.r_[np.nan, np.diff(h) / h[:-1]]
    cash, eq_curve, gross, net, instr = float(equity), np.full(n, np.nan), np.zeros(n), np.zeros(n), []
    cost = dict(fees=0.0, slippage=0.0, funding=0.0)
    last_px = dict(primary=p[0], hedge=h[0])
    avg = dict(primary=p[0], hedge=h[0])
    liquidated, pending_ins, realised = False, [], 0.0
    fee, slip = spec['fee_bps'] / 1e4, spec['slip_bps'] / 1e4 * slip_mult
    for t in range(n):
        px = dict(primary=p[t], hedge=h[t])
        fill_px = dict(primary=opens[0][t], hedge=opens[1][t]) if opens is not None else px
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
            for i in pending_ins:
                if i.kind == 'ALERT':
                    continue
                c = pc if i.leg == 'primary' else hc
                f = min(fills.get(i.oid, 0.0), i.qty)
                if f > 0 and np.isfinite(fill_px[i.leg]) and fill_px[i.leg] > 0:
                    notional = c.notional(f, fill_px[i.leg])
                    cash -= notional * (fee + slip)
                    cost['fees'] += notional * fee
                    cost['slippage'] += notional * slip
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
        if gross[t] > 0 and cash < spec['maint_margin'] * gross[t] and not liquidated:
            liquidated = True
            book.pos = dict(primary=0.0, hedge=0.0)
            book.state = 'FLAT'
            cash -= 0.005 * gross[t]            # liquidation penalty (declared)
            eq_curve[t] = cash
            continue
        # 4. decisions on this bar's close
        if not liquidated and t < n - 1:
            ratio = ratio_override if ratio_override is not None else estimate_ratio(spec, pr, hr, t)
            sig_t = int(signals[t])
            corr = None
            lo = max(0, t - 95)
            a, b = pr[lo:t + 1], hr[lo:t + 1]
            ok = np.isfinite(a) & np.isfinite(b)
            if ok.sum() > 20 and a[ok].std() > 0 and b[ok].std() > 0:
                corr = float(np.corrcoef(a[ok], b[ok])[0, 1])
            ins = book.step(t, _bar(spec, t, p[t], h[t], sig_t, bool(exits[t]), ratio, corr, cash, pc, hc))
            instr += ins
            pending_ins = ins
    if book.pos['primary'] != 0 or book.pos['hedge'] != 0:
        status = 'OPEN_AT_END'
    else:
        status = 'FLAT_AT_END'
    inverse_legs = [k for k in ('primary', 'hedge') if spec[k]['contract'] == 'inverse']
    return dict(equity=eq_curve, gross=gross, net=net, cost=cost,
                fill_price_basis='NEXT_BAR_OPEN' if opens is not None else 'NEXT_OBSERVATION_PRICE_MODEL_ONLY',
                coin_collateral_exposure_usd=None if inverse_legs else 0.0, inverse_legs=inverse_legs,
                collateral_nav_basis='NOT_ESTABLISHED_FOR_INVERSE: contract payoff only' if inverse_legs else 'quote_currency',
                instructions=instr, liquidated=liquidated, ratio_unknown_bars=book.ratio_unknown,
                status=status, final_equity=float(eq_curve[-1]) if np.isfinite(eq_curve[-1]) else None, start_equity=float(equity),
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
    ret = np.diff(eq) / np.where(eq[:-1] > 0, eq[:-1], 1.0) * 100.0
    k = max(1, int(np.ceil(0.05 * len(ret))))
    return dict(max_drawdown_pct=float(-dd.min()) if len(dd) else 0.0, worst_bar_pct=float(-ret.min()) if len(ret) else 0.0,
                cvar5_pct=float(-np.sort(ret)[:k].mean()) if len(ret) else 0.0,
                net_return_pct=float((eq[-1] / res['start_equity'] - 1) * 100) if not unknown else None, costs=res['cost'],
                mean_gross=float(np.nanmean(res['gross'])), mean_abs_net=float(np.nanmean(np.abs(res['net']))),
                unknown_mark_bars=unknown, risk_complete=unknown == 0,
                liquidated=res['liquidated'], entries=res['entries'], rebalances=res['rebalances'])


def stress_cases(spec, p, h, signals, exits, base_kwargs) -> dict:
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
        return kw
    out['correlation_break'] = replay(spec, p, h2, signals, exits, **scenario_prices(h2))
    # basis divergence: hedge price drifts away by 5% over the second half
    h3 = h * np.r_[np.ones(n // 2), np.linspace(1.0, 1.05, n - n // 2)]
    out['basis_divergence'] = replay(spec, p, h3, signals, exits, **scenario_prices(h3))
    # funding spike on both legs
    spike = np.full(n, 0.0005)
    out['funding_spike'] = replay(spec, p, h, signals, exits, **dict(base_kwargs, funding=(spike, -spike)))
    # liquidity loss: slippage x10
    out['liquidity_loss'] = replay(spec, p, h, signals, exits, **dict(base_kwargs, slip_mult=10.0))
    # one-leg fill: hedge leg never fills
    out['one_leg_fill'] = replay(spec, p, h, signals, exits, **dict(base_kwargs, venue=Venue(fill_frac=dict(primary=1.0, hedge=0.0))))
    # margin pressure: leverage raised until maintenance margin binds
    out['margin_pressure'] = replay(dict(spec, maint_margin=0.5), p, h, signals, exits, **base_kwargs)
    return {k: dict(risk_metrics(v), status=v['status']) for k, v in out.items()}


def evaluate(spec: dict, p, h, signals, exits, *, equity=10000.0, fit_end=None, folds=4, funding=None, opens=None,
             flat_funding_rate=0.0) -> dict:
    """Combined-portfolio comparison under ONE capital/margin convention:
    hedged vs unhedged vs reduced-size vs cash on the declared objective."""
    validate(spec)
    spec = dict(spec, ratio=dict(spec['ratio'], **({'fit_end': fit_end} if fit_end is not None else {})))
    kw = dict(equity=equity, funding=funding, opens=opens, flat_funding_rate=flat_funding_rate)
    hedged = replay(spec, p, h, signals, exits, **kw)
    unhedged = replay(spec, p, h, signals, exits, ratio_override=0.0, **kw)
    mh, mu = risk_metrics(hedged), risk_metrics(unhedged)
    # reduced-size: same average NET directional exposure as the hedged run (exposure-matched, outcome-blind)
    frac = float(np.clip(mh['mean_abs_net'] / mu['mean_gross'], 0.0, 1.0)) if mu['mean_gross'] > 0 else 0.0
    reduced_spec = dict(spec, sizing=dict(spec['sizing'], primary_notional_pct=spec['sizing']['primary_notional_pct'] * frac))
    reduced = replay(reduced_spec, p, h, signals, exits, ratio_override=0.0, **kw)
    mr = risk_metrics(reduced)
    metric = spec['objective']['metric']
    bounds = np.linspace(0, len(p), folds + 1).astype(int)
    fold_rows = []
    for a, b in zip(bounds[:-1], bounds[1:]):
        sub = lambda arr: arr[a:b]                      # noqa: E731
        if signals[a:b].any():
            sub_kw = dict(equity=equity, opens=None if opens is None else (sub(opens[0]), sub(opens[1])),
                          funding=None if funding is None else (sub(funding[0]), sub(funding[1])), flat_funding_rate=flat_funding_rate)
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
    stress = stress_cases(spec, p, h, signals, exits, kw)
    if metric == 'stress_loss_pct':
        mh['stress_loss_pct'] = max(v['worst_bar_pct'] for v in stress.values())
        mu['stress_loss_pct'] = max(v['worst_bar_pct'] for v in stress_cases(spec, p, h, signals, exits, dict(kw, ratio_override=0.0)).values())
        mr['stress_loss_pct'] = max(v['worst_bar_pct'] for v in stress_cases(reduced_spec, p, h, signals, exits, dict(kw, ratio_override=0.0)).values())
    relative = spec['purpose'] == 'relative_value'
    complete = all(m['risk_complete'] and m.get(metric) is not None for m in (mh, mu, mr))
    better_u = complete and (mh[metric] > mu[metric] if relative else mh[metric] < mu[metric])
    better_r = complete and (mh[metric] > mr[metric] if relative else mh[metric] < mr[metric])
    wins = sum(f['better'] for f in fold_rows)
    majority = bool(fold_rows) and wins > len(fold_rows) / 2
    if not complete:
        verdict, why = 'INSUFFICIENT_EVIDENCE', 'held-leg marks are unknown; risk benefit cannot be established'
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
    return dict(spec_id=spec['spec_id'], verdict=verdict, reason=why, objective=metric,
                comparators=dict(hedged=mh, unhedged=mu, reduced=dict(mr, fraction=frac), cash=dict(net_return_pct=0.0, max_drawdown_pct=0.0)),
                return_sacrificed_pct=mu['net_return_pct'] - mh['net_return_pct'] if mu['net_return_pct'] is not None and mh['net_return_pct'] is not None else None,
                carry_cost=hedged['cost'], rebalances=mh['rebalances'], residual_exposure=dict(
                    mean_abs_net=mh['mean_abs_net'], mean_gross=mh['mean_gross']),
                folds=fold_rows, stress=stress, runtime_support=RUNTIME_SUPPORT)


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
