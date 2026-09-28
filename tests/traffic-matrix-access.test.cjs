const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'..','web','traffic-matrix-access.js'),'utf8');
const index=fs.readFileSync(path.join(__dirname,'..','web','index.html'),'utf8');
function setup(role='owner',lang='en'){
 const requests=[],opened=[];
 const scope={role,state:{page:'trafficmatrix'},enginePages:{},localStorage:{getItem:()=>lang},
  isOwner:()=>scope.role==='owner',e:x=>String(x??'').replace(/[&<>"']/g,x=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[x])),
  icon:()=>'<svg></svg>',heading:(a,b)=>a+b,empty:x=>x,
  navItems:()=>[['dashboard','Dashboard','grid'],['inbounds','Inbounds','server'],['clients','Clients','users']],
  inboundPage:()=>'<div class="inbounds-v3">inbounds</div>',enginePage:async()=>'<p>Original</p>',
  runAction:async act=>'delegated:'+act,
  api:async(path,method='GET')=>{requests.push([path,method]);return [{id:1,remark:'Dark Vpn',protocol:'vless',port:8569}];},
  DarkTrafficMatrix:{open:async id=>{opened.push(id);return 'opened';}}
 };
 vm.runInNewContext(source,scope);
 return {scope,requests,opened};
}
test('Owner gets exactly one named WARP menu immediately after Inbounds',()=>{
 const {scope}=setup();
 for(let i=0;i<3;i++){
  const nav=scope.navItems();
  assert.equal(nav.filter(x=>x[0]==='trafficmatrix').length,1);
  assert.equal(nav[2][0],'trafficmatrix');
  assert.equal(nav[2][1],'WARP / AdBlock');
 }
 assert.match(scope.inboundPage(),/tm-access-banner/);
 assert.match(scope.inboundPage(),/data-page="trafficmatrix"/);
});
test('Workspace reads real inbound list without applying policies',async()=>{
 const {scope,requests,opened}=setup();
 const html=await scope.enginePage();
 assert.match(html,/Dark Vpn/);assert.match(html,/data-act="tmaccessopen"/);
 assert.deepEqual(requests,[['/api/inbounds','GET']]);assert.deepEqual(opened,[]);
 await scope.runAction('tmaccessopen',{dataset:{id:'1'}});
 assert.deepEqual(opened,[1]);assert.equal(await scope.runAction('other',{}),'delegated:other');
 scope.state.page='routing';assert.equal(await scope.enginePage(),'<p>Original</p>');
});
test('Reseller keeps read-only boundaries and has no WARP entry',async()=>{
 const {scope,requests,opened}=setup('reseller');
 assert.equal(scope.navItems().some(x=>x[0]==='trafficmatrix'),false);
 assert.doesNotMatch(scope.inboundPage(),/tm-access-banner/);
 assert.match(await scope.enginePage(),/Owner access required/);
 await assert.rejects(scope.runAction('tmaccessopen',{dataset:{id:'1'}}),/Owner access required/);
 assert.deepEqual(requests,[]);assert.deepEqual(opened,[]);
});
test('Missing matrix module produces an error instead of silent success',async()=>{
 const {scope}=setup();delete scope.DarkTrafficMatrix;
 await assert.rejects(scope.runAction('tmaccessopen',{dataset:{id:'1'}}),/did not load/);
 await assert.rejects(scope.runAction('tmaccessopen',{dataset:{id:'-1'}}),/valid inbound/);
});
test('Persian entry and asset ordering are explicit',()=>{
 const {scope}=setup('owner','fa');assert.equal(scope.navItems()[2][1],'WARP / حذف تبلیغ');
 assert.ok(index.includes('assets/traffic-matrix-access.css'));
 assert.ok(index.indexOf('assets/traffic-matrix.js')<index.indexOf('assets/traffic-matrix-access.js'));
 assert.ok(index.indexOf('assets/traffic-matrix-access.js')<index.indexOf('assets/ui-stability.js'));
});
