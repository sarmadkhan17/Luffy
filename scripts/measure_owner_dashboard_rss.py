"""Measure dashboard-process RSS for the owner frontend paths (no network).

Each stage runs in a fresh subprocess over a temporary ROOT (fixture journal),
so production data, IPC and the venue are never touched. Reports VmRSS after:
  1. create_app (FastAPI + GraphQL + owner API; nothing served yet)
  2. + 200 owner-API reads (overview/knowledge/system/bootstrap/trades)
  3. + import of trader.data.feed (what marks enrichment and the legacy
     /api/summary, /api/klines paths load in production)

    python scripts/measure_owner_dashboard_rss.py [out.json]
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHILD = r"""
import json, sys, tempfile
from pathlib import Path
sys.path.insert(0, %r)
def rss():
    for line in open('/proc/self/status'):
        if line.startswith('VmRSS:'):
            return round(int(line.split()[1]) / 1024, 1)
stage = sys.argv[1]
from tests.owner_frontend_fixture import make_app
from fastapi.testclient import TestClient
app, journal, gw = make_app(Path(tempfile.mkdtemp(prefix='owner-rss-')))
out = {'create_app': rss()}
if stage in ('reads', 'feed'):
    c = TestClient(app)
    h = {'x-luffy-token': 'fixture-token'}
    for _ in range(40):
        for p in ('overview', 'knowledge', 'system', 'bootstrap', 'trades'):
            assert c.get('/owner-api/v1/' + p, headers=h).status_code == 200
    out['after_200_owner_reads'] = rss()
if stage == 'feed':
    import trader.data.feed  # noqa: F401
    out['after_import_data_feed'] = rss()
print(json.dumps(out))
""" % str(ROOT)


def main():
    results = {}
    for stage in ("app", "reads", "feed"):
        r = subprocess.run([sys.executable, "-c", CHILD, stage], cwd=ROOT,
                           capture_output=True, text=True, timeout=300)
        if r.returncode:
            raise SystemExit(r.stderr[-2000:])
        results[stage] = json.loads(r.stdout.strip().splitlines()[-1])
    report = {"unit": "MiB VmRSS", "python": sys.version.split()[0],
              "note": "Fresh process per stage; temporary fixture ROOT; no network. "
                      "Not comparable 1:1 with the production dashboard, whose journal, "
                      "vault and legacy polling differ.",
              "stages": results}
    text = json.dumps(report, indent=2)
    if len(sys.argv) > 1:
        Path(sys.argv[1]).write_text(text + "\n")
    print(text)


if __name__ == "__main__":
    main()
