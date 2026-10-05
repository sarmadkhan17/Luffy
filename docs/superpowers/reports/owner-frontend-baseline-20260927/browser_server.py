"""Isolated original-HEAD dashboard; all external I/O refused or simulated."""
import asyncio, builtins, contextvars, importlib.util, io, json, os, resource, socket, sqlite3, subprocess, sys, threading, time, types
from pathlib import Path
from unittest.mock import patch
REPO=Path('/home/sarmad/trader-world'); ROOT=Path('/tmp/owner-frontend-v1'); sys.path.insert(0,str(REPO))
from trader.core.journal import Journal
from trader.core.config import Env
import trader.core.config as config
import trader.data.feed as feed
import requests, uvicorn
from fastapi import Request
source=subprocess.check_output(['git','show','1eb00e151113d0add6033ef809ccdf6e30623003:trader/dashboard/server.py'],cwd=REPO,text=True)
module=types.ModuleType('trader.dashboard.profile_original'); module.__file__=str(REPO/'trader/dashboard/server.py'); module.__package__='trader.dashboard'; exec(compile(source,module.__file__,'exec'),module.__dict__)
module.ROOT=ROOT; config.ROOT=ROOT
os.environ['DASH_TOKEN']='isolated-profile-token'
Env.binance_keys=classmethod(lambda cls:('fixture','fixture'))
logfile=ROOT/'server-events.jsonl'; logfile.write_text('')
lock=threading.Lock(); reqid=contextvars.ContextVar('request',default='background')
def record(kind,**kw):
    with lock:
        with logfile.open('a') as f: f.write(json.dumps({'kind':kind,'time':time.time(),'request':reqid.get(),**kw})+'\n')
original_open=builtins.open; original_io=io.open
allowed=ROOT.resolve()
def safe_path(path):
    if isinstance(path,(str,bytes,os.PathLike)):
        p=Path(os.fsdecode(path)).resolve()
        if p.name=='.env' or (str(p).startswith('/home/sarmad/') and ('/data/' in str(p) or p.suffix in ('.db','.sqlite','.sqlite3'))):
            raise PermissionError('profile refuses non-fixture data')
def safe_open(path,*a,**k): safe_path(path); return original_open(path,*a,**k)
def safe_io(path,*a,**k): safe_path(path); return original_io(path,*a,**k)
builtins.open=safe_open; io.open=safe_io
connect=sqlite3.connect
def safe_db(path,*a,**k):
    raw=str(path).replace('file:','').split('?')[0]
    if not Path(raw).resolve().is_relative_to(allowed): raise PermissionError('profile DB not isolated')
    return connect(path,*a,**k)
sqlite3.connect=safe_db
sock_connect=socket.socket.connect
def safe_connect(self,addr):
    if isinstance(addr,tuple) and addr[0] not in ('127.0.0.1','::1','localhost'): raise PermissionError('external network disabled')
    return sock_connect(self,addr)
socket.socket.connect=safe_connect
getaddrinfo=socket.getaddrinfo
socket.getaddrinfo=lambda host,*a,**k: getaddrinfo(host,*a,**k) if host in ('127.0.0.1','::1','localhost',None) else (_ for _ in ()).throw(PermissionError('external DNS disabled'))
model={'delay':.08,'calls':0}
class Reply:
    def json(self):return {'assets':[],'price':'100','totalMarginBalance':'1000'}
def remote(*a,**k):
    model['calls']+=1; t=time.perf_counter(); time.sleep(model['delay']); record('simulated_network',ms=(time.perf_counter()-t)*1000); return Reply()
requests.get=remote
class Feed:
    def __init__(self,*a,**k): pass
    def price(self,s):remote();return 100
feed.DataFeed=Feed;feed.make_exchange=lambda *a,**k:None
query=Journal.query
def measured_query(self,sql,params=()):
    t=time.perf_counter(); result=query(self,sql,params); record('query',sql=sql,ms=(time.perf_counter()-t)*1000,rows=len(result)); return result
Journal.query=measured_query
start=time.perf_counter();app=module.create_app({'attention':{'enabled':False}});record('app_start',ms=(time.perf_counter()-start)*1000,rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
@app.post('/__profile__/model')
def model_config(body:dict):
    model['delay']=float(body['delay']);model['calls']=0
    for cache in (module._ASSET_CACHE,module._PRICE_CACHE,module._MARKS_CACHE):cache.update(ts=0.,data={})
    return {'model':model}
@app.get('/__profile__/resource')
def resources():return {'rss_kib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,'network_model':model}
class Measure:
    def __init__(self,app):self.app=app
    async def __call__(self,scope,receive,send):
        if scope['type']!='http':return await self.app(scope,receive,send)
        t=time.perf_counter(); rid=f'{scope["path"]}:{time.time_ns()}';token=reqid.set(rid); size=0; status=None
        async def counted(message):
            nonlocal size,status
            if message['type']=='http.response.start':status=message['status']
            if message['type']=='http.response.body':size+=len(message.get('body',b''))
            await send(message)
        try:await self.app(scope,receive,counted)
        finally:
            record('http',path=scope['path'],ms=(time.perf_counter()-t)*1000,bytes=size,status=status);reqid.reset(token)
app.add_middleware(Measure)
uvicorn.run(app,host='127.0.0.1',port=18786,log_level='error')
