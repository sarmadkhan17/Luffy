"""Pure synthetic laboratory for the UNFROZEN 28-day M3.2 block proposal.

No input-file, database, trading, search, collector, or network imports. The
synthetic DGP contract is an oracle, NOT a test that detects exchangeability in
real data. Exact calendar results and repeatable-stratum laboratory results must
never be pooled. The association statistic is a synthetic block covariance,
not the historical M3.2 ranking metric or a trading-performance estimator.
"""
from dataclasses import dataclass, replace
from datetime import datetime, timezone, timedelta
from collections import Counter
import hashlib
import math

import numpy as np

DAY = 86_400_000
HOUR = 3_600_000
ORIGIN = int(datetime(2000, 1, 3, tzinfo=timezone.utc).timestamp()*1000)
ALPHA = .05
SCHEMA = 'm3.2-synthetic-block-calibration.v1'


@dataclass(frozen=True)
class Scenario:
    name: str
    common: float = 0.
    symbol: float = 0.
    within_ar: float = 0.
    between_ar: float = 0.
    regimes: bool = False
    regime_conditioned: bool = True
    lookback_hours: int = 4
    horizon_hours: int = 4
    missing: float = 0.
    missing_mode: str = 'template'
    uneven: bool = False
    sequences: bool = False
    cross_link: bool = False
    drift: bool = False
    heteroscedastic: bool = False
    certified: bool = True


SCENARIOS = (
    Scenario('iid'), Scenario('market_shocks', common=2.),
    Scenario('cross_symbol', common=1., symbol=1.5),
    Scenario('within_block_ar', within_ar=.85),
    Scenario('between_block_ar_invalid', between_ar=.85),
    Scenario('persistent_regimes', regimes=True, common=1.),
    Scenario('unconditioned_regimes_invalid', regimes=True, regime_conditioned=False),
    Scenario('overlapping_lookbacks', lookback_hours=400),
    Scenario('overlapping_outcomes', horizon_hours=24),
    Scenario('missing_10', missing=.1), Scenario('missing_30', missing=.3),
    Scenario('missing_50', missing=.5),
    Scenario('random_missing_topology', missing=.3, missing_mode='independent'),
    Scenario('uneven_symbols', uneven=True),
    Scenario('episode_sequences', sequences=True),
    Scenario('cross_block_lookback_invalid', lookback_hours=500),
    Scenario('cross_block_horizon_invalid', horizon_hours=200),
    Scenario('cross_block_sequence_invalid', sequences=True, cross_link=True),
    Scenario('informative_missingness_invalid', missing=.3, missing_mode='outcome'),
    Scenario('drifting_mean_invalid', drift=True),
    Scenario('drifting_variance_invalid', heteroscedastic=True),
    Scenario('uncertified_exchangeability_invalid', certified=False),
)


@dataclass
class Batch:
    scenario: Scenario
    x: np.ndarray                 # features fixed before outcomes, one value/block
    y: np.ndarray                 # unique target tensor [block, symbol, episode]
    observed: np.ndarray
    regimes: np.ndarray
    registered: np.ndarray       # [block, episode]
    source_start: np.ndarray
    available: np.ndarray
    links: tuple                 # (parent block, parent slot, child block, child slot)
    score_sd: np.ndarray         # analytic DGP SD of the observed block mean
    calendar_mode: str
    effect: float


def seed_for(*parts):
    return int.from_bytes(hashlib.sha256(repr(parts).encode()).digest()[:8], 'big')


def generate(scenario, seed, blocks=48, symbols=16, effect=0., calendar_mode='strict'):
    """Build a known synthetic process; no empirical market estimates are used."""
    if calendar_mode not in ('strict', 'laboratory_repeated_strata'):
        raise ValueError('unknown_calendar_mode')
    if not 3 <= blocks <= 512 or not 1 <= symbols <= 16:
        raise ValueError('synthetic_capacity')
    rng = np.random.default_rng(seed)
    # Two slots/day over the full seven-day intake; long horizons overlap.
    slots = 14
    starts = ORIGIN + np.arange(blocks, dtype=np.int64)*28*DAY
    registered = starts[:, None]+17*DAY+np.arange(slots)*12*HOUR
    source_start = registered-scenario.lookback_hours*HOUR
    available = registered+scenario.horizon_hours*HOUR
    regime = ((np.arange(blocks)//6) % 2).astype(int) if scenario.regimes else np.zeros(blocks,dtype=int)
    x = rng.normal(size=blocks) + (regime*.8 if scenario.regimes else 0.)
    # Linear Gaussian factor loadings give a known analytic block-score SD.
    # This preserves common shocks, symbol factors, AR paths and overlapping
    # horizon windows inside the tensor rather than pretending rows are IID.
    n = symbols*slots
    horizon_width = max(1, math.ceil(scenario.horizon_hours/12))
    temporal_n = slots+horizon_width-1
    ar = np.zeros((temporal_n,temporal_n))
    for t in range(temporal_n):
        ar[t,0] = scenario.within_ar**t
        for k in range(1,t+1):
            ar[t,k] = math.sqrt(1-scenario.within_ar**2)*scenario.within_ar**(t-k)
    temporal = np.stack([ar[t:t+horizon_width].mean(axis=0) for t in range(slots)])
    load = np.zeros((n,1+symbols+symbols*temporal_n))
    for s in range(symbols):
        for t in range(slots):
            i = s*slots+t
            load[i,0] = scenario.common
            load[i,1+s] = scenario.symbol
            load[i,1+symbols+s*temporal_n:1+symbols+(s+1)*temporal_n] = temporal[t]
    factors = rng.normal(size=(blocks,load.shape[1]))
    if scenario.between_ar:
        rho = scenario.between_ar
        for b in range(1,blocks):
            factors[b] = rho*factors[b-1]+math.sqrt(1-rho*rho)*factors[b]
            x[b] = rho*x[b-1]+math.sqrt(1-rho*rho)*x[b]
    noise = (factors @ load.T).reshape(blocks,symbols,slots)
    mask = np.ones_like(noise,dtype=bool)
    if scenario.missing:
        if scenario.missing_mode == 'template':
            # Outcome-independent repeating missingness permits exact-mask strata.
            template = rng.uniform(size=(symbols,slots)) >= scenario.missing
            mask[:] = template
        elif scenario.missing_mode == 'independent':
            mask = rng.uniform(size=noise.shape) >= scenario.missing
        else:
            mask = noise > np.quantile(noise,scenario.missing)
    if scenario.uneven:
        mask[1::2,-max(1,symbols//4):,:] = False
    weights = mask.reshape(blocks,n).astype(float)
    counts = weights.sum(axis=1)
    weights /= np.maximum(1,counts[:,None])
    sd = np.sqrt(((weights @ load)**2).sum(axis=1))
    full_sd = float(np.linalg.norm(load.mean(axis=0)))
    y = noise + effect*x[:,None,None]*full_sd
    if scenario.regimes: y += regime[:,None,None]*sd[:,None,None]*2
    if scenario.drift:
        trend = np.linspace(-2,2,blocks)
        y += trend[:,None,None]*sd[:,None,None]
        x += trend
    if scenario.heteroscedastic: y *= np.linspace(.2,3,blocks)[:,None,None]
    y = np.where(mask,y,np.nan)
    links = []
    if scenario.sequences:
        # Previous outcome is known before child registration; gap <=24h.
        links = [(b,t-1,b,t) for b in range(blocks) for t in range(1,slots)]
    if scenario.cross_link: links.append((0,slots-1,1,0))
    return Batch(scenario,x,y,mask,regime,registered,source_start,available,
                 tuple(links),sd,calendar_mode,effect)


def dependency_components(batch):
    """Whole blocks are units; cross-boundary footprints/links can only merge."""
    n = len(batch.x); parent = list(range(n))
    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]; i = parent[i]
        return i
    def union(a,b): parent[root(b)] = root(a)
    spans = sorted((int(batch.source_start[b].min()),int(batch.available[b].max()),b) for b in range(n))
    end, anchor = -1,None
    for lo,hi,b in spans:
        if anchor is not None and lo <= end+4*HOUR: union(anchor,b)
        else: anchor = b
        end = max(end,hi)
    for p,_,c,_ in batch.links: union(p,c)
    return len({root(i) for i in range(n)})


def strata(batch):
    groups = {}
    for b in range(len(batch.x)):
        if batch.calendar_mode == 'strict':
            calendar = tuple(datetime.fromtimestamp(int(t)/1000,timezone.utc).strftime('%Y-%m:%w:%H')
                             for t in batch.registered[b])
        else:
            calendar = ('LABORATORY_REPEATED_CELL_NOT_REAL_CALENDAR',)
        topology = tuple((p_slot,c_slot) for p,p_slot,c,c_slot in batch.links if p==b and c==b)
        key = (calendar,int(batch.regimes[b]),batch.observed[b].tobytes(),
               tuple((batch.available[b]-batch.registered[b]).tolist()),topology)
        groups.setdefault(key,[]).append(b)
    return tuple(tuple(v) for v in groups.values())


def validate(batch):
    s = batch.scenario
    reasons = []
    blocks = len(batch.x)
    starts = ORIGIN+np.arange(blocks)*28*DAY
    if not s.certified: reasons.append('no_synthetic_exchangeability_certificate')
    if s.between_ar: reasons.append('between_block_autocorrelation')
    if s.regimes and not s.regime_conditioned: reasons.append('unconditioned_persistent_regime')
    if s.missing_mode == 'outcome': reasons.append('outcome_dependent_missingness')
    if s.drift: reasons.append('nonstationary_conditional_mean')
    if s.heteroscedastic: reasons.append('nonexchangeable_conditional_variance')
    if np.any(batch.source_start < starts[:,None]) or s.lookback_hours>400:
        reasons.append('feature_footprint_outside_block_or_manifest')
    if np.any(batch.source_start >= batch.registered): reasons.append('feature_clock_invalid')
    if np.any(batch.registered < starts[:,None]+17*DAY) or np.any(batch.registered >= starts[:,None]+24*DAY):
        reasons.append('registration_outside_intake')
    if s.horizon_hours>24 or np.any(batch.available >= starts[:,None]+25*DAY):
        reasons.append('outcome_horizon_or_maturity_violation')
    for p,pt,c,ct in batch.links:
        if p != c:
            reasons.append('cross_block_sequence_dependency'); continue
        gap = batch.registered[c,ct]-batch.available[p,pt]
        if not 0 < gap <= 24*HOUR: reasons.append('sequence_not_known_before_registration')
    if dependency_components(batch) != blocks: reasons.append('dependent_blocks_merge_required')
    if np.any(batch.observed.sum(axis=(1,2)) < 8): reasons.append('observed_support_below_8')
    if np.any(~np.isfinite(batch.y[batch.observed])): reasons.append('nonfinite_observed_outcome')
    if np.any(~np.isnan(batch.y[~batch.observed])): reasons.append('missing_outcome_not_unknown')
    if np.any(~np.isfinite(batch.x)) or np.any(batch.score_sd<=0): reasons.append('invalid_synthetic_score')
    groups = strata(batch)
    if not any(len(g)>1 for g in groups): reasons.append('no_nonidentity_exchangeable_blocks')
    return sorted(set(reasons)),groups


def permutations(groups, blocks, draws, rng):
    """One mapping shared by every symbol, target copy and tested feature."""
    indices = np.tile(np.arange(blocks),(draws,1))
    for group in groups:
        if len(group)>1:
            g = np.asarray(group)
            indices[:,g] = g[np.argsort(rng.random((draws,len(g))),axis=1)]
    return indices


def transformed_targets(batch, mapping):
    """Expose the actual tensor transformation for integrity/chain tests."""
    return batch.y[np.asarray(mapping)].copy()


def _bh(pvalues):
    order = np.argsort(pvalues); ranked = pvalues[order]
    passing = np.flatnonzero(ranked <= ALPHA*np.arange(1,len(ranked)+1)/len(ranked))
    rejected = np.zeros(len(ranked),dtype=bool)
    if passing.size: rejected[order[:passing[-1]+1]] = True
    return rejected


def test_batch(batch, seed, draws=2000, family=1):
    if not 1 <= draws <= 2000 or not 1 <= family <= 384:
        raise ValueError('synthetic_search_budget')
    reasons,groups = validate(batch)
    base = dict(status='refused' if reasons else 'tested',reasons=reasons,
                dependence_groups=dependency_components(batch),raw_blocks=len(batch.x),
                strata=len(groups),movable_blocks=sum(len(g) for g in groups if len(g)>1),
                attempted=0,successful=0,failed=0,p=None,rejected=False,
                missing_fraction=float(1-batch.observed.mean()))
    if reasons: return base
    rng = np.random.default_rng(seed)
    # Missing entries stay NaN in the tensor. This masked sum computes an
    # observed-only mean within identical-mask strata, not a zero-filled label.
    score = np.nansum(batch.y,axis=(1,2))/batch.observed.sum(axis=(1,2))/batch.score_sd
    features = np.vstack([batch.x, rng.normal(size=(family-1,len(batch.x)))])
    for group in groups:
        ix = np.asarray(group)
        features[:,ix] -= features[:,ix].mean(axis=1,keepdims=True)
        score[ix] -= score[ix].mean()
    mapping = permutations(groups,len(batch.x),draws,rng)
    observed = np.abs(features @ score)
    null = np.abs(features @ score[mapping].T)
    # Equal floating-point values are ties, including identity transformations.
    p = (1+(null >= observed[:,None]-1e-12).sum(axis=1))/(draws+1)
    rejected = _bh(p)
    base.update(attempted=draws,successful=draws,p=float(p[0]),
                rejected=bool(rejected[0]),family_rejections=int(rejected.sum()),
                false_rejections=int(rejected[1:].sum()) if batch.effect else int(rejected.sum()),
                family_size=family)
    return base


def wilson(successes,n,z=1.959963984540054):
    if not n: return None
    p = successes/n; den = 1+z*z/n
    mid = (p+z*z/(2*n))/den
    half = z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/den
    return [max(0.,mid-half),min(1.,mid+half)]


def experiment(scenario, worlds=1000, draws=2000, blocks=48, effect=0.,
               calendar_mode='laboratory_repeated_strata', seed=20260919, family=1):
    ps=[]; refusals=Counter(); groups=[]; missing=[]; discoveries=[]; false=[]
    movable=[]; primary=[]
    for i in range(worlds):
        # Common random numbers across effects; not repeated looks at market data.
        batch=generate(scenario,seed_for(seed,scenario.name,i),blocks=blocks,
                       effect=effect,calendar_mode=calendar_mode)
        result=test_batch(batch,seed_for(seed,'null',scenario.name,i),draws,family)
        groups.append(result['dependence_groups']); missing.append(result['missing_fraction'])
        movable.append(result['movable_blocks'])
        if result['status']=='refused':
            refusals.update(result['reasons']); continue
        ps.append(result['p']); discoveries.append(result['family_rejections'])
        false.append(result['false_rejections']); primary.append(result['rejected'])
    n=len(ps); arr=np.asarray(ps)
    hits=int((arr<=ALPHA).sum())
    return dict(scenario=scenario.name,calendar_mode=calendar_mode,effect=effect,
        effect_units='additive association per complete-observation block-mean noise SD',
        worlds=worlds,tested=n,refused=worlds-n,refusal_reasons=dict(sorted(refusals.items())),
        draws=draws,blocks=blocks,family_size=family,
        rejection_rate=hits/n if n else None,rejection_wilson95=wilson(hits,n),
        p_cdf={str(t):float((arr<=t).mean()) if n else None for t in (.01,.025,.05,.1,.5)},
        p_quantiles=dict(zip(('q05','q50','q95'),np.quantile(arr,[.05,.5,.95]).tolist())) if n else None,
        group_range=[min(groups),max(groups)],movable_block_range=[min(movable),max(movable)],
        mean_missing_fraction=float(np.mean(missing)),
        primary_bh_rejection_rate=float(np.mean(primary)) if n else None,
        family_any_rejection_rate=float(np.mean(np.asarray(discoveries)>0)) if n else None,
        family_any_false_rejection_rate=float(np.mean(np.asarray(false)>0)) if n else None,
        empirical_fdr=float(np.mean([f/max(1,d) for f,d in zip(false,discoveries)])) if n else None)


def run_suite(worlds=1000,draws=2000,blocks=48,seed=20260919,family_worlds=250,progress=None):
    if not 1<=worlds<=10000 or not 1<=family_worlds<=10000:
        raise ValueError('world_budget')
    results=[]
    jobs=[(s,0.,'laboratory_repeated_strata',1,worlds) for s in SCENARIOS]
    jobs += [(SCENARIOS[0],e,'strict',1,worlds) for e in (0.,.5)]
    jobs += [(s,e,'laboratory_repeated_strata',1,worlds)
             for s in SCENARIOS if s.name in ('iid','market_shocks','missing_10','missing_30','missing_50')
             for e in (.1,.2,.35,.5)]
    jobs += [(s,e,'laboratory_repeated_strata',384,family_worlds)
             for s in SCENARIOS if s.name in ('iid','market_shocks') for e in (0.,.5)]
    for s,e,mode,family,n in jobs:
        result=experiment(s,n,draws,blocks,e,mode,seed,family)
        results.append(result)
        if progress: progress(result)
    return dict(schema=SCHEMA,synthetic_only=True,seed=seed,alpha=ALPHA,results=results,
        protocol_frozen=False,real_data_read=False,search_run=False,
        statistically_acceptable_to_freeze=False,
        limitations=[
            'DGP metadata certifies synthetic assumptions; no finite-data exchangeability detector is claimed.',
            'Laboratory repeated strata do not satisfy the literal UTC-month sampling proposal.',
            'Synthetic covariance and Gaussian feature families are not the historical M3.2 search metrics.',
            'Size/power conditional on tested cases; refusals are not counted as p=1 or as zero Type-I error.',
            'No data-dependent failed-draw replacement; invalid designs refuse before any null draw.',
            '2000 draws limit minimum Monte Carlo p to 1/2001; multiplicity resolution can limit power.'])
