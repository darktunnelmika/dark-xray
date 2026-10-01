const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const js=fs.readFileSync('web/operations-v3.js','utf8');
function setup(){
 const o={generated_at:Date.now()/1000,hub:{status:'applied'},nodes:[],telegram:[],warp:[],summary:{}};
 const ctx={state:{me:{id:'dark',role:'owner'},ov2:{overview:o,overviewOwner:'dark'}},localStorage:{getItem:()=> 'en'},
  e:x=>String(x).replace(/</g,'&lt;'),runAction:async()=>{},renderPage:async()=>{},api:async()=>o};
 vm.runInNewContext(js,ctx);return {ctx,o};
}
test('active Overview invokes the actual operations renderer',()=>{
 const overview=fs.readFileSync('web/overview-v4.js','utf8'),index=fs.readFileSync('web/index.html','utf8');
 assert.match(overview,/DarkOperationsV3\?\.render/);assert.match(overview,/DarkOperationsV3\?\.attention/);
 assert.ok(index.indexOf('assets/ops-v2.js')<index.indexOf('assets/operations-v3.js'));
 assert.ok(index.indexOf('assets/operations-v3.js')<index.indexOf('assets/overview-v4.js'));
});
test('failed or stale observations never render green nominal data',()=>{
 const {ctx,o}=setup();o.generated_at-=61;assert.match(ctx.DarkOperationsV3.render(),/out of date/);assert.equal(ctx.DarkOperationsV3.attention(),true);
 ctx.state.ov2.overview=null;assert.match(ctx.DarkOperationsV3.render(),/No healthy state is assumed/);
});
test('no bots or disabled-only bots are not shown as healthy',()=>{
 const {ctx,o}=setup();o.telegram=[{owner:'fixture',enabled:false,state:'disabled'}];const html=ctx.DarkOperationsV3.render();assert.match(html,/Not configured/);assert.doesNotMatch(html,/healthy\/configured/);
});
test('configured WARP is explicitly untested for connectivity',()=>{
 const {ctx,o}=setup();o.warp=[{server:'hub',name:'HUB',status:'applied',configured:true,endpoint:'162.159.192.1:2408'}];
 assert.match(ctx.DarkOperationsV3.render(),/connectivity not tested/);
});
test('owner cache is not rendered after a role or account change',()=>{
 const {ctx}=setup();ctx.state.me.role='reseller';assert.equal(ctx.DarkOperationsV3.render(),'');ctx.state.me={id:'other',role:'owner'};assert.match(ctx.DarkOperationsV3.render(),/unavailable/);
});
test('refresh failure discards former good observation',async()=>{
 const {ctx}=setup();ctx.api=async()=>{throw Error('offline');};await ctx.runAction('ov3refresh',{});assert.equal(ctx.state.ov2.overview,null);assert.match(ctx.DarkOperationsV3.render(),/unavailable/);
});

test('disabled retired nodes cannot raise active dashboard sync alerts',()=>{
 const source=fs.readFileSync('web/overview-v4.js','utf8');
 const code=source.slice(source.indexOf('function healthCard(){'),source.indexOf('const activityScopes='));
 const ctx={state:{ov2:{core:{state:'running'},nodes:[{name:'Retired',enabled:false,last_error:'old failure'}]}},L:(en)=>en,e:x=>x,fa:x=>x};
 vm.runInNewContext(code,ctx);const html=ctx.healthCard();
 assert.doesNotMatch(html,/Node sync errors|Retired/);assert.match(html,/All monitored systems nominal/);
 assert.doesNotMatch(source,/errors=nodes\.filter/);
});

test('an enabled bot without credentials never becomes an applied summary card',()=>{
 const {ctx,o}=setup();o.telegram=[{owner:'fixture',enabled:true,configured:false,state:'unconfigured'}];
 const html=ctx.DarkOperationsV3.render();
 const telegram=html.slice(html.indexOf('Telegram bots'),html.indexOf('WARP configuration'));
 assert.match(telegram,/Not configured/);assert.doesNotMatch(telegram,/Applied/);
});
