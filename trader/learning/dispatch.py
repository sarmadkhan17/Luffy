"""Exact compiled registrations. No runtime registration API or test-mode switch.

Future calibrated registrations supply both an eligibility predicate and an
exact deterministic evaluator. No category membership grants update permission.
The empty extension registry is deliberate; test registrations live in tests/.
"""
from dataclasses import dataclass, asdict
import json
from types import MappingProxyType
from typing import Callable

from . import foundation as L
from .authority import LearningTargetRef, Mutation, MutationShape, Refused


@dataclass(frozen=True)
class RegisteredUpdate:
    rule_id: str
    rule_version: str
    target_category: L.Target
    eligible: Callable
    evaluate: Callable

    @property
    def key(self):
        return self.rule_id, self.rule_version, self.target_category


# Deliberately inaccessible to configuration, requests, CLI and normal runtime.
# New production rules require a reviewed source-code registration.
EXTENSIONS = MappingProxyType({})


def resolve(rule_id, rule_version, category):
    if not isinstance(category, L.Target):
        raise Refused('UNREGISTERED_RULE', 'unapproved target category')
    key = (rule_id, rule_version, category)
    if key == (L.DECAY_RULE, L.digest(L.code_manifest()), L.Target.LIFECYCLE):
        return RegisteredUpdate(*key, lambda e, t: L.Target.LIFECYCLE in e.eligible_targets,
            _decay)
    registered = EXTENSIONS.get(key)
    if registered is None or registered.key != key:
        raise Refused('UNREGISTERED_RULE', 'exact rule id/version/category is not registered')
    return registered


def _decay(evidence, target):
    current = json.loads(target.current_json)
    current.pop('history_sha256')
    proposal = L.propose(evidence, L.Target.LIFECYCLE, current, rule=L.DECAY_RULE)
    if proposal.status != L.Status.APPLICABLE:
        raise Refused(proposal.status.value, proposal.reason)
    return Mutation(MutationShape.RETIRE, 'state', L.canonical('RETIRED'))


def verify_proposal(evidence, proposal, target, mutation, at_ms):
    rule = resolve(proposal.rule, proposal.rule_version, target.target_category)
    if proposal.target != target.target_category or proposal.evidence_id != evidence.evidence_id:
        raise Refused('CONFLICT', 'proposal evidence/target binding differs')
    if evidence.quality != 'VERIFIED_REPLAY' or not L.verified_history(evidence):
        raise Refused('INCOMPLETE_REPLAY', 'authoritative historical replay unavailable')
    history = json.loads(evidence.historical_json)
    wrapper = history.get('capture_manifest', {}).get('capture') or history['decision_manifest']
    if type(at_ms) is not int or at_ms < wrapper['observed_ms']:
        raise Refused('STALE', 'application clock precedes evidence')
    if not rule.eligible(evidence, target):
        raise Refused('INSUFFICIENT_EVIDENCE', 'registered target-specific eligibility refused')
    if proposal.status != L.Status.APPLICABLE or rule.evaluate(evidence, target) != mutation:
        raise Refused('CONFLICT', 'not the exact registered recomputed update')
    if target.target_category == L.Target.LIFECYCLE:
        current = json.loads(target.current_json)
        current.pop('history_sha256')
        expected = L.propose(evidence, L.Target.LIFECYCLE, current, rule=L.DECAY_RULE)
        if proposal != expected:
            raise Refused('CONFLICT', 'lifecycle proposal differs')
    elif proposal.current_json != L.canonical(target.to_dict()) or proposal.proposed_json != L.canonical(asdict(mutation)):
        raise Refused('CONFLICT', 'exact dimension precondition/update differs')


def propose(evidence, target, *, rule_id, rule_version):
    """Generic registered proposal generation; refuses absent rules deterministically."""
    if not isinstance(target, LearningTargetRef):
        raise TypeError('LearningTargetRef required')
    if target.target_category == L.Target.LIFECYCLE and (rule_id, rule_version) == (L.DECAY_RULE, L.digest(L.code_manifest())):
        current = json.loads(target.current_json)
        current.pop('history_sha256')
        return L.propose(evidence, target.target_category, current, rule=rule_id)
    mutation = None
    status, reason = L.Status.UNREGISTERED, 'no exact registered rule'
    try:
        rule = resolve(rule_id, rule_version, target.target_category)
        if evidence.quality != 'VERIFIED_REPLAY' or not L.verified_history(evidence):
            status, reason = L.Status.INCOMPLETE, 'authoritative historical replay unavailable'
        elif not rule.eligible(evidence, target):
            status, reason = L.Status.INSUFFICIENT, 'target-specific eligibility refused'
        else:
            mutation = rule.evaluate(evidence, target)
            if not isinstance(mutation, Mutation):
                raise TypeError('registered evaluator must return typed Mutation')
            status, reason = L.Status.APPLICABLE, 'exact registered update'
    except Refused as refusal:
        status = next((s for s in L.Status if s.value == refusal.result), L.Status.UNREGISTERED)
        reason = refusal.reason
    return L.LearningUpdateProposal(evidence.evidence_id, target.target_category,
        L.canonical(target.to_dict()), L.canonical(asdict(mutation)) if mutation else None,
        rule_id, rule_version, status, (evidence.outcome_id, evidence.attribution_id, evidence.replay_id), reason)
