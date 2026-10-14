"""Versioned, conservative within-instrument selection; no voting or authority.

Different strategy identities cannot be ranked without an approved comparison
policy. Preserve all contributors and refuse ambiguous owners, including agreeing
strategies with unknown/different plans. Duplicating one signal adds no authority.
"""
from __future__ import annotations

import json
import math

VERSION = 'strategy-selection.sg06.v1'


def _record(signal):
    value = dict(signal) if isinstance(signal, dict) else vars(signal).copy()
    value['action'] = getattr(value.get('action'), 'value', value.get('action'))
    return value


def select(signals: list) -> dict:
    """A replayable selection receipt derived only from the emitted signals."""
    records = [_record(s) for s in signals or []]
    contributors = sorted({s.get('strategy_id') for s in records
                           if isinstance(s.get('strategy_id'), str) and s['strategy_id']})
    receipt = dict(version=VERSION, status='REFUSED', owner='', direction='HOLD',
                   basis='NO_ELIGIBLE_STRATEGY', contributors=contributors)
    if not records:
        return receipt
    try:
        identities = []
        for s in records:
            confidence = s.get('confidence')
            if (not isinstance(s.get('symbol'), str) or not s['symbol']
                    or not isinstance(s.get('strategy_id'), str) or not s['strategy_id']
                    or s.get('action') not in ('BUY', 'SELL')
                    or type(confidence) not in (float, int)
                    or not math.isfinite(confidence) or not 0 <= confidence <= 1):
                raise ValueError('invalid signal')
            identities.append(json.dumps({k:s.get(k) for k in
                ('strategy_id', 'symbol', 'action', 'confidence', 'params')},
                sort_keys=True, separators=(',', ':'), allow_nan=False))
        if len({s.get('symbol') for s in records}) != 1:
            raise ValueError('different instruments')
    except (TypeError, ValueError):
        receipt['basis'] = 'INVALID_STRATEGY_SIGNAL'
        return receipt
    if len({s['action'] for s in records}) > 1:
        receipt['basis'] = 'OPPOSING_STRATEGIES_NO_JUSTIFIED_COMPARISON'
    elif len(contributors) > 1:
        receipt['basis'] = 'PLAN_OWNER_UNRESOLVED_NO_COMPARABLE_EVIDENCE'
    elif len(set(identities)) > 1:
        receipt['basis'] = 'INCONSISTENT_SIGNALS_FOR_ONE_OWNER'
    else:
        receipt.update(status='SELECTED', owner=contributors[0],
                       direction=records[0]['action'], basis='SOLE_STRATEGY_CONTRACT')
    return receipt


def verified_selection(signals: list, receipt: dict) -> dict:
    if not isinstance(receipt, dict) or receipt != select(signals):
        raise ValueError('strategy_selection_receipt_mismatch')
    return receipt


def historical_owner(signals: list, action) -> str:
    """fc6c0b2 owner rule for UNVERSIONED historical records, never new selection."""
    action = getattr(action, 'value', action)
    agree = [s for s in map(_record, signals or []) if s.get('action') == action]
    return max(agree, key=lambda s:s.get('confidence', 0)).get('strategy_id', '') if agree else ''


def decision_owner(decision) -> str:
    action = getattr(decision.action, 'value', decision.action)
    if action == 'HOLD':
        return ''
    receipt = getattr(decision, 'strategy_selection', None)
    if receipt is None:
        # Old stored Decisions predate receipts. Preserve their original algorithm.
        return historical_owner(decision.strategy_signals, action)
    receipt = verified_selection(decision.strategy_signals, receipt)
    return receipt['owner'] if receipt['status'] == 'SELECTED' and receipt['direction'] == action else ''


def exit_score(decision, trade):
    """Only the held plan's own justified hypothesis may drive legacy flip exits."""
    receipt = getattr(decision, 'strategy_selection', None)
    if receipt is None:
        return decision.score  # original historical contract
    try:
        receipt = verified_selection(decision.strategy_signals, receipt)
    except ValueError:
        return None  # refuse score influence; ordinary stops/trails/exits still run
    return decision.score if receipt['status'] == 'SELECTED' and receipt['owner'] == trade.get('strategy_id') else None
