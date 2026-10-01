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
from ..strategy import exit_policy as E
from .versioned_exits import entry_contract, observations
from . import paper_exit_evidence as X
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
    from ..strategy.exit_policy import unsupported as check
    return check(spec.exit)


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
            columns = {r[1] for r in c.execute("PRAGMA table_info(versioned_paper_trades)")}
            if "exit_state_json" not in columns:
                c.execute("ALTER TABLE versioned_paper_trades ADD COLUMN exit_state_json TEXT")
            if "exit_semantics_id" not in columns:
                c.execute("ALTER TABLE versioned_paper_trades ADD COLUMN exit_semantics_id TEXT")
            X.ensure(c)
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
        price = float(snap.price)
        ref, a, entry_bar = entry_contract(spec, snap, params.get('signal_bar_close_ms'))
        if not all(math.isfinite(x) and x > 0 for x in (price, a, reference_equity)):
            raise ValueError('paper_reference_unavailable')
        side = 'long' if decision.action == Action.BUY else 'short'
        policy, exit_state = E.initialize(spec.exit, ref, a, side, entry_bar, TF_MS[spec.timeframe])
        sl, tp = policy.stop, policy.target
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
                        validation_receipt_id=inst['validation_receipt_id'], exec_mode='paper',
                        exit_semantics_id=inst['exit_semantics_id'], exit_state=E.encode(policy,exit_state))
        row = dict(id=ident, strategy_id=strategy_id, version_id=v['version_id'], spec_hash=v['spec_hash'],
                   install_id=inst['install_id'], exec_mode='paper', decision_id=decision.id,
                   cycle_id=getattr(decision,'cycle_id',None), symbol=snap.symbol, side=side,
                   amount=amount, entry_price=price, stop_loss=sl, take_profit=tp,
                   notional_usdt=amount*price, leverage=self.risk.leverage, opened_at=iso(at),
                   status='open', entry_identity_json=json.dumps(identity,sort_keys=True),
                   fill_basis='decision_snapshot_price', reference_price=price, fill_timestamp=iso(at),
                   commission_basis='UNAVAILABLE', slippage_basis='UNAVAILABLE', funding_basis='UNAVAILABLE',
                   pnl_basis='GROSS_BEFORE_UNAVAILABLE_COSTS', entry_bar_ms=entry_bar,
                   last_bar_ms=entry_bar, initial_risk=policy.initial_r, best=ref,
                   exit_state_json=E.encode(policy,exit_state), exit_semantics_id=inst['exit_semantics_id'])
        with self.journal._tx() as c:
            c.execute('BEGIN IMMEDIATE')
            if c.execute(f'SELECT id FROM {TABLE} WHERE id=?', (ident,)).fetchone():
                return ident
            if c.execute(f"SELECT id FROM {TABLE} WHERE status='open' AND symbol=?", (snap.symbol,)).fetchone():
                raise ValueError('paper_symbol_already_open')
            c.execute(f"INSERT INTO {TABLE} ({','.join(row)}) VALUES ({','.join('?' for _ in row)})", tuple(row.values()))
            X.record(c, X.start(ident, identity, policy, exit_state, amount))
        return ident

    def manage(self, snap):
        """Consume the shared completed-bar transitions; book isolated fills."""
        closed = []
        for original in self.open_positions():
            if original['symbol'] != snap.symbol:
                continue
            v = F.load_version(self.journal, original['version_id'])
            F.verify_exit_binding(self.journal, v)
            inst = F.verify_install(self.journal, v, current=False)
            if (original['spec_hash'], original['install_id']) != (v['spec_hash'], inst['install_id']):
                raise ValueError('paper_exit_identity_differs')
            spec = StrategySpec.from_dict(v['spec'])
            with self.journal._tx() as c:
                c.execute('BEGIN IMMEDIATE')
                p = dict(c.execute(f'SELECT * FROM {TABLE} WHERE id=?', (original['id'],)).fetchone())
                if p['status'] != 'open':
                    continue
                if not p['exit_state_json']:
                    raise ValueError('paper_exit_state_missing')
                policy, state = E.decode(p['exit_state_json'])
                expected, _ = E.decode(json.loads(p['entry_identity_json'])['exit_state'])
                if policy != expected:
                    raise ValueError('paper_exit_policy_differs')
                evidence_row = c.execute(f'SELECT * FROM {X.TABLE} WHERE trade_id=?', (p['id'],)).fetchone()
                if not evidence_row or X.digest(evidence_row['canonical_json']) != evidence_row['canonical_sha256']:
                    raise ValueError('paper_exit_evidence_missing_or_conflicting')
                evidence = json.loads(evidence_row['canonical_json'])
                if evidence['final'] != p['exit_state_json']:
                    raise ValueError('paper_exit_evidence_state_differs')
                for observation in observations(spec, snap, state):
                    # Missing causal history cannot be silently replaced.
                    if observation.bar_ms != state.last_bar_ms + policy.step_ms:
                        break
                    result = E.advance(policy, state, observation, p['amount'])
                    state = result.state
                    X.observe(evidence, policy, result, observation)
                    X.record(c, evidence)
                    c.execute(f'UPDATE {TABLE} SET exit_state_json=?,last_bar_ms=?,bars_held=? WHERE id=?',
                              (E.encode(policy,state),state.last_bar_ms,state.bars_held,p['id']))
                    if result.due:
                        px = state.reference_exit
                        sign = 1 if p['side']=='long' else -1
                        c.execute(f"UPDATE {TABLE} SET status='closed', exit_price=?,realized_pnl=?,closed_at=?,close_reason=?,exit_fill_basis=?,exit_reference_price=? WHERE id=? AND status='open'",
                                  (px,(px-p['entry_price'])*sign*result.quantity,
                                   iso(state.last_bar_ms+policy.step_ms),result.reason,
                                   'recorded_canonical_observation',px,p['id']))
                        closed.append(p['id'])
                        break
        return closed
