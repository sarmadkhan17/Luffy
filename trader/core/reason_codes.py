"""Decision rejection reason codes — vocabulary decision-rejection-reason.v1.

A code names the code branch that refused an entry. It is not a judgement
that the opportunity was good, bad, missed or costly. Each code is set at the
branch that writes the matching `skip_reason` text, in the same statement
group, so text and code always describe the same refusal. Codes are never
derived by parsing text, and the numeric/context detail stays in the text.

Decision.reason_codes is an ordered list mirroring skip_reason composition.
Journal semantics of `decisions.reason_codes` (frozen for v1):

* SQL NULL — reason codes were not recorded / legacy unknown (rows written
  before this vocabulary existed, or by a writer that did not classify).
* explicit `[]` — the reason-code field was recorded, but no coded
  text-producing refusal was present.

`[]` is NOT "no refusal", "all checks passed" or "executed". Some paths
return or fail without writing any skip_reason (e.g. zero-lot quantization,
order submission failure) and so carry `[]` while nothing was executed. Read
`executed` for the outcome; never infer success from an empty code list.
"""
from __future__ import annotations

import json

VERSION = "decision-rejection-reason.v1"

# ── orchestrator.decide ─────────────────────────────────────────────────
STRATEGY_NO_SIGNAL = "strategy_no_signal"            # strategy_gate: no signals
STRATEGY_NO_AGREEMENT = "strategy_no_agreement"      # strategy_gate: none agree
HTF_TREND_HARD_VETO = "htf_trend_hard_veto"          # 4h trend opposes, |s| ≥ veto
META_VETO = "meta_veto"                              # meta p < META_FLOOR
ENTRIES_NOT_ALLOWED = "entries_not_allowed"          # caller disallowed, no code given

# ── kernel cycle: why entries were disallowed before decide() ───────────
CONTROL_STATE_NOT_ACTIVE = "control_state_not_active"
EXECUTION_RECOVERY_PENDING = "execution_recovery_pending"
DAILY_LOSS_BREAKER = "daily_loss_breaker"
RISK_HALT_IN_CYCLE = "risk_halt_in_cycle"            # HALTED after a RiskError this cycle

# ── risk.check_entry (SizingResult.code) ────────────────────────────────
RISK_STATE_FROZEN = "risk_state_frozen"
RISK_STATE_HALTED = "risk_state_halted"
RISK_STATE_NOT_ACTIVE = "risk_state_not_active"
RISK_DAILY_LOSS_BREAKER = "risk_daily_loss_breaker"
RISK_MAX_POSITIONS = "risk_max_positions"
RISK_ALREADY_EXPOSED = "risk_already_exposed"
RISK_BUDGET_EXHAUSTED = "risk_budget_exhausted"
RISK_MARGIN_CAP = "risk_margin_cap"
RISK_BELOW_MIN_NOTIONAL = "risk_below_min_notional"
RISK_HALT = "risk_halt"                              # RiskError raised by check_entry

# ── kernel._try_enter ───────────────────────────────────────────────────
STOP_ATR_UNAVAILABLE = "stop_atr_unavailable"        # spec-frame bars absent or ATR ≤ 0
META_SIZE_BELOW_MIN_NOTIONAL = "meta_size_below_min_notional"

# ── executor.open / submission fence ────────────────────────────────────
SUBMISSION_RECOVERY_PENDING = "submission_recovery_pending"
FENCE_STATE_FROZEN = "fence_state_frozen"
FENCE_STATE_HALTED = "fence_state_halted"
FENCE_STATE_UNREADABLE = "fence_state_unreadable"
FENCE_STATE_NOT_ACTIVE = "fence_state_not_active"

VOCABULARY = frozenset(v for k, v in dict(globals()).items()
                       if k.isupper() and k != "VERSION" and isinstance(v, str))


def encode(codes) -> str | None:
    """Journal form: JSON list, or NULL when the writer did not classify."""
    if codes is None:
        return None
    return json.dumps(list(codes))


def decode(raw) -> list | None:
    """Tolerant read: NULL / unreadable → None (not recorded), never []."""
    if raw is None:
        return None
    try:
        codes = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return list(codes) if isinstance(codes, list) else None
