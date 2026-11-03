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
# Safety monitoring remains independent; data/safety_monitor.off opts it out.
# --status reports validated switch/lock metadata without running any action.
set -u
DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$DIR" || exit 1
if [ "${1:-}" = '--status' ]; then
  exec ./venv/bin/python -B -m trader.observability.pause_state --root "$DIR" --status
fi
if [ "$#" -ne 0 ]; then
  echo 'usage: watchdog.sh [--status]' >&2
  exit 2
fi
STALE_S="${STALE_S:-600}"
LOG="$DIR/logs/watchdog.log"
mkdir -p "$DIR/logs"
say() { echo "$(date '+%F %T') $*" >> "$LOG"; }
# restart.sh requires the full 40-char revision. Honour an explicit pin;
# otherwise bind to the checked-out HEAD (restart.sh still refuses dirty runtime code).
EXPECTED_REV="${LUFFY_EXPECT_REVISION:-$(git -C "$DIR" rev-parse HEAD 2>/dev/null)}"
export LUFFY_EXPECT_REVISION="$EXPECTED_REV"
act() { if [ -n "${DRY_RUN:-}" ]; then say "DRY_RUN would run: $*"; else "$@" >> "$LOG" 2>&1; fi; }

flag_present() {
  local flag_rc
  ./venv/bin/python -B -m trader.observability.pause_state --root "$DIR" --check "$1"
  flag_rc=$?
  case "$flag_rc" in
    0|1) return "$flag_rc" ;;
    *)
      # An invalid opt-out must still permit the independent monitor attempt.
      # All other invalid controls refuse subsequent supervision/observation.
      if [ "$1" != 'safety_monitor.off' ]; then
        say "unsafe control $1 -> no further action"
        exit 2
      fi
      return 2
      ;;
  esac
}

# Independent local safety alerts precede any launcher action. This command
# never contacts a venue and publishes without requiring Journal writes.
monitor_rc=0
if ! flag_present safety_monitor.off; then
  act ./venv/bin/python -m scripts.monitor --safety-only
  monitor_rc=$?
fi

# Invalid controls cannot disable monitoring or authorize a launcher action.
if ! ./venv/bin/python -B -m trader.observability.pause_state --root "$DIR" --validate >> "$LOG" 2>&1; then
  say 'unsafe pause/control path -> supervision refused'
  exit 2
fi
if flag_present watchdog.off; then
  say 'watchdog.off: supervision paused; independent safety pass retained'
  exit "$monitor_rc"
fi

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

# Observers have their own pause switch, independent of the safety monitor.
if flag_present observers.off; then
  exit 0
fi

# Declared-universe public observation, bounded and isolated from trading.
# Complete membership attempts precede both consumers; errors never fall back.
if flag_present declared_population.enabled; then
  act timeout --kill-after=5s 120s ./venv/bin/python -m trader.observability.declared --once
fi

# Separate shadow consumer: bounded once-per-watchdog work, no venue calls.
# flock prevents overlapping manual/cron invocations; timeout bounds DB stalls.
# The module reads its own opt-in configuration and records structured health.
act ./venv/bin/python -B -m trader.observability.pause_state --root "$DIR" \
  --lock-run attention_learning.lock -- timeout 20s \
  ./venv/bin/python -m trader.observability.learning --once

# Opt-in continuing investigations: independent process, internal lock and deadline.
if flag_present investigation.enabled; then
  act timeout 25s ./venv/bin/python -m trader.observability.investigation --once --enable
fi

# Read-only demo accounting: independent lock, bounded retries and no memory import.
# Keep after population consumers so slow venue history cannot delay their capture.
if flag_present accounting.enabled; then
  act timeout --kill-after=5s 50s ./venv/bin/python -m trader.observability.accounting --once --enable
fi
