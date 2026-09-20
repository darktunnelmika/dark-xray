const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'..','web','nodes-v2.js'),'utf8');
function context(result={},lang='en',node={}){
 const messages=[],calls=[];
 const x={console,state:{page:'nodes',inbounds:[],me:{}},localStorage:{getItem:()=>lang},
  enginePage:async()=>'',runAction:async()=>{},renderPage:async()=>{},
  e:v=>String(v??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;'),
  heading:(a,b,c='')=>a+b+c,button:(label,act,icon,attrs)=>`<button data-act="${act}" ${attrs||''}>${label}</button>`,
  fa:String,date:String,bytes:String,empty:String,enc:encodeURIComponent,
  toast:s=>messages.push(s),confirm:()=>true,dialog:(title,html)=>{x.dialogHTML=html;},
  field:()=>'',closeDialog:()=>{},$:()=>null,
  api:async(url,method,body)=>{calls.push(url);if(url==='/api/nodes')return [{id:'n1',name:'Node',enabled:1,online:true,...node}];if(url==='/api/nodes/orchestration')return null;return result;}
 };
 vm.createContext(x);vm.runInContext(source,x);return {x,messages,calls};
}
const element=action=>({dataset:{id:'n1',core:action}});
for(const lang of ['en','fa']){
 test(`queued response never claims execution (${lang})`,async()=>{
  const {x,messages}=context({queued:true,executed:false,control:{pending:true}},lang);
  await x.runAction('nv2core',element('restart'));
  assert.match(messages[0],lang==='fa'?/در انتظار/:/awaiting/);
  assert.doesNotMatch(messages[0],/action completed|اجرای فرمان هسته را تأیید/);
 });
 test(`acknowledged response is distinct (${lang})`,async()=>{
  const {x,messages}=context({queued:false,executed:true,control:{pending:false}},lang);
  await x.runAction('nv2core',element('start'));
  assert.match(messages[0],lang==='fa'?/اجرای فرمان هسته را تأیید/:/acknowledged/);
 });
}
for(const [reason,pattern] of [['unsupported_agent',/update the Node Agent/],['disabled',/until the Node is enabled/],['identity_mismatch',/identity does not match/],['configuration_pending',/configuration synchronization/]]){
 test(`pending ${reason} has actionable wording`,async()=>{
  const {x,messages}=context({queued:true,delivery_state:reason});
  await x.runAction('nv2core',element('stop'));assert.match(messages[0],pattern);
 });
}
test('fleet and manage dialog expose pending identity, escaped errors and Start/Stop',async()=>{
 const {x}=context({},'en',{control:{persisted:true,pending:true,action:'stop',revision:4,last_error:'<script>alert(1)</script>'}});
 const html=await x.enginePage();
 assert.match(html,/COMMAND PENDING/);assert.match(html,/STOP · r4/);
 assert.match(html,/&lt;script&gt;/);assert.doesNotMatch(html,/<script>/);
 await x.runAction('nv2edit',{dataset:{id:'n1'}});
 for(const key of ['Start Xray','Stop Xray','Restart Xray','data-core="start"','data-core="stop"','COMMAND PENDING'])assert.ok(x.dialogHTML.includes(key),key);
});
test('acknowledged command is labelled historical, not live health',async()=>{
 const {x}=context({},'en',{control:{persisted:true,pending:false,action:'start',revision:2}});
 const html=await x.enginePage();assert.match(html,/LAST COMMAND ACKNOWLEDGED/);assert.match(html,/not a live health check/);
});
test('duplicate clicks during an in-flight command make only one API call',async()=>{
 const {x,messages,calls}=context();let release;
 x.api=async url=>{calls.push(url);return new Promise(resolve=>{release=resolve;});};
 const first=x.runAction('nv2core',element('restart'));
 await x.runAction('nv2core',element('restart'));assert.equal(calls.length,1);
 release({executed:true});await first;assert.equal(messages.length,1);
});
test('cancelled Stop and unknown action send no command',async()=>{
 const {x,calls}=context();x.confirm=()=>false;
 await x.runAction('nv2core',element('stop'));await x.runAction('nv2core',element('shell'));assert.equal(calls.length,0);
});
test('deferred sync never reports Node synchronized',async()=>{
 const {x,messages}=context({queued:true,sync_deferred:true});
 await x.runAction('nv2sync',{dataset:{id:'n1'}});assert.match(messages[0],/awaiting/);assert.doesNotMatch(messages[0],/Node synchronized/);
});
test('missing execution confirmation does not receive a success toast',async()=>{
 const {x,messages}=context({queued:false,executed:false,delivery_state:'superseded'});
 await x.runAction('nv2core',element('stop'));assert.match(messages[0],/No execution acknowledgement/);
});
