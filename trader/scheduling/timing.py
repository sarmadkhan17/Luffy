"""Bounded stage timings; no payloads, SQL, credentials or trading decisions.

Nested wall/CPU durations are inclusive. Exclusive durations subtract timed
children on this thread. Wall minus CPU includes scheduling/GIL/lock/network
waits and must not be reported as a measured database or network wait.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from functools import wraps
import threading
import time

_active = ContextVar('cycle_stage_trace', default=None)


class StageTimings:
    def __init__(self, clock=time.perf_counter, cpu=time.thread_time):
        self.clock, self.cpu = clock, cpu
        self.lock = threading.RLock()
        self.current, self.last = {}, {}
        self.sequence = 0

    @contextmanager
    def run(self, name):
        with self.lock:
            self.sequence += 1
            run = dict(id=self.sequence, worker=name, started_at=time.time(),
                       start=self.clock(), status='running', stages={}, stack=[])
            self.current[name] = run
        token = _active.set((self, run))
        try:
            with stage_scope('total'):
                yield
        except BaseException:
            run['status'] = 'failed'
            raise
        else:
            run['status'] = 'completed'
        finally:
            _active.reset(token)
            with self.lock:
                run['elapsed_s'] = self.clock() - run['start']
                self.last[name] = run
                self.current.pop(name, None)

    def snapshot(self):
        with self.lock:
            now = self.clock()
            def public(run):
                row = {k: deepcopy(v) for k,v in run.items() if k not in ('start','stack')}
                row['elapsed_s'] = run.get('elapsed_s', now-run['start'])
                row['active'] = [dict(stage=f['label'], symbol=f['symbol'],
                    elapsed_s=now-f['start']) for f in run['stack']]
                return row
            return dict(current={k:public(v) for k,v in self.current.items()},
                        last={k:public(v) for k,v in self.last.items()})


@contextmanager
def stage_scope(label, symbol=None):
    active = _active.get()
    if active is None:
        yield
        return
    recorder, run = active
    frame = dict(label=label, symbol=symbol, start=recorder.clock(), cpu=recorder.cpu(),
                 child_wall=0., child_cpu=0.)
    with recorder.lock:
        run['stack'].append(frame)
    error = None
    try:
        yield
    except BaseException as exc:
        error = type(exc).__name__
        raise
    finally:
        wall, cpu = recorder.clock()-frame['start'], recorder.cpu()-frame['cpu']
        with recorder.lock:
            run['stack'].pop()
            if run['stack']:
                run['stack'][-1]['child_wall'] += wall
                run['stack'][-1]['child_cpu'] += cpu
            row = run['stages'].setdefault(label, dict(calls=0, wall_s=0., cpu_s=0.,
                exclusive_wall_s=0., exclusive_cpu_s=0., max_s=0., failures=0,
                last_error_type=None, slowest=[]))
            row['calls'] += 1
            row['wall_s'] += wall
            row['cpu_s'] += cpu
            row['exclusive_wall_s'] += max(0.,wall-frame['child_wall'])
            row['exclusive_cpu_s'] += max(0.,cpu-frame['child_cpu'])
            row['max_s'] = max(row['max_s'], wall)
            row['failures'] += int(error is not None)
            row['last_error_type'] = error
            if symbol is not None:
                row['slowest'] = sorted(row['slowest']+[dict(symbol=symbol,wall_s=wall)],
                                        key=lambda r:r['wall_s'],reverse=True)[:5]


def stage(label, symbol_arg=None):
    def decorate(fn):
        @wraps(fn)
        def timed(*args, **kwargs):
            symbol = args[symbol_arg] if symbol_arg is not None and len(args)>symbol_arg else None
            with stage_scope(label, symbol):
                return fn(*args, **kwargs)
        return timed
    return decorate


def timed_worker(name):
    def decorate(fn):
        @wraps(fn)
        def timed(self, *args, **kwargs):
            with self.stage_timings.run(name):
                return fn(self, *args, **kwargs)
        return timed
    return decorate
