"""Isolated deterministic StrategyVersion paper runtime. No venue capability.

Recorded close references are fills, not venue fills. Gross P&L is explicitly
before UNAVAILABLE commission, slippage and funding; no cost realism claimed.
Open and closed paper trades never enter the real trades/accounting tables.
"""
from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone

from ..core.types import Action, ControlState, Position, Side, TF_MS, closed_bars
from ..strategy import factory_handoff as F
from ..strategy.compile import compile_spec
from ..strategy.spec import StrategySpec
from .exits import SpecExit
from .risk import RiskManager
from .trade_provenance import entry_identity, clean

TABLE = 'versioned_paper_trades'
SCHEMA = '''
CREATE TABLE IF NOT EXISTS paper_risk_kv(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS versioned_paper_trades(
 id TEXT PRIMARY KEY, strategy_id TEXT NOT NULL, version_id TEXT NOT NULL,
 spec_hash TEXT NOT NULL, install_id TEXT NOT NULL, exec_mode TEXT NOT NULL CHECK(exec_mode='paper'),
 decision_id TEXT, cycle_id TEXT, symbol TEXT NOT NULL, side TEXT NOT NULL,
 amount REAL NOT NULL, entry_price REAL NOT NULL, stop_loss REAL NOT NULL,
 take_profit REAL NOT NULL, notional_usdt REAL NOT NULL, leverage INTEGER NOT NULL,
 opened_at TEXT NOT NULL, closed_at TEXT, exit_price REAL, realized_pnl REAL,
 status TEXT NOT NULL, close_reason TEXT, entry_identity_json TEXT NOT NULL,
 fill_basis TEXT NOT NULL, reference_price REAL NOT NULL, fill_timestamp TEXT NOT NULL,
 commission_basis TEXT NOT NULL, commission REAL, slippage_basis TEXT NOT NULL,
 funding_basis TEXT NOT NULL, pnl_basis TEXT NOT NULL,
 entry_bar_ms INTEGER NOT NULL, last_bar_ms INTEGER NOT NULL,
 initial_risk REAL NOT NULL, best REAL NOT NULL, bars_held INTEGER NOT NULL DEFAULT 0,
 exit_fill_basis TEXT, exit_reference_price REAL,
 UNIQUE(version_id,symbol,entry_bar_ms)
);
CREATE UNIQUE INDEX IF NOT EXISTS paper_open_symbol ON versioned_paper_trades(symbol) WHERE status='open';
CREATE TRIGGER IF NOT EXISTS paper_identity_immutable BEFORE UPDATE ON versioned_paper_trades
 WHEN NEW.entry_identity_json IS NOT OLD.entry_identity_json OR NEW.version_id IS NOT OLD.version_id
 OR NEW.spec_hash IS NOT OLD.spec_hash OR NEW.install_id IS NOT OLD.install_id
 OR NEW.strategy_id IS NOT OLD.strategy_id OR NEW.exec_mode IS NOT OLD.exec_mode
 BEGIN SELECT RAISE(ABORT,'paper identity immutable'); END;
'''


def iso(ms):
    return datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat()


def bar_ms(ts):
    return int(ts.timestamp() * 1000) if hasattr(ts, 'timestamp') else int(ts)


def unsupported(spec):
    bad = SpecExit.frozen_unsupported(spec)
    if bad:
        return bad
    if spec.exit.signal_exit:
        return 'signal_exit:runtime_parity_unavailable'
    if (spec.exit.trail or {}).get('kind', 'none') not in ('none', 'atr'):
        return 'trail:runtime_parity_unavailable'
    if not 1 <= int((spec.exit.time or {}).get('max_bars', 32)) <= 500:
        return 'time:max_bars_unsupported'
    return None


class PaperRiskStore:
    """Only the Risk KV protocol, persisted in a separate paper namespace.

    Deliberately no control-state or real account writer and no _tx protocol
    (Risk's fallback KV protocol cannot address state_kv).
    """
    def __init__(self, journal):
        self.journal = journal

    def kv_get(self, key, default=None):
        rows = self.journal.query('SELECT value FROM paper_risk_kv WHERE key=?', (key,))
        return rows[0]['value'] if rows else default

    def kv_set(self, key, value):
        with self.journal._tx() as c:
            c.execute('INSERT OR REPLACE INTO paper_risk_kv VALUES (?,?)', (key, value))

    def log_control_event(self, *args, **kwargs):
        # A paper Risk latch is durable KV evidence, never a control event.
        pass


class PaperExecutor:
    def __init__(self, journal, cfg):
        self.journal, self.cfg = journal, cfg
        with journal._tx() as c:
            c.executescript(SCHEMA)
        self.store = PaperRiskStore(journal)
        self.risk = RiskManager(cfg, self.store)

    def open_positions(self):
        return self.journal.query(f"SELECT * FROM {TABLE} WHERE status='open' ORDER BY id")

    def enter(self, decision, snap, strategy_id, *, reference_equity, state):
        """Revalidate exact frozen signal with the existing compiler; book once.

        Global control permission is respected. The equity observation seeds
        a PAPER account once; subsequent paper P&L never changes real equity.
        """
        from ..agents.indicators import atr
        rows = self.journal.query('SELECT version_id FROM strategy_version_installs WHERE strategy_id=?', (strategy_id,))
        if len(rows) != 1:
            raise ValueError('paper_install_ambiguous')
        v = F.load_version(self.journal, rows[0]['version_id'])
        F.verify_validation(self.journal, v)
        inst = F.verify_install(self.journal, v)
        if F.state_of(self.journal, v['version_id']) not in (F.SHADOW, F.APPROVAL_REQUIRED, F.APPROVED_FIRST_LIVE):
            raise ValueError('paper_lifecycle_not_authorized')
        spec = StrategySpec.from_dict(v['spec'])
        bad = unsupported(spec)
        if bad:
            raise ValueError('unsupported_paper_exit:' + bad)
        if snap.symbol not in (spec.universe or {}).get('include', []) or snap.symbol in (spec.universe or {}).get('exclude', []):
            raise ValueError('paper_symbol_not_in_frozen_universe')
        incoming = next((sig for sig in (decision.strategy_signals or [])
                         if sig.get('strategy_id') == strategy_id
                         and sig.get('action') == decision.action.value), None)
        if (decision.symbol != snap.symbol or not incoming
                or (incoming.get('params') or {}).get('spec_sha256') != v['spec_hash']):
            raise ValueError('paper_decision_identity_unverified')
        if snap.market_type not in spec.markets:
            raise ValueError('paper_market_not_in_frozen_spec')
        if spec.regime_filter and snap.regime not in spec.regime_filter:
            raise ValueError('paper_regime_not_in_frozen_spec')
        compiled = compile_spec(spec)
        signal = compiled.to_evaluator()(None, snap)
        if signal is None or signal.action != decision.action:
            raise ValueError('paper_frozen_signal_absent')
        params = signal.params
        if params.get('spec_sha256') != v['spec_hash']:
            raise ValueError('paper_signal_spec_differs')
        if (incoming.get('params') or {}).get('signal_bar_close_ms') != params.get('signal_bar_close_ms'):
            raise ValueError('paper_decision_signal_bar_differs')
        at = int(datetime.fromisoformat(snap.ts).timestamp() * 1000)
        frame = closed_bars(snap.df(spec.timeframe), spec.timeframe, at)
        if frame is None or not len(frame):
            raise ValueError('paper_closed_frame_unavailable')
        if at < inst['installed_at_ms']:
            raise ValueError('paper_before_install')
        entry_bar = bar_ms(frame['ts'].iloc[-1])
        if params.get('signal_bar_close_ms') != entry_bar + TF_MS[spec.timeframe]:
            raise ValueError('paper_signal_bar_differs')
        ident = hashlib.sha256(f"{inst['install_id']}|{snap.symbol}|{entry_bar}".encode()).hexdigest()
        if self.journal.query(f'SELECT id FROM {TABLE} WHERE id=?', (ident,)):
            return ident
        if self.journal.query(
                f"SELECT id FROM {TABLE} WHERE version_id=? AND symbol=? AND status='closed' AND last_bar_ms>=? LIMIT 1",
                (v['version_id'],snap.symbol,entry_bar)):
            raise ValueError('paper_exit_bar_reentry_refused')
        price, a = float(snap.price), float(atr(frame))
        if not all(math.isfinite(x) and x > 0 for x in (price, a, reference_equity)):
            raise ValueError('paper_reference_unavailable')
        side = 'long' if decision.action == Action.BUY else 'short'
        sl, tp = SpecExit.from_spec(spec, versioned=True).frozen_levels(price, a, side)
        positions = self.open_positions()
        seed = self.store.kv_get('seed_equity')
        if seed is None:
            self.store.kv_set('seed_equity', str(reference_equity))
            seed = str(reference_equity)
        totals = self.journal.query(f"SELECT COUNT(*) n, COALESCE(SUM(realized_pnl),0) pnl FROM {TABLE} WHERE status='closed'")[0]
        equity = float(seed) + totals['pnl']
        sizing = self.risk.check_entry(state, snap.symbol, price, a, abs(price-sl)/price,
            [Position(id=p['id'], symbol=p['symbol'], side=Side(p['side']), amount=p['amount'],
                      entry_price=p['entry_price'], notional_usdt=p['notional_usdt'],
                      leverage=p['leverage'], stop_loss=p['stop_loss']) for p in positions],
            equity, totals['n'], snap.market_type)
        if not sizing.ok:
            raise ValueError('paper_risk:' + sizing.reason)
        mult = float(getattr(decision, 'meta_size', 1.0))
        if not math.isfinite(mult) or not 0 < mult <= 1:
            raise ValueError('paper_meta_size_invalid')
        amount = sizing.amount * mult
        if amount * price / self.risk.leverage < self.risk.min_notional:
            raise ValueError('paper_risk:meta_size_below_min_notional')
        loaded = {'kind': 'spec', 'spec_sha256': v['spec_hash']}
        signal_dict = {'strategy_id': strategy_id, 'action': signal.action.value,
                       'confidence': signal.confidence, 'params': clean(params)}
        identity = entry_identity(strategy_id, loaded, decision, signal_dict, iso(at))
        if identity['status'] != 'VERIFIED':
            raise ValueError('paper_identity_unverified')
        identity.update(version_id=v['version_id'], install_id=inst['install_id'],
                        validation_receipt_id=inst['validation_receipt_id'], exec_mode='paper')
        row = dict(id=ident, strategy_id=strategy_id, version_id=v['version_id'], spec_hash=v['spec_hash'],
                   install_id=inst['install_id'], exec_mode='paper', decision_id=decision.id,
                   cycle_id=getattr(decision,'cycle_id',None), symbol=snap.symbol, side=side,
                   amount=amount, entry_price=price, stop_loss=sl, take_profit=tp,
                   notional_usdt=amount*price, leverage=self.risk.leverage, opened_at=iso(at),
                   status='open', entry_identity_json=json.dumps(identity,sort_keys=True),
                   fill_basis='decision_snapshot_price', reference_price=price, fill_timestamp=iso(at),
                   commission_basis='UNAVAILABLE', slippage_basis='UNAVAILABLE', funding_basis='UNAVAILABLE',
                   pnl_basis='GROSS_BEFORE_UNAVAILABLE_COSTS', entry_bar_ms=entry_bar,
                   last_bar_ms=entry_bar, initial_risk=abs(price-sl), best=price)
        with self.journal._tx() as c:
            c.execute('BEGIN IMMEDIATE')
            if c.execute(f'SELECT id FROM {TABLE} WHERE id=?', (ident,)).fetchone():
                return ident
            if c.execute(f"SELECT id FROM {TABLE} WHERE status='open' AND symbol=?", (snap.symbol,)).fetchone():
                raise ValueError('paper_symbol_already_open')
            c.execute(f"INSERT INTO {TABLE} ({','.join(row)}) VALUES ({','.join('?' for _ in row)})", tuple(row.values()))
        return ident

    def manage(self, snap):
        """Replay all unseen CLOSED spec bars, atomically and in order.

        Stop first on a both-hit bar; no entry-bar exits, no TP1/flip overlays.
        Frozen time exits close at max_bars regardless of profit. Trail ratchet
        uses the same high/low + ATR ordering as vector_backtest._trade.
        Costs are unavailable; fills use recorded levels/close, not venue ids.
        """
        from ..agents.indicators import atr
        at = int(datetime.fromisoformat(snap.ts).timestamp()*1000)
        closed = []
        for p in self.open_positions():
            if p['symbol'] != snap.symbol:
                continue
            v = F.load_version(self.journal, p['version_id'])
            spec = StrategySpec.from_dict(v['spec'])
            frame = closed_bars(snap.df(spec.timeframe), spec.timeframe, at)
            if frame is None or not len(frame):
                continue
            sign = 1 if p['side']=='long' else -1
            with self.journal._tx() as c:
                c.execute('BEGIN IMMEDIATE')
                fresh = c.execute(f'SELECT * FROM {TABLE} WHERE id=?', (p['id'],)).fetchone()
                if fresh['status'] != 'open':
                    continue
                p = dict(fresh)
                for i, bar in frame.iterrows():
                    ms = bar_ms(bar['ts'])
                    if ms <= p['last_bar_ms']:
                        continue
                    if ms != p['last_bar_ms'] + TF_MS[spec.timeframe]:
                        # Never silently skip missing bars on restart.
                        break
                    high, low, close = map(float,(bar['high'],bar['low'],bar['close']))
                    a = float(atr(frame.loc[:i]))
                    if not all(math.isfinite(x) and x>0 for x in (high,low,close,a)):
                        break
                    p['bars_held'] += 1
                    trail = spec.exit.trail or {}
                    if trail.get('kind') == 'atr':
                        p['best'] = max(p['best'],high) if sign==1 else min(p['best'],low)
                        risk = p['initial_risk']
                        if abs(p['best']-p['entry_price']) >= float(trail.get('arm_at_r',1.0))*risk:
                            level = p['best']-sign*float(trail['mult'])*a
                            p['stop_loss'] = max(p['stop_loss'],level) if sign==1 else min(p['stop_loss'],level)
                    stop = low<=p['stop_loss'] if sign==1 else high>=p['stop_loss']
                    target = bool(p['take_profit']) and (high>=p['take_profit'] if sign==1 else low<=p['take_profit'])
                    reason = 'stop' if stop else 'target' if target else 'time' if p['bars_held']>=int(spec.exit.time.get('max_bars',32)) else None
                    p['last_bar_ms']=ms
                    if reason:
                        px = p['stop_loss'] if stop else p['take_profit'] if target else close
                        c.execute(f"UPDATE {TABLE} SET status='closed', exit_price=?,realized_pnl=?,closed_at=?,close_reason=?,last_bar_ms=?,bars_held=?,stop_loss=?,best=?,exit_fill_basis=?,exit_reference_price=? WHERE id=? AND status='open'",
                                  (px,(px-p['entry_price'])*sign*p['amount'],iso(ms+TF_MS[spec.timeframe]),reason,ms,p['bars_held'],p['stop_loss'],p['best'],'recorded_frozen_level' if reason!='time' else 'recorded_spec_bar_close',px,p['id']))
                        closed.append(p['id'])
                        break
                    c.execute(f'UPDATE {TABLE} SET last_bar_ms=?,bars_held=?,stop_loss=?,best=? WHERE id=?',
                              (ms,p['bars_held'],p['stop_loss'],p['best'],p['id']))
        return closed
