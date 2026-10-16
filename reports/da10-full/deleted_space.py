"""Reproduce pending deleted-file allocation using only a private 8 MiB fixture."""
from contextlib import closing
from pathlib import Path
import gc,json,os,sqlite3,sys,tempfile
from trader.learning.consumers import retained
P=Path(__file__).parent
with tempfile.TemporaryDirectory(prefix='da10-deleted-',dir='/mnt/luffy-recovery/recovery/sol1-da10-full-tmp-20261016') as tmp:
    path=Path(tmp)/'evidence.db'
    with closing(sqlite3.connect(path)) as db:
        db.execute('CREATE TABLE evidence(value BLOB)')
        for _ in range(128):db.execute('INSERT INTO evidence VALUES (?)',(b'x'*65536,))
        db.commit()
    gc.disable();readers=[]
    for _ in range(32):
        with retained(path) as reader:
            assert reader.query('select count(*) as n from evidence')[0]['n']==128
            readers.append(reader)
    path.unlink()
    unique={};handles=0
    for fd in Path('/proc/self/fd').iterdir():
        try:
            if str(fd.readlink()).startswith(str(path)):
                stat=os.fstat(int(fd.name));handles+=1
                unique[(stat.st_dev,stat.st_ino)]=stat.st_blocks*512
        except OSError:pass
    result=dict(label=sys.argv[1],retained_reader_objects=32,unlinked_file_handles=handles,
        pending_deleted_allocated_bytes=sum(unique.values()),unique_deleted_inodes=len(unique),
        scope='Own 8 MiB SQLite fixture on recovery volume; GC disabled and Reader references retained until measurement. This demonstrates a mechanism, not the historical zero-space cause.')
    readers.clear();reader=None;gc.enable();gc.collect()
(P/(sys.argv[1]+'-deleted-space.json')).write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
