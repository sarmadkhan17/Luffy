"""P6 — a genuine QUALIFIED packet -> one VALIDATED Factory version.

Reuses `factory_transfer.transfer` (spent looks copied verbatim into the
Journal's own error budget) and `factory_handoff.create_version` with the
`historical_qualification` source. Stops at VALIDATED with execution
authority NONE: no Analyst, install, paper, live or order call exists here.
All checks run BEFORE anything is written, so a TEST_ONLY or weaker-than-
configured packet leaves the Journal untouched.
"""
from __future__ import annotations

import time

from ...strategy import factory_handoff as fh
from .. import factory_transfer as ft
from . import ClosureRefused


def transfer_packet(q: dict) -> dict:
    led, h = q['ledger'], q['freeze']['combo_hash']
    look = next(t for t in led['tests'] if t['hash'] == h and t['gate'] == 'gate1')
    pkt = dict(schema=ft.SCHEMA, result_id=q['qualification_id'], hypothesis_id=q['freeze']['candidate']['candidate_id'],
               experiment_id=q['envelope']['envelope_id'], hash=h, tf=look['tf'], geo=look['geo'], look_seq=look['seq'],
               tests=[t for t in led['tests'] if t['seq'] <= look['seq']], budget=led['budget'],
               candidate=led['candidate'], combo=led['combo'])
    return dict(pkt, transfer_id=ft._sha(pkt))


def hand_off(journal, cfg: dict, packet: dict, *, at_ms: int | None = None) -> dict:
    """Idempotent. Returns status VALIDATED (execution_authority NONE) or
    REFUSED/BLOCKED with a stable reason code; retry after an interruption
    between transfer and version creation completes the missing step only."""
    at_ms = int(time.time() * 1000) if at_ms is None else at_ms
    try:
        q = fh._verified_historical(cfg, packet)
        tp = transfer_packet(q)
        moved = ft.transfer(journal, tp)
        ver = fh.create_version(journal, cfg, {'kind': fh.HQ_KIND, 'hash': q['freeze']['combo_hash'], 'packet': packet},
                                at_ms=at_ms)
    except fh.HandoffRefused as exc:
        return dict(status='REFUSED', reason_code=exc.code, execution_authority='NONE')
    except ft.TransferRefused as exc:
        return dict(status='REFUSED', reason_code='transfer:' + exc.code, execution_authority='NONE')
    except (ClosureRefused, KeyError, TypeError, ValueError) as exc:
        return dict(status='REFUSED', reason_code='packet_malformed:' + type(exc).__name__, execution_authority='NONE')
    out = dict(status='VALIDATED', qualification_id=q['qualification_id'], research_hash=tp['hash'],
               transfer_id=tp['transfer_id'], transfer_status=moved['status'], version_id=ver['version_id'],
               validation_receipt_id=ver['validation_receipt_id'], outcome=ver['status'], execution_authority='NONE')
    if ver['status'] != 'duplicate':
        journal.log_brain_event('research_factory_validated', q['qualification_id'], out)
    return out


def hand_off_hedge(journal, cfg: dict, packet: dict, *, at_ms: int | None = None) -> dict:
    """A QUALIFIED hedge/relative-value packet -> one VALIDATED multi-leg Factory version, execution authority NONE.

    A method must have current registered component and full-search calibration before a CONFIGURED packet can qualify.
    Otherwise this returns REFUSED with the exact code (hq_hedge_status_not_qualified:<status> / hq_hedge_admission_not_registered ...). It never converts an
    ASSESSED_* result, a TEST_ONLY packet or an uncalibrated run into a version, and it grants no paper/live authority: a
    version carrying a `multi_leg` block cannot be installed or live-authorized by the single-leg Factory path."""
    at_ms = int(time.time() * 1000) if at_ms is None else at_ms
    try:
        fh._verified_hedge(cfg, packet)
        ver = fh.create_version(journal, cfg, {'kind': fh.HEDGE_HQ_KIND, 'packet': packet}, at_ms=at_ms)
    except fh.HandoffRefused as exc:
        return dict(status='REFUSED', reason_code=exc.code, execution_authority='NONE')
    except (ClosureRefused, KeyError, TypeError, ValueError) as exc:
        return dict(status='REFUSED', reason_code='packet_malformed:' + type(exc).__name__, execution_authority='NONE')
    out = dict(status='VALIDATED', qualification_id=packet['qualification_id'], version_id=ver['version_id'],
               validation_receipt_id=ver['validation_receipt_id'], outcome=ver['status'], execution_authority='NONE',
               multi_leg=True, execution='coordinated caller required; code support is not qualification, installation or owner/Risk authority')
    if ver['status'] != 'duplicate':
        journal.log_brain_event('research_factory_validated_hedge', packet['qualification_id'], out)
    return out
