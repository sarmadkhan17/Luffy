#!/usr/bin/env python3
"""Explicit read-only Stage-6 audit, not a recurring optimizer/activation."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.common_factor_shadow import run as book_run
from trader.portfolio.allocator import Proposal, canonical, digest, inputs_from_payload, verify
from trader.portfolio.reoptimization import evaluate, replay as replay_events
from trader.portfolio.trade_intent import build as build_intents, verify_intents

PACKAGE = 'LUFFY-STAGE6-IMPLEMENTATION-CLOSURE-R1'


def audit(proposal, current_inputs, previous=None):
    if not verify(proposal, current_inputs):
        raise ValueError('STAGE6_PROPOSAL_CURRENT_SOURCES_REFUSED')
    event = evaluate(previous, current_inputs)
    if replay_events(event) != event:
        raise ValueError('STAGE6_EVENT_REPLAY_REFUSED')
    intents = build_intents(proposal, current_inputs)
    if not verify_intents(intents, proposal, current_inputs):
        raise ValueError('STAGE6_INTENT_REPLAY_REFUSED')
    result = json.loads(proposal.result_json)
    body = dict(package=PACKAGE, as_of_ms=current_inputs.as_of_ms,
        proposal=proposal.payload(), event_receipt=event.payload(),
        trade_intents=[i.payload() for i in intents], opportunity_cost=result['opportunity_cost'],
        candidate_count=len(current_inputs.candidates), holdings_count=len(current_inputs.portfolio.positions),
        decision=result['decision'], cash_selected=result['cash_candidate']['selected'],
        expected_net_economics='UNAVAILABLE' if not result['selected'] else 'SEE_EXACT_RECEIPTS',
        event_driven_runtime_activated=False, real_execution_routed=False,
        replay='PASS', production_mutations=0, authenticated_requests=0, trading_behavior_changed=False)
    return dict(receipt_id=digest(body), **body)


def run(journal, attention, investigation, config, output):
    book = book_run(journal, attention, investigation, config, output / 'book-chain')
    raw = json.loads(Path(book['proposal_path']).read_text())
    proposal = Proposal(raw['proposal_id'], canonical(raw['inputs']), canonical(raw['result']), raw['allocator_version'])
    inputs = inputs_from_payload(raw['inputs'])
    body = audit(proposal, inputs)
    target = output / (body['receipt_id'] + '.json')
    text = canonical(body) + '\n'
    try:
        with target.open('x') as f:
            f.write(text)
    except FileExistsError:
        if target.read_text() != text:
            raise ValueError('IMMUTABLE_STAGE6_AUDIT_COLLISION')
    return dict(package=PACKAGE, status='PASS', artifact=str(target), receipt_id=body['receipt_id'],
                as_of_ms=inputs.as_of_ms, candidate_count=body['candidate_count'],
                holdings_count=body['holdings_count'], context_count=book['context_count'],
                decision=body['decision'], cash_selected=body['cash_selected'],
                trade_intent_count=len(body['trade_intents']), replay='PASS',
                control_state=inputs.control_state, authenticated_requests=0,
                production_mutations=0, trading_behavior_changed=False)


def main():
    import sqlite3
    import yaml
    p = argparse.ArgumentParser()
    for name in ('journal', 'attention', 'investigation', 'config', 'output'):
        p.add_argument('--' + name, type=Path, required=True)
    a = p.parse_args()
    cfg = yaml.safe_load(a.config.read_text())
    safe = {k: cfg.get(k, {}) for k in ('attention', 'risk', 'strategies')}
    try:
        result = run(a.journal, a.attention, a.investigation, safe, a.output)
    except (ValueError, TypeError, KeyError, sqlite3.Error) as exc:
        result = dict(package=PACKAGE, status='BLOCKED', blocker=str(exc),
                      production_mutations=0, authenticated_requests=0, trading_behavior_changed=False)
    print(canonical(result))
    return 0 if result['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
