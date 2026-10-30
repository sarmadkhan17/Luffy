"""Bounded off-scan consumer for Factory coordinated paper research.

Signals use cached point-in-time inputs. Prospective fills use public order
books; no private order or control client is used. Missing inputs defer.
"""
import time
import json
import numpy as np

from ..core.types import TF_MS, closed_bars
from ..data import market_provenance as mp
from ..research.compose import hedge as H
from ..research.closure import ClosureRefused
from ..research.closure.coordinated_trial import KIND, proposals
from ..strategy import factory_handoff as F, fill_timing, spec_evidence
from ..strategy.compile import compile_spec
from ..strategy.spec import StrategySpec
from .coordinated_paper import CoordinatedPaper,QUOTE_TIMING,QUOTE_PROTOCOL_ID

PAPER_STATES=(F.VALIDATED,F.SHADOW,F.APPROVAL_REQUIRED,F.APPROVED_FIRST_LIVE)


def admit(journal,cfg,*,at_ms):
    closure=(cfg.get('research') or {}).get('closure') or {}
    if not closure.get('enabled') or not closure.get('paper_intake'):
        return []
    F.ensure(journal)
    rows=journal.query('SELECT version_id FROM strategy_versions WHERE source_kind IN (?,?) ORDER BY recorded_at_ms DESC',(KIND,F.HEDGE_HQ_KIND))
    excluded=set();out=[]
    # Recover an interrupted exact install before looking for a new candidate.
    for row in rows:
        version=F.load_version(journal,row['version_id'])
        packet=version['lineage']['research']['multi_leg']['packet']
        excluded.add((packet.get('candidate') or packet['freeze']['candidate'])['candidate_id'])
        if F.state_of(journal,version['version_id']) in PAPER_STATES:
            out.append(F.record_coordinated_paper_install(journal,version['version_id'],at_ms=at_ms))
    limit=max(0,min(2,int(closure.get('max_coordinated_paper_books',2)))-len(out))
    if limit:
        for packet in proposals(closure['store_path'],limit=limit,exclude=excluded):
            version=F.create_version(journal,cfg,dict(kind=KIND,packet=packet),at_ms=at_ms)
            out.append(F.record_coordinated_paper_install(journal,version['version_id'],at_ms=at_ms))
    return out


def observation(feed,journal,version,stamp,*,decision_ms=None):
    """One exact closed signal cut; later revisions cannot alter this cut.

    Legacy next-open books refuse a late receipt. Prospective quote books
    bind inputs available at the actual later decision; fills follow it.
    """
    spec=StrategySpec.from_dict(version['spec']);compiled=compile_spec(spec)
    hs=version['lineage']['research']['multi_leg']['hedge_spec']
    step=TF_MS[spec.timeframe];cut=stamp+step
    available_by=cut if decision_ms is None else int(decision_ms)
    if available_by<cut:
        raise ClosureRefused('coordinated_paper_future_signal')
    need=fill_timing.history_requirements(spec)
    limit=max(need['base']+2,int(hs['ratio']['lookback'])+2)
    if need['exceeds_bound'] or limit>fill_timing.INFERENCE_HISTORY_MAX:
        raise ClosureRefused('coordinated_paper_history_bound')
    symbols={hs['primary']['symbol'],hs['hedge']['symbol']}
    # Cross-sectional factors must see the actual retained scanner universe,
    # not a pair presented as the whole market.
    requires=set(compiled.data_requires)
    if 'universe' in requires:
        from ..research.compose.universe_replay import selection_at
        from .protection_snapshot import ro_connect
        from contextlib import closing
        with closing(ro_connect(journal.db_path)) as db:
            selection=selection_at(db,'demo',cut)
        if selection['status']!='RECORDED_SCANNER_SELECTION':
            raise ClosureRefused('coordinated_paper_universe_unavailable')
        symbols.update(selection['members'])
    from ..research.compose.experiments import needs_btc
    packet=version['lineage']['research']['multi_leg']['packet']
    wants_btc=needs_btc(packet.get('candidate') or packet['freeze']['candidate'])
    if wants_btc:
        symbols.add('BTC/USDT')
    frames={}
    for symbol in sorted(symbols):
        frame=closed_bars(feed.cached_ohlcv(symbol,spec.timeframe,limit=limit,as_of_ms=available_by),spec.timeframe,cut)
        if frame is None or len(frame)<2:
            raise ClosureRefused('coordinated_paper_candles_unavailable',symbol)
        frames[symbol]=frame
    ps,hsym=hs['primary']['symbol'],hs['hedge']['symbol']
    primary,hedge=frames[ps],frames[hsym]
    if any(int(mp.ms(frame['ts'])[-1])!=stamp for frame in (primary,hedge)):
        raise ClosureRefused('coordinated_paper_closed_cut_unavailable')
    if not np.array_equal(mp.ms(primary['ts']),mp.ms(hedge['ts'])):
        raise ClosureRefused('coordinated_paper_pair_clock_gap')
    kw=dict(btc={spec.timeframe:frames['BTC/USDT']} if wants_btc else None,universe={s:{spec.timeframe:f} for s,f in frames.items()},
        market=spec_evidence.load_refs(requires,as_of_ms=available_by),symbol=ps,
        derivs=spec_evidence.load_derivs(ps,requires,as_of_ms=available_by))
    if 'world' in requires:
        from ..world.context import load_context
        kw['world']=load_context(as_of_ms=available_by)
    sf=spec_evidence.frames_for(primary,spec.timeframe)
    for tf,bars in need['htf'].items():
        if tf.startswith('__'):
            continue
        source=closed_bars(feed.cached_ohlcv(ps,tf,limit=bars+2,as_of_ms=available_by),tf,cut)
        if source is None or len(source)<bars:
            raise ClosureRefused('coordinated_paper_higher_timeframe_unavailable',tf)
        sf[tf]=source
    long,short,validity=compiled.entries_detail(sf,**kw)
    if validity['missing']:
        raise ClosureRefused('coordinated_paper_inputs_unavailable')
    raw=np.asarray(long,bool).astype(int)-np.asarray(short,bool).astype(int)
    signal=int(raw[-1]) if raw[-1]!=raw[-2] else 0
    exit_signal,available=compiled.exit_signal_detail(sf,**kw)
    if exit_signal is not None and not available[-1]:
        raise ClosureRefused('coordinated_paper_exit_input_unavailable')
    exit_=bool(exit_signal[-1]) if exit_signal is not None else False
    pr=np.r_[np.nan,np.diff(primary['close'])/primary['close'].to_numpy()[:-1]]
    hr=np.r_[np.nan,np.diff(hedge['close'])/hedge['close'].to_numpy()[:-1]]
    ratio=H.estimate_ratio(hs,pr,hr,len(primary)-1)
    n=int(hs['ratio']['lookback']);a,b=pr[-n:],hr[-n:];finite=np.isfinite(a)&np.isfinite(b)
    corr=float(np.corrcoef(a[finite],b[finite])[0,1]) if finite.sum()>=20 and np.std(a[finite])>0 and np.std(b[finite])>0 else None
    # Arrival after the close cannot supply a fill at that same next open.
    if decision_ms is None and any('available_at_ms' not in frame or int(frame['available_at_ms'].iloc[-1])>cut for frame in (primary,hedge)):
        signal=0
    funding={}
    for leg,symbol in (('primary',ps),('hedge',hsym)):
        rates=spec_evidence.funding_series(symbol,frames[symbol])
        value=float(rates[-1]) if rates is not None and len(rates) else float('nan')
        if np.isfinite(value):
            funding[leg]=value*step/28_800_000
    from ..strategy.exit_policy import trail_atr_series
    atr=trail_atr_series(primary,step).iloc[-1]
    result=dict(p=float(primary['close'].iloc[-1]),h=float(hedge['close'].iloc[-1]),
        po=float(primary['open'].iloc[-1]),ho=float(hedge['open'].iloc[-1]),signal=signal,exit=exit_,
        ratio=float(ratio) if np.isfinite(ratio) else None,corr=corr,
        funding=funding if len(funding)==2 else None,signal_cut_ms=cut,
        funding_model='RETAINED_SIGNED_RATE_PRORATED_ESTIMATE',
        decision_basis='RECEIVED_INPUTS_BEFORE_QUOTE_DECISION' if decision_ms is not None else 'CLOSED_CUT_POINT_IN_TIME',
        primary_exit_observation=dict(bar_ms=stamp,step_ms=step,**{k:float(primary[k].iloc[-1]) for k in ('open','high','low','close')},
                                     atr=float(atr) if np.isfinite(atr) else None))
    hatr=trail_atr_series(hedge,step).iloc[-1]
    result['primary_exit_observation']['hedge']=dict(bar_ms=stamp,step_ms=step,
        **{k:float(hedge[k].iloc[-1]) for k in ('open','high','low','close')},
        atr=float(hatr) if np.isfinite(hatr) else None)
    return result


def quote(feed,symbol,contract):
    """One public book response, stamped locally; no order client is used."""
    ex=feed.ex
    market=ex.market(symbol)
    if market.get('inverse' if contract=='inverse' else 'linear') is not True:
        raise ClosureRefused('coordinated_paper_quote_contract_unavailable')
    iid,source=mp.venue_identity(ex,symbol)
    if not iid or not source:
        raise ClosureRefused('coordinated_paper_quote_identity_unavailable')
    # Retain the public price source, not every unrelated private API URL
    # from the exchange's configuration in every 30-second event.
    origin=json.loads(source)
    api=origin.get('api') or {}
    base=api.get('dapiPublic' if contract=='inverse' else 'fapiPublic')
    if not isinstance(base,str) or not base:
        raise ClosureRefused('coordinated_paper_quote_source_unavailable')
    source=mp.encode(dict(provider=origin['provider'],api_base=base,operation='fetch_order_book'))
    started=int(time.time()*1000)
    raw=ex.fetch_order_book(symbol,limit=5)
    received=int(time.time()*1000)
    return dict(symbol=symbol,contract=contract,instrument_id=iid,source=source,
                started_ms=started,received_ms=received,bid=float(raw['bids'][0][0]),ask=float(raw['asks'][0][0]))


def prospective_observation(feed,journal,version,installed,state,at_ms,*,quote_reader=quote):
    """Calculate once per native candle; quote/mark separately every tick."""
    from copy import deepcopy
    step=TF_MS[version['spec']['timeframe']]
    stamp=at_ms//step*step-step
    native=(stamp-installed['install']['first_bar_ms'])//step
    cached=state.get('native_observation')
    if cached is not None and cached['signal_cut_ms']==stamp+step:
        base=deepcopy(cached);new_signal=False
    else:
        base=observation(feed,journal,version,stamp,decision_ms=at_ms);new_signal=True
    hs=state['spec'];quotes={};by_instrument={}
    for leg in ('primary','hedge'):
        symbol,contract=hs[leg]['symbol'],hs[leg]['contract']
        key=(symbol,contract)
        if key not in by_instrument:
            by_instrument[key]=quote_reader(feed,symbol,contract)
        quotes[leg]=by_instrument[key]
    decision=int(time.time()*1000)
    if decision<at_ms:
        raise ClosureRefused('coordinated_paper_clock_reversed')
    elapsed=decision-state.get('decision_ms',decision)
    funding=base['funding']
    if funding is not None:
        funding={leg:rate*elapsed/step for leg,rate in funding.items()}
    marks={leg:(quotes[leg]['bid']+quotes[leg]['ask'])/2. for leg in quotes}
    return dict(base,p=marks['primary'],h=marks['hedge'],po=marks['primary'],ho=marks['hedge'],
                signal=base['signal'] if new_signal else 0,exit=base['exit'] if new_signal else False,
                funding=funding,paper_quote=True,quotes=quotes,decision_ms=decision,native_bar=native,
                native_observation=base)


def run_once(journal,cfg,feed,*,at_ms=None,reader=observation,allow_entry=False,quote_reader=quote):
    closure=(cfg.get('research') or {}).get('closure') or {}
    if not closure.get('enabled') or not closure.get('paper_intake'):
        return []
    at_ms=int(time.time()*1000) if at_ms is None else int(at_ms)
    F.ensure(journal)
    rows=journal.query('SELECT version_id FROM strategy_versions WHERE source_kind IN (?,?) ORDER BY recorded_at_ms',(KIND,F.HEDGE_HQ_KIND))
    out=[]
    visited=0
    for row in rows:
        version=F.load_version(journal,row['version_id'])
        if F.state_of(journal,version['version_id']) not in PAPER_STATES:
            continue
        if visited>=2:break
        visited+=1
        installed=F.record_coordinated_paper_install(journal,version['version_id'],at_ms=at_ms)
        runner=CoordinatedPaper(journal);key=installed['book_id'];state=runner.state(key)
        prospective=state.get('paper_execution_protocol')==QUOTE_PROTOCOL_ID
        stamp=installed['install']['first_bar_ms']+(state['bar']+1)*TF_MS[version['spec']['timeframe']]
        if prospective:
            stamp=at_ms//TF_MS[version['spec']['timeframe']]*TF_MS[version['spec']['timeframe']]-TF_MS[version['spec']['timeframe']]
            if stamp<installed['install']['first_bar_ms']:
                continue
            cached=state.get('native_observation')
            if (cached and cached['signal_cut_ms']==stamp+TF_MS[version['spec']['timeframe']]
                    and not state['book']['seq'] and not any(state['book']['pos'].values()) and not state['book']['pending']):
                # Before the first entry intent, actual and comparator books
                # are all cash. There is nothing to price between native cuts.
                continue
        if stamp+TF_MS[version['spec']['timeframe']]>at_ms:
            continue
        try:
            obs=(prospective_observation(feed,journal,version,installed,state,at_ms,quote_reader=quote_reader)
                 if prospective else reader(feed,journal,version,stamp))
            result=runner.advance(key,state['bar']+1,obs,allow_entry=allow_entry and (not prospective or obs['funding'] is not None))
            out.append(dict(version_id=version['version_id'],bar=result['bar'],status='PAPER_OBSERVATION',real_order_submissions=0))
            # Only completed two-leg cycles trigger lifecycle evaluation.
            if any(state['book']['pos'].values()) and not any(result['positions'].values()) and F.state_of(journal,version['version_id'])==F.SHADOW:
                try:
                    assessment=F.evaluate_probation(journal,cfg,version['version_id'],at_ms=at_ms)
                    out[-1]['probation']=assessment['status']
                except (F.HandoffRefused,ValueError,KeyError) as exc:
                    # The execution cut is already committed. A lifecycle
                    # failure must not rewrite it as a different input gap.
                    out[-1].update(probation='UNAVAILABLE',probation_reason=getattr(exc,'code',type(exc).__name__))
        except Exception as exc:
            reason=getattr(exc,'code',type(exc).__name__)
            # Retain an unavailable observation and advance this cut. Retrying
            # the same missing historical receipt forever cannot repair it.
            if not prospective:
                runner.advance(key,state['bar']+1,dict(p=None,h=None,po=None,ho=None,signal=0,
                    exit=True,ratio=None,corr=None,funding=None,input_gap=reason,signal_cut_ms=stamp+TF_MS[version['spec']['timeframe']]),allow_entry=False)
            out.append(dict(version_id=version['version_id'],status='PAPER_INPUT_GAP',reason=reason))
    return out


def input_needs(journal):
    """Exact current collector demand of at most two coordinated paper books.

    Paper demand never becomes claimed venue exposure or a trading strategy.
    """
    from ..strategy.spec_evidence import _SERIES_FOR
    from ..research.compose.experiments import needs_btc
    F.ensure(journal)
    rows=journal.query('SELECT version_id FROM strategy_versions WHERE source_kind IN (?,?) ORDER BY recorded_at_ms',(KIND,F.HEDGE_HQ_KIND))
    candles={};derivs=[];count=0
    for row in rows:
        version=F.load_version(journal,row['version_id'])
        if F.state_of(journal,version['version_id']) not in PAPER_STATES:
            continue
        count+=1
        if count>2:
            break
        spec=StrategySpec.from_dict(version['spec']);compiled=compile_spec(spec)
        hs=version['lineage']['research']['multi_leg']['hedge_spec']
        need=fill_timing.history_requirements(spec)
        tfs={spec.timeframe}|{tf for tf in need['htf'] if not tf.startswith('__')}
        symbols={hs[leg]['symbol'] for leg in ('primary','hedge')}
        packet=version['lineage']['research']['multi_leg']['packet']
        if needs_btc(packet.get('candidate') or packet['freeze']['candidate']):
            candles.setdefault('BTC/USDT',set()).add(spec.timeframe)
        for symbol in symbols:
            candles.setdefault(symbol,set()).update(tfs)
            req=set(compiled.data_requires)|{'funding'}
            derivs.extend((symbol,_SERIES_FOR[r],max(need['base'],hs['ratio']['lookback'])*TF_MS[spec.timeframe])
                          for r in sorted(req) if r in _SERIES_FOR)
    return candles,derivs
