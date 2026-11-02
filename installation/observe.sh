#!/usr/bin/env bash
# Install via crontab (replace <LUFFY_ROOT>): */5 * * * * <LUFFY_ROOT>/installation/observe.sh >> <LUFFY_ROOT>/logs/observation-cycle.log 2>&1
# Bounded, observation-only portion of the existing watchdog. No launcher or owner controls.
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT" || exit 1
[ -e data/observers.off ] && exit 0
mkdir -p logs tmp
export TMPDIR="$ROOT/tmp"
exec 9>data/observation-cycle.lock
# fd 9 holds the cycle lock. It is closed for every child (9>&-) so a hung child can never keep the lock alive
# after this script exits; `timeout --kill-after` bounds each child (worst case 275 s < the 300 s cron period).
if ! flock -n 9; then
  printf '%s observer_cycle skipped=locked\n' "$(date -Is)"
  exit 0
fi
run() { "$@" 9>&-; rc=$?; printf '%s observer_exit=%s command=%s\n' "$(date -Is)" "$rc" "$*"; }
if [ -e data/declared_population.enabled ]; then
  run timeout --kill-after=15s 120s ./venv/bin/python -m trader.observability.declared --once
fi
run flock -n data/attention_learning.lock timeout --kill-after=15s 20s ./venv/bin/python -m trader.observability.learning --once
if [ -e data/investigation.enabled ]; then
  run timeout --kill-after=15s 25s ./venv/bin/python -m trader.observability.investigation --once --enable
fi
if [ -e data/accounting.enabled ]; then
  run timeout --kill-after=15s 50s ./venv/bin/python -m trader.observability.accounting --once --enable
fi
