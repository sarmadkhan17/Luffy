"""Canonical display metadata for scan telemetry, never trading authority."""
from __future__ import annotations

import math

SCHEMA = 'scan-result-classification.v1'
INELIGIBLE = frozenset(('ineligible_regime', 'ineligible_state', 'ineligible_market', 'ineligible_symbol',
                        'NO_ELIGIBLE_STRATEGY'))


def reason_category(reason):
    if reason in INELIGIBLE:
        return 'not_applicable'
    if reason == 'emitted_signal':
        return 'signal'
    if reason == 'returned_none':
        return 'no_signal'
    if reason == 'INPUT_UNAVAILABLE' or reason.startswith(('required_input_missing:', 'missing:', 'no_snapshot:')):
        return 'inputs_unavailable'
    if reason in ('pre_filtered', 'prefiltered', 'pre-filtered') or reason.startswith(('pre_filtered:', 'prefiltered:')):
        return 'prefiltered'
    if reason in ('blocked', 'execution_recovery_pending', 'entries not allowed') or reason.startswith(('blocked:', 'state=')):
        return 'blocked'
    # evaluation_failed, not_invoked and future unknown reasons are incomplete.
    return 'checks_incomplete'


def classify_scan_result(coin):
    codes = []
    malformed = False
    checks = coin.get('strategy_checks')
    if checks is not None:
        if not isinstance(checks, list):
            malformed = True
        else:
            for check in checks:
                reasons = check.get('reasons') if isinstance(check, dict) else None
                if not isinstance(reasons, list) or not reasons:
                    malformed = True
                    continue
                for reason in reasons:
                    if isinstance(reason, str) and reason:
                        codes.append(reason)
                    else:
                        malformed = True
    codes = list(dict.fromkeys(codes))
    kinds = {reason_category(code) for code in codes}
    skip = coin.get('skip_reason')
    skip = skip if isinstance(skip, str) else ''
    action = coin.get('action')
    action = action if isinstance(action, str) and action else 'Unknown'
    score = coin.get('score')
    scored = type(score) in (int, float) and math.isfinite(score) and score != 0
    evaluated = coin.get('evaluated') is not False and action != 'NOT_EVALUATED'
    metrics = evaluated and (action != 'HOLD' or scored)

    def result(category, label, description, tone='neutral'):
        return dict(schema=SCHEMA, category=category, label=label, description=description,
                    tone=tone, show_metrics=bool(metrics),
                    reason_codes=list(dict.fromkeys(([skip] if skip else []) + codes)))

    if coin.get('executed') is True:
        return result('order_sent', 'Order sent', 'An order submission was recorded.', 'positive')
    skip_kind = reason_category(skip) if skip else None
    if skip_kind == 'not_applicable':
        metrics = False
        return result('not_applicable', 'Not applicable', 'No strategy was eligible for this observation.')
    if skip_kind in ('blocked', 'prefiltered'):
        metrics = metrics and skip_kind != 'prefiltered'
        return result(skip_kind, 'Blocked' if skip_kind == 'blocked' else 'Pre-filtered',
                      'A recorded restriction prevented further processing.', 'warning')
    if not evaluated:
        metrics = False
        return result('inputs_unavailable', 'Inputs unavailable', 'This coin was not evaluated.', 'warning')
    if action in ('BUY', 'SELL') or scored:
        return result('signal' if action in ('BUY', 'SELL') else 'decision', action,
                      'Recorded decision; this label does not authorize an order.')
    metrics = False
    if malformed or 'checks_incomplete' in kinds or 'inputs_unavailable' in kinds:
        return result('checks_incomplete', 'Checks incomplete', 'Some strategy checks failed or lacked required inputs.', 'warning')
    if not kinds:
        return result('checks_unavailable', 'Checks unavailable', 'No strategy-check receipts were retained.', 'warning')
    if kinds == {'not_applicable'}:
        return result('not_applicable', 'Not applicable', 'All retained checks were ineligible.')
    if kinds <= {'no_signal', 'not_applicable'}:
        return result('no_signal', 'No signal', 'No strategy output; false predicates and unreported inputs are not distinguished.')
    if 'blocked' in kinds or 'prefiltered' in kinds:
        if len(kinds) == 1:
            kind = next(iter(kinds))
            return result(kind, 'Blocked' if kind == 'blocked' else 'Pre-filtered', 'Checks were prevented from proceeding.', 'warning')
        return result('checks_incomplete', 'Checks incomplete', 'Some checks were blocked or filtered.', 'warning')
    return result('signal', 'Signal emitted', 'A strategy emitted an output; no order submission is implied.')
