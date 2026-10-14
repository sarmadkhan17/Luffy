"""Run every authored spec through the honest walk-forward gauntlet.

Reports worst-symbol out-of-sample profit factor across all backtest symbols,
70/30 split, fees + slippage + funding modelled. Specs whose driving data is
absent are reported UNTESTED, never as failures — the distinction the old
pipeline could not make.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trader.core.config import load_config                  # noqa: E402
from trader.data.feed import DataFeed                       # noqa: E402
from trader.strategy import evidence, spec_evidence         # noqa: E402
from trader.strategy.spec import StrategySpec               # noqa: E402

SPEC_DIRS = ["data/authored_specs", "data/seed_specs"]


def load(dirs):
    out = []
    for d in dirs:
        for p in sorted(Path(d).glob("*.json")):
            out.append((d, StrategySpec.from_dict(json.loads(p.read_text()))))
    return out


def main(dirs=None, min_pf=1.15):
    cfg = load_config()
    feed = spec_evidence.offline_feed(cfg, sys.argv)
    frames = evidence.load_frames(feed, cfg) if feed.retrospective is None else {}
    native_frames = {}
    dirs = dirs if dirs is not None else sys.argv[1:] or SPEC_DIRS
    rows = []
    for src, spec in load(dirs or SPEC_DIRS):
        if feed.retrospective is not None:
            if spec.timeframe not in native_frames:
                native_frames[spec.timeframe] = spec_evidence.load_frames(cfg,spec.timeframe,feed=feed)
            frames = native_frames[spec.timeframe]
        ok, ev = spec_evidence.run_gauntlet(spec, frames, cfg, min_pf=min_pf)
        rows.append({
            "name": spec.name,
            "mech": spec.provenance.get("mechanism", "legacy"),
            "passed": ok,
            "untested": bool(ev.get("untested")),
            "worst_pf": ev.get("worst_test_pf", 0.0),
            "trades": ev.get("test_trades", 0),
            "reason": ev.get("reason", "PASSED"),
            **({'quant_contract':ev.get('quant_contract'), 'factory_validated':False,
                'evidence_scope':'retrospective_observation'} if feed.retrospective is not None else {}),
        })

    rows.sort(key=lambda r: (-r["worst_pf"], r["name"]))
    print(f"{'strategy':<38} {'mechanism':<14} {'OOS PF':>7} "
          f"{'trades':>7}  verdict")
    print("-" * 92)
    for r in rows:
        verdict = ("PASS" if r["passed"] else
                   "UNTESTED — " + r["reason"][:36] if r["untested"] else
                   r["reason"][:44])
        print(f"{r['name']:<38} {r['mech']:<14} {r['worst_pf']:>7.2f} "
              f"{r['trades']:>7}  {verdict}")
    print("-" * 92)
    tested = [r for r in rows if not r["untested"]]
    print(f"{len(rows)} specs · {len(tested)} tested · "
          f"{sum(r['passed'] for r in rows)} passed (PF >= {min_pf}) · "
          f"{sum(r['untested'] for r in rows)} untested for missing data")
    if tested:
        best = max(tested, key=lambda r: r["worst_pf"])
        print(f"best tested: {best['name']} at worst-symbol OOS PF "
              f"{best['worst_pf']:.2f} over {best['trades']} trades")
    if feed.retrospective is not None:
        import hashlib
        print('RETROSPECTIVE_EVIDENCE ' + json.dumps({'program_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), 'config_sha256':hashlib.sha256(json.dumps(cfg,sort_keys=True,default=str).encode()).hexdigest(), 'rows':rows}, sort_keys=True))
    return rows


if __name__ == "__main__":
    main()
