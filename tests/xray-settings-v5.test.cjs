const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('web/xray-settings-v5.js','utf8');
const scanner=fs.readFileSync('web/traffic-matrix.js','utf8');
function setup(){
 const requests=[],events={},delegated=[],dialogues=[];
 const ctx={state:{page:'trafficmatrix'},localStorage:{getItem:()=> 'en'},isOwner:()=>true,
  e:x=>String(x??'').replace(/[<>&"]/g,x=>({'<':'&lt;','>':'&gt;','&':'&amp;','"':'&quot;'}[x])),
  heading:(a,b)=>a+b,enginePage:async()=>'<p>Advanced</p>',runAction:async(a,e)=>delegated.push([a,e]),
  renderPage:async()=>{},go:async p=>{ctx.state.page=p;},toast:()=>{},closeDialog:()=>{},
  dialog:(...args)=>dialogues.push(args),document:{addEventListener:(n,f)=>events[n]=f,querySelector:()=>null},
  api:async(url,method='GET',body)=>{requests.push({url,method,body});
   if(url==='/api/xray-settings/servers')return {items:[{id:'hub',name:'HUB',online:true,applyState:{status:'applied'}}]};
   if(url==='/api/inbounds')return [{id:1,remark:'Inbound One'}];
   if(url.startsWith('/api/traffic-matrix/warp?'))return {registered:false,pendingRegistration:false,applyState:{status:'pending'}};
   if(url.startsWith('/api/traffic-matrix?'))return {rows:[{inboundId:1,serverId:'hub',accessPath:'direct',port:443,policy:'normal',applyState:{status:'applied'}}]};
   throw Error('Unexpected API call '+url);
  }};
 vm.runInNewContext(source,ctx);return {ctx,requests,events,delegated,dialogues};
}
test('server selection is empty by default and page entry performs only reads',async()=>{
 const {ctx,requests}=setup();const html=await ctx.enginePage();
 assert.equal(ctx.state.xraySettingsV5.server,'');assert.match(html,/No server selected/);
 assert.match(html,/data-x5-select="server"/);assert.doesNotMatch(html,/data-act="x5warpscan"/);
 assert.ok(requests.every(r=>r.method==='GET'));
});
test('server-first WARP works without selecting an inbound and reuses scanner',async()=>{
 const {ctx,delegated}=setup();ctx.state.xraySettingsV5.server='hub';
 const html=await ctx.enginePage();assert.match(html,/data-act="x5warpregister"/);
 await ctx.runAction('x5warpregister',{});
 assert.equal(delegated[0][0],'tmwarpcreate');assert.equal(delegated[0][1].dataset.server,'hub');
 assert.equal(delegated[0][1].dataset.id,'0');
});
test('routing requires explicit server inbound and path and separates adblock',async()=>{
 const {ctx,requests}=setup();Object.assign(ctx.state.xraySettingsV5,{server:'hub',inbound:1,path:'direct'});
 const html=await ctx.enginePage();assert.match(html,/data-x5-select="inbound"/);assert.match(html,/data-x5-select="path"/);
 assert.match(html,/data-x5-adblock/);assert.match(html,/data-act="x5preview"/);assert.match(html,/Applied/);
 assert.ok(requests.every(r=>r.method==='GET'));
 assert.equal(ctx.DarkXraySettingsV5.policyFrom('warp_ai',true),'warp_ai_adblock');
 assert.equal(ctx.DarkXraySettingsV5.policyFrom('normal',true),'adblock');
 assert.throws(()=>ctx.DarkXraySettingsV5.policyFrom('custom',true),/simple route/);
});
test('API failure produces visible error and retry, never blank settings',async()=>{
 const {ctx}=setup();ctx.api=async()=>{throw Error('Fixture network failure');};
 const html=await ctx.enginePage();assert.match(html,/role="alert"/);assert.match(html,/Fixture network failure/);assert.match(html,/Retry/);
});
test('no owner access means no data fetch and no WARP action',async()=>{
 const {ctx,requests}=setup();ctx.isOwner=()=>false;assert.match(await ctx.enginePage(),/Owner access required/);
 await assert.rejects(ctx.runAction('x5warpregister',{}),/Owner access/);assert.equal(requests.length,0);
});
test('Auto Best is an unchecked toggle and only reveals explicit preview',()=>{
 assert.match(scanner,/<input type="checkbox" data-warp-auto-best>/);
 assert.match(scanner,/hidden data-warp-best-preview/);assert.match(scanner,/preview.hidden=!auto.checked/);
 assert.match(scanner,/x.status==='degraded'/);assert.match(scanner,/Unknown location/);
 assert.match(scanner,/if\(Number.isSafeInteger\(id\)&&id>0\)await openMatrix\(id\);else await go\('trafficmatrix'\)/);
});
test('new assets follow existing matrix module and precede UI stability',()=>{
 const index=fs.readFileSync('web/index.html','utf8');
 assert.ok(index.indexOf('assets/traffic-matrix-access.js')<index.indexOf('assets/xray-settings-v5.js'));
 assert.ok(index.indexOf('assets/xray-settings-v5.js')<index.indexOf('assets/ui-stability.js'));
});
