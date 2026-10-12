/* DARK XRAY Dashboard Fastpath V1 — read-only, progressive monitoring.
 * The old full loader remains untouched on every non-Dashboard page.
 * No timers here trigger writes, Node probes, Xray apply or reconciliation.
 */
(function(){
'use strict';
if(typeof load!=='function'||typeof api!=='function'||typeof state==='undefined')return;
const standardLoad=load,MAX_BACKGROUND=2,CRITICAL_BUDGET_MS=1800;
const sources=[
  {key:'operations',path:'/api/operations/overview',ttl:20000,when:()=>isOwner()},
  {key:'nodes',path:'/api/nodes',ttl:25000,when:()=>isOwner()},
  {key:'sync',path:'/api/sync',ttl:25000,when:()=>true},
  {key:'core',path:'/api/core/state',ttl:25000,when:()=>isOwner()},
  {key:'inbounds',path:'/api/inbounds',ttl:60000,when:()=>true},
  {key:'clients',path:'/api/clients',ttl:75000,when:()=>true},
  {key:'resellers',path:'/api/resellers',ttl:75000,when:()=>isOwner()},
  {key:'owners',path:'/api/owners',ttl:75000,when:()=>true},
  {key:'backup',path:'/api/backup/status',ttl:90000,when:()=>isOwner()},
  {key:'update',path:'/api/update/status',ttl:55000,when:()=>isOwner()},
  {key:'audit',path:'/api/audit',ttl:90000,when:()=>can('audit.read')},
  {key:'ip',path:'/api/ip/events',ttl:90000,when:()=>can('clients.ip')}
];
const systemSource={key:'system',path:'/api/system?live_only=true',ttl:11000};
function valid(box){return !!state.me&&state.me===box.identity&&state.page==='dashboard';}
function ensure(){
  if(state.dashboardFast?.identity===state.me)return state.dashboardFast;
  const old=state.dashboardFast;
  const box={identity:state.me,last:Object.create(null),attempts:Object.create(null),
    status:Object.create(null),errors:Object.create(null),inflight:Object.create(null),
    queue:[],queued:new Set(),active:0,renderTimer:null};
  state.dashboardFast=box;
  // Never display privileged values collected for an earlier login/role.
  if(old){
    state.system=null;state.sync=null;state.clients=[];state.inbounds=[];
    state.owners=[];state.resellers=[];state.errors={};state.history=[];
    if(state.ov2){state.ov2.nodes=[];state.ov2.overview=null;state.ov2.overviewOwner='';
      state.ov2.core=null;state.ov2.backup=null;state.ov2.audit=[];state.ov2.ip=null;}
    if(state.updateCenter){state.updateCenter.status=null;state.updateCenter.error='';}
    if(state.ov4?.history)Object.values(state.ov4.history).forEach(a=>a.splice(0));
  }
  return box;
}
function record(box,key,value){
  if(!valid(box))return;
  if(key==='system'){
    if(!value||!value.engine)throw Error('Host telemetry unavailable');
    state.system=value;delete state.errors.system;
    const cpu=Number(value.engine.cpu);
    if(Number.isFinite(cpu)){state.history.push(cpu);state.history=state.history.slice(-60);}
    if(typeof state.ov4?.capture==='function')state.ov4.capture();
  }else if(key==='operations'){
    state.ov2.overview=value;state.ov2.overviewOwner=state.me?.id||state.me?.username||'owner';
    state.ov2.overviewError='';
  }else if(key==='nodes')state.ov2.nodes=Array.isArray(value)?value:[];
  else if(key==='core')state.ov2.core=value;
  else if(key==='backup')state.ov2.backup=value;
  else if(key==='audit')state.ov2.audit=Array.isArray(value)?value:[];
  else if(key==='ip')state.ov2.ip=value;
  else if(key==='update'){state.updateCenter.status=value;state.updateCenter.error='';}
  else if(['sync','clients','inbounds','owners','resellers'].includes(key)){
    state[key]=value;delete state.errors[key];
  }
}
function rerender(box){
  if(!valid(box)||box.renderTimer!==null)return;
  box.renderTimer=setTimeout(async()=>{
    box.renderTimer=null;
    if(!valid(box))return;
    if(state.busy){rerender(box);return;}
    if(typeof document!=='undefined'&&document.getElementById('overlay')?.classList.contains('show'))return;
    try{await renderPage();}catch(_ex){}
  },140);
}
function request(box,source){
  if(!valid(box))return Promise.resolve();
  const key=source.key,now=Date.now();
  if(box.inflight[key])return box.inflight[key];
  if(box.attempts[key]&&now-box.attempts[key]<source.ttl)return Promise.resolve();
  box.attempts[key]=now;box.status[key]='loading';
  const task=Promise.resolve().then(()=>api(source.path)).then(value=>{
    if(!valid(box))return;
    record(box,key,value);
    box.last[key]=Date.now();box.status[key]='ready';delete box.errors[key];
  }).catch(ex=>{
    if(!valid(box))return;
    box.status[key]='error';
    box.errors[key]=String(ex?.message||ex).slice(0,180);
    if(key==='system'){state.system=null;state.errors.system=box.errors[key];}
    if(key==='operations')state.ov2.overviewError='unavailable';
    if(key==='update')state.updateCenter.error=box.errors[key];
  }).finally(()=>{
    delete box.inflight[key];
    if(valid(box))rerender(box);
  });
  box.inflight[key]=task;
  return task;
}
function pump(box){
  if(!valid(box)){box.queue.length=0;box.queued.clear();return;}
  while(box.active<MAX_BACKGROUND&&box.queue.length){
    const source=box.queue.shift();
    box.queued.delete(source.key);
    ++box.active;
    request(box,source).finally(()=>{--box.active;pump(box);});
  }
}
function schedule(box){
  const now=Date.now();
  for(const source of sources){
    if(!source.when()||box.queued.has(source.key)||box.inflight[source.key])continue;
    if(box.attempts[source.key]&&now-box.attempts[source.key]<source.ttl)continue;
    box.queue.push(source);box.queued.add(source.key);
  }
  pump(box);
}
function deadline(promise){
  let timer;
  return Promise.race([promise,new Promise(resolve=>{timer=setTimeout(resolve,CRITICAL_BUDGET_MS);})])
    .finally(()=>clearTimeout(timer));
}
load=async function(){
  if(!state.me||state.page!=='dashboard')return standardLoad();
  const box=ensure();
  if(can('system.read'))await deadline(request(box,systemSource));
  if(valid(box))schedule(box);
};
})();