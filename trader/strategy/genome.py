"""StrategyGenome — a falsifiable trading hypothesis with constrained genes.

Rules (REQUIREMENTS §5):
- every strategy MUST declare which inefficiency it exploits (hypothesis)
  and HOW it dies (invalidation) — vague proposals are rejected at birth,
  before they can waste backtest or paper time
- genes are constrained per family: no freestyle parameter soup
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..core.types import Strategy, StrategyState, new_id

REGIMES = {"TRENDING_UP", "TRENDING_DOWN", "RANGING", "VOLATILE"}
FAMILIES = {"ema_trend", "vwap_fade", "breakout_retest",
            "sweep_reversal", "rotation_momo",
            "rsi_extreme", "ma_cross", "bb_fade"}

# gene name -> (python type, low, high, default)
FAMILY_GENE_SPECS: dict[str, dict[str, tuple]] = {
    "ema_trend": {
        "adx_min":        (float, 15.0, 35.0, 22.0),
        "pullback_atr":   (float, 0.2, 2.0, 0.8),    # entry within N·ATR of EMA20
        "trend_tf":       (str, None, None, "15m"),
    },
    "vwap_fade": {
        "z_entry":        (float, 1.5, 4.0, 2.5),
        "anchor_bars":    (int, 48, 192, 96),
        "max_hold_bars":  (int, 8, 64, 24),
    },
    "breakout_retest": {
        "range_lookback": (int, 24, 120, 48),
        "vol_mult":       (float, 1.1, 3.0, 1.4),
        "retest_atr":     (float, 0.2, 1.5, 0.5),
    },
    "sweep_reversal": {
        "max_reclaim_bars": (int, 1, 8, 3),
        "min_sweep_frac":   (float, 0.0005, 0.01, 0.002),
    },
    "rotation_momo": {
        "btc_ret_1h_min": (float, 0.003, 0.03, 0.008),
        "lag_lookback":   (int, 2, 12, 4),
    },
    # ── families added to absorb classical harvested/crawled rules ──
    "rsi_extreme": {                       # fade momentum extremes on reclaim
        "rsi_len":        (int, 5, 30, 14),
        "os_level":       (float, 15.0, 35.0, 30.0),
        "ob_level":       (float, 65.0, 85.0, 70.0),
    },
    "ma_cross": {                          # classic fast/slow MA crossover
        "fast_len":       (int, 5, 50, 20),
        "slow_len":       (int, 30, 200, 50),
    },
    "bb_fade": {                           # pierce of Bollinger band reverts
        "bb_len":         (int, 10, 40, 20),
        "bb_k":           (float, 1.5, 3.0, 2.0),
    },
}


@dataclass
class Genome:
    strategy_id: str
    family: str
    hypothesis: str
    invalidation: str
    regime_filter: frozenset[str]
    markets: frozenset[str]            # {"spot","futures"} subset
    params: dict = field(default_factory=dict)
    generation: int = 0                # 0=seed; brain mutations increment
    parent_id: str = ""

    @staticmethod
    def validate(g: "Genome") -> list[str]:
        """Return list of problems; empty = valid."""
        errs: list[str] = []
        if g.family not in FAMILIES:
            errs.append(f"unknown family '{g.family}'")
        if len((g.hypothesis or "").strip()) < 60:
            errs.append("hypothesis must cite the inefficiency (≥60 chars)")
        if len((g.invalidation or "").strip()) < 20:
            errs.append("invalidation clause required (≥20 chars)")
        bad_regimes = g.regime_filter - REGIMES
        if bad_regimes:
            errs.append(f"unknown regimes: {bad_regimes}")
        if not (g.markets <= {"spot", "futures"}) or not g.markets:
            errs.append(f"markets must be non-empty subset of spot/futures")
        spec = FAMILY_GENE_SPECS.get(g.family, {})
        for k, v in g.params.items():
            if k not in spec:
                errs.append(f"gene '{k}' not in family '{g.family}' spec")
                continue
            typ, lo, hi, _ = spec[k]
            if typ is str:
                continue
            # JSON/YAML brains emit ints for round floats — coerce, don't reject
            if isinstance(v, int) and typ is float:
                v = float(v)
            if not isinstance(v, typ):
                errs.append(f"gene '{k}' wrong type {type(v).__name__}")
            elif not (lo <= v <= hi):
                errs.append(f"gene '{k}'={v} outside [{lo},{hi}]")
        missing = set(spec) - set(g.params)
        for k in missing:
            g.params[k] = spec[k][3]     # fill defaults
        return errs


def spawn_seed(sid_hint: str, family: str, name: str, description: str,
               hypothesis: str, invalidation: str, regimes: set[str],
               markets: set[str], **params) -> tuple[Strategy, Genome]:
    st = Strategy(
        id=f"{sid_hint}_{new_id('g')[:6]}", name=name, kind=family,
        params={}, state=StrategyState.PAPER,      # seeds start in paper probation
        description=description, origin="seed")
    g = Genome(strategy_id=st.id, family=family, hypothesis=hypothesis,
               invalidation=invalidation,
               regime_filter=frozenset(regimes), markets=frozenset(markets),
               params=params)
    errs = Genome.validate(g)
    if errs:
        raise ValueError(f"seed '{name}' invalid: {errs}")
    st.params = dict(g.params)
    return st, g


# Sane per-family fallbacks so genomes mined with missing params still
# forge valid Pine and evaluate without KeyErrors (single source of
# truth shared by pine.py templates and the strategy evaluators).
# NOTE: values must sit INSIDE FAMILY_GENE_SPECS ranges — earlier unit
# errors (sweep 0.3 vs spec ≤0.01; rotation 15% vs ≤3%; lag 24 vs ≤12)
# silently made mined variants of those families untradeable.
def register_family(name: str, gene_spec: dict, param_defaults: dict) -> None:
    """Register a runtime-invented family into all genome structures."""
    FAMILIES.add(name)
    FAMILY_GENE_SPECS[name] = gene_spec
    PARAM_DEFAULTS[name] = param_defaults


PARAM_DEFAULTS: dict[str, dict] = {
    # NOTE: every key here MUST exist in that family's FAMILY_GENE_SPECS —
    # see assert_defaults_consistent() below. ema_trend used to carry
    # fast_len/slow_len, which are not genes: library.evaluate() injected
    # them, the Python evaluator ignored them (it hardcodes ema 20/50/200)
    # and the Pine template interpolated them — so the two judges were
    # scoring different strategies.
    "ema_trend": {"adx_min": 18.0, "pullback_atr": 1.0, "trend_tf": "15m"},
    "vwap_fade": {"anchor_bars": 48, "z_entry": 2.0, "max_hold_bars": 24},
    "breakout_retest": {"range_lookback": 48, "vol_mult": 1.5,
                        "retest_atr": 0.5},
    "sweep_reversal": {"max_reclaim_bars": 6, "min_sweep_frac": 0.002},
    "rotation_momo": {"btc_ret_1h_min": 0.008, "lag_lookback": 4},
    "rsi_extreme": {"rsi_len": 14, "os_level": 25, "ob_level": 75},
    "ma_cross": {"fast_len": 20, "slow_len": 50},
    "bb_fade": {"bb_len": 20, "bb_k": 2.0},
}


def assert_defaults_consistent() -> list[str]:
    """Every PARAM_DEFAULTS key must be a real gene of its family, and every
    default must sit inside the gene's declared range.

    Silent drift here is expensive: the Python evaluator and the Pine
    template read params from different places, so a phantom default makes
    the internal backtest and the TradingView test score different logic.
    Returns the list of problems (empty = consistent).
    """
    problems: list[str] = []
    for fam, defaults in PARAM_DEFAULTS.items():
        spec = FAMILY_GENE_SPECS.get(fam)
        if spec is None:
            problems.append(f"PARAM_DEFAULTS has unknown family '{fam}'")
            continue
        for k, v in defaults.items():
            if k not in spec:
                problems.append(f"{fam}: default '{k}' is not a gene")
                continue
            typ, lo, hi, _ = spec[k]
            if typ is str:
                continue
            if not isinstance(v, (int, float)):
                problems.append(f"{fam}.{k}: default {v!r} is not numeric")
            elif not (lo <= v <= hi):
                problems.append(f"{fam}.{k}: default {v} outside [{lo},{hi}]")
    return problems


_DEFAULT_PROBLEMS = assert_defaults_consistent()
if _DEFAULT_PROBLEMS:                      # fail loudly at import, not silently
    import logging as _logging
    _logging.getLogger(__name__).error(
        "genome PARAM_DEFAULTS inconsistent: %s", _DEFAULT_PROBLEMS)
