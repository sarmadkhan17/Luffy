"""Read-only Brain queue adapter for the maintained predictive bridge.

Consumption belongs to this Bank's research consumer, never the strategy stream.
No passage can enter the Investigation or quantitative source contracts.
"""
from contextlib import closing
from datetime import datetime, timezone
import json
import sqlite3
from pathlib import Path

from trader.brain import ideas
from trader.cognition import predictive as P
from .predictive_experiment import readonly

SCHEMA = 'brain-research-source.v1'


def question(source):
    """Rebuild the sole allowed projection from the exact retained queue row."""
    fields = {k: v for k, v in source.items() if k != 'bank_id'}
    row = source['queue_row']
    passage = json.loads(row['detail'])
    if (source['schema'] != SCHEMA or source['authority'] != 'QUESTION_ONLY'
            or source['bank_id'] != P.digest(fields) or row['kind'] != ideas.KIND
            or row['subject'] != source['idea_id']):
        raise ValueError('brain_source_integrity')
    stamp = datetime.fromisoformat(row['ts'])
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    available = int(stamp.timestamp() * 1000)
    idea = ideas._row_to_idea(row['subject'], row['detail'])
    if (ideas.RESEARCH not in idea['streams'] or len(idea['text']) < ideas.MIN_TEXT
            or idea['research_score'] <= 0 or not idea['source']
            or not idea['url'].startswith(('https://', 'http://'))):
        raise ValueError('brain_source_stream_or_text')
    for consumed in source['consumption_at_intake']:
        if consumed['kind'] != ideas.CONSUMED or consumed['subject'] != source['idea_id']:
            raise ValueError('brain_consumption_binding')
        try:
            detail = json.loads(consumed['detail'])
        except (TypeError, ValueError):
            continue  # Same unreadable-row semantics as the maintained selector.
        if detail.get('stream') in (None, ideas.RESEARCH):
            raise ValueError('brain_source_already_consumed')
    body = dict(available_ms=available, stage=P.QUESTION, authority='QUESTION_ONLY', classification='UNTESTED',
                source_bank_id=source['bank_id'], text='What registered predictor, target, scope and falsifier would test this unverified passage?',
                passage=passage, source_hash=P.digest(row), suppression=None)
    return dict(question_id=P.digest(body), **body)


class Queue:
    """Use the existing selection and per-stream consumption contract unchanged.

    Source rows are read in one SQLite snapshot. The Bank overlays only its own
    research consumption; source consumption rows are retained exactly as read.
    """
    _BATCH = 500  # below SQLite's historical 999-variable limit
    _PENDING = 'SELECT id, ts, kind, subject, detail FROM brain_events e WHERE e.kind=? '
    _CONSUMED_DETAIL = 'SELECT detail FROM brain_events WHERE kind=? AND subject=?'

    def __init__(self, source, bank, append):
        self.source, self.bank, self.append = source, bank, append
        self._consumed = {}  # subject -> exact source consumption rows in id order; one snapshot only

    def _prefetch(self, subjects):
        """One bounded batched scan per 500 subjects instead of one scan per idea.

        The source is a single BEGIN snapshot, so rows read here equal what any
        later per-subject SELECT would return; ORDER BY id is the rowid scan order.
        """
        wanted = list(dict.fromkeys(s for s in subjects if s not in self._consumed))
        for i in range(0, len(wanted), self._BATCH):
            chunk = wanted[i:i + self._BATCH]
            found = {s: [] for s in chunk}
            for r in self.source.execute(
                    'SELECT id,ts,kind,subject,detail FROM brain_events WHERE kind=? AND subject IN (%s) ORDER BY id'
                    % ','.join('?' * len(chunk)), (ideas.CONSUMED, *chunk)):
                found[r['subject']].append(dict(r))
            self._consumed.update(found)

    def consumption(self, idea_id):
        """Exact source consumption rows (id order) for the retained receipt."""
        if idea_id not in self._consumed:
            self._prefetch([idea_id])
        return self._consumed[idea_id]

    def query(self, sql, args=()):
        if sql == self._CONSUMED_DETAIL and args[0] == ideas.CONSUMED and args[1] in self._consumed:
            rows = [{'detail': r['detail']} for r in self._consumed[args[1]]]
        else:
            rows = [dict(r) for r in self.source.execute(sql, args)]
            if sql.startswith(self._PENDING) and args and args[0] == ideas.KIND:
                self._prefetch(r['subject'] for r in rows)
        if args and args[0] == ideas.CONSUMED:
            rows += [json.loads(p) for p, in self.bank.execute(
                'SELECT payload FROM bridge_idea_consumption WHERE json_extract(payload,\'$.subject\')=?', (args[1],))]
        return rows

    def log_brain_event(self, kind, subject, detail):
        assert kind == ideas.CONSUMED and detail['stream'] == ideas.RESEARCH
        event = dict(kind=kind, subject=subject, detail=P.canonical(detail))
        self.append(self.bank, 'bridge_idea_consumption', P.digest(event), event)


def intake(source_path, bank, append, *, now_ms, limit):
    """Atomic accepted/refused receipts; deferred inputs remain eligible on retry."""
    path = str(Path(source_path).resolve())
    counts = dict(accepted=0, refused=0, deferred=0)
    with bank:
        append(bank, 'bridge_runtime_bindings', 'brain-source', dict(path=path))
    source = None
    try:
        source = readonly(source_path)
        source.execute('BEGIN')
        columns = {r[1] for r in source.execute('PRAGMA table_info(brain_events)')}
        if not {'id', 'ts', 'kind', 'subject', 'detail'} <= columns:
            raise ValueError('brain_source_schema')
    except (OSError, ValueError, sqlite3.Error) as exc:
        if source is not None:
            source.close()
        status = 'REFUSED' if isinstance(exc, ValueError) else 'DEFERRED'
        reason = 'brain_source_schema' if status == 'REFUSED' else 'brain_source_unavailable'
        receipt = dict(source_path=path, status=status, reason=reason,
                       observed_ms=now_ms, authority='QUESTION_ONLY')
        key = P.digest([path, reason])
        with bank:
            if not bank.execute('SELECT 1 FROM bridge_intake_outcomes WHERE id=?', (key,)).fetchone():
                append(bank, 'bridge_intake_outcomes', key, receipt)
        counts[status.lower()] = 1
        return counts
    with closing(source):
        source.row_factory = sqlite3.Row
        queue = Queue(source, bank, append)
        selected = ideas.pending(queue, stream=ideas.RESEARCH, limit=limit,
                                 now_ms=now_ms, retain_queue_row=True)
        for idea in selected:
            row = idea['queue_row']
            consumed = queue.consumption(idea['idea_id'])
            fields = dict(schema=SCHEMA, authority='QUESTION_ONLY', source_path=path,
                          idea_id=idea['idea_id'], queue_row=row, consumption_at_intake=consumed)
            retained = dict(bank_id=P.digest(fields), **fields)
            status, reason = 'ACCEPTED', 'question_retained'
            try:
                stamp = datetime.fromisoformat(row['ts'])
                if stamp.tzinfo is None:
                    stamp = stamp.replace(tzinfo=timezone.utc)
                available = int(stamp.timestamp() * 1000)
                if available > now_ms:
                    status, reason = 'DEFERRED', 'source_not_available'
                elif not idea['source'] or not idea['url'].startswith(('https://', 'http://')):
                    status, reason = 'REFUSED', 'source_provenance_missing'
            except (ValueError, TypeError, OverflowError):
                status, reason = 'REFUSED', 'source_timestamp_invalid'
                available = None
            receipt = dict(source_bank_id=retained['bank_id'], source=retained,
                           status=status, reason=reason, available_ms=available,
                           observed_ms=now_ms, authority='QUESTION_ONLY')
            # Exact repeated deferrals have a single stable receipt, preserving
            # first observation rather than accumulating wall-clock duplicates.
            key = P.digest([retained['bank_id'], status, reason])
            with bank:
                if not bank.execute('SELECT 1 FROM bridge_intake_outcomes WHERE id=?', (key,)).fetchone():
                    append(bank, 'bridge_intake_outcomes', key, receipt)
                if status == 'ACCEPTED':
                    append(bank, 'bridge_sources', retained['bank_id'], retained)
                    q = question(retained)
                    append(bank, 'bridge_questions', q['question_id'], q)
                if status != 'DEFERRED':
                    ideas.mark_consumed(queue, idea['idea_id'], status, stream=ideas.RESEARCH,
                                        detail=dict(source_bank_id=retained['bank_id'], reason=reason))
            counts[status.lower()] += 1
    return counts
