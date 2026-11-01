"""Bind shared position buying power to owned cashflows before an increase.

Existing Risk still governs account heat, allocation, actual leverage and
available venue margin. This module cannot approve or submit an order.
"""
import json
import math
import time
import zlib

from . import entry_authority as A,coordinated_management as M,coordinated_marked as Q
from ..research.compose import hedge as H,capital as K
from ..research.closure import canonical,digest

TABLE='coordinated_capital_receipts'
MAX_BYTES=M.MAX_EVIDENCE_BYTES


def calculate(proof,*,now_ms=None):
    """Replay the budget from the exact retained accounting observations."""
    hs=proof['hedge_spec'];identity=proof['identity'];ins=proof['instruction'];source=proof['source']
    if proof['policy_id']!=K.POLICY_ID:raise ValueError('coordinated_capital_policy_differs')
    allocated=identity['coordinated']['allocated_equity_usdt']
    positions={'primary':0.,'hedge':0.};marks={}
    if source['kind']=='INITIAL_ALLOCATION':
        equity=float(A.positive(allocated));gross=0.
        if equity>float(A.positive(source['account_equity'])):
            raise ValueError('coordinated_capital_exceeds_account')
    elif source['kind']=='OWNED_MARKED_POSITION':
        current=Q.assess(source['evidence'],now_ms=proof['observed_ms'] if now_ms is None else now_ms)
        if current['allocated_equity_usdt']!=allocated:
            raise ValueError('coordinated_capital_allocation_differs')
        equity=current['marked_equity_usdt'];gross=current['gross_notional_usdt']
        for part in source['evidence']['components']:
            root=part['identity'];trade=part['capture']['bookings']['trade'];leg=root['coordinated']['leg']
            if any(root.get(k)!=identity.get(k) for k in ('version_id','spec_hash','install_id')):
                raise ValueError('coordinated_capital_version_differs')
            if trade['status']=='open':
                positions[leg]+=float(trade['amount'])*(1 if trade['side']=='long' else -1)
                marks[leg]=float(A.positive(part['capture']['position']['markPrice']))
        if current['decision']['exit_reason']:
            # An existing whole-position exit takes priority over an increase.
            return dict(policy_id=K.POLICY_ID,quantity=0.,reason='coordinated_capital_exit_due')
    else:raise ValueError('coordinated_capital_source_unavailable')
    if any(not math.isclose(positions[k],proof['positions'][k],rel_tol=1e-10,abs_tol=1e-12) for k in positions):
        raise ValueError('coordinated_capital_owned_quantity_differs')
    leg=ins['leg'];price=float(A.positive(proof['reference']['price']))
    if positions[leg]:gross+=abs(positions[leg])*max(0.,price-marks[leg])
    quantity=H.pair_increase_quantity(hs,positions,dict(marks,**{leg:price}),leg,ins['qty'],proof.get('ratio'))
    return K.capacity(equity=equity,gross=gross,leverage=hs['leverage'],
        unit_notional=price,requested=quantity,cost_rate=(hs['fee_bps']+hs['slip_bps'])/10000.)


def prepare(executor,book,instruction,identity,reference):
    if not H.capital_enabled(book.spec):raise ValueError('coordinated_capital_policy_unavailable')
    if any(book.spec[k]['contract']!='linear' for k in ('primary','hedge')):
        raise ValueError('coordinated_capital_contract_unsupported')
    if book.spec['leverage']!=executor.risk_manager.leverage:
        raise ValueError('coordinated_account_leverage_differs')
    if any(book.pos.values()):
        evidence=M.source(executor,identity['coordinated']['group_id'])
        source=dict(kind='OWNED_MARKED_POSITION',evidence=evidence)
    else:
        account,receipt=A.account(executor.journal)
        source=dict(kind='INITIAL_ALLOCATION',account_equity=account['equity'],account_receipt_id=receipt)
    proof=dict(policy_id=K.POLICY_ID,observed_ms=int(time.time()*1000),
        identity={k:identity[k] for k in ('version_id','spec_hash','install_id','coordinated','coordinated_action_id')},
        hedge_spec=book.spec,positions=book.pos,instruction=instruction,reference=reference,source=source)
    from ..research.compose.managed_hedge import is_pair
    if is_pair(book.spec.get('management')):proof['ratio']=book.last_ratio
    assessment=calculate(proof);proof['assessment']=assessment
    text=canonical(proof).encode()
    if len(text)>MAX_BYTES:raise ValueError('coordinated_capital_evidence_bound')
    key=digest(proof)
    with executor.journal._tx() as db:
        db.execute('CREATE TABLE IF NOT EXISTS '+TABLE+'(receipt_id TEXT PRIMARY KEY,payload BLOB NOT NULL)')
        for verb in ('update','delete'):
            db.execute('CREATE TRIGGER IF NOT EXISTS '+TABLE+'_no_'+verb+' BEFORE '+verb.upper()+' ON '+TABLE+
                " BEGIN SELECT RAISE(ABORT,'capital evidence is immutable'); END")
        db.execute('INSERT OR IGNORE INTO '+TABLE+' VALUES(?,?)',(key,zlib.compress(text)))
    return key,assessment


def verify(journal,version,identity,requested,reference):
    key=identity.get('coordinated_capital_receipt')
    if not key or not journal.query("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(TABLE,)):
        raise ValueError('coordinated_capital_receipt_missing')
    rows=journal.query('SELECT payload FROM '+TABLE+' WHERE receipt_id=?',(key,))
    if len(rows)!=1:raise ValueError('coordinated_capital_receipt_missing')
    try:
        decoder=zlib.decompressobj();raw=decoder.decompress(rows[0]['payload'],MAX_BYTES+1)
        if len(raw)>MAX_BYTES or not decoder.eof or decoder.unused_data:
            raise ValueError('coordinated_capital_evidence_bound')
        proof=json.loads(raw)
    except (zlib.error,TypeError,json.JSONDecodeError):
        raise ValueError('coordinated_capital_evidence_corrupt') from None
    if digest(proof)!=key:raise ValueError('coordinated_capital_evidence_corrupt')
    if (proof['hedge_spec']!=version['lineage']['research']['multi_leg']['hedge_spec']
            or any(proof['identity'].get(k)!=identity.get(k) for k in proof['identity'])
            or proof['instruction']!=identity.get('coordinated_instruction') or proof['reference']!=reference):
        raise ValueError('coordinated_capital_identity_differs')
    now=int(time.time()*1000);observed=proof['observed_ms'];quote=reference.get('observed_ms')
    if (type(observed) is not int or not 0<=now-observed<=30000 or type(quote) is not int
            or not 0<=now-quote<=30000):raise ValueError('coordinated_capital_receipt_stale')
    if proof['source']['kind']=='INITIAL_ALLOCATION' and A.account(journal)[1]!=proof['source']['account_receipt_id']:
        raise ValueError('coordinated_capital_account_changed')
    result=calculate(proof,now_ms=now)
    if result!=proof['assessment']:raise ValueError('coordinated_capital_assessment_differs')
    if A.positive(requested)>A.positive(result['quantity'],zero=True):raise ValueError('coordinated_capital_exceeded')
    return result
