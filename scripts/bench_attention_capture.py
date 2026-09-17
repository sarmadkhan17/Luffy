"""Synthetic producer latency only; never opens the journal or a venue.

Run: ./venv/bin/python scripts/bench_attention_capture.py
Budget: p99 <= 50ms at shipped caps (16 symbols, 64 receipts per symbol).
OS scheduling and larger opt-in limits are not covered by this measurement.
"""
import json
from pathlib import Path
import statistics
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
import pandas as pd
from trader.observability.collector import Collector
from trader.observability.diagnostics import Receipts


def main():
    with tempfile.TemporaryDirectory() as directory:
        collector = Collector(directory, start=False)
        anchor = int(time.time()*1000)//14_400_000*14_400_000
        c = 100*np.exp(np.cumsum(np.sin(np.arange(30))*.01))
        df = pd.DataFrame({'ts':pd.to_datetime([anchor-(30-i)*14_400_000 for i in range(30)],unit='ms',utc=True),
                           'open':c,'high':c*1.01,'low':c*.99,'close':c,'volume':100+np.arange(30)%7})
        frames = {f'S{i}/USDT':{'4h':df.copy()} for i in range(collector.cfg['max_symbols'])}
        timings=[]
        for run in range(210):
            start=time.perf_counter()
            sid=collector.begin(frames,list(frames))
            items=[]
            for symbol in frames:
                receipts=Receipts(True,collector.cfg['max_causes'])
                for i in range(collector.cfg['max_causes']):
                    receipts.add('strategy',str(i),'returned_none')
                items.append({'symbol':symbol,'evaluations':receipts.items})
            collector.causes(sid,items)
            elapsed=(time.perf_counter()-start)*1000
            while not collector.queue.empty(): collector.queue.get_nowait()
            if run>=10:timings.append(elapsed)
        result={'samples':len(timings),'symbols':collector.cfg['max_symbols'],
                'receipts_per_symbol':collector.cfg['max_causes'],'budget_ms':50,
                'p50_ms':statistics.median(timings),'p99_ms':sorted(timings)[197],'max_ms':max(timings)}
        print(json.dumps(result,indent=2))
        return 0 if result['p99_ms']<=50 else 1

if __name__=='__main__': raise SystemExit(main())
