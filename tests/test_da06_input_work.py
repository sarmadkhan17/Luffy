"""DA-06: avoid duplicate frame serialization without stale evidence."""
from hashlib import sha256
from unittest.mock import patch
import pandas as pd
import pytest
from trader.agents.base import Analyst
from trader.agents.flow import FlowAnalyst
from trader.core.types import Snapshot


def snap():
    df=pd.DataFrame({'close':[10.]*40,'volume':[100.]*40,'taker_buy':[60.]*40})
    return Snapshot('NEW/USDT','2026-10-05T00:00:00+00:00',10,{'15m':df})


@pytest.mark.parametrize('invalid',[False,True])
def test_preflight_serializes_no_frames_and_final_vote_hashes_once(invalid):
    s=snap()
    if invalid:s.dfs['15m'].loc[39,'taker_buy']=float('nan')
    original=pd.DataFrame.to_csv
    calls=[]
    def count(self,*a,**kw):
        calls.append(self)
        return original(self,*a,**kw)
    with patch.object(pd.DataFrame,'to_csv',count):
        p=FlowAnalyst().evaluate(s).measurement
    assert len(calls)==1
    assert p.inputs[0]['source_ref']==sha256(original(s.dfs['15m'],index=True).encode()).hexdigest()
    assert (p.strength is None)==invalid


def test_final_evidence_reflects_mutation_inside_custom_evaluator():
    class Mutating(Analyst):
        name='mutating'
        frame_inputs=(('15m',('close',),1),)
        measurement_limitations=('test input mutation',)
        def evaluate(self,s):
            s.dfs['15m'].loc[0,'close']+=1
            return self._vote(self.name,s,.2,.3,'measured after mutation')
    s=snap();a=Mutating()
    first=a.evaluate(s).measurement
    assert first.inputs[0]['source_ref']==sha256(s.dfs['15m'].to_csv(index=True).encode()).hexdigest()
    second=a.evaluate(s).measurement
    assert first.inputs[0]['source_ref']!=second.inputs[0]['source_ref']
    assert second.inputs[0]['source_ref']==sha256(s.dfs['15m'].to_csv(index=True).encode()).hexdigest()


def test_direct_vote_and_precise_frame_changes_never_reuse_preflight_digest():
    s=snap();a=FlowAnalyst()
    first=a._vote(a.name,s,.1,.2,'direct').measurement
    s.dfs['15m'].loc[0,'close']=10.000000000000002
    second=a._vote(a.name,s,.1,.2,'direct').measurement
    assert first.inputs[0]['source_ref']!=second.inputs[0]['source_ref']
    assert a.evaluate(s).measurement.inputs[0]['source_ref']==second.inputs[0]['source_ref']
