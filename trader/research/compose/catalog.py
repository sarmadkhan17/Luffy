"""I1 — the versioned factor catalog and the data-inventory contract.

Every entry states what the factor MEANS, who provides it, the formula (an
expression in the existing DSL, the single executable vocabulary), units,
horizons, warmup, timestamp/vintage/freshness policy, fit boundary and missing
data behaviour. `status` is derived, never assumed:

  EXECUTABLE   the DSL/feature/reference layer can evaluate it now
  GAP_PROVIDER no provider series with the declared definition is wired
  GAP_HISTORY  wired, but retained history is absent/too short (from measurement)

Providers are NOT interchangeable: BTCDOM futures is a Binance index future,
`alts` is an equal-weight index of the tracked universe, CoinGecko dominance is
a forward-recorded snapshot. None is silently used for market-cap dominance,
TOTAL, TOTAL2 or TOTAL3; those carry GAP_PROVIDER until an exact series with
that definition exists. `measure()` reads only quiescent snapshots (never a
live database) and `inventory()` combines catalog and measurement into the
per-domain verified-status report required by A1.
"""
from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass, field

from ...data.references import REFS
from ...strategy.features import FEATURES
from ...strategy.fitted import MODEL_TYPES
from ..closure import digest

CATALOG_VERSION = 'factor-catalog.v1'
DOMAINS = ('price_structure', 'momentum_reversion', 'volatility_participation', 'derivatives_liquidity',
           'relative_cross_sectional', 'market_context', 'regime')
HORIZONS_ALL = ('15m', '1h', '4h')


@dataclass(frozen=True)
class Factor:
    key: str
    domain: str
    meaning: str
    formula: str                 # DSL expression; the only executable form
    unit: str                    # price | ratio | pct | z | oscillator100 | rank01 | count | corr | score
    provider: str = 'binance-usdm-ohlcv'
    instrument: str = 'traded symbol (USDT-M perpetual)'
    warmup: int = 0
    horizons: tuple = HORIZONS_ALL
    publication: str = 'bar close (closed bars only)'
    vintage: str = 'immutable once closed'
    freshness: str = 'NaN until warm; no carry-forward'
    fit_boundary: str = 'none (no fitted parameter)'
    missing: str = 'NaN'
    requires: tuple = ('ohlcv',)
    role: tuple = ('feature', 'condition')
    provider_gap: str = ''       # non-empty -> GAP_PROVIDER with this explanation
    definition: str = ''

    def as_dict(self) -> dict:
        return {k: (list(v) if isinstance(v, tuple) else v) for k, v in self.__dict__.items()}


def _f(key, domain, meaning, formula, unit, warmup=0, **kw):
    return Factor(key, domain, meaning, formula, unit, warmup=warmup, **kw)


_YAHOO = dict(provider='yahoo-finance', instrument='', publication='daily close known 1 day after bar stamp (conservative)',
              vintage='as retained; later restatements unknown', freshness='stale after 100h silent -> NaN (weekend close stays valid)',
              role=('feature', 'condition', 'exposure', 'stress'))
_REF_NOTE = {
    'spx': ('S&P 500 index (^GSPC)', 'index level'), 'vix': ('CBOE VIX (^VIX)', 'index level'),
    'dxy': ('US dollar index (DX-Y.NYB)', 'index level'), 'us10y': ('US 10y yield (^TNX)', 'yield x10'),
    'gold': ('Gold futures (GC=F)', 'USD'), 'oil': ('WTI crude futures (CL=F)', 'USD')}


def _build() -> tuple:
    p = []
    # price / structure / trend
    for n in (6, 24, 72):
        p.append(_f(f'ret{n}', 'price_structure', f'{n}-bar return (rate of change)', f'ret({n})', 'pct', n))
    p += [
        _f('slope20', 'price_structure', 'log-linear slope of close', 'slope(close, 20)', 'pct', 20),
        _f('emadist50', 'price_structure', 'distance from EMA50', '(close - ema(50)) / ema(50)', 'ratio', 50),
        _f('emadist200', 'price_structure', 'distance from EMA200', '(close - ema(200)) / ema(200)', 'ratio', 200),
        _f('vwapdist96', 'price_structure', 'distance from rolling VWAP', '(close - vwap(96)) / vwap(96)', 'ratio', 96),
        _f('donch_up50', 'price_structure', 'close above 50-bar Donchian high (event)', 'close > donchian_hi(50)', 'bool', 50),
        _f('range_pos100', 'price_structure', 'drawdown from 100-bar high', 'dd_from_high(100)', 'pct', 100),
        _f('wick_low', 'price_structure', 'lower wick fraction', 'lower_wick()', 'ratio'),
        # momentum / reversion alternatives
        _f('rsi14', 'momentum_reversion', 'Wilder RSI', 'rsi(14)', 'oscillator100', 14),
        _f('stoch14', 'momentum_reversion', 'stochastic %K', 'stoch_k(14)', 'oscillator100', 14),
        _f('macd', 'momentum_reversion', 'MACD line', 'macd(12, 26, 9)', 'price', 35),
        _f('zclose96', 'momentum_reversion', 'standardised close deviation', 'zscore(close, 96)', 'z', 96),
        _f('bbpctb20', 'momentum_reversion', 'Bollinger %B', 'bb_pctb(20, 2.0)', 'ratio', 20),
        # volatility / participation
        _f('atrrank200', 'volatility_participation', 'ATR percentile rank', 'atr_pct_rank(200)', 'rank01', 214),
        _f('rvol96', 'volatility_participation', 'realized volatility', 'realized_vol(96)', 'pct', 96),
        _f('relvol50', 'volatility_participation', 'relative volume', 'rel_volume(50)', 'ratio', 50),
        _f('volz96', 'volatility_participation', 'volume z-score', 'volume_z(96)', 'z', 96),
        _f('takerfrac', 'volatility_participation', 'taker-buy volume share', 'taker_buy_frac()', 'ratio'),
        # derivatives / liquidity
        _f('fundz360', 'derivatives_liquidity', 'funding rate z-score', 'funding_z(360)', 'z', 360,
           provider='binance-usdm-funding', requires=('funding',), publication='funding settlement time',
           freshness='aligned as-of; stale values not carried past source cadence'),
        _f('basisz360', 'derivatives_liquidity', 'perp-vs-index basis z-score', 'basis_z(360)', 'z', 360,
           provider='binance-usdm-basis', requires=('basis',)),
        _f('oiret24', 'derivatives_liquidity', 'open-interest change', 'oi_ret(24)', 'pct', 24,
           provider='binance-usdm-open-interest', requires=('open_interest',)),
        _f('lsacct360', 'derivatives_liquidity', 'long/short ACCOUNT ratio z-score', 'ls_account_ratio_z(360)', 'z', 360,
           provider='binance-usdm-ls-account', requires=('ls_account_ratio',)),
        _f('lspos360', 'derivatives_liquidity', 'long/short POSITION ratio z-score (distinct from account ratio)',
           'ls_ratio_z(360)', 'z', 360, provider='binance-usdm-ls-position', requires=('ls_ratio',)),
        _f('spread_depth', 'derivatives_liquidity', 'quoted spread / book depth', 'close', 'ratio',
           provider='none', provider_gap='no historical order-book spread/depth series is retained or wired'),
        # relative / cross-sectional
        _f('relbtc30', 'relative_cross_sectional', 'return minus BTC return', 'rel_strength_btc(30)', 'pct', 30,
           requires=('ohlcv',)),
        _f('corrbtc90', 'relative_cross_sectional', 'rolling correlation to BTC', 'corr_btc(90)', 'corr', 90),
        _f('xsret30', 'relative_cross_sectional', 'cross-sectional rank of 30-bar return', 'xs_rank(ret(30))', 'rank01', 30),
        _f('breadth50', 'relative_cross_sectional', 'share of universe above EMA50', 'breadth(close > ema(50))', 'rank01', 50),
        _f('disp6', 'relative_cross_sectional', 'cross-sectional dispersion of 6-bar return', 'dispersion(ret(6))', 'pct', 6),
        _f('pair_corr96', 'relative_cross_sectional', 'rolling correlation of own vs BTC returns (pair relationship)',
           'rolling_corr(ret(1), btc_ret(1), 96)', 'corr', 97),
        _f('asset_size', 'relative_cross_sectional', 'asset market capitalisation', 'close', 'price', provider='none',
           provider_gap='no point-in-time market-cap series is wired; volume rank is a liquidity proxy only'),
    ]
    # market context: each reference with its ACTUAL provider definition
    for key, (meaning, unit) in _REF_NOTE.items():
        p.append(_f(f'ref_{key}_ret30', 'market_context', f'{meaning} 30-bar-equivalent return', f'ref("{key}", ret(30))',
                    'pct', 30, requires=(f'ref:{key}',), definition=meaning, **_YAHOO))
    p += [
        _f('ref_btcdom_ret30', 'market_context', 'BTCDOM/USDT perpetual FUTURES return - a Binance index future, NOT '
           'BTC market-cap dominance', 'ref("btcdom", ret(30))', 'pct', 30, provider='binance-usdm-BTCDOM-future',
           requires=('ref:btcdom',), instrument='BTCDOM/USDT:USDT', role=('feature', 'condition', 'exposure'),
           definition='Binance BTCDOM index future (alts-vs-BTC basket); not market-cap dominance'),
        _f('ref_alts_ret30', 'market_context', 'equal-weight index of the tracked universe - NOT TOTAL2',
           'ref("alts", ret(30))', 'pct', 30, provider='computed-from-tracked-universe', requires=('ref:alts',),
           definition='equal-weight alt index over currently tracked symbols (membership not point-in-time)'),
        _f('ref_stables_ret30', 'market_context', 'total stablecoin supply change (DefiLlama)',
           'ref("stables", ret(30))', 'pct', 30, provider='defillama', requires=('ref:stables',),
           publication='daily, known 1 day after stamp'),
    ]
    # User meaning -> published TradingView series. No local capitalization calculation.
    for key, ticker, label in (
        ('total', 'TOTAL', 'Total cryptocurrency market capitalization'),
        ('total1', 'TOTAL2', 'Crypto market capitalization excluding BTC'),
        ('total2', 'TOTAL3', 'Crypto market capitalization excluding BTC and ETH'),
        ('btc_dominance', 'BTC.D', 'BTC market capitalization dominance'),
        ('usdt_dominance', 'USDT.D', 'USDT market capitalization dominance')):
        p.append(_f(f'ctx_{key}', 'market_context', label, 'ref("tv_' + ticker.lower().replace('.', '_') + '", close)', 'ratio',
                    provider='TradingView-CRYPTOCAP', instrument='CRYPTOCAP:' + ticker,
                    requires=('ref:tv_' + ticker.lower().replace('.', '_'),),
                    definition=label + '; published TradingView series',
                    publication='original export captured locally; historical vintage unverified',
                    vintage='as-returned export hash and capture receipt', freshness='reference loader availability and age checks'))
    # CoinGecko's own published TOTAL (website CSV): exact identity, NOT TradingView TOTAL and NOT the cg_total API snapshot.
    # It is a total-market series only; no ex-BTC / ex-BTC-ETH figure is derived from it.
    _CG = dict(provider='CoinGecko-website-CSV-TOTAL', instrument='CoinGecko global total crypto market cap (USD)',
               requires=('ref:cg_total_daily',),
               definition='CoinGecko published total crypto market cap, daily snapshot at 00:00 UTC, not an OHLC bar; '
                          'provider universe differs from TradingView CRYPTOCAP:TOTAL',
               publication='daily snapshot, treated as known one day after its stamp',
               vintage='AS_RETURNED_AT_CAPTURE: file hash + capture receipt; original release vintages unverified; '
                       'retrospective research only, not point-in-time proof',
               freshness='stale after 3 days without a new row -> NaN')
    p += [
        _f('ctx_cg_total', 'market_context', 'CoinGecko total crypto market capitalization (published, USD)',
           'ref("cg_total_daily", close)', 'ratio', **_CG),
        _f('ctx_cg_total_ret30', 'market_context', 'CoinGecko total crypto market capitalization 30-day change',
           'ref("cg_total_daily", ret(30))', 'pct', 30, **_CG),
    ]
    # regimes: reproducible observable descriptions
    p += [
        _f('regime_trend', 'regime', 'trending: efficiency ratio and ADX both high', 'efficiency_ratio(30) > 0.3 and adx(14) > 20',
           'bool', 30, role=('condition',)),
        _f('regime_range', 'regime', 'ranging: low efficiency ratio and low ADX', 'efficiency_ratio(30) < 0.2 and adx(14) < 20',
           'bool', 30, role=('condition',)),
        _f('regime_highvol', 'regime', 'high-volatility state', 'atr_pct_rank(200) > 0.75', 'bool', 214, role=('condition',)),
        _f('regime_thin', 'regime', 'thin participation state', 'rel_volume(50) < 0.7', 'bool', 50, role=('condition',)),
    ]
    p.append(_f('world_volume_anomaly', 'market_context',
                'Retained World Model volume anomaly at the exact instrument and candle-close cut',
                'world_observation("volume_anomaly", "intraday")', 'z',
                provider='luffy-world-model', requires=('world',),
                publication='exact retained cut; no current-state fallback',
                vintage='immutable WorldModelRecord identity', freshness='VALID exact-cut observations only'))
    for key, kind, meaning, unit in (
            ('world_return24h', 'price_return_24h', 'Closed-bar trailing 24-hour return', 'fraction'),
            ('world_quote_volume24h', 'quote_volume_24h', 'Closed-bar trailing quote notional', 'quote_notional'),
            ('world_volatility7d', 'realized_volatility_7d', 'Trailing seven-day log-return volatility per bar', 'fraction')):
        p.append(_f(key, 'market_context', meaning, f'world_observation("{kind}", "intraday")', unit,
                    provider='luffy-world-model', requires=('world',), publication='exact retained cut and timeframe',
                    vintage='immutable WorldModelRecord identity', freshness='VALID exact-cut observations only'))
    return tuple(p)


FACTORS: tuple = _build()
BY_KEY = {f.key: f for f in FACTORS}
assert len(BY_KEY) == len(FACTORS)


def catalog_id() -> str:
    return digest(dict(v=CATALOG_VERSION, factors=[f.as_dict() for f in FACTORS], models=list(MODEL_TYPES)))


def validate_catalog() -> list:
    """Problems that make an entry non-executable even though it is declared."""
    from ...strategy import dsl
    bad = []
    for f in FACTORS:
        if f.provider_gap:
            continue
        try:
            tree = dsl.parse(f.formula)
        except dsl.SpecError as exc:
            bad.append((f.key, str(exc)))
            continue
        derived = set(dsl.data_requires(tree))
        if not derived <= set(f.requires) | {'ohlcv'}:
            bad.append((f.key, f'requires undeclared {sorted(derived - set(f.requires))}'))
        for ref in (r for r in f.requires if r.startswith('ref:')):
            if ref.split(':', 1)[1] not in REFS:
                bad.append((f.key, f'unknown reference {ref}'))
    return bad


def measure(snapshot_path, derivs_path=None, references_path=None, world_path=None) -> dict:
    """Coverage of refs and derivative series in QUIESCENT snapshot copies
    (refuse_live guards all). Metadata only: counts and first/last stamps.
    With a detached `references_path`, references are measured there instead
    of in the snapshot (one source, as development will read them)."""
    from ..closure.manifest import refuse_live
    out = dict(refs={}, derivs={}, derivs_source=None)
    with closing(sqlite3.connect(refuse_live(references_path or snapshot_path).as_uri() + '?mode=ro', uri=True)) as db:
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='refs'").fetchone():
            for k, lo, hi, n in db.execute('SELECT key, MIN(ts), MAX(ts), COUNT(*) FROM refs GROUP BY key'):
                out['refs'][k] = dict(first_ms=lo, last_ms=hi, rows=n)
    if derivs_path:
        out['derivs_source'] = str(derivs_path)
        with closing(sqlite3.connect(refuse_live(derivs_path).as_uri() + '?mode=ro', uri=True)) as db:
            for s, lo, hi, n, ns in db.execute('SELECT series, MIN(ts), MAX(ts), COUNT(*), COUNT(DISTINCT symbol) '
                                               'FROM derivs GROUP BY series'):
                out['derivs'][s] = dict(first_ms=lo, last_ms=hi, rows=n, symbols=ns)
    out['world'] = {}
    if world_path:
        from ...observability.store import read_world_history
        history, status = read_world_history(refuse_live(world_path), immutable=True, latest_only=True)
        out['world_source_status'] = status
        out['world_measurement_scope'] = 'LATEST_VERIFIED_CUT_ONLY: full history verified by evaluation, not inventory'
        if status in ('RETAINED', 'RECONSTRUCTED_RETROSPECTIVE'):
            for cut in history.cuts:
                for state in history.get_exact(cut).states:
                    for obs in state.observations:
                        if obs.quality.value == 'VALID' and type(obs.value) in (int, float):
                            item = out['world'].setdefault(obs.kind, dict(rows=0, first_ms=cut, last_ms=cut))
                            item['rows'] += 1
                            item['last_ms'] = max(item['last_ms'], cut)
    return out


#: derivative requirement name -> stored series name (strategy.spec_evidence._SERIES_FOR)
_SERIES = {'funding': 'funding', 'open_interest': 'oi', 'taker_ratio': 'taker_ratio', 'ls_ratio': 'ls_ratio',
           'ls_account_ratio': 'ls_account_ratio', 'basis': 'basis'}
MIN_REF_ROWS = 200           # fewer rows than this cannot support any rolling factor: GAP_HISTORY


def status_of(f: Factor, measured: dict | None) -> tuple:
    """(status, reason). With no measurement, executable factors are
    EXECUTABLE_UNVERIFIED: code path exists, data coverage was not measured."""
    if f.provider_gap:
        return 'GAP_PROVIDER', f.provider_gap
    if measured is None:
        return ('EXECUTABLE_UNVERIFIED', 'no snapshot measured') if f.requires != ('ohlcv',) else ('EXECUTABLE', 'candle path')
    reasons = []
    for r in f.requires:
        if r == 'world':
            import ast
            kind = next(n.args[0].value for n in ast.walk(ast.parse(f.formula))
                        if isinstance(n, ast.Call) and getattr(n.func, 'id', None) == 'world_observation')
            if not (measured.get('world') or {}).get(kind, {}).get('rows'):
                reasons.append('required World observation has no verified retained coverage')
        if r.startswith('ref:'):
            m = measured['refs'].get(r.split(':', 1)[1])
            if not m:
                reasons.append(f'{r}: no rows')
            elif m['rows'] < MIN_REF_ROWS:
                reasons.append(f'{r}: only {m["rows"]} rows')
        elif r in _SERIES:
            m = measured['derivs'].get(_SERIES[r])
            if not m:
                reasons.append(f'{r}: no rows in supplied derivatives snapshot')
    return ('GAP_HISTORY', '; '.join(reasons)) if reasons else ('EXECUTABLE', 'measured coverage present')


def inventory(measured: dict | None = None) -> dict:
    """A1: every required domain with per-factor verified status. A domain is
    READY only if at least one factor is EXECUTABLE and no required factor is a
    gap is NOT claimed: gaps are listed so the requirement stays open."""
    doms = {}
    for d in DOMAINS:
        rows = []
        for f in (x for x in FACTORS if x.domain == d):
            st, why = status_of(f, measured)
            rows.append(dict(key=f.key, status=st, reason=why, provider=f.provider, requires=list(f.requires)))
        gaps = [r for r in rows if r['status'].startswith('GAP') or r['status'] == 'EXECUTABLE_UNVERIFIED']
        doms[d] = dict(factors=rows, executable=sum(r['status'] == 'EXECUTABLE' for r in rows),
                       open_gaps=[dict(key=r['key'], status=r['status'], reason=r['reason']) for r in gaps],
                       domain_status='COMPLETE' if rows and not gaps else
                       ('PARTIAL' if any(r['status'] == 'EXECUTABLE' for r in rows) else 'BLOCKED'))
    return dict(catalog_id=catalog_id(), version=CATALOG_VERSION, measured=measured is not None, domains=doms,
                registry_features=len(FEATURES))
