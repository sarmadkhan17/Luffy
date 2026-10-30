"""Start exact approved books from the current volume scan, through Risk.

No promotion, account-wide history acquisition or separate order loop. The
durable caller persists the intent before its existing dispatcher can submit.
"""
import json
import math
import time

from . import coordinated_authority as CA, entry_authority as A, coordinated_unwind as U
from .coordinated_live import LiveBooks, TABLE
from ..attention_admission import verify_receipt
from ..core.types import TF_MS, Position, Side
from ..research.closure import digest
from ..research.compose import hedge as H
from ..strategy import factory_handoff as F, exit_policy as E


def candidates(journal):
    """The projection selects work; exact install/authority authenticates it."""
    if not journal.query("SELECT 1 FROM sqlite_master WHERE type='table' AND name='strategy_versions'"):
        return []
    rows=journal.query("SELECT id FROM strategies WHERE kind='coordinated_spec' AND state='active' ORDER BY id LIMIT 65")
    if len(rows)>64:raise ValueError('coordinated_active_version_bound')
    out=[]
    for row in rows:
        install=F.resolve_current_install(journal,row['id'])
        version=F.load_version(journal,install['version_id'])
        if version['strategy_id']!=row['id']:raise ValueError('coordinated_active_projection_differs')
        if CA.support_block(version) is None:out.append(version)
    return out


def admission(receipt,at_ms):
    value=verify_receipt(receipt)
    if (value.get('policy_version')!='volume-crypto.v1'
            or not 0<=at_ms-value['source_cut_ms']<=60_000
            or len(value['admitted_symbols'])>30):
        raise ValueError('coordinated_entry_shortlist_unavailable')
    return value


def capital(executor,version,bar):
    """Shrink the model to Risk's allocated size using current reconciled truth.

    This is preparation, not an EntryPermission. Dispatch rechecks everything
    against the actual quote, current book and current account before sending.
    """
    hs=version['lineage']['research']['multi_leg']['hedge_spec'];symbol=hs['primary']['symbol']
    manager=executor.risk_manager
    if float(A.positive(hs['leverage']))!=float(A.positive(manager.leverage)):
        raise ValueError('coordinated_account_leverage_differs')
    cap=A.capability(executor.journal,symbol,int(time.time()*1000),manager.leverage,
                     'long' if bar['signal']>0 else 'short')
    view,_,rows,available=A.authoritative_inputs(executor.journal,cap)
    held=[]
    for row in rows:
        fields={k:row[k] for k in Position.__dataclass_fields__ if k in row}
        fields['side']=Side(row['side']);held.append(Position(**fields))
    q=bar['quotes']['primary'];price=q['ask'] if bar['signal']>0 else q['bid']
    native=bar['primary_exit_observation'];atr=native['atr']
    from ..strategy.spec import ExitSpec
    policy,_=E.initialize(ExitSpec(**version['spec']['exit']),native['close'],atr,
        'long' if bar['signal']>0 else 'short',native['bar_ms'],native['step_ms'])
    sizing=manager.check_entry(A.persisted_state(executor.journal),symbol,price,atr,
        abs(price-policy.stop)/price,held,view['equity'],int(executor.journal.query(
            "SELECT COUNT(*) n FROM trades WHERE status='closed'")[0]['n']),'futures')
    if not sizing.ok:raise ValueError('coordinated_entry_risk:'+sizing.reason)
    allocation=F.live_allocation(executor.journal,version['strategy_id'],executor.cfg)
    if not allocation or allocation['version_id']!=version['version_id']:
        raise ValueError('coordinated_entry_allocation_missing')
    fraction=float(A.positive(allocation['fraction']))
    pct=float(A.positive(hs['sizing']['primary_notional_pct']))/100.
    amount=float(A.allocated(sizing.amount,fraction))
    result=min(view['equity']*fraction,amount*bar['p']/pct,
               float(available)*manager.leverage*bar['p']/price/pct)
    if not math.isfinite(result) or result<=0:raise ValueError('coordinated_entry_capital_unavailable')
    return result


def run_once(executor,feed,receipt):
    """Called after a completed scan, within the existing execution lock."""
    versions=candidates(executor.journal)
    if not versions:return []  # no schema writes, quotes or account reads
    admitted=admission(receipt,int(time.time()*1000));out=[]
    from . import coordinated_trials as inputs
    live=LiveBooks(executor)
    for version in versions:
        vid=version['version_id'];hs=version['lineage']['research']['multi_leg']['hedge_spec']
        if hs['primary']['symbol'] not in admitted['admitted_symbols']:continue
        try:
            with executor._entry_lock:
                active=executor.journal.query("SELECT payload FROM "+TABLE+
                    " WHERE json_extract(payload,'$.status')='OPEN' ORDER BY group_id LIMIT 3")
                groups={json.loads(row['payload'])['group_id'] for row in active}
                groups.update(gid for row in executor.journal.open_trades() if (gid:=U.owned_group(row)))
                if len(groups)>=2:break
                if executor.recovery_pending():break
                if any(json.loads(row['payload'])['identity']['version_id']==vid for row in active):continue
                # Native cut is part of the durable identity: a close/retry
                # cannot turn the same signal into another economic position.
                at=int(time.time()*1000);step=TF_MS[version['spec']['timeframe']];cut=at//step*step
                install=F.verify_install(executor.journal,version)
                gid='cgbook_'+digest([vid,install['install_id'],cut])
                if executor.journal.query('SELECT 1 FROM '+TABLE+' WHERE group_id=?',(gid,)):continue
                with CA.execution_scope(executor.journal,executor.cfg,vid,group_id=gid,leg='primary',action_id='intake:'+gid):
                    first=install.get('first_bar_ms')
                    if first is None:first=((install['installed_at_ms']+step-1)//step)*step
                    from ..data import market_provenance as mp
                    with mp.cache_only():
                        raw=inputs.prospective_observation(feed,executor.journal,version,
                            {'install':dict(install,first_bar_ms=first)},dict(spec=hs),at)
                    admission(receipt,raw['decision_ms'])  # slow reads cannot revive an old scan
                    if raw['signal_cut_ms']!=cut:raise ValueError('coordinated_entry_signal_cut_differs')
                    if not raw['signal'] or raw['native_bar']<0:continue
                    raw['equity']=capital(executor,version,raw)
                    raw['ratio']=raw['ratio'] if raw['ratio'] is not None else float('nan')
                    for leg,key in [('primary','prim_c'),('hedge','hedge_c')]:
                        raw[key]=H.Contract(hs[leg]['contract'],hs[leg]['multiplier'])
                    book=H.HedgeBook(hs,primary_exit_definition=version['spec']['exit'])
                    book.step(raw['native_bar'],raw)
                    if not book.pending:continue
                    identity=dict(status='VERIFIED',strategy_id=version['strategy_id'],version_id=vid,
                        spec_hash=version['spec_hash'],spec_sha256=version['spec_hash'],install_id=install['install_id'],
                        exit_semantics_id=H.BOOK_EXIT_SEMANTICS_ID,exit_state=book.primary_exit_state,
                        coordinated_instruction_observed_ms=raw['decision_ms'],
                        coordinated=dict(group_id=gid,leg='primary',management_policy=hs['management'],
                            allocated_equity_usdt=raw['equity'],maintenance_fraction=hs['maint_margin']),
                        coordinated_admission_receipt=admitted['receipt_id'],signal_cut_ms=cut)
                    live.register(book,identity,prepared_ms=raw['decision_ms'],
                                  native_observation=raw.get('native_observation'))
                    # No send here: the existing position worker drains the
                    # persisted intent, with a fresh quote and actual Risk.
                    out.append(dict(version_id=vid,group_id=gid,status='INTENT_REGISTERED',
                                    pending=len(book.pending),fill_claim=False))
        except Exception as exc:
            out.append(dict(U._unavailable(None,exc),version_id=vid))
    return out
