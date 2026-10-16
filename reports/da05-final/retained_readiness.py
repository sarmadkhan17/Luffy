"""Read retained packets only; report gaps without inventing operational proof."""
from collections import Counter
import gzip
import json
from pathlib import Path
from types import SimpleNamespace as NS

from trader import attention_admission as A
from trader.data import market_provenance as P
from trader.engine.orchestrator import symbol_allows
from trader.learning.capture_runtime import restore_frame
from trader.learning.foundation import digest
from trader.observability.attention import evaluate_snapshot
from trader.strategy.compile import compile_spec
from trader.strategy.spec import StrategySpec


def verify():
    event = json.loads(gzip.decompress(Path('tests/fixtures/da01-event2338.json.gz').read_bytes()))
    obs = event['observation']
    replay = evaluate_snapshot(obs['attention_event'])
    assert {r['symbol']:r for r in replay['rows']} == obs['salience_rows']
    assert A.canonical(A.verify_receipt(event)) == A.canonical(event)
    sp = StrategySpec.from_dict(obs['strategy_specs'][0])
    compiled = compile_spec(sp)
    source = json.loads(gzip.decompress(Path('reports/da10-full/historical-sources.json.gz').read_bytes()))
    assert all(digest(raw)==sha for sha,raw in source['blobs'].items())
    rows = []
    for entry in source['entries']:
        data = source['blobs'][entry['data_sha']]
        raw = source['blobs'][data['chunks']['dfs']['4h']]
        frame = restore_frame(raw)
        safe, status = P.current_frame_status(frame,'4h',data['cutoff_ms'],1800)
        allowed = symbol_allows(NS(symbols=sp.universe['include']),data['symbol'])
        lo, sh, detail = compiled.entries_detail({'4h':safe},symbol=data['symbol'])
        rows.append(dict(symbol=data['symbol'],source_cut_ms=data['cutoff_ms'],
            source_blob_sha256=digest(raw),bars=len(frame),current_frame_status=status,
            declared_include_member=allowed,
            enough_bars_for_donchian_100=len(safe)>=101,
            last_long=bool(lo[-1]),last_short=bool(sh[-1]),
            missing_declared_dependency=detail['missing'],
            interpretation='Insufficient lookback is a false/nonfinite predicate, not proof of an evaluated opportunity.' if len(safe)<101 else 'Complete numerical window; off-strategy eligibility is still binding.'))
    return dict(status='OPERATIONAL_VERIFICATION_REQUIRED',
        event_2338=dict(cut_ms=obs['source_cut_ms'],members=len(obs['rows']),
            status_counts=dict(Counter(r['status'] for r in replay['rows'])),
            rows_exact=True,receipt_exact=True,receipt_id=event['receipt_id']),
        da06_retained_scan=dict(scan_id=source['attention']['scan_id'],hash_verified_source_blobs=len(source['blobs']),
            source=source['source'],snapshots=rows,
            sufficient_eligible_snapshots=sum(r['declared_include_member'] and r['enough_bars_for_donchian_100'] for r in rows)),
        limitations=['Two historical packets at different cuts; neither proves current full-market coverage.',
            'The retained strategy definition is used only as a numerical/input and declared-include probe; no lifecycle or admission decision is replayed.',
            'No missing bars, availability clocks, funding or other dependencies are fabricated.',
            'No strategy output is required merely because input data is complete.'])


if __name__ == '__main__':
    import sys
    result=verify()
    Path(sys.argv[1]).write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k!='da06_retained_scan'},indent=2))
