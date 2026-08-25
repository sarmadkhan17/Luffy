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

SELECTORS = {
    "pine_editor_tab": "[data-name='bottom-panel-tabs'] >> text=Pine Editor",
    "pine_editor_alt": "div[class*='pine'] >> text=Pine Editor",
    "editor_area": ".editor-container textarea, .view-lines",
    "add_to_chart": "button:has-text('Add to chart')",
    "strategy_tester_tab": "[data-name='bottom-panel-tabs'] >> text=Strategy Tester",
    "strategy_tester_alt": "div[title='Strategy Tester']",
    "tester_panel": "[data-name='strategy-tester-panel'], div[id^='strategy-tester']",
    "chart_loaded": "div[class*='chart-container'], canvas",
}


def sha_code(code: str, symbol: str = "BINANCE:BTCUSDT", tf: str = "1h") -> str:
    return hashlib.sha256(f"{code}|{symbol}|{tf}".encode()).hexdigest()[:16]


def parse_metrics(text: str) -> dict:
    """Tolerant label→value scrape from the tester panel's plain text."""
    flat = re.sub(r"[ \t]+", " ", text)
    out = {}

    def grab(patterns, cast=str):
        for pat in patterns:
            m = re.search(pat, flat, re.I | re.S)
            if m:
                return cast(m.group(1))
        return None

    num = r"([+-]?[\d,]+\.?\d*)\s*(?:USD|USDT|\$)?"
    out["net_profit_usd"] = grab([
        rf"Total Net Profit\s*\n?\s*{num}", rf"\bNet Profit\s*\n?\s*{num}"],
        lambda v: float(v.replace(",", "")))
    out["net_profit_pct"] = grab(
        [rf"Total Net Profit[^%\n]*\n?.*?(-?[\d.]+)\s*%",
         rf"Net Profit.*?(-?[\d.]+)\s*%"],
        lambda v: float(v))
    out["total_trades"] = grab(
        [rf"Total Closed Trades\s*\n?\s*{num}",
         rf"Total Trades\s*\n?\s*{num}"],
        lambda v: int(float(v.replace(",", ""))))
    out["win_rate_pct"] = grab(
        [rf"Percent Profitable\s*\n?\s*(-?[\d.]+)", ],
        lambda v: float(v))
    out["profit_factor"] = grab(
        [rf"Profit Factor\s*\n?\s*(-?[\d.]+)"], lambda v: float(v))
    out["max_drawdown_pct"] = grab(
        [rf"Max (?:Equity |Strategy )?Drawdown\s*\n?\s*[+-]?[\d,.]+\s*"
         rf"(?:USD)?\s*([+-]?[\d.]+)\s*%",
         rf"Max (?:Equity |Strategy )?Drawdown\s*\n?\s*(-?[\d.]+)"],
        lambda v: float(v))
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
        if self.runs_today() >= self.daily_budget:
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
            t0 = time.time()
            try:
                page.goto(CHART_URL, timeout=45_000)
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
        try:
            btn = page.locator("[data-name='menu-inner']").first
            interval_btn = page.locator(
                "button[id$='-interval'], [data-name='interval-button']"
            ).first
            if interval_btn.count():
                interval_btn.click(timeout=4000)
                page.locator(f"[data-name='items-continuous'] >> "
                             f"text='{tf}'").first.click(timeout=4000)
        except Exception as e:
            log.debug(f"timeframe set skipped ({e}); keeping chart default")

    def _open_pine_editor(self, page) -> None:
        for sel in (SELECTORS["pine_editor_tab"], SELECTORS["pine_editor_alt"]):
            loc = page.locator(sel).first
            try:
                if loc.count():
                    loc.click(timeout=6000)
                    break
            except Exception:
                continue
        page.wait_for_timeout(800)

    def _paste_script(self, page, code: str) -> None:
        area = page.locator(SELECTORS["editor_area"]).first
        area.click(timeout=8000)
        page.keyboard.press("Control+A")
        page.keyboard.press("Delete")
        page.keyboard.insert_text(code)
        page.wait_for_timeout(500)

    def _add_to_chart(self, page) -> None:
        btn = page.locator(SELECTORS["add_to_chart"]).first
        btn.click(timeout=8000)
        page.wait_for_timeout(3500)      # strategy compiles + renders

    def _read_tester(self, page) -> dict:
        for sel in (SELECTORS["strategy_tester_tab"],
                    SELECTORS["strategy_tester_alt"]):
            loc = page.locator(sel).first
            try:
                if loc.count():
                    loc.click(timeout=6000)
                    break
            except Exception:
                continue
        page.wait_for_timeout(1500)
        panel = page.locator(SELECTORS["tester_panel"]).first
        text = panel.inner_text(timeout=8000) if panel.count() else \
            page.inner_text("body")
        metrics = parse_metrics(text)
        if not metrics:
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
def evaluate_manifest(harness: TVHarness, manifest: dict,
                      symbol: str = "BINANCE:BTCUSDT",
                      tf: str = "1h",
                      min_oos_trades: int = 5) -> dict:
    """Run full + fold variants through the real tester → same verdict
    shape as trader.brain.tv.TVClient.walk_forward, so the gauntlet can
    consume either judge interchangeably."""
    from ..brain.pine import PINE_DIR
    sid = manifest["strategy_id"]
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
    checks = {
        "profitable_oos": (m.get("net_profit_pct") or -100) > 0,
        "enough_trades": (m.get("total_trades") or 0) >= min_oos_trades,
        "quality": ((m.get("sharpe") or 0) >= 1.0) or
                   ((m.get("win_rate_pct") or 0) >= 50.0),
        "drawdown_ok": abs(m.get("max_drawdown_pct") or 100) <= 20.0,
        "folds_positive": pos_folds >= max(1, len(folds) // 2 + 1),
    }
    return {
        "valid": all(checks.values()), "checks": checks,
        "judge": "real_tv",
        "net_profit_pct": m.get("net_profit_pct"),
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
