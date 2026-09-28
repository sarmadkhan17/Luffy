"""Local IPC between owner adapters and the kernel's OwnerService.

Transport: one AF_UNIX stream socket (no network listener) inside a private
directory (default /run/user/<uid>/luffy-owner: mode 0700, owned by the kernel
uid, never a symlink, and no ancestor another user could rename it through). The socket is mode 0600 and the peer uid is checked (SO_PEERCRED).

Channel authentication: the kernel keeps one random key per IPC channel
(dashboard, openclaw, whatsapp, cli) in <dir>/keys/<channel>.key (0600). A
request frame names its channel and carries an HMAC-SHA256 over
(channel, nonce, request) with that channel's key, so a client can act only as
the channel whose key it holds, and the kernel resolves the principal from
that channel's identity table. The response carries an HMAC over
(nonce, result) with the same key: the client accepts only a response bound to
its own nonce and request id from a holder of the key — a replacement
listener cannot fabricate a result.

    request  {"v": 2, "channel": c, "nonce": n, "request": {...}, "mac": m}
    response {"v": 2, "nonce": n, "result": {...}, "mac": m}

Limits of this boundary: every process running as the kernel uid can read the
key files (and the journal and the trading credentials file). Separating that
authority needs separate OS users; see the report.

Outcome semantics: failure to connect → UNAVAILABLE (never delivered). Any
failure after the first byte is sent → ERROR outcome unknown; a retry with the
same request_id returns the recorded result, never a second execution. The
client never falls back to a local state change and never queues.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import secrets
import socket
import stat
import struct
import threading
from pathlib import Path

from .contract import (CONTAINMENT_OPERATIONS, INTENT_OPERATIONS, READ_OPERATIONS,
                       RECOVERY_OPERATIONS, WIRE_VERSION, MalformedRequest,
                       OwnerRequest, OwnerResult, Status, refused)

log = logging.getLogger("owner_interface.ipc")

MAX_FRAME = 64 * 1024
IPC_CHANNELS = ("dashboard", "openclaw", "whatsapp", "cli")
SOCKET_NAME = "owner.sock"
_HDR = struct.Struct(">I")


class InsecurePath(RuntimeError):
    pass


# ── filesystem trust ─────────────────────────────────────────────────────
def check_private_dir(path: Path, uid: int | None = None) -> None:
    """A real directory (not a symlink), owned by uid, no group/other access."""
    uid = os.getuid() if uid is None else uid
    st = os.lstat(path)
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
        raise InsecurePath(f"{path} is not a real directory")
    if st.st_uid != uid or st.st_mode & 0o077:
        raise InsecurePath(f"{path} must be owned by uid {uid} with mode 0700")


def check_ancestors(path: Path, uid: int | None = None) -> None:
    """No ancestor may let another user replace the IPC directory: every
    ancestor is a real directory owned by root or uid, and is not group/other
    writable unless sticky (e.g. /tmp, where others cannot rename our entry)."""
    uid = os.getuid() if uid is None else uid
    path = Path(os.path.abspath(path))
    for parent in path.parents:
        st = os.lstat(parent)
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
            raise InsecurePath(f"{parent} is not a real directory")
        if st.st_uid not in (0, uid):
            raise InsecurePath(f"{parent} is owned by uid {st.st_uid}")
        if st.st_mode & 0o022 and not st.st_mode & stat.S_ISVTX:
            raise InsecurePath(f"{parent} is group/other writable (mode {oct(st.st_mode & 0o777)})")


def default_ipc_dir() -> Path | None:
    """The per-user runtime directory (tmpfs, 0700, owned by this uid) if present."""
    run = Path(f"/run/user/{os.getuid()}")
    return run / "luffy-owner" if run.is_dir() else None


def resolve_ipc_dir(configured) -> Path | None:
    if configured in (None, "", "auto"):
        return default_ipc_dir()
    return Path(configured)


def ensure_private_dir(path: Path) -> None:
    try:
        os.mkdir(path, 0o700)
    except FileExistsError:
        pass
    check_private_dir(path)


def _read_key(path: Path) -> bytes:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        st = os.fstat(fd)
        if st.st_uid != os.getuid() or st.st_mode & 0o077 or not stat.S_ISREG(st.st_mode):
            raise InsecurePath(f"{path} must be a 0600 file owned by uid {os.getuid()}")
        key = bytes.fromhex(os.read(fd, 256).decode().strip())
    finally:
        os.close(fd)
    if len(key) < 32:
        raise InsecurePath(f"{path} holds a short key")
    return key


def load_key(ipc_dir: Path, channel: str) -> bytes:
    ipc_dir = Path(ipc_dir)
    check_ancestors(ipc_dir)
    check_private_dir(ipc_dir)
    check_private_dir(ipc_dir / "keys")
    return _read_key(ipc_dir / "keys" / f"{channel}.key")


def ensure_keys(ipc_dir: Path, channels=IPC_CHANNELS) -> dict[str, bytes]:
    """Create missing channel keys (0600, O_EXCL); keep existing ones across restarts."""
    ipc_dir = Path(ipc_dir)
    check_ancestors(ipc_dir)
    ensure_private_dir(ipc_dir)
    ensure_private_dir(ipc_dir / "keys")
    keys = {}
    for ch in channels:
        path = ipc_dir / "keys" / f"{ch}.key"
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        except FileExistsError:
            pass
        else:
            try:
                os.write(fd, secrets.token_hex(32).encode())
            finally:
                os.close(fd)
        keys[ch] = _read_key(path)
    return keys


# ── framing and MACs ─────────────────────────────────────────────────────
def _canon(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str).encode()


def _mac(key: bytes, obj) -> str:
    return hmac.new(key, _canon(obj), hashlib.sha256).hexdigest()


def _recv_exact(conn: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = conn.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("peer closed")
        buf.extend(chunk)
    return bytes(buf)


def read_frame(conn: socket.socket) -> dict:
    (size,) = _HDR.unpack(_recv_exact(conn, _HDR.size))
    if size == 0 or size > MAX_FRAME:
        raise MalformedRequest(f"frame size {size} refused")
    try:
        obj = json.loads(_recv_exact(conn, size).decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        raise MalformedRequest("frame is not JSON") from e
    if not isinstance(obj, dict) or obj.get("v") != WIRE_VERSION:
        raise MalformedRequest("unsupported wire version")
    return obj


def write_frame(conn: socket.socket, obj: dict) -> None:
    data = json.dumps(obj, default=str).encode("utf-8")
    if len(data) > MAX_FRAME:
        raise ValueError("frame too large")
    conn.sendall(_HDR.pack(len(data)) + data)


def _peer_uid(conn: socket.socket) -> int | None:
    try:
        creds = conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED,
                                struct.calcsize("3i"))
        return struct.unpack("3i", creds)[1]
    except (OSError, AttributeError):
        return None


def _class_of(operation: str) -> str:
    if operation in RECOVERY_OPERATIONS:
        return "recovery"
    if operation in CONTAINMENT_OPERATIONS or operation in INTENT_OPERATIONS:
        return "control"
    return "read"


# ── server (kernel) ──────────────────────────────────────────────────────
class OwnerIPCServer:
    """Runs inside the kernel. Hands each authenticated request to `service.execute`.

    Connection budget: up to `max_connections` are read concurrently; after
    authentication each request takes a slot of its class — containment and
    intents (16), reads (8), recovery (2). Recovery can therefore never
    exhaust the slots an urgent freeze/halt/panic needs.
    """

    def __init__(self, service, ipc_dir, *, channels=IPC_CHANNELS, max_connections: int = 32,
                 io_timeout_s: float = 5.0, allowed_uid: int | None = None,
                 class_slots: dict | None = None):
        self.service = service
        self.dir = Path(ipc_dir)
        self.path = self.dir / SOCKET_NAME
        self.channels = tuple(channels)
        self.io_timeout_s = io_timeout_s
        self.allowed_uid = os.getuid() if allowed_uid is None else allowed_uid
        self._conn_slots = threading.BoundedSemaphore(max_connections)
        slots = {"control": 16, "read": 8, "recovery": 2, **(class_slots or {})}
        self._class_slots = {k: threading.BoundedSemaphore(v) for k, v in slots.items()}
        self._keys: dict[str, bytes] = {}
        self._sock: socket.socket | None = None
        self._inode: tuple[int, int] | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> "OwnerIPCServer":
        self._keys = ensure_keys(self.dir, self.channels)
        try:
            st = os.lstat(self.path)
        except FileNotFoundError:
            st = None
        if st is not None:
            if not stat.S_ISSOCK(st.st_mode):
                raise InsecurePath(f"{self.path} exists and is not a socket")
            probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            probe.settimeout(1.0)
            try:
                probe.connect(str(self.path))
                raise RuntimeError(f"another owner interface is listening on {self.path}")
            except (ConnectionRefusedError, FileNotFoundError):
                os.unlink(self.path)        # stale socket from a dead kernel
            except socket.timeout as e:
                raise RuntimeError(f"a listener on {self.path} is not answering") from e
            finally:
                probe.close()
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        old = os.umask(0o177)
        try:
            sock.bind(str(self.path))
        finally:
            os.umask(old)
        os.chmod(self.path, 0o600)          # inside our 0700 directory: no swap window
        st = os.lstat(self.path)
        self._inode = (st.st_dev, st.st_ino)
        sock.listen(32)
        sock.settimeout(0.5)
        self._sock = sock
        self._thread = threading.Thread(target=self._accept_loop, daemon=True,
                                        name="owner-interface")
        self._thread.start()
        flush = getattr(self.service, "flush_audit", None)
        if flush is not None:
            threading.Thread(target=self._flush_loop, args=(flush,), daemon=True,
                             name="owner-audit-flush").start()
        log.info("owner interface listening on %s", self.path)
        return self

    def _flush_loop(self, flush) -> None:
        while not self._stop.wait(2.0):
            flush()
        flush()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
        if self._sock:
            self._sock.close()
        # unlink only the socket this server bound, never a replacement
        try:
            st = os.lstat(self.path)
            if stat.S_ISSOCK(st.st_mode) and (st.st_dev, st.st_ino) == self._inode:
                os.unlink(self.path)
        except OSError:
            pass

    def _accept_loop(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                if self._stop.is_set():
                    return
                continue
            if not self._conn_slots.acquire(blocking=False):
                conn.close()                      # unauthenticated: no answer owed
                self.service.record_ingress_refusal(None, "connection_limit")
                continue
            threading.Thread(target=self._serve, args=(conn,), daemon=True,
                             name="owner-interface-conn").start()

    def _answer(self, conn, key: bytes | None, nonce, result: OwnerResult) -> None:
        try:
            conn.settimeout(self.io_timeout_s)
            body = {"nonce": nonce, "result": result.to_wire()}
            frame = {"v": WIRE_VERSION, **body}
            if key is not None:
                frame["mac"] = _mac(key, body)
            write_frame(conn, frame)
        except OSError:
            pass

    def _authenticate(self, frame: dict):
        channel, nonce, raw, mac = (frame.get("channel"), frame.get("nonce"),
                                    frame.get("request"), frame.get("mac"))
        key = self._keys.get(channel) if isinstance(channel, str) else None
        if key is None or not isinstance(nonce, str) or not isinstance(mac, str) \
                or not 16 <= len(nonce) <= 128:
            return None, None, None, "channel_not_authenticated"
        if not hmac.compare_digest(_mac(key, {"channel": channel, "nonce": nonce,
                                              "request": raw}), mac):
            return None, None, None, "channel_not_authenticated"
        return channel, key, nonce, None

    def _serve(self, conn: socket.socket) -> None:
        cls = None
        try:
            if _peer_uid(conn) != self.allowed_uid:
                self.service.record_ingress_refusal(None, "peer_not_permitted")
                return
            conn.settimeout(self.io_timeout_s)
            try:
                frame = read_frame(conn)
            except MalformedRequest:
                self.service.record_ingress_refusal(None, "malformed_frame")
                return
            except (OSError, ConnectionError):
                return
            channel, key, nonce, denial = self._authenticate(frame)
            if denial:
                self.service.record_ingress_refusal(None, denial)
                return                             # no MAC key: nothing trustworthy to say
            try:
                req = OwnerRequest.from_wire(frame.get("request"))
                if req.channel != channel:
                    raise MalformedRequest("request channel differs from authenticated channel")
            except (MalformedRequest, TypeError, KeyError):
                self.service.record_ingress_refusal(channel, "malformed_request")
                return self._answer(conn, key, nonce,
                                    OwnerResult(None, None, Status.REFUSED, channel=channel,
                                                reasons=("malformed_request",)))
            cls = _class_of(req.operation)
            if not self._class_slots[cls].acquire(blocking=False):
                cls = None
                return self._answer(conn, key, nonce, self.service.record_admission_refusal(
                    req, "owner_interface_busy"))
            conn.settimeout(None)               # execution time is the service's bound
            result = self.service.execute(req)
            self._answer(conn, key, nonce, result)
        except Exception:
            log.exception("owner interface connection failed")
        finally:
            if cls is not None:
                self._class_slots[cls].release()
            conn.close()
            self._conn_slots.release()


# ── client (adapters) ────────────────────────────────────────────────────
class OwnerClient:
    """Adapter-side client for one channel. Holds only that channel's key."""

    def __init__(self, ipc_dir, channel: str, *, timeout_s: float = 10.0,
                 control_timeout_s: float = 120.0):
        if channel not in IPC_CHANNELS:
            raise ValueError(f"not an IPC channel: {channel!r}")
        self.dir = Path(ipc_dir)
        self.channel = channel
        self.timeout_s = timeout_s
        self.control_timeout_s = control_timeout_s

    def call(self, req: OwnerRequest) -> OwnerResult:
        if req.channel != self.channel:
            return refused(req, "wrong_channel_for_client")
        try:
            key = load_key(self.dir, self.channel)
        except (OSError, ValueError, InsecurePath):
            return refused(req, "owner_interface_key_unavailable", Status.UNAVAILABLE)
        timeout = self.control_timeout_s if req.is_control else self.timeout_s
        nonce = secrets.token_hex(16)
        body = {"channel": self.channel, "nonce": nonce, "request": req.to_wire()}
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        try:
            try:
                sock.connect(str(self.dir / SOCKET_NAME))
            except (FileNotFoundError, ConnectionRefusedError, PermissionError, socket.timeout):
                return refused(req, "kernel_unavailable", Status.UNAVAILABLE)
            # from here on the request may have been delivered: never "not executed"
            try:
                write_frame(sock, {"v": WIRE_VERSION, **body, "mac": _mac(key, body)})
                frame = read_frame(sock)
            except socket.timeout:
                return refused(req, "kernel_timeout_outcome_unknown", Status.OUTCOME_UNKNOWN)
            except (OSError, ConnectionError, MalformedRequest):
                return refused(req, "kernel_response_missing_outcome_unknown", Status.OUTCOME_UNKNOWN)
            answer = {"nonce": frame.get("nonce"), "result": frame.get("result")}
            mac = frame.get("mac")
            if (not isinstance(mac, str) or frame.get("nonce") != nonce
                    or not hmac.compare_digest(_mac(key, answer), mac)):
                return refused(req, "kernel_response_unverified_outcome_unknown", Status.OUTCOME_UNKNOWN)
            try:
                result = OwnerResult.from_wire(frame.get("result"))
            except (MalformedRequest, TypeError):
                return refused(req, "kernel_response_invalid_outcome_unknown", Status.OUTCOME_UNKNOWN)
            if result.request_id not in (req.request_id, None):
                return refused(req, "kernel_response_mismatch_outcome_unknown", Status.OUTCOME_UNKNOWN)
            return result
        finally:
            sock.close()
