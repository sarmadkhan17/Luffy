"""Opt-in main-thread stack sampling; never captures frame locals or arguments."""
from collections import Counter
import json
import logging
import os
from pathlib import Path
import sys
import tempfile
import threading
import time


def sample_cycle(cycle, directory, *, interval=0.05):
    """Observe only the invoking thread; preserve callback result and exception."""
    owner = threading.get_ident()
    stop = threading.Event()
    started = time.monotonic()
    target = Path(directory) / f'kernel-cycle-{os.getpid()}-{time.time_ns()}.samples.json'
    counts = Counter()

    def save(complete):
        temporary = None
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(mode='w', dir=target.parent,
                    prefix='.cycle-samples-', delete=False) as out:
                temporary = Path(out.name)
                json.dump(dict(schema='luffy-cycle-samples.v1', pid=os.getpid(),
                    thread_id=owner, elapsed_s=time.monotonic()-started,
                    complete=complete, samples=sum(counts.values()),
                    stacks=[dict(count=n, frames=stack) for stack,n in counts.most_common()],
                    note='Sample distribution, not exact wall time; scheduling and GIL can bias samples.'), out)
            os.replace(temporary, target)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def observe():
        next_save = started + 10
        try:
            while not stop.wait(interval):
                frame = sys._current_frames().get(owner)
                stack = []
                while frame is not None:
                    stack.append((frame.f_code.co_filename, frame.f_lineno, frame.f_code.co_name))
                    frame = frame.f_back
                counts[tuple(reversed(stack))] += 1
                if time.monotonic() >= next_save:
                    save(False)
                    next_save = time.monotonic() + 10
            save(True)
        except Exception as exc:
            logging.getLogger(__name__).warning('cycle sampling failed: %s', type(exc).__name__)

    worker = threading.Thread(target=observe, name='cycle-sampler', daemon=True)
    running = False
    try:
        try:
            worker.start()
            running = True
        except Exception as exc:
            logging.getLogger(__name__).warning('cycle sampler unavailable: %s', type(exc).__name__)
        return cycle()
    finally:
        stop.set()
        if running:
            worker.join(timeout=2)
