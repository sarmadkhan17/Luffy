"""Keyset trade history (owner frontend): ordering, no duplicate/gap, concurrency,
cursor validation, bounded plans. Offline; temporary journals only."""
import base64
import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from tests.owner_frontend_fixture import FakeChat, make_app
from trader.core.journal import Journal
from trader.dashboard import trade_history as th

H = {"x-luffy-token": "fixture-token"}
T0 = datetime(2026, 9, 1, tzinfo=timezone.utc)


def add(j: Journal, tid: str, opened: datetime, status="closed", **kw):
    row = {"id": tid, "decision_id": f"d-{tid}", "symbol": kw.get("symbol", "SOL/USDT"),
           "side": "long", "amount": 1.0, "entry_price": 100.0, "notional_usdt": 100.0,
           "leverage": 2, "stop_loss": 95.0, "sl_order_id": f"algo-{tid}",
           "strategy_id": "spec:donchian", "strategy_name": "Donchian", "market_type": "futures",
           "exec_mode": "live", "opened_at": opened.isoformat(), "status": status,
           "realized_pnl": kw.get("pnl", 1.5 if status == "closed" else 0.0),
           "closed_at": (opened + timedelta(hours=1)).isoformat() if status == "closed" else None}
    with j._tx() as c:
        c.execute(f"INSERT INTO trades ({','.join(row)}) VALUES ({','.join('?' * len(row))})",
                  tuple(row.values()))


@pytest.fixture
def book(tmp_path):
    j = Journal(tmp_path / "luffy.db")
    # 237 trades, with deliberate ties on opened_at (three per minute) and mixed status
    for n in range(237):
        add(j, f"t{n:04d}", T0 + timedelta(minutes=n // 3),
            status="open" if n % 7 == 0 else "closed")
    return j


def expected(j, status="all"):
    where = "" if status == "all" else f"WHERE status='{status}'"
    return [r["id"] for r in j.query(f"SELECT id FROM trades {where} "
                                     "ORDER BY opened_at DESC, id DESC")]


def walk(j, status="all", limit=50, between=None):
    seen, cursor, pages = [], None, []
    while True:
        p = th.trade_page(j.db_path, limit=limit, status=status, cursor=cursor)
        pages.append(p)
        seen += [r["id"] for r in p["trades"]]
        if between:
            between(len(pages))
        if not p["page"]["has_more"]:
            return seen, pages
        cursor = p["page"]["next_cursor"]


@pytest.mark.parametrize("status", th.STATUSES)
@pytest.mark.parametrize("limit", [1, 7, 50, 200])
def test_traversal_is_the_full_ordered_history_without_duplicates_or_gaps(book, status, limit):
    seen, pages = walk(book, status, limit)
    assert seen == expected(book, status)               # same order, nothing missing
    assert len(seen) == len(set(seen))
    shown = 0
    for p in pages:                                     # positions are exact
        assert p["page"]["preceding"] == shown
        assert p["page"]["total"] == len(seen)
        assert len(p["trades"]) <= limit
        shown += len(p["trades"])
    assert pages[-1]["page"]["next_cursor"] is None


def test_ties_on_opened_at_are_broken_by_id_newest_first(book):
    p = th.trade_page(book.db_path, limit=6)
    ids = [r["id"] for r in p["trades"]]
    assert ids == ["t0236", "t0235", "t0234", "t0233", "t0232", "t0231"]
    assert p["order"] == ["opened_at DESC", "id DESC"]


def test_empty_history(tmp_path):
    j = Journal(tmp_path / "luffy.db")
    p = th.trade_page(j.db_path)
    assert p["trades"] == [] and p["page"]["has_more"] is False
    assert p["page"]["next_cursor"] is None and p["page"]["total"] == 0
    assert p["page"]["first"] is None and p["page"]["last"] is None


def test_journal_fields_pass_through_unchanged_and_nothing_is_derived(book):
    stored = book.query("SELECT * FROM trades WHERE id='t0231'")[0]
    row = th.trade_page(book.db_path, limit=6)["trades"][5]
    assert row["id"] == "t0231"
    assert set(row) == set(th.COLUMNS)
    for k in th.COLUMNS:
        assert row[k] == stored[k], k
    assert "excursion_json" not in row
    open_row = next(r for r in th.trade_page(book.db_path, status="open")["trades"])
    assert open_row["status"] == "open" and open_row["closed_at"] is None
    assert open_row["exit_price"] is None                # missing stays missing


def test_new_trade_after_page_one_is_neither_duplicated_nor_hides_older_rows(book):
    before = expected(book)
    seen, pages = walk(book, between=lambda n: n == 1 and add(
        book, "t-new", T0 + timedelta(days=30), status="open"))
    assert seen == before                               # the traversal's range is intact
    # the newer row is visible, not silently absorbed: every later page reports it
    assert [p["page"]["preceding"] for p in pages[1:]] == [51, 101, 151, 201]
    assert th.trade_page(book.db_path, limit=1)["trades"][0]["id"] == "t-new"


def test_backdated_insert_behind_the_cursor_is_reported_not_silently_skipped(book):
    # entry recovery books a trade later with its original submission time
    before = expected(book)
    seen, pages = walk(book, between=lambda n: n == 2 and add(
        book, "t-recovered", T0 + timedelta(minutes=70), status="open"))
    assert "t-recovered" not in seen and seen == before
    assert pages[2]["page"]["preceding"] == 101          # 100 shown + the late row
    fresh, _ = walk(book)
    assert fresh == expected(book) and "t-recovered" in fresh


def test_close_while_paging_keeps_identity_and_position(book):
    open_ids = expected(book, "open")
    target = expected(book)[120]                         # on page 3 of "all"

    def close(n):
        if n == 1:
            with book._tx() as c:
                c.execute("UPDATE trades SET status='closed', closed_at=?, realized_pnl=-2.5 "
                          "WHERE id=?", ((T0 + timedelta(days=9)).isoformat(), target))
    seen, pages = walk(book, between=close)
    assert seen.count(target) == 1 and seen == expected(book)
    row = next(r for p in pages for r in p["trades"] if r["id"] == target)
    assert row["realized_pnl"] == -2.5                   # the current journal value

    # status=open: a trade closed after its page was shown moves the count, not the rows
    first = th.trade_page(book.db_path, status="open", limit=10)
    shown = first["trades"][3]["id"]
    with book._tx() as c:
        c.execute("UPDATE trades SET status='closed' WHERE id=?", (shown,))
    second = th.trade_page(book.db_path, status="open", limit=10,
                           cursor=first["page"]["next_cursor"])
    assert second["page"]["preceding"] == 9              # client showed 10 → reported
    assert not {r["id"] for r in second["trades"]} & {r["id"] for r in first["trades"]}
    assert [r["id"] for r in first["trades"] + second["trades"]
            if r["id"] != shown] == [i for i in open_ids if i != shown][:19]


def test_repeated_page_request_is_identical(book):
    c = th.trade_page(book.db_path, limit=50)["page"]["next_cursor"]
    a = th.trade_page(book.db_path, limit=50, cursor=c)
    b = th.trade_page(book.db_path, limit=50, cursor=c)
    assert a == b


def _raw(obj) -> str:
    return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")


@pytest.mark.parametrize("cursor,code", [
    ("", "invalid_cursor"),
    ("not base64 !!", "invalid_cursor"),
    ("x" * 1100, "invalid_cursor"),
    (_raw([1, 2]), "invalid_cursor"),
    (_raw({"v": 2, "f": "all", "o": "2026", "i": "t"}), "invalid_cursor"),
    (_raw({"v": 1, "f": "all", "o": "2026"}), "invalid_cursor"),
    (_raw({"v": 1, "f": "all", "o": "2026", "i": "t", "x": 1}), "invalid_cursor"),
    (_raw({"v": 1, "f": "all", "o": "", "i": "t"}), "invalid_cursor"),
    (_raw({"v": 1, "f": "all", "o": 5, "i": "t"}), "invalid_cursor"),
    (base64.urlsafe_b64encode(b"\xff\xfe").decode(), "invalid_cursor"),
    (_raw({"v": 1, "f": "open", "o": "2026", "i": "t"}), "cursor_filter_mismatch"),
])
def test_invalid_cursor_fails_clearly(book, cursor, code):
    with pytest.raises(th.CursorError) as e:
        th.trade_page(book.db_path, status="all", cursor=cursor)
    assert e.value.code == code


def test_invalid_status_and_limit_bounds(book):
    with pytest.raises(ValueError, match="invalid_status"):
        th.trade_page(book.db_path, status="pending")
    assert th.trade_page(book.db_path, limit=0)["limit"] == 1
    assert len(th.trade_page(book.db_path, limit=10_000)["trades"]) == th.MAX_LIMIT


@pytest.mark.parametrize("status", th.STATUSES)
@pytest.mark.parametrize("with_cursor", [False, True])
def test_page_query_uses_the_index_without_a_sort_or_full_serialization(book, status, with_cursor):
    filt = "1" if status == "all" else f"status='{status}'"
    where = filt + (" AND (opened_at, id) < ('2026-09-01T01:00:00+00:00', 't9')"
                    if with_cursor else "")
    plan = " ".join(r[3] for r in book._conn().execute(
        f"EXPLAIN QUERY PLAN SELECT id FROM trades WHERE {where} "
        "ORDER BY opened_at DESC, id DESC LIMIT 51"))
    assert "TEMP B-TREE" not in plan, plan
    assert "idx_trades_" in plan, plan


@pytest.mark.parametrize("status", th.STATUSES)
def test_membership_check_streams_the_index_without_a_sort(book, status, monkeypatch):
    import sqlite3
    seen = []
    real = th.ro_connect

    def spy(path):
        conn = real(path)
        conn.set_trace_callback(seen.append)
        return conn
    monkeypatch.setattr(th, "ro_connect", spy)
    c = th.trade_page(book.db_path, status=status, limit=20)["page"]["next_cursor"]
    th.trade_page(book.db_path, status=status, limit=20, cursor=c)
    sql = [s for s in seen if "quote(opened_at)" in s]
    assert sql, "membership query not issued"
    conn = sqlite3.connect(book.db_path)
    plan = " ".join(r[3] for r in conn.execute("EXPLAIN QUERY PLAN " + sql[-1]))
    conn.close()
    assert "TEMP B-TREE" not in plan, plan
    assert "COVERING INDEX idx_trades_" in plan, plan


def test_reads_cannot_write(book, monkeypatch):
    import sqlite3
    conn = th.ro_connect(book.db_path)
    with pytest.raises(sqlite3.DatabaseError):
        conn.execute("DELETE FROM trades")
    conn.close()


# ── HTTP contract ────────────────────────────────────────────────────────────
@pytest.fixture
def api(tmp_path, monkeypatch):
    FakeChat.calls, FakeChat.mode = [], "ok"
    app, journal, _ = make_app(tmp_path, monkeypatch)
    for n in range(120):
        add(journal, f"h{n:03d}", T0 - timedelta(hours=n))
    return TestClient(app), journal


def test_http_contract_pages_through_everything(api):
    c, journal = api
    first = c.get("/owner-api/v1/trades", headers=H)
    assert first.status_code == 200 and first.headers["cache-control"].startswith("no-store")
    body = first.json()
    assert body["limit"] == 50 and body["status"] == "all"
    assert set(body["page"]) == {"cursor", "next_cursor", "has_more", "preceding", "total",
                                 "first", "last", "history"}
    assert body["source"].startswith("journal trades") and body["consistency"]
    ids, cursor = [], None
    while True:
        q = {"limit": 50} | ({"cursor": cursor} if cursor else {})
        p = c.get("/owner-api/v1/trades", params=q, headers=H).json()
        ids += [t["id"] for t in p["trades"]]
        if not p["page"]["has_more"]:
            break
        cursor = p["page"]["next_cursor"]
    assert ids == expected(journal) and len(ids) == 123   # fixture's 3 + 120


def test_http_errors_are_explicit(api):
    c, _ = api
    r = c.get("/owner-api/v1/trades", params={"cursor": "garbage!"}, headers=H)
    assert r.status_code == 400 and r.json()["error"] == "invalid_cursor"
    assert "trades" not in r.json()
    good = c.get("/owner-api/v1/trades", params={"limit": 5}, headers=H).json()
    r = c.get("/owner-api/v1/trades", params={"status": "open",
              "cursor": good["page"]["next_cursor"]}, headers=H)
    assert r.status_code == 400 and r.json()["error"] == "cursor_filter_mismatch"
    r = c.get("/owner-api/v1/trades", params={"status": "bogus"}, headers=H)
    assert r.status_code == 400 and r.json()["error"] == "invalid_status"
    assert c.get("/owner-api/v1/trades", params={"limit": "x"}, headers=H).status_code == 422
    assert c.get("/owner-api/v1/trades").status_code == 401


# ── correction pass: strict cursors ──────────────────────────────────────────
def _valid(j, status="all"):
    return th.trade_page(j.db_path, limit=5, status=status)["page"]["next_cursor"]


def _fields(cursor):
    return json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))


def _with(cursor, **kw):
    return _raw(_fields(cursor) | kw)


def _without(cursor, key):
    return _raw({k: v for k, v in _fields(cursor).items() if k != key})


def _text_cursor(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


def _canon(obj) -> str:
    return _text_cursor(json.dumps(obj, separators=(",", ":")))


@pytest.mark.parametrize("mangle", [
    pytest.param(lambda c: "!!!!" + c, id="prepended-invalid"),
    pytest.param(lambda c: c + "!!!!", id="appended-invalid"),
    pytest.param(lambda c: " " + c, id="prepended-space"),
    pytest.param(lambda c: c + "\n", id="appended-newline"),
    pytest.param(lambda c: c + "=" * (-len(c) % 4 or 4), id="padding"),
    pytest.param(lambda c: c + "==", id="extra-padding"),
    pytest.param(lambda c: c[:-1] + "=", id="padding-inside"),
    pytest.param(lambda c: c + "A", id="appended-valid-char"),
    pytest.param(lambda c: c.replace("-", "+").replace("_", "/") + "+/", id="std-alphabet"),
    pytest.param(lambda c: _text_cursor('{"v":1,'), id="malformed-json"),
    pytest.param(lambda c: _with(c, v=True), id="version-true"),
    pytest.param(lambda c: _with(c, v=False), id="version-false"),
    pytest.param(lambda c: _with(c, v=1.0), id="version-1.0"),
    pytest.param(lambda c: _with(c, v="1"), id="version-string"),
    pytest.param(lambda c: _with(c, v=None), id="version-null"),
    pytest.param(lambda c: _without(c, "v"), id="missing-version"),
    pytest.param(lambda c: _without(c, "h"), id="missing-digest"),
    pytest.param(lambda c: _without(c, "a"), id="missing-anchor"),
    pytest.param(lambda c: _without(c, "c"), id="missing-flag"),
    pytest.param(lambda c: _with(c, x=1), id="extra-field"),
    pytest.param(lambda c: _with(c, c=0), id="flag-not-bool"),
    pytest.param(lambda c: _with(c, h="ABC"), id="digest-malformed"),
    pytest.param(lambda c: _with(c, a=["2026"]), id="anchor-short"),
    pytest.param(lambda c: _with(c, a=["2000-01-01", "t"]), id="anchor-below-cursor"),
    pytest.param(lambda c: _text_cursor('{"v":1,' + json.dumps(_fields(c))[1:]),
                 id="duplicate-key"),
    pytest.param(lambda c: _text_cursor(json.dumps(_fields(c) | {"o": float("nan")})),
                 id="nan"),
    pytest.param(lambda c: _with(c, a=["\ud800", "id"]), id="anchor-lone-surrogate"),
    pytest.param(lambda c: _with(c, o="\udfff"), id="opened-lone-surrogate"),
    pytest.param(lambda c: _with(c, i="t\ud83d"), id="id-lone-surrogate"),
    pytest.param(lambda c: _with(c, f="\ud800"), id="filter-lone-surrogate"),
])
def test_cursor_decoding_is_strict_and_fails_closed(book, mangle):
    cursor = mangle(_valid(book))
    with pytest.raises(th.CursorError) as e:
        th.trade_page(book.db_path, cursor=cursor)
    assert e.value.code == "invalid_cursor"


def test_non_canonical_trailing_bits_are_rejected(book):
    def dec(t):
        return base64.urlsafe_b64decode(t + "=" * (-len(t) % 4))
    for limit in range(1, 8):                             # find a cursor with spare bits
        c = th.trade_page(book.db_path, limit=limit)["page"]["next_cursor"]
        alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
        twins = [c[:-1] + ch for ch in alphabet if ch != c[-1] and dec(c[:-1] + ch) == dec(c)]
        if twins:
            break
    assert twins, "no fixture cursor with spare bits"
    for t in twins:
        with pytest.raises(th.CursorError):
            th.trade_page(book.db_path, cursor=t)


def test_issued_and_legacy_cursors_still_decode(book):
    c = _valid(book)
    assert th.trade_page(book.db_path, limit=5, cursor=c)["page"]["history"] == {
        "changed": False, "reason": None}
    assert type(_fields(c)["v"]) is int and set(_fields(c)) == th._KEYS
    legacy = _canon({"v": 1, "f": "all", "o": _fields(c)["o"], "i": _fields(c)["i"]})
    p = th.trade_page(book.db_path, limit=5, cursor=legacy)
    assert [r["id"] for r in p["trades"]] == expected(book)[5:10]
    assert p["page"]["history"] == {"changed": None, "reason": "unverifiable_cursor"}
    nxt = th.trade_page(book.db_path, limit=5, cursor=p["page"]["next_cursor"])
    assert nxt["page"]["history"]["changed"] is None     # stays unverifiable


@pytest.mark.parametrize("form", ["!!!!{c}", "{c}!!!!", "{c}=", "{v_true}", "{surrogate}"])
def test_http_rejects_mangled_cursor_with_zero_rows(api, form):
    c, _ = api
    nc = c.get("/owner-api/v1/trades", params={"limit": 5}, headers=H).json()["page"]["next_cursor"]
    bad = form.format(c=nc, v_true=_with(nc, v=True), surrogate=_with(nc, a=["\ud800", "id"]))
    r = c.get("/owner-api/v1/trades", params={"cursor": bad}, headers=H)
    assert r.status_code == 400 and r.json()["error"] == "invalid_cursor"
    assert "trades" not in r.json()


# ── correction pass: history change detection ────────────────────────────────
def _close(j, tid, status="closed"):
    with j._tx() as c:
        c.execute("UPDATE trades SET status=? WHERE id=?", (status, tid))


def _pages(j, status, n, limit=50, cursor=None):
    out = []
    for _ in range(n):
        p = th.trade_page(j.db_path, status=status, limit=limit, cursor=cursor)
        out.append(p)
        cursor = p["page"]["next_cursor"]
    return out


@pytest.fixture
def open_book(tmp_path):
    j = Journal(tmp_path / "luffy.db")
    for n in range(120):                                  # 120 matching (open) records
        add(j, f"o{n:03d}", T0 + timedelta(hours=n), status="open")
    for n in range(30):
        add(j, f"c{n:03d}", T0 + timedelta(hours=n, minutes=30), status="closed")
    return j


def test_astra_cancellation_is_reported_although_counts_match(open_book):
    j = open_book
    p1, p2 = _pages(j, "open", 2)                         # 100 consumed
    shown = [r["id"] for r in p1["trades"] + p2["trades"]]
    control = th.trade_page(j.db_path, status="open", cursor=p2["page"]["next_cursor"])
    assert control["page"]["history"]["changed"] is False
    before = (control["page"]["preceding"], control["page"]["total"])
    add(j, "o-recovered", T0 + timedelta(hours=60, minutes=5), status="open")  # behind cursor
    _close(j, shown[10])                                  # a shown trade leaves the filter
    p3 = th.trade_page(j.db_path, status="open", cursor=p2["page"]["next_cursor"])
    assert (p3["page"]["preceding"], p3["page"]["total"]) == before == (100, 120)
    assert p3["page"]["history"] == {"changed": True, "reason": "membership_changed"}
    assert "o-recovered" not in [r["id"] for r in p3["trades"]]


def test_unchanged_traversal_never_warns(book):
    for status in th.STATUSES:
        _, pages = walk(book, status, 50)
        assert all(p["page"]["history"]["changed"] is False for p in pages)


def test_newest_insertion_is_new_not_a_history_change(book):
    _, pages = walk(book, between=lambda n: n == 1 and add(
        book, "t-new", T0 + timedelta(days=30), status="open"))
    assert all(p["page"]["history"]["changed"] is False for p in pages)
    assert pages[1]["page"]["preceding"] == 51            # still visible as newer


def test_single_backdated_insertion_is_reported(book):
    _, pages = walk(book, between=lambda n: n == 2 and add(
        book, "t-recovered", T0 + timedelta(minutes=70), status="closed"))
    assert [p["page"]["history"]["changed"] for p in pages] == [False, False, True, True, True]


def test_filter_membership_change_is_reported_both_directions(open_book):
    j = open_book
    p1 = th.trade_page(j.db_path, status="open", limit=50)
    _close(j, p1["trades"][3]["id"])                      # leaves "open"
    p2 = th.trade_page(j.db_path, status="open", limit=50, cursor=p1["page"]["next_cursor"])
    assert p2["page"]["history"]["changed"] is True
    q1 = th.trade_page(j.db_path, status="closed", limit=10)
    _close(j, "o115")                                     # enters "closed" in the read range
    q2 = th.trade_page(j.db_path, status="closed", limit=10, cursor=q1["page"]["next_cursor"])
    assert q2["page"]["history"]["changed"] is True
    a1 = th.trade_page(j.db_path, status="all", limit=10)  # "all" membership is unaffected
    _close(j, a1["trades"][0]["id"], status="open")
    a2 = th.trade_page(j.db_path, status="all", limit=10, cursor=a1["page"]["next_cursor"])
    assert a2["page"]["history"]["changed"] is False


def test_change_in_unread_range_is_not_a_history_change(open_book):
    j = open_book
    p1 = th.trade_page(j.db_path, status="open", limit=50)
    _close(j, "o000")                                     # not yet read: its page shows truth
    p2 = th.trade_page(j.db_path, status="open", limit=50, cursor=p1["page"]["next_cursor"])
    assert p2["page"]["history"]["changed"] is False


def test_warning_persists_across_remaining_pages_even_if_change_reverts(open_book):
    j = open_book
    p1 = th.trade_page(j.db_path, status="open", limit=20)
    _close(j, p1["trades"][0]["id"])
    p2 = th.trade_page(j.db_path, status="open", limit=20, cursor=p1["page"]["next_cursor"])
    _close(j, p1["trades"][0]["id"], status="open")      # membership restored
    rest = _pages(j, "open", 4, limit=20, cursor=p2["page"]["next_cursor"])
    assert p2["page"]["history"] == {"changed": True, "reason": "membership_changed"}
    assert [p["page"]["history"] for p in rest] == [
        {"changed": True, "reason": "earlier_page"}] * 4
    assert rest[-1]["page"]["next_cursor"] is None


def test_restart_from_newest_clears_the_warning(book):
    _, pages = walk(book, between=lambda n: n == 1 and add(
        book, "t-recovered", T0 + timedelta(minutes=70)))
    assert pages[-1]["page"]["history"]["changed"] is True
    fresh, again = walk(book)
    assert fresh == expected(book) and "t-recovered" in fresh
    assert all(p["page"]["history"]["changed"] is False for p in again)


def test_http_reports_history(api):
    c, journal = api
    p1 = c.get("/owner-api/v1/trades", params={"limit": 50}, headers=H).json()
    assert p1["page"]["history"] == {"changed": False, "reason": None}
    add(journal, "h-late", T0 - timedelta(hours=10, minutes=30))
    p2 = c.get("/owner-api/v1/trades", params={"cursor": p1["page"]["next_cursor"]},
               headers=H).json()
    assert p2["page"]["history"] == {"changed": True, "reason": "membership_changed"}
