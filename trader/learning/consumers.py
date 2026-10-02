"""Exact contextual projections at normal decision and scheduling boundaries."""
from contextlib import contextmanager
from pathlib import Path
import sqlite3
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


def allocation_observations(journal, candidates):
    result = {}
    for candidate in candidates:
        try:
            context = allocation_context(candidate)
        except ValueError:
            continue # Unknown horizon/identity receives no learned adjustment.
        result[candidate.candidate_id] = T.observation(journal,L.Target.ALLOCATION,context)
    return result
