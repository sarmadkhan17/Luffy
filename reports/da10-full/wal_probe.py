"""Bounded legitimate-reader WAL pin, release and checkpoint verification."""
from contextlib import closing
from pathlib import Path
import json,sqlite3,sys,tempfile
from trader.learning.consumers import retained
P=Path(__file__).parent
with tempfile.TemporaryDirectory(prefix='da10-wal-',dir='/mnt/luffy-recovery/recovery/sol1-da10-full-tmp-20261016') as tmp:
    path=Path(tmp)/'j.db'
    with closing(sqlite3.connect(path)) as writer:
        writer.execute('PRAGMA journal_mode=WAL');writer.execute('PRAGMA busy_timeout=25')
        writer.execute('create table evidence(value BLOB)');writer.execute('insert into evidence values (?)',(b'initial',));writer.commit()
        with retained(path) as reader:
            assert reader.query('select count(*) as n from evidence')[0]['n']==1
            for _ in range(16):
                writer.execute('insert into evidence values (?)',(b'x'*65536,));writer.commit()
            still=reader.query('select count(*) as n from evidence')[0]['n']
            size=Path(str(path)+'-wal').stat().st_size
            pinned=writer.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()
            assert pinned[0]==1 and still==1
        released=writer.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()
        after=Path(str(path)+'-wal').stat().st_size
        assert released[0]==0 and after==0
result=dict(label=sys.argv[1],snapshot_rows=still,new_rows=16,pinned_wal_bytes=size,pinned_checkpoint=pinned,released_checkpoint=released,released_wal_bytes=after,
            scope='Own 1 MiB fixture; default WAL auto-checkpoint and synchronous policy. Only this probe connection uses a 25ms busy wait to bound the intentional checkpoint refusal. Active coherent readers necessarily retain WAL history.')
(P/(sys.argv[1]+'-wal.json')).write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
