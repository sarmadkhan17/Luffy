/* Revision-4 dashboard owner-control block, kept verbatim as a negative control
   (whole-map localStorage read-modify-write). Never served. */
/* Owner controls: typed requests executed by the kernel's Owner Interface.
   The dashboard sets no state. Each intended action gets one request id and
   one issue time, kept (localStorage) until the kernel gives a definitive
   answer: a double-submit, retry, reconnect or reload re-sends the SAME id,
   which the kernel answers from its record — never a second execution.
   Containment (freeze/halt/panic) is never disabled; only a second recovery
   waits for the first. */
const OWNER_ASK={freeze:'Freeze: block new entries, keep managing exits?',
 halt:'Halt: stop entries and exit management (venue stops stay)?',
 resume:'Resume: request a guarded recovery check (ACTIVE only if proven safe)?',
 unhalt:'Unhalt: request a guarded, Risk-checked recovery out of HALTED?',
 panic:'FLATTEN ALL POSITIONS AND FREEZE?'};
const OWNER_DEFINITIVE=['ACCEPTED','ACTIVATED','CONTAINED','ALREADY_SET','REFUSED'];
const OWNER_KEY='luffy.owner.pending';
let recoveryInFlight=false;
/* Pending ids live in shared localStorage, read FRESH on every access and
   changed with a read-modify-write of one key only, so a tab never replaces
   another tab's entries with a stale copy. OWNER_MEM holds only entries whose
   storage write failed (this page keeps retrying with the same id; the owner
   is told a reload would lose it). */
const OWNER_MEM={};let ownerStorageWarned=false;
function ownerRead(){try{const m=JSON.parse(localStorage.getItem(OWNER_KEY)||'{}');
 return (m&&typeof m==='object')?m:{};}catch(_){return null;}}
function ownerWarn(){if(!ownerStorageWarned){ownerStorageWarned=true;
 toast('Browser storage unavailable: unresolved owner requests are kept only until this page reloads',true);}}
function ownerGet(key){const m=ownerRead();return (m&&m[key])||OWNER_MEM[key]||null;}
function ownerPut(key,p){const m=ownerRead();
 if(m){m[key]=p;try{localStorage.setItem(OWNER_KEY,JSON.stringify(m));delete OWNER_MEM[key];return;}catch(_){}}
 OWNER_MEM[key]=p;ownerWarn();}
/* delete only if the stored entry is still THIS request */
function ownerDone(key,id){
 if(OWNER_MEM[key]&&OWNER_MEM[key].id===id)delete OWNER_MEM[key];
 const m=ownerRead();
 if(m&&m[key]&&m[key].id===id){delete m[key];
  try{localStorage.setItem(OWNER_KEY,JSON.stringify(m));}catch(_){}}}
function ownerId(){return (crypto.randomUUID?crypto.randomUUID():
 Array.from(crypto.getRandomValues(new Uint8Array(16)),b=>b.toString(16).padStart(2,'0')).join(''));}
const OWNER_Q={
 control:`mutation($o:String!,$r:String!,$t:Float!){res:owner_control(operation:$o,request_id:$r,issued_at_ms:$t){status control_state_after reasons message replayed}}`,
 panic:`mutation($r:String!,$t:Float!){res:panic(request_id:$r,issued_at_ms:$t){status control_state_after reasons message replayed}}`,
 close_trade:`mutation($x:String!,$r:String!,$t:Float!){res:close_trade(trade_id:$x,request_id:$r,issued_at_ms:$t){status control_state_after reasons message replayed}}`};
/* key: the action (op + its target). An unresolved earlier attempt is reused. */
async function ownerSubmit(op,target){
 const key=op+(target?':'+target:'');
 let p=ownerGet(key);
 if(!p){p={id:ownerId(),t:Date.now()};ownerPut(key,p);}
 const q=op==='panic'?OWNER_Q.panic:op==='close_trade'?OWNER_Q.close_trade:OWNER_Q.control;
 const v={r:p.id,t:p.t,o:op,x:target};
 let r=null;
 for(let attempt=0;attempt<2&&!r;attempt++){
  try{const d=await gql(q,v);r=d&&d.res;}catch(_){r=null;}}
 /* clear only THIS request's entry: a late answer to an older attempt must
    never delete a newer unresolved request stored under the same action */
 if(r&&OWNER_DEFINITIVE.includes(r.status))ownerDone(key,p.id);
 return r;}
async function ownerControl(op){
 const recovery=(op==='resume'||op==='unhalt');
 if(recovery&&recoveryInFlight){toast('A recovery check is already running',true);return;}
 const pending=ownerGet(op);
 if(!pending&&!confirm(OWNER_ASK[op]))return;       // a retry of an unresolved action needs no new consent
 if(recovery){recoveryInFlight=true;document.querySelectorAll('.owner-rec').forEach(b=>b.disabled=true);}
 try{const r=await ownerSubmit(op);
  if(!r)toast(op+': no answer — outcome unknown. Press '+op.toUpperCase()+' again to retry the same request.',true);
  else toast(r.message+(r.replayed?' (recorded result)':''),
   !['ACCEPTED','ACTIVATED','ALREADY_SET'].includes(r.status));
 }finally{if(recovery){recoveryInFlight=false;
  document.querySelectorAll('.owner-rec').forEach(b=>b.disabled=false);}refresh();}}
async function panic(){await ownerControl('panic');}
/* one position, by hand. The browser places nothing: the kernel validates
   the trade and queues its close for the next cycle. */
const closePending=new Set();
async function closeTrade(id,sym){
 if(!ownerGet('close_trade:'+id)&&!confirm('Market-close '+sym+' now?'))return;
 const r=await ownerSubmit('close_trade',id);
 if(!r)toast(sym+': no answer — outcome unknown; press close again to retry',true);
 else if(r.status==='ACCEPTED'||r.status==='ALREADY_SET'){closePending.add(id);
  toast(sym+' close queued — next cycle');}
 else toast(sym+': '+r.message,true);
 refresh();}
