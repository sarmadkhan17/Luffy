"""Adaptive branch of the single LearningApplication authority."""
import json
from dataclasses import asdict
from . import foundation as L, targets as T

def apply(journal, cfg, ev, proposal, req, *, at_ms, shadow, supplied):
    from . import application as A
    rules = T.registry(journal, supplied)
    def decide(reader):
        target = json.loads(req.target_json)
        body = dict(schema=A.SCHEMA, application_id=req.application_id, proposal_id=proposal.proposal_id,
            learning_evidence_id=ev.evidence_id, target_type=getattr(proposal.target, 'value', proposal.target),
            target_id=target.get('state_hash'), previous=target, requested=None,
            resulting=target, source_hashes=json.loads(req.source_versions_json),
            rule_id=proposal.rule, rule_version=proposal.rule_version, applied_at=at_ms,
            request=asdict(req), governor_transition_receipt=None)
        def finish(result, reason): return dict(body, result=result, reason=reason)
        rule = rules.get(proposal.rule)
        if rule is None or rule.target != proposal.target:
            return finish('UNREGISTERED_RULE', 'POLICY_UNAVAILABLE')
        if req.evidence_id != ev.evidence_id or req.proposal_id != proposal.proposal_id or proposal.evidence_id != ev.evidence_id:
            return finish('CONFLICT', 'evidence/proposal binding differs')
        if ev.quality != 'VERIFIED_REPLAY' or not L.verified_history(ev):
            return finish('INCOMPLETE_REPLAY', 'verified evidence required')
        h = json.loads(ev.historical_json)
        observed = h['capture_manifest']['capture']['observed_ms'] if 'capture_manifest' in h else h['decision_manifest']['observed_ms']
        if type(at_ms) is not int or at_ms < observed:
            return finish('STALE', 'application clock precedes evidence')
        historical_config = h.get('risk_config')
        if 'capture_manifest' in h:
            historical_config = h['capture_manifest']['sources'].get('risk_config', {}).get('config')
            safe = {k:cfg[k] for k in historical_config or {} if k in cfg}
        else:
            safe = cfg
        if json.loads(req.source_versions_json) != A.source_versions(cfg) or L.digest(safe) != L.digest(historical_config):
            return finish('STALE', 'source policy/version changed')
        if proposal.rule_version != rule.version:
            return finish('STALE', 'registered rule version changed')
        if proposal.current_json != L.canonical(target):
            return finish('CONFLICT', 'target request differs')
        expected = T.propose(ev, proposal.target, target, proposal.rule, rules)
        if expected != proposal:
            return finish('CONFLICT', 'registered recomputation differs')
        if proposal.status != L.Status.APPLICABLE:
            return finish(proposal.status.value, proposal.reason)
        if T.read(reader, proposal.target, T.Context(**target['context'])) != target:
            return finish('CONFLICT', 'target state/version changed')
        body['requested'] = json.loads(proposal.proposed_json)
        T.typed(proposal.target, body['requested'])
        return finish('APPLIED', proposal.reason)
    if shadow: return decide(journal)
    A.ensure(journal); T.ensure(journal)
    with journal._tx() as c:
        c.execute('BEGIN IMMEDIATE')
        bound = A._BoundJournal(c, journal)
        old = bound.query(f'SELECT * FROM {A.TABLE} WHERE application_id=?', (req.application_id,))
        if old: return A._receipt(old[0])
        receipt = decide(bound)
        if receipt['result'] == 'APPLIED':
            receipt['resulting'] = T.compare_and_apply(c, bound, proposal.target,
                receipt['previous'], receipt['requested'], req.application_id,
                authorization=(ev,proposal,req,supplied))
        c.execute(f'INSERT INTO {A.TABLE} VALUES(?,?,?,?,?)',
            (req.application_id, proposal.proposal_id, receipt['result'], L.canonical(receipt), L.digest(receipt)))
        return receipt
