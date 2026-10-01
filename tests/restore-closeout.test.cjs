const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');

const root=path.join(__dirname,'..');
const source=fs.readFileSync(path.join(root,'web','dark-restore.js'),'utf8');
const css=fs.readFileSync(path.join(root,'web','dark-restore.css'),'utf8');

function setup(){
 const scope={navItems:()=>[['account','Account','shield']],enginePage:async()=>'',runAction:async()=>{},
  isOwner:()=>true,enginePages:{},state:{inbounds:[],page:'darkrestore'},localStorage:{getItem:()=> 'en'},
  e:x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])),
  bytes:n=>'bytes:'+n,icon:()=>'',document:{addEventListener:()=>{},querySelector:()=>null},enc:encodeURIComponent};
 vm.runInNewContext(source,scope);return scope;
}

test('Closeout filters split plan, presence and lifecycle without mixing groups',()=>{
 const s=setup(),dr=s.DarkRestoreGroups.state;
 dr.data={items:[
  {id:'a',group_id:'g1',group_name:'A',legacy_host:'a.example',legacy_path:'/1',plan_type:'limited',presence_state:'online',promoted:false},
  {id:'b',group_id:'g1',group_name:'A',legacy_host:'a.example',legacy_path:'/2',plan_type:'unlimited',presence_state:'offline',promoted:false},
  {id:'c',group_id:'g2',group_name:'B',legacy_host:'b.example',legacy_path:'/3',plan_type:'limited',presence_state:'offline',promoted:true,promoted_owner:'rep'}
 ]};
 dr.group='g1';dr.plan='limited';dr.presence='online';dr.lifecycle='active';
 assert.deepEqual(Array.from(s.DarkRestoreGroups.filtered(),x=>x.id),['a']);
 dr.plan='all';dr.presence='offline';
 assert.deepEqual(Array.from(s.DarkRestoreGroups.filtered(),x=>x.id),['b']);
 dr.group='';dr.lifecycle='promoted';
 assert.deepEqual(Array.from(s.DarkRestoreGroups.filtered(),x=>x.id),['c']);
});

test('Restore user cards show plan presence and native promotion state',()=>{
 const s=setup();
 const active=s.DarkRestoreGroups.users([{id:'a',group_id:'g',group_name:'A',legacy_host:'x',legacy_path:'/x',
  plan_type:'unlimited',presence_state:'online',promoted:false,dark_used:1,local_used:1,node_used:0,
  legacy_total:0,legacy_used:0,remaining:0,inbound_ids:[1],scan_status:'verified'}]);
 assert.match(active,/ONLINE/);assert.match(active,/Unlimited/);assert.match(active,/data-act="drmap"/);
 const promoted=s.DarkRestoreGroups.users([{id:'b',group_id:'g',group_name:'A',legacy_host:'x',legacy_path:'/y',
  plan_type:'limited',presence_state:'offline',promoted:true,promoted_owner:'seller',dark_used:2,local_used:2,node_used:0,
  legacy_total:100,legacy_used:10,remaining:88,inbound_ids:[1],scan_status:'verified'}]);
 assert.match(promoted,/NATIVE → seller/);
 assert.match(promoted,/data-dr-select="b"[^>]*disabled/);
 assert.doesNotMatch(promoted,/data-act="drdelete"/);
});

test('Closeout exposes bulk promotion and representative endpoint',()=>{
 assert.match(source,/data-act="drpromote"/);
 assert.match(source,/\/api\/dark-restore\/representatives/);
 assert.match(source,/\/api\/dark-restore\/promote/);
 assert.match(source,/future usage moves to native accounting/);
});

test('Closeout mobile UI keeps compact badges and sticky bulk actions',()=>{
 assert.match(css,/\.dr-presence\.online/);
 assert.match(css,/\.dr-plan\.unlimited/);
 assert.match(css,/\.dr-native/);
 assert.match(css,/position:sticky/);
});


test('Restore client list matches compact native Clients layout with visible presence signal',()=>{
 assert.match(source,/dr-client-list/);
 assert.match(source,/dr-list-head/);
 assert.match(source,/dr-live/);
 assert.match(source,/data-act="drdetail"/);
 assert.match(source,/data-dr-dark-usage/);
 assert.match(css,/min-height:58px/);
 assert.match(css,/grid-template-columns:28px minmax\(190px,1\.3fr\)/);
 assert.match(css,/\.dr-live\.online/);
 assert.match(css,/@media\(max-width:760px\)/);
 assert.match(css,/min-height:54px/);
});

test('Restore detail opens legacy metadata instead of expanding every list row',()=>{
 assert.match(source,/async function detailDialog/);
 assert.match(source,/LEGACY SOURCE/);
 assert.match(source,/DARK USAGE/);
 assert.match(source,/TARGETS/);
});
