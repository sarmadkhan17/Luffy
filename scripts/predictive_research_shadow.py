"""Bounded read-only real source inventory and normal bridge dry submission.

No missing Bank result is backfilled. Scratch checkpoints live in a temporary
directory; the original investigation/journal/candle stores stay read-only.
"""
import argparse
from contextlib import closing
from pathlib import Path
import sqlite3
import sys
import tempfile
import signal

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from trader.research import predictive_bridge as B, predictive_experiment as E
from trader.observability import investigation_research as R
from trader.cognition import predictive as P


def inventory(path, wanted):
    with closing(E.readonly(path)) as db:
        db.execute('BEGIN')
        names={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        counts={name:db.execute('SELECT COUNT(*) FROM '+name).fetchone()[0] if name in names else None for name in wanted}
        return dict(path=str(Path(path).resolve()),counts=counts,mutations=db.total_changes)


def run(investigation,journal,candles,cfg,*,as_of_ms,max_sources):
    if not 1<=max_sources<=B.MAX_SOURCES:
        raise ValueError('source_budget')
    actual=inventory(investigation,['cases','updates','memory_cases','investigation_research_records'])
    quantitative=inventory(journal,['research_bank_objects','research_unreadable_bank_objects',
        'research_combos','research_tests','research_candidates'])
    reasons={}
    inspected=0
    with closing(E.readonly(investigation)) as db:
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='cases'").fetchone():
            ids=[r[0] for r in db.execute("SELECT id FROM cases WHERE json_extract(payload,'$.primary_trigger')='volume_anomaly' ORDER BY created_ms,id LIMIT ?",(max_sources,))]
        else:
            ids=[]
    for iid in ids:
        inspected+=1
        try:
            R.read_source(investigation,iid)
        except (ValueError,KeyError,sqlite3.Error) as exc:
            key=str(exc)[:120]
            reasons[key]=reasons.get(key,0)+1
    with tempfile.TemporaryDirectory(prefix='luffy-predictive-shadow-') as tmp:
        result=B.cycle(investigation,Path(tmp)/'bank.db',candles,cfg,
            now_ms=as_of_ms,max_sources=max_sources,max_experiments=1,submit=False,
            quantitative_source_path=journal)
    return dict(package='LUFFY-PREDICTIVE-RESEARCH-BRIDGE-R1',status='PASS',
        as_of_ms=as_of_ms,bounds=dict(max_sources=max_sources,max_experiments=1),
        investigation=actual,journal=quantitative,investigations_inspected=inspected,
        investigation_refusals=reasons,**result,
        real_evidence_blocked=['No eligible persisted Research Bank source' ] if not result['counts']['eligible_research_results'] else
            ['Protected post-generation quantitative evidence is not yet mature'],
        limitation='Zero real support is reported literally; fixture results are never substituted for real evidence.')


if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__)
    for name in ('investigation','journal','candles','config','output'):
        ap.add_argument('--'+name,type=Path,required=True)
    ap.add_argument('--as-of-ms',type=int,required=True)
    ap.add_argument('--max-sources',type=int,required=True)
    args=ap.parse_args()
    def deadline(*_):
        raise TimeoutError('real_shadow_deadline')
    signal.signal(signal.SIGALRM,deadline)
    signal.alarm(30)
    import yaml
    result=run(args.investigation,args.journal,args.candles,yaml.safe_load(args.config.read_text()),
               as_of_ms=args.as_of_ms,max_sources=args.max_sources)
    signal.alarm(0)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(P.canonical(result)+'\n')
    print(P.canonical(result))
