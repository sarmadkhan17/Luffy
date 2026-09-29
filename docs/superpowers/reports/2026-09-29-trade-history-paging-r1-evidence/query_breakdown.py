import sys, time, statistics, tempfile
from pathlib import Path
sys.path.insert(0, ".")
sys.argv = ["x"]
import importlib.util
spec = importlib.util.spec_from_file_location("b", "scripts/bench_trade_history.py"); b = importlib.util.module_from_spec(spec); spec.loader.exec_module(b)
from trader.engine.protection_snapshot import ro_connect
from trader.dashboard import trade_history as th
with tempfile.TemporaryDirectory() as tmp:
    j = b.build(Path(tmp), 100_000)
    last = j.query("SELECT opened_at, id FROM trades ORDER BY opened_at DESC, id DESC LIMIT 1 OFFSET 99949")[0]
    conn = ro_connect(j.db_path)
    def t(sql, p):
        xs=[]
        for _ in range(40):
            s=time.perf_counter(); conn.execute(sql,p).fetchall(); xs.append((time.perf_counter()-s)*1000)
        return round(statistics.median(xs),3)
    cols=", ".join(th.COLUMNS)
    def m(st):
        """The history check at the deepest page: stream-hash the consumed range."""
        where = "" if st == "all" else f"WHERE status='{st}'"
        q = f"SELECT opened_at, id FROM trades {where} ORDER BY opened_at DESC, id DESC LIMIT 1 OFFSET ?"
        anchor = tuple(conn.execute(q, (0,)).fetchone())
        n = conn.execute(f"SELECT COUNT(*) FROM trades {where}").fetchone()[0]
        lo, hi = tuple(conn.execute(q, (n - 1,)).fetchone()), tuple(conn.execute(q, (n - 51,)).fetchone())
        filt, params = ("1", []) if st == "all" else ("status = ?", [st])
        xs = []
        for _ in range(10):
            s = time.perf_counter(); th._membership(conn, filt, params, anchor, lo, hi)
            xs.append((time.perf_counter() - s) * 1000)
        return round(statistics.median(xs), 3)
    for st in ("all","closed"):
        f = "1" if st=="all" else "status='closed'"
        print(st, "page_rows_deep", t(f"SELECT {cols} FROM trades WHERE {f} AND (opened_at,id)<(?,?) ORDER BY opened_at DESC, id DESC LIMIT 51",(last["opened_at"],last["id"])),
              "total", t(f"SELECT COUNT(*) FROM trades WHERE {f}",()),
              "preceding_deep", t(f"SELECT COUNT(*) FROM trades WHERE {f} AND (opened_at,id)>=(?,?)",(last["opened_at"],last["id"])),
              "membership_deep", m(st))

