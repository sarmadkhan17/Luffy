#!/usr/bin/env python3
"""Validate the frozen M3.2 input and print a zero-search manifest."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trader.cognition.m32_protocol import ARTIFACT, load_locked_artifact, zero_search_manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact", type=Path, default=ARTIFACT)
    args = parser.parse_args()
    artifact = load_locked_artifact(args.artifact)
    print(json.dumps(zero_search_manifest(artifact), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
