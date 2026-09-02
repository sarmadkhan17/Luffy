"""Is this strategy still doing what it was validated to do?

The book's only live check was a fixed threshold — profit factor under 0.85
over 10 trades and you are decayed. That answers a different question than
"is this working now". A mechanism validated at a 37% win rate throws three
losses out of three about a quarter of the time, and a fixed rule retires it
for behaving exactly as designed. The same rule protects a genuinely broken
strategy right up until its tenth trade.

The question worth asking is whether live results are still consistent with
the envelope the strategy was admitted on, or whether the gap has grown
larger than sampling noise can account for. Until there are enough trades to
distinguish those, the answer is INSUFFICIENT — which is neither a pass nor a
failure, and is the honest state for most young strategies.

The tail is deliberately ONE-SIDED. Beating the validated rate is not a
fault, and a two-sided test would eventually flag a strategy for doing well.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import comb

#: below this probability, underperformance stops being explicable as variance
DIVERGENCE_P = 0.01


@dataclass(frozen=True)
class Health:
    verdict: str                 # INSUFFICIENT | CONSISTENT | DIVERGED
    trades: int
    wins: int
    observed_winrate: float
    expected_winrate: float
    p_underperform: float
    enough: bool

    @property
    def summary(self) -> str:
        return (f"{self.verdict}: {self.wins}/{self.trades} won "
                f"({self.observed_winrate:.0%}) against a validated "
                f"{self.expected_winrate:.0%}, "
                f"p(this bad or worse) = {self.p_underperform:.3f}")


def _binom_cdf(k: int, n: int, p: float) -> float:
    """P(X <= k) for X ~ Binomial(n, p). Exact; n here is small."""
    if p <= 0.0:
        return 1.0
    if p >= 1.0:
        return 1.0 if k >= n else 0.0
    return sum(comb(n, i) * p ** i * (1 - p) ** (n - i)
               for i in range(0, k + 1))


def assess_health(expected_winrate: float, wins: int, losses: int,
                  p_threshold: float = DIVERGENCE_P) -> Health:
    """Compare a live record against the win rate the strategy was admitted on.

    `p_underperform` is the probability of seeing this many wins OR FEWER if
    the strategy were still performing exactly as validated. A small value
    means variance no longer explains the shortfall.
    """
    n = int(wins) + int(losses)
    obs = (wins / n) if n else 0.0
    if n == 0:
        return Health("INSUFFICIENT", 0, 0, 0.0, expected_winrate, 1.0, False)
    p = _binom_cdf(int(wins), n, float(expected_winrate))
    # "enough" means this sample COULD have produced a divergence verdict.
    # Zero wins in n trades is the worst possible record, so if even that is
    # not improbable enough, no outcome at this sample size could be.
    floor = _binom_cdf(0, n, float(expected_winrate))
    enough = floor < p_threshold
    if not enough:
        verdict = "INSUFFICIENT"
    elif p < p_threshold:
        verdict = "DIVERGED"
    else:
        verdict = "CONSISTENT"
    return Health(verdict, n, int(wins), obs, float(expected_winrate),
                  p, enough)
