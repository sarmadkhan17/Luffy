"""Read-only owner view, authentication and safe DOM rendering."""
import json
from pathlib import Path
import subprocess

from fastapi.testclient import TestClient
import pytest


@pytest.fixture
def server(tmp_path, monkeypatch):
    from trader.dashboard import server
    from fastapi import APIRouter
    monkeypatch.setattr(server,'ROOT',tmp_path)
    monkeypatch.setattr(server,'make_graphql_router',lambda _:APIRouter())
    monkeypatch.setenv('DASH_TOKEN','fixture-token')
    monkeypatch.setattr(server,'_account_snapshot',lambda:pytest.fail('venue lookup'))
    return server


def test_api_disabled_auth_and_missing_store(server, tmp_path):
    client=TestClient(server.create_app({'attention':{'enabled':False}}))
    assert client.get('/api/attention/latest').status_code==401
    r=client.get('/api/attention/latest?token=fixture-token')
    assert r.json()['status']=='disabled' and r.headers['cache-control'].startswith('no-store')
    assert not (tmp_path/'data/attention.db').exists()
    client=TestClient(server.create_app({'attention':{'enabled':True}}))
    assert client.get('/api/attention/latest?token=fixture-token').json()['status']=='waiting'
    assert client.get('/attention.js', headers={'x-luffy-token':'fixture-token'}).status_code==200
    assert 'attention-content' in client.get('/', headers={'x-luffy-token':'fixture-token'}).text


def test_api_bad_config_and_error_health(server, tmp_path):
    client=TestClient(server.create_app({'attention':{'enabled':True,'max_symbols':-1}}))
    assert client.get('/api/attention/latest?token=fixture-token').json()['status']=='configuration_error'
    client=TestClient(server.create_app({'attention':{'enabled':True}}))
    (tmp_path/'data/attention_health.json').write_text(json.dumps({'errors':1,'last_error':'worker_timeout'}))
    assert client.get('/api/attention/latest?token=fixture-token').json()['status']=='error'


def test_learning_health_is_read_only_and_visible_when_enabled(server, tmp_path):
    client=TestClient(server.create_app({'attention':{'enabled':True},
                                      'attention_learning':{'enabled':True}}))
    data=client.get('/api/attention/latest?token=fixture-token').json()
    assert data['learning']['status']=='unavailable'
    assert not (tmp_path/'data/attention_learning.db').exists()


def test_panel_treats_symbols_and_causes_as_text(tmp_path):
    script=Path('trader/dashboard/web/attention.js').resolve()
    harness=r'''
const fs = require('fs'), vm = require('vm'), assert = require('assert');
class Element {
 constructor() { this.children=[]; this.textContent=''; this.style={}; }
 appendChild(el) { this.children.push(el); }
 replaceChildren() { this.children=[]; }
 set innerHTML(_) { throw Error('unsafe HTML'); }
}
const status=new Element(), content=new Element();
const payload={status:'stale',age_seconds:400,causes_complete:true,
 execution_recovery:{symbol:'<script>recovery</script>',reason:'entry_fill_unconfirmed',attempts:2},
 learning:{status:'ok',counts:{pending:1},recent:[{symbol:'<svg onload=x>',status:'pending',deadline_ms:2000,direction:1,scan_id:'s'}]},
 scan:{scan_id:'s',as_of_ms:1000,
 rows:[{symbol:'<img src=x onerror=alert(1)>',reason:'selected'}]},
 causes:[{symbol:'x',evaluations:[{component:'strategy',component_id:'<script>x</script>',reason:'evaluation_failed'}]}]};
const context={document:{getElementById:id=>id==='attention-status'?status:content,createElement:()=>new Element()},
 location:{search:''},fetch:async()=>({ok:true,json:async()=>payload}),
 AbortController, setTimeout, clearTimeout, setInterval:()=>0};
vm.runInNewContext(fs.readFileSync(process.argv[2],'utf8'),context);
setTimeout(()=>{
 const text=el=>el.textContent+' '+el.children.map(text).join(' ');
 assert(status.textContent.includes('stale'));
 assert(text(content).includes('<img src=x onerror=alert(1)>'));
 assert(text(content).includes('<script>x</script>'));
 assert(text(content).includes('<svg onload=x>'));
 assert(text(content).includes('pending 1'));
 assert(text(content).includes('new entries blocked'));
 assert(text(content).includes('<script>recovery</script>'));
 console.log('safe DOM rendering passed');
},20);
'''
    harness_path=tmp_path/'render.cjs'
    harness_path.write_text(harness)
    result=subprocess.run(['node',str(harness_path),str(script)],text=True,capture_output=True,timeout=10)
    assert result.returncode==0,result.stderr
    assert 'safe DOM rendering passed' in result.stdout


def test_recovery_visibility_is_authenticated_and_read_only(server, tmp_path):
    from trader.core.journal import Journal
    from trader.engine.recovery import KEY
    client = TestClient(server.create_app({'attention': {'enabled': True}}))
    journal = Journal(tmp_path/'data/luffy.db')
    raw = json.dumps({'id': 'intent', 'symbol': 'BTC/USDT', 'phase': 'entry',
                      'reason': 'entry_fill_unconfirmed', 'position': {'internal': 'omitted'}})
    journal.kv_set(KEY, raw)
    assert client.get('/api/attention/latest').status_code == 401
    result = client.get('/api/attention/latest?token=fixture-token').json()['execution_recovery']
    assert result['reason'] == 'entry_fill_unconfirmed'
    assert 'position' not in result
    assert journal.kv_get(KEY) == raw
    journal.kv_set(KEY, '{}')
    result = client.get('/api/attention/latest?token=fixture-token').json()['execution_recovery']
    assert result['reason'] == 'recovery_ledger_unreadable'
