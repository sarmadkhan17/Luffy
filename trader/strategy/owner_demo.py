"""Explicit owner permission for an exact existing strategy on Binance Demo.

This is not a Backtest PASS, Factory promotion or real-money permission.
Risk, controls, entry provenance and protective execution still apply.
"""
import hashlib
import json
import time

from ..core.config import Env
from ..engine.state import OWNER_ACTORS

TABLE = 'strategy_owner_demo_versions'
SCHEMA = 'owner-demo-strategy-version.v1'


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def installed_definition(row):
    """Bind the existing spec, or the owner's legacy EMA rules and evaluator."""
    if row['kind'] == 'spec':
        from .factory_handoff import _frozen_spec
        from .spec import StrategySpec
        spec, spec_hash = _frozen_spec(StrategySpec.from_json(row['spec_json']))
        return dict(spec=spec), spec_hash
    if row['kind'] != 'ema_trend':
        raise ValueError('owner_demo_unsupported_legacy_family')
    import inspect
    from .library import eval_ema_trend
    from .genome import Genome
    params = json.loads(row['params'])
    genome = Genome(row['id'], row['kind'], row['hypothesis'], row['invalidation'],
                    frozenset(json.loads(row['regime_filter'])),
                    frozenset(json.loads(row['markets'])), dict(params))
    if Genome.validate(genome) or genome.params != params:
        raise ValueError('owner_demo_invalid_legacy_definition')
    definition = {k: row[k] for k in ('id', 'kind', 'params', 'regime_filter', 'markets')}
    definition['evaluator_sha256'] = hashlib.sha256(inspect.getsource(eval_ema_trend).encode()).hexdigest()
    return dict(legacy_definition=definition), digest(definition)


def rows(journal, strategy_id):
    if not journal.query("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (TABLE,)):
        return []
    return journal.query(f'SELECT canonical_json FROM {TABLE} WHERE strategy_id=?', (strategy_id,))


def block(journal, strategy_id, cfg):
    """None only for explicit owner permission matching this exact active spec."""
    from .factory_handoff import _frozen_spec
    from .spec import StrategySpec
    try:
        grants = rows(journal, strategy_id)
        if len(grants) != 1:
            return 'OWNER_DEMO_AUTHORITY_MISSING'
        if Env.get('BINANCE_DEMO', '').lower() not in ('true', '1', 'yes'):
            return 'OWNER_DEMO_ENVIRONMENT_REQUIRED'
        if (cfg or {}).get('mode') != {'market': 'futures', 'execution': 'live'}:
            return 'OWNER_DEMO_EXECUTION_CONFIGURATION_REQUIRED'
        grant = json.loads(grants[0]['canonical_json'])
        body = {k: v for k, v in grant.items() if k != 'version_id'}
        if (grant.get('version_id') != digest(body) or grant.get('schema') != SCHEMA
                or grant.get('scope') != 'BINANCE_DEMO_ONLY'
                or grant.get('actor') not in OWNER_ACTORS
                or grant.get('validation') != 'NOT_ASSERTED_OWNER_AUTHORIZED'
                or grant.get('strategy_id') != strategy_id
                or type(grant.get('authorized_at_ms')) is not int
                or not 0 < grant['authorized_at_ms'] <= int(time.time()*1000)+5000
                or not grant.get('authorization_reason')):
            return 'OWNER_DEMO_RECORD_INVALID'
        installed = journal.query('SELECT * FROM strategies WHERE id=?', (strategy_id,))
        if len(installed) != 1 or installed[0]['state'] != 'active':
            return 'OWNER_DEMO_STRATEGY_NOT_ACTIVE'
        definition, spec_hash = installed_definition(installed[0])
        if grant.get('spec_hash') != spec_hash or any(grant.get(k) != v for k, v in definition.items()):
            return 'OWNER_DEMO_DEFINITION_CHANGED'
        return None
    except (ValueError, KeyError, TypeError, OSError):
        return 'OWNER_DEMO_RECORD_INVALID'


def authorize(journal, cfg, strategy_id, *, actor, reason, at_ms):
    """Owner-only maintenance operation. Records unvalidated demo permission honestly."""
    from .factory_handoff import _frozen_spec, versioned
    from .spec import StrategySpec
    if actor not in OWNER_ACTORS or not reason or type(at_ms) is not int or abs(at_ms-int(time.time()*1000)) > 5000:
        raise ValueError('owner_demo_explicit_current_authorization_required')
    if Env.get('BINANCE_DEMO', '').lower() not in ('true', '1', 'yes') or cfg.get('mode') != {'market':'futures','execution':'live'}:
        raise ValueError('owner_demo_environment_required')
    if journal.kv_get('control_state', None) != 'FROZEN' or journal.kv_get('macro_guard_operator_hold', None) != '1':
        raise ValueError('owner_demo_frozen_maintenance_required')
    if versioned(journal, strategy_id):
        raise ValueError('owner_demo_cannot_replace_factory_version')
    installed = journal.query('SELECT * FROM strategies WHERE id=?', (strategy_id,))
    if len(installed) != 1 or installed[0]['state'] not in ('paper','active','demoted'):
        raise ValueError('owner_demo_existing_strategy_required')
    definition, spec_hash = installed_definition(installed[0])
    body = dict(schema=SCHEMA,strategy_id=strategy_id,**definition,spec_hash=spec_hash,scope='BINANCE_DEMO_ONLY',
                actor=actor,authorized_at_ms=at_ms,authorization_reason=reason,validation='NOT_ASSERTED_OWNER_AUTHORIZED')
    record = dict(body, version_id=digest(body))
    with journal._tx() as db:
        db.execute(f'CREATE TABLE IF NOT EXISTS {TABLE} (strategy_id TEXT PRIMARY KEY, canonical_json TEXT NOT NULL)')
        for op in ('UPDATE','DELETE'):
            db.execute(f"CREATE TRIGGER IF NOT EXISTS owner_demo_no_{op.lower()} BEFORE {op} ON {TABLE} BEGIN SELECT RAISE(ABORT,'owner demo versions are immutable'); END")
        old = db.execute(f'SELECT canonical_json FROM {TABLE} WHERE strategy_id=?',(strategy_id,)).fetchone()
        if old:
            previous=json.loads(old[0])
            if previous['spec_hash'] != spec_hash:
                raise ValueError('owner_demo_existing_version_differs')
            record=previous
        else:
            db.execute(f'INSERT INTO {TABLE}(strategy_id,canonical_json) VALUES (?,?)',(strategy_id,json.dumps(record,sort_keys=True,separators=(',',':'),allow_nan=False)))
        db.execute("UPDATE strategies SET state='active',state_changed_at=? WHERE id=?",(time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime(at_ms/1000)),strategy_id))
    return record


def venue_block(journal, strategy_id, exchange):
    """The actual submission client must also target Demo, independently of Env."""
    if not rows(journal, strategy_id):
        return None
    from ..observability.portfolio_observation import trading_source
    try:
        return None if trading_source(exchange)[0] == 'demo' else 'OWNER_DEMO_ACTUAL_VENUE_REQUIRED'
    except (ValueError, TypeError, AttributeError):
        return 'OWNER_DEMO_ACTUAL_VENUE_REQUIRED'


def startup_allowed(journal, cfg):
    """A frozen Demo startup may contain only explicitly owner-authorized active specs."""
    try:
        active = journal.query("SELECT id FROM strategies WHERE state='active'")
        return bool(active) and all(block(journal, row['id'], cfg) is None for row in active)
    except (ValueError, KeyError, TypeError, OSError):
        return False
