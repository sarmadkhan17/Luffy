#!/usr/bin/env python3
"""Fresh-process deterministic replay of an explicit retained learning bundle."""
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from trader.learning import foundation as L


def run(raw):
    o = L.outcome_from_payload(raw['outcome'])
    sources = {(v['source_id'], v['version']): v['payload'] for v in raw['retained']}
    a, r = L.attribute(o), L.replay(o, sources)
    e = L.evidence(o, a, r)
    p = L.propose(e, L.Target(raw['target']), raw['current'], rule=raw.get('rule'))
    return dict(outcome_id=o.outcome_id, attribution_id=a.attribution_id, replay_id=r.replay_id,
                evidence_id=e.evidence_id, proposal_id=p.proposal_id, status=p.status.value)


if __name__ == '__main__':
    print(L.canonical(run(json.loads(Path(sys.argv[1]).read_text()))))
