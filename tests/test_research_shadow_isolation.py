"""Research shadow isolation: the source journal is read-only, research
artifacts land only in the shadow store, the child has no credentials and
cannot import trading/authority or network modules."""
import ast
import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from tests.research_shadow_fixtures import (RESEARCH_TABLES, checkpoint, dump,
                                            file_sha, make_source,
                                            research_counts)
from trader.cognition import _research_shadow_child as child
from trader.cognition import research_shadow as rs
from trader.cognition import research_shadow_contract as sc
from trader.cognition import research_shadow_store as st
from trader.core.journal import Journal

ROOT = Path(__file__).resolve().parents[1]


def _stores(tmp_path):
    src = make_source(tmp_path / "luffy.db")
    shadow = tmp_path / "research_shadow.db"
    st.init_shadow(shadow)
    return src, shadow


def _invoke(src, shadow, key="k", ms=1000, **kw):
    kw.setdefault("max_sources", 50)
    kw.setdefault("wall_clock_deadline_s", 120)
    return rs.invoke(source_db=src, shadow_db=shadow, invocation_key=key,
                     recorded_at_ms=ms, **kw)


# ── source writes fail through the facade ────────────────────────────────
@pytest.mark.parametrize("sql", [
    "INSERT INTO brain_events(ts,kind,subject,detail) VALUES ('t','k','s','d')",
    "INSERT INTO src.brain_events(ts,kind,subject,detail) "
    "VALUES ('t','k','s','d')",
    "UPDATE src.brain_events SET detail='x'",
    "DELETE FROM src.brain_events",
    "UPDATE decisions SET executed=1",
    "DELETE FROM src.decisions",
    "CREATE TABLE src.x(a)",
    "DROP TABLE src.brain_events",
    "SELECT * FROM src.trades",
    "SELECT * FROM trades",
    "ATTACH DATABASE ':memory:' AS other",
    "DETACH DATABASE src",
    "PRAGMA src.journal_mode=DELETE",
])
def test_attempted_source_access_outside_the_views_fails(tmp_path, sql):
    src, shadow = _stores(tmp_path)
    before = dump(src)
    j = st.open_shadow(shadow, src, readonly=False)
    try:
        with pytest.raises(sqlite3.Error):
            j._conn().execute(sql).fetchall()
    finally:
        j.close()
    assert dump(src) == before


def test_journal_writer_to_a_source_table_fails(tmp_path):
    src, shadow = _stores(tmp_path)
    before = dump(src)
    j = st.open_shadow(shadow, src, readonly=False)
    try:
        with pytest.raises(sqlite3.Error):
            j.log_brain_event("strategy_health_observed", "s1", "{}")
        # dropping the temp view exposes the attached table itself, which
        # is still read-only and outside the authorizer's writable set
        j._conn().execute("DROP VIEW temp.brain_events")
        with pytest.raises(sqlite3.Error):
            j.log_brain_event("strategy_health_observed", "s1", "{}")
    finally:
        j.close()
    assert dump(src) == before


def test_source_is_attached_read_only(tmp_path):
    src, shadow = _stores(tmp_path)
    j = st.open_shadow(shadow, src, readonly=False)
    try:
        dbs = {r[1]: r[2] for r in j._conn().execute("PRAGMA database_list")}
        assert Path(dbs["src"]).resolve() == src.resolve()
        assert j._conn().execute(
            "SELECT count(*) FROM brain_events").fetchone()[0] == 0
        j.set_bound(st.MAX_EVENT_ID, st.MAX_EVENT_ID)
        assert len(j.strategy_health_rows()) == len(
            Journal(src).strategy_health_rows())
    finally:
        j.close()


def test_shadow_store_has_only_research_and_harness_tables(tmp_path):
    _src, shadow = _stores(tmp_path)
    c = sqlite3.connect(shadow)
    names = {r[0] for r in c.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%'")}
    ddl = {r[0]: r[1] for r in c.execute(
        "SELECT name, sql FROM sqlite_master WHERE sql IS NOT NULL")}
    c.close()
    assert names == set(RESEARCH_TABLES) | {
        "research_shadow_source_binding",
        "research_shadow_cursor", "research_shadow_starts",
        "research_shadow_snapshots", "research_shadow_completions",
        "research_shadow_invocations"}
    # the Journal's own research DDL, verbatim
    for name, sql in st.research_ddl():
        assert ddl[name] == sql


def test_view_bound_is_a_stable_snapshot_of_the_append_only_source(tmp_path):
    src, shadow = _stores(tmp_path)
    j = st.open_shadow(shadow, src, readonly=True)
    try:
        j.set_bound(3, 3)
        first = j.strategy_health_rows()
        Journal(src).log_brain_event("strategy_health_sweep", "late", "{}")
        assert j.strategy_health_rows() == first
        assert [r["id"] for r in first] == [1, 2, 3]
    finally:
        j.close()


# ── an invocation leaves the source byte-identical ──────────────────────
def test_invocation_does_not_mutate_the_source(tmp_path):
    src = make_source(tmp_path / "luffy.db")
    shadow = tmp_path / "research_shadow.db"
    logical, raw = dump(src), file_sha(src)
    res = _invoke(src, shadow)
    assert res["receipt"]["outcome"] == sc.OK
    assert dump(src) == logical
    assert file_sha(src) == raw
    # no research row, registration or receipt was written to the source
    assert all(n == 0 for n in research_counts(src).values())
    assert research_counts(shadow)["research_bank_objects"] > 0
    assert research_counts(shadow)["research_unreadable_bank_objects"] > 0
    c = sqlite3.connect(src)
    assert not [r for r in c.execute(
        "SELECT name FROM sqlite_master WHERE name LIKE 'research_shadow%'")]
    c.close()


def test_source_writer_commits_while_the_shadow_reads_it(tmp_path):
    src, shadow = _stores(tmp_path)
    live = Journal(src)
    live._conn().execute("PRAGMA journal_mode=WAL")
    j = st.open_shadow(shadow, src, readonly=False)
    try:
        j.set_bound(st.MAX_EVENT_ID, st.MAX_EVENT_ID)
        with j.unit() as u:
            u.execute("SELECT count(*) FROM brain_events").fetchone()
            cur = u.execute("SELECT id FROM brain_events")
            cur.fetchone()                      # an open read on the source
            live._conn().execute("PRAGMA busy_timeout=1000")
            live.log_brain_event("note", "concurrent", "committed")
        assert Journal(src).query(
            "SELECT detail FROM brain_events WHERE subject='concurrent'"
        ) == [{"detail": "committed"}]
    finally:
        j.close()


def test_source_writer_commits_during_a_running_invocation(tmp_path):
    src = make_source(tmp_path / "luffy.db")
    shadow = tmp_path / "research_shadow.db"
    out = {}
    t = threading.Thread(target=lambda: out.update(res=_invoke(
        src, shadow, wall_clock_deadline_s=8, _fault="hang_in_transaction")))
    t.start()
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if shadow.exists():
                c = sqlite3.connect(shadow.resolve().as_uri() + "?mode=ro",
                                    uri=True)
                try:
                    n = c.execute("SELECT count(*) FROM "
                                  "research_shadow_snapshots").fetchone()[0]
                except sqlite3.Error:
                    n = 0
                c.close()
                if n:
                    break
            time.sleep(.05)
        time.sleep(1.0)            # the child is inside its work transaction
        w = sqlite3.connect(src, timeout=2)
        w.execute("INSERT INTO brain_events(ts,kind,subject,detail) VALUES "
                  "('t','note','concurrent','x')")
        w.commit()
        w.close()
    finally:
        t.join()
    assert out["res"]["receipt"]["outcome"] == sc.TIMEOUT
    assert Journal(src).query("SELECT count(*) AS n FROM brain_events "
                              "WHERE subject='concurrent'") == [{"n": 1}]


# ── sanitized environment ────────────────────────────────────────────────
def test_child_environment_is_an_allow_list_built_from_constants(monkeypatch):
    for k in ("BINANCE_API_KEY", "BINANCE_SECRET", "ANTHROPIC_API_KEY",
              "OPENAI_API_KEY", "AWS_SECRET_ACCESS_KEY"):
        monkeypatch.setenv(k, "secret-value-123")
    monkeypatch.setenv("PATH", "/secret/bin")
    env = rs.child_env()
    assert tuple(env) == sc.CHILD_ENV_KEYS
    assert "secret" not in json.dumps(env)
    assert env["PYTHONPATH"] == str(ROOT)


def test_child_refuses_a_polluted_environment_naming_keys_only(tmp_path):
    env = dict(rs.child_env(), BINANCE_API_KEY="hunter2")
    p = subprocess.run([sys.executable, "-s", "-m", rs.CHILD_MODULE],
                       input=b"{}", capture_output=True, env=env,
                       cwd=str(ROOT), timeout=60)
    out = p.stdout.decode()
    assert p.returncode == 1
    assert "environment_not_sanitized:BINANCE_API_KEY" in out
    assert "hunter2" not in out + p.stderr.decode()


def test_invocation_with_secrets_in_the_parent_env_still_runs_clean(
        tmp_path, monkeypatch):
    monkeypatch.setenv("BINANCE_API_KEY", "hunter2")
    monkeypatch.setenv("BINANCE_DEMO", "true")
    src = make_source(tmp_path / "luffy.db")
    res = _invoke(src, tmp_path / "research_shadow.db")
    # the child refuses any non-allow-listed variable, so OK proves none
    # was inherited
    assert res["receipt"]["outcome"] == sc.OK
    assert "hunter2" not in json.dumps(res)


# ── import boundary ──────────────────────────────────────────────────────
@pytest.mark.parametrize("name", [
    "trader.kernel", "trader.engine.executor", "trader.engine.risk",
    "trader.engine.orchestrator", "trader.engine.exits",
    "trader.engine.supervisor", "trader.engine.booking", "trader.brain",
    "trader.brain.llm", "trader.data.feed", "trader.data.derivatives",
    "trader.dashboard.server", "ccxt", "requests", "socket", "ssl",
    "http.client", "urllib.request", "subprocess", "anthropic", "dotenv"])
def test_guard_forbids_trading_authority_and_network_modules(name):
    assert child.forbidden(name)


@pytest.mark.parametrize("name", [
    "trader.engine.protective", "trader.engine", "trader.cognition.research_run",
    "trader.core.journal", "sqlite3", "json", "urllib.parse"])
def test_guard_allows_the_research_closure(name):
    assert not child.forbidden(name)


def test_forbidden_import_is_blocked_inside_the_child(tmp_path):
    src = make_source(tmp_path / "luffy.db")
    res = _invoke(src, tmp_path / "research_shadow.db",
                  _fault="import_forbidden_module")
    r = res["receipt"]
    assert r["outcome"] == sc.FAILED
    assert r["outcome_reason"] == \
        "forbidden_import_blocked:trader.engine.executor"


def test_child_import_closure_has_no_authority_module():
    code = ("import sys\n"
            "from trader.cognition import _research_shadow_child as c\n"
            "assert c.install_import_guard() == []\n"
            "c._runners()\n"
            "from trader.cognition import research_shadow_store, "
            "research_shadow_contract, research_families\n"
            "print('\\n'.join(sorted(sys.modules)))\n")
    p = subprocess.run([sys.executable, "-s", "-c", code],
                       capture_output=True, env=rs.child_env(),
                       cwd=str(ROOT), timeout=120)
    assert p.returncode == 0, p.stderr.decode()
    mods = p.stdout.decode().split()
    assert not [m for m in mods if child.forbidden(m)]
    assert [m for m in mods if m.startswith("trader.engine")] == [
        "trader.engine", "trader.engine.protective"]
    for bad in ("trader.kernel", "trader.brain.llm", "socket", "ssl",
                "subprocess", "ccxt"):
        assert bad not in mods


def _imports(path):
    tree = ast.parse(Path(path).read_text())
    out = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            out |= {a.name for a in n.names}
        elif isinstance(n, ast.ImportFrom):
            out.add(n.module + "." + n.names[0].name
                    if n.module == "trader.cognition" else n.module)
    return out


def test_harness_modules_import_no_trading_or_network_module():
    # modules that run inside the child: every import is child-allowed
    for f in ("_research_shadow_child.py", "research_shadow_store.py",
              "research_shadow_contract.py", "research_families.py"):
        for m in _imports(ROOT / "trader/cognition" / f):
            assert not child.forbidden(m), (f, m)
    # the parent launcher and the owner report run outside the child; they
    # still import no authority, Attention, LLM or network module
    authority = ("trader.kernel", "trader.engine", "trader.brain",
                 "trader.data", "trader.dashboard", "trader.api",
                 "trader.observability", "trader.cognition.attention",
                 "ccxt", "requests", "httpx", "socket", "ssl", "http",
                 "urllib.request", "anthropic", "openai", "dotenv")
    for f in ("research_shadow.py", "research_shadow_report.py"):
        for m in _imports(ROOT / "trader/cognition" / f):
            assert not any(m == a or m.startswith(a + ".")
                           for a in authority), (f, m)


def test_no_kernel_scheduling_or_autostart_references_the_harness():
    for p in [ROOT / "trader/kernel.py", ROOT / "restart.sh",
              ROOT / "scripts/watchdog.sh", ROOT / "config.yaml"]:
        if p.exists():
            assert "research_shadow" not in p.read_text(errors="ignore")
    hits = subprocess.run(["git", "ls-files"], capture_output=True,
                          cwd=str(ROOT), text=True).stdout.split()
    assert not [h for h in hits if "research_shadow" in h and (
        h.endswith((".service", ".timer", ".cron", ".sh", ".yaml")))]


# ── review findings: alias, Attention guard, import contract ────────────
@pytest.mark.parametrize("alias", ["hardlink", "symlink"])
def test_shadow_path_aliasing_the_source_is_refused_before_any_write(
        tmp_path, alias):
    src = make_source(tmp_path / "luffy.db")
    link = tmp_path / "shadow_alias.db"
    (os.link if alias == "hardlink" else os.symlink)(src, link)
    logical, raw = dump(src), file_sha(src)
    res = _invoke(src, link, _python="/nonexistent/python")
    assert res["status"] == rs.REFUSED_START
    assert res["reason"] == "source_is_shadow"
    assert dump(src) == logical and file_sha(src) == raw
    # the store's own guards refuse the alias too, without writing it
    with pytest.raises(st.ShadowStoreError, match="not_a_shadow_store"):
        st.init_shadow(link)
    with pytest.raises(st.ShadowStoreError, match="source_is_shadow"):
        st.open_shadow(link, src, readonly=True)
    assert dump(src) == logical and file_sha(src) == raw


def test_existing_non_shadow_database_is_never_initialized(tmp_path):
    other = tmp_path / "other.db"
    c = sqlite3.connect(other)
    c.execute("CREATE TABLE trades(a)")
    c.commit()
    c.close()
    before = dump(other)
    with pytest.raises(st.ShadowStoreError, match="not_a_shadow_store"):
        st.init_shadow(other)
    assert dump(other) == before


@pytest.mark.parametrize("name", child.AUTHORITY_MODULES)
def test_real_authority_imports_fail_after_the_guard(name):
    code = ("from trader.cognition import _research_shadow_child as c\n"
            "assert c.install_import_guard() == []\n"
            f"import {name}\n")
    p = subprocess.run([sys.executable, "-s", "-c", code],
                       capture_output=True, env=rs.child_env(),
                       cwd=str(ROOT), timeout=60)
    assert p.returncode != 0
    assert f"import of {name.split('.')[0]}" in p.stderr.decode() or \
        "is forbidden" in p.stderr.decode()


def test_attention_modules_are_forbidden_and_detected_if_preloaded():
    for name in ("trader.cognition.attention", "trader.observability",
                 "trader.observability.attention", "trader.cognition.memory",
                 "trader.cognition.research_bank_view", "trader.world"):
        assert child.forbidden(name)
    code = ("import trader.cognition.attention\n"
            "from trader.cognition import _research_shadow_child as c\n"
            "print(c.install_import_guard())\n")
    p = subprocess.run([sys.executable, "-s", "-c", code],
                       capture_output=True, env=rs.child_env(),
                       cwd=str(ROOT), timeout=60, text=True)
    assert "trader.cognition.attention" in p.stdout


def test_harness_modules_satisfy_the_cognition_import_contract():
    from tests.test_cognition_contracts import (ALLOWED_IMPORTS,
                                                HARNESS_IMPORTS)
    for f, extra in HARNESS_IMPORTS.items():
        for n in ast.walk(ast.parse((ROOT / "trader/cognition" / f)
                                    .read_text())):
            names = ([a.name for a in n.names] if isinstance(n, ast.Import)
                     else [n.module] if isinstance(n, ast.ImportFrom) else [])
            for m in names:
                assert m in ALLOWED_IMPORTS | extra, (f, m)


# ── review round 2: the initialization race ─────────────────────────────
def _swap_to_link(path, src):
    for suffix in ("", "-wal", "-shm", "-journal"):
        try:
            os.unlink(str(path) + suffix)
        except FileNotFoundError:
            pass
    os.link(src, path)


def _hook_connect(monkeypatch, match, before=None, after=None):
    """Wrap sqlite3.connect: run ``before``/``after`` around the first
    plain-path connect whose target satisfies ``match``."""
    real = sqlite3.connect
    fired = {}

    def connect(target, *a, **k):
        t = str(target)
        hit = not fired and not t.startswith("file:") and match(t)
        if hit:
            fired["at"] = t
            if before:
                before(t)
        conn = real(target, *a, **k)
        if hit and after:
            after(t)
        return conn
    monkeypatch.setattr(sqlite3, "connect", connect)
    return fired


def test_existing_shadow_replaced_before_the_writable_open_is_refused(
        tmp_path, monkeypatch):
    src = make_source(tmp_path / "luffy.db")
    shadow = tmp_path / "research_shadow.db"
    st.init_shadow(shadow)
    logical, raw = dump(src), file_sha(src)
    fired = _hook_connect(monkeypatch, lambda t: t == str(shadow),
                          before=lambda t: _swap_to_link(shadow, src))
    with pytest.raises(st.ShadowStoreError, match="not_a_shadow_store"):
        st.init_shadow(shadow)
    monkeypatch.undo()
    assert fired
    assert dump(src) == logical and file_sha(src) == raw


def test_replacement_after_the_writable_open_cannot_redirect_writes(
        tmp_path, monkeypatch):
    src = make_source(tmp_path / "luffy.db")
    shadow = tmp_path / "research_shadow.db"
    st.init_shadow(shadow)
    logical, raw = dump(src), file_sha(src)
    fired = _hook_connect(monkeypatch, lambda t: t == str(shadow),
                          after=lambda t: _swap_to_link(shadow, src))
    st.init_shadow(shadow)          # writes only the inode it opened
    monkeypatch.undo()
    assert fired
    assert dump(src) == logical and file_sha(src) == raw


def test_new_store_temp_file_swapped_for_the_source_is_refused(
        tmp_path, monkeypatch):
    src = make_source(tmp_path / "luffy.db")
    shadow = tmp_path / "research_shadow.db"
    logical, raw = dump(src), file_sha(src)
    fired = _hook_connect(monkeypatch, lambda t: ".init-" in t,
                          before=lambda t: _swap_to_link(Path(t), src))
    with pytest.raises(st.ShadowStoreError, match="not_a_shadow_store"):
        st.init_shadow(shadow)
    monkeypatch.undo()
    assert fired and not shadow.exists()
    assert dump(src) == logical and file_sha(src) == raw
    assert not list(tmp_path.glob(".research_shadow.db.init-*"))


def test_new_store_path_appearing_during_initialization_is_refused(
        tmp_path, monkeypatch):
    src = make_source(tmp_path / "luffy.db")
    shadow = tmp_path / "research_shadow.db"
    logical, raw = dump(src), file_sha(src)
    fired = _hook_connect(monkeypatch, lambda t: ".init-" in t,
                          before=lambda t: os.link(src, shadow))
    with pytest.raises(st.ShadowStoreError, match="shadow_path_raced"):
        st.init_shadow(shadow)
    monkeypatch.undo()
    assert fired
    assert dump(src) == logical and file_sha(src) == raw
    assert not list(tmp_path.glob(".research_shadow.db.init-*"))


def test_new_store_is_created_and_verified(tmp_path):
    shadow = tmp_path / "research_shadow.db"
    st.init_shadow(shadow)
    c = sqlite3.connect(shadow)
    assert c.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert st.SHADOW_MARKER in {r[0] for r in c.execute(
        "SELECT name FROM sqlite_master")}
    c.close()
    assert not list(tmp_path.glob(".research_shadow.db.init-*"))
