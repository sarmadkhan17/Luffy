import importlib.util, json, logging, pathlib, subprocess, tempfile, types
from trader.core.journal import Journal
from trader.owner import queries
from trader.core.config import Env
import requests
BASE = subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()
def load(path, name, package):
    source=subprocess.check_output(['git','show',BASE+':'+path],text=True)
    module=types.ModuleType(name);module.__package__=package
    exec(compile(source,path,'exec'),module.__dict__)
    return module
agent=load('trader/chat/agent.py','trader.chat.review10_baseline','trader.chat')
tools=load('trader/chat/tools.py','trader.chat.review10_tools_baseline','trader.chat')
notify=load('trader/notify/telegram.py','trader.notify.review10_baseline','trader.notify')
with tempfile.TemporaryDirectory(dir='/tmp') as tmp:
    j=Journal(pathlib.Path(tmp)/'test.db')
    a=agent.AnalystAgent(j,None)
    legacy=a._exec('get_positions','{}')
    observed=[]; original=queries.query
    queries.query=lambda journal,**kw: observed.append(kw) or {'records':[]}
    a._exec('owner_query','{"kind":"cost","root":"/tmp/undeclared-root"}')
    queries.query=original
    with j._tx() as db:
        db.execute("INSERT INTO trades(id,symbol,side,amount,entry_price,opened_at,status,realized_pnl) VALUES('t','SOL/USDT','long',1,100,'2026-10-20','closed',NULL)")
    pnl=tools.get_pnl(j,'all')
    tg=notify.Telegram.__new__(notify.Telegram);tg.token='SENTINEL_TEST_TOKEN';tg.chat_id='123'
    original_post=requests.post
    requests.post=lambda *a,**kw: types.SimpleNamespace(ok=True,json=lambda:{'ok':False})
    acknowledged=tg.send('test')
    messages=[]
    class Handler(logging.Handler):
        def emit(self, record): messages.append(record.getMessage())
    handler=Handler();notify.log.addHandler(handler)
    def fail(url,**kw): raise requests.ConnectionError('unable to reach '+url)
    requests.post=fail;tg.send('test');notify.log.removeHandler(handler);requests.post=original_post
    proofs=dict(baseline=BASE, legacy_tool_dispatch_returned_rows=isinstance(legacy,list),
        undeclared_root_reached_query=observed[0].get('root')=='/tmp/undeclared-root',
        unknown_null_pnl_fabricated_zero=pnl['realized_pnl']==0 and pnl['win_rate']==0,
        bot_api_rejection_reported_success=acknowledged,
        request_exception_disclosed_token=any('SENTINEL_TEST_TOKEN' in m for m in messages),
        isolation='git show modules executed under temporary names; fixture-only Journal and mocked transport; no source swap')
    assert all(v is True for k,v in proofs.items() if k not in ('baseline','isolation'))
    pathlib.Path('reports/review10/chat-notify-baseline-proofs.json').write_text(json.dumps(proofs,indent=2)+'\n')
