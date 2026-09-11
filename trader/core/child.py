"""Run one function in a separate, low-priority process with a deadline.

The research pipeline's search is CPU-heavy pandas work. Run on a kernel
thread it would hold the GIL against the trade loop, and an exception or a
memory blow-up in it would be the kernel's. So the kernel thread owns the
schedule and hands each batch to a child:

- **spawn**, never fork: the kernel holds threads and SQLite connections,
  and a forked copy of those is undefined behaviour;
- **niced** (19 by default), so the trade loop always wins the CPU;
- **killed at its deadline**, and a child that dies without answering is a
  failure with its exit code, never a hang.

The child must not write the database: it returns results, and the calling
thread writes them through the journal.
"""
from __future__ import annotations

import multiprocessing as mp
import os
import time
import traceback
from dataclasses import dataclass
from typing import Any, Callable


@dataclass
class ChildResult:
    ok: bool
    value: Any = None
    error: str = ""
    timed_out: bool = False
    elapsed_s: float = 0.0
    exitcode: int | None = None


def _entry(conn, fn, args, kwargs, nice):
    try:
        if nice:
            os.nice(nice)
        conn.send(("ok", fn(*args, **kwargs)))
    except BaseException:
        conn.send(("err", traceback.format_exc()[-4000:]))
    finally:
        conn.close()


def run_child(fn: Callable, *args, timeout_s: float, nice: int = 19,
              **kwargs) -> ChildResult:
    ctx = mp.get_context("spawn")
    parent, child = ctx.Pipe(duplex=False)
    p = ctx.Process(target=_entry, args=(child, fn, args, kwargs, nice),
                    daemon=True)
    t0 = time.monotonic()
    p.start()
    child.close()
    try:
        if not parent.poll(timeout_s):
            return ChildResult(ok=False, timed_out=True,
                               error=f"timed out after {timeout_s}s",
                               elapsed_s=time.monotonic() - t0)
        try:
            kind, payload = parent.recv()
        except EOFError:
            p.join(5)
            return ChildResult(ok=False, exitcode=p.exitcode,
                               error=f"child exited with code {p.exitcode} "
                                     f"before answering",
                               elapsed_s=time.monotonic() - t0)
        p.join(5)
        if kind == "ok":
            return ChildResult(ok=True, value=payload, exitcode=p.exitcode,
                               elapsed_s=time.monotonic() - t0)
        return ChildResult(ok=False, error=payload, exitcode=p.exitcode,
                           elapsed_s=time.monotonic() - t0)
    finally:
        if p.is_alive():
            p.terminate()
            p.join(5)
        if p.is_alive():
            p.kill()
            p.join(5)
        parent.close()
