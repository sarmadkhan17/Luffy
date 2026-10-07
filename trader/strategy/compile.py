"""Compile a StrategySpec into the three things the company needs from it:
vectorized entry arrays (Analyst), a live evaluator (Trader), and a readable
card (Librarian). One artifact, three consumers, no drift.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from copy import deepcopy

import logging
import time

import numpy as np

from ..core.types import TF_MS, Action, StrategySignal, closed_bars
from . import dsl
from .features import FeatureCtx
from .spec import StrategySpec
from .signal_occurrence import (ENTRY_SERIES_MISALIGNED, SIGNAL_BAR_CLOSE_UNAVAILABLE, bar_open_ms, spec_fingerprint)

log = logging.getLogger(__name__)

#: context frames the snapshot keys by name rather than by timeframe
_CTX_TF = {"BTC_1h": "1h"}

#: Bump COMPILER_VERSION when compile/evaluate semantics change and
#: FEATURE_VERSION when any feature implementation changes. The feature
#: *contract* (names, arg specs, domains, data requirements) is hashed
#: automatically, so a contract change alters identity even if a bump is
#: forgotten; a pure implementation change cannot be detected and needs the bump.
COMPILER_VERSION = "strategy-compiler.v1"
FEATURE_VERSION = "strategy-features.v1"


def feature_contract_sha256() -> str:
    import hashlib
    import json
    from .features import FEATURES
    body = [[n, repr(f.arg_specs), repr(f.domain), list(f.requires)]
            for n, f in sorted(FEATURES.items())]
    return hashlib.sha256(json.dumps(
        [FEATURE_VERSION, body], sort_keys=True).encode()).hexdigest()


def compile_identity(spec_sha256: str | None, compiler_version: str,
                     feature_version: str, feature_sha256: str) -> str:
    """The evaluation identity: any material change to the spec, compiler or
    feature contract yields a different value."""
    import hashlib
    import json
    return hashlib.sha256(json.dumps(
        [spec_sha256, compiler_version, feature_version, feature_sha256],
        sort_keys=True).encode()).hexdigest()


@dataclass
class CompiledStrategy:
    spec: StrategySpec
    _long: object | None = None
    _short: object | None = None
    _filters: list = field(default_factory=list)
    _exit: object | None = None
    data_requires: tuple = ("ohlcv",)
    #: sha256 of the exact compiled spec this evaluator runs; stamped on every
    #: signal so a trade can name the version that proposed it (provenance only)
    spec_sha256: str | None = None
    #: stamped when compiled from an exact StrategyVersion (`compile_version`);
    #: None for a bare spec (research / admission probes). Provenance only —
    #: compiling grants no install, approval, capital or execution authority.
    version_id: str | None = None
    compiler_version: str = COMPILER_VERSION
    feature_version: str = FEATURE_VERSION
    feature_sha256: str | None = None
    identity: str | None = None

    # ── vectorized path (Analyst) ────────────────────────────────────────
    def entries(self, frames: dict, btc: dict | None = None,
                derivs: dict | None = None, universe: dict | None = None,
                market: dict | None = None, symbol: str | None = None, world=None):
        lo, sh, _ = self.entries_detail(frames, btc, derivs, universe, market, symbol, world)
        return lo, sh

    def entries_detail(self, frames: dict, btc=None, derivs=None, universe=None,
                       market=None, symbol=None, world=None):
        """`entries` plus the per-bar conditions that suppressed signals, so a
        caller can tell "no signal" from "rejected / unavailable"."""
        ctx = self._ctx(frames, btc, derivs, universe, market, symbol, world)
        n = len(ctx.index)
        # This accumulator is mutated below; pandas can expose a read-only view.
        quality_ok = (ctx.df['quality'].eq('VALID').to_numpy(copy=True)
                      if 'quality' in ctx.df else np.ones(n, dtype=bool))
        keep = quality_ok.copy()
        for f in self._filters:
            keep &= dsl.evaluate_bool(f, ctx)
        lo = (dsl.evaluate_bool(self._long, ctx) & keep) \
            if self._long is not None else np.zeros(n, dtype=bool)
        sh = (dsl.evaluate_bool(self._short, ctx) & keep) \
            if self._short is not None else np.zeros(n, dtype=bool)
        # a bar cannot be both; a direction conflict resolves to no trade
        both = lo & sh
        world_ok = ctx._world_valid
        if world_ok is not None:
            lo &= world_ok
            sh &= world_ok
        return lo & ~both, sh & ~both, {"quality_ok": quality_ok, "world_ok": world_ok}

    def exit_signal(self, frames: dict, btc=None, derivs=None,
                    universe=None, market=None, symbol: str | None = None, world=None):
        if self._exit is None:
            return None
        ctx = self._ctx(frames, btc, derivs, universe, market, symbol, world)
        result = dsl.evaluate_bool(self._exit, ctx)
        return result & ctx._world_valid if ctx._world_valid is not None else result

    def _ctx(self, frames, btc, derivs, universe=None,
             market=None, symbol=None, world=None) -> FeatureCtx:
        self._verify_spec()
        tf = self.spec.timeframe
        if tf not in frames:
            raise dsl.SpecError(f"spec timeframe '{tf}' not in frames "
                                f"{sorted(frames)}")
        if world is None and "world" in self.data_requires:
            # No latest/unbounded fallback: an uncut load would answer a
            # historical evaluation from a different world than a current one.
            cut = frames[tf].attrs.get('as_of_ms')
            if cut is None:
                raise dsl.SpecError("world_context_cut_required")
            from ..world.context import load_context
            world = load_context(as_of_ms=cut)
        return FeatureCtx(frames=frames, tf=tf, btc=btc, derivs=derivs,
                          universe=universe, market=market, symbol=symbol, world=world)

    def _verify_spec(self):
        from ..engine.trade_provenance import spec_version
        if spec_version(self.spec)["spec_sha256"] != self.spec_sha256:
            raise dsl.SpecError("compiled_spec_version_mismatch")
        if (self.compiler_version != COMPILER_VERSION
                or self.feature_version != FEATURE_VERSION
                or self.feature_sha256 != feature_contract_sha256()):
            raise dsl.SpecError("compiled_feature_version_mismatch")

    # ── live path (Trader) ───────────────────────────────────────────────
    def to_evaluator(self):
        """A callable with the EXACT signature library.evaluate() dispatches
        to: (genome_like, Snapshot) -> StrategySignal | None.

        This is why the orchestrator needs no change at cutover — registering
        this under f"spec:{id}" makes library.evaluate()'s family dispatch
        (library.py:293) find it like any other evaluator.
        """
        def _evaluate(_genome, snap, *, diagnostic=None):
            import pandas as pd
            from ..data.market_provenance import cut as valid_cut
            try:
                self._verify_spec()
            except dsl.SpecError as e:
                code = str(e) or "compiled_spec_version_mismatch"
                if diagnostic:
                    diagnostic(code)
                log.warning("spec %s refused: %s", self.spec.id, code)
                return None
            try:
                if not snap.ts or snap.ts in ('now','today'):
                    raise ValueError('explicit snapshot cut required')
                cut = valid_cut(int(pd.Timestamp(snap.ts).timestamp()*1000))
            except (ValueError,TypeError,OverflowError):
                if diagnostic:
                    diagnostic('invalid_snapshot_cut')
                return None
            # Judge CLOSED bars only. The live frame's last row is the bar
            # currently forming, whose `close` is just the last trade — but
            # the statistics that admitted this spec came from
            # vector_backtest, which signals on bar i and fills at
            # closes[i]. Reading the forming bar made the live rule "price
            # is beyond the level right now" where the validated rule is
            # "the bar CLOSED beyond it", so every intrabar poke that
            # retraced was an entry the backtest never took. On 2026-09-02
            # UNI/USDT 4h traded to 6.373 above its channel and closed at
            # 5.742 below it.
            frames = {}
            for k, v in snap.dfs.items():
                if v is None or not len(v):
                    continue
                # "BTC_1h" is a context frame keyed by name, not timeframe
                tf = k if k in TF_MS else _CTX_TF.get(k)
                from ..data.market_provenance import eligible_frame
                if cut is not None:
                    v = eligible_frame(v, tf, cut)
                    if v is None:
                        continue
                v = closed_bars(v, tf, cut) if tf else v
                if len(v):
                    frames[k] = v
            if self.spec.timeframe not in frames:
                if diagnostic:
                    diagnostic("missing_closed_timeframe")
                return None
            btc = ({"15m": frames["BTC_1h"]}
                   if frames.get("BTC_1h") is not None else None)
            missing = self._missing_inputs(snap)
            if missing:
                if diagnostic:
                    diagnostic("required_input_missing:" + missing)
                return None
            try:
                world = getattr(snap, "world", None)
                if world is None and "world" in self.data_requires:
                    from ..world.context import load_context
                    world = load_context(as_of_ms=cut)
                lo, sh, detail = self.entries_detail(frames, btc=btc,
                                      derivs=getattr(snap, "derivs", None),
                                      universe=getattr(snap, "universe", None),
                                      market=getattr(snap, "market", None),
                                      symbol=snap.symbol, world=world)
            except Exception as e:
                # Silence here cost three authored specs their entire live
                # career: they raised on every bar and read as "no signal".
                if diagnostic:
                    diagnostic("evaluation_failed", e)
                log.warning("spec %s failed to evaluate on %s: %s",
                            self.spec.id, snap.symbol, e)
                return None
            if not len(lo):
                if diagnostic:
                    diagnostic("empty_entry_series")
                return None
            if not detail["quality_ok"][-1]:
                if diagnostic:
                    diagnostic("quality_condition_failed")
                return None
            if detail["world_ok"] is not None and not detail["world_ok"][-1]:
                if diagnostic:
                    diagnostic("world_dependency_unavailable" + (
                        ":" + str(world.source_status) if world is not None else ""))
                return None
            if lo[-1]:
                action, why = Action.BUY, self.spec.entry_long
            elif sh[-1]:
                action, why = Action.SELL, self.spec.entry_short
            else:
                return None
            # How stale is the bar this fired on? The backtest fills at the
            # signal bar's own close (vector_backtest.py:115), so a live fill
            # far into the bar is a different trade at a different price.
            # Recorded, not gated: the right cutoff is a measurement nobody
            # has taken yet, and guessing one risks suppressing real entries.
            bar_age_min = None
            try:
                sig_tf = frames[self.spec.timeframe]
                closed_at = (sig_tf["ts"].iloc[-1].timestamp() * 1000
                             + TF_MS.get(self.spec.timeframe, 0))
                bar_age_min = round(
                    (cut - closed_at) / 60_000, 1)
            except Exception:
                pass
            # Signal-occurrence identity: the exact close of the SAME closed
            # bar lo/sh[-1] was evaluated on — never age, wall clock or a
            # nearest bar. Unknown stays None with a reason.
            tf = self.spec.timeframe
            close_ms, unavailable = None, None
            sig_frame = frames[tf]
            if len(lo) != len(sig_frame):
                unavailable = ENTRY_SERIES_MISALIGNED
            else:
                open_ms = (bar_open_ms(sig_frame["ts"].iloc[-1])
                           if "ts" in sig_frame else None)
                if open_ms is None or tf not in TF_MS:
                    unavailable = SIGNAL_BAR_CLOSE_UNAVAILABLE
                else:
                    close_ms = int(open_ms + TF_MS[tf])
            params = {"spec_id": self.spec.id,
                      "spec_sha256": self.spec_sha256,
                      "compile_identity": self.identity,
                      "compiler_version": self.compiler_version,
                      "feature_version": self.feature_version,
                      "signal_bar_age_min": bar_age_min,
                      "spec_fingerprint": (getattr(self, "fingerprint", "")
                                           or spec_fingerprint(self.spec)),
                      "signal_timeframe": tf,
                      "signal_bar_close_ms": close_ms}
            if "world" in self.data_requires and world is not None:
                params['world_context'] = dict(context_id=world.context_id, query_cut_ms=cut,
                                              source_status=world.source_status)
            sources=[]
            for family, bundle in (('frames',frames),('derivs',snap.derivs),('references',snap.market)):
                for name, source_frame in (bundle or {}).items():
                    if source_frame is None or 'revision_id' not in source_frame:
                        continue
                    sources.append(dict(kind=family,key=name,
                        available_at_ms=int(source_frame['available_at_ms'].max()),
                        observed_at_ms=int(source_frame['observed_at_ms'].max()),
                        revision_ids=source_frame['revision_id'].tolist(),
                        content_hashes=source_frame['content_hash'].tolist()))
            params['market_provenance'] = dict(schema_version='market.receipt.v1',as_of_ms=cut,sources=sources)
            if self.version_id:
                params["version_id"] = self.version_id
            if unavailable:
                params["signal_occurrence_unavailable"] = unavailable
            return StrategySignal(
                strategy_id=self.spec.id, strategy_name=self.spec.name,
                symbol=snap.symbol, action=action,
                confidence=0.6,
                rationale=f"{self.spec.name}: {why}",
                params=params)
        _evaluate._diagnostic_capable = True
        return _evaluate

    def _missing_inputs(self, snap) -> str | None:
        """Declared non-OHLCV dependencies the snapshot does not carry at all.
        Absent is surfaced, never evaluated as NaN/zero and left to read as
        "no signal". (Present-but-NaN stays NaN in the feature layer.)"""
        for req in self.data_requires:
            if req == "ohlcv" or req == "world":
                continue
            if req.startswith("ref:"):
                if (getattr(snap, "market", None) or {}).get(req[4:]) is None:
                    return req
            elif not getattr(snap, "derivs", None):
                return req
        return None

    # ── tradingview path ─────────────────────────────────────────────────
    def to_pine(self, fee_pct: float = 0.05) -> tuple[str, bool, list]:
        """(pine_source, tv_testable, reasons).

        Honest degradation: a spec whose features have no Pine analogue
        (funding, open interest, flow) returns tv_testable=False with the
        reason, rather than a substituted proxy. Substituting a proxy is what
        made FAMILY_TV score every candidate of a family identically.
        """
        from .pine_spec import to_pine as _to_pine
        return _to_pine(self.spec, fee_pct=fee_pct)

    # ── librarian path ───────────────────────────────────────────────────
    def to_markdown(self) -> str:
        s = self.spec
        ex = s.exit
        prov = s.provenance or {}
        src = prov.get("source_kind", "unknown")
        if prov.get("source_url"):
            src += f" — {prov['source_url']}"
        lines = [
            f"# {s.name}", "",
            f"**Thesis** — {s.thesis}", "",
            f"**Invalidation** — {s.invalidation}", "",
            f"- Timeframe: `{s.timeframe}`  ·  Direction: `{s.direction}`",
            f"- Regimes: {', '.join(s.regime_filter) or 'any'}",
            f"- Data: {', '.join(self.data_requires)}",
            f"- Provenance: {src}",
            "", "## Logic", "```",
        ]
        if s.entry_long:
            lines.append(f"long:   {s.entry_long}")
        if s.entry_short:
            lines.append(f"short:  {s.entry_short}")
        for f in s.filters:
            lines.append(f"filter: {f}")
        lines += [
            f"stop:   {ex.stop}",
            f"target: {ex.target}",
            f"trail:  {ex.trail}",
            f"time:   max {ex.time.get('max_bars')} bars",
        ]
        if ex.signal_exit:
            lines.append(f"exit:   {ex.signal_exit}")
        lines += ["```", ""]
        return "\n".join(lines)


def compile_spec(spec: StrategySpec, *, exit_semantics_id=None) -> CompiledStrategy:
    """Parse every expression up front. A spec that cannot compile must never
    reach the gauntlet, let alone the book."""
    if exit_semantics_id is not None:
        from .exit_policy import EXIT_SEMANTICS_ID, bind_research, unsupported
        if exit_semantics_id != EXIT_SEMANTICS_ID or unsupported(spec.exit):
            raise ValueError('exit_semantics_unavailable')
        bind_research(spec)
    errs = StrategySpec.validate(spec)
    if errs:
        raise dsl.SpecError(f"invalid spec '{spec.id}': {errs}")
    long_t = dsl.parse(spec.entry_long) if (spec.entry_long or "").strip() \
        else None
    short_t = dsl.parse(spec.entry_short) if (spec.entry_short or "").strip() \
        else None
    filters = [dsl.parse(f) for f in (spec.filters or []) if (f or "").strip()]
    exit_t = dsl.parse(spec.exit.signal_exit) \
        if (spec.exit.signal_exit or "").strip() else None
    trees = [t for t in (long_t, short_t, exit_t, *filters) if t is not None]
    req = dsl.data_requires(*trees)
    spec.data_requires = list(req)
    from ..engine.trade_provenance import spec_version
    sha = spec_version(spec)["spec_sha256"]
    fsha = feature_contract_sha256()
    return CompiledStrategy(spec=deepcopy(spec), _long=long_t, _short=short_t,
                            _filters=filters, _exit=exit_t, data_requires=req,
                            spec_sha256=sha, feature_sha256=fsha,
                            identity=compile_identity(sha, COMPILER_VERSION,
                                                      FEATURE_VERSION, fsha))


def compile_version(journal, version_id: str, *, spec_hash: str | None = None,
                    compiler_version: str = COMPILER_VERSION,
                    feature_version: str = FEATURE_VERSION,
                    require_installed: bool = True) -> CompiledStrategy:
    """Compile the EXACT stored StrategyVersion; refuse anything else.

    The version is re-authenticated (`load_version` re-derives every identity
    and lineage), must carry typed lineage (legacy versions are unsupported),
    must be in a live lifecycle state, and — for the normal install path —
    must be the currently installed spec. The result is bound to the version
    and grants no authority: nothing is written, installed or approved here.
    Raises `factory_handoff.HandoffRefused` (stable `.code`) or SpecError."""
    from . import factory_handoff as fh
    v = fh.load_version(journal, version_id)
    if v.get("schema") != fh.VERSION_SCHEMA:
        fh._refuse("version_schema_unsupported")
    if v.get("lineage") is None:
        fh._refuse("legacy_version_unsupported")
    if spec_hash is not None and spec_hash != v["spec_hash"]:
        fh._refuse("spec_hash_mismatch")
    if compiler_version != COMPILER_VERSION:
        fh._refuse("compiler_version_mismatch")
    if feature_version != FEATURE_VERSION:
        fh._refuse("feature_version_mismatch")
    if fh.state_of(journal, version_id) in (None, fh.RETIRED, fh.REJECTED):
        fh._refuse("version_not_live")
    if require_installed:
        fh.verify_validation(journal, v)
        fh.verify_install(journal, v, current=True)
    spec = StrategySpec.from_dict(v["spec"])
    declared = list(spec.data_requires)
    compiled = compile_spec(spec)
    # the frozen declaration must equal what the expressions actually need
    if sorted(declared) != sorted(compiled.data_requires):
        fh._refuse("dependency_mismatch")
    compiled.version_id = version_id
    return compiled
