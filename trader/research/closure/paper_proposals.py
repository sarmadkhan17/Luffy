"""Development results -> proposals for the EXISTING numerical paper gate.

Nothing here approves, installs, changes a strategy row or submits an order.
Historical development support is a lead; the Analyst must independently run
its configured numerical paper-entry qualification on the exact proposal.
"""
import json
from contextlib import closing
from pathlib import Path
from ...engine.protection_snapshot import ro_connect
from ..combo import Combination
from . import ClosureRefused, digest
from ..compose import graph


def proposals(path, *, limit=8, exclude=()):
    if not Path(path).is_file():
        return []
    out, seen = [], set()
    with closing(ro_connect(path)) as db:
        db.execute('BEGIN')
        rows = db.execute("SELECT payload FROM runs WHERE json_extract(payload,'$.stage')='DEVELOPMENT' "
                          "AND json_extract(payload,'$.outcome')='DEVELOPMENT_POSITIVE' "
                          "AND json_extract(payload,'$.candidate_id') NOT IN (SELECT value FROM json_each(?)) "
                          "ORDER BY rowid DESC LIMIT ?", (json.dumps(sorted(set(exclude))), limit * 4)).fetchall()
        for row in rows:
            r = json.loads(row[0])
            if r.get('result_id') != digest({k:v for k,v in r.items() if k not in ('result_id','cached')}):
                raise ClosureRefused('paper_proposal_result_integrity')
            cid = r.get('candidate_id')
            if cid in seen:
                continue
            seen.add(cid)
            raw = db.execute('SELECT payload FROM candidates WHERE id=?', (cid,)).fetchone()
            env = db.execute('SELECT payload FROM envelopes WHERE id=?', (r.get('envelope_id'),)).fetchone()
            if raw is None or env is None:
                continue
            c, envelope = json.loads(raw[0]), json.loads(env[0])
            if envelope.get('policy_class') != 'CONFIGURED':
                continue
            graph.verify(c)
            if c['structure'] not in graph.ENTRY_STRUCTURES:
                continue  # multi-leg proposals have their own required execution path
            combination = Combination.from_dict(r['combo'])
            if (combination.hash != r.get('combo_hash') or combination.trigger != cid
                    or combination.evaluation_scope != r['envelope_id']):
                raise ClosureRefused('paper_proposal_identity_differs')
            spec = combination.to_spec()
            spec.name = ('Research: ' + c['question'])[:100]
            spec.thesis += '\nResearch mechanism: ' + c['rationale'] + '\nExpected: ' + c['expected']
            spec.invalidation += '\nResearch falsifier: ' + ('; '.join(c['failure_conditions'])
                                    if isinstance(c['failure_conditions'],list) else c['failure_conditions'])
            spec.universe = dict(include=sorted(envelope['discovery_symbols']), exclude=[])
            spec.provenance.update(closure_candidate_id=cid, closure_result_id=r['result_id'],
                                   closure_envelope_id=r['envelope_id'],
                                   development_evidence_scope=r.get('evidence_scope', r.get('data_mode')),
                                   admission='UNTESTED_PROPOSAL_REQUIRES_EXISTING_ANALYST_QUALIFICATION')
            out.append(dict(spec=spec, candidate_id=cid, result_id=r['result_id']))
            if len(out) == limit:
                break
        db.execute('COMMIT')
    return out
