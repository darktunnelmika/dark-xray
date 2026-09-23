/* Real Manage Node function, small DOM/API harness; not browser or WAN proof. */
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const src=fs.readFileSync(require('node:path').join(__dirname,'../web/nodes-v2.js'),'utf8');
const code=src.slice(src.indexOf('async function nodeDialog'),src.indexOf('async function pairNodeDialog'));
const pending={node_id:'turkey',attempt_id:'a'.repeat(32),binding_id:'b'.repeat(32),phase:'rotating',pending:true,rotated:false,binding_current:true,credential_current:false};
const completed={...pending,phase:'completed',pending:false,rotated:true,credential_current:true};
function harness(status={...pending,phase:'not_started',pending:false}){
 const h={calls:[],closed:0,messages:[],result:completed,input:{value:'secret'},checkbox:{checked:true}};
 const state={me:{id:'owner',role:'owner',writes_enabled:true},nv2:{nodes:[{id:'turkey',name:'Turkey',origin:'https://tr.example.test',enabled:true,failover_enabled:true,inboundIds:[]}]},inbounds:[]};
 h.form={isConnected:true,querySelector:s=>s.includes('token')?h.input:h.checkbox};
 const ctx=vm.createContext({state,isOwner:()=>state.me?.role==='owner',L:en=>en,e:s=>String(s??'').replaceAll('<','&lt;').replaceAll('"','&quot;'),enc:encodeURIComponent,
  api:async(path,method='GET',body)=>{h.calls.push({path,method,body});if(h.delay)await h.delay;return method==='GET'?status:h.result;},
  field:()=>'',manageActions:()=>'',inboundPicker:()=>'',dialog:(t,html,submit)=>{h.html=html;h.submit=submit;},
  closeDialog:()=>h.closed++,toast:t=>h.messages.push(t),renderPage:async()=>{},$:()=>null});
 vm.runInContext(code+';globalThis.openNode=nodeDialog;',ctx);h.state=state;h.open=()=>ctx.openNode('turkey');
 h.send=(values={})=>h.submit({get:k=>({name:'Turkey',origin:'https://tr.example.test',enabled:'true',failoverEnabled:'true',priority:'100',dataAddress:'',...values}[k]),getAll:()=>[]},h.form);return h;
}
test('opening only reads saved status and does not persist secrets or consent',async()=>{
 const h=harness(pending);await h.open();assert.deepEqual(h.calls.map(x=>x.method),['GET']);assert.ok(h.html.includes('retryCredential'));
 assert.ok(!/name="retryCredential"[^>]*checked/.test(h.html));assert.ok(!code.includes('setItem'));
});
test('pending response to token edit is not a settings-saved success',async()=>{
 const h=harness();await h.open();h.result=pending;await assert.rejects(h.send({token:'dkn_fixture'}),/not confirmed/);
 assert.equal(h.closed,0);assert.equal(h.messages.length,0);assert.equal(h.input.value,'');
});
test('retry requires consent, empty token, same node and same saved attempt',async()=>{
 const h=harness(pending);await h.open();await assert.rejects(h.send(),/Resolve/);
 await assert.rejects(h.send({retryCredential:'on',token:'new'}),/Keep token empty/);
 h.result={...completed,attempt_id:'c'.repeat(32)};await assert.rejects(h.send({retryCredential:'on'}),/not confirmed/);
 assert.equal(h.closed,0);assert.equal(h.checkbox.checked,false);
});
test('successful saved retry reports token confirmation without PATCH or settings changes',async()=>{
 const h=harness(pending);await h.open();await h.send({retryCredential:'on'});
 assert.equal(h.calls[1].path,'/api/nodes/turkey/credentials/'+pending.attempt_id+'/retry');assert.equal(h.calls[1].body.confirmRetry,true);
 assert.equal(h.closed,1);assert.equal(h.messages[0],'Node token change confirmed.');
});
test('unknown phase and contradictory booleans are not actionable',async()=>{
 for(const status of [{...pending,phase:'unknown'},{...pending,pending:false},{...pending,rotated:true},{...pending,node_id:'other'}]){
  const h=harness(status);await assert.rejects(h.open(),/Invalid credential/);assert.equal(h.submit,undefined);
 }
});
test('read-only view and lost owner session cannot submit',async()=>{
 const h=harness(pending);h.state.me.writes_enabled=false;await h.open();assert.equal(h.submit,null);
 const x=harness(pending);await x.open();x.state.me=null;await x.send({retryCredential:'on'});assert.equal(x.calls.length,1);
});
test('two concurrent submits issue only one request',async()=>{
 const h=harness(pending);await h.open();let release;h.delay=new Promise(r=>release=r);
 const first=h.send({retryCredential:'on'});await h.send({retryCredential:'on'});assert.equal(h.calls.length,2);release();await first;
});
test('completion cannot close another form or follow a revoked write permission',async()=>{
 const h=harness();await h.open();h.form.isConnected=false;await h.send({token:'x'});assert.equal(h.calls.length,1);
 const x=harness();await x.open();let release;x.delay=new Promise(r=>release=r);const sending=x.send({token:'x'});
 x.state.me.writes_enabled=false;release();await sending;assert.equal(x.closed,0);assert.equal(x.messages.length,0);
});
