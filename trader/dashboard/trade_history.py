"""Trade history pages for the owner frontend: keyset, read-only, bounded.

Order: ``opened_at DESC, id DESC`` — the journal's own immutable identity.
``opened_at`` is written once by ``Journal.add_trade`` and never updated
(trade_accounting treats it as identity); ``id`` is the primary key and
breaks ties. Both comparisons are SQLite's TEXT order of the stored values,
the same order the index and the legacy views use.

A cursor names the last row of a page, so a forward traversal never repeats
or skips a row that existed in its range. What keyset alone cannot see is a
change *behind* the cursor — entry recovery books a trade later with the
``opened_at`` of its original submission, and a close/open moves a trade in or
out of a status filter. Counts cannot carry that: one late insert and one
departure cancel. Each cursor therefore also carries the traversal's anchor
(the first row of its first page) and a digest of the exact membership of the
consumed range ``anchor >= (opened_at, id) >= cursor`` for its filter, read in
the same transaction as the page. The next page re-reads that range and
compares: any different set of trades is reported as
``history.changed = true`` and every later cursor of the traversal carries the
flag, so the report cannot be cancelled or lost. Trades newer than the anchor
are new, not missed; they show as ``preceding`` exceeding the rows shown.
``preceding`` and ``total`` are counted in the same transaction.

Rows are the journal columns as stored; nothing is derived. ``excursion_json``
is omitted (bulky provenance); ``mfe_r``/``mae_r`` are the journal's own
measurements, ``None`` meaning not measured.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re

from ..engine.protection_snapshot import ro_connect

DEFAULT_LIMIT = 50
MAX_LIMIT = 200
MAX_CURSOR_CHARS = 1024
STATUSES = ("all", "open", "closed")
COLUMNS = ("id", "decision_id", "symbol", "side", "amount", "entry_price", "exit_price",
           "notional_usdt", "leverage", "stop_loss", "take_profit", "sl_order_id",
           "initial_risk", "tp1_done", "strategy_id", "strategy_name", "market_type",
           "exec_mode", "status", "realized_pnl", "close_reason", "opened_at", "closed_at",
           "mfe_r", "mae_r")
ORDER = ("opened_at DESC", "id DESC")


class CursorError(ValueError):
    """The cursor is not one this endpoint issued for this filter."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


_B64URL = re.compile(r"[A-Za-z0-9_-]+")
_DIGEST = re.compile(r"[0-9a-f]{32}")
_KEYS = {"v", "f", "o", "i", "a", "h", "c"}
_LEGACY_KEYS = {"v", "f", "o", "i"}          # issued before the history digest


def _b64(obj: dict) -> str:
    raw = json.dumps(obj, separators=(",", ":"), ensure_ascii=True).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def encode_cursor(status: str, opened_at: str, trade_id: str,
                  anchor: tuple[str, str], digest: str, changed: bool) -> str:
    return _b64({"v": 1, "f": status, "o": opened_at, "i": trade_id,
                 "a": list(anchor), "h": digest, "c": changed})


def _unique_keys(pairs):
    if len({k for k, _ in pairs}) != len(pairs):
        raise ValueError("duplicate key")
    return dict(pairs)


def _reject_constant(name):
    raise ValueError(name)


def _text(x) -> bool:
    """A non-empty string that is valid Unicode (JSON can carry lone surrogates)."""
    if not isinstance(x, str) or x == "":
        return False
    try:
        x.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def decode_cursor(cursor: str, status: str) -> dict:
    """The cursor's fields, or CursorError. Strict: canonical unpadded base64url
    of a JSON object with exactly the issued keys and types; ``v`` is the int 1."""
    if (not isinstance(cursor, str) or not cursor or len(cursor) > MAX_CURSOR_CHARS
            or not _B64URL.fullmatch(cursor) or len(cursor) % 4 == 1):
        raise CursorError("invalid_cursor")
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        if base64.urlsafe_b64encode(raw).decode().rstrip("=") != cursor:
            raise ValueError("non-canonical")        # stray trailing bits
        c = json.loads(raw.decode("ascii"), object_pairs_hook=_unique_keys,
                       parse_constant=_reject_constant)
    except (binascii.Error, ValueError, UnicodeDecodeError):
        raise CursorError("invalid_cursor") from None
    if not isinstance(c, dict) or set(c) not in (_KEYS, _LEGACY_KEYS):
        raise CursorError("invalid_cursor")
    if type(c["v"]) is not int or c["v"] != 1 or not all(_text(c[k]) for k in "foi"):
        raise CursorError("invalid_cursor")
    if len(c) == len(_KEYS) and not (
            isinstance(c["a"], list) and len(c["a"]) == 2 and all(map(_text, c["a"]))
            and isinstance(c["h"], str) and _DIGEST.fullmatch(c["h"])
            and type(c["c"]) is bool
            and (c["a"][0], c["a"][1]) >= (c["o"], c["i"])):
        raise CursorError("invalid_cursor")
    if c["f"] != status:
        raise CursorError("cursor_filter_mismatch")
    return c


def _membership(conn, filt: str, params: list, anchor, low, split) -> tuple[str, str]:
    """Digests of the matching trades in ``low <= (opened_at, id) <= anchor``, newest
    first: (over those >= split, over all of them). A range's digest is the SHA-256 of
    each key's ``quote(opened_at),quote(id);`` in order — ``quote()`` makes keys
    unambiguous. One covering-index scan in index order, hashed as it streams: no
    sort, and memory does not grow with the range."""
    h = hashlib.sha256()
    upper = None
    for item, above in conn.execute(
            "SELECT quote(opened_at) || ',' || quote(id) || ';', (opened_at, id) >= (?, ?) "
            f"FROM trades WHERE {filt} AND (opened_at, id) <= (?, ?) "
            f"AND (opened_at, id) >= (?, ?) ORDER BY {', '.join(ORDER)}",
            (*split, *params, *anchor, *low)):
        if not above and upper is None:
            upper = h.hexdigest()[:32]
        h.update(item.encode())
    whole = h.hexdigest()[:32]
    return upper or whole, whole


def trade_page(db_path, *, limit: int = DEFAULT_LIMIT, status: str = "all",
               cursor: str | None = None) -> dict:
    """One page, newest first. Raises CursorError / ValueError on bad input."""
    if status not in STATUSES:
        raise ValueError("invalid_status")
    limit = max(1, min(int(limit), MAX_LIMIT))
    c = decode_cursor(cursor, status) if cursor is not None else None
    after = (c["o"], c["i"]) if c else None
    where, params = [], []
    if status != "all":
        where.append("status = ?")
        params.append(status)
    filt = " AND ".join(where) or "1"
    conn = ro_connect(db_path)
    try:
        conn.execute("BEGIN")                  # rows, counts, digests: one snapshot
        page_where, page_params = filt, list(params)
        if after is not None:
            page_where += " AND (opened_at, id) < (?, ?)"
            page_params += list(after)
        rows = [dict(r) for r in conn.execute(
            f"SELECT {', '.join(COLUMNS)} FROM trades WHERE {page_where} "
            f"ORDER BY {', '.join(ORDER)} LIMIT ?", (*page_params, limit + 1))]
        total = conn.execute(f"SELECT COUNT(*) FROM trades WHERE {filt}",
                             params).fetchone()[0]
        preceding = 0 if after is None else conn.execute(
            f"SELECT COUNT(*) FROM trades WHERE {filt} AND (opened_at, id) >= (?, ?)",
            (*params, *after)).fetchone()[0]
        has_more = len(rows) > limit
        rows = rows[:limit]
        key = (lambda r: (r["opened_at"], r["id"]))
        last = key(rows[-1]) if has_more and rows else None
        if c is None:                          # first page: the traversal starts here
            anchor = key(rows[0]) if rows else None
            changed, reason = False, None
        elif len(c) == len(_LEGACY_KEYS):      # nothing to verify against
            anchor, changed, reason = None, None, "unverifiable_cursor"
        else:
            anchor = tuple(c["a"])
        consumed = extended = None
        if anchor is not None and (after is not None or last is not None):
            # consumed range [after, anchor] (verified) and this page, in one scan
            consumed, extended = _membership(conn, filt, params, anchor, last or after,
                                             after or anchor)
        if c is not None and anchor is not None:
            changed = c["c"] or consumed != c["h"]
            reason = (("earlier_page" if c["c"] else "membership_changed")
                      if changed else None)
        next_cursor = None
        if last is not None:
            next_cursor = (_b64({"v": 1, "f": status, "o": last[0], "i": last[1]})
                           if anchor is None else    # a legacy traversal stays unverifiable
                           encode_cursor(status, *last, anchor, extended, bool(changed)))
        conn.execute("COMMIT")
    finally:
        conn.close()
    edge = (lambda r: {"opened_at": r["opened_at"], "id": r["id"]})
    last = rows[-1] if rows else None
    return {
        "trades": rows, "limit": limit, "status": status, "order": list(ORDER),
        "page": {
            "cursor": cursor,
            "next_cursor": next_cursor,
            "has_more": has_more,
            "preceding": int(preceding),
            "total": int(total),
            "first": edge(rows[0]) if rows else None,
            "last": edge(last) if last else None,
            "history": {"changed": changed, "reason": reason},
        },
    }
