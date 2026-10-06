#!/usr/bin/env bash
# Restart a Luffy process safely. Usage: ./restart.sh kernel|dashboard
#
# Kernel: stop only the lock-holding, identity-verified process with SIGTERM and
# wait for it to release data/kernel.lock (trader/runtime_identity.py). There is
# NO SIGKILL: if it does not exit within KERNEL_STOP_TIMEOUT (default 300s; a
# graceful stop has taken ~224s) or cannot be verified, nothing is started and
# this exits non-zero, so a slow shutdown never becomes a second kernel. The
# kernel itself also refuses to start while the lock is held.
# Set LUFFY_EXPECT_REVISION=<sha> to also refuse a start on any other revision
# or on modified runtime code.
# Dashboard: SIGTERM with a short grace, never SIGKILL; the kernel is untouched.
set -u
DIR="$(cd "$(dirname "$0")" && pwd)"
TARGET="${1:-}"
case "$TARGET" in
  kernel)    MOD="trader.kernel"; LOG="/tmp/opencode/luffy_kernel.log" ;;
  dashboard) MOD="trader.dashboard.server"; LOG="/tmp/opencode/luffy_dash.log" ;;
  *) echo "usage: $0 kernel|dashboard"; exit 1 ;;
esac
cd "$DIR"
PY=./venv/bin/python
ARGS=()

if [ "$TARGET" = kernel ]; then
  if [ -n "${LUFFY_EXPECT_REVISION:-}" ]; then
    $PY -m trader.runtime_identity verify --expect-revision "$LUFFY_EXPECT_REVISION" || {
      echo "refusing to start: wrong revision/modified code"; exit 6; }
    ARGS=(--expect-revision "$LUFFY_EXPECT_REVISION")
  fi
  $PY -m trader.runtime_identity stop --timeout "${KERNEL_STOP_TIMEOUT:-300}"
  rc=$?
  if [ "$rc" -ne 0 ]; then
    echo "kernel not stopped (exit $rc); NOT starting another"; exit "$rc"
  fi
else
  SELF=$$
  for PID in $(pgrep -f "$MOD"); do
    [ "$PID" = "$SELF" ] && continue
    # only real python module processes, not shells mentioning the name
    if readlink "/proc/$PID/exe" 2>/dev/null | grep -q python; then
      kill -TERM "$PID" 2>/dev/null && echo "TERM $PID"
      for _ in $(seq 1 "${DASH_STOP_TIMEOUT:-20}"); do kill -0 "$PID" 2>/dev/null || break; sleep 1; done
      if kill -0 "$PID" 2>/dev/null; then
        echo "dashboard $PID still running; NOT starting another"; exit 4
      fi
    fi
  done
fi

# /tmp is wiped on reboot; a redirect into a missing directory fails and bash
# then never runs the command, so "started" would print over a dead process.
mkdir -p "$(dirname "$LOG")"
setsid nohup $PY -m "$MOD" "${ARGS[@]}" > "$LOG" 2>&1 < /dev/null &
echo "started $MOD"
