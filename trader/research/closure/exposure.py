"""Controlled exposure registry (append-only, hash-chained events).

* `FORWARD_SEAL` events take their clock from this process (`_now`), never
  from a caller: an API/CLI argument cannot backdate a seal. A seal proves
  nothing about data that already existed: `manifest.exposure_state` accepts it
  only when the window's first retained availability is later than the seal.
* `SYNTHETIC_DECLARED` is for generated fixtures; it is honoured only by a
  TEST_ONLY policy and every packet re-derives its proofs from the recorded
  events, so rehashing `policy_class` cannot promote it.
* MARK events (development read, protected look) are appended by the framework.
* Each event carries the previous event id; a rewritten history breaks the
  chain on read. (The store file itself remains the trust anchor: whoever can
  rewrite it wholesale can rewrite the chain — a stated residual, not a claim.)
"""
import time

from . import ClosureRefused, digest

FORWARD_SEAL, SYNTHETIC_DECLARED = 'FORWARD_SEAL', 'SYNTHETIC_DECLARED'


def _now() -> int:
    return int(time.time() * 1000)


def verify_chain(events: list) -> list:
    prev = None
    for e in events:
        body = {k: v for k, v in e.items() if k != 'event_id'}
        if e.get('event_id') != digest(body) or body.get('prev') != prev:
            raise ClosureRefused('exposure_chain_broken')
        prev = e['event_id']
    return events


def events(store) -> list:
    return verify_chain(store.all_once('exposure'))


def _append(store, ev):
    with store._tx() as c:            # chain head read and append are one transaction
        rows = [__import__('json').loads(r[0]) for r in c.execute('SELECT payload FROM exposure ORDER BY rowid')]
        ev = dict(ev, prev=rows[-1]['event_id'] if rows else None)
        ev = dict(ev, event_id=digest(ev))
        from . import canonical
        c.execute('INSERT INTO exposure VALUES (?,?)', (ev['event_id'], canonical(ev)))
    return ev


def seal(store, kind, start_ms, end_ms, at_ms=None, symbol='*'):
    if kind not in (FORWARD_SEAL, SYNTHETIC_DECLARED) or end_ms < start_ms:
        raise ClosureRefused('seal_malformed')
    if kind == FORWARD_SEAL:
        if at_ms is not None:
            raise ClosureRefused('seal_clock_is_trusted_only')
        at_ms = _now()
    elif at_ms is None:
        at_ms = _now()
    return _append(store, dict(type='SEAL', kind=kind, symbol=symbol, start_ms=start_ms, end_ms=end_ms, at_ms=at_ms))


def mark(store, state, start_ms, end_ms, at_ms, reason, symbol='*'):
    if state not in ('DEVELOPMENT_EXPOSED', 'PROTECTED_EXPOSED'):
        raise ClosureRefused('mark_state_invalid')
    return _append(store, dict(type='MARK', state=state, symbol=symbol, start_ms=start_ms, end_ms=end_ms,
                               at_ms=at_ms, reason=reason))
