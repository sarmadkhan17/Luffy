"""Exact coordinated caller scope; all existing live/Risk gates still apply.

This scope removes only the single-leg code-path refusal. It cannot qualify,
approve, activate or deploy a strategy and cannot authorize an order by itself.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path

_CONTEXT=ContextVar('coordinated_execution_scope',default=None)
# The scan registers durable intents; the positions worker dispatches them.
# Only supported linear USDT books can use that caller. This
# code capability supplies no qualification, installation or live authority.
RUNTIME_READY=True


@dataclass(frozen=True)
class _Scope:
    journal_path:str
    version_id:str
    strategy_id:str
    spec_hash:str
    install_id:str
    group_id:str
    leg:str
    action_id:str
    symbol:str


def support_block(version):
    from ..strategy import factory_handoff as F
    from ..research.compose import hedge as H
    multi=((version.get('lineage') or {}).get('research') or {}).get('multi_leg')
    if not multi:return None
    if not RUNTIME_READY or version.get('source',{}).get('kind')!=F.HEDGE_HQ_KIND:
        return 'multi_leg_requires_coordinated_execution'
    return H.execution_support_block(multi['hedge_spec'])


def matches(version):
    scope=_CONTEXT.get()
    return bool(scope and scope.version_id==version['version_id'] and scope.spec_hash==version['spec_hash']
                and scope.strategy_id==version['strategy_id'] and support_block(version) is None)


@contextmanager
def execution_scope(journal,cfg,version_id,*,group_id,leg,action_id):
    from ..strategy import factory_handoff as F
    if leg not in ('primary','hedge') or not all(isinstance(v,str) and v for v in (group_id,action_id)):
        raise ValueError('coordinated_action_identity_required')
    version=F.load_version(journal,version_id)
    blocked=support_block(version)
    if blocked:raise ValueError(blocked)
    # Re-authenticates the registered method, calibrated protected evidence,
    # evaluator and configured cost policy. Development-only paper cannot pass.
    F.verify_validation(journal,version)
    install=F.verify_install(journal,version)
    hs=version['lineage']['research']['multi_leg']['hedge_spec']
    scope=_Scope(str(Path(journal.db_path).resolve()),version_id,version['strategy_id'],version['spec_hash'],
                 install['install_id'],group_id,leg,action_id,hs[leg]['symbol'])
    token=_CONTEXT.set(scope)
    try:
        blocked=F.live_entry_block(journal,version['strategy_id'],cfg=cfg)
        if blocked:raise ValueError(blocked)
        yield version,install
    finally:_CONTEXT.reset(token)


def validate_identity(journal,strategy_id,identity,symbol,*,requested=None,reference=None):
    """Rechecked both when Risk issues and when Execution consumes permission."""
    if not isinstance(identity,dict) or not identity.get('coordinated') or not identity.get('version_id'):
        return  # existing non-Factory/grandfather rules remain at their boundary
    from ..strategy import factory_handoff as F
    from ..research.compose import hedge as H
    scope=_CONTEXT.get()
    if scope is None or identity['version_id']!=scope.version_id:
        raise ValueError('coordinated_execution_scope_mismatch')
    version=F.load_version(journal,identity['version_id'])
    group=identity['coordinated']
    if (not matches(version) or scope.journal_path!=str(Path(journal.db_path).resolve())
            or strategy_id!=scope.strategy_id or symbol!=scope.symbol
            or identity.get('status')!='VERIFIED' or identity.get('strategy_id')!=strategy_id
            or identity.get('spec_sha256')!=scope.spec_hash or identity.get('spec_hash')!=scope.spec_hash
            or identity.get('install_id')!=scope.install_id or identity.get('exit_semantics_id')!=H.BOOK_EXIT_SEMANTICS_ID
            or group.get('group_id')!=scope.group_id or group.get('leg')!=scope.leg
            or identity.get('coordinated_action_id')!=scope.action_id):
        raise ValueError('coordinated_execution_scope_mismatch')
    if requested is not None:
        from .coordinated_budget import verify
        verify(journal,version,identity,requested,reference)


def risk_book(journal, rows, identity, cap):
    """Count verified economic groups once; retain every leg in Risk heat.

    Only the current authenticated coordinated caller may add its owned
    opposite native side. This never exempts unrelated same-symbol exposure.
    """
    import json
    from . import entry_authority as A, position_mode as PM
    from .coordinated_close import IDENTITY_FIELDS
    current = None
    if (identity or {}).get('version_id') and (identity or {}).get('coordinated'):
        scope = _CONTEXT.get()
        if (scope is None or scope.version_id != identity['version_id']
                or scope.group_id != identity['coordinated']['group_id']):
            raise ValueError('coordinated_risk_scope_missing')
        current = (scope.group_id, scope.version_id, scope.install_id, cap['account_scope'])
    groups, siblings, existing, grouped = set(), set(), False, False
    for row in rows:
        root = PM.identity_of(row); group = root.get('coordinated') or {}
        binding = root.get('execution_binding') or {}
        if not group or not root.get('version_id'):
            groups.add(('trade', row['id'])); continue
        grouped = True
        request = journal.query('SELECT * FROM execution_requests WHERE logical_id=?', (binding.get('logical_id'),))
        if len(request) != 1:raise ValueError('coordinated_risk_owner_receipt_missing')
        saved = request[0]; intent = json.loads(saved['intent_json'])
        result = json.loads(saved.get('result_json') or '{}')
        if (root.get('status') != 'VERIFIED' or A.digest(intent) != saved['intent_hash']
                or saved['state'] != 'TERMINAL' or result.get('position_id') != row['id']
                or any(root.get(k) != (intent.get('strategy_identity') or {}).get(k) for k in IDENTITY_FIELDS)
                or intent.get('symbol') != row['symbol'] or intent.get('strategy_id') != row['strategy_id']
                or intent.get('instrument_id') != binding.get('instrument_id')
                or intent.get('capability') != binding.get('capability')
                or PM.position_side(root) != intent.get('position_side', 'BOTH')):
            raise ValueError('coordinated_risk_owner_receipt_differs')
        key = (group.get('group_id'), root.get('version_id'), root.get('install_id'),
               (binding.get('capability') or {}).get('account_scope'))
        if not all(key):raise ValueError('coordinated_risk_group_identity_missing')
        if cap.get('account_scope') and key[3] != cap['account_scope']:
            raise ValueError('coordinated_risk_group_account_differs')
        groups.add(key)
        if key != current:continue
        existing = True
        if (binding.get('instrument_id') == cap['instrument_id']
                and PM.position_side(root) in ('LONG', 'SHORT')
                and PM.position_side(identity) in ('LONG', 'SHORT')
                and PM.position_side(root) != PM.position_side(identity)
                and group.get('leg') != identity['coordinated'].get('leg')):
            siblings.add(row['id'])
    return (len(groups), frozenset(siblings), existing) if grouped or current is not None else None
