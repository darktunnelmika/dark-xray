/* DARK XRAY Dashboard FastPath V2
 * Independent, abortable read-only monitoring and bounded page hydration.
 * Never mutates Xray, Nodes, routing, leases, clients, accounting or settings.
 */
(function(){
'use strict';
if(typeof load!=='function'||typeof api!=='function'||typeof state==='undefined')return;
const previousLoad=load,previousGo=typeof go==='function'?go:null;
const MAX_CONCURRENT=3,DEFAULT_TIMEOUT=3500;
const sources=[
  {key:'summary',path:'/api/dashboard/summary',ttl:12000,timeout:1800,when:()=>isOwner()||state.me?.role==='reseller'},
  {key:'system',path:'/api/system?live_only=true',ttl:12000,timeout:2500,when:()=>can('system.read')},
  {key:'nodes',path:'/api/nodes',ttl:30000,timeout:4000,when:()=>isOwner()},
  {key:'operations',path:'/api/operations/overview',ttl:40000,timeout:4000,when:()=>isOwner()},
  {key:'core',path:'/api/core/state',ttl:40000,timeout:4000,when:()=>isOwner()},
  {key:'update',path:'/api/update/status',ttl:65000,timeout:5000,when:()=>isOwner()},
  {key:'backup',path:'/api/backup/status',ttl:120000,timeout:4500,when:()=>isOwner()},
  {key:'audit',path:'/api/audit',ttl:120000,timeout:4500,when:()=>can('audit.read')},
  {key:'ip',path:'/api/ip/events',ttl:120000,timeout:4500,when:()=>can('clients.ip')},
  {key:'sync',path:'/api/sync',ttl:60000,timeout:4500,when:()=>true}
];
// Dashboard never requests large /api/clients, /api/resellers or /api/owners.
const now=()=>Date.now();
const ownerIdentity=()=>state.me;
const same=(box)=>!!box&&!!state.me&&box.identity===ownerIdentity();
const visible=(box)=>same(box)&&state.page==='dashboard';
function ensure(){
  if(same(state.dashboardFast))return state.dashboardFast;
  // Clear privileged caches when the authenticated identity changes.
  const box={identity:ownerIdentity(),summary:null,last:Object.create(null),
    attempts:Object.create(null),status:Object.create(null),
    errors:Object.create(null),inflight:Object.create(null),
    controllers:Object.create(null),queue:[],queued:new Set(),
    active:0,renderTimer:null,navigation:0,pageLoadVersion:0,pageLast:Object.create(null)};
  if(state.dashboardFast){
    state.clients=[];state.inbounds=[];state.owners=[];state.resellers=[];
    state.sync=null;state.system=null;state.errors={};state.history=[];
    if(state.ov2){state.ov2.nodes=[];state.ov2.overview=null;
      state.ov2.overviewError='';state.ov2.overviewOwner='';
      state.ov2.core=null;state.ov2.audit=[];state.ov2.ip=null;state.ov2.backup=null;}
    if(state.updateCenter){state.updateCenter.status=null;state.updateCenter.error='';}
    if(state.ov4?.history)Object.values(state.ov4.history).forEach(a=>a.splice(0));
  }
  state.dashboardFast=box;
  return box;
}
function renderSoon(box){
  if(!visible(box)||box.renderTimer!==null)return;
  box.renderTimer=setTimeout(async()=>{
    box.renderTimer=null;
    if(!visible(box))return;
    if(state.busy){renderSoon(box);return;}
    const overlay=document.getElementById('overlay');
    if(overlay?.classList.contains('show'))return;
    const focus=document.activeElement;
    if(focus&&document.getElementById('content')?.contains(focus)&&
       (focus.matches?.('input,textarea,select,[contenteditable=true]')||focus.closest?.('[contenteditable=true]')))return;
    try{await renderPage();}catch(_ex){}
  },180);
}
function record(box,key,value){
  if(!same(box))return;
  if(key==='summary'){
    if(!value||value.read_only!==true||!value.clients)throw Error('Invalid Dashboard summary');
    box.summary=value;
  }else if(key==='system'){
    if(!value?.engine)throw Error('Host telemetry unavailable');
    state.system=value;delete state.errors.system;
    const cpu=value.engine.cpu;
    if(cpu!==null&&cpu!==undefined&&Number.isFinite(Number(cpu))){
      state.history.push(Number(cpu));state.history=state.history.slice(-60);
    }
    if(typeof state.ov4?.capture==='function')state.ov4.capture();
  }else if(key==='nodes'){if(!Array.isArray(value))throw Error('Invalid node snapshot');state.ov2.nodes=value;}
  else if(key==='operations'){
    state.ov2.overview=value;state.ov2.overviewOwner=state.me?.id||state.me?.username||'owner';
    state.ov2.overviewError='';
  }else if(key==='core')state.ov2.core=value;
  else if(key==='update'){state.updateCenter.status=value;state.updateCenter.error='';}
  else if(key==='backup')state.ov2.backup=value;
  else if(key==='audit'){if(!Array.isArray(value))throw Error('Invalid audit snapshot');state.ov2.audit=value;}
  else if(key==='ip')state.ov2.ip=value;
  else if(key==='sync'){state.sync=value;delete state.errors.sync;}
}
function boundedRead(path,timeout){
  const controller=new AbortController();
  let timer;
  const timeoutPromise=new Promise((_,reject)=>{
    timer=setTimeout(()=>{controller.abort();reject(new Error('Telemetry request timed out'));},timeout);
  });
  const operation=Promise.resolve().then(()=>api(path,'GET',undefined,{signal:controller.signal}));
  return {controller,promise:Promise.race([operation,timeoutPromise]).finally(()=>clearTimeout(timer))};
}
function request(box,source){
  if(!visible(box))return Promise.resolve();
  const key=source.key,time=now();
  if(box.inflight[key])return box.inflight[key];
  if(box.attempts[key]&&time-box.attempts[key]<source.ttl)return Promise.resolve();
  box.attempts[key]=time;box.status[key]='loading';
  const {controller,promise}=boundedRead(source.path,source.timeout||DEFAULT_TIMEOUT);
  box.controllers[key]=controller;
  const task=promise.then(value=>{
    if(!visible(box))return;
    record(box,key,value);
    box.last[key]=now();
    box.status[key]=key==='summary'&&value?.freshness==='stale'?'stale':'ready';
    delete box.errors[key];
  }).catch(ex=>{
    if(!visible(box))return;
    box.errors[key]=String(ex?.message||ex).slice(0,160);
    // A transient timeout must not turn real previously observed data into zero.
    box.status[key]='error';
    if(key==='operations')state.ov2.overviewError='unavailable';
    if(key==='update')state.updateCenter.error=box.errors[key];
    if(key==='system')state.errors.system=box.errors[key];
  }).finally(()=>{
    delete box.inflight[key];delete box.controllers[key];
    if(visible(box))renderSoon(box);
  });
  box.inflight[key]=task;
  return task;
}
function pump(box){
  if(!visible(box)){box.queue=[];box.queued.clear();return;}
  while(box.active<MAX_CONCURRENT&&box.queue.length){
    const source=box.queue.shift();box.queued.delete(source.key);box.active++;
    request(box,source).finally(()=>{box.active--;pump(box);});
  }
}
function schedule(box){
  const stamp=now();
  for(const source of sources){
    if(!source.when()||box.queued.has(source.key)||box.inflight[source.key])continue;
    if(box.attempts[source.key]&&stamp-box.attempts[source.key]<source.ttl)continue;
    box.queue.push(source);box.queued.add(source.key);
  }
  pump(box);
}
function dashboardLoad(){
  if(!state.me)return Promise.resolve();
  const box=ensure();
  if(visible(box))schedule(box);
  // Never block first paint on any API, including system and summary.
  return Promise.resolve();
}
// Preserve every specialized loader installed by other workspace modules.
// In particular, Clients V2 loads /api/groups and Update Center owns its state.
load=async function(){
  if(!state.me)return previousLoad();
  if(state.page==='dashboard')return dashboardLoad();
  const box=ensure();box.pageLoadVersion++;
  return previousLoad();
};
if(previousGo){
  go=async function(page){
    if(!state.me)return previousGo(page);
    const box=ensure();
    box.navigation++;
    const seq=box.navigation,oldPage=state.page,loadMarker=box.pageLoadVersion;
    if(oldPage==='dashboard'&&page!=='dashboard'){
      for(const ctl of Object.values(box.controllers))ctl.abort();
      box.queue=[];box.queued.clear();
    }
    await previousGo(page);
    if(page==='dashboard'){
      void dashboardLoad();
      return;
    }
    if(!same(box)||state.page!==page||seq!==box.navigation)return;
    // The existing shell is rendered immediately. A visible loading state
    // replaces any misleading empty list until actual page data arrives.
    const target=document.getElementById('content');
    const pending=typeof pageResources==='function'?pageResources(page).length>0:page!=='account';
    // Keep the actual workspace shell visible, but mark it busy until its
    // backing data is fresh. Do not replace a rendered page with a fake
    // "empty" placeholder that looks like a finished workspace.
    if(target&&pending){
      target.setAttribute?.('aria-busy','true');
      target.insertAdjacentHTML?.('afterbegin','<div class="notice" role="status">'+
        (localStorage.getItem('dark_lang')==='fa'?
         'در حال دریافت اطلاعات واقعی این بخش…':'Loading current page data…')+'</div>');
    }
    try{
      // Re-entering Sync/Clients needs fresh data, but the full loader stack
      // must remain intact (Groups, Operations, Update Center and future wrappers).
      if(box.pageLoadVersion===loadMarker)await load();
      if(same(box)&&state.page===page&&seq===box.navigation)await renderPage();
    }finally{
      document.getElementById('content')?.removeAttribute?.('aria-busy');
    }
  };
}
// Only real active dashboards are polled, with a bounded independent queue.
setInterval(()=>{
  const box=state.dashboardFast;
  if(!visible(box)||document.hidden||document.getElementById('overlay')?.classList.contains('show'))return;
  dashboardLoad();
},12000);
})();