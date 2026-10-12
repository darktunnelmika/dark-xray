const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');

const root=path.resolve(__dirname,'..');
const src=fs.readFileSync(path.join(root,'web/dashboard-fastpath-v2.js'),'utf8');
const overview=fs.readFileSync(path.join(root,'web/overview-v4.js'),'utf8');
const html=fs.readFileSync(path.join(root,'web/index.html'),'utf8');
const sleep=ms=>new Promise(r=>setTimeout(r,ms));

function runtime(reply,role='owner'){
 const calls=[],rendered=[],intervals=[];
 const content={innerHTML:'',busy:false,
   setAttribute(name,value){if(name==='aria-busy')this.busy=value==='true';},
   removeAttribute(name){if(name==='aria-busy')this.busy=false;},
   insertAdjacentHTML(_where,value){this.innerHTML=value+this.innerHTML;}};
 const state={me:{id:'dark',role},page:'dashboard',busy:false,
   system:null,sync:null,errors:{},clients:[],inbounds:[],resellers:[],owners:[],history:[],
   ov2:{nodes:[],overview:null,overviewOwner:'',overviewError:'',core:null,backup:null,audit:[],ip:null},
   ov4:{history:{cpu:[],mem:[],swap:[],disk:[],up:[],down:[],tcp:[],udp:[]},
     capture(){state.captured=(state.captured||0)+1;}},
   updateCenter:{status:null,error:''}
 };
 const doc={hidden:false,activeElement:null,getElementById:id=>id==='content'?content:
   id==='overlay'?{classList:{contains:()=>false}}:null};
 let legacyLoads=0;
 const invoke=(uri,method='GET',body,options)=>{calls.push({uri,method,body,options});return reply(uri,options);};
 // Model the existing base + Clients V2 wrapper, not a replacement
 // navigation loader. Future wrappers are preserved by FastPath.
 const baseResources={clients:['clients','inbounds','owners','sync'],
   inbounds:['inbounds','clients','sync'],resellers:['resellers','owners','inbounds'],
   finance:['resellers','owners'],sync:['sync'],nodes:['inbounds']};
 const originalLoad=async()=>{
   legacyLoads++;
   const keys=baseResources[state.page]||[];
   await Promise.all(keys.map(async key=>{
     try{state[key]=await invoke('/api/'+key);delete state.errors[key];}
     catch(ex){state.errors[key]=String(ex?.message||ex);}
   }));
   if(state.page==='clients'){
     try{state.groups=await invoke('/api/groups');delete state.errors.groups;}
     catch(ex){state.errors.groups=String(ex?.message||ex);}
   }
 };
 const ctx={state,api:invoke,pageResources:page=>baseResources[page]||[],
   can:()=>true,isOwner:()=>state.me?.role==='owner',
   load:originalLoad,
   go:async page=>{state.page=page;content.innerHTML='legacy shell';},
   renderPage:async()=>{rendered.push(state.page);content.innerHTML='page rendered';},
   setTimeout:(fn,ms)=>setTimeout(fn,Math.min(ms,50)),clearTimeout,
   setInterval:(fn,ms)=>{intervals.push({fn,ms});return 1;},
   AbortController,document:doc,localStorage:{getItem:()=> 'en'}
 };
 vm.createContext(ctx);vm.runInContext(src,ctx,{filename:'dashboard-fastpath-v2.js'});
 return {ctx,state,calls,rendered,content,intervals,legacyLoads:()=>legacyLoads};
}
const sample=(cpu=13)=>({source:'DARK local host',engine:{cpu,
  mem:{current:1,total:2},netIO:{up:0,down:0},
  connections:{available:true,open:0,tcp:0,udp:0}}});
const brief={read_only:true,freshness:'fresh',generated_at:1000,
 clients:{total:3,policy_eligible:2,policy_blocked:1,
          current_stored_usage_bytes:42,usage_scope:'current-stored-counters-not-lifetime-ledger'},
 representatives:{total:1,enabled:1},inbounds:{total:1,enabled:1},
 nodes:{total:2,enabled:2,recent_heartbeat:1,status_source:'cached-heartbeat-only-not-route-readiness'}};

test('V2 is loaded last, uses authenticated GETs, and never mutates accounting or Nodes',()=>{
 assert.ok(html.indexOf('assets/dashboard-fastpath-v2.js')>html.indexOf('assets/ui-stability.js'));
 assert.ok(!html.includes('assets/dashboard-fastpath-v1.js'));
 assert.match(src,/\/api\/dashboard\/summary/);
 assert.match(src,/\/api\/system\?live_only=true/);
 assert.doesNotMatch(src,/['"]POST['"]|\/api\/core\/restart|\/api\/nodes\/[^'"]+\/sync/);
 assert.doesNotMatch(src,/\{key:'clients',path:|\/api\/resellers',ttl:/);
});

test('first paint never awaits blocked APIs and summary is independent of node errors',async()=>{
 const pending=new Promise(()=>{});
 const t=runtime(uri=>uri==='/api/dashboard/summary'?brief:
   uri==='/api/system?live_only=true'?sample():pending);
 await t.ctx.load();
 assert.equal(t.legacyLoads(),0);
 await sleep(12);
 assert.equal(t.state.dashboardFast.summary.clients.total,3);
 assert.equal(t.state.system.engine.cpu,13);
 assert.equal(t.state.dashboardFast.status.summary,'ready');
 assert.equal(t.state.dashboardFast.active<=3,true);
 assert.ok(t.calls.some(c=>c.uri==='/api/nodes'));
 assert.ok(!t.calls.some(c=>c.uri==='/api/clients'||c.uri==='/api/resellers'));
});

test('timeouts release queue slots and a stale backend summary is labeled stale',async()=>{
 const never=new Promise(()=>{});
 const t=runtime(uri=>uri==='/api/dashboard/summary'?{...brief,freshness:'stale'}:
   uri==='/api/system?live_only=true'?sample(0):never);
 await t.ctx.load();
 await sleep(120);
 assert.equal(t.state.dashboardFast.status.summary,'stale');
 assert.equal(t.state.system.engine.cpu,0);
 assert.ok(t.calls.some(c=>c.uri==='/api/operations/overview'),
   'queue must progress after a Node timeout');
 assert.ok(t.state.dashboardFast.active<=3);
});

test('changing principal rejects late privileged responses',async()=>{
 let resolveSummary;
 const pending=new Promise(r=>{resolveSummary=r;});
 const t=runtime(uri=>uri==='/api/dashboard/summary'?pending:sample());
 await t.ctx.load();
 t.state.me={id:'other',role:'reseller'};
 resolveSummary(brief);
 await sleep(10);
 assert.equal(t.state.dashboardFast.summary,null);
});

test('navigation loads only required target page data before verified render',async()=>{
 const never=new Promise(()=>{});
 const t=runtime(uri=>{
  if(uri==='/api/dashboard/summary')return brief;
  if(uri.startsWith('/api/system?'))return sample();
  if(uri==='/api/clients')return [{email:'visible-client'}];
  if(uri==='/api/inbounds')return [{id:10}];
  if(uri==='/api/owners')return [{id:'dark'}];
  if(uri==='/api/sync')return {last_poll:42};
  if(uri==='/api/groups')return [{name:'canary-group',owner:'dark'}];
  return never;
 });
 await t.ctx.load();
 await t.ctx.go('clients');
 assert.equal(t.state.page,'clients');
 assert.equal(t.state.clients[0].email,'visible-client');
 assert.equal(t.state.inbounds[0].id,10);
 assert.equal(t.state.owners[0].id,'dark');
 assert.equal(t.state.groups[0].name,'canary-group');
 assert.ok(t.legacyLoads()>0,'original specialized loader must execute');
 assert.equal(t.content.innerHTML,'page rendered');
 assert.ok(!t.calls.some(x=>x.uri==='/api/resellers'));
});

test('failed page fetch shows an error rather than an invented empty list',async()=>{
 const never=new Promise(()=>{});
 const t=runtime(uri=>{
  if(uri==='/api/dashboard/summary')return brief;
  if(uri.startsWith('/api/system?'))return sample();
  if(uri==='/api/clients')throw new Error('Client DB unavailable');
  if(['/api/inbounds','/api/owners','/api/sync','/api/groups'].includes(uri))return [];
  return never;
 });
 await t.ctx.load();
 await t.ctx.go('clients');
 assert.match(t.state.errors.clients,/Client DB unavailable/);
 assert.equal(t.content.innerHTML,'page rendered');
});

test('rendered summary is sourced from FastPath, not loaded client arrays',()=>{
 assert.match(overview,/function currentSummary\(\)/);
 assert.match(overview,/clients\.policy_eligible/);
 assert.match(overview,/clients\.current_stored_usage_bytes/);
 assert.match(overview,/not lifetime ledger/);
 assert.match(overview,/sourceReady\('nodes',45000\)/);
 assert.doesNotMatch(overview,/const clients=state\.clients\|\|\[\]/);
});

test('re-entering Sync fetches fresh drift instead of reusing a short-lived cache',async()=>{
 let version=0;
 const t=runtime(uri=>{
  if(uri==='/api/dashboard/summary')return brief;
  if(uri.startsWith('/api/system?'))return sample();
  if(uri==='/api/sync')return {items:[{email:'a',reason_code:++version===1?'clean':'external_disabled'}]};
  return [];
 });
 await t.ctx.load();
 await t.ctx.go('sync');
 assert.equal(t.state.sync.items[0].reason_code,'clean');
 await t.ctx.go('dashboard');
 await t.ctx.go('sync');
 assert.equal(t.state.sync.items[0].reason_code,'external_disabled');
 assert.equal(t.calls.filter(c=>c.uri==='/api/sync').length,2);
});

test('workspace remains marked busy while page hydration is pending',async()=>{
 let resolveClients;
 const pendingClients=new Promise(r=>{resolveClients=r;});
 const t=runtime(uri=>{
  if(uri==='/api/dashboard/summary')return brief;
  if(uri.startsWith('/api/system?'))return sample();
  if(uri==='/api/clients')return pendingClients;
  if(['/api/inbounds','/api/owners','/api/sync'].includes(uri))return [];
  return [];
 });
 await t.ctx.load();
 const nav=t.ctx.go('clients');
 await sleep(5);
 assert.equal(t.content.busy,true);
 assert.match(t.content.innerHTML,/Loading current page data/);
 assert.match(t.content.innerHTML,/legacy shell/);
 resolveClients([{email:'actual'}]);
 await nav;
 assert.equal(t.content.busy,false);
 assert.equal(t.content.innerHTML,'page rendered');
});

test('V2 preserves the complete existing loader stack on non-Dashboard pages',async()=>{
 const t=runtime(uri=>uri==='/api/dashboard/summary'?brief:
   uri.startsWith('/api/system?')?sample():uri==='/api/groups'?[{name:'preserved',owner:'dark'}]:[]);
 await t.ctx.load();
 assert.equal(t.legacyLoads(),0);
 await t.ctx.go('clients');
 assert.equal(t.legacyLoads(),1);
 assert.equal(t.state.groups[0].name,'preserved');
 assert.match(src,/const previousLoad=load/);
 assert.match(src,/return previousLoad\(\)/);
});
