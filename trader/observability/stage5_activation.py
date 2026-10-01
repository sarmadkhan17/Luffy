"""Opt-in Stage-5 capture startup and bounded, credential-free public receipts.

The durable collection window is never extended by a restart. Public receipts
have no exposure/commission/eligibility authority. Event-linked depth remains
the reviewed execution producer; these two-symbol books are supplemental.
"""
import json
import logging
import threading
import time
from pathlib import Path

from . import depth_evidence as D, funding_events as F
from .prospective_execution import Capture
from trader.data.registry_provider import VenueTarget

log = logging.getLogger(__name__)
SYMBOLS = ('BTCUSDT', 'ETHUSDT')
INTERVAL_S = 60
FUNDING_INTERVAL_S = 300
MAX_BYTES = 64 * 1024 * 1024
MAX_WINDOW_MS = 72 * 3600 * 1000


def read_window(path, now_ms):
    body = json.loads(path.read_text())
    start, end = body['started_ms'], body['deadline_ms']
    if (type(start) is not int or type(end) is not int
            or not 0 < end - start <= MAX_WINDOW_MS or now_ms < start):
        raise ValueError('PUBLIC_COLLECTION_WINDOW_INVALID')
    return body, now_ms < end


def run(data, stop, *, clock=None, funding_lookup=F.lookup):
    clock = clock or (lambda: time.time_ns() // 1_000_000)
    directory = data / 'stage5-public'
    directory.mkdir(exist_ok=True)
    capture = Capture(directory / 'public.db')
    target = VenueTarget.production()
    next_funding = 0
    while not stop():
        now = clock()
        window, valid = read_window(data / 'stage5-activation.json', now)
        if not valid or (directory / 'STOP').exists():
            state = 'WINDOW_EXPIRED' if not valid else 'OWNER_STOP_FILE'
            (directory / 'status.json').write_text(json.dumps({'status': 'EXTERNAL_BLOCKED', 'reason': state, 'observed_ms': now}))
            return
        if capture.path.exists() and capture.path.stat().st_size >= MAX_BYTES:
            capture.blocked('PUBLIC_STORAGE_BOUND_REACHED')
            return
        outcomes = {}
        for symbol in SYMBOLS:
            if stop():
                return
            if capture.path.exists() and capture.path.stat().st_size >= MAX_BYTES:
                capture.blocked('PUBLIC_STORAGE_BOUND_REACHED')
                return
            start_ms = clock()
            try:
                response = F.urllib_fetch(D.request_url(target, symbol, 20), timeout_s=10, max_bytes=D.MAX_BODY_BYTES)
                if response.status != 200:
                    raise ValueError('depth_http_failure')
                book = D.observe(response.body, target=target, symbol=symbol, limit=20,
                    request_start_ms=start_ms, received_ms=clock(), max_age_ms=5000)
                if capture.write('supplemental_public_depth', book) is None:
                    raise ValueError('depth_storage_failure')
                outcomes[symbol + ':depth'] = 'RUNNING'
            except Exception as exc:
                outcomes[symbol + ':depth'] = type(exc).__name__
                capture.write('public_collection_failure', {'stream': 'depth', 'symbol': symbol, 'reason': str(exc)[:160]})
            if now >= next_funding:
                try:
                    end = clock()
                    receipt = funding_lookup(symbol, end - 8 * 3600 * 1000, end, max_pages=1)
                    if capture.write('public_funding_interval', receipt) is None:
                        raise ValueError('funding_storage_failure')
                    outcomes[symbol + ':funding'] = 'RUNNING'
                except Exception as exc:
                    outcomes[symbol + ':funding'] = type(exc).__name__
                    capture.write('public_collection_failure', {'stream': 'funding', 'symbol': symbol, 'reason': str(exc)[:160]})
        if now >= next_funding:
            next_funding = now + FUNDING_INTERVAL_S * 1000
        (directory / 'status.json').write_text(json.dumps(dict(status='RUNNING', observed_ms=clock(),
            symbols=SYMBOLS, outcomes=outcomes, credentials='NONE', deadline_ms=window['deadline_ms'],
            storage_bytes=capture.path.stat().st_size if capture.path.exists() else 0,
            storage_limit_bytes=MAX_BYTES, depth_interval_s=INTERVAL_S, funding_interval_s=FUNDING_INTERVAL_S)))
        deadline = time.monotonic() + INTERVAL_S
        while not stop() and time.monotonic() < deadline:
            time.sleep(min(1, max(0, deadline - time.monotonic())))


def start(kernel):
    data = Path(kernel.journal.db_path).parent
    window = data / 'stage5-activation.json'
    if not window.exists():
        return
    read_window(window, time.time_ns() // 1_000_000)
    from trader.strategy import factory_handoff as H, capacity
    from .prospective_execution import for_journal
    H.ensure(kernel.journal)
    capacity.ensure(kernel.journal)
    from trader.engine.evidence_capture import SNAPSHOT_DDL
    with kernel.journal._tx() as db:
        db.executescript(SNAPSHOT_DDL)
    kernel._paper_executor()  # schema only, no signal/trade/economic receipt
    capture = for_journal(kernel.journal)
    capture.write('capture_startup', dict(schema='stage5-capture-startup.v1', observed_ms=time.time_ns() // 1_000_000,
        control=kernel.state_machine.state.value, referee=kernel.cfg['research']['referee'],
        handoff=kernel.cfg['research']['handoff'], grants='OBSERVATION_ONLY'))
    threading.Thread(target=kernel._paper_funding_recorder, daemon=True, name='paper-funding-evidence').start()
    def collect():
        try:
            run(data, lambda: kernel._stop)
        except Exception as exc:
            capture.blocked('PUBLIC_PRODUCER:' + type(exc).__name__)
            log.exception('Stage-5 public collector stopped')
    threading.Thread(target=collect, daemon=True, name='stage5-public-evidence').start()
