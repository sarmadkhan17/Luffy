"""Immutable analyst measurements; legacy Vote is only a scoring projection.

Source refs bind the supplied bytes, not an inferred provider or availability.
Confidence is explicitly heuristic, not a calibrated probability of profit.
"""
from dataclasses import dataclass
from hashlib import sha256
import json
import math

from ..world.observation import Quality, _freeze, _plain

SCHEMA = 'analyst.measurement.v1'


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


@dataclass(frozen=True)
class Measurement:
    analyst: str
    instrument: str
    observed_at: str
    inputs: tuple
    horizon: str | None
    quality: Quality
    strength: float | None
    uncertainty: dict
    limitations: tuple
    rationale: str
    schema: str = SCHEMA

    def __post_init__(self):
        if self.schema != SCHEMA or not all(isinstance(x, str) and x for x in
                (self.analyst, self.instrument, self.observed_at, self.rationale)):
            raise ValueError('measurement identity/schema unavailable')
        if not isinstance(self.quality, Quality) or not self.inputs or not self.limitations:
            raise ValueError('measurement declarations unavailable')
        if not isinstance(self.inputs,(tuple,list)) or not isinstance(self.limitations,(tuple,list)) or any(
                not isinstance(x,str) or not x for x in self.limitations):
            raise ValueError('invalid measurement declarations')
        if self.horizon is not None and (not isinstance(self.horizon, str) or not self.horizon):
            raise ValueError('invalid horizon')
        if self.strength is not None and (type(self.strength) not in (int, float)
                or not math.isfinite(self.strength) or not -1 <= self.strength <= 1):
            raise ValueError('invalid measurement strength')
        if self.quality in (Quality.MISSING, Quality.INVALID, Quality.UNSUPPORTED) and self.strength is not None:
            raise ValueError('unavailable measurement cannot carry strength')
        for item in self.inputs:
            if set(item) != {'name', 'source', 'source_ref', 'quality', 'required', 'reason'}:
                raise ValueError('input declarations incomplete')
            Quality(item['quality'])
            if not item['name'] or not item['source'] or type(item['required']) is not bool:
                raise ValueError('invalid input identity')
            if item['source_ref'] is not None and (not isinstance(item['source_ref'],str)
                    or len(item['source_ref'])!=64 or any(c not in '0123456789abcdef' for c in item['source_ref'])):
                raise ValueError('invalid exact input reference')
            if item['source_ref'] is None and item['quality'] not in ('MISSING', 'UNSUPPORTED'):
                raise ValueError('available input requires exact source ref')
            if item['required'] and item['quality'] in ('MISSING','INVALID','UNSUPPORTED') and self.strength is not None:
                raise ValueError('required missing input cannot carry measurement strength')
        if set(self.uncertainty) != {'confidence', 'basis'} or not self.uncertainty['basis']:
            raise ValueError('uncertainty declaration incomplete')
        confidence = self.uncertainty['confidence']
        if confidence is not None and (type(confidence) not in (int, float)
                or not math.isfinite(confidence) or not 0 <= confidence <= 1):
            raise ValueError('invalid confidence')
        if (self.strength is None) != (confidence is None):
            raise ValueError('missing measurement cannot default confidence')
        object.__setattr__(self, 'inputs', tuple(_freeze(x) for x in self.inputs))
        object.__setattr__(self, 'uncertainty', _freeze(self.uncertainty))
        object.__setattr__(self, 'limitations', tuple(self.limitations))

    def to_dict(self):
        result = {k: _plain(getattr(self, k)) for k in self.__dataclass_fields__}
        result['quality'] = self.quality.value
        return result

    @classmethod
    def from_dict(cls, record):
        fields = dict(record)
        # Missing fields are never supplied by dataclass defaults on replay.
        if set(fields) != set(cls.__dataclass_fields__):
            raise ValueError('measurement wire fields missing/unsupported')
        fields['quality'] = Quality(fields['quality'])
        return cls(**fields)

    @property
    def record_id(self):
        return sha256(canonical(self.to_dict()).encode()).hexdigest()
