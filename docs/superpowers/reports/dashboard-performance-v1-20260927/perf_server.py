"""Isolated dashboard for before/after profiling; all external I/O refused or
simulated. Usage: PERF_ROOT=... perf_server.py before|after PORT"""
import builtins, contextvars, io, json, os, resource, socket, sqlite3, subprocess, sys, threading, time, types
from pathlib import Path
MODE, PORT = sys.argv[1], int(sys.argv[2])
REPO = Path(__file__).resolve().parents[4]; ROOT = Path(os.environ['PERF_ROOT']); sys.path.insert(0, str(REPO))
BASE = '1eb00e151113d0add6033ef809ccdf6e30623003'
import trader.core.config as config
from trader.core.config import Env
import trader.data.feed as feed
import requests, uvicorn
config.ROOT = ROOT
os.environ['DASH_TOKEN'] = 'isolated-profile-token'
Env.binance_keys = classmethod(lambda cls: ('fixture', 'fixture'))
if MODE == 'before':
    src = subprocess.check_output(['git', 'show', f'{BASE}:trader/dashboard/server.py'], cwd=REPO, text=True)
    server = types.ModuleType('trader.dashboard.profile_original'); server.__file__ = str(REPO / 'trader/dashboard/server.py'); server.__package__ = 'trader.dashboard'
    exec(compile(src, server.__file__, 'exec'), server.__dict__)
    web = ROOT / 'web-before'; web.mkdir(exist_ok=True)
    for f in ('index.html', 'attention.js', 'investigation.js'):   # baseline assets, not the worktree's
        (web / f).write_text(subprocess.check_output(['git', 'show', f'{BASE}:trader/dashboard/web/{f}'], cwd=REPO, text=True))
    server.WEB = web
else:
    import trader.dashboard.server as server
server.ROOT = ROOT
allowed = ROOT.resolve()
_open, _io = builtins.open, io.open
def guard(path):
    if isinstance(path, (str, bytes, os.PathLike)):
        p = Path(os.fsdecode(path)).resolve()
        if p.name == '.env' or (str(p).startswith('/home/sarmad/') and ('/data/' in str(p) or p.suffix in ('.db', '.sqlite', '.sqlite3')) and not p.is_relative_to(allowed)):
            raise PermissionError('profile refuses non-fixture data')
builtins.open = lambda p, *a, **k: (guard(p), _open(p, *a, **k))[1]
io.open = lambda p, *a, **k: (guard(p), _io(p, *a, **k))[1]
_connect = sqlite3.connect
def safe_db(path, *a, **k):
    raw = str(path).replace('file:', '').split('?')[0]
    if not Path(raw).resolve().is_relative_to(allowed): raise PermissionError('profile DB not isolated')
    return _connect(path, *a, **k)
sqlite3.connect = safe_db
_sock = socket.socket.connect
socket.socket.connect = lambda self, addr: (_ for _ in ()).throw(PermissionError('external network disabled')) if isinstance(addr, tuple) and addr[0] not in ('127.0.0.1', '::1', 'localhost') else _sock(self, addr)
_gai = socket.getaddrinfo
socket.getaddrinfo = lambda host, *a, **k: _gai(host, *a, **k) if host in ('127.0.0.1', '::1', 'localhost', None) else (_ for _ in ()).throw(PermissionError('external DNS disabled'))
model = {'delay': .08, 'calls': 0, 'max_concurrent': 0}; active = [0]; lock = threading.Lock()
class Reply:
    def json(self): return {'assets': [{'asset': 'USDT', 'walletBalance': '1000', 'updateTime': int(time.time() * 1000)}], 'price': '100', 'totalMarginBalance': '1000'}
def remote(*a, **k):
    with lock:
        model['calls'] += 1; active[0] += 1; model['max_concurrent'] = max(model['max_concurrent'], active[0])
    try: time.sleep(model['delay']); return Reply()
    finally:
        with lock: active[0] -= 1
requests.get = remote
class Feed:
    def __init__(self, *a, **k): self.ex = self; self.timeout = None
    def price(self, s): remote(); return 100
    def fetch_ticker(self, s): remote(); return {'last': 100.0, 'timestamp': int(time.time() * 1000)}
feed.DataFeed = Feed; feed.make_exchange = lambda *a, **k: None
app = server.create_app({'attention': {'enabled': False}})
@app.post('/__profile__/model')
def model_config(body: dict):
    model.update(delay=float(body['delay']), calls=0, max_concurrent=0)
    if MODE == 'before':
        for c in (server._ASSET_CACHE, server._PRICE_CACHE, server._MARKS_CACHE): c.update(ts=0., data={})
    else:
        # simulated restart of this app's enrichment state; old jobs may still
        # finish on the replaced pool (harness artifact, no production analogue)
        app.state.enrichment.__init__()
    return {'model': model}
@app.get('/__profile__/resource')
def resources():
    return {'rss_kib': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss, 'network_model': model, 'threads': threading.active_count()}
uvicorn.run(app, host='127.0.0.1', port=PORT, log_level='error')
