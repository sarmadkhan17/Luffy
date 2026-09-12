"""The known-good rule, confined to the same window as the challengers.

A screen that prints "nothing survives" makes a claim about its own power
before it makes one about the market. Measured 2026-09-11: seven positioning
mechanisms found nothing at 333 days, and the incumbent rule confined to that
same window read p=2.5e-01 where it normally reads 3.5e-04. The family was
UNTESTED at that depth, not refuted — and without the control row the run
would have been written up as a fact about positioning.

Two decisions here differ from the obvious:

- the control runs on the incumbent's DECLARED 16, not on the discovery
  universe. On the discovery universe the incumbent itself reads p=2.3e-01,
  so it can calibrate nothing there. The question is "could an edge of known
  size register in a window this short", and the declared 16 is the only
  place that edge is known to have a size.
- the channel is DURATION-matched per horizon (100 bars at 4h is ~17 days),
  because a 100-bar channel at 15m is a different mechanism, not the same one
  measured faster.
"""
from __future__ import annotations

import logging

from .combo import MAX_PARTS, Combination
from .vocab import Part

log = logging.getLogger(__name__)

#: ~17 days of channel at every horizon
CHANNEL = {"4h": 100, "1h": 400, "15m": 1600}

#: the markets auth_donchian_breakout_trail declares, where its evidence is
INCUMBENT_UNIVERSE = (
    "BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT", "BNB/USDT", "DOGE/USDT",
    "LINK/USDT", "AVAX/USDT", "SUI/USDT", "AAVE/USDT", "NEAR/USDT",
    "FIL/USDT", "UNI/USDT", "TAO/USDT", "ZEC/USDT", "HYPE/USDT")

#: how to confine a rule to the window a series exists in. The comparison is
#: always true where the series is present and NaN — therefore False — where
#: it is not, so the conjunction simply cannot fire outside the window.
MASKS = {
    "funding": "funding_z(360) > -99",
    "basis": "basis_z(360) > -99",
    "open_interest": "oi_z(360) > -99",
    "ls_account_ratio": "ls_account_ratio_z(360) > -99",
    "ls_ratio": "ls_ratio_z(360) > -99",
    "taker_ratio": "taker_ratio_z(360) > -99",
}

#: fewer symbols than this and the control cannot calibrate anything.
#: `consistency_p` is a Bonferroni-corrected binomial tail, so the universe
#: size sets what it can DETECT: at 8 symbols the 0.01 bar is unreachable
#: even when every symbol clears the no-edge median, and a control that can
#: never read as powered would label every window UNDERPOWERED — silently
#: converting "no edge here" into "we cannot see here" for the whole search.
#: At 12, all-twelve-above-median reaches 7e-04 and the bar is reachable.
MIN_CONTROL_SYMBOLS = 12


def incumbent_universe(journal=None) -> list:
    """The declared 16, read from the book when it is available.

    Falls back to the recorded constant: a control that silently ran on a
    different universe than it claims would be worse than no control, and a
    TRUNCATED universe is exactly that — it reads as a weaker incumbent
    rather than as a smaller sample.
    """
    if journal is None:
        return list(INCUMBENT_UNIVERSE)
    try:
        import json
        rows = journal.query(
            "SELECT spec_json FROM strategies WHERE id=?",
            ("auth_donchian_breakout_trail",))
        if rows and rows[0].get("spec_json"):
            inc = (json.loads(rows[0]["spec_json"]).get("universe") or {}
                   ).get("include") or []
            if len(inc) >= MIN_CONTROL_SYMBOLS:
                if len(inc) != len(INCUMBENT_UNIVERSE):
                    log.warning(
                        f"incumbent control universe is {len(inc)} symbols, "
                        f"not the recorded {len(INCUMBENT_UNIVERSE)} — the "
                        f"book's declared set has changed")
                return list(inc)
            log.warning(f"book declares only {len(inc)} symbols for the "
                        f"incumbent; falling back to the recorded set, "
                        f"below {MIN_CONTROL_SYMBOLS} the control cannot "
                        f"calibrate")
    except Exception as e:                              # noqa: BLE001
        log.debug(f"incumbent universe unavailable: {e}")
    return list(INCUMBENT_UNIVERSE)


def mask_part(req: str) -> Part | None:
    """A part that is true wherever `req`'s series exists, and NaN before."""
    if req.startswith("ref:"):
        key = req.split(":", 1)[1]
        expr = f'ref("{key}", close) > -99'
    else:
        expr = MASKS.get(req)
    if not expr:
        return None
    return Part(key=f"mask:{req}", kind="state", gauge=f"mask:{req}",
                long=expr, short=expr)


def control_combo(tf: str, window: str, geo: str = "trail"):
    """The incumbent rule, confined to `window`. None when it cannot be."""
    n = CHANNEL.get(tf)
    if not n:
        return None
    parts = [Part(key=f"ev:control{n}", kind="event", gauge="ev:control",
                  long=f"close > donchian_hi({n})",
                  short=f"close < donchian_lo({n})")]
    if window and window != "ohlcv":
        for req in window.split("|"):
            m = mask_part(req)
            if m is None:
                return None
            parts.append(m)
    if len(parts) > MAX_PARTS:
        # a window needing more masks than a rule may hold cannot be
        # calibrated; results in it stay labelled as uncalibrated
        return None
    return Combination(parts=tuple(parts), tf=tf, geo=geo, round="control",
                       trigger=f"ev:control{n}")


def powered(res, max_p: float = 0.01) -> bool:
    """Could an edge of the incumbent's size register in this window?"""
    if not res:
        return False
    p = res.get("consistency_p")
    return p is not None and float(p) <= float(max_p)


def label(res, is_powered: bool) -> str:
    """What a result in this window is allowed to be called.

    A rule that registers is `scored` whatever the control says — power is
    about false negatives. A rule that does not register in a window where
    the known-good rule also cannot register is UNDERPOWERED, never no_edge.
    """
    if not res or res.get("verdict") != "scored":
        return (res or {}).get("verdict", "error")
    p = res.get("consistency_p")
    if p is not None and float(p) <= 0.01:
        return "scored"
    return "no_edge" if is_powered else "underpowered"
