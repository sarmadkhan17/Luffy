"""What a searched condition may say.

A PART is one condition, written twice: once for the long side and once as
its mirror image. Two kinds:

- a GAUGE part compares a scalar expression against a MEASURED percentile of
  that expression's own distribution on the discovery slice. A threshold
  stated as a round number is a guess about a distribution: `> 0.62` on
  taker_buy/volume fires on 0.005% of 4h bars, so the whole flow family read
  as "tested" in a results table while it never took a trade.
- a STATE or EVENT part is boolean on its own (`close > ema(50)`,
  `close > donchian_hi(100)`). An EVENT is sparse — it fires on a bar rather
  than persisting — and a combination may contain at most one, because two
  breakouts landing on the same bar is a rule that never fires.

THE MIRROR IS WRITTEN, NOT COMPUTED. Each gauge's `short` expression is
composed so that the SAME percentile rank means the mirror-image state:

    ret(24)              ->  0 - ret(24)
    rsi(14)              ->  100 - rsi(14)
    lower_wick()         ->  upper_wick()
    dd_from_high(100)    ->  0 - runup_from_low(100)
    atr_pct_rank(200)    ->  atr_pct_rank(200)      (direction-neutral)

Percentiles are then measured separately for each side, so an asymmetric
distribution — which crypto returns are — mirrors honestly instead of being
reflected arithmetically. Only BOTH directions ever worked: long alone won 2
of 5 yearly windows, short alone 3, the pair 4.

Nothing here invents a feature. Every expression resolves against
`strategy.features.FEATURES`, and `part_requires` derives what data a part
needs from the DSL rather than trusting a declaration.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..strategy import dsl

#: the only thresholds a part may use — the spec's four measured cuts
QUANTILES = (0.10, 0.25, 0.75, 0.90)


@dataclass(frozen=True)
class Gauge:
    """A scalar quantity and its mirror. `tfs` empty = every horizon."""
    key: str
    long: str
    short: str
    tfs: tuple = ()


@dataclass(frozen=True)
class Part:
    """One condition, already resolved to concrete boolean expressions."""
    key: str            # canonical: percentile RANK, never the value
    kind: str           # "gauge" | "state" | "event"
    gauge: str          # what it is about — two parts of one gauge conflict
    long: str
    short: str

    def as_dict(self) -> dict:
        return {"key": self.key, "kind": self.kind, "gauge": self.gauge,
                "long": self.long, "short": self.short}

    @staticmethod
    def from_dict(d: dict) -> "Part":
        return Part(d["key"], d["kind"], d["gauge"], d["long"], d["short"])


def _neg(expr: str) -> str:
    """The mirror of a quantity centred on zero."""
    return f"0 - {expr}"


#: every reference with years of free depth. cg_* are excluded: measured
#: 2026-09-11 they hold 3 rows each, recorded forward from that day.
_REFS_ALL = ("btcdom", "alts", "stables", "spx", "dxy", "gold", "us10y",
             "vix", "oil")
#: hourly references only mean something below the 4h frame
_REFS_HOURLY = ("spx_1h", "vix_1h")


def _ref_gauges() -> list[Gauge]:
    out = []
    for key in _REFS_ALL:
        out.append(Gauge(f"r_{key}_ret30", f'ref("{key}", ret(30))',
                         _neg(f'ref("{key}", ret(30))')))
        out.append(Gauge(f"r_{key}_z96", f'ref("{key}", zscore(close, 96))',
                         _neg(f'ref("{key}", zscore(close, 96))')))
    for key in _REFS_HOURLY:
        out.append(Gauge(f"r_{key}_ret30", f'ref("{key}", ret(30))',
                         _neg(f'ref("{key}", ret(30))'), tfs=("1h", "15m")))
    return out


GAUGES: tuple[Gauge, ...] = tuple([
    # ── price, trend, momentum
    Gauge("ret6", "ret(6)", _neg("ret(6)")),
    Gauge("ret24", "ret(24)", _neg("ret(24)")),
    Gauge("ret72", "ret(72)", _neg("ret(72)")),
    Gauge("slope20", "slope(close, 20)", _neg("slope(close, 20)")),
    Gauge("zclose96", "zscore(close, 96)", _neg("zscore(close, 96)")),
    Gauge("bb20", "bb_pctb(20, 2.0)", "1 - bb_pctb(20, 2.0)"),
    Gauge("rsi14", "rsi(14)", "100 - rsi(14)"),
    Gauge("macd", "macd(12, 26, 9)", _neg("macd(12, 26, 9)")),
    Gauge("emadist50", "(close - ema(50)) / ema(50)",
          _neg("(close - ema(50)) / ema(50)")),
    Gauge("emadist200", "(close - ema(200)) / ema(200)",
          _neg("(close - ema(200)) / ema(200)")),
    # a deep pullback inside a trend; its mirror is a deep bounce
    Gauge("pull100", "dd_from_high(100)", _neg("runup_from_low(100)")),
    # ── bar shape
    Gauge("wick", "lower_wick()", "upper_wick()"),
    Gauge("bodyfrac", "body_frac()", "body_frac()"),
    # ── path quality and volatility regime (direction-neutral)
    Gauge("er30", "efficiency_ratio(30)", "efficiency_ratio(30)"),
    Gauge("atrrank200", "atr_pct_rank(200)", "atr_pct_rank(200)"),
    Gauge("volofvol90", "vol_of_vol(90)", "vol_of_vol(90)"),
    Gauge("adx14", "adx(14)", "adx(14)"),
    # ── participation
    Gauge("relvol50", "rel_volume(50)", "rel_volume(50)"),
    Gauge("volz96", "volume_z(96)", "volume_z(96)"),
    Gauge("takerfrac", "taker_buy_frac()", "1 - taker_buy_frac()"),
    # ── sequence
    Gauge("streak", "streak(close > prev(close, 1))",
          "streak(close < prev(close, 1))"),
    Gauge("since_break", "bars_since(close > donchian_hi(100))",
          "bars_since(close < donchian_lo(100))"),
    # ── the leader
    Gauge("corrbtc90", "corr_btc(90)", "corr_btc(90)"),
    Gauge("relbtc30", "rel_strength_btc(30)", _neg("rel_strength_btc(30)")),
    Gauge("btcret24", "btc_ret(24)", _neg("btc_ret(24)")),
    # ── the rest of the book
    Gauge("xsret30", "xs_rank(ret(30))", "1 - xs_rank(ret(30))"),
    Gauge("xsret6", "xs_rank(ret(6))", "1 - xs_rank(ret(6))"),
    Gauge("breadth50", "breadth(close > ema(50))",
          "1 - breadth(close > ema(50))"),
    Gauge("disp6", "dispersion(ret(6))", "dispersion(ret(6))"),
    # ── carry and positioning. Whether these are usable at a horizon is
    # MEASURED (thresholds.usable), never assumed: open interest begins
    # 2025-10 and the 4h discovery cut lands ~2025-05, so at 4h these
    # gauges are finite over none of the slice and drop out by themselves.
    Gauge("fundz360", "funding_z(360)", _neg("funding_z(360)")),
    Gauge("fundpct720", "funding_pct(720)", "1 - funding_pct(720)"),
    Gauge("basisz360", "basis_z(360)", _neg("basis_z(360)")),
    Gauge("oiret24", "oi_ret(24)", "oi_ret(24)"),
    Gauge("oidiv24", "oi_price_div(24)", _neg("oi_price_div(24)")),
    Gauge("acctz360", "ls_account_ratio_z(360)",
          _neg("ls_account_ratio_z(360)")),
    # ── the higher frame, below it only
    Gauge("htf4h_dist", 'htf("4h", (close - ema(50)) / ema(50))',
          _neg('htf("4h", (close - ema(50)) / ema(50))'), tfs=("1h", "15m")),
    # ── relationships: one market moving while another does not
    Gauge("rel_alts30", 'ret(30) - ref("alts", ret(30))',
          _neg('ret(30) - ref("alts", ret(30))')),
    Gauge("dom_vs_alts", 'ref("btcdom", ret(30)) - ref("alts", ret(30))',
          _neg('ref("btcdom", ret(30)) - ref("alts", ret(30))')),
] + _ref_gauges())


BOOL_PARTS: tuple[Part, ...] = (
    # ── states: what must also be true
    Part("st:above_ema50", "state", "st:above_ema50",
         "close > ema(50)", "close < ema(50)"),
    Part("st:above_ema200", "state", "st:above_ema200",
         "close > ema(200)", "close < ema(200)"),
    Part("st:ema_stack", "state", "st:ema_stack",
         "ema(20) > ema(50) and ema(50) > ema(200)",
         "ema(20) < ema(50) and ema(50) < ema(200)"),
    Part("st:above_vwap", "state", "st:above_vwap",
         "close > vwap(96)", "close < vwap(96)"),
    Part("st:htf_trend", "state", "st:htf_trend",
         'htf("4h", close > ema(50))', 'htf("4h", close < ema(50))'),
    # ── events: what fires
    Part("ev:donch20", "event", "ev:donch20",
         "close > donchian_hi(20)", "close < donchian_lo(20)"),
    Part("ev:donch50", "event", "ev:donch50",
         "close > donchian_hi(50)", "close < donchian_lo(50)"),
    Part("ev:donch100", "event", "ev:donch100",
         "close > donchian_hi(100)", "close < donchian_lo(100)"),
    Part("ev:swing10", "event", "ev:swing10",
         "close > swing_high(10)", "close < swing_low(10)"),
    Part("ev:bb_break", "event", "ev:bb_break",
         "close > bb_upper(20, 2.0)", "close < bb_lower(20, 2.0)"),
    Part("ev:keltner", "event", "ev:keltner",
         "close > keltner_upper(20, 2.0)", "close < keltner_lower(20, 2.0)"),
    Part("ev:ema50_cross", "event", "ev:ema50_cross",
         "close > ema(50) and prev(close, 1) < prev(ema(50), 1)",
         "close < ema(50) and prev(close, 1) > prev(ema(50), 1)"),
    Part("ev:macd_cross", "event", "ev:macd_cross",
         "macd(12, 26, 9) > 0 and prev(macd(12, 26, 9), 1) < 0",
         "macd(12, 26, 9) < 0 and prev(macd(12, 26, 9), 1) > 0"),
)

#: states that only mean something below the frame they read
_BOOL_TFS = {"st:htf_trend": ("1h", "15m")}


def _allowed(tfs: tuple, tf: str) -> bool:
    return not tfs or tf in tfs


def gauges_for(tf: str) -> list[Gauge]:
    return [g for g in GAUGES if _allowed(g.tfs, tf)]


def expressions(tf: str) -> list[str]:
    """Every scalar expression needing a measured percentile at `tf`."""
    seen, out = set(), []
    for g in gauges_for(tf):
        for e in (g.long, g.short):
            if e not in seen:
                seen.add(e)
                out.append(e)
    return out


def _fmt(v: float) -> str:
    """A threshold as the expression will carry it. Rounded so the rendered
    text stays readable and re-parses to the same number."""
    r = round(float(v), 6)
    return str(int(r)) if float(r).is_integer() else str(r)


def parts_for(tf: str, thresholds: dict) -> list[Part]:
    """The concrete catalogue at this horizon.

    `thresholds` maps expression -> {"p10","p25","p75","p90","usable"}. A
    gauge whose either side is missing or unusable contributes NOTHING: a
    one-sided condition is a market-direction bet wearing a strategy's
    clothes, and an unmeasured threshold is a guess.
    """
    out: list[Part] = []
    for g in gauges_for(tf):
        a, b = thresholds.get(g.long), thresholds.get(g.short)
        if not a or not b or not a.get("usable") or not b.get("usable"):
            continue
        for q in QUANTILES:
            name = f"p{int(round(q * 100))}"
            va, vb = a.get(name), b.get(name)
            if va is None or vb is None:
                continue
            op = "<" if q < 0.5 else ">"
            out.append(Part(
                key=f"{g.key}{op}{name}", kind="gauge", gauge=g.key,
                long=f"{g.long} {op} {_fmt(va)}",
                short=f"{g.short} {op} {_fmt(vb)}"))
    for p in BOOL_PARTS:
        if _allowed(_BOOL_TFS.get(p.key, ()), tf):
            out.append(p)
    return out


def part_requires(part: Part) -> tuple[str, ...]:
    """What data this part needs — DERIVED from the expressions.

    A declaration can be wrong; `dsl.data_requires` reads the features that
    are actually referenced, which is what decides whether a combination can
    be scored at all.
    """
    trees = [dsl.parse(part.long), dsl.parse(part.short)]
    return dsl.data_requires(*trees)
