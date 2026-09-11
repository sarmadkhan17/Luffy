"""Backfill every reference market once, then print what is stored.

The kernel's ref-recorder does the same on its first pass; this exists so
the depth of every series can be checked without waiting for it.
"""
from __future__ import annotations

import sys
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
warnings.filterwarnings("ignore")

from trader.core.config import load_config          # noqa: E402
from trader.data.feed import DataFeed               # noqa: E402
from trader.data.ref_sources import refresh_all     # noqa: E402
from trader.data.references import REFS, RefStore   # noqa: E402


def main() -> int:
    cfg = load_config()
    store = RefStore()
    members = (cfg.get("references") or {}).get("alts_members") or []
    rep = refresh_all(DataFeed(), members, store)
    print(f"{'key':11s} {'source':10s} {'tf':5s} {'rows':>6s}  span")
    for key, ref in REFS.items():
        df = store.load(key)
        n = 0 if df is None else len(df)
        span = "" if not n else (f"{df['ts'].iloc[0]:%Y-%m-%d %H:%M} -> "
                                 f"{df['ts'].iloc[-1]:%Y-%m-%d %H:%M}")
        note = rep.get(key, "")
        print(f"{key:11s} {ref.source:10s} {ref.tf:5s} {n:6d}  {span}"
              + (f"   [{note}]" if isinstance(note, str) and note else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
