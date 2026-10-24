"""Spawn one bounded worker; receive only complete owned result artifacts.

No Pipe.recv/unpickling in the parent. Result byte/node budgets are checked
before decoding; only bounded data values (including numerical arrays) cross.
"""
from __future__ import annotations
import base64
import datetime as dt
import json
import multiprocessing as mp
import os
from pathlib import Path
import tempfile
import time
import traceback
from dataclasses import dataclass
from typing import Any, Callable

@dataclass
class ChildResult:
    ok: bool
    value: Any = None
    error: str = ''
    timed_out: bool = False
    elapsed_s: float = 0.0
    exitcode: int | None = None
    cancelled: bool = False
    terminated: bool = True
    pid: int | None = None
    start_ticks: int | None = None
    artifact_dir: str | None = None
    owned_pending: tuple = ()


def process_state(pid):
    """None proves absent; errors are UNKNOWN, never absence."""
    try:
        fields = Path('/proc', str(pid), 'stat').read_text().rsplit(')', 1)[1].split()
        return fields[0], int(fields[19])
    except FileNotFoundError:
        return None


def completed_artifact(directory, max_bytes=16 * 1024 * 1024, decode_bytes=128 * 1024 * 1024):
    path = Path(directory) / 'result.json'
    if path.is_symlink() or path.stat().st_size > max_bytes:
        raise ValueError('unusable_completed_result')
    raw = path.read_bytes()
    if len(raw) > max_bytes:
        raise ValueError('unusable_completed_result')
    overhead = _predecode(raw, decode_bytes)
    kind, payload = json.loads(raw)
    if kind != 'ok':
        raise ValueError('child_error_result')
    return _unpack(payload, [decode_bytes - overhead])

_UNREAPED = []

def _apply_limits(memory_mb, cpu_seconds):
    import resource
    if memory_mb:
        cap = int(memory_mb) * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (cap, cap))
    if cpu_seconds:
        cpu = max(1, int(cpu_seconds))
        resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu + 5))

def _pack(value, budget, depth=0):
    budget[0] -= 1
    if budget[0] < 0 or depth > 60:
        raise ValueError('result_structure_exceeds_budget')
    if value is None or isinstance(value, (str, bool, float)):
        return value
    if isinstance(value, int):
        if value.bit_length() > 256:
            raise ValueError('result_integer_exceeds_budget')
        return value
    if isinstance(value, complex):
        return {'t': 'complex', 'v': [value.real, value.imag]}
    if isinstance(value, dt.datetime):
        return {'t': 'datetime', 'v': value.isoformat()}
    if isinstance(value, dt.date):
        return {'t': 'date', 'v': value.isoformat()}
    if isinstance(value, dt.timedelta):
        return {'t': 'timedelta', 'v': [value.days, value.seconds, value.microseconds]}
    if isinstance(value, bytes):
        return {'t': 'bytes', 'v': base64.b64encode(value).decode('ascii')}
    if isinstance(value, (tuple, list)):
        return {'t': 'tuple' if isinstance(value, tuple) else 'list',
                'v': [_pack(v, budget, depth + 1) for v in value]}
    if isinstance(value, dict):
        return {'t': 'dict', 'v': [[_pack(k, budget, depth + 1), _pack(v, budget, depth + 1)] for k, v in value.items()]}
    if type(value).__module__.split('.')[0] == 'numpy':
        if type(value).__name__ == 'ndarray':
            if value.dtype.hasobject or value.dtype.fields or value.dtype.subdtype:
                raise ValueError('object_array_not_a_result')
            return {'t': 'array', 'dtype': str(value.dtype), 'shape': list(value.shape),
                    'v': _pack(value.tolist(), budget, depth + 1)}
        return _pack(value.item(), budget, depth + 1)
    raise TypeError('unsupported_child_result_type:' + type(value).__name__)

def _unpack(value, allocation_budget=None):
    if allocation_budget is None:
        allocation_budget = [128 * 1024 * 1024]
    if not isinstance(value, dict):
        return value
    kind, values = value['t'], value['v']
    if kind == 'complex':
        return complex(*values)
    if kind == 'datetime':
        return dt.datetime.fromisoformat(values)
    if kind == 'date':
        return dt.date.fromisoformat(values)
    if kind == 'timedelta':
        return dt.timedelta(days=values[0], seconds=values[1], microseconds=values[2])
    if kind == 'dict':
        return {_unpack(k, allocation_budget): _unpack(v, allocation_budget) for k, v in values}
    if kind == 'list':
        return [_unpack(v, allocation_budget) for v in values]
    if kind == 'tuple':
        return tuple(_unpack(v, allocation_budget) for v in values)
    if kind == 'bytes':
        return base64.b64decode(values, validate=True)
    if kind == 'array':
        import numpy as np
        dtype = np.dtype(value['dtype'])
        if dtype.hasobject or dtype.fields or dtype.subdtype or dtype.kind not in 'biufcUSmMV':
            raise ValueError('unsupported_result_dtype')
        count = 1
        shape = value['shape']
        if not isinstance(shape, list) or len(shape) > 32:
            raise ValueError('result_array_shape')
        for dim in shape:
            if type(dim) is not int or dim < 0:
                raise ValueError('result_array_shape')
            count *= dim
        needed = count * max(dtype.itemsize, 1)
        if needed > allocation_budget[0]:
            raise ValueError('result_array_allocation_budget_exceeded')
        allocation_budget[0] -= needed
        decoded = _unpack(values, allocation_budget)
        result = np.asarray(decoded, dtype=dtype)
        if result.size != count:
            raise ValueError('result_array_shape_mismatch')
        return result.reshape(shape)
    raise ValueError('unsupported_result_encoding')

def _entry(path, fn, args, kwargs, nice, limits, max_bytes, max_nodes):
    try:
        if nice:
            os.nice(nice)
        if limits:
            _apply_limits(**limits)
        packet = ['ok', _pack(fn(*args, **kwargs), [max_nodes])]
    except BaseException:
        packet = ['err', traceback.format_exc()[-4000:]]
    partial = Path(path).with_suffix('.partial')
    try:
        used = 0
        with partial.open('wb') as out:
            for chunk in json.JSONEncoder(ensure_ascii=True, separators=(',', ':')).iterencode(packet):
                raw = chunk.encode('utf8')
                used += len(raw)
                if used > max_bytes:
                    raise ValueError('child_result_bytes_exceeded')
                out.write(raw)
        os.replace(partial, path)
    finally:
        partial.unlink(missing_ok=True)

def _predecode(raw, decode_bytes):
    # Conservative budget for the controlled JSON data codec (no pickle globals
    # or arbitrary constructors). Count structure outside strings before loads.
    quoted = escaped = False
    nodes = depth = 0
    for byte in raw:
        if quoted:
            if escaped:
                escaped = False
            elif byte == 92:
                escaped = True
            elif byte == 34:
                quoted = False
        elif byte == 34:
            quoted = True
        elif byte in (91, 123):
            depth += 1
            nodes += 1
            if depth > 200:
                raise ValueError('result_depth_exceeded')
        elif byte in (93, 125):
            depth -= 1
        elif byte in (44, 58):
            nodes += 1
    estimated = len(raw) * 8 + nodes * 512
    if estimated > decode_bytes:
        raise ValueError('parent_decode_budget_exceeded')
    return estimated

def run_child(fn: Callable, *args, timeout_s: float, nice: int = 19,
              memory_mb: int | None = None, cpu_seconds: float | None = None,
              stop_requested=None, max_result_bytes=16 * 1024 * 1024,
              max_decode_bytes=128 * 1024 * 1024, cleanup_s=1.0, **kwargs) -> ChildResult:
    """One deadline includes startup/wait/decode; cleanup has its own allowance.

    A complete artifact without an exited child is never success. Database
    side effects remain the caller's durable attempt/idempotency contract.
    """
    global _UNREAPED
    _UNREAPED = [p for p in _UNREAPED if p.is_alive()]
    if _UNREAPED:
        return ChildResult(ok=False, error='prior_child_not_reaped', terminated=False)
    if timeout_s <= 0 or min(max_result_bytes, max_decode_bytes) <= 0:
        raise ValueError('positive child budgets required')
    t0 = time.monotonic()
    deadline = t0 + float(timeout_s)
    ctx = mp.get_context('spawn')
    directory = tempfile.mkdtemp(prefix='luffy-child-')
    path = Path(directory) / 'result.json'
    limits = dict(memory_mb=memory_mb, cpu_seconds=cpu_seconds) if memory_mb or cpu_seconds else None
    p = ctx.Process(target=_entry, args=(str(path), fn, args, kwargs, nice, limits,
                                      int(max_result_bytes), max(1, int(max_decode_bytes) // 2048)), daemon=True)
    result = ChildResult(ok=False)
    started = False
    try:
        p.start()
        started = True
        result.pid = p.pid
        identity = process_state(p.pid)
        result.start_ticks = identity[1] if identity else None
        result.artifact_dir = directory
        while p.is_alive():
            if stop_requested is not None and stop_requested():
                result.cancelled, result.error = True, 'child_cancelled'
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                result.timed_out, result.error = True, f'timed out after {timeout_s}s'
                break
            p.join(min(.05, remaining))
        if not p.is_alive() and not result.error:
            p.join(0)
            result.exitcode = p.exitcode
            if p.exitcode != 0 or not path.is_file():
                result.error = f'child exited with code {p.exitcode} before completing result'
            elif time.monotonic() >= deadline:
                result.timed_out, result.error = True, 'deadline_before_result_decode'
            else:
                fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
                with os.fdopen(fd, 'rb') as source:
                    st = os.fstat(source.fileno())
                    if st.st_size > max_result_bytes or st.st_size * 8 > max_decode_bytes:
                        raise ValueError('child_result_bytes_exceeded')
                    raw = source.read(int(max_result_bytes) + 1)
                if len(raw) != st.st_size:
                    raise ValueError('child_result_changed')
                overhead = _predecode(raw, max_decode_bytes)
                kind, payload = json.loads(raw)
                if stop_requested is not None and stop_requested():
                    result.cancelled, result.error = True, 'child_cancelled_after_result'
                elif time.monotonic() >= deadline:
                    result.timed_out, result.error = True, 'deadline_during_result_decode'
                elif kind == 'ok':
                    result.value = _unpack(payload, [max_decode_bytes - overhead])
                    result.ok = True
                else:
                    result.error = payload
                if time.monotonic() >= deadline or (stop_requested is not None and stop_requested()):
                    result.ok, result.value = False, None
                    result.timed_out = time.monotonic() >= deadline
                    result.cancelled = not result.timed_out
                    result.error = 'deadline_or_stop_after_decode'
    except Exception as exc:
        result.error = type(exc).__name__ + ':' + str(exc)
    finally:
        if started and p.is_alive():
            p.terminate()
            p.join(max(0, float(cleanup_s) / 2))
            if p.is_alive():
                p.kill()
                p.join(max(0, float(cleanup_s) / 2))
        result.terminated = not started or not p.is_alive()
        result.elapsed_s = time.monotonic() - t0
        result.exitcode = p.exitcode if started else None
        if not result.terminated:
            result.ok, result.value, result.error = False, None, 'child_not_reaped'
            _UNREAPED.append(p)
        else:
            import shutil
            shutil.rmtree(directory)
            if started:
                p.close()
    return result
