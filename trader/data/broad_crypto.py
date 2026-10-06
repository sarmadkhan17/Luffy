"""Cheap crypto observation: one public bulk ticker call and local cached bars.

No order books, symbol candle acquisition, strategy evaluation or account
permission inference. Venue metadata's underlyingType=COIN proves crypto.
"""
import math
import time
from trader.core.types import norm_symbol
from trader.attention_admission import digest, PEER_VERSION
from trader.cognition.contracts import TF_MS
from trader.observability.attention import capture, evaluate_snapshot, settings
from trader.strategy.scan_plan import plan_scan

ANCHORS = ('BTC/USDT','ETH/USDT')


def project(tickers, markets, *, cut, blacklist, stale_ms, capability_exclusions=()):
    """Pure point-in-time eligible cohort projection, with explicit refusals."""
    rows, excluded = {}, {}
    by_symbol = {norm_symbol(k):m for k,m in markets.items()}
    for key,t in sorted(tickers.items()):
        sym = norm_symbol(key)
        m = by_symbol.get(sym,{})
        info = m.get('info') or {}
        reason = None
        if sym in blacklist: reason = 'deterministic_blacklist'
        elif sym in capability_exclusions: reason = 'account_symbol_ineligible'
        elif info.get('underlyingType') != 'COIN': reason = 'non_crypto_or_unknown_classification'
        elif not m.get('active',False) or info.get('status') != 'TRADING': reason = 'venue_not_trading'
        elif m.get('quote') != 'USDT' or m.get('spot') or info.get('contractType') != 'PERPETUAL': reason = 'unsupported_instrument'
        event = t.get('timestamp')
        if reason is None and event is not None:
            if type(event) not in (int,float) or not math.isfinite(event) or event > cut or event < cut-stale_ms:
                reason = 'stale_or_invalid_ticker_clock'
        def number(name,positive=False):
            v=t.get(name)
            return v if type(v) in (int,float) and math.isfinite(v) and (v>0 if positive else True) else None
        last, volume = number('last',True),number('quoteVolume')
        if reason is None and (last is None or volume is None or volume < 0): reason = 'invalid_ticker'
        if reason:
            excluded[sym] = reason; continue
        if sym in rows: raise ValueError('duplicate_crypto_identity')
        missing = [k for k in ('percentage','change','bid','ask','baseVolume') if number(k) is None]
        rows[sym] = dict(symbol=sym,asset_class='CRYPTO',quality='VALID',available_at_ms=cut,
            event_time_ms=event,last=last,quote_volume=volume,return_24h_pct=number('percentage'),
            change_24h=number('change'),bid=number('bid',True),ask=number('ask',True),
            base_volume=number('baseVolume'),missing_reasons=missing + ([] if event is not None else ['provider_event_time_unknown']),
            instrument_id='binance_usdm:futures:'+info['symbol'],metadata=info,ticker=t,
            listing_onboard_ms=info.get('onboardDate'))
    return rows,excluded


def observe(kernel, exposure):
    """Freeze a broad cohort independently of old top-N and deep members."""
    ex = kernel.universe.ex
    cfg = kernel.cfg
    try:
        tickers = ex.fetch_tickers()
        cut = int(time.time()*1000)
        if not isinstance(tickers,dict) or not tickers: raise ValueError('empty_ticker_response')
        # Metadata is already loaded by boot/fetch_tickers; no per-symbol API.
        markets = getattr(kernel.exchange,'markets',{})
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
            stale_ms=cfg['attention']['stale_seconds']*1000,capability_exclusions=account_excluded)
        status='VALID'
    except Exception as exc:
        cut=int(time.time()*1000);tickers={};markets={};rows={};excluded={};status='UNAVAILABLE:'+type(exc).__name__
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
            if df is not None: frames[sym]={cfg['attention']['timeframe']:df}
        except Exception:
            rows[sym]['missing_reasons'].append('cached_history_unavailable')
    capture_cfg=settings(cfg['attention'])
    capture_cfg['max_symbols']=max(1,len(rows))
    memberships={s:dict(source='broad-crypto.v1',symbol=s,quality='VALID',available_at_ms=cut,observed_at_ms=cut) for s in rows}
    event=capture(frames,sorted(rows),'broad:'+digest([cut,rows]),capture_cfg,cut,membership_receipts=memberships)
    # Pure evaluator; worker availability cannot alter synchronous admission.
    event['capture_ms'] = None  # elapsed timing is not a deterministic admission input
    result=evaluate_snapshot(event)
    scores={r['symbol']:r for r in result['rows']}
    if status=='VALID' and any(not r.get('eligible') for r in scores.values()): status='DEGRADED_CACHED_HISTORY'
    if peer_plan: kernel._scan_timeframes=peer_plan.timeframes
    observation=dict(schema='broad-crypto.v1',source_cut_ms=cut,source_identity=digest([cut,tickers,markets]),
        rows=rows,excluded=excluded,peer_cohort=peers,peer_version=PEER_VERSION,
        strategy_relevance=relevance,required_by_strategy=required_by_strategy,required_symbols=required_symbols,
        attention_event=event,strategy_specs=[sp.to_dict() for sp in specs],
        context_anchors=list(ANCHORS),exposure_required=sorted(exposure),
        salience_rows=scores,salience_config=result['config'],salience_anchor_ms=result.get('anchor_close_ms',cut//TF_MS[cfg['attention']['timeframe']]*TF_MS[cfg['attention']['timeframe']]),
        market=result['market'],status=status,acquisition='bulk tickers + local closed-bar cache; no per-symbol network',
        source_receipts=dict(tickers=tickers,venue_metadata=markets))
    return observation,frames,memberships
