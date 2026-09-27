"""Synthetic isolated store (same shape as the 2026-09-27 baseline fixture).
SYNTHETIC; no production copy. Writes only under $PERF_ROOT."""
import json, os, sys, time
from datetime import datetime, timezone
from pathlib import Path
REPO = Path(__file__).resolve().parents[4]; sys.path.insert(0, str(REPO))
from trader.core.journal import Journal
ROOT = Path(os.environ['PERF_ROOT']); (ROOT / 'data').mkdir(parents=True, exist_ok=True)
(ROOT / 'logs').mkdir(exist_ok=True)
j = Journal(str(ROOT / 'data/luffy.db'))
now = datetime.now(timezone.utc).isoformat()
with j._tx() as c:
    if not c.execute('select count(*) from votes').fetchone()[0]:
        c.execute("insert into cycles(id,ts,symbol) values('fixture',?,'FIXTURE/USDT')", (now,))
        c.executemany('insert into votes(cycle_id,ts,symbol,agent,side,conviction,confidence,rationale) values(?,?,?,?,?,?,?,?)', (('fixture', now, 'FIXTURE/USDT', f'fixture-{i%8}', 'long', .5, .5, 'synthetic load fixture ' + ('x' * 256)) for i in range(100000)))
        c.executemany('insert into decisions(id,cycle_id,ts,symbol,action,score,threshold,confidence) values(?,?,?,?,?,?,?,?)', ((str(i), 'fixture', now, 'FIXTURE/USDT', 'HOLD', 0, .5, .5) for i in range(100000)))
        c.executemany('insert into trades(id,symbol,side,amount,entry_price,opened_at,status,closed_at,realized_pnl) values(?,?,?,?,?,?,?,?,?)', ((str(i), 'FIXTURE/USDT', 'long', 1, 100, '2026-09-26', 'closed', '2026-09-27', i % 3 - 1) for i in range(10000)))
    c.execute('delete from equity')
    c.execute('insert into equity values(?,1000,1000,0)', (now,))
c = j._conn(); c.execute('PRAGMA journal_mode=WAL'); c.commit()
(ROOT / 'data/heartbeat_luffy.json').write_text(json.dumps({'timestamp': time.time()}))
(ROOT / 'logs/luffy.log').write_bytes(b''.join(b'2026-09-27 synthetic log line %07d\n' % i for i in range(300000)))
print(json.dumps({'store_bytes': sum(p.stat().st_size for p in (ROOT / 'data').glob('luffy.db*')),
                  'log_bytes': (ROOT / 'logs/luffy.log').stat().st_size}))
