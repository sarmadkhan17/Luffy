#!/usr/bin/env python3
"""Execute the frozen, bounded M3.2 search and freeze its result ledger."""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from trader.cognition.m32_search import write_immutable, run_search
from trader.cognition.m32_protocol import ARTIFACT, ProtocolError


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--output", type=Path, default=Path("docs/superpowers/artifacts/m32-search/2026-09-19-historical-pattern-search-result.json"))
    args=ap.parse_args()
    result=run_search()
    write_immutable(result,args.output)
    print(json.dumps({k:result[k] for k in ("artifact_sha256","patterns_evaluated","null_draws_used","counts","search_status")},sort_keys=True,indent=2))
    candidates=[r for r in result["records"] if r["status"]=="descriptive_candidate"]
    for r in candidates[:10]: print(f"candidate {r['pattern_id']} rank={r['rank']:.8g} support={r['support']} metric={r['metric']}")
    return 0

if __name__ == "__main__": raise SystemExit(main())
