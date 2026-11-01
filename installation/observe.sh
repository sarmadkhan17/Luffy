#!/usr/bin/env bash
# Installed via crontab: */5 * * * * /mnt/luffy-data/LUFFY/installation/observe.sh >> /mnt/luffy-data/LUFFY/logs/observation-cycle.log 2>&1
# Bounded, observation-only portion of the existing watchdog. No launcher or owner controls.
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT" || exit 1
[ -e data/observers.off ] && exit 0
mkdir -p logs tmp
export TMPDIR="$ROOT/tmp"
exec 9>data/observation-cycle.lock
flock -n 9 || exit 0
run() { "$@"; rc=$?; printf '%s observer_exit=%s command=%s\n' "$(date -Is)" "$rc" "$*"; }
if [ -e data/declared_population.enabled ]; then
  run timeout --kill-after=5s 120s ./venv/bin/python -m trader.observability.declared --once
fi
run flock -n data/attention_learning.lock timeout 20s ./venv/bin/python -m trader.observability.learning --once
if [ -e data/investigation.enabled ]; then
  run timeout 25s ./venv/bin/python -m trader.observability.investigation --once --enable
fi
if [ -e data/accounting.enabled ]; then
  run timeout --kill-after=5s 50s ./venv/bin/python -m trader.observability.accounting --once --enable
fi
