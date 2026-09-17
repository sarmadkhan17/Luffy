import json
from pathlib import Path
import subprocess
from fastapi.testclient import TestClient
from tests.test_attention_view import server
from trader.observability import investigation as C


def test_authenticated_missing_readonly_endpoint(server,tmp_path):
    client=TestClient(server.create_app({'attention':{'enabled':True}}))
    assert client.get('/api/investigations/latest').status_code==401
    r=client.get('/api/investigations/latest?token=fixture-token')
    assert r.json()['status']=='unavailable'
    assert r.headers['cache-control'].startswith('no-store')
    assert not (tmp_path/'data/investigation.db').exists()
    assert client.get('/investigation.js', headers={'x-luffy-token':'fixture-token'}).status_code==200
    assert 'investigation-content' in client.get('/', headers={'x-luffy-token':'fixture-token'}).text


def test_health_stale_and_safe_dom(tmp_path):
    path=tmp_path/'investigation.db'
    C.ledger(path).close()
    path.with_name('investigation_health.json').write_text(json.dumps({'status':'ok','updated_ms':1}))
    assert C.owner_view(path,700000)['status']=='stale'
    script=Path('trader/dashboard/web/investigation.js').resolve()
    harness=r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert');
class E {constructor(){this.children=[];this.textContent='';this.style={}} appendChild(e){this.children.push(e)} replaceChildren(){this.children=[]} set innerHTML(_){throw Error('unsafe HTML')}}
const status=new E(),content=new E();
const payload={status:'ok',health:{active:1,cases:1},cases:[{investigation:{registered_ms:1000,primary_trigger:'volume_anomaly',question:'<img src=x onerror=evil()>',state:{symbol:'<script>x</script>',dimensions:[],contradictions:[],missing:['participation']},alternatives:[{name:'same_direction',status:'indistinguishable',prediction:'future volume',invalidator:'normalization'}]},updates:[{observed_ms:2000,assessment:[['same_direction','compatible']],previous_assessment:[],reason_codes:['new_input'],evidence:{reason:'complete_exact_window',explanatory_uncertainty:'Cause unknown'},next_action:{kind:'WAIT',test:'Need exact bars',earliest_ms:3000}}]}]};
vm.runInNewContext(fs.readFileSync(process.argv[2],'utf8'),{document:{getElementById:id=>id==='investigation-status'?status:content,createElement:()=>new E()},fetch:async()=>({ok:true,json:async()=>payload}),location:{search:''},AbortController,setTimeout,clearTimeout,setInterval:()=>0});
setTimeout(()=>{const text=e=>e.textContent+' '+e.children.map(text).join(' '); const t=text(content); for(const s of ['<script>x</script>','<img src=x onerror=evil()>','Cause unknown','WAIT','What changed','compatible']) assert(t.includes(s),s); console.log('safe dossier DOM passed')},20);
'''
    h=tmp_path/'render.cjs';h.write_text(harness)
    r=subprocess.run(['node',str(h),str(script)],capture_output=True,text=True,timeout=10)
    assert r.returncode==0,r.stderr
