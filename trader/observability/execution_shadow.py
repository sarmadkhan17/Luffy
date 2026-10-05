"""One public depth capture linked to an already existing intent event.

No polling and no historical reconstruction. Host must invoke prospectively;
it is NOT wired or deployed. Caller owns bounded shadow storage. Capture timing
is preserved; a response after a decision is never called a book at that time.
"""
import time

from trader.data.registry_provider import VenueTarget, urllib_fetch
from trader.observability import depth_evidence as D
from trader.engine.paper_exit_evidence import canonical, digest


def capture(event, *, depth, max_age_ms, fetch=urllib_fetch, clock=None):
    required = ('decision_id', 'intent_id', 'symbol', 'side', 'requested_quantity',
                'phase', 'event_ms', 'environment')
    if any(event.get(k) is None for k in required):
        raise ValueError('exact_intent_event_missing')
    if event['phase'] not in ('decision', 'pre_submission', 'pre_paper_execution'):
        raise ValueError('capture_phase_invalid')
    if event['side'] not in ('buy', 'sell') or type(event['event_ms']) is not int:
        raise ValueError('intent_geometry_invalid')
    from .execution_calibration import positive
    positive(event['requested_quantity'])
    target = {'production': VenueTarget.production, 'demo': VenueTarget.demo}.get(event['environment'])
    if target is None:
        raise ValueError('capture_environment_unknown')
    target = target()
    clock = clock or (lambda: time.time_ns() // 1_000_000)
    start = clock()
    if start < event['event_ms']:
        raise ValueError('capture_clock_invalid')
    response = fetch(D.request_url(target, event['symbol'], depth), timeout_s=10, max_bytes=D.MAX_BODY_BYTES)
    received = clock()
    if response.status != 200:
        raise ValueError('capture_http_failure')
    obs = D.observe(response.body, target=target, symbol=event['symbol'], limit=depth,
                    request_start_ms=start, received_ms=received, max_age_ms=max_age_ms)
    body = dict(schema='intent-depth-capture.v1', event=dict(event), observation=obs,
                event_to_request_ms=start-event['event_ms'],
                event_to_received_ms=received-event['event_ms'],
                authority='OBSERVATION_ONLY; NOT_EXACT_EVENT_TIME_BOOK')
    body['capture_id'] = digest(canonical(body))
    return body
