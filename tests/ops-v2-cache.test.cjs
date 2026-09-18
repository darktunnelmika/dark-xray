const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');

function context(){
 const calls=[],events=[];
 const ctx={
  console,
  state:{me:{id:'seller',role:'reseller',permissions:{}},page:'clients',clients:[],owners:[],inbounds:[],sync:null,system:null,errors:{}},
  enginePages:{},
  dashboard(){return ''},
  load:async()=>{events.push('baseLoad')},
  enginePage:async()=>'',
  navItems:()=>[],
  runAction:async()=>{},
  go:async page=>{ctx.state.page=page;events.push('baseGo:'+page)},
  refresh:async()=>{events.push('refresh')},
  renderPage:async()=>{},
  api:async u=>{calls.push(u);if(u==='/api/core/state')return {state:'running'};if(u==='/api/audit')return [{id:1}];if(u==='/api/ip/events')return {bans:[1]};if(u==='/api/backup/status')return {database_bytes:4096,managed_clients:3,restore_isolated:true,database_download:'/api/backup'};if(u==='/api/nodes')return [{id:'de-1',name:'DE-1',enabled:true,online:true,inboundIds:[1,2],assignments:[]}];return {};},
  isOwner:()=>ctx.state.me?.role==='owner',
  can:key=>ctx.state.me?.role==='owner'||ctx.state.me?.permissions?.[key]==='all'||ctx.state.me?.permissions?.[key]==='own',
  localStorage:{getItem:()=> 'en'},
  e:v=>String(v??''),heading:()=>'',button:()=>'',notices:()=>'',empty:()=>'',date:v=>String(v),fa:v=>String(v),bytes:v=>String(v),percent:v=>String(v),appUrl:v=>v,icon:()=>'',
 };
 vm.createContext(ctx);
 vm.runInContext(fs.readFileSync(path.join(__dirname,'..','web','ops-v2.js'),'utf8'),ctx,{filename:'ops-v2.js'});
 return {ctx,calls,events};
}

test('restricted account clears stale privileged Operations cache without heavy polling',async()=>{
 const {ctx,calls}=context();
 ctx.state.ov2.core={secret:'old-owner-core'};ctx.state.ov2.audit=[{target:'old-owner'}];ctx.state.ov2.ip={bans:[1]};
 ctx.state.page='clients';ctx.state.me={id:'seller',role:'reseller',permissions:{}};
 await ctx.load();
 assert.equal(ctx.state.ov2.core,null);
 assert.equal(Array.isArray(ctx.state.ov2.audit),true);
 assert.equal(ctx.state.ov2.audit.length,0);
 assert.equal(ctx.state.ov2.ip,null);
 assert.deepEqual(calls,[]);
});

test('dashboard fetches operational sources in addition to the base load',async()=>{
 const {ctx,calls}=context();
 ctx.state.page='dashboard';ctx.state.me={id:'dark',role:'owner',permissions:{}};
 await ctx.load();
 assert.deepEqual(new Set(calls),new Set(['/api/core/state','/api/audit','/api/ip/events','/api/backup/status','/api/nodes']));
 assert.equal(ctx.state.ov2.core.state,'running');
 assert.equal(ctx.state.ov2.audit.length,1);
 assert.equal(ctx.state.ov2.ip.bans.length,1);
 assert.equal(ctx.state.ov2.backup.database_bytes,4096);
 assert.equal(ctx.state.ov2.nodes.length,1);
});

test('entering dashboard triggers an immediate fresh load instead of waiting for timer',async()=>{
 const {ctx,events}=context();
 ctx.state.me={id:'dark',role:'owner',permissions:{}};
 await ctx.go('dashboard');
 assert.deepEqual(events,['baseGo:dashboard','refresh']);
});


test('dashboard priority order is health, nodes, update slot, recovery, metrics',async()=>{
 const {ctx}=context();
 ctx.state.page='dashboard';ctx.state.me={id:'dark',role:'owner',permissions:{}};
 await ctx.load();
 const html=ctx.dashboard();
 const order=['01 / HEALTH & ALERTS','02 / MULTI-NODE','dark-update-center-slot','04 / BACKUP & RECOVERY','05 / LIVE OPERATIONS'].map(x=>html.indexOf(x));
 assert.ok(order.every(x=>x>=0),order);
 assert.deepEqual([...order].sort((a,b)=>a-b),order);
});
