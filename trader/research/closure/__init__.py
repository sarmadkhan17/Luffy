"""Research gap-closure layer: question -> executable candidate -> historical
development -> frozen candidate -> independent qualification -> VALIDATED.

Everything here is research-only. It reuses the existing evaluator, referee
gates, error-budget ledger and Factory; it adds typed contracts around them and
never installs, enables or sizes a strategy. Statuses are never pass labels:
`QUALIFIED` exists only when the registered referee look rejected at its own
alpha on protected data whose provenance and exposure were proven UNEXPOSED.
"""
import hashlib
import json


def canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


class ClosureRefused(ValueError):
    """A contract refused; `.code` is a stable machine reason."""

    def __init__(self, code: str, detail: str = ''):
        super().__init__(code if not detail else f'{code}: {detail}')
        self.code = code
