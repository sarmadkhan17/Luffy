"""Sleep until today's TV budget has room for a full gauntlet, then run
the analyst candidates once. Detached via setsid."""
import subprocess
import sys
import time

sys.path.insert(0, "/home/sarmad/trader")
from trader.core.journal import Journal          # noqa: E402
from datetime import datetime, timezone          # noqa: E402


def used() -> int:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return Journal("data/luffy.db").query(
        "SELECT COUNT(*) n FROM brain_events WHERE kind='tv_backtest' "
        "AND ts >= ?", (today,))[0]["n"]


while True:
    u = used()
    print(f"budget watch: {u}/20 used", flush=True)
    if u <= 15:                                  # room for full+folds
        break
    time.sleep(900)

print("budget free — launching analyst gauntlet", flush=True)
subprocess.run(
    ["/home/sarmad/trader/venv/bin/python",
     "/home/sarmad/trader/scripts/analyst_gauntlet.py"],
    cwd="/home/sarmad/trader",
    env={"PYTHONPATH": "/home/sarmad/trader", "PATH": "/usr/bin:/bin"},
)
