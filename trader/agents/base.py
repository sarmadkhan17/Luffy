"""Analyst agent contract.

Every analyst measures ONE market mechanism and returns typed measurement
evidence inside the legacy Vote scoring projection. Analysts never decide.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from functools import wraps
from hashlib import sha256
import math

import numpy as np

from ..core.types import Side, Snapshot, Vote
from .measurement import Measurement, canonical
from ..world.observation import Quality


class Analyst(ABC):
    name: str = "analyst"
    #: regimes where this mechanism is historically paid; orchestrator scales fit
    regime_affinity: tuple = ("TRENDING_UP", "TRENDING_DOWN", "RANGING", "VOLATILE")
    #: bar timeframe the mechanism is actually computed from. None means it
    #: genuinely has no bar horizon (e.g. an order-book snapshot); contextual
    #: learned adjustments then never apply to its votes.
    evidence_timeframe: str | None = None
    # (frame key, required columns, minimum rows). No declaration means unsupported.
    frame_inputs: tuple = ()
    optional_frame_inputs: tuple = ()
    context_inputs: tuple = ()
    measurement_limitations: tuple = ()
    uses_btc_context: bool = False

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        evaluate = cls.__dict__.get('evaluate')
        if evaluate is None:
            return
        @wraps(evaluate)
        def measured(self, snap):
            inputs = self._inputs(snap)
            if any(i['required'] and i['quality'] in ('MISSING', 'INVALID', 'UNSUPPORTED') for i in inputs):
                return self._vote(self.name, snap, None, None, 'required measurement inputs unavailable')
            return evaluate(self, snap)
        cls.evaluate = measured

    def _inputs(self, snap):
        inputs = []
        def add(name, value, quality, required, reason, source):
            inputs.append(dict(name=name, source=source,
                source_ref=None if value is None else sha256(value.encode()).hexdigest(),
                quality=quality, required=required, reason=reason))
        for key, columns, rows in self.frame_inputs:
            required = key not in self.optional_frame_inputs
            df = snap.dfs.get(key)
            if df is None:
                add(key, None, 'MISSING', required, 'frame absent', 'snapshot.dfs.'+key)
                continue
            # CSV preserves pandas' round-trippable float representation;
            # to_json's default decimal rounding can alias distinct inputs.
            payload = df.to_csv(index=True)
            valid = (len(df) >= rows and all(c in df for c in columns)
                     and np.isfinite(df[list(columns)].to_numpy(dtype=float)).all())
            add(key, payload, 'UNKNOWN' if valid else 'INVALID', required,
                'provider availability not established by frame bytes' if valid else 'insufficient/nonfinite input',
                'snapshot.dfs.'+key)
        if self.uses_btc_context:
            ctx = snap.btc_ctx or {}
            # Retain unknown fields explicitly; no neutral values are substituted.
            ctx = {k: (None if isinstance(v, float) and not math.isfinite(v) else v) for k,v in ctx.items()}
            known = ctx.get('trend') in ('UP', 'DOWN', 'FLAT') or any(ctx.get(k) is not None for k in ('ret_1h','ret_4h'))
            add('btc_context', canonical(ctx) if ctx else None, 'UNKNOWN' if known else 'MISSING', False,
                'optional leader context; availability unverified', 'snapshot.btc_ctx')
        if 'funding' in self.context_inputs:
            symbol, funding, oi = self._ctx
            for key, value in (('funding', funding), ('open_interest', oi)):
                valid = value is not None and symbol == snap.symbol
                if isinstance(value, (float, int)):
                    valid = valid and math.isfinite(value)
                elif isinstance(value, dict):
                    valid = valid and all(type(value.get(k)) in (int,float) and math.isfinite(value[k]) for k in ('now','chg_24h'))
                payload = canonical(value) if valid else None
                add(key, payload, 'UNKNOWN' if valid else 'MISSING', False,
                    'injected snapshot; provider/event/availability times unverified' if valid else 'absent/nonfinite/wrong instrument',
                    'kernel.positioning_context')
        if 'order_book' in self.context_inputs:
            symbol, book = self._ctx
            valid = bool(symbol == snap.symbol and book and book.get('bids') and book.get('asks'))
            if valid:
                valid = all(len(level) == 2 and all(type(v) in (float,int) and math.isfinite(v) and v > 0 for v in level)
                            for side in ('bids','asks') for level in book[side])
            add('order_book', canonical(book) if valid else None, 'UNKNOWN' if valid else 'MISSING', True,
                'current snapshot only; no historical depth or availability claim' if valid else 'absent/invalid/wrong instrument',
                'kernel.order_book_context')
        if not inputs:
            add('undeclared', None, 'UNSUPPORTED', True, 'analyst input contract absent', 'undeclared')
        return inputs

    @abstractmethod
    def evaluate(self, snap: Snapshot) -> Vote: ...

    def _vote(self, name, snap, conviction: float | None, confidence: float | None,
              rationale: str, **meta) -> Vote:
        inputs = self._inputs(snap)
        missing = any(i['required'] and i['quality'] in ('MISSING','INVALID','UNSUPPORTED') for i in inputs)
        unavailable = (missing or conviction is None or confidence is None
                       or not math.isfinite(conviction) or not math.isfinite(confidence))
        quality = (Quality.INVALID if any(i['required'] and i['quality']=='INVALID' for i in inputs)
                   else Quality.UNSUPPORTED if any(i['quality']=='UNSUPPORTED' for i in inputs)
                   else Quality.MISSING if unavailable else Quality.SUSPECT)
        strength = None if unavailable else float(max(-1.0, min(1.0, conviction)))
        conf = None if unavailable else float(max(0.0, min(1.0, confidence)))
        packet = Measurement(name, snap.symbol, snap.ts, tuple(inputs), self.evidence_timeframe,
            quality, strength, dict(confidence=conf, basis='heuristic reliability; not calibrated probability'),
            self.measurement_limitations + ('Source digests identify supplied inputs; provider availability is unverified.',),
            rationale)
        meta['measurement'] = packet.to_dict()
        # Zero is only the legacy abstention projection; the measurement stays null.
        conviction, confidence = strength or 0.0, conf or 0.0
        side = (Side.LONG if conviction > 0.05 else
                Side.SHORT if conviction < -0.05 else Side.FLAT)
        return Vote(agent=name, symbol=snap.symbol, side=side,
                    conviction=max(-1.0, min(1.0, conviction)),
                    confidence=max(0.0, min(1.0, confidence)),
                    rationale=rationale, meta=meta)
