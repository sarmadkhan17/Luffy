"""Cheap crypto observation: one public bulk ticker call and local cached bars.

No order books, symbol candle acquisition, strategy evaluation or account
permission inference. Venue metadata's underlyingType=COIN proves crypto.
"""
import math
import time
from collections import Counter
from trader.core.types import norm_symbol, MarketType
from trader.core.instrument_registry import InstrumentId
from trader.attention_admission import digest, PEER_VERSION
from trader.cognition.contracts import TF_MS
from trader.observability.attention import capture, evaluate_snapshot, settings
from trader.strategy.scan_plan import plan_scan

ANCHORS = ('BTC/USDT','ETH/USDT')
SCHEMA = 'broad-crypto.v2'


def _instrument_id(value):
    if not isinstance(value,str) or not value or any(c.isspace() for c in value):
        return None
    try:
        return InstrumentId('binance_usdm',MarketType.FUTURES,value).value
    except ValueError:
        return None


def _info(record):
    return record.get('info') if isinstance(record,dict) and isinstance(record.get('info'),dict) else {}


def _identity_matches(tickers, markets):
    """Exact retained native IDs; duplicate aliases never select a winner."""
    if (not isinstance(tickers,dict) or not isinstance(markets,dict)
            or any(not isinstance(k,str) or not k or any(c.isspace() for c in k)
                   for k in (*tickers,*markets))):
        raise ValueError('malformed_crypto_identity')
    by_id = {}
    for key,m in sorted(markets.items()):
        info = _info(m)
        native = m.get('id') if isinstance(m,dict) else None
        iid, raw_iid = _instrument_id(native), _instrument_id(info.get('symbol'))
        reason = None
        if iid is None or raw_iid is None: reason = 'malformed_crypto_identity'
        elif iid != raw_iid or m.get('symbol',key) != key: reason = 'conflicting_crypto_identity'
        # Index both declared IDs of inconsistent metadata so neither can
        # borrow a different valid alias or masquerade as missing metadata.
        for identity in {iid,raw_iid} - {None}:
            by_id.setdefault(identity,[]).append((key,m,reason))
    counts = Counter(_instrument_id(_info(t).get('symbol')) for t in tickers.values())
    for key,t in sorted(tickers.items()):
        iid = _instrument_id(_info(t).get('symbol'))
        matches = by_id.get(iid,[]) if iid is not None else []
        m, reason = {}, None
        if iid is None: reason = 'malformed_crypto_identity'
        elif counts[iid] > 1 or len(matches) > 1: reason = 'ambiguous_crypto_identity'
        elif not matches:
            reason = 'malformed_crypto_identity' if key in markets else 'venue_metadata_missing'
        else:
            market_key,m,reason = matches[0]  # exactly one candidate, never first-wins
            if reason is None and (key != market_key or t.get('symbol',key) != key):
                reason = 'conflicting_crypto_identity'
        yield key,t,m,iid,reason


def _venue_exclusion(m):
    info = _info(m)
    if info.get('underlyingType') != 'COIN': return 'non_crypto_or_unknown_classification'
    if not m.get('active',False) or info.get('status') != 'TRADING': return 'venue_not_trading'
    if m.get('quote') != 'USDT' or m.get('spot') or info.get('contractType') != 'PERPETUAL':
        return 'unsupported_instrument'


def _ticker_clock_exclusion(event, cut, stale_ms):
    if event is not None and (type(event) not in (int,float) or not math.isfinite(event)
                             or event > cut or event < cut-stale_ms):
        return 'stale_or_invalid_ticker_clock'


def verify_identity_projection(observation):
    """Qualify v2 rows against their frozen sources, without replaying policy."""
    try:
        sources, cut = observation['source_receipts'], observation['source_cut_ms']
        tickers, markets = sources['tickers'], sources['venue_metadata']
        if observation['source_identity'] != digest([SCHEMA,cut,tickers,markets]):
            raise ValueError('source_identity_mismatch')
        stale_ms = observation['attention_event']['capture_settings']['stale_seconds']*1000
        matched = {}
        for key,t,m,iid,reason in _identity_matches(tickers,markets):
            if reason is None and _venue_exclusion(m) is None:
                matched[iid] = (norm_symbol(key),t,_info(m))
        for sym,row in observation['rows'].items():
            label,t,info = matched[row['instrument_id']]
            if (label != sym or row['ticker'] != t or row['metadata'] != info
                    or row['event_time_ms'] != t.get('timestamp') or row['available_at_ms'] != cut
                    or _ticker_clock_exclusion(t.get('timestamp'),cut,stale_ms)):
                raise ValueError('source_identity_mismatch')
    except (KeyError,TypeError,ValueError) as exc:
        raise ValueError('admission_crypto_identity_invalid') from exc


def project(tickers, markets, *, cut, blacklist, stale_ms, capability_exclusions=(), source_exclusions=None):
    """Pure point-in-time eligible cohort projection, with explicit refusals."""
    rows, refusals = {}, {}
    for key,t,m,iid,reason in _identity_matches(tickers,markets):
        info = _info(m)
        if reason is None: reason = _venue_exclusion(m)
        # Only the exact matched contract was judged above. Normalization is
        # for existing policy masks and consumer labels, never for matching.
        sym = norm_symbol(key)
        if sym in blacklist: reason = 'deterministic_blacklist'
        elif sym in capability_exclusions: reason = 'account_symbol_ineligible'
        event = t.get('timestamp') if isinstance(t,dict) else None
        if reason is None: reason = _ticker_clock_exclusion(event,cut,stale_ms)
        def number(name,positive=False):
            v=t.get(name) if isinstance(t,dict) else None
            return v if type(v) in (int,float) and math.isfinite(v) and (v>0 if positive else True) else None
        last, volume = number('last',True),number('quoteVolume')
        if reason is None and (last is None or volume is None or volume < 0): reason = 'invalid_ticker'
        if reason:
            refusals.setdefault(sym,set()).add(reason)
            if source_exclusions is not None:
                source_exclusions[key] = dict(instrument_id=iid,reason=reason)
            continue
        if sym in rows: raise ValueError('duplicate_crypto_identity')
        missing = [k for k in ('percentage','change','bid','ask','baseVolume') if number(k) is None]
        rows[sym] = dict(symbol=sym,asset_class='CRYPTO',quality='VALID',available_at_ms=cut,
            event_time_ms=event,last=last,quote_volume=volume,return_24h_pct=number('percentage'),
            change_24h=number('change'),bid=number('bid',True),ask=number('ask',True),
            base_volume=number('baseVolume'),missing_reasons=missing + ([] if event is not None else ['provider_event_time_unknown']),
            instrument_id=iid,metadata=info,ticker=t,
            listing_onboard_ms=info.get('onboardDate'))
    # A rejected sibling is not an exclusion of the accepted perpetual.
    excluded = {sym:next(iter(reasons)) if len(reasons)==1 else 'multiple_instrument_refusals'
                for sym,reasons in sorted(refusals.items()) if sym not in rows}
    return rows,excluded


def observe(kernel, exposure):
    """Freeze a broad cohort independently of old top-N and deep members."""
    ex = kernel.universe.ex
    cfg = kernel.cfg
    started = int(time.time()*1000)
    source_exclusions = {}
    try:
        tickers = ex.fetch_tickers()
        cut = int(time.time()*1000)
        if cut < started:
            raise ValueError('bulk_acquisition_clock_reversed')
        if not isinstance(tickers,dict) or not tickers: raise ValueError('empty_ticker_response')
        # Read the exact boot acquisition through DATA's durable shared reader.
        # Mutable ccxt markets have no historical observation clock. An older
        # boot receipt cannot qualify this process's newer configuration/specs.
        from trader.data import market_provenance as mp
        key = getattr(kernel,'_venue_metadata_key',None)
        if key is None: raise ValueError('venue_metadata_receipt_unavailable')
        metadata_source = mp.venue_source(kernel.exchange)
        if metadata_source is None: raise ValueError('venue_metadata_source_unavailable')
        metadata = mp.load(kernel.journal._conn(),key,as_of_ms=cut,limit=1,
                           instrument_id='ref:venue_metadata',source=metadata_source)
        metadata = mp.eligible_frame(metadata,None,cut)
        if (metadata is None or metadata.empty or metadata['quality'].iloc[0] != 'VALID'
                or metadata['revision_id'].iloc[0] != getattr(kernel,'_venue_metadata_revision',None)):
            raise ValueError('venue_metadata_receipt_unavailable')
        metadata_receipt = mp.receipt_metadata(metadata.iloc[0])
        if metadata_receipt['kind'] != 'reference' or metadata_receipt['transform_version'] != 'venue_metadata.v1':
            raise ValueError('venue_metadata_contract_mismatch')
        markets = __import__('json').loads(metadata_receipt['raw_json'])['metadata']
        if not markets: raise ValueError('venue_metadata_unavailable')
        account_excluded=[]
        from trader.engine.entry_authority import CAP_KEY
        for r in kernel.journal.query('SELECT key,value FROM state_kv WHERE key LIKE ?', (CAP_KEY+'%',)):
            cap=__import__('json').loads(r['value'])
            if (cap.get('valid_until_ms',-1)>=cut and cap.get('observed_at_ms',cut+1)<=cut
                    and cap.get('receipt_id')==digest({k:v for k,v in cap.items() if k!='receipt_id'})
                    and cap.get('record',{}).get('account_eligibility')=='INELIGIBLE'):
                account_excluded.append(r['key'][len(CAP_KEY):])
        rows,excluded = project(tickers,markets,cut=cut,blacklist=kernel.universe.blacklist,
            stale_ms=cfg['attention']['stale_seconds']*1000,capability_exclusions=account_excluded,
            source_exclusions=source_exclusions)
        status='VALID'
    except Exception as exc:
        cut=int(time.time()*1000);tickers={};markets={};rows={};excluded={};source_exclusions={};metadata_receipt=None;status='UNAVAILABLE:'+type(exc).__name__
    # Retain the old strategy-volume peer definition using cheap onboard
    # metadata instead of first-candle network probes (explicit v2 semantics).
    scan=cfg['universe']['auto_scan']
    base=[s for s,r in rows.items() if s not in cfg['universe']['majors'] and r['quote_volume']>=scan['min_volume_usdt'] and r['last']>=scan['min_price']
          and type(r['listing_onboard_ms']) is int and 0<=r['listing_onboard_ms']<=cut-scan['min_age_days']*86400000]
    base=sorted(base,key=lambda s:(rows[s]['quote_volume'],s),reverse=True)[:scan['top_n']]
    base=sorted(set(base) | (set(cfg['universe']['majors']) & set(rows)))
    specs=[sp for _row,sp in getattr(kernel,'_spec_rows',[])]
    volumes={s:r['quote_volume'] for s,r in rows.items()}
    broad_plan=plan_scan(specs,rows,volumes) if specs else None
    peer_plan=plan_scan(specs,base,volumes) if specs else None
    peers=sorted((set(peer_plan.symbols) if peer_plan else set(base)) & set(rows))
    relevance={s:sorted(sp.id for sp in specs if broad_plan.wants(sp.id,s)) for s in rows}
    required_by_strategy={s:sorted(sp.id for sp in specs if s in {norm_symbol(x) for x in (sp.universe or {}).get('required_evaluation',[])}
        and broad_plan.wants(sp.id,s)) for s in rows}
    required_symbols=sorted({norm_symbol(s) for sp in specs for s in (sp.universe or {}).get('required_evaluation',[])})
    if any(norm_symbol(s) not in broad_plan.by_strategy.get(sp.id,()) for sp in specs
           for s in (sp.universe or {}).get('required_evaluation',[]) if norm_symbol(s) in rows):
        raise ValueError('required_strategy_symbol_ineligible')
    # Closed-bar peer data remains independent of admission. Attention's
    # broad statistical cohort deliberately replaces first-16 subset semantics.
    frames={}
    for sym in rows:
        try:
            df=kernel.feed.cached_ohlcv(sym,cfg['attention']['timeframe'],limit=153,as_of_ms=cut)
            if df is not None:
                frames[sym]={cfg['attention']['timeframe']:df}
                if 'quality' in df and not df['quality'].eq('VALID').all():
                    rows[sym]['missing_reasons'].append('retained_history_quality_unusable')
            else:
                rows[sym]['missing_reasons'].append('no_retained_eligible_history_at_cut')
        except Exception:
            rows[sym]['missing_reasons'].append('cached_history_unavailable')
    capture_cfg=settings(cfg['attention'])
    capture_cfg['max_symbols']=max(1,len(rows))
    memberships={s:dict(source=SCHEMA,symbol=s,quality='VALID',available_at_ms=cut,observed_at_ms=cut) for s in rows}
    event=capture(frames,sorted(rows),'broad:'+digest([cut,rows]),capture_cfg,cut,membership_receipts=memberships)
    # Pure evaluator; worker availability cannot alter synchronous admission.
    event['capture_ms'] = None  # elapsed timing is not a deterministic admission input
    result=evaluate_snapshot(event)
    scores={r['symbol']:r for r in result['rows']}
    if status=='VALID' and any(not r.get('eligible') for r in scores.values()): status='DEGRADED_CACHED_HISTORY'
    if peer_plan: kernel._scan_timeframes=peer_plan.timeframes
    observation=dict(schema=SCHEMA,source_cut_ms=cut,source_identity=digest([SCHEMA,cut,tickers,markets]),
        rows=rows,excluded=excluded,source_exclusions=source_exclusions,peer_cohort=peers,peer_version=PEER_VERSION,
        strategy_relevance=relevance,required_by_strategy=required_by_strategy,required_symbols=required_symbols,
        attention_event=event,strategy_specs=[sp.to_dict() for sp in specs],
        context_anchors=list(ANCHORS),exposure_required=sorted(exposure),
        salience_rows=scores,salience_config=result['config'],salience_anchor_ms=result.get('anchor_close_ms',cut//TF_MS[cfg['attention']['timeframe']]*TF_MS[cfg['attention']['timeframe']]),
        market=result['market'],status=status,acquisition='bulk tickers + local closed-bar cache; no per-symbol network',
        source_receipts=dict(tickers=tickers,venue_metadata=markets,
                             venue_metadata_receipt=metadata_receipt,
                             request_started_ms=started, observed_at_ms=cut))
    from .attention_history import coverage
    observation['history_coverage'] = coverage(result, rows, result['config']['min_salience'])
    return observation,frames,memberships
