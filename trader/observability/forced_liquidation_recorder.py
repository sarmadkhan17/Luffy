"""Off-path forward recorder for production ``!forceOrder@arr`` snapshots.

Standalone process: no Kernel, trading loop, exchange object, credentials,
order/account API, Attention, Risk, Execution or research dependency. It
only appends raw public frames plus session/liveness/failure records to its
own log (``forced_liquidation_store``). No REST backfill.

Transport policy actually used: the ``websockets`` asyncio client's own
defaults (keepalive ping interval/timeout, close timeout, max frame size,
receive queue, reconnect backoff). Nothing here sets a timing constant; the
values in force are read back from the live connection and archived. Every
client ping and every matched pong is persisted with its payload, so
liveness is evidence, not socket-open status. Whether those defaults are an
adequate *coverage* policy is a separate, not-yet-frozen decision; see
``forced_liquidation_coverage.PRODUCTION_POLICY``.

Frames are written synchronously in the reader coroutine, so there is no
application queue and no application-level drop path. Any write failure
ends the session and the process (fail closed).
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import platform
import random
import resource
import sqlite3
import struct
import sys
import time
import uuid
from pathlib import Path

from .forced_liquidation import (
    ENVIRONMENT, METADATA_SOURCE_URL, PROTOCOL_ID, STREAM_URL, SymbolMap, classify_frame, digest,
)
from .forced_liquidation_store import (
    DROP, FRAME, HANDSHAKE, HANDSHAKE_FAILURE, PING_SENT, PONG, READER_ERROR, SERVER_PING,
    SESSION_CLOSE, SESSION_OPEN, WRITE_FAILURE, LogStore,
)

RECORDER_SCHEMA = "forced-liquidation-recorder.v1"
_CODE = ("observability/forced_liquidation.py", "observability/forced_liquidation_store.py",
         "observability/forced_liquidation_recorder.py", "core/instrument_registry.py",
         "core/types.py")
_MESSAGE_TOO_BIG = 1009


def code_manifest() -> dict:
    base = Path(__file__).resolve().parents[1]
    return {name: hashlib.sha256((base / name).read_bytes()).hexdigest() for name in _CODE}


def dependency_manifest() -> dict:
    import websockets
    return {"python": platform.python_version(), "websockets": websockets.__version__,
            "sqlite": sqlite3.sqlite_version}


def _clock() -> tuple[int, int]:
    return time.time_ns() // 1_000_000, time.monotonic_ns()


def _transport(conn) -> dict:
    """Read back the transport settings in force on this connection."""
    protocol = getattr(conn, "protocol", None)
    return {"library": "websockets.asyncio.client",
            "ping_interval_s": getattr(conn, "ping_interval", None),
            "ping_timeout_s": getattr(conn, "ping_timeout", None),
            "close_timeout_s": getattr(conn, "close_timeout", None),
            "max_queue_high": getattr(conn, "max_queue_high", None),
            "max_queue_low": getattr(conn, "max_queue_low", None),
            "max_size": getattr(protocol, "max_message_size", getattr(protocol, "max_size", None)),
            "reconnect_backoff": "library default (connect.__aiter__)"}


class RecorderStopped(Exception):
    """A persistence failure ended recording; coverage cannot continue."""


class Session:
    """Per-connection hooks. Every call appends one durable record."""

    def __init__(self, recorder: "Recorder", attempt: int):
        self.recorder, self.attempt = recorder, attempt
        self.session_id = uuid.uuid4().hex
        self.conn = None
        self.failed: str | None = None
        self.frames = self.payload_bytes = self.pings = self.pongs = 0

    def write(self, kind, body, raw=None) -> bool:
        if self.failed:
            return False
        utc, mono = self.recorder.clock()
        try:
            self.recorder.store.append(kind, self.session_id, utc, mono, body, raw)
            return True
        except Exception as exc:  # durable failure evidence, then fail closed
            self.fail(kind, exc)
            return False

    def fail(self, kind, exc):
        self.failed = f"{kind}:{type(exc).__name__}"
        utc, mono = self.recorder.clock()
        try:
            self.recorder.store.append(WRITE_FAILURE, self.session_id, utc, mono,
                                       {"failed_kind": kind, "error": type(exc).__name__})
        except Exception:
            pass  # no durable end seal: replay reports the crash
        conn = self.conn
        if conn is not None and hasattr(conn, "close"):
            try:
                asyncio.get_running_loop().create_task(conn.close(1011, "recorder write failure"))
            except RuntimeError:
                pass

    # liveness hooks (called by _RecordingConnection or test transports)
    def on_ping_sent(self, payload: bytes):
        self.pings += 1
        self.write(PING_SENT, {"payload_hex": payload.hex()})

    def on_pong(self, payload: bytes, solicited: bool):
        self.pongs += solicited
        self.write(PONG, {"payload_hex": payload.hex(), "solicited": bool(solicited)})

    def on_server_ping(self, payload: bytes):
        self.write(SERVER_PING, {"payload_hex": payload.hex()})


def _recording_connection_class():
    from websockets.asyncio.client import ClientConnection
    from websockets.frames import Opcode

    class _RecordingConnection(ClientConnection):
        hook: Session | None = None

        async def ping(self, data=None):
            # Same payload rule as the library (four random bytes); fixed here so
            # the ping is durably recorded before it is sent.
            if data is None:
                data = struct.pack("!I", random.getrandbits(32))
            elif isinstance(data, str):
                data = data.encode()
            if self.hook is not None:
                self.hook.on_ping_sent(bytes(data))
            return await super().ping(data)

        def acknowledge_pings(self, data: bytes) -> None:
            solicited = data in self.pending_pings
            super().acknowledge_pings(data)
            if self.hook is not None:
                self.hook.on_pong(bytes(data), solicited)

        def process_event(self, event) -> None:
            super().process_event(event)
            if self.hook is not None and getattr(event, "opcode", None) is Opcode.PING:
                self.hook.on_server_ping(bytes(event.data))

    return _RecordingConnection


def websocket_connector(recorder: "Recorder", url: str, connect_kwargs: dict):
    """Real transport: library ``connect`` iterator with recording hooks."""
    from websockets.asyncio.client import connect

    conn_class = _recording_connection_class()

    class _Connect(connect):
        def process_exception(self, exc):
            recorder.record_handshake_failure(exc)
            return super().process_exception(exc)

    def factory(protocol, **kw):
        conn = conn_class(protocol, **kw)
        conn.hook = recorder.new_session()
        conn.hook.conn = conn
        return conn

    return _Connect(url, create_connection=factory, **connect_kwargs)


class Recorder:
    def __init__(self, store: LogStore, symbols: SymbolMap, metadata_raw: bytes, *,
                 url: str = STREAM_URL, environment: str = ENVIRONMENT,
                 connector=websocket_connector, connect_kwargs: dict | None = None,
                 clock=_clock):
        if symbols.raw_sha256 != hashlib.sha256(metadata_raw).hexdigest():
            raise ValueError("metadata bytes do not match the symbol map")
        self.store, self.symbols, self.url, self.environment = store, symbols, url, environment
        self.connector, self.clock = connector, clock
        # Test-only transport overrides; archived verbatim in every session_open.
        self.connect_kwargs = dict(connect_kwargs or {})
        self.config = {"schema": RECORDER_SCHEMA, "protocol_id": PROTOCOL_ID, "url": url,
                       "environment": environment, "connect_overrides": sorted(self.connect_kwargs)}
        self.mapping_key = store.put_blob(metadata_raw, symbols.envelope())
        self.attempts = 0
        self.session: Session | None = None
        self.sessions: list[dict] = []
        self.stopped_reason: str | None = None

    def new_session(self) -> Session:
        self.attempts += 1
        self.session = Session(self, self.attempts)
        return self.session

    def _open_body(self, session: Session, conn) -> dict:
        return {"schema": RECORDER_SCHEMA, "protocol_id": PROTOCOL_ID, "url": self.url,
                "environment": self.environment, "attempt": session.attempt,
                "transport": _transport(conn), "connect_overrides": {
                    k: self.connect_kwargs[k] for k in sorted(self.connect_kwargs)},
                "mapping": {**self.symbols.envelope(), "blob": self.mapping_key},
                "code_sha256": code_manifest(), "config_sha256": digest(self.config),
                "dependencies": dependency_manifest(),
                "subscription_ack": "NOT_APPLICABLE_RAW_STREAM_URL"}

    def record_handshake_failure(self, exc):
        session = self.session if self.session and self.session.conn is not None else self.new_session()
        session.write(HANDSHAKE_FAILURE, {"error": type(exc).__name__, "detail": str(exc)[:200],
                                          "attempt": session.attempt})
        self.session = None

    async def _watch(self, stop: asyncio.Event, conn):
        await stop.wait()
        self.stopped_reason = self.stopped_reason or "operator_stop"
        await conn.close(1000, "operator stop")

    async def run(self, stop: asyncio.Event | None = None) -> dict:
        stop = stop or asyncio.Event()
        start_mono, start_cpu = time.monotonic_ns(), time.process_time()
        async for conn in self.connector(self, self.url, self.connect_kwargs):
            session = getattr(conn, "hook", None) or self.session or self.new_session()
            session.conn = conn
            await self._session(session, conn, stop)
            self.session = None
            if session.failed:
                raise RecorderStopped(session.failed)
            if stop.is_set():
                break
        return self.telemetry(start_mono, start_cpu)

    async def _session(self, session: Session, conn, stop: asyncio.Event):
        from websockets.exceptions import ConnectionClosed

        session.write(SESSION_OPEN, self._open_body(session, conn))
        response = getattr(conn, "response", None)
        session.write(HANDSHAKE, {"status": "OK", "http_status": getattr(response, "status_code", None),
                                  "attempt": session.attempt})
        watcher = asyncio.get_running_loop().create_task(self._watch(stop, conn))
        start = time.monotonic_ns()
        close = {"reason": None}
        try:
            while not session.failed:
                raw = await conn.recv(decode=False)
                utc, mono = self.clock()
                raw = bytes(raw)
                body = classify_frame(raw, self.symbols)
                try:
                    self.store.append(FRAME, session.session_id, utc, mono, body, raw)
                except Exception as exc:
                    session.fail(FRAME, exc)
                    break
                session.frames += 1
                session.payload_bytes += len(raw)
        except ConnectionClosed as exc:
            rcvd, sent = exc.rcvd, exc.sent
            code = rcvd.code if rcvd else (sent.code if sent else None)
            close = {"reason": "connection_closed", "code": code,
                     "rcvd_code": rcvd.code if rcvd else None,
                     "sent_code": sent.code if sent else None,
                     "rcvd_then_sent": exc.rcvd_then_sent}
            if code == _MESSAGE_TOO_BIG:
                session.write(DROP, {"reason": "frame_exceeded_max_size", "code": code})
        except asyncio.CancelledError:
            close = {"reason": "cancelled"}
            raise
        except Exception as exc:
            session.write(READER_ERROR, {"error": type(exc).__name__, "detail": str(exc)[:200]})
            close = {"reason": "reader_error"}
            try:
                await conn.close(1011, "reader error")
            except Exception:
                pass
        finally:
            watcher.cancel()
            if stop.is_set():
                close["operator_stop"] = True
            if session.failed:
                close["reason"] = "write_failure"
            telemetry = {"wall_ms": (time.monotonic_ns() - start) // 1_000_000,
                         "frames": session.frames, "payload_bytes_received": session.payload_bytes,
                         "pings_sent": session.pings, "pongs_matched": session.pongs,
                         "application_queue": "NONE_SYNCHRONOUS_WRITE",
                         "application_drops": 0 if not session.failed else "UNKNOWN",
                         "wire_bytes_received": "UNKNOWN"}
            self.sessions.append({"session_id": session.session_id, **close, **telemetry})
            session.write(SESSION_CLOSE, {**close, "telemetry": telemetry})

    def telemetry(self, start_mono: int, start_cpu: float) -> dict:
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return {"wall_ms": (time.monotonic_ns() - start_mono) // 1_000_000,
                "cpu_process_s": round(time.process_time() - start_cpu, 6),
                "peak_rss_bytes": peak * 1024 if sys.platform.startswith("linux") else "UNKNOWN",
                "connection_attempts": self.attempts,
                "logical_bytes_persisted": self.store.bytes_persisted,
                "payload_bytes_received": sum(s["payload_bytes_received"] for s in self.sessions),
                "wire_bytes_received": "UNKNOWN", "sessions": self.sessions}


def fetch_production_metadata() -> tuple[bytes, int]:
    """One unauthenticated public GET of production exchangeInfo (no backfill)."""
    from ..data.registry_provider import DEFAULT_MAX_BYTES, DEFAULT_TIMEOUT_S, urllib_fetch
    response = urllib_fetch(METADATA_SOURCE_URL, timeout_s=DEFAULT_TIMEOUT_S,
                            max_bytes=DEFAULT_MAX_BYTES)
    received = time.time_ns() // 1_000_000
    if response.status != 200:
        raise RuntimeError(f"exchangeInfo HTTP {response.status}")
    return response.body, received


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", default="data/forced_liquidation.db")
    args = parser.parse_args(argv)
    raw, received = fetch_production_metadata()
    symbols = SymbolMap.from_exchange_info(raw, source_url=METADATA_SOURCE_URL,
                                           environment=ENVIRONMENT, received_utc_ms=received)
    store = LogStore(args.db)
    recorder = Recorder(store, symbols, raw)
    try:
        print(asyncio.run(recorder.run()))
    except KeyboardInterrupt:
        return 0
    except RecorderStopped as exc:
        print(f"recorder stopped: {exc}", file=sys.stderr)
        return 2
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
