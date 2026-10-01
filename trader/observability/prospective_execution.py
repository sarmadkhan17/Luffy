"""Prospective observations only. No added account reads or execution authority.

Bounded public depth work runs outside the order thread. Every late book keeps
its actual delay and is NEVER called the book at the decision or submission.
Storage is append-only, capped at 256 MiB; exhaustion is an explicit blocker.
"""
import builtins
import json
import logging
import sqlite3
import threading
import time
import uuid
import queue
from pathlib import Path
from urllib.parse import urlsplit

from trader.engine.paper_exit_evidence import canonical, digest
from trader.engine.accounting import safe_fill
from . import execution_shadow as S, funding_events as F

log = logging.getLogger(__name__)
MAX_STORAGE_BYTES = 256 * 1024 * 1024
MAX_PENDING = 32


def environment(exchange):
    """Known public endpoint host only; no keys, current flag or symbol inference."""
    try:
        api = (getattr(exchange, 'urls', {}) or {}).get('api', {})
    except Exception:
        return None
    url = api.get('fapiPublic') if isinstance(api, dict) else None
    if not isinstance(url, str):
        return None
    hosts={'fapi.binance.com': 'production', 'demo-fapi.binance.com': 'demo'}
    public=hosts.get(urlsplit(url).hostname)
    private=api.get('fapiPrivate')
    if isinstance(private,str) and hosts.get(urlsplit(private).hostname) != public:
        return None
    return public


class PublicWorker:
    """One bounded daemon worker; it cannot hold Kernel shutdown open."""
    def __init__(self):
        self.jobs=queue.Queue(maxsize=MAX_PENDING)
        self.lock=threading.Lock()
        self.thread=None
        self.stopping=False

    def submit(self,fn):
        with self.lock:
            if self.stopping:
                raise RuntimeError('public_worker_stopped')
            if self.thread is None:
                self.thread=threading.Thread(target=self.run,daemon=True,name='execution-public-evidence')
                self.thread.start()
            self.jobs.put_nowait(fn)

    def run(self):
        while True:
            try:
                fn=self.jobs.get(timeout=.1)
            except queue.Empty:
                if self.stopping:
                    return
                continue
            try:
                fn()
            finally:
                self.jobs.task_done()

    def shutdown(self,wait=True):
        if wait:
            self.jobs.join()
        self.stopping=True
        if wait and self.thread:
            self.thread.join()


class Capture:
    def __init__(self, path, *, fetch=None):
        self.path = Path(path)
        self.fetch = fetch
        self.pool = PublicWorker()
        self.pending = threading.BoundedSemaphore(MAX_PENDING)
        self.lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, kind, body):
        try:
            with self.lock:
                if self.path.with_suffix('.STOP').exists():
                    self.blocked('OWNER_STOP_FILE')
                    return None
                if self.path.exists() and self.path.stat().st_size >= MAX_STORAGE_BYTES:
                    self.blocked('STORAGE_BOUND_REACHED')
                    return None
                text = canonical(body)
                if len(text.encode('utf-8')) > 512*1024:
                    self.blocked('RECORD_SIZE_BOUND_REACHED')
                    return None
                key = digest(canonical({'kind': kind, 'body': body}))
                with sqlite3.connect(self.path, timeout=0.1) as db:
                    db.executescript('''CREATE TABLE IF NOT EXISTS observations(
                      seq INTEGER PRIMARY KEY AUTOINCREMENT, observation_id TEXT UNIQUE,
                      kind TEXT NOT NULL, captured_ms INTEGER NOT NULL, body TEXT NOT NULL);
                    CREATE TRIGGER IF NOT EXISTS obs_no_update BEFORE UPDATE ON observations
                      BEGIN SELECT RAISE(ABORT,'observations immutable'); END;
                    CREATE TRIGGER IF NOT EXISTS obs_no_delete BEFORE DELETE ON observations
                      BEGIN SELECT RAISE(ABORT,'observations immutable'); END;''')
                    db.execute('INSERT OR IGNORE INTO observations(observation_id,kind,captured_ms,body) VALUES(?,?,?,?)',
                               (key,kind,time.time_ns()//1_000_000,text))
                return key
        except Exception as exc:
            self.blocked('STORAGE_ERROR:' + builtins.type(exc).__name__)
            return None

    def blocked(self, reason):
        log.warning('execution evidence blocked: %s', reason)
        try:
            self.path.with_suffix('.status.json').write_text(canonical(dict(
                status='EXTERNAL_BLOCKED', reason=reason, observed_ms=time.time_ns()//1_000_000)))
        except OSError:
            log.error('execution evidence blocker could not be persisted')

    def submit(self, fn):
        if not self.pending.acquire(blocking=False):
            self.blocked('PUBLIC_WORK_QUEUE_BOUND_REACHED')
            return
        def run():
            try:
                if not self.pool.stopping and not self.path.with_suffix('.STOP').exists():
                    fn()
                elif self.path.with_suffix('.STOP').exists():
                    self.blocked('OWNER_STOP_FILE')
            except Exception as exc:
                self.write('collection_failure', {'reason':builtins.type(exc).__name__})
            finally:
                self.pending.release()
        try:
            self.pool.submit(run)
        except Exception as exc:
            self.pending.release()
            self.blocked('PUBLIC_WORKER_UNAVAILABLE:' + builtins.type(exc).__name__)

    def book(self, event):
        if self.write('intent', event) is None:
            return
        if event.get('environment') not in ('production','demo'):
            self.write('book_unavailable', dict(intent_id=event['intent_id'], reason='ENVIRONMENT_UNPROVEN'))
            return
        def collect():
            try:
                kwargs = {'fetch': self.fetch} if self.fetch else {}
                book = S.capture(event, depth=20, max_age_ms=5000, **kwargs)
                self.write('linked_book', book)
            except Exception as exc:
                self.write('book_unavailable', dict(intent_id=event['intent_id'], reason=builtins.type(exc).__name__))
        self.submit(collect)

    def funding(self, trade, end_ms):
        """Frozen paper exposure + venue's event-time mark, no entry-price basis.
        Does not finalize/upgrade cost receipts or assert venue boundary semantics.
        """
        frozen = json.loads(trade['entry_identity_json']).get('paper_exposure')
        if not frozen or frozen['environment'] != 'production':
            self.write('funding_unavailable', dict(trade_id=trade['id'], reason='FROZEN_PRODUCTION_EXPOSURE_MISSING'))
            return
        if frozen['quantity'] != trade['amount']:
            self.write('funding_unavailable', dict(trade_id=trade['id'],reason='EXPOSURE_CHANGED_WITHOUT_EVENT_LEDGER'))
            return
        if self.write('funding_request',dict(trade_id=trade['id'],exposure=frozen,end_ms=end_ms)) is None:
            return
        symbol = trade['symbol'].split(':')[0].replace('/','')
        def collect():
            kwargs = {'fetch':self.fetch} if self.fetch else {}
            r = F.lookup(symbol,frozen['opened_ms'],end_ms,**kwargs)
            self.write('funding_interval', dict(trade_id=trade['id'], exposure=frozen, receipt=r))
            for e in F.replay(r):
                mark = e['published_mark_price']
                from .execution_calibration import positive
                try:
                    notional = str(positive(mark) * positive(frozen['quantity']))
                except (ValueError,TypeError,ArithmeticError):
                    notional = None
                boundary = e['timestamp_ms'] in (frozen['opened_ms'],end_ms)
                self.write('paper_funding_event', dict(trade_id=trade['id'], event=e,
                    exposure=frozen, position_notional=notional,
                    status='EVIDENCE_ONLY' if notional and not boundary else 'UNAVAILABLE',
                    qualification='VENUE_BOUNDARY_TIMING_UNPROVEN' if boundary else 'NOT_A_FINALIZED_COST_RECEIPT'))
        self.submit(collect)


class ObservedExchange:
    """Transparent observation of EXISTING calls. Original results/exceptions
    are returned unchanged. Only whitelisted execution fields are persisted.
    """
    def __init__(self, exchange, capture):
        self.exchange, self.capture = exchange, capture
        self.local = threading.local()

    def __getattribute__(self,key):
        if key in ('fetch_order','fetch_my_trades','cancel_order'):
            getattr(object.__getattribute__(self,'exchange'),key)
        return object.__getattribute__(self,key)

    def __getattr__(self, key):
        fn = getattr(self.exchange,key)
        if key != 'fapiPrivateDeleteAlgoOrder':
            return fn
        def observed(params, *args, **kwargs):
            identity = dict(venue_algo_id=params.get('algoId'), environment=environment(self.exchange))
            try:
                result=fn(params,*args,**kwargs)
            except Exception as exc:
                self.capture.write('algo_cancel_error',dict(identity,error_type=builtins.type(exc).__name__))
                raise
            self.capture.write('algo_cancel_response',dict(identity,received_ms=time.time_ns()//1_000_000))
            return result
        return observed

    @property
    def submitted_ms(self):
        return getattr(self.local,'submitted_ms',None)

    def context(self, body):
        self.local.context = dict(body)

    def create_order(self, symbol, type, side, amount, *args, **kwargs):
        ctx = dict(getattr(self.local,'context',{}))
        event = dict(ctx, intent_id=uuid.uuid4().hex, symbol=symbol.split(':')[0].replace('/',''),
            unified_symbol=symbol, side=side, requested_quantity=amount, order_type=type,
            phase='pre_submission', event_ms=time.time_ns()//1_000_000,
            environment=environment(self.exchange), decision_id=ctx.get('decision_id'))
        reference = ctx.get('reference') or {}
        ref_ms = reference.get('observed_at')
        if isinstance(ref_ms,str):
            from trader.engine.paper_cost_evidence import ms
            try:
                ref_ms = ms(ref_ms)
            except (ValueError,TypeError):
                ref_ms = None
        if builtins.type(ref_ms) is int and bool(event['decision_id']):
            self.capture.book(dict(event,phase='decision',event_ms=ref_ms,
                                   intent_id=event['intent_id'] + ':decision'))
        # Missing identity is explicitly retained; no fabricated decision ID.
        self.capture.write('submission_intent',event)
        if bool(event['decision_id']):
            self.capture.book(event)
        submitted = time.time_ns()//1_000_000
        self.local.submitted_ms = submitted
        try:
            result = self.exchange.create_order(symbol,type,side,amount,*args,**kwargs)
        except Exception as exc:
            self.capture.write('submission_error',dict(intent_id=event['intent_id'],
                submitted_ms=submitted, received_ms=time.time_ns()//1_000_000,
                error_type=builtins.type(exc).__name__, outcome='REJECTED' if builtins.type(exc).__name__ in
                ('InvalidOrder','InsufficientFunds','AuthenticationError','PermissionDenied') else 'AMBIGUOUS'))
            raise
        self.capture.write('order_response',dict(intent_id=event['intent_id'], submitted_ms=submitted,
            received_ms=time.time_ns()//1_000_000, environment=event['environment'], symbol=symbol,
            order={k:result.get(k) if isinstance(result,dict) else None for k in ('id','timestamp','status','side','amount','filled','remaining','average','price')}))
        self.report_order_fees(result,event['environment'],symbol)
        return result

    def report_order_fees(self,result,env,symbol):
        if not isinstance(result,dict):
            return
        try:
            for fill in result.get('trades') or []:
                self.capture.write('venue_fill',dict(environment=env,symbol=symbol,fill=safe_fill(fill)))
            fees=result.get('fees') or ([result['fee']] if result.get('fee') else [])
            for fee in fees:
                self.capture.write('reported_order_fee',dict(environment=env,symbol=symbol,venue_order_id=result.get('id'),
                    fee={k:fee.get(k) for k in ('currency','cost','rate')},qualification='EXACT_ORDER_ONLY; NOT_ACCOUNT_COMMISSION_AUTHORITY'))
        except Exception as exc:
            self.capture.write('order_fee_unavailable',dict(venue_order_id=result.get('id'),reason=builtins.type(exc).__name__))

    def fetch_order(self, order_id, symbol=None, *args, **kwargs):
        result=self.exchange.fetch_order(order_id,symbol,*args,**kwargs)
        self.capture.write('order_status',dict(venue_order_id=str(order_id),symbol=symbol,
            environment=environment(self.exchange), received_ms=time.time_ns()//1_000_000,
            order={k:result.get(k) if isinstance(result,dict) else None for k in ('id','timestamp','status','side','amount','filled','remaining','average','price')}))
        self.report_order_fees(result,environment(self.exchange),symbol)
        return result

    def fetch_my_trades(self, symbol=None, *args, **kwargs):
        results=self.exchange.fetch_my_trades(symbol,*args,**kwargs)
        for row in results or []:
            self.capture.write('venue_fill',dict(environment=environment(self.exchange),symbol=symbol, fill=safe_fill(row)))
        return results

    def cancel_order(self, order_id, symbol=None, *args, **kwargs):
        try:
            result=self.exchange.cancel_order(order_id,symbol,*args,**kwargs)
        except Exception as exc:
            self.capture.write('cancel_error',dict(venue_order_id=str(order_id),symbol=symbol,error_type=builtins.type(exc).__name__))
            raise
        self.capture.write('cancel_response',dict(venue_order_id=str(order_id),symbol=symbol,
            environment=environment(self.exchange),status=result.get('status') if isinstance(result,dict) else None,received_ms=time.time_ns()//1_000_000))
        return result


def for_journal(journal):
    existing = getattr(journal,'_prospective_capture',None)
    if existing is None and getattr(journal,'db_path',None):
        try:
            existing = Capture(journal.db_path.parent / 'execution-evidence.db')
            journal._prospective_capture = existing
        except Exception as exc:
            log.error('execution evidence initialization blocked: %s',builtins.type(exc).__name__)
    return existing



def replay_observations(path):
    """Read-only hash replay. Exact prospective links only; no time joins.
    Book capture delay and unconfirmed/partial outcomes retain their labels.
    """
    with sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True) as db:
        records=[]
        for key,kind,text in db.execute('SELECT observation_id,kind,body FROM observations ORDER BY seq'):
            body=json.loads(text)
            if canonical(body)!=text or key!=digest(canonical({'kind':kind,'body':body})):
                raise ValueError('execution_observation_replay_differs')
            records.append((kind,body))
    intents={b['intent_id']:b for k,b in records if k=='submission_intent'}
    orders=[b for k,b in records if k=='order_response']
    scoped={}
    for reply in orders:
        intent=intents.get(reply['intent_id'])
        if not intent:
            raise ValueError('execution_intent_link_missing')
        key=(reply['environment'],reply['symbol'],str(reply['order']['id']))
        scoped.setdefault(key,[]).append(reply)
    result=[]
    from trader.core.types import norm_symbol
    for reply in orders:
        intent=intents[reply['intent_id']]
        key=(reply['environment'],reply['symbol'],str(reply['order']['id']))
        if not reply['environment'] or not reply['order']['id'] or len(scoped[key])!=1:
            continue
        fills={}
        for kind,body in records:
            if kind!='venue_fill':continue
            fill=body['fill']
            if (body['environment']==reply['environment'] and norm_symbol(fill['symbol'])==norm_symbol(reply['symbol'])
                    and fill['order']==str(reply['order']['id']) and fill.get('id') and fill['side']==intent['side']):
                fid=str(fill['id'])
                if fid in fills and fills[fid]!=fill:
                    raise ValueError('execution_fill_identity_conflict')
                fills[fid]=fill
        result.append(dict(intent=intent,response=reply,fills=list(fills.values()),
            books=[b for k,b in records if k=='linked_book' and b['event']['intent_id'] in
                   (reply['intent_id'],reply['intent_id']+':decision')],
            authority='EXACT_OBSERVATIONS_ONLY; NOT_VALIDATED_COST_MODEL'))
    return result
