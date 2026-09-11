"""Pine Forge — compiles strategy genomes into Pine Script v5.

Deterministic, battle-tested templates per family; genome params inject
as numeric literals (no string interpolation into logic). An optional
LLM pass may refine the entry block given the hypothesis, but refined
code must pass the same lint gate as templates — otherwise the template
version ships. Walk-forward folds are generated as script variants with
hardcoded date windows so no UI input fiddling is ever needed.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..core.config import ROOT
from ..core.journal import Journal
from ..strategy.genome import PARAM_DEFAULTS as _PARAM_DEFAULTS

log = logging.getLogger(__name__)

PINE_DIR = ROOT / "data" / "pine"

_HEADER = """//@version=5
// LUFFY forged · strategy_id={sid} · family={family} · v{version}
// hypothesis: {hypothesis}
strategy("LUFFY {family}", overlay={overlay}, initial_capital=10000, default_qty_type=strategy.percent_of_equity, default_qty_value=10, commission_type=strategy.commission.percent, commission_value=0.05, pyramiding=0)
"""

_WINDOW = """
// ── walk-forward window (fold variants hardcode these) ──
tStart = timestamp({sy}, {sm}, {sd}, 00, 00)
tEnd   = timestamp({ey}, {em}, {ed}, 00, 00)
inWin  = time >= tStart and time <= tEnd
"""

_EXITS_ATR = """
// ── ATR protective exits ──
// Multipliers come from config.yaml risk: (stop_loss_atr_mult /
// take_profit_atr_mult) so the Pine tester sizes risk the same way the live
// engine does. Templates used to hardcode their own (2.5/4.0, 1.5/3.0,
// 2.5/3.5, 2.0/…), none of which matched the traded system.
SL_ATR = {sl_atr}
TP_ATR = {tp_atr}
atrV = ta.atr(14)
var float slP = na
var float tpP = na
if strategy.position_size > 0 and not na(slP)
    strategy.exit("LX", "L", stop=slP, limit=tpP)
if strategy.position_size < 0 and not na(slP)
    strategy.exit("SX", "S", stop=slP, limit=tpP)
"""

# Each template: overlay flag + body. Placeholders are Python format
# fields filled from validated genome params only.
TEMPLATES: dict[str, dict] = {
    "ema_trend": {
        "overlay": "true",
        "body": """
// lengths are FIXED at 20/50/200 to mirror library.eval_ema_trend exactly.
// They are not genes: FAMILY_GENE_SPECS['ema_trend'] has no fast_len/slow_len.
fastE = ta.ema(close, 20)
slowE = ta.ema(close, 50)
trendE = ta.ema(close, 200)
[dPlus, dMinus, adxV] = ta.dmi(14, 14)
pull = {pullback_atr} * atrV
longStack = close > fastE and fastE > slowE and slowE > trendE
shortStack = close < fastE and fastE < slowE and slowE < trendE
longEntry = inWin and adxV >= {adx_min} and longStack and fastE - close <= pull
shortEntry = inWin and adxV >= {adx_min} and shortStack and close - fastE <= pull
if longEntry and strategy.position_size == 0
    strategy.entry("L", strategy.long)
    slP := close - SL_ATR * atrV
    tpP := close + TP_ATR * atrV
if shortEntry and strategy.position_size == 0
    strategy.entry("S", strategy.short)
    slP := close + SL_ATR * atrV
    tpP := close - TP_ATR * atrV
if strategy.position_size > 0 and close < slowE
    strategy.close("L")
if strategy.position_size < 0 and close > slowE
    strategy.close("S")
""",
    },
    "vwap_fade": {
        "overlay": "false",
        "body": """
tp = hlc3
cVol = math.sum(volume, {anchor_bars})
cPV = math.sum(tp * volume, {anchor_bars})
rVwap = cPV / cVol
dev = (close - rVwap) / rVwap
zRaw = (dev - ta.sma(dev, {anchor_bars})) / ta.stdev(dev, {anchor_bars})
entryLong = inWin and zRaw <= -{z_entry}
entryShort = inWin and zRaw >= {z_entry}
if entryLong and strategy.position_size == 0
    strategy.entry("L", strategy.long)
if entryShort and strategy.position_size == 0
    strategy.entry("S", strategy.short)
barsHeld = strategy.opentrades > 0 ? bar_index - strategy.opentrades.entry_bar_index(strategy.opentrades - 1) : 0
if strategy.position_size > 0 and (close >= rVwap or barsHeld >= {max_hold_bars})
    strategy.close("L")
if strategy.position_size < 0 and (close <= rVwap or barsHeld >= {max_hold_bars})
    strategy.close("S")
""",
    },
    "breakout_retest": {
        "overlay": "true",
        "body": """
lookN = {range_lookback}
refHi = ta.highest(high, lookN)[1]
refLo = ta.lowest(low, lookN)[1]
volOk = volume > {vol_mult} * ta.sma(volume, 48)
tol = {retest_atr} * atrV
brokeUp = inWin and ta.crossover(close, refHi)
brokeDn = inWin and ta.crossunder(close, refLo)
var bool waitL = false
var float lvlL = na
var int ageL = 0
var bool waitS = false
var float lvlS = na
var int ageS = 0
if brokeUp and volOk
    waitL := true
    lvlL := refHi
    ageL := 0
if brokeDn and volOk
    waitS := true
    lvlS := refLo
    ageS := 0
waitL := waitL and not brokeUp ? true : waitL
ageL := waitL ? ageL + 1 : ageL
ageS := waitS ? ageS + 1 : ageS
retestL = waitL and ageL <= 12 and low <= lvlL + tol and close > lvlL
retestS = waitS and ageS <= 12 and high >= lvlS - tol and close < lvlS
if retestL and strategy.position_size == 0
    strategy.entry("L", strategy.long)
    slP := close - SL_ATR * atrV
    tpP := close + TP_ATR * atrV
if retestS and strategy.position_size == 0
    strategy.entry("S", strategy.short)
    slP := close + SL_ATR * atrV
    tpP := close - TP_ATR * atrV
if retestL or ageL > 12
    waitL := false
if retestS or ageS > 12
    waitS := false
""",
    },
    "sweep_reversal": {
        "overlay": "true",
        "body": """
swingHi = ta.highest(high, 60)[{max_reclaim_bars} + 1]
swingLo = ta.lowest(low, 60)[{max_reclaim_bars} + 1]
pierceLo = low < swingLo * (1 - {min_sweep_frac})
pierceHi = high > swingHi * (1 + {min_sweep_frac})
var int ageLo = 9999
var int ageHi = 9999
ageLo := pierceLo ? 0 : ageLo + 1
ageHi := pierceHi ? 0 : ageHi + 1
spring = inWin and ageLo >= 1 and ageLo <= {max_reclaim_bars} and close > swingLo
upthrust = inWin and ageHi >= 1 and ageHi <= {max_reclaim_bars} and close < swingHi
if spring and strategy.position_size == 0
    strategy.entry("L", strategy.long)
    slP := low - SL_ATR * atrV
    tpP := close + TP_ATR * atrV
if upthrust and strategy.position_size == 0
    strategy.entry("S", strategy.short)
    slP := high + SL_ATR * atrV
    tpP := close - TP_ATR * atrV
""",
    },
    "rotation_momo": {
        "overlay": "false",
        "body": """
btcC = request.security("BINANCE:BTCUSDT", timeframe.period, close, lookahead=barmerge.lookahead_off)
btcRet = btcC / btcC[{lag_lookback}] - 1
myRet = close / close[{lag_lookback}] - 1
laggards = myRet > 0 and myRet < btcRet * 0.7
entry = inWin and btcRet >= {btc_ret_1h_min} and laggards
if entry and strategy.position_size == 0
    strategy.entry("L", strategy.long)
    slP := close - SL_ATR * atrV
    tpP := close + TP_ATR * atrV
if strategy.position_size > 0 and btcRet < 0
    strategy.close("L")
""",
    },
    "rsi_extreme": {
        "overlay": "false",
        "body": """
r = ta.rsi(close, {rsi_len})
buySig = inWin and ta.crossover(r, {os_level})
sellSig = inWin and ta.crossunder(r, {ob_level})
if buySig and strategy.position_size == 0
    strategy.entry("L", strategy.long)
    slP := close - SL_ATR * atrV
    tpP := close + TP_ATR * atrV
if sellSig and strategy.position_size == 0
    strategy.entry("S", strategy.short)
    slP := close + SL_ATR * atrV
    tpP := close - TP_ATR * atrV
""",
    },
    "ma_cross": {
        "overlay": "true",
        "body": """
fastM = ta.ema(close, {fast_len})
slowM = ta.ema(close, {slow_len})
bullCross = inWin and ta.crossover(fastM, slowM)
bearCross = inWin and ta.crossunder(fastM, slowM)
if bullCross
    strategy.entry("L", strategy.long)
    slP := close - SL_ATR * atrV
    tpP := close + TP_ATR * atrV
if bearCross
    strategy.entry("S", strategy.short)
    slP := close + SL_ATR * atrV
    tpP := close - TP_ATR * atrV
""",
    },
    "bb_fade": {
        "overlay": "true",
        "body": """
basis = ta.sma(close, {bb_len})
bandW = {bb_k} * ta.stdev(close, {bb_len})
upperB = basis + bandW
lowerB = basis - bandW
piercedUp = close[1] > upperB[1] and close < upperB
piercedDn = close[1] < lowerB[1] and close > lowerB
sellSig = inWin and piercedUp
buySig = inWin and piercedDn
if buySig and strategy.position_size == 0
    strategy.entry("L", strategy.long)
    slP := close - SL_ATR * atrV
    tpP := basis
if sellSig and strategy.position_size == 0
    strategy.entry("S", strategy.short)
    slP := close + SL_ATR * atrV
    tpP := basis
""",
    },
}

# Sane fallbacks live in strategy.genome (single source of truth shared
# with the evaluators); imported above as _PARAM_DEFAULTS.

_PARAM_MAP = {
    # family -> template field -> genome param name
    "ema_trend": {"adx_min": "adx_min", "pullback_atr": "pullback_atr"},
    "vwap_fade": {"anchor_bars": "anchor_bars", "z_entry": "z_entry",
                  "max_hold_bars": "max_hold_bars"},
    "breakout_retest": {"range_lookback": "range_lookback",
                        "vol_mult": "vol_mult",
                        "retest_atr": "retest_atr"},
    "sweep_reversal": {"max_reclaim_bars": "max_reclaim_bars",
                       "min_sweep_frac": "min_sweep_frac"},
    "rotation_momo": {"btc_ret_1h_min": "btc_ret_1h_min",
                      "lag_lookback": "lag_lookback"},
    "rsi_extreme": {"rsi_len": "rsi_len", "os_level": "os_level",
                    "ob_level": "ob_level"},
    "ma_cross": {"fast_len": "fast_len", "slow_len": "slow_len"},
    "bb_fade": {"bb_len": "bb_len", "bb_k": "bb_k"},
}


def lint(code: str) -> list[str]:
    """Cheap compile-risk screen. Empty = acceptable."""
    errs = []
    if "//@version=5" not in code:
        errs.append("missing version pragma")
    if "strategy(" not in code:
        errs.append("missing strategy() declaration")
    for a, b in (("(", ")"), ("[", "]")):
        if code.count(a) != code.count(b):
            errs.append(f"unbalanced {a}{b}")
    if re.search(r"\{[a-z_]+\}", code):
        errs.append("unfilled placeholder")
    for line in code.splitlines():
        if len(line) > 400:
            errs.append(f"overlong line: {line[:40]}…")
            break
    if re.search(r"request\.security\(", code) and \
            "BINANCE:BTCUSDT" not in code:
        errs.append("unexpected security call")
    return errs


def _fmt_window(win: tuple[str, str] | None) -> str:
    if not win:
        return _WINDOW.format(sy=1970, sm=1, sd=1, ey=2069, em=12, ed=31)
    s = datetime.fromisoformat(win[0])
    e = datetime.fromisoformat(win[1])
    return _WINDOW.format(sy=s.year, sm=s.month, sd=s.day,
                          ey=e.year, em=e.month, ed=e.day)


def _risk_atr() -> tuple[float, float]:
    """(stop_loss_atr_mult, take_profit_atr_mult) from config.yaml risk:.

    The Pine tester is only meaningful as evidence if it risks the way the
    live engine risks. Falls back to the shipped defaults if config is
    unreadable — forging must never raise.
    """
    try:
        from ..core.config import load_config
        r = load_config().get("risk", {}) or {}
        return (float(r.get("stop_loss_atr_mult", 2.5)),
                float(r.get("take_profit_atr_mult", 4.5)))
    except Exception:
        return (2.5, 4.5)


def forge(genome, window: tuple[str, str] | None = None,
          version: int = 1) -> str:
    """Genome → Pine v5 source. Deterministic; never raises on params —
    they arrive pre-validated by Genome.validate."""
    fam = genome.family
    tpl = TEMPLATES.get(fam)
    if tpl is None:
        raise ValueError(f"no pine template for family '{fam}'")
    body = tpl["body"]
    mapping = _PARAM_MAP.get(fam)
    if mapping is None:
        raise ValueError(f"no param mapping for family '{fam}'")
    defaults = _PARAM_DEFAULTS.get(fam, {})
    fields = {}
    for tmpl_key, gene_key in mapping.items():
        val = genome.params.get(gene_key if gene_key else tmpl_key)
        if val is None:
            val = defaults.get(tmpl_key)
        if isinstance(val, float) and val == int(val):
            val = int(val)          # cleaner literals
        fields[tmpl_key] = val
    body = body.format(**fields)

    header = (_HEADER.format(sid=genome.strategy_id, family=fam,
                             version=version,
                             hypothesis=(genome.hypothesis or "")[:120]
                             .replace("\n", " "),
                             overlay=tpl["overlay"]))
    if fam == "vwap_fade":
        exits = ""                       # time-based exit lives in its body
    else:
        exits = _EXITS_ATR.format(sl_atr=_risk_atr()[0],
                                  tp_atr=_risk_atr()[1])
    return header + _fmt_window(window) + exits + body


def fold_windows(n_folds: int = 3, span_days: int = 360) -> list[tuple[str, str]]:
    """Chronological train/test windows ending yesterday (UTC)."""
    end = datetime.now(timezone.utc).date() - timedelta(days=1)
    start_all = end - timedelta(days=span_days)
    fold_len = span_days // (n_folds + 1)
    out = []
    for i in range(n_folds):
        test_start = start_all + timedelta(days=fold_len * (i + 1))
        test_end = test_start + timedelta(days=fold_len)
        out.append((test_start.isoformat(), test_end.isoformat()))
    return out


def save(strategy_id: str, code: str, variant: str = "full") -> Path:
    d = PINE_DIR / strategy_id
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{variant}.pine"
    p.write_text(code)
    return p


def refine_with_llm(genome, base_code: str, llm) -> tuple[str, bool]:
    """Optional brain pass: tighten the entry block per the hypothesis.
    Refined output must pass lint; any failure returns the template."""
    if llm is None or not getattr(llm, "available", False):
        return base_code, False
    prompt = (
        "Below is a working Pine Script v5 strategy generated from a "
        "trading genome. Improve ONLY the entry conditions to better "
        "express this hypothesis, keeping everything else byte-identical "
        "(window filter, exits, variable names, structure):\n\n"
        f"HYPOTHESIS: {genome.hypothesis}\nPARAMS: "
        f"{json.dumps(genome.params)}\n\n```pine\n{base_code}\n```\n\n"
        'Reply as one JSON object: {"code":"<full improved script>"} '
        "or {\"skip\":true}. Do not change strategy() metadata lines.")
    try:
        raw = llm.chat_json(prompt, deep=False, purpose="tv")
    except Exception as e:
        log.warning(f"pine refine llm failed: {e}")
        return base_code, False
    code = (raw or {}).get("code")
    if not code or raw.get("skip"):
        return base_code, False
    # guardrails: metadata + structure must survive the edit
    must_keep = [ln for ln in base_code.splitlines()
                 if ln.startswith(("//@version", "strategy(", "tStart",
                                   "tEnd", "inWin"))]
    if all(ln in code for ln in must_keep) and not lint(code):
        return code, True
    log.warning("pine refinement rejected (lint/structure) — template kept")
    return base_code, False


def sha(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()[:16]


def forge_and_store(genome, journal: Journal, llm=None,
                    n_folds: int = 3) -> dict:
    """Full pipeline for one genome → files + journal event. Returns
    manifest consumed later by the TV harness queue."""
    # full-run window pinned to end-yesterday (same span as the folds):
    # an unbounded window re-evaluates as live candles form, flipping
    # marginal verdicts between runs and burning budget
    end = datetime.now(timezone.utc).date() - timedelta(days=1)
    start_all = end - timedelta(days=360)
    full = forge(genome, version=1,
                 window=(start_all.isoformat(), end.isoformat()))
    full, refined = refine_with_llm(genome, full, llm)
    errs = lint(full)
    if errs:
        raise ValueError(f"forged pine failed lint: {errs}")
    paths = {}
    save(genome.strategy_id, full, "full")
    paths["full"] = sha(full)
    folds = []
    for i, w in enumerate(fold_windows(n_folds), 1):
        fv = forge(genome, window=w, version=1)
        save(genome.strategy_id, fv, f"fold{i}")
        folds.append({"variant": f"fold{i}",
                      "window": list(w), "sha": sha(fv)})
    manifest = {"strategy_id": genome.strategy_id, "family": genome.family,
                "params": dict(genome.params), "refined": refined,
                "files": {"full": paths["full"], "folds": folds}}
    journal.log_brain_event("pine_forged", genome.strategy_id, {
        "sha_full": paths["full"], "refined": refined,
        "folds": n_folds})
    return manifest
