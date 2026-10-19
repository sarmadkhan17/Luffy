#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
scratch="$PWD/data/review07-verification"
mkdir -p "$scratch"
export TMPDIR="$scratch" TEMP="$scratch" TMP="$scratch" OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH="$PWD/reports/review07-quantitative-d1-d4/guard:$PWD"
# Explicit paths must be filtered before pytest; --ignore does not exclude a
# file explicitly passed on the command line. No operational depth checks.
nice -n 19 ./venv/bin/python -m pytest -o addopts= $(rg -v 'test_predictive_research_bridge.py|test_res08_governed_bridge.py|test_rp02_validation_window.py' reports/review07-quantitative-d1-d4/test-files.txt) --basetemp="$scratch/replay-impact" -k 'not every_enabled_horizon_carries_enough_discovery_symbols and not four_hour_is_deep_enough_today'
nice -n 19 ./venv/bin/python -m pytest -o addopts= tests/test_predictive_research_bridge.py tests/test_res08_governed_bridge.py tests/test_rp02_validation_window.py --basetemp="$scratch/replay-lifecycle"
