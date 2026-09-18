const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');

const src=fs.readFileSync(path.join(__dirname,'..','web','update-center.js'),'utf8');

function ctx(owner=true){
  const x={
    console,
    state:{page:'dashboard',me:{id:'dark',version:'0.9.0-rc7'},selected:new Set(),updateCenter:null},
    enginePages:{settings:['Settings']},
    navItems:()=>[['dashboard','Overview','grid'],['settings','Settings','settings'],['account','Account','shield']],
    dashboard:()=>'<before><div id="dark-update-center-slot"></div><after>',load:async()=>{},renderPage:async()=>{},runAction:async()=>{},isOwner:()=>owner,
    localStorage:{getItem:()=> 'en'},e:v=>String(v??''),icon:()=>'<i></i>',heading:(a,b,c='')=>a+b+c,
    api:async()=>({broker_ready:true,current:{version:'0.9.0-rc7'},state:'idle',phase:'idle',percent:0,log_tail:[]}),
    document:{querySelector:()=>null},setTimeout:()=>{},toast:()=>{},dialog:()=>{},closeDialog:()=>{}
  };
  vm.createContext(x);vm.runInContext(src,x,{filename:'update-center.js'});return x;
}

test('Update Center lives on the owner dashboard and not in standalone navigation',async()=>{
  const owner=ctx(true),reseller=ctx(false);
  assert.ok(!owner.navItems().some(x=>x[0]==='update'));
  assert.ok(!reseller.navItems().some(x=>x[0]==='update'));
  await owner.load();
  assert.match(owner.dashboard(),/Update Center/);
  assert.equal(reseller.dashboard(),'<before><after>');
  const html=owner.dashboard();
  assert.ok(html.indexOf('<before>')<html.indexOf('Update Center'));
  assert.ok(html.indexOf('Update Center')<html.indexOf('<after>'));
  assert.ok(!html.includes('dark-update-center-slot'));
  assert.match(html,/up-dashboard-compact/);
  assert.match(html,/Open Update Center/);
  assert.match(html,/Check Latest Verified/);
  assert.ok(!html.includes('Choose update channel'));
});

test('web update flow uses broker APIs and immutable commit confirmation',()=>{
  assert.match(src,/\/api\/update\/status/);
  assert.match(src,/\/api\/update\/check/);
  assert.match(src,/\/api\/update\/start/);
  assert.match(src,/Confirmation must be exactly UPDATE/);
  assert.match(src,/commit:commit/);
  assert.doesNotMatch(src,/sudo\s/);
  assert.doesNotMatch(src,/child_process/);
});

test('Update Center includes CI, preflight, rollback and reconnect UX',()=>{
  assert.match(src,/CI VERIFIED/);
  assert.match(src,/Automatic rollback/);
  assert.match(src,/snapshot_database/);
  assert.match(src,/Panel is restarting/);
  assert.match(src,/Latest Verified/);
});
