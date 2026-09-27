#!/usr/bin/env bash
# Usage: PERF_ROOT=... run_part.sh before|after scenarios|behaviours|api [REP]
# Starts a FRESH isolated server on loopback, runs one part, stops that server.
set -u
MODE=$1 PART=$2 REP=${3:-} PORT=18791 TAG=$1-$2${3:+-r$3} PY=/home/sarmad/trader/venv/bin/python
cd "$(dirname "$0")"
$PY perf_server.py "$MODE" $PORT & SRV=$!
for i in $(seq 1 60); do
  [ "$(curl -s -o /dev/null -w '%{http_code}' -H 'x-luffy-token: isolated-profile-token' http://127.0.0.1:$PORT/api/review_status)" = 200 ] && break; sleep 1; done
if [ "$PART" = api ]; then timeout 900 $PY perf_api.py "$MODE" $PORT $SRV "$TAG.json" > "$TAG.log.txt" 2>&1
else timeout 900 $PY perf_browser.py "$MODE" $PORT "$TAG.json" "$PART" > "$TAG.log.txt" 2>&1; fi
RC=$?
kill $SRV 2>/dev/null; sleep 2; kill -9 $SRV 2>/dev/null   # the original server may be stuck in queued slow work
wait $SRV 2>/dev/null
echo "$TAG rc=$RC"
