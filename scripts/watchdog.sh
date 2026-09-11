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
# Every start goes through restart.sh, which kills before it starts, so this
# can never leave two kernels trading one account. The kernel has no
# single-instance guard of its own.
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

# -x: the WHOLE command line must match, so `python -m trader.kernel --status`
# and shells that merely mention the module are not mistaken for the kernel
kpid=$(pgrep -f -x '.*/python[0-9.]* -m trader\.kernel' | head -1)
if [ -z "$kpid" ]; then
  say "kernel not running -> starting"
  act ./restart.sh kernel
else
  # A kernel younger than STALE_S has not had the chance to beat yet; the file
  # still holds the previous process's last beat. Judging it would restart
  # every fresh kernel in a loop.
  up=$(ps -o etimes= -p "$kpid" | tr -d ' ')
  age=$(python3 -c 'import json,sys,time; print(int(time.time()-json.load(open(sys.argv[1]))["timestamp"]))' \
        data/heartbeat_luffy.json 2>/dev/null)
  if [ "${up:-0}" -gt "$STALE_S" ] && { [ -z "$age" ] || [ "$age" -gt "$STALE_S" ]; }; then
    say "kernel pid $kpid up ${up}s, heartbeat ${age:-missing}s old (> ${STALE_S}s) -> restarting"
    act ./restart.sh kernel
  fi
fi

if ! pgrep -f -x '.*/python[0-9.]* -m trader\.dashboard\.server' >/dev/null; then
  say "dashboard not running -> starting"
  act ./restart.sh dashboard
fi
