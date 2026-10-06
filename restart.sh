#!/usr/bin/env bash
# Restart a Luffy process safely. Usage: ./restart.sh kernel|dashboard
#
# Order: OBS-01 preflight -> (kernel: optional revision bind) -> OBS-02 readiness
# (dashboard) -> verified graceful stop -> start.
# Kernel stop (RUN-01): SIGTERM only the lock-holding, identity-verified process
# and wait for data/kernel.lock to be released. NO SIGKILL: if it does not exit
# within KERNEL_STOP_TIMEOUT (default 300s; a graceful stop has taken ~224s) or
# cannot be verified, nothing is started and this exits non-zero, so a slow
# shutdown never becomes a second kernel (the kernel also refuses to start while
# the lock is held). LUFFY_EXPECT_REVISION=<sha> additionally refuses any other
# revision or modified runtime code. Dashboard stop: SIGTERM + short grace, never
# SIGKILL; a kernel restart never touches the Dashboard.
set -u
DIR="$(cd "$(dirname "$0")" && pwd)"
TARGET="${1:-}"
case "$TARGET" in
  kernel)    MOD="trader.kernel"; LOG="/tmp/opencode/luffy_kernel.log" ;;
  dashboard) MOD="trader.dashboard.server"; LOG="/tmp/opencode/luffy_dash.log" ;;
  *) echo "usage: $0 kernel|dashboard"; exit 1 ;;
esac

# OBS-01: refuse before any process stop/start. Identity/boot ordering is RUN-01.
if ! (cd "$DIR" && ./venv/bin/python -m trader.observability.preflight --root "$DIR" --target "$TARGET"); then
  echo '{"schema":"luffy-launch-preflight.v1","allow":false,"result":"FAIL","reasons":["preflight_command_failed"]}'
  exit 1
fi

# OBS-02: a Dashboard attaches only to a verified current boot/health instance.
if [ "$TARGET" = dashboard ]; then
  if ! (cd "$DIR" && ./venv/bin/python -m trader.observability.dashboard_readiness --root "$DIR"); then
    echo '{"schema":"luffy-dashboard-readiness.v1","allow":false,"result":"FAIL","reasons":["readiness_command_failed"]}'
    exit 1
  fi
fi

ARGS=()
if [ "$TARGET" = kernel ]; then
  if [ -n "${LUFFY_EXPECT_REVISION:-}" ]; then
    (cd "$DIR" && ./venv/bin/python -m trader.runtime_identity verify --expect-revision "$LUFFY_EXPECT_REVISION") || {
      echo "refusing to start: wrong revision/modified code"; exit 6; }
    ARGS=(--expect-revision "$LUFFY_EXPECT_REVISION")
  fi
  (cd "$DIR" && ./venv/bin/python -m trader.runtime_identity stop --timeout "${KERNEL_STOP_TIMEOUT:-300}")
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
cd "$DIR"
# /tmp is wiped on reboot; a redirect into a missing directory fails and bash
# then never runs the command, so "started" would print over a dead process.
mkdir -p "$(dirname "$LOG")"
setsid nohup ./venv/bin/python -m "$MOD" "${ARGS[@]}" > "$LOG" 2>&1 < /dev/null &
echo "started $MOD"
