const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'..','web','traffic-matrix-access.js'),'utf8');
const index=fs.readFileSync(path.join(__dirname,'..','web','index.html'),'utf8');
const css=fs.readFileSync(path.join(__dirname,'..','web','traffic-matrix-access.css'),'utf8');
function setup(role='owner',lang='en'){
 const requests=[],opened=[],delegated=[],navigation=[];
 const buttons=['dashboard','inbounds','xray','account'].map(page=>({dataset:{page},active:false,attrs:{},
  classList:{toggle(_name,on){buttons.find(b=>b.dataset.page===page).active=on;}},
  setAttribute(name,value){this.attrs[name]=value;},removeAttribute(name){delete this.attrs[name];}}));
 const breadcrumb={textContent:''};
 const originalNav=[['dashboard','Dashboard','grid'],['inbounds','Inbounds','server'],['clients','Clients','users'],
  ['outbounds','Outbounds','arrow'],['routing','Routing','node'],['trafficmatrix','WARP','activity'],['xray','Xray','terminal'],['account','Account','shield']];
 const scope={role,state:{page:'trafficmatrix',xv2:{tab:'general'}},enginePages:{},localStorage:{getItem:()=>lang},
  isOwner:()=>scope.role==='owner',e:x=>String(x??'').replace(/[&<>"']/g,x=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[x])),
  icon:()=>'<svg></svg>',heading:(a,b)=>a+b,empty:x=>x,
  navItems:()=>originalNav,inboundPage:()=>'<div class="inbounds-v3">inbounds</div>',
  enginePage:async()=>{delegated.push(scope.state.page);return '<p>Original</p>';},
  shell:()=>{buttons.forEach(b=>{b.active=b.dataset.page===scope.state.page;});return 'shell';},
  document:{querySelectorAll:()=>buttons,querySelector:()=>breadcrumb},
  runAction:async act=>'delegated:'+act,
  toast:()=>{},confirm:()=>true,
  renderPage:async()=>scope.enginePage(),
  go:async page=>{navigation.push(page);scope.state.page=page;scope.shell();return scope.enginePage();},
  api:async(url,method='GET',body)=>{requests.push([url,method]);
   if(url==='/api/inbounds')return [{id:1,remark:'Dark Vpn',protocol:'vless',port:8569}];
   if(url==='/api/traffic-matrix/runtimes')return {items:[{id:'hub',kind:'hub',name:'HUB',address:'1.2.3.4',online:true}]};
   if(url.startsWith('/api/traffic-matrix/warp?'))return {registered:false,state:'not_created',selectionConfirmed:false,endpoint:'',candidateEndpoint:'',server:{id:'hub',kind:'hub',name:'HUB',online:true}};
   if(url==='/api/traffic-matrix/warp/create')return {registered:true,selectionRequired:true,items:[]};
   if(url==='/api/traffic-matrix/warp/scan')return {items:[],selectionConfirmed:false};
   if(url==='/api/traffic-matrix/warp/endpoint')return {selectionConfirmed:true,selected:body?.endpoint||''};
   if(url==='/api/traffic-matrix/warp/auto')return {selectionConfirmed:true,autoSelected:true};
   return {};
  },
  DarkTrafficMatrix:{open:async id=>{opened.push(id);return 'opened';}}
 };
 vm.runInNewContext(source,scope);
 return {scope,requests,opened,delegated,navigation,buttons,breadcrumb,originalNav};
}
test('Owner sidebar contains one Xray Settings entry and no separate traffic entries',()=>{
 const {scope,originalNav}=setup();
 for(let i=0;i<3;i++){
  const nav=scope.navItems();
  assert.equal(nav.filter(x=>x[0]==='xray').length,1);
  assert.equal(nav.find(x=>x[0]==='xray')[1],'Xray Settings');
  assert.equal(nav.some(x=>['outbounds','routing','trafficmatrix'].includes(x[0])),false);
 }
 assert.equal(originalNav.length,8);
 assert.doesNotMatch(scope.inboundPage(),/tm-access-banner/);
 assert.equal(scope.DarkTrafficMatrixAccess.parentPage,'xray');
});
test('All four sections remain reachable using original engines without changing routes',async()=>{
 const {scope,requests,delegated}=setup();
 for(const page of ['xray','outbounds','routing','trafficmatrix']){
  scope.state.page=page;const html=await scope.enginePage();
  assert.match(html,/class="xray-settings-nav"/);
  assert.equal((html.match(/aria-current="page"/g)||[]).length,1);
  assert.ok(html.includes('data-page="'+page+'" class="active" aria-current="page"'));
  for(const id of ['xray','outbounds','routing','trafficmatrix'])assert.ok(html.includes('data-page="'+id+'"'));
  assert.equal(scope.state.page,page);
 }
 assert.deepEqual(delegated,['xray','outbounds','routing']);
 assert.deepEqual(requests,[['/api/inbounds','GET'],['/api/traffic-matrix/runtimes','GET'],['/api/traffic-matrix/warp?server=hub','GET']]);
});
test('Xray parent stays active and the breadcrumb identifies the selected child',()=>{
 const {scope,buttons,breadcrumb}=setup();
 for(const page of ['xray','outbounds','routing','trafficmatrix']){
  scope.state.page=page;assert.equal(scope.shell(),'shell');
  assert.deepEqual(buttons.filter(b=>b.active).map(b=>b.dataset.page),['xray']);
  assert.equal(buttons.find(b=>b.active).attrs['aria-current'],'page');
  assert.match(breadcrumb.textContent,/^Xray Settings/);
 }
 scope.state.page='account';scope.shell();assert.deepEqual(buttons.filter(b=>b.active).map(b=>b.dataset.page),['account']);
});
test('WARP opens the existing per-inbound matrix and does not apply a policy',async()=>{
 const {scope,requests,opened}=setup();
 const html=await scope.enginePage();assert.match(html,/Dark Vpn/);assert.match(html,/data-act="tmaccessopen"/);
 assert.deepEqual(requests,[['/api/inbounds','GET'],['/api/traffic-matrix/runtimes','GET'],['/api/traffic-matrix/warp?server=hub','GET']]);assert.deepEqual(opened,[]);
 await scope.runAction('tmaccessopen',{dataset:{id:'1'}});assert.deepEqual(opened,[1]);
 assert.equal(await scope.runAction('other',{}),'delegated:other');
 scope.state.page='settings';assert.equal(await scope.enginePage(),'<p>Original</p>');
});
test('Legacy traffic shortcuts select V4 sections rather than obsolete V2 panels',async()=>{
 const {scope,navigation,delegated}=setup();
 await scope.runAction('xv2tab',{dataset:{tab:'outbounds'}});
 await scope.runAction('xv2tab',{dataset:{tab:'routing'}});
 assert.deepEqual(navigation,['outbounds','routing']);assert.deepEqual(delegated,['outbounds','routing']);
 scope.state.page='xray';scope.state.xv2.tab='outbounds';await scope.enginePage();assert.equal(scope.state.xv2.tab,'general');
});
test('Reseller cannot enter grouped owner settings or trigger WARP actions',async()=>{
 const {scope,requests,opened}=setup('reseller');
 assert.equal(scope.navItems().some(x=>['xray','outbounds','routing','trafficmatrix'].includes(x[0])),false);
 for(const page of ['xray','outbounds','routing','trafficmatrix']){scope.state.page=page;assert.match(await scope.enginePage(),/Owner access required/);}
 await assert.rejects(scope.runAction('tmaccessopen',{dataset:{id:'1'}}),/Owner access required/);
 await assert.rejects(scope.runAction('xv2tab',{dataset:{tab:'routing'}}),/Owner access required/);
 assert.deepEqual(requests,[]);assert.deepEqual(opened,[]);
});
test('Missing matrix module and invalid identities remain explicit errors',async()=>{
 const {scope}=setup();delete scope.DarkTrafficMatrix;
 await assert.rejects(scope.runAction('tmaccessopen',{dataset:{id:'1'}}),/did not load/);
 await assert.rejects(scope.runAction('tmaccessopen',{dataset:{id:'-1'}}),/valid inbound/);
});
test('Persian grouping, mobile targets and asset order remain explicit',async()=>{
 const {scope}=setup('owner','fa');assert.equal(scope.navItems().find(x=>x[0]==='xray')[1],'تنظیمات Xray');
 assert.match(await scope.enginePage(),/WARP \/ حذف تبلیغ/);
 assert.match(css,/min-height:44px/);assert.match(css,/repeat\(2,minmax\(0,1fr\)\)/);
 assert.ok(index.includes('assets/traffic-matrix-access.css'));
 assert.ok(index.indexOf('assets/traffic-matrix.js')<index.indexOf('assets/traffic-matrix-access.js'));
 assert.ok(index.indexOf('assets/traffic-matrix-access.js')<index.indexOf('assets/ui-stability.js'));
});

test('WARP center is runtime-first and manual selection is the default',async()=>{
 const {scope}=setup();
 const html=await scope.enginePage();
 assert.match(html,/WARP RUNTIME CENTER/);
 assert.match(html,/data-xw-runtime/);
 assert.match(html,/Create \+ Scan/);
 assert.match(source,/Select & Apply/);
 assert.match(source,/selectionConfirmed/);
 assert.match(source,/Production WARP policies stay blocked/i);
 assert.match(source,/data-act="xwauto"/);
});

test('WARP center calls explicit create scan select endpoints without hidden auto apply',async()=>{
 const {scope,requests}=setup();
 await scope.enginePage();
 requests.length=0;
 await scope.runAction('xwcreate',{});
 assert.deepEqual(requests.map(x=>x[0]),['/api/traffic-matrix/warp/create','/api/inbounds','/api/traffic-matrix/runtimes','/api/traffic-matrix/warp?server=hub']);
 assert.doesNotMatch(source,/WARP created and installed/);
 assert.match(source,/Select a verified path to activate it/);
});

test('Xray Settings compact WARP UI has responsive scan result layout',()=>{
 assert.match(css,/\.xw-center/);
 assert.match(css,/\.xw-path/);
 assert.match(css,/\.xw-state\.warn/);
 assert.match(css,/@media\(max-width:760px\)/);
});
