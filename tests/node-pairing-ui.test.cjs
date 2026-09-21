/* Execute the actual dialog function with a small API/DOM harness, not browser HTTP. */
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync(require('node:path').join(__dirname,'../web/nodes-v2.js'),'utf8');
const code=source.slice(source.indexOf('async function pairNodeDialog'),source.indexOf('async function createToken'));
const attempt='a'.repeat(32);
const pending={attempt_id:attempt,node_id:'turkey',name:'Turkey',origin:'https://tr.example.test',phase:'rotating',paired:false};
const completed={attempt_id:attempt,node_id:'turkey',node:{id:'turkey',name:'Turkey'},phase:'paired',paired:true,pair_code_consumed:true,registration_current:true};
function harness(items=[]){
 const h={calls:[],closed:0,messages:[],result:completed};
 const state={me:{id:'owner',role:'owner',writes_enabled:true}};
 const context=vm.createContext({state,L:en=>en,e:s=>String(s??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;'),enc:encodeURIComponent,
  isOwner:()=>state.me?.role==='owner',api:async(path,method='GET',body)=>{h.calls.push({path,method,body});return method==='GET'?{items}:h.result;},
  dialog:(title,html,submit)=>{h.html=html;h.submit=submit;},closeDialog:()=>{h.closed++;},toast:t=>h.messages.push(t),renderPage:async()=>{}});
 vm.runInContext(code+';globalThis.openPair=pairNodeDialog;',context);h.context=context;h.state=state;
 h.open=()=>context.openPair();h.send=fields=>h.submit(new Map(Object.entries(fields)),{isConnected:true});return h;
}
test('opening only reads saved operations, with no preselected consent or persisted secrets',async()=>{
 const h=harness([pending]);await h.open();assert.equal(h.calls.length,1);assert.equal(h.calls[0].method,'GET');
 assert.ok(h.html.includes('name="confirmRetry"'));assert.ok(!h.html.includes('checked'));
 assert.ok(!code.includes('setItem'));assert.ok(h.html.includes(attempt));
});
test('pending HTTP success is not shown as a paired Node',async()=>{
 const h=harness();await h.open();h.result=pending;
 await assert.rejects(h.send({code:'DXN1.fixture'}),/not confirmed/);assert.equal(h.closed,0);assert.equal(h.messages.length,0);
});
test('a confirmed response closes only the owning dialog',async()=>{
 const h=harness();await h.open();await h.send({code:'DXN1.fixture'});
 assert.equal(h.closed,1);assert.equal(h.calls[1].path,'/api/nodes/pair');assert.equal(h.messages.length,1);
});
test('saved retry needs explicit consent and no conflicting Pair Code',async()=>{
 const h=harness([pending]);await h.open();
 await assert.rejects(h.send({resume:attempt}),/explicitly confirm/);
 await assert.rejects(h.send({resume:attempt,confirmRetry:'on',code:'DXN1.fixture'}),/leave Pair Code empty/);
 assert.equal(h.calls.length,1);await h.send({resume:attempt,confirmRetry:'on'});
 assert.equal(h.calls[1].path,'/api/nodes/pairings/'+attempt+'/retry');assert.equal(h.calls[1].body.confirmRetry,true);
});
test('unknown attempts and responses for another attempt never become success',async()=>{
 const h=harness([pending]);await h.open();await assert.rejects(h.send({resume:'b'.repeat(32),confirmRetry:'on'}));
 h.result={...completed,attempt_id:'b'.repeat(32)};
 await assert.rejects(h.send({resume:attempt,confirmRetry:'on'}),/not confirmed/);assert.equal(h.closed,0);
});
test('late completion cannot close a new dialog or a changed owner session',async()=>{
 const h=harness();await h.open();await h.submit(new Map([['code','DXN1.fixture']]),{isConnected:false});assert.equal(h.closed,0);
 h.state.me=null;await h.send({code:'DXN1.fixture'});assert.equal(h.closed,0);assert.equal(h.messages.length,0);
});
test('read-only owner sees saved work without a mutation submit handler',async()=>{
 const h=harness([pending]);h.state.me.writes_enabled=false;await h.open();assert.equal(h.submit,null);assert.equal(h.calls.length,1);
});
test('duplicate or malformed saved identities reject actionable UI, names are escaped',async()=>{
 const bad=harness([pending,pending]);await assert.rejects(bad.open(),/Invalid saved/);assert.equal(bad.submit,undefined);
 const h=harness([{...pending,name:'<img src=x onerror="alert(1)">'}]);await h.open();assert.ok(!h.html.includes('<img'));assert.ok(h.html.includes('&lt;img'));
});
test('an incomplete positive response cannot close the dialog',async()=>{
 const h=harness();await h.open();h.result={...completed};delete h.result.node;delete h.result.node_id;
 await assert.rejects(h.send({code:'DXN1.fixture'}),/not confirmed/);assert.equal(h.closed,0);
});

const cancelled=(discarded=false)=>({attempt_id:attempt,node_id:pending.node_id,origin:pending.origin,
 phase:'cancelled',cancelled:true,paired:false,registration_current:false,
 outcome:discarded?'discarded_after_rotation':'withdrawn_before_rotation',
 pair_code_consumed:discarded,candidate_discarded:discarded,candidate_requires_reinstall:discarded,
 credential_revocation_confirmed:discarded,remote_idle_confirmed:discarded,
 credentials_retained_in_journal:false,reservation_released:true,
 status_scope:'saved_pairing_cancellation_receipt',live_state_verified:false});
const cancelFields={resume:attempt,pairAction:'cancel',confirmCancel:'on',acknowledgeCredentialReset:'on'};
test('cancel needs selection, empty code and two confirmations distinct from retry',async()=>{
 const h=harness([pending]);await h.open();h.result=cancelled();
 for(const change of [{resume:''},{confirmCancel:''},{acknowledgeCredentialReset:''},{code:'DXN1.conflict'},{confirmRetry:'on'}]){
  await assert.rejects(h.send({...cancelFields,...change}),/confirm both/);
 }
 assert.equal(h.calls.length,1);assert.equal(h.closed,0);
 await h.send(cancelFields);assert.equal(h.calls[1].path,'/api/nodes/pairings/'+attempt+'/cancel');
 assert.deepEqual(JSON.parse(JSON.stringify(h.calls[1].body)),{confirmCancel:true,acknowledgeCredentialReset:true});
 assert.equal(h.closed,1);assert.match(h.messages[0],/without contacting/);
});
test('confirmed remote cancellation warns about reinstall, never says Node paired',async()=>{
 const h=harness([pending]);await h.open();h.result=cancelled(true);await h.send(cancelFields);
 assert.match(h.messages[0],/Reinstall/);assert.doesNotMatch(h.messages[0],/Node paired/);
});
test('cancelling status is readable but cannot resume enrollment',async()=>{
 const h=harness([{...pending,phase:'cancelling'}]);await h.open();assert.match(h.html,/CANCELLATION PENDING/);
 await assert.rejects(h.send({resume:attempt,confirmRetry:'on'}),/Cancellation has started/);
 assert.equal(h.calls.length,1);
 h.result={...pending,phase:'cancelling'};await assert.rejects(h.send(cancelFields),/Cancellation is not confirmed/);
 assert.equal(h.closed,0);assert.equal(h.messages.length,0);
});
test('malformed or mismatched cancellation receipts never close the view',async()=>{
 for(const change of [{attempt_id:'b'.repeat(32)},{node_id:'other'},{origin:'https://other.test'},
  {paired:true},{remote_idle_confirmed:true},{reservation_released:false},{live_state_verified:true},
  {status_scope:'live'},{outcome:'unknown'},{cancelled:'true'},{credentials_retained_in_journal:true}]){
  const h=harness([pending]);await h.open();h.result={...cancelled(),...change};
  await assert.rejects(h.send(cancelFields),/Cancellation is not confirmed/);assert.equal(h.closed,0);
 }
});
test('stale write capability prevents cancel, both before dispatch and after a late response',async()=>{
 const h=harness([pending]);await h.open();h.result=cancelled();h.state.me.writes_enabled=false;
 await h.send(cancelFields);assert.equal(h.calls.length,1);
 h.state.me.writes_enabled=true;const original=h.context.api;
 h.context.api=async(...args)=>{const doc=await original(...args);h.state.me={id:'reseller',role:'reseller'};return doc;};
 await h.send(cancelFields);assert.equal(h.closed,0);assert.equal(h.messages.length,0);
});
test('two direct cancel submissions share one in-flight request',async()=>{
 const h=harness([pending]);await h.open();h.result=cancelled();let release;
 const original=h.context.api;h.context.api=async(...args)=>{const doc=await original(...args);await new Promise(r=>release=r);return doc;};
 const first=h.send(cancelFields);await new Promise(r=>setImmediate(r));await h.send(cancelFields);
 assert.equal(h.calls.length,2);release();await first;assert.equal(h.closed,1);
});
test('selection changes and requests clear all consent; requests also erase the Pair Code',async()=>{
 const h=harness([pending]);const controls={code:{value:'secret'},confirmRetry:{checked:true},confirmCancel:{checked:true},acknowledgeCredentialReset:{checked:true}};
 const form={isConnected:true,querySelector:s=>controls[s.slice(6,-1)],addEventListener:(event,handler)=>{h.change=handler;}};
 h.context.$=()=>form;await h.open();h.change({target:{name:'pairAction'}});
 for(const name of ['confirmRetry','confirmCancel','acknowledgeCredentialReset'])assert.equal(controls[name].checked,false);
 h.result=cancelled();for(const name of ['confirmCancel','acknowledgeCredentialReset'])controls[name].checked=true;
 await h.submit(new Map(Object.entries(cancelFields)),form);
 assert.equal(controls.code.value,'');for(const name of ['confirmRetry','confirmCancel','acknowledgeCredentialReset'])assert.equal(controls[name].checked,false);
});
test('cancel consent cannot accidentally submit enrollment',async()=>{
 const h=harness([pending]);await h.open();
 await assert.rejects(h.send({...cancelFields,pairAction:'resume'}),/Choose cancellation/);
 await assert.rejects(h.send({...cancelFields,pairAction:'unknown'}),/Invalid connection action/);
 assert.equal(h.calls.length,1);
});
