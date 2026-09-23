"""Read-only producer of RegistrySnapshot from public Binance USD-M exchangeInfo.

One unauthenticated GET of ``/fapi/v1/exchangeInfo`` per ``refresh()``. No
credentials, account endpoints, order APIs, CCXT, DataFeed, Universe or Kernel.
It grants no trading authority: account-global trading and every per-symbol
account eligibility, symbolConfig, leverageBracket and shortability stay UNKNOWN.

Observation cut: ``RegistrySnapshot.as_of_ms`` (and every record's
``observed_at_ms``) is ``response_received_ms`` -- the local wall clock after
the complete response body was read, i.e. the first local instant the whole
response is knowable. It is NOT an exchange-side atomic cut; the exchange's
``serverTime``, when supplied, is retained separately in provenance only.

Target environment (production/demo) is declared by the caller's configuration
and checked against the known host table; it is never inferred from response
content. A failed refresh never produces or re-stamps a snapshot: the last good
snapshot object is kept unchanged and the failure is exposed as the latest
attempt. Kernel wiring and refresh cadence are deliberately absent.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from ..core.instrument_registry import RegistrySnapshot
from .binance_usdm_registry import from_binance_usdm_responses


PROVENANCE_SCHEMA = "binance-usdm-registry-refresh.v1"
PROVIDER = "trader.data.registry_provider"
ENDPOINT_PATH = "/fapi/v1/exchangeInfo"
ACCOUNT_SCOPE = "unauthenticated-public-metadata"
DEFAULT_TIMEOUT_S = 10.0
DEFAULT_MAX_BYTES = 8 * 1024 * 1024

PRODUCTION = "production"
DEMO = "demo"
UNSPECIFIED = "unspecified"
# Known USD-M hosts and the only environment each may be declared as.
KNOWN_HOSTS = {"https://fapi.binance.com": PRODUCTION,
               "https://demo-fapi.binance.com": DEMO}
_LOOPBACK = {"127.0.0.1", "localhost", "::1"}


@dataclass(frozen=True)
class VenueTarget:
    """Explicit request target: ``scheme://host[:port]`` plus declared environment."""

    base_url: str
    environment: str

    def __post_init__(self) -> None:
        parts = urllib.parse.urlsplit(self.base_url)
        if (parts.path or parts.query or parts.fragment or parts.username or parts.password
                or not parts.hostname or self.base_url != f"{parts.scheme}://{parts.netloc}"):
            raise ValueError("base_url must be exactly scheme://host[:port]")
        if parts.scheme != "https" and not (parts.scheme == "http" and parts.hostname in _LOOPBACK):
            raise ValueError("https required (plain http only for loopback)")
        if self.environment not in (PRODUCTION, DEMO, UNSPECIFIED):
            raise ValueError("environment must be production, demo or unspecified")
        known = KNOWN_HOSTS.get(self.base_url)
        if known is not None and self.environment != known:
            raise ValueError(f"{self.base_url} is the {known} host")
        if known is None and self.environment != UNSPECIFIED:
            raise ValueError("an unknown host cannot be declared production or demo")

    @classmethod
    def production(cls) -> "VenueTarget":
        return cls("https://fapi.binance.com", PRODUCTION)

    @classmethod
    def demo(cls) -> "VenueTarget":
        return cls("https://demo-fapi.binance.com", DEMO)

    @property
    def request_url(self) -> str:
        return self.base_url + ENDPOINT_PATH

    @property
    def source(self) -> str:
        return f"binance-usdm:{self.environment}:{self.request_url}"


@dataclass(frozen=True)
class HttpResponse:
    status: int
    body: bytes


class FetchError(Exception):
    """Transport failure with a short reason token; ``status``/``body`` if any arrived."""

    def __init__(self, reason: str, status: int | None = None, body: bytes | None = None):
        super().__init__(reason)
        self.reason, self.status, self.body = reason, status, body


class Fetcher(Protocol):
    def __call__(self, url: str, *, timeout_s: float, max_bytes: int) -> HttpResponse: ...


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # a 3xx surfaces as HTTPError; never a second request


def urllib_fetch(url: str, *, timeout_s: float, max_bytes: int) -> HttpResponse:
    """One public GET: no auth headers, no proxies, no redirects, bounded bytes.

    ``timeout_s`` bounds connect and every socket read, and a monotonic total
    deadline is checked across the chunked body read. DNS resolution is not
    covered by the total deadline.
    """
    deadline = time.monotonic() + timeout_s
    request = urllib.request.Request(url, method="GET", headers={
        "Accept": "application/json", "User-Agent": "luffy-registry-provider/1"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    try:
        with opener.open(request, timeout=timeout_s) as resp:
            if resp.geturl() != url:
                raise FetchError("redirect_refused", resp.status)
            declared = resp.headers.get("Content-Length")
            if declared is not None and declared.isdigit() and int(declared) > max_bytes:
                raise FetchError("oversized", resp.status)
            chunks, total = [], 0
            while True:
                if time.monotonic() > deadline:
                    raise FetchError("timeout", resp.status)
                chunk = resp.read(min(65536, max_bytes + 1 - total))
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
                if total > max_bytes:
                    raise FetchError("oversized", resp.status)
            return HttpResponse(resp.status, b"".join(chunks))
    except FetchError:
        raise
    except urllib.error.HTTPError as exc:
        reason = "redirect_refused" if 300 <= exc.code < 400 else "http_error"
        raise FetchError(reason, exc.code) from None
    except TimeoutError:
        raise FetchError("timeout") from None
    except urllib.error.URLError as exc:
        raise FetchError("timeout" if isinstance(exc.reason, TimeoutError) else "network_error") from None
    except OSError:
        raise FetchError("network_error") from None


def _wall_ms() -> int:
    return time.time_ns() // 1_000_000


@dataclass(frozen=True)
class RegistryRefreshProvenance:
    """Immutable record of one refresh attempt, successful or not."""

    outcome: str                       # "SUCCESS" | "FAILED"
    failure_reason: str | None
    environment: str
    base_url: str
    endpoint_path: str
    request_url: str
    source: str
    request_start_ms: int
    response_received_ms: int | None   # None when no complete body arrived
    server_time_ms: int | None         # exchange serverTime, provenance only
    http_status: int | None
    body_sha256: str | None
    body_bytes: int | None
    snapshot_id: str | None
    as_of_ms: int | None
    record_count: int | None
    method: str = "GET"
    provider: str = PROVIDER
    schema: str = PROVENANCE_SCHEMA


@dataclass(frozen=True)
class RegistryRefreshResult:
    provenance: RegistryRefreshProvenance
    snapshot: RegistrySnapshot | None  # None on failure; never a prior snapshot

    @property
    def ok(self) -> bool:
        return self.snapshot is not None


class _Refused(Exception):
    pass


def _no_constant(name: str):
    raise _Refused("non_finite_json")


def _parse(body: bytes) -> tuple[dict, int | None]:
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        raise _Refused("malformed_json") from None
    try:
        payload = json.loads(text, parse_constant=_no_constant)
    except ValueError:
        raise _Refused("malformed_json") from None
    if not isinstance(payload, dict):
        raise _Refused("malformed_exchange_info")
    symbols = payload.get("symbols")
    if not isinstance(symbols, list) or not symbols or not all(isinstance(s, dict) for s in symbols):
        raise _Refused("malformed_exchange_info")
    server_time = payload.get("serverTime")
    if server_time is not None and (type(server_time) is not int or server_time < 0):
        raise _Refused("malformed_server_time")
    return payload, server_time


class BinanceUsdmRegistryProvider:
    """Synchronous public exchangeInfo -> RegistrySnapshot producer."""

    def __init__(self, target: VenueTarget, *, fetcher: Fetcher = urllib_fetch,
                 clock_ms: Callable[[], int] = _wall_ms,
                 timeout_s: float = DEFAULT_TIMEOUT_S, max_bytes: int = DEFAULT_MAX_BYTES):
        if not isinstance(target, VenueTarget):
            raise TypeError("explicit VenueTarget required")
        if not timeout_s > 0 or max_bytes <= 0:
            raise ValueError("positive timeout and byte limit required")
        self.target, self.timeout_s, self.max_bytes = target, float(timeout_s), int(max_bytes)
        self._fetch, self._clock = fetcher, clock_ms
        self._lock = threading.Lock()
        # (snapshot, provenance) replaced together by one reference assignment.
        self._good: tuple[RegistrySnapshot, RegistryRefreshProvenance] | None = None
        self._attempt: RegistryRefreshProvenance | None = None

    def latest(self) -> RegistrySnapshot | None:
        good = self._good
        return good[0] if good else None

    def latest_provenance(self) -> RegistryRefreshProvenance | None:
        """Provenance of the refresh that produced ``latest()``."""
        good = self._good
        return good[1] if good else None

    def latest_attempt(self) -> RegistryRefreshProvenance | None:
        """Provenance of the most recent refresh attempt, including failures."""
        return self._attempt

    def refresh(self) -> RegistryRefreshResult:
        with self._lock:
            result = self._refresh()
            if result.snapshot is not None:
                self._good = (result.snapshot, result.provenance)
            self._attempt = result.provenance
            return result

    def _refresh(self) -> RegistryRefreshResult:
        t = self.target
        start = self._clock()
        received = status = body = server_time = None
        try:
            try:
                response = self._fetch(t.request_url, timeout_s=self.timeout_s, max_bytes=self.max_bytes)
            except FetchError as exc:
                status, body = exc.status, exc.body
                raise _Refused(exc.reason) from None
            except Exception:
                raise _Refused("transport_error") from None
            received = self._clock()
            status, body = response.status, response.body
            if not isinstance(body, bytes):
                raise _Refused("transport_error")
            if len(body) > self.max_bytes:
                raise _Refused("oversized")
            if status != 200:
                raise _Refused("http_error")
            payload, server_time = _parse(body)
            try:
                snapshot = from_binance_usdm_responses(
                    exchange_info=payload, as_of_ms=received,
                    account_scope=ACCOUNT_SCOPE, source=t.source)
            except Exception:
                raise _Refused("translation_failed") from None
            return RegistryRefreshResult(self._provenance(
                "SUCCESS", None, start, received, server_time, status, body, snapshot), snapshot)
        except _Refused as exc:
            return RegistryRefreshResult(self._provenance(
                "FAILED", str(exc), start, received, server_time, status, body, None), None)

    def _provenance(self, outcome, reason, start, received, server_time, status, body, snapshot):
        t = self.target
        return RegistryRefreshProvenance(
            outcome=outcome, failure_reason=reason, environment=t.environment,
            base_url=t.base_url, endpoint_path=ENDPOINT_PATH, request_url=t.request_url,
            source=t.source, request_start_ms=start, response_received_ms=received,
            server_time_ms=server_time, http_status=status,
            body_sha256=hashlib.sha256(body).hexdigest() if isinstance(body, bytes) else None,
            body_bytes=len(body) if isinstance(body, bytes) else None,
            snapshot_id=snapshot.snapshot_id if snapshot else None,
            as_of_ms=snapshot.as_of_ms if snapshot else None,
            record_count=len(snapshot.records) if snapshot else None)
