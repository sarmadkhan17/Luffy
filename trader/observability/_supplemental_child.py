"""Child process for one supplemental Attention kline request.

Standard library only, run with `python -I` by path so no trader package is
imported. It holds no DataFeed, cache, candle store, journal or credentials.
Its only output is one compact JSON object on stdout that echoes the
requested symbol/interval/limit, the wall-clock request bounds and either
the parsed kline `rows` or a short `error` token. The parent validates every
field, kills the child's process group at the deadline and discards
anything written after it.

argv: url symbol interval limit timeout_s max_bytes
"""
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def _now_ms():
    return time.time_ns() // 1_000_000


def _no_constant(name):
    raise ValueError(f"non-finite JSON constant {name}")


def _request(url, symbol, interval, limit, timeout_s, max_bytes):
    """Exactly one request, no retry. Returns ('rows', list) or ('error', token)."""
    query = urllib.parse.urlencode(
        {"symbol": symbol, "interval": interval, "limit": limit})
    try:
        # Disable redirects and proxies: a 3xx must never become a second
        # HTTP request or send this observation to a different endpoint.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
        with opener.open(f"{url}?{query}", timeout=timeout_s) as resp:
            body = resp.read(max_bytes + 1)
    except urllib.error.HTTPError as exc:
        return "error", f"http_{int(exc.code)}"
    except TimeoutError:
        return "error", "network_timeout"
    except (urllib.error.URLError, OSError):
        return "error", "network_error"
    if len(body) > max_bytes:
        return "error", "response_too_large"
    try:
        rows = json.loads(body, parse_constant=_no_constant)
    except ValueError:
        return "error", "body_not_json"
    if not isinstance(rows, list):
        return "error", "body_not_list"
    return "rows", rows


def main(argv):
    url, symbol, interval, limit, timeout_s, max_bytes = argv
    limit, max_bytes, timeout_s = int(limit), int(max_bytes), float(timeout_s)
    started = _now_ms()
    key, value = _request(url, symbol, interval, limit, timeout_s, max_bytes)
    msg = {"symbol": symbol, "interval": interval, "limit": limit,
           "request_start_ms": started, "request_end_ms": _now_ms(), key: value}
    sys.stdout.write(json.dumps(msg, separators=(",", ":"), allow_nan=False))
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
