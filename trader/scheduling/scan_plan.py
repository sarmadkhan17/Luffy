"""Strategy-owned trading inputs. Plans are local to one shortlisted batch.

No training windows, analyst-only context, or speculative dependency defaults.
The existing regime and trend guards are explicit execution dependencies.
"""
import ast
from dataclasses import dataclass, field

from ..core.types import TF_MS
from ..engine.orchestrator import symbol_allows
from ..strategy import dsl, fill_timing
from ..strategy.spec_evidence import _SERIES_FOR as _EXTERNAL


@dataclass
class Inputs:
    population: list = field(default_factory=list)
    limits: dict = field(default_factory=dict)
    requires: set = field(default_factory=set)
    btc: dict = field(default_factory=dict)
    peer_limits: dict = field(default_factory=dict)
    peers: bool = False
    world: bool = False
    needs: dict = field(default_factory=dict)   # strategy id -> its own declared requirements
    spans: dict = field(default_factory=dict)   # external requirement -> span (ms) its OWN consumers need

    def merge(self, other):
        self.population.extend(other.population)
        for tf, bars in other.limits.items():
            self.limits[tf] = max(self.limits.get(tf, 0), bars)
        for tf, bars in other.btc.items():
            self.btc[tf] = max(self.btc.get(tf, 0), bars)
        for tf, bars in other.peer_limits.items():
            self.peer_limits[tf] = max(self.peer_limits.get(tf, 0), bars)
        self.requires.update(other.requires)
        self.peers |= other.peers
        self.world |= other.world
        self.needs.update(other.needs)
        for name, span in other.spans.items():
            self.spans[name] = max(self.spans.get(name, 0), span)


def _calls(spec):
    """Include frozen fitted inputs, including their external dependencies."""
    seen = set()
    def walk(expression, tf):
        for node in ast.walk(dsl.parse(expression)):
            if not isinstance(node, ast.Call):
                continue
            yield node, tf
            if node.func.id in ('fitted', 'fitted_pass'):
                aid = node.args[0].value
                if aid in seen:
                    raise ValueError('recursive_fitted_dependency')
                seen.add(aid)
                for expr in fill_timing._artifact_inputs(aid):
                    yield from walk(expr, tf)
                seen.remove(aid)
    for expr in fill_timing._expressions(spec):
        yield from walk(expr, spec.timeframe)


def strategy_inputs(view, st, genome):
    metadata = getattr(view, '_strategy_inputs', {}).get(st.id)
    if metadata is None:
        # Legacy evaluators are opaque Python. Keep their established input
        # contract rather than inventing a smaller unverified one.
        if str(genome.family).startswith('spec:'):
            raise ValueError('compiled_dependency_missing')
        known = {'ema_trend','vwap_fade','breakout_retest','sweep_reversal',
                 'rotation_momo','rsi_extreme','ma_cross','bb_fade'}
        if genome.family not in known:
            raise ValueError('legacy_dependency_unknown')
        limits = {tf: max(400, getattr(view, '_history_limits', {}).get(tf, 400))
                  for tf in view.cfg['timeframes']['context'] + [view.cfg['timeframes']['execution']]}
        limits[genome.params.get('trend_tf', '15m')] = max(400, int(genome.params.get('slow_len', 0)) + 60,
            int(genome.params.get('anchor_bars', 0)) + 10, int(genome.params.get('range_lookback', 0)) + 80)
        out = Inputs(limits=limits)
        if genome.family == 'rotation_momo':
            out.btc['1h'] = max(400, int(genome.params.get('lag_lookback', 0)) + 1)
        return out
    spec, requires = metadata
    return declared_inputs(spec,requires)


def declared_inputs(spec,requires):
    """Finite inference inputs shared by candidate scans and owned exits."""
    need = fill_timing.history_requirements(spec)
    if need['exceeds_bound']:
        raise ValueError('history_exceeds_bound')
    # Retain the established finite warmup convention; no training history.
    out = Inputs(limits=fill_timing.inference_limits([spec]), requires=set(requires))
    out.world = 'world' in out.requires
    for node, tf in _calls(spec):
        name = node.func.id
        if name in ('xs_rank', 'breadth', 'dispersion'):
            out.peers = True
        if name in ('btc_ret', 'btc_ema_dist', 'corr_btc', 'rel_strength_btc'):
            # FeatureCtx may evaluate the expression under htf(). Supplying
            # every actually declared frame avoids a wrong-timeframe fallback.
            for source_tf, bars in out.limits.items():
                out.btc[source_tf] = max(out.btc.get(source_tf, 0), bars)
    if out.peers:
        out.peer_limits = dict(out.limits)
    if out.world:
        from ..cognition.attention import CognitionConfig
        for tf in out.limits:
            out.limits[tf] = max(out.limits[tf], min(2048, max(
                CognitionConfig().window + 1, 604_800_000 // TF_MS[tf] + 1)))
    # Dependency-specific contract, fixed BEFORE the shared execution/regime guards
    # are added in build(): an external source only spans what its consumers declare.
    span = max((bars * TF_MS[tf] for tf, bars in out.limits.items()), default=0)
    out.spans = {r: span for r in out.requires if r in _EXTERNAL or r.startswith('ref:')}
    return out


def build(view, symbols):
    plans, errors, resolved = {}, {}, {}
    for symbol in symbols:
        combined = Inputs()
        for st, genome in view.population:
            if not st.is_trade_eligible or view.market_type.value not in genome.markets or not symbol_allows(genome, symbol):
                continue
            if st.id not in resolved and st.id not in errors:
                try:
                    resolved[st.id] = strategy_inputs(view, st, genome)
                except Exception as exc:
                    errors[st.id] = str(exc)
            if st.id in errors:
                continue
            inputs = resolved[st.id]
            inputs.population = [(st, genome)]
            inputs.needs = {st.id: dict(requires=frozenset(inputs.requires), btc=dict(inputs.btc), peers=inputs.peers,
                                        world=inputs.world, tfs=frozenset(inputs.limits) if genome is not None else frozenset())}
            combined.merge(inputs)
        if combined.population:
            # Orchestrator classify(15m,1h), htf_trend_score(4h), configured
            # execution price and ATR sizing. These are not analyst inputs.
            for tf in {'15m', '4h', view.cfg['timeframes']['execution']}:
                combined.limits[tf] = max(combined.limits.get(tf, 0), 400)
            plans[symbol] = combined
    return plans, errors
