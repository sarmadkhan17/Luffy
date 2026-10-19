"""Isolated counterpart of the retained baseline native-paper health probe."""
import sys,socket,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
def blocked(*a,**kw):raise AssertionError('network forbidden')
socket.socket.connect=socket.getaddrinfo=blocked
from tests.test_sol1_paper_cost import Run,cost_cfg
from trader.brain import postmortem as PM
from trader.strategy import factory_handoff as F
r=Run(ROOT/'reports/review07-remediation/native-tmp',cost_cfg())
tid=r.trade('win');r.complete()
before=r.j._conn().total_changes
health=PM.book_health(r.j,r.cfg)
out={'native_paper_rows':len(r.j.query('SELECT id FROM versioned_paper_trades')),
 'health':health,'read_only':before==r.j._conn().total_changes,
 'lifecycle_after':F.state_of(r.j,r.vid),'approval_request':F.approval_request(r.j,r.vid)}
(ROOT/'reports/review07-remediation/after-native-health.json').write_text(json.dumps(out,indent=2)+'\n')
