#!/usr/bin/env bash
# Restart a Luffy process safely. Usage: ./restart.sh kernel|dashboard
set -u
DIR="$(cd "$(dirname "$0")" && pwd)"
TARGET="$1"
case "$TARGET" in
  kernel)    MOD="trader.kernel"; LOG="/tmp/opencode/luffy_kernel.log" ;;
  dashboard) MOD="trader.dashboard.server"; LOG="/tmp/opencode/luffy_dash.log" ;;
  *) echo "usage: $0 kernel|dashboard"; exit 1 ;;
esac

SELF=$$
for PID in $(pgrep -f "$MOD"); do
  [ "$PID" = "$SELF" ] && continue
  # only kill real python module processes, not shells mentioning the name
  if readlink "/proc/$PID/exe" 2>/dev/null | grep -q python; then
    kill -9 "$PID" 2>/dev/null && echo "killed $PID"
  fi
done
sleep 1
cd "$DIR"
setsid nohup ./venv/bin/python -m "$MOD" > "$LOG" 2>&1 < /dev/null &
echo "started $MOD"
