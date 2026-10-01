"""Recorded observations for probation replay of the existing exit contract.

No legacy evidence is inferred. The digest detects conflicting records; replay
establishes internal consistency, not external market-data authenticity.
"""
import hashlib
import json
import math
from dataclasses import asdict
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
    for raw in observations:
        if not isinstance(raw, dict) or set(raw) != {'bar_ms', 'open', 'high', 'low', 'close', 'completed'}:
            raise ValueError('canonical_exit_observations_invalid')
        observation = E.Observation(**raw)
        if (state.reason or type(observation.bar_ms) is not int
                or observation.bar_ms != state.last_bar_ms + policy.step_ms
                or observation.completed is not True):
            raise ValueError('canonical_exit_observations_invalid')
        result = E.advance(policy, state, observation, amount)
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
    if not initial.last_bar_ms + policy.step_ms <= opened <= closed or closed != state.last_bar_ms + policy.step_ms:
        raise ValueError('canonical_exit_clock_differs')
    return row['canonical_sha256']
