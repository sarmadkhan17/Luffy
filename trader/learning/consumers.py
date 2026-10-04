"""Exact contextual projections at normal decision and scheduling boundaries."""
from contextlib import contextmanager
from pathlib import Path
import sqlite3
import json
from . import targets as T, foundation as L


class Reader:
    def __init__(self, db): self.db = db
    def query(self, sql, params=()):
        cur = self.db.execute(sql, params)
        names = [d[0] for d in cur.description]
        return [dict(zip(names, r)) for r in cur.fetchall()]


@contextmanager
def retained(path):
    path = Path(path)
    if not path.is_file():
        yield None
        return
    with sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True, timeout=2) as db:
        db.execute('PRAGMA query_only=ON'); db.execute('BEGIN')
        yield Reader(db)


def attention_context(symbol, timeframe):
    return T.Context(symbol, timeframe, 'NOT_APPLICABLE', 'attention_selection',
                     'NOT_APPLICABLE', 'NOT_APPLICABLE', symbol)


def research_context(subject):
    return T.Context('research-resource', 'planning', 'NOT_APPLICABLE',
                     'research_scheduling', 'NOT_APPLICABLE', 'NOT_APPLICABLE', subject)


def allocation_context(candidate):
    # Exact version and context already encode regime/horizon; no fuzzy match.
    return T.Context(candidate.instrument, candidate.horizon,
        'NOT_APPLICABLE', 'allocation', candidate.direction, candidate.strategy_id, candidate.version_id)


def evidence_context(symbol, horizon, regime, evidence_type, direction, family, subject):
    return T.Context(symbol, horizon, regime, evidence_type, direction, family, subject)


UNKNOWN_REGIMES = ('', 'UNKNOWN', 'unknown', 'NOT_PROVEN')


def vote_context(vote, symbol, regime, timeframe):
    """Exact identity of this analyst's evidence from what is actually known.

    Returns (Context, None) or (None, reason). No dimension is defaulted: an
    unknown horizon or regime means no contextual learned adjustment applies.
    """
    missing = [name for name, value in (('horizon', timeframe), ('regime', regime), ('instrument', symbol))
               if not value or (name == 'regime' and regime in UNKNOWN_REGIMES)]
    if missing:
        return None, 'UNAVAILABLE:' + ','.join(missing)
    try:
        return evidence_context(symbol, str(timeframe), regime, vote.agent,
                                vote.side.value, 'analyst', vote.agent), None
    except ValueError:
        return None, 'UNAVAILABLE:context_dimension_invalid'


def govern_vote(journal, vote, symbol, regime, timeframe):
    """Normal analyst vote path: carry truthful context, then look up governed state.

    Raw conviction/confidence are never rewritten. With no learned state the
    vote is unchanged apart from the recorded context identity.
    """
    context, reason = vote_context(vote, symbol, regime, timeframe)
    if timeframe:
        vote.meta['horizon'] = str(timeframe)
    if context is None:
        vote.meta['learning_context'] = dict(status='UNAVAILABLE', reason=reason)
        return vote
    vote.meta['learning_context'] = dict(status='EXACT', context_id=context.identity,
        instrument=context.asset_scope, horizon=context.horizon, regime=context.regime,
        evidence_type=context.evidence_type, direction=context.direction,
        strategy_family=context.strategy_family, subject_id=context.subject_id)
    return aggregate_vote(journal, vote, context)


def aggregate_vote(journal, vote, context):
    """Preserve raw evidence; attach optional governed reliability projections."""
    for target, key in ((L.Target.CONFIDENCE, 'governed_confidence'), (L.Target.WEIGHTS, 'governed_evidence')):
        item = T.observation(journal, target, context)
        if item['value'] is not None: vote.meta[key] = item
    return vote


def vote_confidence(vote):
    learned = vote.meta.get('governed_confidence')
    return learned['value']['reliability'] if learned else vote.confidence


def evidence_factor(vote):
    learned = vote.meta.get('governed_evidence')
    return learned['value']['reliability'] if learned else 1.0


def attention_order(journal, ranked, timeframe):
    return T.priority_order(journal, L.Target.ATTENTION, ranked,
                            lambda symbol: attention_context(symbol, timeframe))


def research_order(journal, items, subject_for=lambda item:item):
    return T.priority_order(journal, L.Target.RESEARCH, items,
                            lambda item:research_context(subject_for(item)))


def allocation_observations(journal, candidates, *, as_of_ms=None):
    result = {}
    for candidate in candidates:
        try:
            context = allocation_context(candidate)
        except ValueError:
            continue # Unknown horizon/identity receives no learned adjustment.
        result[candidate.candidate_id] = T.observation(journal,L.Target.ALLOCATION,context,as_of_ms=as_of_ms)
    return result


def claim_context(claim, *, regime, direction, family):
    """Exact ClaimConfidence context for one WorldClaim; caller supplies only
    what it truly knows, anything invalid raises and yields no overlay."""
    c = claim.coordinate
    return T.Context(c.scope.identifier, c.horizon.value, regime, claim.source,
                     direction, family, c.dimension)


def world_claims(journal, model, scope, horizon, *, regime, direction, family, dimension=None):
    """Normal WorldModel decision query: base claims + governed learned overlay."""
    return model.effective_claims(scope, horizon, learning_journal=journal, dimension=dimension,
        context_for=lambda claim: claim_context(claim, regime=regime, direction=direction, family=family))


class WorldQueryReader:
    """Retain the exact governed revision consumed, or replay frozen revisions.

    This descriptive Attention query has no regime, direction or strategy
    family; those dimensions are explicitly NOT_APPLICABLE. It cannot consume
    an overlay governed for a directional strategy or a named regime.
    """
    def __init__(self, journal=None, frozen=None):
        self.journal = journal
        self.frozen = frozen
        self.states = {}

    def read_target(self, target, context, *, as_of_ms=None):
        if as_of_ms is None:
            raise ValueError('world_query_requires_temporal_cut')
        if self.frozen is None:
            state = T.read(self.journal, target, context, as_of_ms=as_of_ms) if self.journal is not None else None
        else:
            state = self.frozen.get(context.identity)
        if state is None:
            body = dict(schema=T.SCHEMA, target=target.value, context=__import__('dataclasses').asdict(context),
                        revision=0, value=None, previous_hash=None)
            state = dict(body, state_hash=L.digest(body))
        T.temporal_state(state, target, context, as_of_ms)
        if state['value'] is not None:
            self.states[context.identity] = state
        return state
