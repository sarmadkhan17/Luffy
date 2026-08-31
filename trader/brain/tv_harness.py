"""TV Harness — REAL TradingView Strategy Tester results via browser.

Free-plan compatible: uses only Pine Editor + Strategy Tester (no
alerts/webhooks). Persistent browser profile means you log in once;
sessions persist for months. Every run is cached by script hash,
budget-capped daily, screenshot-on-failure, and degrades gracefully —
the gauntlet falls back to the Yahoo judge when this is unhealthy.

Selectors live in ONE dict; when TradingView ships a redesign, fix
SELECTORS and nothing else.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from ..core.config import ROOT
from ..core.journal import Journal

log = logging.getLogger(__name__)

PROFILE_DIR = ROOT / "data" / "tv_profile"
CACHE_PATH = ROOT / "data" / "tv_cache.json"
SHOT_DIR = ROOT / "logs"

CHART_URL = "https://www.tradingview.com/chart/?symbol=BINANCE%3ABTCUSDT"


def chart_url(symbol: str = "BINANCE:BTCUSDT") -> str:
    """Chart URL for a specific symbol.

    backtest() has always ACCEPTED a `symbol`, cached under it and journaled
    it — but then navigated to the hardcoded CHART_URL, so every run actually
    executed on BTCUSDT no matter what was requested. Results were therefore
    mislabelled, and any family whose logic references BTC (rotation_momo)
    compared BTC against itself.
    """
    from urllib.parse import quote
    return f"https://www.tradingview.com/chart/?symbol={quote(symbol, safe='')}"

SELECTORS = {
    # 2026-08 UI: Pine Editor is a floating overlay launched from the
    # right toolbar; editor surface is Monaco; tester tab appears in the
    # same overlay after a strategy is added.
    "pine_toolbar_btn": "button[aria-label='Pine']",
    "editor_surface": ".monaco-editor .view-lines, .monaco-editor",
    "add_to_chart": "button:has-text('Add to chart')",
    "chart_loaded": "div[class*='chart-container'], canvas",
}


def _find_tester_tab(page):
    """Strategy Tester tab in the overlay panel (role/text agnostic)."""
    for sel in ("[role='tab']:has-text('Strategy Tester')",
                "button:has-text('Strategy Tester')",
                "div:has-text('Strategy Tester')"):
        try:
            loc = page.locator(sel).first
            if loc.count() and loc.is_visible():
                return loc
        except Exception:
            continue
    return None


def sha_code(code: str, symbol: str = "BINANCE:BTCUSDT", tf: str = "1h") -> str:
    # semantic hash: comments carry provenance only (strategy_id,
    # hypothesis) and cannot affect tester output — exclude them so
    # re-forged identical strategies reuse cached verdicts
    core = "\n".join(
        ln for ln in code.splitlines()
        if ln.strip() and not ln.strip().startswith("//"))
    return hashlib.sha256(f"{core}|{symbol}|{tf}".encode()).hexdigest()[:16]


def parse_metrics(text: str) -> dict:
    """Tolerant label→value scrape. 2026-08 free-plan tester ('Key
    stats') exposes Total PnL + Max drawdown + Profitable trades only;
    legacy Performance-Summary labels kept as fallbacks."""
    flat = re.sub(r"[ \t]+", " ", text)
    # TV renders negative numbers with the typographic minus U+2212
    flat = flat.replace("\u2212", "-").replace("–", "-")
    out = {}

    def grab(patterns, cast=str):
        for pat in patterns:
            m = re.search(pat, flat, re.I | re.S)
            if m:
                return cast(m.group(1))
        return None

    num = r"([+-]?[\d,]+\.?\d*)\s*(?:USD|USDT|\$)?"
    # ── new Key-stats labels ────────────────────────────────────────────
    out["net_profit_usd"] = grab(
        [rf"Total PnL\s*\n?\s*{num}",
         rf"Total Net Profit\s*\n?\s*{num}",
         rf"\bNet Profit\s*\n?\s*{num}"],
        lambda v: float(v.replace(",", "")))
    out["net_profit_pct"] = grab(
        [rf"Total PnL\s*\n?\s*[+-]?[\d,.]+\s*(?:USD|USDT|\$)?\s*"
         rf"([+-]?[\d.]+)\s*%",
         rf"Total Net Profit[^%\n]*\n?.*?(-?[\d.]+)\s*%",
         rf"Net Profit.*?(-?[\d.]+)\s*%"],
        lambda v: float(v))
    out["max_drawdown_usd"] = grab(
        [rf"Max drawdown\s*\n?\s*{num}"],
        lambda v: float(v.replace(",", "")))
    out["max_drawdown_pct"] = grab(
        [rf"Max drawdown\s*\n?\s*[+-]?[\d,.]+\s*(?:USD|USDT|\$)?\s*"
         rf"([+-]?[\d.]+)\s*%",
         rf"Max (?:Equity |Strategy )?Drawdown\s*\n?\s*"
         rf"[+-]?[\d,.]+\s*(?:USD)?\s*([+-]?[\d.]+)\s*%",
         rf"Max Drawdown[^%\n]*\n?.*?(-?[\d.]+)\s*%"],
        lambda v: float(v))
    out["win_rate_pct"] = grab(
        [rf"Profitable trades\s*\n?\s*([+-]?[\d.]+)",
         rf"Percent Profitable\s*\n?\s*(-?[\d.]+)"],
        lambda v: float(v))
    # ── legacy/deep-report labels (paid tiers) ──────────────────────────
    out["total_trades"] = grab(
        [rf"Total Closed Trades\s*\n?\s*{num}",
         rf"Total Trades\s*\n?\s*{num}"],
        lambda v: int(float(v.replace(",", ""))))
    out["profit_factor"] = grab(
        [rf"Profit Factor\s*\n?\s*(-?[\d.]+)"], lambda v: float(v))
    out["sharpe"] = grab([rf"Sharpe Ratio\s*\n?\s*(-?[\d.]+)"],
                         lambda v: float(v))
    out["buy_hold_pct"] = grab(
        [rf"Buy & Hold Return\s*\n?\s*(-?[\d.]+)\s*%"], lambda v: float(v))
    return {k: v for k, v in out.items() if v is not None}


class TVHarness:
    def __init__(self, journal: Journal, cfg: dict):
        self.journal = journal
        c = cfg.get("tv_harness", {})
        self.daily_budget = int(c.get("daily_runs", 20))
        # development switch: unlimited tester runs until Luffy finalizes
        self.budget_enabled = bool(c.get("budget_enabled", True))
        self.run_timeout_s = int(c.get("run_timeout_s", 90))

    # ── cache ────────────────────────────────────────────────────────────
    @staticmethod
    def _cache_load() -> dict:
        try:
            return json.loads(CACHE_PATH.read_text())
        except Exception:
            return {}

    @staticmethod
    def _cache_save(cache: dict) -> None:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        CACHE_PATH.write_text(json.dumps(cache, indent=1))

    def cached(self, key: str) -> dict | None:
        return self._cache_load().get(key)

    # ── budget + health ──────────────────────────────────────────────────
    def runs_today(self) -> int:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        n = self.journal.query(
            "SELECT COUNT(*) n FROM brain_events WHERE kind='tv_backtest' "
            "AND ts >= ?", (today,))[0]["n"]
        return n

    def health(self) -> dict:
        recent = self.journal.query(
            "SELECT detail FROM brain_events WHERE kind='tv_backtest' "
            "ORDER BY ts DESC LIMIT 10")
        oks = sum(1 for r in recent
                  if json.loads(r["detail"]).get("ok"))
        state = "down" if not recent else \
            "healthy" if oks >= len(recent) * 0.7 else "degraded"
        return {"state": state, "runs_today": self.runs_today(),
                "budget": self.daily_budget}

    # ── the run ──────────────────────────────────────────────────────────
    def backtest(self, code: str, symbol: str = "BINANCE:BTCUSDT",
                 tf: str = "1h", force: bool = False) -> dict:
        key = sha_code(code, symbol, tf)
        if not force:
            hit = self.cached(key)
            if hit:
                return {**hit, "cached": True}
        if self.budget_enabled and \
                self.runs_today() >= self.daily_budget:
            log.warning("TV harness daily budget exhausted")
            return {"ok": False, "skipped": "daily_budget"}

        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
        PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        result: dict = {"ok": False}
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                str(PROFILE_DIR), headless=True,
                viewport={"width": 1680, "height": 980})
            page = ctx.new_page()
            try:
                ctx.grant_permissions(
                    ["clipboard-read", "clipboard-write"],
                    origin="https://www.tradingview.com")
            except Exception:
                pass                     # best-effort; keyboard fallback below
            t0 = time.time()
            try:
                page.goto(chart_url(symbol), timeout=45_000)
                page.wait_for_selector(SELECTORS["chart_loaded"],
                                       timeout=30_000)
                self._set_timeframe(page, tf)
                self._open_pine_editor(page)
                self._paste_script(page, code)
                self._add_to_chart(page)
                metrics = self._read_tester(page)
                result = {"ok": bool(metrics), "metrics": metrics,
                          "symbol": symbol, "tf": tf,
                          "seconds": round(time.time() - t0, 1)}
            except Exception as e:
                shot = SHOT_DIR / f"tv_fail_{int(time.time())}.png"
                try:
                    page.screenshot(path=str(shot), full_page=False)
                except Exception:
                    pass
                result = {"ok": False, "error": str(e)[:300],
                          "screenshot": str(shot)}
            finally:
                ctx.close()

        self.journal.log_brain_event("tv_backtest", key[:16], {
            "ok": result.get("ok"), "symbol": symbol, "tf": tf,
            "error": result.get("error"), "runs_used": True})
        if result.get("ok"):
            cache = self._cache_load()
            cache[key] = result
            self._cache_save(cache)
        return result

    # ── UI interactions (selectors may need repair as TV evolves) ───────
    def _set_timeframe(self, page, tf: str) -> None:
        """TV quick-interval: type the tf while chart has focus."""
        try:
            page.locator(SELECTORS["chart_loaded"]).first.click(
                timeout=5000, position={"x": 600, "y": 400})
            page.keyboard.type(tf, delay=60)
            page.keyboard.press("Enter")
            page.wait_for_timeout(2500)
            log.info(f"timeframe set to {tf} via quick-input")
        except Exception as e:
            log.debug(f"timeframe set skipped ({e}); keeping chart default")

    def _open_pine_editor(self, page) -> None:
        page.locator(SELECTORS["pine_toolbar_btn"]).first.click(
            timeout=10000)
        page.wait_for_selector(".monaco-editor", timeout=20000)
        page.wait_for_timeout(1000)

    def _paste_script(self, page, code: str) -> None:
        """Replace editor content deterministically and VERIFY.
        keyboard.insert_text is NOT safe here: TradingView's Monaco
        auto-indents inserted newlines, stacking indentation until the
        script is mangled. A real clipboard paste (Ctrl+V) inserts
        verbatim, so we go through the clipboard and then verify."""
        def editor_text() -> str:
            # Monaco virtualizes the DOM (.view-lines holds only rendered
            # lines), so long scripts read back truncated and fail
            # verification. The JS model always has the exact content.
            try:
                v = page.evaluate(
                    "() => { const ms = window.monaco || "
                    "(window.tradingView && window.tradingView.monaco);"
                    " if (ms && ms.editor) { const m = "
                    "ms.editor.getModels()[0]; if (m) return m.getValue(); }"
                    " return null; }")
                if v:
                    return v
            except Exception:
                pass
            try:
                # ensure the head of the script is rendered before any
                # DOM-based read (virtualization); wait out the re-render
                page.keyboard.press("Control+Home")
                page.wait_for_timeout(400)
                t = page.locator(".monaco-editor .view-lines").first \
                    .inner_text(timeout=4000)
                # Monaco renders spaces as U+00A0 in view-lines
                return t.replace("\xa0", " ")
            except Exception:
                return ""

        def looks_clean() -> bool:
            t = editor_text()
            if not t.strip():
                return False
            # head may be scrolled out of the virtualized DOM — accept
            # version marker anywhere in the captured text, but require
            # exactly one strategy declaration
            if "//@version=5" not in t[:4000]:
                return False
            if t.count("strategy(") != 1:
                return False
            # auto-indent mangling leaves absurdly deep indents
            if any(len(ln) - len(ln.lstrip()) > 12
                   for ln in t.splitlines()):
                return False
            return True

        surface = page.locator(SELECTORS["editor_surface"]).first
        for attempt in range(2):
            page.keyboard.press("Escape")       # close suggest/hover widgets
            surface.click(timeout=8000)
            page.wait_for_timeout(300)
            # Monaco keeps a JS model — setValue is atomic and cannot
            # merge with stale content the way keypress-clearing can
            try:
                page.evaluate(
                    "() => { const ms = window.monaco || "
                    "(window.tradingView && window.tradingView.monaco);"
                    " if (ms && ms.editor) { const m = "
                    "ms.editor.getModels()[0]; if (m) { m.setValue(''); "
                    "return true; } } return false; }")
            except Exception:
                pass
            page.keyboard.press("Control+A")
            page.keyboard.press("Delete")
            page.wait_for_timeout(200)
            pasted = False
            try:
                page.evaluate("c => navigator.clipboard.writeText(c)", code)
                page.keyboard.press("Control+V")
                pasted = True
            except Exception:
                page.keyboard.insert_text(code)   # fallback
            page.wait_for_timeout(1800)           # settle + TV lint
            if looks_clean():
                self._assert_no_compile_errors(page)
                return
            # capture what actually landed for diagnosis
            t = editor_text()
            lines = t.splitlines()
            log.error(f"paste verify failed (attempt {attempt}): "
                      f"editor has {len(lines)} lines; head="
                      f"{lines[:2] if lines else 'EMPTY'!r} "
                      f"strategy_count={t.count('strategy(')}")
            shot = SHOT_DIR / f"paste_fail_{int(time.time())}.png"
            try:
                page.screenshot(path=str(shot), full_page=False)
            except Exception:
                pass
            if attempt == 0 and not pasted:
                continue
        raise RuntimeError("pine editor did not take the pasted script "
                           "cleanly (stale content or focus loss)")

    def _assert_no_compile_errors(self, page) -> None:
        """Fail fast with the compiler's own message instead of wasting
        an add-to-chart + tester wait on a broken script."""
        try:
            banner = page.locator(
                "text=/of \\d+ problem/").first
            if banner.count() and banner.is_visible(timeout=1500):
                detail = page.locator(
                    "[class*='error'], [class*='problem']").all_inner_texts()
                raise RuntimeError(
                    "pine compile error: "
                    + "; ".join(t.strip() for t in detail[:3])[:200])
        except RuntimeError:
            raise
        except Exception:
            pass                                  # no banner = fine

    def _add_to_chart(self, page) -> None:
        btn = page.locator(SELECTORS["add_to_chart"]).first
        btn.click(timeout=10000)
        page.wait_for_timeout(4000)          # compile + render on chart

    def _read_tester(self, page) -> dict:
        # one-time "tester menu has moved" notice
        try:
            g = page.locator("button:has-text('Got it')").first
            if g.count() and g.is_visible(timeout=2000):
                g.click(timeout=3000)
                page.wait_for_timeout(800)
        except Exception:
            pass
        # tester auto-opens as the left "strategy report" panel after a
        # strategy is added; no tab click needed on 2026-08 UI
        page.wait_for_timeout(1500)
        text = page.inner_text("body")
        metrics = parse_metrics(text)
        # free plan gates the deep report ("Upgrade to get full access");
        # stray digits near those labels are false matches — drop them
        if "Upgrade to get full access" in text:
            for k in ("profit_factor", "total_trades", "sharpe",
                      "buy_hold_pct"):
                metrics.pop(k, None)
        if "net_profit_pct" not in metrics and \
                "net_profit_usd" not in metrics:
            raise RuntimeError("tester panel produced no metrics")
        return metrics

    # ── assisted first login ─────────────────────────────────────────────
    def assisted_login(self) -> None:
        """Run once interactively: log into TradingView yourself; the
        persistent profile keeps the session."""
        from playwright.sync_api import sync_playwright
        PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                str(PROFILE_DIR), headless=False,
                viewport={"width": 1400, "height": 900})
            page = ctx.new_page()
            page.goto("https://www.tradingview.com/", timeout=60_000)
            input("Log into TradingView in the opened browser, then press "
                  "Enter here… ")
            ctx.close()
        log.info("TV profile saved")


# ── manifest evaluation (walk-forward on real tester data) ───────────────
# Families whose Pine logic references another instrument must NOT be tested
# on that same instrument. rotation_momo pulls BINANCE:BTCUSDT via
# request.security and requires `myRet < btcRet * 0.7` while `myRet > 0` — on a
# BTC chart btcRet == myRet, so the condition is unsatisfiable and the family
# could never produce a single TV trade.
FAMILY_TEST_SYMBOL = {
    "rotation_momo": "BINANCE:ETHUSDT",
}


def test_symbol_for(family: str, default: str = "BINANCE:BTCUSDT") -> str:
    return FAMILY_TEST_SYMBOL.get(family, default)


def evaluate_manifest(harness: TVHarness, manifest: dict,
                      symbol: str = "BINANCE:BTCUSDT",
                      tf: str = "1h",
                      min_oos_trades: int = 5) -> dict:
    """Run full + fold variants through the real tester → same verdict
    shape as trader.brain.tv.TVClient.walk_forward, so the gauntlet can
    consume either judge interchangeably."""
    from ..brain.pine import PINE_DIR
    sid = manifest["strategy_id"]
    symbol = test_symbol_for(manifest.get("family", ""), symbol)
    full_path = PINE_DIR / sid / "full.pine"
    if not full_path.exists():
        return {"valid": False, "reason": "no forged script"}
    res = harness.backtest(full_path.read_text(), symbol, tf)
    if not res.get("ok"):
        return {"valid": False, "reason":
                res.get("error") or res.get("skipped") or "harness failed",
                "harness": harness.health()}
    folds = []
    for i in range(1, 4):
        fp = PINE_DIR / sid / f"fold{i}.pine"
        if not fp.exists():
            continue
        fr = harness.backtest(fp.read_text(), symbol, tf)
        folds.append(fr.get("metrics") or {})
    m = res["metrics"]
    pos_folds = sum(1 for fm in folds
                    if (fm.get("net_profit_usd") or 0) > 0)
    # Free-plan reality: the tester exposes PnL + drawdown (+ per-fold
    # PnL) only. Trade-count and sharpe/winrate gates are enforced by
    # the internal backtest stage in the gauntlet, which has full
    # metrics — so here they apply only when the data exists (paid).
    checks = {
        "profitable_oos": (m.get("net_profit_pct") or -100) > 0,
        "drawdown_ok": abs(m.get("max_drawdown_pct") or 100) <= 20.0,
        "folds_positive": pos_folds >= max(1, len(folds) // 2 + 1),
    }
    if m.get("total_trades") is not None:
        checks["enough_trades"] = m["total_trades"] >= min_oos_trades
    if m.get("sharpe") is not None or m.get("win_rate_pct") is not None \
            or m.get("profit_factor") is not None:
        # trend systems legitimately run low WR with big winners — PF
        # is the honest quality signal when WR alone would mislead
        checks["quality"] = ((m.get("sharpe") or 0) >= 1.0) or \
            ((m.get("win_rate_pct") or 0) >= 50.0) or \
            ((m.get("profit_factor") or 0) >= 1.15)
    return {
        "valid": all(checks.values()), "checks": checks,
        "judge": "real_tv",
        "net_profit_pct": m.get("net_profit_pct"),
        "net_profit_usd": m.get("net_profit_usd"),
        "trades": m.get("total_trades"),
        "win_rate": m.get("win_rate_pct"),
        "profit_factor": m.get("profit_factor"),
        "max_dd": m.get("max_drawdown_pct"),
        "sharpe": m.get("sharpe"),
        "buy_hold_pct": m.get("buy_hold_pct"),
        "positive_folds": pos_folds, "fold_count": len(folds),
        "fold_metrics": folds,
    }


def main() -> None:
    import argparse
    import yaml
    ap = argparse.ArgumentParser(prog="tv-harness")
    ap.add_argument("--login", action="store_true")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO)
    from ..core.config import load_config
    cfg = load_config(None)
    h = TVHarness(Journal(str(ROOT / "data" / "luffy.db")), cfg)
    if args.login:
        h.assisted_login()
    elif args.check:
        print(json.dumps(h.health(), indent=1))


if __name__ == "__main__":
    main()
