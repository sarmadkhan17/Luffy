"""One production policy: a verified realized loss deserves investigation.

This changes research priority only. A single loss establishes neither decay
nor a causal explanation, and never changes risk, weights or trading state.
"""
import json
import math
from . import foundation as L

RULE_ID = 'investigate-verified-strategy-loss.v1'


def sufficient(view):
    line, obs = view.lineage, view.observation
    if view.kind != L.Kind.EXECUTED or view.boundary != L.Boundary.REALIZED:
        return False, 'verified realized execution required'
    if not all(line.get(k) for k in ('strategy_id', 'version_id', 'spec_hash')):
        return False, 'exact strategy version required'
    pnl = obs.get('net_pnl')
    if (obs.get('monetary_status') != 'VERIFIED' or isinstance(pnl, bool)
            or not isinstance(pnl, (int, float)) or not math.isfinite(pnl)):
        return False, 'verified cost-inclusive money required'
    return (pnl < 0, 'negative realized net outcome; investigate, no performance conclusion')


def context(view):
    from .consumers import research_context
    return research_context('strategy:' + view.lineage['strategy_id'] + ':' + view.lineage['version_id'])


def evaluate(evidence, current):
    from .dispatch import view
    facts = view(evidence)
    good, reason = sufficient(facts)
    if not good:
        return L.Status.INSUFFICIENT, None, reason
    if context(facts).__dict__ != current['context']:
        return L.Status.CONFLICTING, None, 'research context differs'
    if (current.get('value') or {}).get('priority') == 'HIGH':
        return L.Status.INSUFFICIENT, None, 'investigation already prioritized'
    return L.Status.APPLICABLE, {'priority': 'HIGH'}, reason


def rule():
    from .targets import Rule
    return Rule(RULE_ID, L.Target.RESEARCH, RULE_ID, evaluate, kinds=(L.Kind.EXECUTED,),
                context=context, sufficient=sufficient,
                requirements=('VERIFIED_REPLAY', 'REALIZED_NET_LOSS', 'EXACT_STRATEGY_VERSION',
                              'INVESTIGATION_ONLY_NO_TRADING_AUTHORITY'))


def tasks(journal, *, at_ms, limit=8):
    """Read governed research tasks consumed by the next strategy-writing prompt."""
    from . import targets as T
    if not journal.query("SELECT 1 FROM sqlite_master WHERE name='learning_target_revisions'"):
        return []
    rows = journal.query("SELECT payload FROM learning_target_revisions WHERE target=? "
                         "ORDER BY rowid DESC LIMIT ?", (L.Target.RESEARCH.value, limit * 4))
    seen, out = set(), []
    for row in rows:
        state = json.loads(row['payload'])
        ctx = T.Context(**state['context'])
        if not ctx.subject_id.startswith('strategy:') or ctx.identity in seen:
            continue
        seen.add(ctx.identity)
        current = T.read(journal, L.Target.RESEARCH, ctx, as_of_ms=at_ms)
        if (current.get('value') or {}).get('priority') != 'HIGH':
            continue
        out.append(dict(subject=ctx.subject_id, application_id=current['application']['application_id'],
                        instruction='Investigate this exact strategy/version. A verified realized loss raised priority; '
                                    'one loss is not proof of a weak strategy or a causal explanation.',
                        authority='RESEARCH_PRIORITY_ONLY'))
        if len(out) == limit:
            break
    return out
