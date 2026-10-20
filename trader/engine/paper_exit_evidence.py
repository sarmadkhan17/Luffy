"""Recorded observations for probation replay of the existing exit contract.

No legacy evidence is inferred. The digest detects conflicting records; replay
establishes internal consistency, not external market-data authenticity.
"""
import hashlib
import json
import math
from dataclasses import asdict, replace
from datetime import datetime

from ..core.types import TF_MS
from ..strategy import exit_policy as E
from ..strategy.spec import StrategySpec

TABLE = 'versioned_paper_exit_evidence'


def canonical(body):
    return json.dumps(body, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def ensure(db):
    db.execute(f'CREATE TABLE IF NOT EXISTS {TABLE}('
               'trade_id TEXT PRIMARY KEY, canonical_json TEXT NOT NULL, '
               'canonical_sha256 TEXT NOT NULL)')


def start(trade_id, identity, policy, state, quantity):
    return dict(trade_id=trade_id, strategy_id=identity['strategy_id'],
                version_id=identity['version_id'], install_id=identity['install_id'],
                spec_hash=identity['spec_sha256'], exec_mode='paper',
                exit_semantics_id=policy.semantics_id,
                entry_identity_sha256=digest(json.dumps(identity, sort_keys=True)),
                initial=E.encode(policy, state), final=E.encode(policy, state),
                quantity=quantity, observations=[])


def record(db, body):
    text = canonical(body)
    db.execute(f'INSERT OR REPLACE INTO {TABLE} VALUES(?,?,?)',
               (body['trade_id'], text, digest(text)))


def observe(body, policy, result, observation):
    body['observations'].append(asdict(observation))
    body['final'] = E.encode(policy, result.state)


def advance(policy, state, observation, quantity, *, pending=False):
    """Keep paper signal intent separate from the continuing shared policy.

    The pure policy's terminal signal result remains unchanged for Live. Paper
    advances the surviving bar with signal disabled, including max_bars and
    the declared NEXT-bar trail; protection always precedes a new decision.
    """
    if pending:
        return E.advance(replace(policy, signal_exit=False), state, observation, quantity), False
    result = E.advance(policy, state, observation, quantity)
    if result.reason != 'signal':
        return result, False
    return E.advance(replace(policy, signal_exit=False), state, observation, quantity), True


def _receipt_rows(frame):
    from ..data import market_provenance as P
    # Explicit fields keep pandas/numpy values and NaNs out of canonical JSON.
    keys = (*P.VALUES, *P.META)
    rows = []
    for _, row in frame.iterrows():
        raw = {k: row[k] for k in keys if k in row}
        raw['ts'] = int(row['ts'].timestamp()*1000) if hasattr(row['ts'], 'timestamp') else int(row['ts'])
        from .trade_provenance import clean
        rows.append({key: clean(value) for key, value in raw.items()})
    return rows


def _identity(body, kind, payload):
    return digest(canonical(dict(kind=kind, trade_id=body['trade_id'],
        version_id=body['version_id'], spec_hash=body['spec_hash'],
        install_id=body['install_id'], payload=payload)))


def decision(body, spec, snap, observation, *, legacy=False):
    """Freeze actual D6 lookback receipts, never reconstruct availability."""
    from ..strategy import fill_timing as D6
    window = D6.lookback_bars(spec)
    frame = snap.df(spec.timeframe)
    cut = int(datetime.fromisoformat(snap.ts).timestamp()*1000)
    stamps = frame['ts'].map(lambda t: int(t.timestamp()*1000) if hasattr(t, 'timestamp') else int(t))
    inputs = frame.loc[stamps <= observation.bar_ms].tail(window)
    raw = dict(bar_ms=observation.bar_ms, observed_at_ms=cut, lookback_bars=window,
               inputs=[] if legacy else _receipt_rows(inputs))
    if legacy:
        # Old D7 observations omitted their source receipts. Recover protection
        # without pretending today's revision proves the old decision's inputs.
        raw['availability'] = 'UNAVAILABLE_LEGACY_DECISION_RECEIPTS'
    raw['decision_id'] = _identity(body, 'signal_decision', raw)
    return raw


def _d6_candidate(decision, candidates):
    """Replay the existing D6 timing rule on frozen receipts, with no fallback."""
    import pandas as pd
    from ..strategy import fill_timing as D6
    inputs = decision['inputs']
    if len(inputs) != decision['lookback_bars'] or not inputs or inputs[-1]['ts'] != decision['bar_ms']:
        return None
    rows = inputs + candidates
    required = ('available_at_ms', 'observed_at_ms', 'revision_id', 'content_hash', 'quality')
    if any(any(r.get(k) is None for k in required) for r in inputs):
        return None
    if any(r['quality'] != 'VALID' or r.get('bar_state') != 'FINAL' for r in inputs):
        return None
    stamps = [r['ts'] for r in rows]
    if any(b <= a for a, b in zip(stamps, stamps[1:])):
        return None
    if any(r['available_at_ms'] > decision['observed_at_ms'] or r['observed_at_ms'] > decision['observed_at_ms'] for r in inputs):
        return None
    df = pd.DataFrame(rows)
    df['ts'] = pd.to_datetime(df['ts'], unit='ms', utc=True)
    df.attrs['signal_lookback_bars'] = decision['lookback_bars']
    fill, _ = D6.fills(df)
    index = int(fill[len(inputs)-1])
    if index < len(inputs):
        return None
    row = rows[index]
    retained = candidates[:index-len(inputs)+1]
    proven = inputs + retained
    if any(any(r.get(k) is None for k in required) for r in proven):
        return None
    # Verify genuine receipt identities with the existing market-data sealer;
    # do not bless modified clocks/prices by writing new receipt hashes.
    from ..data import market_provenance as P
    if any(not all(k in r for k in P.META) or r['event_time_ms'] != r['ts']
           or r['instrument_id'] != inputs[-1]['instrument_id'] or r['kind'] != 'candle'
           for r in proven):
        return None
    try:
        sealed = P.seal(pd.DataFrame(proven))
        if any(sealed.iloc[i][k] != r[k] for i, r in enumerate(proven)
               for k in ('content_hash', 'revision_id')):
            return None
    except (KeyError, TypeError, ValueError, OverflowError):
        return None
    # A recorded open is usable only once its receipt is actually observed.
    # PARTIAL/INCOMPLETE is allowed for the open, never for decision inputs.
    if row['quality'] not in ('VALID', 'INCOMPLETE') or row.get('bar_state') not in ('FINAL', 'PARTIAL'):
        return None
    if not math.isfinite(float(row['open'])) or row['open'] <= 0:
        return None
    return row, retained


def executable_fill(body, spec, snap):
    """First D6 executable retained open, distinct from the signal decision."""
    pending = body.get('signal_decision')
    if not pending:
        return None
    frame = snap.df(spec.timeframe)
    if frame is None or frame.attrs.get('read_mode') == 'retrospective':
        return None
    cut = int(datetime.fromisoformat(snap.ts).timestamp()*1000)
    candidates = [r for r in _receipt_rows(frame) if pending['bar_ms'] < r['ts'] <= cut]
    found = _d6_candidate(pending, candidates)
    if found is None:
        return None
    row, retained = found
    if any(r['available_at_ms'] > cut or r['observed_at_ms'] > cut for r in retained):
        return None
    raw = dict(decision_id=pending['decision_id'], bar_ms=row['ts'], price=row['open'],
               observed_at_ms=cut, candidates=retained, policy='next-executable-open.v2')
    raw['fill_id'] = _identity(body, 'signal_fill', raw)
    return raw


def fill_transition(policy, state, fill, quantity):
    """Existing protection at this OPEN precedes signal execution.

    Do not inspect the fill candle's future range or pretend it completed.
    last_bar_ms/bars_held remain the last genuinely completed observation.
    """
    return E.execute_open(policy, state, fill['bar_ms'], fill['price'], quantity)


def verify(journal, trade, version, install):
    """Return the verified digest, or refuse without rewriting historical data."""
    identity = json.loads(trade['entry_identity_json'])
    expected_id = install['exit_semantics_id']
    if (identity.get('exit_semantics_id') != expected_id
            or expected_id != E.EXIT_SEMANTICS_ID
            or version['evidence_ids'].get('exit_semantics_id') != expected_id
            or ('exit_semantics_id' in trade and trade['exit_semantics_id'] != expected_id)):
        raise ValueError('trade_exit_semantics_unbound')
    if not journal.query("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (TABLE,)):
        raise ValueError('canonical_exit_evidence_missing')
    rows = journal.query(f'SELECT * FROM {TABLE} WHERE trade_id=?', (trade['id'],))
    if len(rows) != 1:
        raise ValueError('canonical_exit_evidence_missing')
    row = rows[0]
    body = json.loads(row['canonical_json'])
    if canonical(body) != row['canonical_json'] or digest(row['canonical_json']) != row['canonical_sha256']:
        raise ValueError('canonical_exit_evidence_digest_differs')
    binding = dict(trade_id=trade['id'], strategy_id=version['strategy_id'],
                   version_id=version['version_id'], install_id=install['install_id'],
                   spec_hash=version['spec_hash'], exec_mode='paper',
                   exit_semantics_id=expected_id,
                   entry_identity_sha256=digest(trade['entry_identity_json']))
    if any(body.get(k) != v for k, v in binding.items()):
        raise ValueError('canonical_exit_evidence_binding_differs')
    policy, initial = E.decode(body['initial'])
    if body['initial'] != identity.get('exit_state'):
        raise ValueError('canonical_exit_entry_differs')
    spec = StrategySpec.from_dict(version['spec'])
    expected_policy, expected_initial = E.initialize(spec.exit, policy.reference,
        policy.entry_atr, trade['side'], initial.last_bar_ms, TF_MS[spec.timeframe])
    if body['initial'] != E.encode(expected_policy, expected_initial):
        raise ValueError('canonical_exit_geometry_differs')
    observations = body['observations']
    if not isinstance(observations, list) or not 1 <= len(observations) <= policy.max_bars:
        raise ValueError('canonical_exit_observations_invalid')
    amount = float(trade['amount'])
    quantity = body.get('quantity')
    if (not math.isfinite(amount) or amount <= 0 or isinstance(quantity, bool)
            or not isinstance(quantity, (int, float)) or quantity != amount):
        raise ValueError('canonical_exit_quantity_invalid')
    state = initial
    pending = body.get('signal_decision')
    seen_signal = False
    for raw in observations:
        if not isinstance(raw, dict) or set(raw) != {'bar_ms', 'open', 'high', 'low', 'close', 'completed', 'atr', 'signal_exit'}:
            raise ValueError('canonical_exit_observations_invalid')
        observation = E.Observation(**raw)
        if (state.reason or type(observation.bar_ms) is not int
                or observation.bar_ms != state.last_bar_ms + policy.step_ms
                or observation.completed is not True):
            raise ValueError('canonical_exit_observations_invalid')
        if body.get('paper_signal_execution'):
            result, decided = advance(policy, state, observation, amount, pending=seen_signal)
            if decided:
                if seen_signal or not pending or pending['bar_ms'] != observation.bar_ms:
                    raise ValueError('canonical_signal_decision_differs')
                raw_decision = dict(pending)
                decision_id = raw_decision.pop('decision_id')
                from ..strategy import fill_timing as D6
                if (decision_id != _identity(body, 'signal_decision', raw_decision)
                        or pending['lookback_bars'] != D6.lookback_bars(spec)):
                    raise ValueError('canonical_signal_decision_differs')
                seen_signal = True
        else:
            result = E.advance(policy, state, observation, amount)
        state = result.state
    execution = body.get('signal_fill')
    if body.get('paper_signal_execution'):
        if bool(pending) != seen_signal:
            raise ValueError('canonical_signal_decision_differs')
        if execution:
            raw_fill = dict(execution)
            fill_id = raw_fill.pop('fill_id')
            if (not pending or state.reason not in (None, 'time') or execution['decision_id'] != pending['decision_id']
                    or execution['policy'] != 'next-executable-open.v2'
                    or fill_id != _identity(body, 'signal_fill', raw_fill)):
                raise ValueError('canonical_signal_fill_differs')
            found = _d6_candidate(pending, execution['candidates'])
            if (found is None or found[0]['ts'] != execution['bar_ms']
                    or found[0]['open'] != execution['price']
                    or any(r['observed_at_ms'] > execution['observed_at_ms']
                           or r['available_at_ms'] > execution['observed_at_ms']
                           for r in execution['candidates'])):
                raise ValueError('canonical_signal_fill_differs')
            result = fill_transition(policy, state, execution, amount)
            state = result.state
    if not result.due or body['final'] != E.encode(policy, state):
        raise ValueError('canonical_exit_transition_differs')
    if trade['status'] != 'closed' or trade['close_reason'] != result.reason:
        raise ValueError('canonical_exit_reason_differs')
    for name, value in (('stop_loss', policy.stop), ('take_profit', policy.target),
                        ('exit_price', state.reference_exit),
                        ('initial_risk', policy.initial_r), ('bars_held', state.bars_held),
                        ('last_bar_ms', state.last_bar_ms), ('entry_bar_ms', initial.last_bar_ms)):
        if name in trade and (trade[name] is None or not math.isclose(float(trade[name]), value, rel_tol=1e-10, abs_tol=1e-10)):
            raise ValueError('canonical_exit_final_state_differs:' + name)
    if 'exit_state_json' in trade and trade['exit_state_json'] != body['final']:
        raise ValueError('canonical_exit_final_state_differs')
    opened = int(datetime.fromisoformat(trade['opened_at']).timestamp()*1000)
    closed = int(datetime.fromisoformat(trade['closed_at']).timestamp()*1000)
    expected_close = execution['bar_ms'] if execution else state.last_bar_ms + policy.step_ms
    if not initial.last_bar_ms + policy.step_ms <= opened <= closed or closed != expected_close:
        raise ValueError('canonical_exit_clock_differs')
    return row['canonical_sha256']
