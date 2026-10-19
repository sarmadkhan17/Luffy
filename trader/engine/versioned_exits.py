"""Closed-bar adapters for the pure Factory exit contract.

Venue protection, emergency Risk and recovery may act independently between
observations. These receipts record STRATEGY intent, never simulated fills.
"""
from __future__ import annotations

import json

from ..core.types import TF_MS, closed_bars
from ..strategy import exit_policy as E


def bar_ms(ts):
    return int(ts.timestamp()*1000) if hasattr(ts, 'timestamp') else int(ts)


def observations(spec, snap, state):
    from datetime import datetime
    at = int(datetime.fromisoformat(snap.ts).timestamp()*1000)
    frame = closed_bars(snap.df(spec.timeframe), spec.timeframe, at)
    if frame is None:
        return
    if frame.attrs.get('read_mode') == 'retrospective':
        raise ValueError('retrospective_observations_have_no_execution_authority')
    signal, signal_valid = None, None
    if spec.exit.signal_exit:
        from ..strategy.compile import compile_spec, _CTX_TF
        from ..data.market_provenance import eligible_frame
        frames = {}
        for key, raw in snap.dfs.items():
            tf = key if key in TF_MS else _CTX_TF.get(key)
            if raw is None or tf is None:
                continue
            eligible = eligible_frame(raw, tf, at)
            if eligible is not None:
                frames[key] = closed_bars(eligible, tf, at)
        frame = frames.get(spec.timeframe)
        if frame is None or not len(frame):
            raise ValueError('exit_signal_evidence_unavailable')
        signal, signal_valid = compile_spec(spec).exit_signal_detail(frames,
            btc={'15m': frames['BTC_1h']} if 'BTC_1h' in frames else None,
            derivs=getattr(snap, 'derivs', None), universe=getattr(snap, 'universe', None),
            market=getattr(snap, 'market', None), symbol=snap.symbol,
            world=getattr(snap, 'world', None))
    atr = (E.trail_atr_series(frame, TF_MS[spec.timeframe]).to_numpy(float)
           if spec.exit.trail.get('kind') == 'atr' else None)
    for i, (_, row) in enumerate(frame.iterrows()):
        ms = bar_ms(row['ts'])
        if ms > state.last_bar_ms:
            usable = (row.get('quality', 'VALID') == 'VALID'
                      and row.get('bar_state', 'FINAL') == 'FINAL')
            # Consume existing receipt cuts; never infer historical availability
            # from the candle's event timestamp. D6 owns those source receipts.
            for key in ('available_at_ms', 'observed_at_ms'):
                if key in row:
                    usable = usable and bool(row[key] <= at)
            yield E.Observation(ms, float(row['open']), float(row['high']),
                                float(row['low']), float(row['close']),
                                completed=bool(usable),
                                atr=float(atr[i]) if atr is not None else None,
                                signal_exit=bool(signal[i]) if signal is not None and signal_valid[i] else None)


def entry_contract(spec, snap, signal_close_ms):
    from datetime import datetime
    from ..agents.indicators import atr
    at = int(datetime.fromisoformat(snap.ts).timestamp()*1000)
    frame = closed_bars(snap.df(spec.timeframe), spec.timeframe, at)
    if frame is None or not len(frame):
        raise ValueError('exit_closed_entry_frame_unavailable')
    ms = bar_ms(frame['ts'].iloc[-1])
    if signal_close_ms != ms + TF_MS[spec.timeframe]:
        raise ValueError('exit_signal_bar_mismatch')
    return float(frame['close'].iloc[-1]), float(atr(frame)), ms


class LiveExitAdapter:
    """Persist intent BEFORE submission; failed/partial fills retry remainder.

    No current strategy registry may substitute for the trade's exact frozen
    identity. Missing receipts/state refuse STRATEGY management; the existing
    protective/Risk/recovery paths stay available.
    """
    def __init__(self, journal, executor):
        self.journal, self.executor = journal, executor
        with journal._tx() as c:
            c.execute('CREATE TABLE IF NOT EXISTS versioned_live_exit_states('
                      'trade_id TEXT PRIMARY KEY, version_id TEXT NOT NULL, '
                      'install_id TEXT NOT NULL, spec_hash TEXT NOT NULL, '
                      'exit_semantics_id TEXT NOT NULL, state_json TEXT NOT NULL)')

    def manage(self, trade, snap):
        trade['_execution_reference'] = dict(price=snap.price,observed_at=snap.ts,basis='exit_decision_snapshot_price')
        from ..strategy import factory_handoff as F
        from ..strategy.spec import StrategySpec
        identity = json.loads(trade.get('entry_identity_json') or '{}')
        if identity.get('status') != 'VERIFIED' or identity.get('exit_semantics_id') != E.EXIT_SEMANTICS_ID:
            raise ValueError('live_exit_identity_unbound')
        version = F.load_version(self.journal, identity['version_id'])
        F.verify_exit_binding(self.journal, version)
        install = F.verify_install(self.journal, version, current=False)
        if (trade['strategy_id'], identity.get('strategy_id'), identity.get('spec_sha256'), identity.get('install_id')) != (
                version['strategy_id'], version['strategy_id'], version['spec_hash'], install['install_id']):
            raise ValueError('live_exit_identity_differs')
        spec = StrategySpec.from_dict(version['spec'])
        bad = E.unsupported(spec.exit)
        if bad:
            raise ValueError('unsupported_versioned_exit:' + bad)
        with self.journal._tx() as c:
            c.execute('BEGIN IMMEDIATE')
            row = c.execute('SELECT * FROM versioned_live_exit_states WHERE trade_id=?', (trade['id'],)).fetchone()
            initial = identity.get('exit_state')
            if not initial:
                raise ValueError('live_exit_state_missing')
            policy, state = E.decode(row['state_json'] if row else initial)
            expected, _ = E.decode(initial)
            if policy != expected or policy.side != trade['side']:
                raise ValueError('live_exit_policy_differs')
            if row and (row['version_id'], row['install_id'], row['spec_hash'], row['exit_semantics_id']) != (
                    version['version_id'], install['install_id'], version['spec_hash'], E.EXIT_SEMANTICS_ID):
                raise ValueError('live_exit_state_identity_differs')
            result = None
            if state.reason:
                result = E.advance(policy, state, E.Observation(state.last_bar_ms,0,0,0,0), float(trade['amount']))
            else:
                for observation in observations(spec, snap, state):
                    result = E.advance(policy, state, observation, float(trade['amount']))
                    state = result.state
                    if result.due:
                        break
            c.execute('INSERT OR REPLACE INTO versioned_live_exit_states VALUES(?,?,?,?,?,?)',
                      (trade['id'],version['version_id'],install['install_id'],version['spec_hash'],
                       E.EXIT_SEMANTICS_ID,E.encode(policy,state)))
        if result and result.due:
            trade['_strategy_exit_evidence'] = {
                'authority': 'STRATEGY', 'exit_semantics_id': E.EXIT_SEMANTICS_ID,
                'version_id': version['version_id'], 'install_id': install['install_id'],
                'spec_hash': version['spec_hash'], 'intent': result.reason,
                'intended_quantity': result.quantity, 'state': json.loads(E.encode(policy,state)),
            }
            if result.reason == 'signal':
                # Intent is durable, but no D6 executable-price interface has
                # been reconciled yet. Never submit at the signal close.
                raise ValueError('signal_exit_execution_timing_requires_D6')
            if self.executor.close(trade, exit_price_hint=snap.price, reason=result.reason):
                return result.reason
        return None
