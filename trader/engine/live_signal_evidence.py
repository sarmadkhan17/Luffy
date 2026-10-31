"""One immutable pending signal decision per owned trade.

This ledger retains the original base-frame receipts, not an order or fill.
Protection continues while executable timing and other context receipts are
unavailable. Recording a decision confers no execution authority.
"""
import json
import zlib
import time
from dataclasses import replace,asdict
from datetime import datetime

from . import paper_exit_evidence as X

TABLE='versioned_live_signal_decisions'
MAX_BYTES=8*1024*1024


def ensure(db):
    db.executescript('''CREATE TABLE IF NOT EXISTS versioned_live_signal_decisions(
        trade_id TEXT PRIMARY KEY,payload BLOB NOT NULL,sha256 TEXT NOT NULL);
        CREATE TRIGGER IF NOT EXISTS versioned_live_signal_no_update BEFORE UPDATE ON versioned_live_signal_decisions
        BEGIN SELECT RAISE(ABORT,'live signal evidence is immutable'); END;
        CREATE TRIGGER IF NOT EXISTS versioned_live_signal_no_delete BEFORE DELETE ON versioned_live_signal_decisions
        BEGIN SELECT RAISE(ABORT,'live signal evidence is immutable'); END;''')


def load(db,trade_id,identity):
    row=db.execute('SELECT * FROM '+TABLE+' WHERE trade_id=?',(trade_id,)).fetchone()
    if row is None:return None
    decoder=zlib.decompressobj()
    try:raw=decoder.decompress(row['payload'],MAX_BYTES+1)
    except zlib.error:raise ValueError('live_signal_evidence_corrupt') from None
    if len(raw)>MAX_BYTES or not decoder.eof or decoder.unused_data:
        raise ValueError('live_signal_evidence_bound')
    body=json.loads(raw)
    if (X.digest(raw.decode())!=row['sha256'] or body['trade_id']!=trade_id
            or body['entry_identity_sha256']!=X.digest(json.dumps(identity,sort_keys=True))
            or any(body[k]!=identity[k] for k in ('version_id','install_id'))
            or body['spec_hash']!=identity['spec_sha256']):
        raise ValueError('live_signal_evidence_identity_differs')
    return body


def retain(db,trade,identity,spec,snap,policy,state,observation,*,legacy=False,context=None):
    body=dict(trade_id=trade['id'],strategy_id=identity['strategy_id'],version_id=identity['version_id'],
        install_id=identity['install_id'],spec_hash=identity['spec_sha256'],exit_semantics_id=policy.semantics_id,
        entry_identity_sha256=X.digest(json.dumps(identity,sort_keys=True)))
    body.update(schema='live-signal-decision.v1',exec_mode='live',signal_execution_ready=False,
        recorded_ms=int(time.time()*1000),
        evidence_scope='BASE_FRAME_RECEIPTS_ONLY_OTHER_CONTEXT_NOT_FROZEN',fill_claim=False,financial_result=None,
        state_before=asdict(state),policy=asdict(policy),quantity_at_decision=float(trade['amount']),
        decision_observation=None if legacy else asdict(observation),legacy=legacy)
    try:
        if legacy:raise ValueError('original_signal_receipts_unavailable')
        body['signal_decision']=X.decision(body,spec,snap,observation)
    except (ValueError,KeyError,TypeError,AttributeError,OverflowError) as exc:
        raw=dict(bar_ms=observation.bar_ms,observed_at_ms=int(datetime.fromisoformat(snap.ts).timestamp()*1000),
            lookback_bars=None,inputs=[],availability=('UNAVAILABLE_LEGACY_DECISION_RECEIPTS' if legacy
                else 'UNAVAILABLE_INPUT_CAPTURE'),capture_error=type(exc).__name__)
        raw['decision_id']=X._identity(body,'signal_decision',raw)
        body['signal_decision']=raw
    if context is not None:
        from . import exit_signal_context as K
        try:
            bundle=K.capture(context,spec)
            reproduced=K.replay(bundle,observation.bar_ms)
            if reproduced!={'signal':True,'available':True}:raise ValueError('signal_context_replay_differs')
            body['context']=bundle
            body['evidence_scope']='RETAINED_COMPILER_INPUT_CONTEXT_INTERNAL_REPLAY_PASS'
        except (ValueError,KeyError,TypeError,AttributeError,OverflowError) as exc:
            body['context_error']=type(exc).__name__
    raw=X.canonical(body).encode()
    if len(raw)>MAX_BYTES:
        # Retain the decision and explicit source gap without disabling the
        # independent stop/target/time/trail path or storing an oversized blob.
        body['signal_decision']['inputs']=[]
        body['signal_decision']['availability']='UNAVAILABLE_SOURCE_RECEIPTS_EXCEED_BOUND'
        body.pop('context',None)
        body['evidence_scope']='UNAVAILABLE_SOURCE_RECEIPTS_EXCEED_BOUND'
        decision=dict(body['signal_decision']);decision.pop('decision_id')
        body['signal_decision']['decision_id']=X._identity(body,'signal_decision',decision)
        raw=X.canonical(body).encode()
    db.execute('INSERT INTO '+TABLE+' VALUES(?,?,?)',(trade['id'],zlib.compress(raw),X.digest(raw.decode())))
    return body


def advance(policy,state,observation,quantity,*,pending=False):
    """Use the same continuing-protection rule as prospective paper."""
    return X.advance(policy,state,observation,quantity,pending=pending)


def legacy_continuing_state(policy,state):
    """Old terminal signal rows never submitted an order in this adapter.

    Keep the last saved stop/counters. Missing original inputs and any missed
    trailing transition remain unknown; current history cannot backdate them.
    """
    if state.reason!='signal':return state
    return replace(state,reason='time' if state.bars_held>=policy.max_bars else None,reference_exit=None)
