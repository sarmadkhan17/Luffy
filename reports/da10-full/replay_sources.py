"""Exact retained source roundtrip through baseline/new Learning SQLite writers."""
import gzip,hashlib,importlib.util,json,sqlite3,sys,tempfile
from contextlib import closing
from pathlib import Path
from trader.learning import capture,foundation as L
from trader.persistence.storage_cleanup import inventory

P=Path(__file__).parent
fixture=json.loads(gzip.decompress((P/'historical-sources.json.gz').read_bytes()))
spec=importlib.util.spec_from_file_location('trader.learning.da10_baseline_capture',P/'baseline-capture.py')
baseline=importlib.util.module_from_spec(spec);sys.modules[spec.name]=baseline;spec.loader.exec_module(baseline)
results={}
with tempfile.TemporaryDirectory(prefix='da10-source-replay-',dir='/mnt/luffy-recovery/recovery/sol1-da10-full-tmp-20261016') as tmp:
    for name,module in [('before',baseline),('after',capture)]:
        path=Path(tmp)/(name+'.db');bindings=[]
        with closing(sqlite3.connect(path)) as db:
            module.ensure(db)
            for sha,raw in sorted(fixture['blobs'].items()):
                assert L.digest(raw)==sha
                dep=module.snapshot(db,'data',raw,source_id=sha,version='prospective-source.v1',
                                    available_ms=fixture['attention']['as_of_ms'],producer='DA06 exact retained source')
                assert dep['sha256']==sha
                assert module.resolve(db,dep)==raw
                bindings.append(dep)
            db.commit();summary=inventory(db)
        # Restart and resolve the same identities, with no feed/latest lookup.
        with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as db:
            for dep in bindings:assert module.resolve(db,dep)==fixture['blobs'][dep['sha256']]
        with path.open('rb') as stream:file_sha=hashlib.file_digest(stream,'sha256').hexdigest()
        results[name]=dict(source_blobs=len(bindings),bindings_sha256=L.digest(bindings),
                           schema_sha256=summary['schema_sha256'],tables=summary['tables'],database_sha256=file_sha)
assert results['before']==results['after']
(P/'historical-source-parity.json').write_text(json.dumps(dict(exact=True,results=results,
    source='DA06 retained scan scan_78513e8e21a148388868f783ce7cce34; all 77 exact retained source blobs',
    scope='Source identities, bytes, immutable rows, rowids, schema and physical SQLite file plus fresh-reader roundtrip; not a new trade/outcome performance evaluation.'),indent=2))
print(json.dumps(results,indent=2))
