"""P2 — registered executable candidate families on the existing DSL.

A family is a versioned interface, not a prompt: which parts it assembles
(vocab part keys resolved by the existing `vocab.parts_for`), the parameter
slots and their ALLOWED values (the only legal mutations), its data needs and
its comparison (the existing one-part ablations). A request that no family
expresses stays visible as `UNSUPPORTED_FAMILY`; nothing is silently mapped to
volume breakout. Candidate identity is the economic content only, so changing
a slot is a different candidate while a code repair keeps the identity.
"""
from __future__ import annotations

from dataclasses import dataclass
import re

from .. import vocab
from ..combo import Combination, subsets
from . import ClosureRefused, digest

CANDIDATE_SCHEMA = 'closure-candidate.v1'
TF = '4h'                                   # config restricts research to 4h


@dataclass(frozen=True)
class Family:
    family_id: str
    summary: str
    slots: tuple                             # ((name, allowed values), ...) in mutation order
    default: tuple                           # ((name, value), ...)
    keywords: tuple                          # all-of groups matched against QUESTION_ONLY text
    data_needs: tuple = ('ohlcv',)

    def parts(self, params: dict) -> tuple:
        raise NotImplementedError


def _donch(p):
    return f"ev:donch{p['channel']}"


_PARTS = {
    'volume_breakout_context.v1': lambda p: (_donch(p), p['volume_cut']),
    'trend_breakout.v1': lambda p: (_donch(p), p['trend']),
    'volatility_expansion_breakout.v1': lambda p: (p['event'], p['expansion']),
    'oversold_in_uptrend.v1': lambda p: (p['gauge'], p['trend']),
}

FAMILIES = {f.family_id: f for f in (
    Family('volume_breakout_context.v1', 'Does high volume context improve a Donchian breakout?',
           (('channel', (20, 50, 100)), ('volume_cut', ('volz96>p75', 'volz96>p90')), ('geo', ('fixed', 'trail'))),
           (('channel', 100), ('volume_cut', 'volz96>p90'), ('geo', 'fixed')),
           (('volume', 'breakout'),)),
    Family('trend_breakout.v1', 'Does a trend state improve a Donchian breakout?',
           (('channel', (20, 50, 100)), ('trend', ('st:above_ema50', 'st:above_ema200')), ('geo', ('fixed', 'trail'))),
           (('channel', 50), ('trend', 'st:above_ema200'), ('geo', 'fixed')),
           (('trend', 'breakout'), ('trend', 'following'))),
    Family('volatility_expansion_breakout.v1', 'Does volatility-rank expansion improve a band breakout?',
           (('event', ('ev:keltner', 'ev:bb_break')), ('expansion', ('atrrank200>p75', 'atrrank200>p90')),
            ('geo', ('fixed', 'trail'))),
           (('event', 'ev:keltner'), ('expansion', 'atrrank200>p75'), ('geo', 'fixed')),
           (('volatility', 'expansion'), ('volatility', 'breakout'))),
    Family('oversold_in_uptrend.v1', 'Does an oversold RSI inside a long-term trend mean-revert?',
           (('gauge', ('rsi14<p10', 'rsi14<p25')), ('trend', ('st:above_ema200', 'st:above_ema50')),
            ('geo', ('fixed', 'trail'))),
           (('gauge', 'rsi14<p25'), ('trend', 'st:above_ema200'), ('geo', 'fixed')),
           (('oversold',), ('pullback',), ('mean', 'reversion'))),
)}

#: P.TRANSFORMS templates the predictive generator knows but no executable
#: family here can run; they stay visible as unsupported shapes.
TRANSFORM_FAMILY = {'volume_breakout_context.v1': 'volume_breakout_context.v1'}


def _validate_params(fam: Family, params: dict) -> dict:
    allowed = dict(fam.slots)
    if set(params) != set(allowed):
        raise ClosureRefused('params_slots_mismatch', fam.family_id)
    for k, v in params.items():
        if v not in allowed[k]:
            raise ClosureRefused('param_not_allowed', f'{fam.family_id}.{k}={v}')
    return dict(params)


def make_candidate(family_id: str, params: dict | None = None) -> dict:
    """The typed candidate for `family_id`; identity = economic content only."""
    fam = FAMILIES.get(family_id)
    if fam is None:
        raise ClosureRefused('UNSUPPORTED_FAMILY', str(family_id))
    p = _validate_params(fam, dict(params) if params is not None else dict(fam.default))
    keys = tuple(sorted(_PARTS[family_id](p)))
    body = dict(schema=CANDIDATE_SCHEMA, family_id=family_id, params=p, tf=TF, geo=p['geo'],
                part_keys=list(keys), data_needs=list(fam.data_needs),
                comparison='one-part ablations (existing growth.earns_place)')
    return dict(body, candidate_id=digest(body))


def verify_candidate(c: dict) -> dict:
    if c.get('schema') != CANDIDATE_SCHEMA or c.get('candidate_id') != digest(
            {k: v for k, v in c.items() if k != 'candidate_id'}):
        raise ClosureRefused('candidate_integrity')
    if make_candidate(c['family_id'], c['params']) != c:
        raise ClosureRefused('candidate_not_family_instance')
    return c


def mutations(candidate: dict) -> list:
    """Every legal single-slot neighbour of `candidate`, in a fixed order.
    This list is the whole mutation space: there is no other legal change."""
    fam = FAMILIES[candidate['family_id']]
    out = []
    for slot, allowed in fam.slots:
        for value in allowed:
            if value != candidate['params'][slot]:
                out.append(dict(kind='economic', slot=slot, **{'from': candidate['params'][slot], 'to': value}))
    return out


def mutate(candidate: dict, change: dict) -> dict:
    if change not in mutations(candidate):
        raise ClosureRefused('mutation_not_allowed', str(change))
    return make_candidate(candidate['family_id'], dict(candidate['params'], **{change['slot']: change['to']}))


def realize(candidate: dict, gauges: dict, evaluation_scope: str = '') -> Combination:
    """The existing Combination for this candidate. Gauge thresholds come from
    `gauges` (measured on the development window only); a part the data cannot
    support makes the candidate DATA_INSUFFICIENT, not a different candidate."""
    verify_candidate(candidate)
    known = {p.key: p for p in vocab.parts_for(candidate['tf'], gauges)}
    known.update({p.key: p for p in vocab.BOOL_PARTS})
    missing = [k for k in candidate['part_keys'] if k not in known]
    if missing:
        raise ClosureRefused('DATA_INSUFFICIENT', ','.join(missing))
    c = Combination(tuple(known[k] for k in candidate['part_keys']), candidate['tf'], candidate['geo'],
                    trigger=candidate['candidate_id'], round='closure', evaluation_scope=evaluation_scope)
    if not c.parts:
        raise ClosureRefused('candidate_empty')
    return c


def baselines(c: Combination) -> list:
    """The declared comparison: every one-part ablation of the candidate."""
    return subsets(c)


def gauge_expressions(candidate: dict) -> list:
    """Scalar expressions that need a development-window percentile."""
    keys = {g.key: g for g in vocab.GAUGES}
    out = []
    for k in candidate['part_keys']:
        m = re.match(r'^([a-z0-9_]+)[<>]p\d+$', k)
        if m and m.group(1) in keys:
            out += [keys[m.group(1)].long, keys[m.group(1)].short]
    return list(dict.fromkeys(out))


def match_text(text: str) -> tuple:
    """Deterministic registered-keyword match for QUESTION_ONLY passages.
    Returns (status, family_ids): exactly one family -> 'MATCHED'; several ->
    'AMBIGUOUS'; none -> 'NONE'. Never defaults to any family."""
    t = re.findall(r'[a-z]+', (text or '').lower())
    words = set(t)
    hit = [fid for fid, f in FAMILIES.items()
           if any(all(any(w.startswith(k) for w in words) for k in group) for group in f.keywords)]
    return ('MATCHED' if len(hit) == 1 else 'AMBIGUOUS' if hit else 'NONE'), tuple(sorted(hit))
