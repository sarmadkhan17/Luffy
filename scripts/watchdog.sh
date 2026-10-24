#!/usr/bin/env bash
# Keep Luffy up. Cron runs this every 5 minutes and once at boot.
#
#   no kernel process              -> start it
#   heartbeat older than STALE_S   -> restart it (hung, or the box slept)
#   no dashboard process           -> start it
#
# Between 2026-09-03 and 2026-09-11 the kernel was down for 7.5 days and 17 of
# the 19 real Donchian breaks since 09-01 fired while nothing was listening.
#
# Every start goes through restart.sh, which gracefully stops before it starts, so this
# can never leave two kernels trading one account. The kernel also holds
# data/kernel.lock and refuses to start twice (trader/runtime_identity.py).
#
# To take Luffy down on purpose, `touch data/watchdog.off` first, or this will
# bring it straight back. Remove the file to re-arm.
set -u
DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$DIR" || exit 1
STALE_S="${STALE_S:-600}"
LOG="$DIR/logs/watchdog.log"
mkdir -p "$DIR/logs"
say() { echo "$(date '+%F %T') $*" >> "$LOG"; }
act() { if [ -n "${DRY_RUN:-}" ]; then say "DRY_RUN would run: $*"; else "$@" >> "$LOG" 2>&1; fi; }

[ -e "$DIR/data/watchdog.off" ] && exit 0

# Independent local safety alerts precede any launcher action. This command
# never contacts a venue and publishes without requiring Journal writes.
act ./venv/bin/python -m scripts.monitor --safety-only

# Kernel presence comes from the verified, noncreating identity probe (lock
# table + record + /proc start ticks + real `python -B -m trader.kernel` argv,
# excluding --status/--panic). It creates no path and takes no lock. Exit codes:
# 0 verified RUNNING, 10 NONE, 11 STALE_RECORD, anything else = not verifiable,
# in which case nothing is started or restarted.
kstatus=$(./venv/bin/python -B -m trader.runtime_identity status-only --root "$DIR" 2>/dev/null)
krc=$?
case "$krc" in
  10|11)
    say "kernel not running (status-only exit $krc) -> starting"
    act ./restart.sh kernel
    ;;
  0)
    # A kernel younger than STALE_S has not had the chance to beat yet; the file
    # still holds the previous process's last beat. Judging it would restart
    # every fresh kernel in a loop.
    kpid=$(printf '%s' "$kstatus" | python3 -c 'import json,sys; print(json.load(sys.stdin)["record"]["pid"])' 2>/dev/null)
    # Only a heartbeat bound to THIS kernel instance counts as health; a fresh
    # beat from another instance is treated as missing, never as a live kernel.
    age=$(printf '%s' "$kstatus" | python3 -c 'import json,sys; d=json.load(sys.stdin); v=d["heartbeat_age_s"] if d.get("heartbeat_belongs_to_instance") is True else None; print("" if v is None else int(v))' 2>/dev/null)
    up=$(ps -o etimes= -p "${kpid:-0}" 2>/dev/null | tr -d ' ')
    if [ "${up:-0}" -gt "$STALE_S" ] && { [ -z "$age" ] || [ "$age" -gt "$STALE_S" ]; }; then
      say "kernel pid $kpid up ${up}s, heartbeat ${age:-missing}s old (> ${STALE_S}s) -> restarting"
      act ./restart.sh kernel
    fi
    ;;
  *)
    say "kernel identity not verifiable (status-only exit $krc) -> no action"
    ;;
esac

if ! pgrep -f -x '.*/python[0-9.]* -m trader\.dashboard\.server' >/dev/null; then
  say "dashboard not running -> starting"
  act ./restart.sh dashboard
fi

# Declared-universe public observation, bounded and isolated from trading.
# Complete membership attempts precede both consumers; errors never fall back.
if [ -e "$DIR/data/declared_population.enabled" ]; then
  act timeout --kill-after=5s 120s ./venv/bin/python -m trader.observability.declared --once
fi

# Separate shadow consumer: bounded once-per-watchdog work, no venue calls.
# flock prevents overlapping manual/cron invocations; timeout bounds DB stalls.
# The module reads its own opt-in configuration and records structured health.
act flock -n "$DIR/data/attention_learning.lock" timeout 20s \
  ./venv/bin/python -m trader.observability.learning --once

# Opt-in continuing investigations: independent process, internal lock and deadline.
if [ -e "$DIR/data/investigation.enabled" ]; then
  act timeout 25s ./venv/bin/python -m trader.observability.investigation --once --enable
fi

# Read-only demo accounting: independent lock, bounded retries and no memory import.
# Keep after population consumers so slow venue history cannot delay their capture.
if [ -e "$DIR/data/accounting.enabled" ]; then
  act timeout --kill-after=5s 50s ./venv/bin/python -m trader.observability.accounting --once --enable
fi
