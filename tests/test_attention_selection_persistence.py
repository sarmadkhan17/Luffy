"""Attention selection and its cursor commit together; fetch outcomes never move the cursor."""
import ast
import hashlib
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from trader.core.instrument_registry import (
    AccountTrading, AssetClass, Capability, Eligibility, InstrumentId, InstrumentRecord,
    OrderConstraints, Presence, RegistrySnapshot,
)
from trader.core.journal import Journal
from trader.core.types import MarketType
from trader.observability import selection_persistence as sp
from trader.observability.registry_selector import (
    NOTHING_SELECTABLE, SELECTED, SNAPSHOT_STALE, select,
)
from trader.observability.selection_persistence import (
    SelectionPersistenceError, load_cursor, load_selection, record_fetch_outcome,
    record_selection,
)

ROOT = Path(__file__).resolve().parents[1]
AS_OF = 1_800_000_000_000
REC_AT = AS_OF + 5


def cid(sym):
    return InstrumentId("binance_usdm", MarketType.FUTURES, sym).value


def _rec(sym, **over):
    fields = dict(
        instrument_id=InstrumentId("binance_usdm", MarketType.FUTURES, sym),
        asset_class=AssetClass.UNKNOWN, contract_type="PERPETUAL", base_asset=sym[:-4],
        quote_asset="USDT", settlement_asset="USDT", contract_multiplier=None,
        delivery_ms=None, venue_listing=Presence.PRESENT, venue_status="TRADING",
        onboard_ms=None, constraints=OrderConstraints(), shortability=Capability.UNKNOWN,
        account_eligibility=Eligibility.UNKNOWN, account_state_ref="demo-account",
        symbol_config=Presence.UNKNOWN, leverage_bracket=Presence.UNKNOWN,
        data_availability=Presence.UNKNOWN, observed_at_ms=AS_OF, source="test",
        eligibility_basis=None)
    fields.update(over)
    return InstrumentRecord(**fields)


SNAP = RegistrySnapshot(as_of_ms=AS_OF, account_scope="demo-account",
                        account_trading=AccountTrading.UNKNOWN,
                        records=(_rec("BTCUSDT"), _rec("ETHUSDT"), _rec("SOLUSDT")),
                        source="test")


def _sel(cursor=None, cycle=AS_OF, snap=SNAP, scan=()):
    return select(snap, cycle_as_of_ms=cycle, max_snapshot_age_ms=60_000,
                  strategy_scan=scan, cursor_before=cursor)


def _record(j, sel, at=REC_AT):
    return record_selection(j, sel, selection_id=sel.selection_id, recorded_at_ms=at)


@pytest.fixture
def db(tmp_path):
    return tmp_path / "luffy.db"


@pytest.fixture
def j(db):
    return Journal(db)


def _rows(db, table):
    with sqlite3.connect(db) as c:
        return c.execute(f"SELECT * FROM {table}").fetchall()


class _FailOn:
    """Connection proxy that raises on the first statement containing `needle`."""

    def __init__(self, conn, needle):
        self.conn, self.needle, self.seen = conn, needle, []

    def execute(self, sql, params=()):
        if self.needle in sql:
            raise sqlite3.OperationalError("injected failure")
        self.seen.append(sql)
        return self.conn.execute(sql, params)

    @property
    def in_transaction(self):
        return self.conn.in_transaction

    def commit(self):
        self.conn.commit()

    def rollback(self):
        self.conn.rollback()


# -- atomic pair ---------------------------------------------------------------

def test_selection_row_and_cursor_commit_together(j, db):
    sel = _sel()
    assert sel.outcome == SELECTED
    assert _record(j, sel) == sp.INSERTED
    row = j.attention_selection(sel.selection_id)
    assert row["selected_id"] == cid("BTCUSDT") and row["cursor_after"] == cid("ETHUSDT")
    assert row["recorded_at_ms"] == REC_AT and row["canonical_json"] == sel.canonical_json()
    assert load_cursor(j) == cid("ETHUSDT")
    # visible to an independent connection: committed, not merely buffered
    assert len(_rows(db, "attention_selections")) == 1
    assert _rows(db, "attention_cursor") == [(sp.CURSOR_NAME, cid("ETHUSDT"))]


def test_failure_after_selection_insert_rolls_back_both(j, db, monkeypatch):
    _record(j, _sel())
    before = load_cursor(j)
    proxy = _FailOn(j._conn(), "INTO attention_cursor")
    monkeypatch.setattr(j, "_conn", lambda: proxy)
    nxt = _sel(cursor=before)
    with pytest.raises(sqlite3.OperationalError, match="injected"):
        _record(j, nxt)
    assert any("INSERT INTO attention_selections" in s for s in proxy.seen)
    monkeypatch.undo()
    assert j.attention_selection(nxt.selection_id) is None
    assert len(_rows(db, "attention_selections")) == 1
    assert load_cursor(j) == before


@pytest.mark.parametrize("outcome", [SELECTED, SNAPSHOT_STALE])
def test_sqlite_failure_during_cursor_update_rolls_back_both(j, db, outcome):
    first = _sel()
    _record(j, first)
    with sqlite3.connect(db) as c:
        c.execute("CREATE TRIGGER boom BEFORE INSERT ON attention_cursor "
                  "BEGIN SELECT RAISE(ABORT, 'injected cursor failure'); END")
    cycle = AS_OF + 1 if outcome == SELECTED else AS_OF + 120_000
    nxt = _sel(cursor=first.cursor_after, cycle=cycle)   # both rewrite the cursor row
    assert nxt.outcome == outcome
    with pytest.raises(sqlite3.IntegrityError, match="injected cursor failure"):
        _record(j, nxt)
    assert j.attention_selection(nxt.selection_id) is None
    assert load_cursor(j) == first.cursor_after


def test_failure_on_cursor_delete_path_rolls_back_selection(j, db, monkeypatch):
    # a None cursor_after reaches the DELETE only when no cursor exists (CAS)
    stale = _sel(cursor=None, cycle=AS_OF + 120_000)
    assert stale.outcome == SNAPSHOT_STALE and stale.cursor_after is None
    proxy = _FailOn(j._conn(), "DELETE FROM attention_cursor")
    monkeypatch.setattr(j, "_conn", lambda: proxy)
    with pytest.raises(sqlite3.OperationalError, match="injected"):
        _record(j, stale)
    assert any("INSERT INTO attention_selections" in s for s in proxy.seen)
    monkeypatch.undo()
    assert j.attention_selection(stale.selection_id) is None
    assert load_cursor(j) is None and not _rows(db, "attention_selections")


def test_cursor_survives_close_and_reopen(j, db):
    sel = _sel()
    _record(j, sel)
    j._conn().close()
    reopened = Journal(db)
    assert load_cursor(reopened) == sel.cursor_after
    assert load_selection(reopened, sel.selection_id) == sel


# -- cursor semantics ------------------------------------------------------------

def test_absent_cursor_is_none(j):
    assert load_cursor(j) is None


@pytest.mark.parametrize("stored", ["", "BTCUSDT", "binance_usdm:futures", "garbage:x:y",
                                    "binance_usdm:futures:BTC:USDT", " " + "x"])
def test_malformed_stored_cursor_fails_closed(j, db, stored):
    with sqlite3.connect(db) as c:
        c.execute("INSERT INTO attention_cursor(name,value) VALUES (?,?)",
                  (sp.CURSOR_NAME, stored))
    with pytest.raises(SelectionPersistenceError) as e:
        load_cursor(j)
    assert e.value.reason == sp.CORRUPT_CURSOR


def test_selected_outcome_advances_cursor(j):
    s1 = _sel()
    _record(j, s1)
    s2 = _sel(cursor=load_cursor(j), cycle=AS_OF + 1)
    _record(j, s2)
    assert (s1.selected_id, s2.selected_id) == (cid("BTCUSDT"), cid("ETHUSDT"))
    assert load_cursor(j) == cid("SOLUSDT")


def test_stale_outcome_persists_unchanged_cursor(j):
    _record(j, _sel())
    cur = load_cursor(j)
    stale = _sel(cursor=cur, cycle=AS_OF + 120_000)
    assert stale.outcome == SNAPSHOT_STALE and stale.cursor_after == cur
    assert _record(j, stale) == sp.INSERTED
    row = j.attention_selection(stale.selection_id)
    assert row["outcome"] == SNAPSHOT_STALE and row["selected_id"] is None
    assert row["cursor_before"] == row["cursor_after"] == cur
    assert load_cursor(j) == cur


def test_stale_outcome_with_no_cursor_keeps_it_absent(j):
    stale = _sel(cycle=AS_OF + 120_000)
    _record(j, stale)
    assert load_cursor(j) is None
    assert j.attention_selection(stale.selection_id)["outcome"] == SNAPSHOT_STALE


def test_nothing_selectable_persists_unchanged_cursor(j):
    _record(j, _sel())
    cur = load_cursor(j)
    everything = ("BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT")
    none = _sel(cursor=cur, cycle=AS_OF + 1, scan=everything)
    assert none.outcome == NOTHING_SELECTABLE and none.cursor_after == cur
    _record(j, none)
    assert j.attention_selection(none.selection_id)["outcome"] == NOTHING_SELECTABLE
    assert load_cursor(j) == cur


# -- cursor compare-and-swap -------------------------------------------------------

def _set_cursor(db, value):
    with sqlite3.connect(db) as c:
        c.execute("INSERT OR REPLACE INTO attention_cursor(name,value) VALUES (?,?)",
                  (sp.CURSOR_NAME, value))


def _assert_refused_cursor_conflict(j, db, sel, cursor, n_rows):
    with pytest.raises(SelectionPersistenceError) as e:
        _record(j, sel)
    assert e.value.reason == sp.CURSOR_CONFLICT
    assert j.attention_selection(sel.selection_id) is None
    assert len(_rows(db, "attention_selections")) == n_rows
    assert load_cursor(j) == cursor


def test_matching_cursor_before_commits(j):
    _set_cursor(j.db_path, cid("ETHUSDT"))
    sel = _sel(cursor=cid("ETHUSDT"))
    assert _record(j, sel) == sp.INSERTED
    assert load_cursor(j) == sel.cursor_after == cid("SOLUSDT")


@pytest.mark.parametrize("stored,before", [
    (cid("SOLUSDT"), cid("ETHUSDT")),     # cursor moved elsewhere
    (cid("ETHUSDT"), None),               # cursor exists, selection assumed none
    (None, cid("ETHUSDT")),               # cursor absent, selection assumed one
])
def test_differing_cursor_is_refused_without_writing(j, db, stored, before):
    if stored is not None:
        _set_cursor(db, stored)
    _assert_refused_cursor_conflict(j, db, _sel(cursor=before), stored, 0)


def test_stale_outcome_from_old_none_cursor_cannot_delete_advanced_cursor(j, db):
    stale = _sel(cursor=None, cycle=AS_OF + 120_000)     # built before any commit
    assert stale.outcome == SNAPSHOT_STALE and stale.cursor_after is None
    first = _sel()
    _record(j, first)                                      # cursor advances meanwhile
    _assert_refused_cursor_conflict(j, db, stale, first.cursor_after, 1)


def test_nothing_selectable_from_old_cursor_cannot_rewind(j, db):
    everything = ("BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT")
    s1 = _sel()
    _record(j, s1)
    late = _sel(cursor=s1.cursor_before, cycle=AS_OF + 1, scan=everything)
    assert late.outcome == NOTHING_SELECTABLE
    _assert_refused_cursor_conflict(j, db, late, s1.cursor_after, 1)


def test_two_journals_racing_from_same_cursor(db):
    a, b = Journal(db), Journal(db)
    _record(a, _sel())
    start = load_cursor(a)
    assert load_cursor(b) == start
    sel_a = _sel(cursor=start, cycle=AS_OF + 1)
    sel_b = _sel(cursor=start, cycle=AS_OF + 2)      # distinct selection, same old cursor
    assert sel_a.selection_id != sel_b.selection_id
    assert _record(a, sel_a) == sp.INSERTED
    _assert_refused_cursor_conflict(b, db, sel_b, sel_a.cursor_after, 2)
    assert load_cursor(a) == load_cursor(b) == sel_a.cursor_after


def test_exact_duplicate_is_idempotent_after_cursor_advanced_further(j, db):
    s1 = _sel()
    _record(j, s1)
    s2 = _sel(cursor=load_cursor(j), cycle=AS_OF + 1)
    _record(j, s2)
    s3 = _sel(cursor=load_cursor(j), cycle=AS_OF + 2)
    _record(j, s3)
    assert load_cursor(j) not in (s1.cursor_before, s1.cursor_after)
    assert _record(j, s1) == sp.DUPLICATE
    assert load_cursor(j) == s3.cursor_after
    assert len(_rows(db, "attention_selections")) == 3


def test_cursor_is_read_under_an_immediate_write_lock(j, db):
    """Another connection holding the write lock blocks the CAS read, so the
    compared cursor cannot change between read and write."""
    j._conn().execute("PRAGMA busy_timeout=0")
    holder = sqlite3.connect(db, isolation_level=None)
    holder.execute("BEGIN IMMEDIATE")
    try:
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            _record(j, _sel())
    finally:
        holder.execute("ROLLBACK")
        holder.close()
    assert load_cursor(j) is None and not _rows(db, "attention_selections")


# -- idempotency ---------------------------------------------------------------------

def test_exact_duplicate_is_idempotent_and_never_rewinds(j, db):
    s1 = _sel()
    _record(j, s1)
    s2 = _sel(cursor=load_cursor(j), cycle=AS_OF + 1)
    _record(j, s2)
    # a late replay of s1 (even at a later wall time) changes nothing
    assert _record(j, s1, at=REC_AT + 999) == sp.DUPLICATE
    assert load_cursor(j) == s2.cursor_after
    assert len(_rows(db, "attention_selections")) == 2
    assert j.attention_selection(s1.selection_id)["recorded_at_ms"] == REC_AT


def test_conflicting_duplicate_is_refused_and_cursor_unchanged(j, db):
    s1 = _sel()
    _record(j, s1)
    with sqlite3.connect(db) as c:   # same ID, different stored content
        c.execute("UPDATE attention_selections SET selected_symbol='XRP/USDT:USDT'")
        c.execute("UPDATE attention_cursor SET value=?", (cid("SOLUSDT"),))
    with pytest.raises(SelectionPersistenceError) as e:
        _record(j, s1)
    assert e.value.reason == sp.CONFLICTING_DUPLICATE
    assert load_cursor(j) == cid("SOLUSDT")
    assert j.attention_selection(s1.selection_id)["selected_symbol"] == "XRP/USDT:USDT"


def test_journal_primitive_reports_conflict_without_writing(j):
    s1 = _sel()
    _record(j, s1)
    other = _sel(cursor=s1.cursor_after, cycle=AS_OF + 1)
    row = sp._row(other, s1.selection_id, other.canonical_json())
    assert j.record_attention_selection(row, cursor_name=sp.CURSOR_NAME,
                                        recorded_at_ms=REC_AT) == "conflict"
    assert load_cursor(j) == s1.cursor_after


# -- canonical integrity -------------------------------------------------------------

def test_canonical_payload_rehashes_to_selection_id(j):
    sel = _sel()
    _record(j, sel)
    text = j.attention_selection(sel.selection_id)["canonical_json"]
    assert hashlib.sha256(text.encode()).hexdigest() == sel.selection_id
    assert load_selection(j, sel.selection_id) == sel
    assert load_selection(j, "0" * 64) is None


def test_supplied_selection_id_must_match_payload(j):
    sel = _sel()
    with pytest.raises(SelectionPersistenceError) as e:
        record_selection(j, sel, selection_id="0" * 64, recorded_at_ms=REC_AT)
    assert e.value.reason == sp.SELECTION_ID_MISMATCH
    assert load_cursor(j) is None and j.attention_selection(sel.selection_id) is None


@pytest.mark.parametrize("sql", [
    "UPDATE attention_selections SET canonical_json=replace(canonical_json,'selected','stale')",
    "UPDATE attention_selections SET canonical_json='{'",
    "UPDATE attention_selections SET cursor_after=NULL",
    "UPDATE attention_selections SET snapshot_as_of_ms=snapshot_as_of_ms+1",
])
def test_corrupted_payload_is_detected(j, db, sql):
    sel = _sel()
    _record(j, sel)
    with sqlite3.connect(db) as c:
        c.execute(sql)
    with pytest.raises(SelectionPersistenceError) as e:
        load_selection(j, sel.selection_id)
    assert e.value.reason == sp.CORRUPT_SELECTION


def test_tampered_payload_rehashed_under_its_new_id_still_refused(j, db):
    sel = _sel()
    _record(j, sel)
    d = json.loads(sel.canonical_json())
    d["cursor_after"] = d["cursor_before"]           # selected but cursor unmoved
    text = json.dumps(d, sort_keys=True, separators=(",", ":"))
    new_id = hashlib.sha256(text.encode()).hexdigest()
    with sqlite3.connect(db) as c:
        c.execute("UPDATE attention_selections SET selection_id=?, canonical_json=?, "
                  "cursor_after=NULL", (new_id, text))
    with pytest.raises(SelectionPersistenceError) as e:
        load_selection(j, new_id)
    assert e.value.reason == sp.CORRUPT_SELECTION


def test_inconsistent_selection_is_refused_before_writing(j):
    import dataclasses
    sel = _sel()
    bad = dataclasses.replace(sel, cursor_after=sel.cursor_before)
    with pytest.raises(SelectionPersistenceError) as e:
        _record(j, bad)
    assert e.value.reason == sp.INVALID_SELECTION
    bad = dataclasses.replace(sel, cursor_after="BTCUSDT")
    with pytest.raises(SelectionPersistenceError):
        _record(j, bad)
    assert load_cursor(j) is None and not j.query("SELECT * FROM attention_selections")


# -- fetch outcome ----------------------------------------------------------------------

def test_fetch_outcome_persists_separately_and_leaves_cursor(j):
    sel = _sel()
    _record(j, sel)
    rid = record_fetch_outcome(j, sel.selection_id, status="error", reason="fetch_timeout",
                               started_ms=AS_OF + 10, ended_ms=AS_OF + 2010,
                               recorded_at_ms=AS_OF + 2011)
    record_fetch_outcome(j, sel.selection_id, status="missing", reason="no_rows",
                         started_ms=None, ended_ms=None, recorded_at_ms=AS_OF + 3000)
    rows = j.attention_fetch_outcomes(sel.selection_id)
    assert [r["id"] for r in rows][0] == rid
    assert [(r["status"], r["reason"], r["started_ms"], r["ended_ms"]) for r in rows] == [
        ("error", "fetch_timeout", AS_OF + 10, AS_OF + 2010), ("missing", "no_rows", None, None)]
    assert load_cursor(j) == sel.cursor_after
    assert load_selection(j, sel.selection_id) == sel


def test_fetch_outcome_for_unknown_selection_is_refused(j):
    _record(j, _sel())
    cur = load_cursor(j)
    with pytest.raises(SelectionPersistenceError) as e:
        record_fetch_outcome(j, "a" * 64, status="usable", reason="ok", started_ms=1,
                             ended_ms=2, recorded_at_ms=3)
    assert e.value.reason == sp.UNKNOWN_SELECTION
    assert load_cursor(j) == cur and not j.query("SELECT * FROM attention_fetch_outcomes")


@pytest.mark.parametrize("kw", [dict(status="Bad Status"), dict(reason=""),
                                dict(started_ms=-1), dict(started_ms=5, ended_ms=4),
                                dict(recorded_at_ms=True)])
def test_invalid_fetch_outcome_is_refused(j, kw):
    sel = _sel()
    _record(j, sel)
    args = dict(status="usable", reason="ok", started_ms=1, ended_ms=2, recorded_at_ms=3)
    args.update(kw)
    with pytest.raises(SelectionPersistenceError) as e:
        record_fetch_outcome(j, sel.selection_id, **args)
    assert e.value.reason == sp.INVALID_FETCH_OUTCOME
    assert load_cursor(j) == sel.cursor_after


def test_fetch_outcome_failure_leaves_committed_cursor_intact(j, db):
    sel = _sel()
    _record(j, sel)
    with sqlite3.connect(db) as c:
        c.execute("CREATE TRIGGER boom BEFORE INSERT ON attention_fetch_outcomes "
                  "BEGIN SELECT RAISE(ABORT, 'injected fetch-record failure'); END")
    with pytest.raises(sqlite3.IntegrityError):
        j.record_attention_fetch_outcome(sel.selection_id, "error", "fetch_failed",
                                         None, None, REC_AT)
    assert load_cursor(j) == sel.cursor_after
    assert load_selection(j, sel.selection_id) == sel
    assert not j.attention_fetch_outcomes(sel.selection_id)


# -- boundaries ----------------------------------------------------------------------------

def _imports(path):
    tree = ast.parse(path.read_text())
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            out.add(("." * node.level) + (node.module or ""))
    return out


FORBIDDEN_NET = ("socket", "http", "urllib", "requests", "aiohttp", "ccxt", "websocket",
                 "subprocess")
FORBIDDEN_TRADER = ("trader.kernel", "trader.engine", "trader.data",
                    "trader.observability.store", "trader.observability.collector",
                    "trader.observability.attention", "trader.observability.supplemental",
                    "trader.observability.worker")


def test_adapter_has_no_network_store_collector_or_kernel_imports():
    mods = _imports(ROOT / "trader/observability/selection_persistence.py")
    assert not {m for m in mods if m.split(".")[0] in FORBIDDEN_NET}
    assert not {m for m in mods if m.startswith(FORBIDDEN_TRADER)}
    out = subprocess.run(
        [sys.executable, "-c", "import sys, trader.observability.selection_persistence;"
         "print('\\n'.join(sys.modules))"],
        cwd=ROOT, capture_output=True, text=True, check=True).stdout.split()
    assert not [m for m in out if m.split(".")[0] in FORBIDDEN_NET[:-1]
                or m.startswith(FORBIDDEN_TRADER)]


def test_journal_does_not_import_observability():
    assert not {m for m in _imports(ROOT / "trader/core/journal.py")
                if "observability" in m or "registry_selector" in m}


def test_no_kernel_wiring():
    """Nothing in the package imports the adapter yet; Kernel stays unwired."""
    for path in (ROOT / "trader").rglob("*.py"):
        assert not {m for m in _imports(path) if m.endswith("selection_persistence")}, path
    kernel = (ROOT / "trader/kernel.py").read_text()
    assert "selection_persistence" not in kernel and "registry_selector" not in kernel


@pytest.mark.parametrize("value,ok", [
    (cid("BTCUSDT"), True), (InstrumentId("x", MarketType.SPOT, "Y").value, True),
    ("BTCUSDT", False), ("binance_usdm:futures", False), ("garbage:x:y", False),
    ("binance_usdm:futures:BTC:USDT", False), (":futures:BTCUSDT", False),
    (None, False), (123, False),
])
def test_public_canonical_instrument_id_validator(value, ok):
    from trader.core.instrument_registry import is_canonical_instrument_id
    assert is_canonical_instrument_id(value) is ok


def test_no_private_validator_imported_across_modules():
    for path in (ROOT / "trader").rglob("*.py"):
        assert "_is_canonical_id" not in path.read_text(), path
