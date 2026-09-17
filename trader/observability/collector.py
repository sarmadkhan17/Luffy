"""Non-waiting producer, bounded queue, disposable diagnostic subprocesses.

No database/file/network work on the producer. Capture is bounded and timed;
disk operations and CPU evaluation run in a child killed on timeout. Health is
also included in the kernel heartbeat, independent of the telemetry sink.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
from uuid import uuid4

from .attention import capture, settings


class Collector:
    def __init__(self, directory, raw=None, *, start=True):
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
            self._health["last_error"] = "queue_full"
            return False

    def begin(self, frames, members, as_of_ms=None):
        scan_id = "scan_" + uuid4().hex
        self._health.update(last_scan_id=scan_id, last_attempt_ms=int(time.time() * 1000))
        try:
            event = capture(frames, members, scan_id, self.cfg, as_of_ms)
            ms = event["capture_ms"]
            self._health.update(capture_ms=ms, max_capture_ms=max(ms, self._health["max_capture_ms"]))
            if ms > self._health["capture_budget_ms"]:
                self._health["over_budget"] += 1
            # Candle values were already detached from the frames.
            if self._put(event):
                return scan_id
        except Exception as exc:
            self._health["capture_errors"] += 1
            self._health["last_error"] = type(exc).__name__
        return None

    def causes(self, scan_id, items):
        started = time.perf_counter()
        if not scan_id:
            return
        try:
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
                       "as_of_ms": int(time.time() * 1000), "items": bounded})
        except Exception as exc:
            self._health["capture_errors"] += 1
            self._health["last_error"] = type(exc).__name__

        finally:
            ms = (time.perf_counter() - started) * 1000
            total = self._health["capture_ms"] + ms
            self._health.update(causes_capture_ms=ms, producer_ms=total,
                                max_producer_ms=max(total, self._health.get("max_producer_ms", 0)))
            if total > self._health["capture_budget_ms"] >= self._health["capture_ms"]:
                self._health["over_budget"] += 1

    def health(self):
        h = dict(self._health)
        h.update(errors=h["capture_errors"] + h["worker_errors"],
                 queue_depth=self.queue.qsize(), enabled=True,
                 updated_ms=int(time.time() * 1000),
                 worker_alive=bool(self.thread and self.thread.is_alive()))
        return h

    def close(self):
        self._stop.set()  # never join/wait on the trading or exit thread

    def _run(self, event):
        job = json.dumps({"path": str(self.path), "settings": self.cfg, "event": event},
                         allow_nan=False, separators=(",", ":"))
        result = subprocess.run([sys.executable, "-m", "trader.observability.worker"],
                                input=job, text=True, capture_output=True,
                                cwd=str(Path(__file__).resolve().parents[2]),
                                timeout=self.cfg["timeout_seconds"])
        data = json.loads(result.stdout)
        if result.returncode or not data.get("ok"):
            raise WorkerError(data.get("error_type", "worker_failed"))

    def _write_health(self):
        try:
            self.health_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.health_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.health()))
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
                self._run(event)
                self._health["processed"] += 1
                self._health.update(status="ok", last_success_ms=int(time.time() * 1000))
                # A success does not erase a producer overflow or a newer failure.
                if event["kind"] == "scan" and not self._health["dropped"] and not self._health["capture_errors"]:
                    self._health["last_error"] = None
            except subprocess.TimeoutExpired:
                self._health["timeouts"] += 1
                self._health["worker_errors"] += 1
                self._health.update(status="error", last_error="worker_timeout")
            except Exception as exc:
                self._health["worker_errors"] += 1
                self._health.update(status="error", last_error=(str(exc) if isinstance(exc, WorkerError)
                                                                else type(exc).__name__))
            finally:
                self.queue.task_done()
                self._write_health()


class WorkerError(Exception):
    """Contains only the child's allowlisted exception class name."""
