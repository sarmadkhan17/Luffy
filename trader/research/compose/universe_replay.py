"""Replay retained scanner selections; never approximate historical top 30.

Older membership-only history is useful evidence, but cannot establish ticker
or listing parity. No full selection at a cut means UNAVAILABLE, not all coins.
The caller supplies a read-only connection or a private snapshot connection.
"""
import json
import hashlib
import base64
import zlib
from ...data import market_provenance as mp
from ...data.feed import norm_symbol


def selection_at(db, environment, cut_ms):
    missing = dict(status='UNAVAILABLE', members=None, reason='no retained selection at this cut')
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not {'universe_revisions','universe_selection_evidence'} <= tables:
        return missing
    row = db.execute('SELECT r.revision_id,e.payload,e.sha256 FROM universe_revisions r '
                     'LEFT JOIN universe_selection_evidence e ON e.environment=r.environment '
                     'AND e.revision_id=r.revision_id WHERE r.environment=? AND r.observed_at_ms<=? '
                     'AND r.recorded_ms<=? ORDER BY r.observed_at_ms DESC LIMIT 1',
                     (environment,cut_ms,cut_ms)).fetchone()
    if row is None or row[1] is None:
        return dict(missing,reason='historical selection inputs were not retained')
    if hashlib.sha256(row[1].encode()).hexdigest() != row[2]:
        raise ValueError('universe_selection_corrupt')
    payload=row[1]
    if payload.startswith('zlib:'):
        decoder=zlib.decompressobj()
        raw=decoder.decompress(base64.b64decode(payload[5:],validate=True),8*1024*1024+1)
        if len(raw)>8*1024*1024 or decoder.unconsumed_tail or not decoder.eof:
            raise ValueError('universe_selection_expansion_bound')
        payload=raw
    r = json.loads(payload)
    if mp.digest({k:v for k,v in r.items() if k!='revision_id'}) != r['revision_id']:
        raise ValueError('universe_revision_identity_differs')
    cfg = r['selection_config']
    if r.get('selection_version')!='universe.volume-age.v2':
        return dict(missing,reason='historical selection contract lacks membership/expiry inputs')
    if not 0 <= cut_ms-r['available_at_ms'] <= cfg['rescan_hours']*3600000:
        return dict(missing,reason='retained selection expired at this cut')
    if r['observed_at_ms']>cut_ms or r['quality']!='VALID':
        return missing
    excluded = r['exclusions']
    eligible = []
    for symbol, receipt in r['volume_receipts'].items():
        if max(receipt['observed_at_ms'],receipt['available_at_ms'])>cut_ms:
            raise ValueError('universe_input_from_future')
        ticker = receipt['raw']
        if mp.digest(ticker)!=receipt['content_hash']:
            raise ValueError('universe_ticker_identity_differs')
        if symbol in cfg['majors'] or (symbol in excluded and excluded[symbol]!='below_top_n_rank'):
            continue
        if float(ticker['quoteVolume'])<cfg['min_vol'] or float(ticker['last'])<cfg['min_price']:
            raise ValueError('universe_eligible_input_differs')
        eligible.append((float(ticker['quoteVolume']),norm_symbol(symbol)))
    expected=[s for _,s in sorted(eligible,reverse=True)[:cfg['top_n']]]
    if expected!=r['members']:
        raise ValueError('universe_rank_differs')
    return dict(status='RECORDED_SCANNER_SELECTION', members=expected, revision_id=r['revision_id'],
                available_at_ms=r['available_at_ms'], expiry_ms=r['available_at_ms']+cfg['rescan_hours']*3600000,
                basis='retained exact quoteVolume rank and recorded venue eligibility; not candle volume x close',
                authority='MEMBERSHIP_OBSERVATION_ONLY')


def entry_masks(path, environment, frames, tf):
    """One historical mask at each closed signal cut, or an explicit gap.

    No current membership is applied to old candles. Incomplete history is
    a separate unfiltered retrospective experiment, never exact live parity.
    """
    from contextlib import closing
    import numpy as np
    from ...core.types import TF_MS
    from ...engine.protection_snapshot import ro_connect
    cuts={s:(mp.ms(df['ts'])+TF_MS[tf]).tolist() for s,df in frames.items()}
    selections={}
    with closing(ro_connect(path)) as db:
        db.execute('BEGIN')
        for at in sorted({t for ts in cuts.values() for t in ts}):
            selections[at]=selection_at(db,environment,at)
    unknown=[t for t,r in selections.items() if r['status']=='UNAVAILABLE']
    if unknown:
        return {},dict(status='UNAVAILABLE',known_cuts=len(selections)-len(unknown),total_cuts=len(selections),
                       first_missing_cut=unknown[0],reason='exact historical selection inputs missing or expired; '
                       'unfiltered retrospective experiment only; historical top30 parity not established')
    masks={s:np.array([s in selections[t]['members'] for t in ts],dtype=bool) for s,ts in cuts.items()}
    return masks,dict(status='EXACT_RECORDED_SELECTION',known_cuts=len(selections),total_cuts=len(selections),
                      identity=mp.digest(selections),environment=environment)
