"""Non-waiting producer, bounded queue, disposable diagnostic subprocesses.

No database/file/network work on the producer. Capture is bounded and timed;
disk operations and CPU evaluation run in a child killed on timeout. Health is
also included in the kernel heartbeat, independent of the telemetry sink.
"""
from __future__ import annotations

import json
from collections import deque
import re
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
from uuid import uuid4

from .attention import capture, settings
from . import collector_health as H


class Collector:
    def __init__(self, directory, raw=None, *, start=True, clock=None):
        self.cfg = settings(raw)
        self.path = Path(directory) / "attention.db"
        self.health_path = Path(directory) / "attention_health.json"
        self.queue = queue.Queue(maxsize=self.cfg["queue_size"])
        self._stop = threading.Event()
        self._health = {"status": "waiting", "submitted": 0, "processed": 0,
                        "dropped": 0, "capture_errors": 0, "worker_errors": 0,
                        "timeouts": 0, "health_write_errors": 0, "last_error": None,
                        "last_scan_id": None, "last_success_ms": None,
                        "capture_ms": 0, "max_capture_ms": 0,
                        "capture_budget_ms": 50, "over_budget": 0}
        self.clock = clock or H.clock_ms
        self.instance_id = uuid4().hex
        try:
            self.boot_id = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        except OSError:
            self.boot_id = None
        self.manifest = H.code_hash()  # construction only, never the producer path
        self._issued = 0
        self._attempts = {}
        self._producer = (0, 0, None)
        self._producer_errors = deque(maxlen=64)
        self._seen_producer = 0
        self._recent = deque(maxlen=64)
        self._tracking = {}
        self._complete = deque(maxlen=H.RECOVERY_SCANS)
        self._state = dict(health_schema=H.SCHEMA, instance_id=self.instance_id,
            boot_id=self.boot_id, process_started_ms=self.clock(), failure_generation=0, fence_seq=0,
            first_error_ms=None, last_error_ms=None, certificate=None, last_complete=None,
            details_lost=0, status='waiting', last_error=None)
        self._view = dict(self._state, recent_errors=[])
        self.thread = None
        if start:
            self.thread = threading.Thread(target=self._loop, daemon=True, name="attention-collector")
            self.thread.start()

    def _put(self, event):
        try:
            self.queue.put_nowait(event)
            self._health["submitted"] += 1
            return True
        except queue.Full:
            self._health["dropped"] += 1
            self._producer_failure(event.get("scan_id"), "queue_full", event.get("kind"))
            return False

    def begin(self, frames, members, as_of_ms=None, supplemental=None, provenance=None):
        scan_id = "scan_" + uuid4().hex
        self._issued += 1
        self._producer = (self._issued, self._producer[1], self._producer[2])
        if len(self._attempts) >= 2*self.cfg['queue_size']:
            self._attempts.pop(next(iter(self._attempts)))
            self._health['capture_errors'] += 1
            self._producer_failure(scan_id, 'tracking_overflow', 'scan')
        self._attempts[scan_id] = self._issued
        self._health.update(last_scan_id=scan_id, last_attempt_ms=int(time.time() * 1000))
        try:
            event = capture(frames, members, scan_id, self.cfg, as_of_ms, supplemental,
                            provenance)
            event['identity'] = dict(schema=H.IDENTITY_SCHEMA, instance_id=self.instance_id, seq=self._issued)
            ms = event["capture_ms"]
            self._health.update(capture_ms=ms, max_capture_ms=max(ms, self._health["max_capture_ms"]))
            if ms > self._health["capture_budget_ms"]:
                self._health["over_budget"] += 1
            # Candle values were already detached from the frames.
            if self._put(event):
                return scan_id
        except Exception as exc:
            self._health["capture_errors"] += 1
            self._producer_failure(scan_id, type(exc).__name__, "capture")
        self._attempts.pop(scan_id, None)
        return None

    def causes(self, scan_id, items):
        started = time.perf_counter()
        if not scan_id:
            return
        try:
            seq = self._attempts.pop(scan_id, None)
            if seq is None:
                raise ValueError('unknown_attempt')
            bounded = []
            for item in items[:self.cfg["max_symbols"]]:
                # Fixed schema: copy mutable containers only; strings/numbers
                # are immutable. Avoid recursive copying on the trading thread.
                row = {k: item[k] for k in ("symbol", "decision_id", "cycle_id", "action",
                       "executed", "entry_allowed", "blocked", "reason", "decision_detail",
                       "omitted_causes") if k in item}
                evaluations = item.get("evaluations", [])
                row["evaluations"] = []
                for source in evaluations[:self.cfg["max_causes"]]:
                    receipt = {k: source[k] for k in ("component", "component_id", "reason",
                               "input_status", "meaning", "error_type") if k in source}
                    if "frames" in source:
                        receipt["frames"] = [dict(f) for f in source["frames"][-8:]]
                    row["evaluations"].append(receipt)
                row["omitted_causes"] = row.get("omitted_causes", 0) + max(0, len(evaluations) - self.cfg["max_causes"])
                bounded.append(row)
            self._health["dropped_cause_symbols"] = self._health.get("dropped_cause_symbols", 0) + max(0, len(items) - len(bounded))
            self._put({"kind": "causes", "scan_id": scan_id,
                       "as_of_ms": int(time.time() * 1000), "items": bounded,
                       "identity": dict(schema=H.IDENTITY_SCHEMA, instance_id=self.instance_id, seq=seq)})
        except Exception as exc:
            self._health["capture_errors"] += 1
            self._producer_failure(scan_id, type(exc).__name__, "capture")

        finally:
            ms = (time.perf_counter() - started) * 1000
            total = self._health["capture_ms"] + ms
            self._health.update(causes_capture_ms=ms, producer_ms=total,
                                max_producer_ms=max(total, self._health.get("max_producer_ms", 0)))
            if total > self._health["capture_budget_ms"] >= self._health["capture_ms"]:
                self._health["over_budget"] += 1

    def _producer_failure(self, scan_id, reason, kind):
        count = self._producer[1]+1
        rec = dict(n=count, ts_ms=self.clock(), seq=self._issued,
                   scan_id=scan_id, phase=str(kind or 'unknown')[:24], error_class=str(reason)[:48])
        self._producer_errors.append(rec)
        self._producer = (self._issued, count, rec)

    def _incident(self, rec, count=1):
        st=self._state
        st['failure_generation'] += count
        st['fence_seq'] = max(st['fence_seq'], self._producer[0], rec['seq'])
        st['first_error_ms'] = st['first_error_ms'] if st['first_error_ms'] is not None else rec['ts_ms']
        st.update(last_error_ms=rec['ts_ms'], last_error=rec['error_class'], status='error', certificate=None)
        self._tracking.clear()
        self._complete.clear()

    def _sync_producer(self):
        issued, count, latest = self._producer
        delta=count-self._seen_producer
        if delta:
            try:
                rows=[r for r in tuple(self._producer_errors) if self._seen_producer < r['n'] <= count]
            except RuntimeError:
                rows=[latest]
            self._state['details_lost'] += max(0,delta-len(rows))
            self._incident(latest, delta)
            self._recent.extend(rows)
            self._seen_producer=count

    def _publish_view(self):
        self._view = dict(self._state, recent_errors=list(self._recent),
                          acknowledged_producer=self._seen_producer,
                          worker_errors=self._health['worker_errors'])

    def health(self):
        # No mutation, blocking or I/O. Pending producer failures invalidate the
        # worker's older view even when its child is still running.
        v=self._view
        prod=self._producer
        h=dict(self._health)
        h.update(v)
        h['worker_errors']=v.get('worker_errors',0)
        h['errors']=h['capture_errors']+h['worker_errors']
        h['failure_generation']=h['errors']+h['dropped']
        if prod[1] > v.get('acknowledged_producer',0):
            h.update(status='error', last_error='producer_failure_pending', certificate=None,
                     fence_seq=max(h['fence_seq'],prod[0]), last_error_ms=prod[2]['ts_ms'])
        h.update(queue_depth=self.queue.qsize(), enabled=True, updated_ms=self.clock(),
                 worker_alive=bool(self.thread and self.thread.is_alive()))
        return h

    def _success(self, event, proof):
        ident=event.get('identity')
        if not H.identity(ident) or ident['instance_id'] != self.instance_id:
            raise WorkerError('invalid_identity')
        sid=event['scan_id']; seq=ident['seq']
        if not isinstance(proof,dict) or proof.get('scan_id')!=sid:
            raise WorkerError('invalid_proof')
        if event['kind']=='scan' and proof.get('scan_identity')!=ident:
            raise WorkerError('invalid_proof')
        if event['kind']=='causes' and (proof.get('cause_identities')!=[ident] or not proof.get('completion_marker')):
            raise WorkerError('invalid_proof')
        if sid not in self._tracking and len(self._tracking)>=2*self.cfg['queue_size']:
            raise WorkerError('tracking_overflow')
        st=self._tracking.setdefault(sid,dict(seq=seq, kinds=set()))
        if st['seq']!=seq: raise WorkerError('invalid_identity')
        st['kinds'].add(event['kind'])
        if st['kinds'] != {'scan','causes'}: return
        del self._tracking[sid]
        if not proof.get('payload') or not proof.get('causes_complete') or proof.get('scan_identity')!=ident or proof.get('cause_identities')!=[ident]:
            raise WorkerError('invalid_proof')
        row=dict(scan_id=sid,seq=seq)
        previous=self._state['last_complete']
        if previous and seq <= previous['seq']:
            raise WorkerError('completion_order')
        self._state['last_complete']=row
        if not self._state['failure_generation']:
            self._state.update(status='ok',last_error=None)
        elif seq>self._state['fence_seq']:
            if self._complete and seq<=self._complete[-1]['seq']: return
            self._complete.append(row)
            if len(self._complete)==H.RECOVERY_SCANS and self.clock()-self._state['last_error_ms']>=H.QUARANTINE_MS:
                if self._state['certificate'] is None:
                    self._state['certificate']=dict(instance_id=self.instance_id,
                        failure_generation=self._state['failure_generation'],fence_seq=self._state['fence_seq'],
                        scans=list(self._complete),issued_ms=self.clock())
                self._state.update(status='recovered',last_error=None)

    def close(self):
        self._stop.set()  # never join/wait on the trading or exit thread

    def _run(self, event):
        job = json.dumps({"path": str(self.path), "settings": self.cfg, "event": event},
                         allow_nan=False, separators=(",", ":"))
        result = subprocess.run([sys.executable, "-m", "trader.observability.worker"],
                                input=job, text=True, capture_output=True,
                                cwd=str(Path(__file__).resolve().parents[2]),
                                timeout=self.cfg["timeout_seconds"])
        try:
            data = json.loads(result.stdout)
        except (ValueError,TypeError):
            last=(result.stderr or '').strip().splitlines()
            token=last[-1].split(':',1)[0].strip() if last else ''
            safe=token if re.fullmatch(r'[A-Za-z_][A-Za-z0-9_.]*(?:Error|Exception)',token) else 'child_no_result'
            raise WorkerError(safe, result.returncode, 'child_no_result') from None
        if not isinstance(data,dict) or result.returncode or not data.get('ok'):
            name=data.get('error_type','worker_failed') if isinstance(data,dict) else 'worker_failed'
            if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,47}',str(name)): name='worker_failed'
            raise WorkerError(name,result.returncode,'child_error')
        if data.get('code_hash') != self.manifest:
            raise WorkerError('code_mismatch',result.returncode,'code_mismatch')
        return data.get('proof')

    def _write_health(self):
        self._sync_producer()
        self._publish_view()
        try:
            self.health_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.health_path.with_suffix(".tmp")
            h=self.health()
            raw=json.dumps(h)
            while len(raw.encode())>H.MAX_HEALTH_BYTES and h.get('recent_errors'):
                h['recent_errors']=h['recent_errors'][1:]
                h['details_lost']+=1
                raw=json.dumps(h)
            if len(raw.encode())>H.MAX_HEALTH_BYTES: raise OSError('health_capacity')
            tmp.write_text(raw)
            os.replace(tmp, self.health_path)
        except OSError:
            self._health["health_write_errors"] += 1

    def _loop(self):
        while not self._stop.is_set():
            try:
                event = self.queue.get(timeout=1)
            except queue.Empty:
                self._write_health()
                continue
            try:
                self._sync_producer()
                proof=self._run(event)
                self._sync_producer()
                self._success(event,proof)
                self._health['processed'] += 1
                self._health['last_success_ms']=self.clock()
            except Exception as exc:
                self._sync_producer()
                self._health['worker_errors'] += 1
                timeout=isinstance(exc,subprocess.TimeoutExpired)
                if timeout: self._health['timeouts'] += 1
                rec=dict(ts_ms=self.clock(),seq=self._producer[0],scan_id=event.get('scan_id'),
                         phase='timeout' if timeout else getattr(exc,'phase',event.get('kind','unknown')),
                         error_class='worker_timeout' if timeout else str(exc) if isinstance(exc,WorkerError) else type(exc).__name__,
                         returncode=getattr(exc,'returncode',None))
                if timeout:
                    rec['row_present_after_timeout']=self._timeout_row(event.get('scan_id'))
                self._incident(rec)
                self._recent.append(rec)
            finally:
                self.queue.task_done()
                self._write_health()

    def _timeout_row(self, sid):
        import sqlite3
        from contextlib import closing
        try:
            with closing(sqlite3.connect(self.path.resolve().as_uri()+'?mode=ro',uri=True,timeout=.05)) as db:
                return bool(db.execute('SELECT 1 FROM scans WHERE scan_id=?',(sid,)).fetchone())
        except sqlite3.Error:
            return None


class WorkerError(Exception):
    """Allowlisted class/reason only; never stores stderr or arbitrary messages."""
    def __init__(self, reason, returncode=None, phase='worker'):
        super().__init__(reason)
        self.returncode, self.phase = returncode, phase
