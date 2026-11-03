"""Development cohort rule: drop only symbols that LACK a required input, never below a fixed floor.

Rule `data-availability-cohort.v1`, fixed here before any outcome is evaluated:

* A symbol is excluded if and only if a required retained input is missing for it over the development window
  (a derivatives series with no eligible rows, or no measured funding for its bars). Availability is the ONLY
  criterion. The rule runs before any bundle is built or any trade is simulated, so no performance, p-value or
  ranking can influence who is excluded.
* Only gaps that can be attributed to specific symbols are eligible. Any other gap (world, reference, BTC context,
  a missing snapshot, a contract limit) still blocks the whole study, exactly as before.
* The kept cohort must stay at or above `max(policy min_discovery_symbols, control.MIN_CONTROL_SYMBOLS)`. At the
  configured policy that is 16 discovery symbols and never fewer than 12 control symbols. If excluding would go
  below it, NOTHING is excluded and the study stays BLOCKED_DATA with the would-be exclusions recorded.
* Every exclusion is recorded as structured evidence (symbol, missing inputs, measured coverage, reason code) in
  the result and logged. The cohort identity is folded into the data identity, so a reduced-cohort result can
  never be mistaken for, or cached as, a full-cohort one.

This changes which symbols a development study uses. It grants no eligibility and spends no held-out look.
"""
from __future__ import annotations

import logging

from .. import control, slices
from . import canonical, digest

log = logging.getLogger(__name__)

RULE_ID = 'data-availability-cohort.v1'


def floor_for(env) -> dict:
    policy_floor = int(env['policy']['min_discovery_symbols'])
    return dict(discovery=policy_floor, control=int(control.MIN_CONTROL_SYMBOLS),
                effective=max(policy_floor, int(control.MIN_CONTROL_SYMBOLS)))


def _rows(frame) -> int:
    try:
        return int(len(frame))
    except TypeError:
        return 0


def _coverage(sym, frames, derivs, series_for, needed) -> dict:
    series = {}
    for requirement in sorted(needed):
        name = series_for[requirement]
        frame = (derivs.get(sym) or {}).get(name)
        rows = _rows(frame) if frame is not None else 0
        entry = dict(rows=rows)
        if rows:
            ms = slices._ms(frame)
            entry.update(first_ms=int(ms.min()), last_ms=int(ms.max()))
        series[requirement] = entry
    return dict(bars=_rows(frames.get(sym)), series=series)


def decide(frames, derivs, gaps, required, series_for, floor: dict) -> dict:
    """Pure decision. Returns status, excluded entries and the gaps that remain after exclusion."""
    series_required = sorted(r for r in required if r in series_for)
    per_symbol: dict = {}
    explained, global_gaps = [], []
    for gap in gaps:
        requirement = gap.get('requirement')
        named = [s for s in (gap.get('symbols') or []) if s in frames]
        if named:
            for sym in named:
                per_symbol.setdefault(sym, set()).add(requirement)
            explained.append(gap)
        elif requirement in series_required:
            missing = [s for s in frames if not _rows((derivs.get(s) or {}).get(series_for[requirement]))]
            if not missing:
                global_gaps.append(gap)          # cannot be attributed to any symbol: stays blocking
                continue
            for sym in missing:
                per_symbol.setdefault(sym, set()).add(requirement)
            explained.append(gap)
        else:
            global_gaps.append(gap)
    if not gaps:
        return dict(status='NONE', excluded=[], remaining_gaps=[])
    if global_gaps:
        return dict(status='BLOCKED_GLOBAL', excluded=[], remaining_gaps=list(gaps))
    excluded = []
    for sym in sorted(per_symbol):
        missing = sorted(per_symbol[sym])
        excluded.append(dict(
            symbol=sym, missing=missing,
            code=f"symbol_excluded_missing_input:{sym}:{','.join(missing)}",
            coverage=_coverage(sym, frames, derivs, series_for, [m for m in missing if m in series_for])))
    kept = len(frames) - len(excluded)
    status = 'EXCLUDE' if kept >= floor['effective'] else 'FLOOR_BREACHED'
    return dict(status=status, excluded=excluded, kept=kept,
                remaining_gaps=[] if status == 'EXCLUDE' else list(gaps))


def apply_exclusion(frames: dict, result: dict, required, env, series_for, *, stage: str) -> None:
    """Apply the rule to `frames` and the loaded `result` in place; record the cohort evidence on `result`."""
    gaps = result.get('gaps') or []
    if not gaps:
        return
    floor = floor_for(env)
    decision = decide(frames, result.get('derivs') or {}, gaps, required, series_for, floor)
    if decision['status'] in ('NONE', 'BLOCKED_GLOBAL'):
        return
    prior = result.get('cohort') or {}
    record = dict(rule=RULE_ID, stage=stage, floor=floor, considered=prior.get('considered') or len(frames), kept=decision['kept'],
                  excluded=list(prior.get('excluded', [])) + decision['excluded'], status=decision['status'])
    record['cohort_id'] = digest({k: v for k, v in record.items() if k != 'cohort_id'})
    result['cohort'] = record
    for entry in decision['excluded']:
        log.warning('closure cohort %s %s', decision['status'], canonical(entry))
    if decision['status'] != 'EXCLUDE':
        return                                        # floor would be breached: keep every gap, study stays blocked
    for entry in decision['excluded']:
        frames.pop(entry['symbol'], None)
        (result.get('derivs') or {}).pop(entry['symbol'], None)
    result['gaps'] = decision['remaining_gaps']
    result['data_id'] = digest([result['data_id'], record['cohort_id']])
    log.info('closure cohort rule=%s stage=%s kept=%d excluded=%d floor=%s', RULE_ID, stage, record['kept'],
             len(decision['excluded']), canonical(floor))
