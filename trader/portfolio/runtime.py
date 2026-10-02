"""Normal proposal-only runtime consumer with atomic durable checkpoints.

One frozen whole-book cut feeds the existing event gate, allocator, TradeIntent
and Risk. The ledger is separate from the trading journal. It has no Execution,
exchange, protective-order, activation or control-plane write capability.
"""
from dataclasses import asdict
import json
from pathlib import Path
import sqlite3
import time

from .allocator import canonical, digest, inputs_from_payload, Proposal, allocate, verify
from . import reoptimization as gate
from trader.engine.risk_intent import evaluate as risk_evaluate, replay as risk_replay, RiskDecision

SCHEMA = 'runtime-portfolio-checkpoint.v1'


class Consumer:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS state(id INTEGER PRIMARY KEY CHECK(id=1), inputs_json TEXT,
                previous_proposal_id TEXT);
            CREATE TABLE IF NOT EXISTS checkpoints(event_id TEXT PRIMARY KEY, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS evaluations(cut_id TEXT PRIMARY KEY, payload TEXT NOT NULL);
            CREATE TRIGGER IF NOT EXISTS checkpoint_no_update BEFORE UPDATE ON checkpoints
                BEGIN SELECT RAISE(ABORT,'checkpoint immutable'); END;
            CREATE TRIGGER IF NOT EXISTS checkpoint_no_delete BEFORE DELETE ON checkpoints
                BEGIN SELECT RAISE(ABORT,'checkpoint immutable'); END;
            CREATE TRIGGER IF NOT EXISTS evaluation_no_update BEFORE UPDATE ON evaluations
                BEGIN SELECT RAISE(ABORT,'evaluation immutable'); END;
            CREATE TRIGGER IF NOT EXISTS evaluation_no_delete BEFORE DELETE ON evaluations
                BEGIN SELECT RAISE(ABORT,'evaluation immutable'); END;
            ''')

    def _db(self):
        return sqlite3.connect(self.path, timeout=5)

    def consume(self, current, *, processed_at=None):
        raw = gate._cut(current)
        cut = digest(raw)
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            saved = db.execute('SELECT payload FROM evaluations WHERE cut_id=?', (cut,)).fetchone()
            if saved:
                checked = replay(json.loads(saved[0]))
                return dict(checked, duplicate=True)
            row = db.execute('SELECT inputs_json,previous_proposal_id FROM state WHERE id=1').fetchone()
            previous = inputs_from_payload(json.loads(row[0])) if row else None
            previous_id = row[1] if row else None
            # IDs are append-only, and the previous cut prevents regeneration
            # of a processed setup after clock-only refresh or process restart.
            expected_events = json.loads(gate.evaluate(previous,current).result_json)['events']
            processed = tuple(e['event_id'] for e in expected_events if db.execute(
                'SELECT 1 FROM checkpoints WHERE event_id=?',(e['event_id'],)).fetchone())
            event, proposal, intents = gate.reoptimize(previous, current, processed_ids=processed)
            decisions = tuple(risk_evaluate(i, proposal, current) for i in intents) if proposal else ()
            for decision in decisions:
                risk_replay(decision)
            if gate.replay(event) != event or proposal and not verify(proposal, current):
                raise ValueError('RUNTIME_REPLAY_REFUSED')
            candidate_id = digest([asdict(c) for c in sorted(current.candidates,key=lambda c:c.identity)])
            events = json.loads(event.result_json)['events']
            stamp = int(time.time()*1000) if processed_at is None else processed_at
            if type(stamp) is not int or stamp < current.as_of_ms:
                raise ValueError('CHECKPOINT_CLOCK_INVALID')
            for e in events:
                payload = dict(schema=SCHEMA, event_id=e['event_id'], portfolio_cut_id=cut,
                    portfolio_snapshot_id=current.portfolio.snapshot_id,
                    candidate_set_id=candidate_id, candidate_set_sha256=candidate_id,
                    trigger_reason=e['kind'] if e['trigger'] else e['reason'], reason_codes=e['reason_codes'],
                    previous_proposal_id=previous_id, resulting_proposal_id=proposal.proposal_id if proposal else None,
                    processed_at=stamp, event=e, triggered=e['trigger'])
                db.execute('INSERT OR IGNORE INTO checkpoints VALUES (?,?)', (e['event_id'],canonical(payload)))
            body = dict(schema=SCHEMA, portfolio_cut_id=cut, candidate_set_sha256=candidate_id,
                proposal=proposal.payload() if proposal else None,
                trade_intents=[asdict(i) for i in intents], risk_decisions=[asdict(d) for d in decisions],
                event_receipt=event.payload(), checkpointed_event_ids=[e['event_id'] for e in events],
                triggered=proposal is not None, execution_routed=False, real_order_submissions=0,
                replay='PASS', duplicate=False)
            db.execute('INSERT INTO evaluations VALUES (?,?)', (cut,canonical(body)))
            db.execute('INSERT OR REPLACE INTO state VALUES (1,?,?)',
                (canonical(raw),proposal.proposal_id if proposal else previous_id))
            return body


def replay(body):
    from .reoptimization import EventReceipt
    event = body['event_receipt']
    r = EventReceipt(event['receipt_id'],canonical(event['inputs']),canonical(event['result']))
    gate.replay(r)
    inputs = inputs_from_payload(event['inputs']['current'])
    triggered = event['result']['trigger']
    if triggered != body['triggered']:
        raise ValueError('RUNTIME_TRIGGER_REPLAY_REFUSED')
    if triggered:
        from .trade_intent import build
        proposal = allocate(inputs)
        intents = build(proposal, inputs)
        decisions = tuple(risk_evaluate(i,proposal,inputs) for i in intents)
        if (proposal.payload() != body['proposal'] or [asdict(i) for i in intents] != body['trade_intents']
                or [asdict(d) for d in decisions] != body['risk_decisions']):
            raise ValueError('RUNTIME_RESULT_REPLAY_REFUSED')
    elif body['proposal'] or body['trade_intents'] or body['risk_decisions']:
        raise ValueError('RUNTIME_UNTRIGGERED_WORK_REFUSED')
    return body
