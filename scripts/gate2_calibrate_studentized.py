"""Offline experimental studentized gate-2 calibration. Generated trades only; never admission.

Run with --output-dir NEW_DIRECTORY. No external input-data option exists.
The gate-1 selector is an explicitly labelled Gaussian coupling PROXY.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from trader.research.fdr import alpha_at  # pure arithmetic only

SCHEMA = "luffy.gate2.synthetic.studentized.v1"
THESIS = "25fe401f6be653b1dfb355f74091ff179237b743517f2d11cfc33144c1357b4d"
SCENARIOS = ("iid", "shared_factor", "long_memory", "regime_switching",
             "heavy_tail", "heavy_tail_floor", "missingness", "spillover_0.1", "spillover_0.5",
             "spillover_1", "spillover_2")


def zero_failure_n(alpha, error):
    if not 0 < alpha < 1 or not 0 < error < 1:
        raise ValueError("alpha and error must be inside (0,1)")
    return math.ceil(math.log(error) / math.log1p(-alpha))


def binomial_bounds(k, n, error):
    """Exact one-sided Clopper-Pearson bounds, each with error probability error.

    Invert the binomial CDF in log space; no scipy dependency or normal tail fit.
    No trials yields [0,1], never a successful calibration.
    """
    if not 0 <= k <= n or not 0 < error < 1:
        raise ValueError("invalid binomial arguments")
    if n == 0:
        return (0.0, 1.0)

    def root(count, target):
        j = np.arange(count + 1, dtype=float)
        coeff = np.zeros(count + 1)
        if count:
            coeff[1:] = np.cumsum(np.log((n - j[1:] + 1) / j[1:]))
        lo, hi = 0.0, 1.0
        for _ in range(60):
            p = (lo + hi) / 2
            v = coeff + j * math.log(p) + (n-j) * math.log1p(-p)
            peak = v.max()
            cdf = math.exp(float(peak)) * float(np.exp(v-peak).sum())
            if cdf > target:
                lo = p
            else:
                hi = p
        return (lo + hi) / 2

    lower = 0.0 if k == 0 else root(k-1, 1-error)
    upper = 1.0 if k == n else root(k, error)
    return (lower, upper)


def rate(k, n, error, target=None):
    lo, hi = binomial_bounds(k, n, error)
    return {"events": int(k), "trials": int(n), "estimate": k/n if n else None,
            "lower": lo, "upper": hi, "target": target,
            "assessment": ("descriptive" if target is None else
                           "bounded_in_fixture" if n and hi <= target else
                           "inflated" if lo > target else "inconclusive")}


def draws_for(alpha, cap):
    need = math.ceil(1/alpha)-1
    return need if need <= cap else None


def family_levels(t, rejections):
    """Two in-memory reservations against ONE history, no H1 feedback into H2."""
    return [alpha_at(t, rejections), alpha_at(t+1, rejections)]


@dataclass
class Cohort:
    entry: np.ndarray  # days on a common synthetic calendar
    exit: np.ndarray
    r: np.ndarray
    labels: np.ndarray  # N x 2, -1 unknown, 0 complement, 1 subset
    complete: np.ndarray
    span_days: int


def persistent(rng, n, phi):
    x = np.empty(n)
    x[0] = rng.normal()
    scale = math.sqrt(1-phi*phi)
    eps = rng.normal(size=n)
    for i in range(1, n):
        x[i] = phi*x[i-1] + scale*eps[i]
    return x


def fractional(rng, n, d=.4, memory=4096):
    """Truncated ARFIMA(0,d,0) filter, with a full presample innovation buffer.

    Finite simulation approximates power-law memory up to 4096 days; no claim
    of literal infinite-memory simulation. Unit unconditional variance.
    """
    j = np.arange(1, memory)
    weights = np.concatenate(([1.], np.cumprod((j-1+d)/j)))
    eps = rng.normal(size=n+memory-1)
    size = 1 << (len(eps)+memory-2).bit_length()
    conv = np.fft.irfft(np.fft.rfft(eps, size)*np.fft.rfft(weights, size), size)
    return conv[memory-1:memory-1+n]/np.linalg.norm(weights)


def generate(rng, scenario, blocks=12, trades_per_block=20, effect=(0., 0.)):
    """Generic persistent labels, NOT the frozen thesis DSL predicates.

    Independent label/outcome innovations establish zero population contrast
    under every null fixture, including missingness (which depends on labels,
    calendar and latent volatility, never the signed outcome).
    """
    if scenario not in SCENARIOS or blocks < 1 or trades_per_block < 4:
        raise ValueError("invalid generator specification")
    days = blocks*30
    duration = float(scenario.split('_')[1])*30 if scenario.startswith('spillover') else 1.
    nday = days + math.ceil(duration) + 1
    phi = .995 if scenario == 'long_memory' else .8
    factor = fractional(rng, nday) if scenario == 'long_memory' else persistent(rng, nday, phi)
    state = np.empty(nday)
    state[0] = rng.choice([-1., 1.])
    for d in range(1, nday):
        state[d] = -state[d-1] if rng.random() < 1/120 else state[d-1]
    if scenario == 'regime_switching':
        factor = state
    label_factor = (fractional(rng, nday) if scenario == 'long_memory' else
                    persistent(rng, nday, .995 if scenario == 'regime_switching' else .8))
    # Four synthetic symbols; no same-symbol overlaps, even for long holds.
    symbols = max(4, math.ceil(trades_per_block*duration/30)+1)
    n = blocks*trades_per_block
    entry = np.arange(n)*30/trades_per_block
    di = entry.astype(int)
    noise = rng.normal(size=(n, 2))
    latent = label_factor[di] + noise[:, 0]
    h1 = latent > 0
    h2 = (.9*latent + .45*noise[:, 1]) > 0
    if scenario == 'iid':
        h1 = noise[:, 0] > 0
        h2 = (.9*noise[:, 0]+.45*noise[:, 1]) > 0
    if scenario == 'heavy_tail_floor':
        # Exactly 20 subset / 40 complement trades at the default floor;
        # both arms in every block, same labels gives maximal H dependence.
        h1 = np.zeros(n, dtype=bool)
        for b in range(blocks):
            take = 2 if b % 3 else 1
            h1[b*trades_per_block + rng.choice(trades_per_block, take, replace=False)] = True
        h2 = h1.copy()
    labels = np.column_stack((h1, h2)).astype(np.int8)
    idio = rng.normal(size=n)
    if scenario in ('heavy_tail', 'heavy_tail_floor'):
        # Pareto shape 2.5: finite mean/variance, infinite third moment.
        idio = (rng.pareto(2.5, n)+1-2.5/1.5) / math.sqrt(2.5/(1.5**2*.5))
    shared = factor[di]
    if scenario.startswith('spillover'):
        width = max(1, math.ceil(duration))
        sums = np.concatenate(([0.], np.cumsum(factor)))
        shared = (sums[di+width]-sums[di])/width
    r = idio if scenario == 'iid' else .8*shared + .6*idio
    # In power fixtures coefficients are planted, marginal contrasts also
    # include the correlated other label; they are reported empirically.
    r = r + effect[0]*h1 + effect[1]*h2
    if scenario == 'missingness':
        volatility = np.abs(persistent(rng, nday, .97))[di]
        missing = ((entry < 30) | (rng.random(n) <
                   np.clip(.1+.25*h1+.15*(volatility > 1), 0, .8)))
        labels[missing] = -1
    complete = entry+duration <= days
    assert symbols*30/trades_per_block > duration
    return Cohort(entry, entry+duration, r, labels, complete, days)


def summaries(c, block_days=30):
    """K x H x arm x (count,sum,sumsq), retaining empty calendar blocks."""
    k = math.ceil(c.span_days/block_days)
    out = np.zeros((k, 2, 2, 3))
    counts = []
    for h in range(2):
        known = c.complete & (c.labels[:, h] >= 0)
        for arm in (0, 1):
            mask = known & (c.labels[:, h] == arm)
            ix = (c.entry[mask]//block_days).astype(int)
            for j, weights in enumerate((np.ones(mask.sum()), c.r[mask], c.r[mask]**2)):
                out[:, h, arm, j] = np.bincount(ix, weights=weights, minlength=k)
        arms = out[:, h, :, 0].sum(axis=0).astype(int)
        both = int(np.all(out[:, h, :, 0] > 0, axis=1).sum())
        counts.append({"arms": arms.tolist(), "both_blocks": both,
                       "unknown": int((c.complete & (c.labels[:, h] < 0)).sum()),
                       "incomplete": int((~c.complete).sum()),
                       "eligible": bool(arms.min() >= 20 and arms.sum() >= 60 and both >= 12)})
    return out, counts


def contrasts(s):
    """Accept (...,2 arms,3 moments). Zero pooled variance is degenerate."""
    n, total, squares = s[..., 0], s[..., 1], s[..., 2]
    with np.errstate(divide='ignore', invalid='ignore'):
        delta = total[..., 1]/n[..., 1] - total[..., 0]/n[..., 0]
        pooled = squares.sum(axis=-1)/n.sum(axis=-1) - (total.sum(axis=-1)/n.sum(axis=-1))**2
    valid = (n.min(axis=-1) > 0) & (pooled > 1e-14) & np.isfinite(delta)
    return delta, valid


def studentized_moments(s):
    """Paired cluster SE for the difference of two trade-weighted ratios.

    s has shape (..., K, H, arm, moment). Every calendar cluster counts,
    including empty and duplicated clusters. No arm-independent resampling.
    """
    k = s.shape[-4]
    total = s.sum(axis=-4)
    delta, valid = contrasts(total)
    n, sums = total[..., 0], total[..., 1]
    with np.errstate(divide='ignore', invalid='ignore', over='ignore'):
        means = sums / n
        residual = (s[..., 1] - s[..., 0]*np.expand_dims(means, -3))
        influence = residual / np.expand_dims(n, -3)
        paired = influence[..., 1] - influence[..., 0]
        variance = (k/max(k-1, 1))*np.sum(paired**2, axis=-2)
        se = np.sqrt(variance)
        t = delta/se
    valid &= (k > 1) & np.isfinite(se) & (se > 1e-14) & np.isfinite(t)
    return delta, se, t, valid


def bootstrap(c, draws, rng, block_days=30, decision_alpha=None):
    s, counts = summaries(c, block_days)
    delta, se, observed, valid = studentized_moments(s)
    extreme = np.zeros(2, dtype=int)
    degenerate = np.zeros(2, dtype=int)
    k = len(s)
    generated = 0
    for start in range(0, draws, 256):
        ix = rng.integers(k, size=(min(256, draws-start), k))
        d, se_b, _, ok = studentized_moments(s[ix])
        with np.errstate(divide='ignore', invalid='ignore', over='ignore'):
            centered_t = (d-delta)/se_b
        ok &= np.isfinite(centered_t)
        degenerate += (~ok).sum(axis=0)
        extreme += ((~ok) | (centered_t >= observed)).sum(axis=0)
        generated += len(ix)
        if decision_alpha is not None:
            failed = ((extreme+1)/(draws+1) > decision_alpha) | (delta <= 0)
            failed |= np.array([not co['eligible'] for co in counts]) | ~valid
            if failed.all():
                break
    p = (extreme+1)/(draws+1)
    for h in range(2):
        if not counts[h]['eligible'] or not valid[h] or degenerate[h] > .2*draws:
            p[h] = 1.
    return {"p": p, "delta": delta, "se": se, "counts": counts,
            "degenerate": degenerate, "generated_draws": generated,
            "planned_draws": draws, "p_is_lower_bound": generated < draws}


def combine_required(results):
    if not results:
        return np.ones(2), np.zeros(2, dtype=bool)
    return (np.max([r['p'] for r in results], axis=0),
            np.all([np.isfinite(r['delta']) & (r['delta'] > 0) for r in results], axis=0))


def gate1_proxy(c, rng, shift=0.):
    """Maximum of three Gaussian tail probabilities, NOT production gate1.

    First score is a standardized mean of synthetic cohort noise, making
    selection depend on the same outcomes as gate2. Its p is not guaranteed
    uniform for non-iid fixtures; deliberately a selection stress.
    """
    z = float(c.r.mean()*math.sqrt(len(c.r)))
    scores = [z+shift, .8*z+.6*rng.normal()+shift,
              .8*z+.6*rng.normal()+shift]
    return max(.5*math.erfc(v/math.sqrt(2)) for v in scores)


def make_plan(seed=20260917, smoke=False, cap=10000):
    histories = [{"t": 2, "rejections": [1]}, {"t": 11, "rejections": [10]}]
    levels = [family_levels(h['t'], h['rejections']) for h in histories]
    # Fixed simultaneous error budget across upper AND lower endpoints,
    # all null cells, power cells and stream endpoints (conservative 1000).
    error = .05/1000
    target = min(min(a) for a in levels)
    repetitions = zero_failure_n(target, error)
    draws = draws_for(target, cap)
    if draws is None:
        raise ValueError("bootstrap cap cannot resolve predeclared alpha grid")
    return {"schema": SCHEMA, "seed": seed, "smoke": smoke, "alpha": .1, "w0": .05,
            "histories": histories, "levels": levels, "error_per_bound": error,
            "simultaneous_bound_budget": 1000,
            "bootstrap_shortcut": "stop only on irreversible nonrejection using full-B p lower bound; fixed outer N",
            "long_memory_model": "truncated ARFIMA(0,.4,0), 4096-day kernel with presample innovations; unit variance",
            "labels": "synthetic correlated proxies, not frozen H1/H2 DSL",
            "selection": "maximum of three Gaussian tail probabilities (minimum z), not production gate1",
            "statistic": "paired calendar-cluster bootstrap-t, SE recomputed per draw",
            "observed_se_floor": 1e-14,
            "charged_block_days": 30,
            "family_target": "not approved; shared stream descriptive only",
            "stream_reuse": "same cohort across five candidates per independent stream",
            "partial_null": "correlated binary labels (.6 correlation), residualized effect keeps other marginal contrast zero", "planned_repetitions": repetitions,
            "repetitions": min(100, repetitions) if smoke else repetitions,
            "draws": draws, "draw_cap": cap, "scenarios": list(SCENARIOS),
            "null_blocks": 24, "null_trades_per_block": 20,
            "diagnostic_block_days": [60, 90],
            "diagnostic_repetitions": 100 if smoke else 1000,
            "power_repetitions": 100 if smoke else 1000,
            "effects": [0., .25, .5, 1., 2., 4.], "power_blocks": [12, 24],
            "power_trades_per_block": [5, 20],
            "stream_repetitions": 100 if smoke else repetitions,
            "stream_candidates": 5, "workers": 2,
            "stream_models": ["all_null", "H1_only", "H2_only"],
            "candidate_hash": "55243573515adc3b", "thesis_sha256": THESIS,
            "approval": "BLOCKED; synthetic proxy calibration is not gate evidence"}


def null_cell(plan, scenario, cell):
    rng = np.random.default_rng(np.random.SeedSequence([plan['seed'], 0, cell]))
    levels = np.array(plan['levels'])
    events = np.zeros((len(levels), 3), dtype=int)
    count_sum = np.zeros((2, 5))
    for _ in range(plan['repetitions']):
        c = generate(rng, scenario, 12 if scenario == 'heavy_tail_floor' else plan['null_blocks'],
                     5 if scenario == 'heavy_tail_floor' else plan['null_trades_per_block'])
        b = bootstrap(c, plan['draws'], rng, decision_alpha=float(levels.max()))
        reject = (b['p'] <= levels) & (b['delta'] > 0)
        events[:, :2] += reject
        events[:, 2] += reject.all(axis=1)
        for h, co in enumerate(b['counts']):
            count_sum[h] += [*co['arms'], co['unknown'], co['incomplete'], co['eligible']]
    results = []
    for i, a in enumerate(levels):
        # All-null conjunction bounded against smaller marginal level, not
        # alpha_H1*alpha_H2 (independence is not assumed).
        results.append({"alphas": a.tolist(), "H1": rate(events[i, 0], plan['repetitions'], plan['error_per_bound'], a[0]),
                        "H2": rate(events[i, 1], plan['repetitions'], plan['error_per_bound'], a[1]),
                        "both": rate(events[i, 2], plan['repetitions'], plan['error_per_bound'], min(a))})
    return {"scenario": scenario, "results": results,
            "mean_counts_H1_H2_complement_subset_unknown_incomplete_eligible":
                (count_sum/plan['repetitions']).tolist()}


def power_cell(plan, blocks, trades, cell):
    rows = []
    levels = np.array(plan['levels'][1])
    for e, effect in enumerate(plan['effects']):
        rng = np.random.default_rng(np.random.SeedSequence([plan['seed'], 1, cell, e]))
        count = np.zeros(3, int)
        contrasts_sum = np.zeros(2)
        for _ in range(plan['power_repetitions']):
            c = generate(rng, 'iid', blocks, trades, (effect, effect))
            b = bootstrap(c, plan['draws'], rng, decision_alpha=float(levels.max()))
            passed = (b['p'] <= levels) & (b['delta'] > 0)
            count[:2] += passed
            count[2] += passed.all()
            contrasts_sum += b['delta']
        rates = {name: rate(count[j], plan['power_repetitions'], plan['error_per_bound'])
                 for j, name in enumerate(('H1', 'H2', 'both'))}
        rows.append({"coefficient_R_per_label": effect,
                     "mean_observed_contrasts_R": (contrasts_sum/plan['power_repetitions']).tolist(), **rates})
    detectable = [r for r in rows if r['both']['lower'] >= .8]
    return {"blocks": blocks, "trades_per_block": trades, "alphas": levels.tolist(),
            "rows": rows, "minimum_demonstrated_grid_coefficient_R":
                detectable[0]['coefficient_R_per_label'] if detectable else None,
            "mde_scope": "80% joint power lower bound; iid generator only; no interpolation"}


def stream_cell(plan, model, cell):
    rng = np.random.default_rng(np.random.SeedSequence([plan['seed'], 2, cell]))
    any_false_row = any_false_admission = selected_streams = conditional_false = 0
    selected_candidates = deferred = 0
    fdp = []
    alpha_min, alpha_max = 1., 0.
    for _ in range(plan['stream_repetitions']):
        t, history, false_rows, total_rows = 1, [], 0, 0
        false_admit, selected = False, False
        # Reuse the same population across all five candidate looks: an
        # explicit maximal reuse stress, not five independent market samples.
        c = generate(rng, 'iid')
        active = None
        if model != 'all_null':
            h1 = rng.integers(2, size=len(c.r))
            h2 = np.logical_xor(h1, rng.random(len(c.r)) < .2).astype(int)
            c.labels = np.column_stack((h1, h2)).astype(np.int8)
            active = 0 if model == 'H1_only' else 1
            # P(Hother=1|Hactive=1)-P(Hother=1|Hactive=0)=.6.
            # Residualization leaves the other *marginal mean contrast* zero
            # in the population while maintaining dependent partitions.
            c.r += 2*(c.labels[:, active]-.6*c.labels[:, 1-active])
        for candidate in range(plan['stream_candidates']):
            a1 = alpha_at(t, history)
            pg = gate1_proxy(c, rng, shift=0. if model == 'all_null' else 2.)
            gate1_reject = pg <= a1
            if gate1_reject:
                history.append(t)
                total_rows += 1
                false_rows += model == 'all_null'
            t += 1
            if not gate1_reject:
                continue
            selected = True
            selected_candidates += 1
            levels = family_levels(t, history)
            alpha_min, alpha_max = min(alpha_min, *levels), max(alpha_max, *levels)
            draws = draws_for(min(levels), plan['draw_cap'])
            if draws is None:
                deferred += 1
                continue  # whole family defers before its synthetic evaluation
            b = bootstrap(c, draws, rng, decision_alpha=max(levels))
            rejected = (b['p'] <= levels) & (b['delta'] > 0)
            for h in range(2):
                if rejected[h]:
                    history.append(t+h)
                    total_rows += 1
                    false_rows += model == 'all_null' or h != active
            false_admit |= bool(rejected.all())
            t += 2
        any_false_row += false_rows > 0
        any_false_admission += false_admit
        selected_streams += selected
        conditional_false += selected and false_admit
        fdp.append(false_rows/max(total_rows, 1))
    n = plan['stream_repetitions']
    err = plan['error_per_bound']
    return {"model": model, "selector": "maximum of three Gaussian tail probabilities coupling PROXY",
            "any_false_row": rate(any_false_row, n, err),
            "any_false_admission": rate(any_false_admission, n, err),
            "false_admission_given_any_selection": rate(conditional_false, selected_streams, err),
            "selected_candidates": selected_candidates, "deferred_families": deferred,
            "alpha_range": [alpha_min, alpha_max] if selected_candidates else None,
            "stream_FDR_estimate": float(np.mean(fdp)),
            "stream_FDR_Hoeffding_upper": min(1., float(np.mean(fdp))+math.sqrt(math.log(1/err)/(2*n))),
            "scope": "independent stream replicates; no production selection guarantee; no approved family target"}


def diagnostic_cell(plan):
    rng = np.random.default_rng(np.random.SeedSequence([plan['seed'], 3]))
    days = [30, *plan['diagnostic_block_days']]
    events = np.zeros((3, 3), int)
    disagreements = np.zeros(2, int)
    levels = np.array(plan['levels'][1])
    for _ in range(plan['diagnostic_repetitions']):
        c = generate(rng, 'long_memory', 48)
        decisions = []
        for i, day in enumerate(days):
            b = bootstrap(c, plan['draws'], rng, day, decision_alpha=float(levels.max()))
            r = (b['p'] <= levels) & (b['delta'] > 0)
            events[i, :2] += r
            events[i, 2] += r.all()
            decisions.append(r)
        for i in range(2):
            disagreements[i] += bool(np.any(decisions[0] != decisions[i+1]))
    return {"scenario": "long_memory", "calendar_span_days": 1440,
            "charged_block_days": 30, "diagnostic_only": [60, 90],
            "results": [{"block_days": day, **{
                key: rate(events[i, j], plan['diagnostic_repetitions'],
                          plan['error_per_bound'], float(levels[j] if j < 2 else min(levels)))
                for j, key in enumerate(('H1', 'H2', 'both'))}}
                for i, day in enumerate(days)],
            "disagreement_with_30": disagreements.tolist()}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--smoke', action='store_true')
    parser.add_argument('--seed', type=int, default=20260917)
    parser.add_argument('--draw-cap', type=int, default=10000)
    args = parser.parse_args(argv)
    plan = make_plan(args.seed, args.smoke, args.draw_cap)
    out = args.output_dir.resolve()
    # New directory only: never overwrite a previous artifact or dossier.
    if out == ROOT/'data' or ROOT/'data' in out.parents:
        parser.error('output must not be inside data/')
    out.mkdir(parents=True, exist_ok=False)
    def write(name, value):
        with (out/name).open('x') as f:
            json.dump(value, f, indent=2, sort_keys=True, allow_nan=False)
            f.write('\n')
    source_files = [Path(__file__), ROOT/'trader/research/fdr.py',
                    ROOT/'tests/test_gate2_calibrate_studentized.py',
                    ROOT/'docs/superpowers/specs/2026-09-16-gate2-studentized-calibration.md']
    plan['source_sha256'] = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in source_files}
    plan['numpy_version'] = np.__version__
    write('plan.json', plan)  # freeze before draws
    result = {"schema": SCHEMA, "approval": plan['approval'], "null": [], "power": [], "streams": []}
    jobs = []
    with ProcessPoolExecutor(max_workers=2) as pool:
        for i, scenario in enumerate(plan['scenarios']):
            jobs.append((pool.submit(null_cell, plan, scenario, i), 'null', f'null-{scenario}.json'))
        for i, (b, n) in enumerate((b, n) for b in plan['power_blocks'] for n in plan['power_trades_per_block']):
            jobs.append((pool.submit(power_cell, plan, b, n, i), 'power', f'power-{b}-{n}.json'))
        for i, model in enumerate(plan['stream_models']):
            jobs.append((pool.submit(stream_cell, plan, model, i), 'streams', f'stream-{model}.json'))
        jobs.append((pool.submit(diagnostic_cell, plan), 'diagnostics', 'diagnostics.json'))
        lookup = {job: (group, name) for job, group, name in jobs}
        for job in as_completed(lookup):
            group, name = lookup[job]
            row = job.result()
            write(name, row)
            if group == 'diagnostics':
                result[group] = row
            else:
                result[group].append(row)
            print(f'completed {name}', flush=True)
    result['null'].sort(key=lambda r: plan['scenarios'].index(r['scenario']))
    result['power'].sort(key=lambda r: (r['blocks'], r['trades_per_block']))
    result['streams'].sort(key=lambda r: plan['stream_models'].index(r['model']))
    write('results.json', result)
    write('manifest.json', {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(out.glob('*.json'))})
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
