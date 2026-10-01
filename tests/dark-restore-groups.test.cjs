const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'..','web','dark-restore.js'),'utf8');
function setup(role='owner'){
 const scope={navItems:()=>[['dashboard','Dashboard','grid'],['account','Account','shield']],
  enginePage:async()=>'',runAction:async()=>{},isOwner:()=>role==='owner',enginePages:{},
  state:{inbounds:[],page:'darkrestore'},localStorage:{getItem:()=> 'en'},
  e:x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])),
  bytes:n=>'bytes:'+n,icon:()=>'',document:{addEventListener:()=>{},querySelector:()=>null},
  enc:encodeURIComponent};
 vm.runInNewContext(source,scope);return scope;
}
test('Restore usage label reads DARK usage, never the legacy-inclusive effective total',()=>{
 const s=setup();
 const html=s.DarkRestoreGroups.users([{id:'rst_1',group_id:'g1',group_name:'Rep A',dark_used:3000,
  effective_used:13000,legacy_used:10000,legacy_total:50000,remaining:37000,inbound_ids:[1],node_ids:[]}]);
 assert.match(html,/data-dr-dark-usage>bytes:3000<\/strong>/);
 assert.doesNotMatch(html,/bytes:13000/);
 assert.doesNotMatch(html,/<details>/);
 assert.match(source,/LEGACY SOURCE/);
 assert.match(source,/legacy_used/);
 assert.match(html,/bytes:37000/);
});
test('Import requires a group choice and validates new names before sending',()=>{
 const s=setup(),payload=s.DarkRestoreGroups.groupPayload;
 assert.throws(()=>payload(new Map([['groupChoice','__new__'],['groupName','   ']])),/group name/);
 assert.equal(payload(new Map([['groupChoice','__new__'],['groupName',' Rep A ']])).groupName,'Rep A');
 assert.equal(payload(new Map([['groupChoice','grp_A']])).groupId,'grp_A');
 assert.match(source,/scan:true,\.\.\.group/);
});
test('Group and query filters do not combine representatives',()=>{
 const s=setup(),dr=s.DarkRestoreGroups.state;
 dr.data={items:[{id:'1',group_id:'a',legacy_host:'one.example',legacy_path:'/sub/a',group_name:'Rep A'},
  {id:'2',group_id:'b',legacy_host:'one.example',legacy_path:'/sub/b',group_name:'Rep B'}]};
 dr.group='a';assert.equal(s.DarkRestoreGroups.filtered().length,1);
 dr.query='/sub/b';assert.equal(s.DarkRestoreGroups.filtered().length,0);
 dr.group='';assert.equal(s.DarkRestoreGroups.filtered()[0].id,'2');
});
test('Existing imports support explicit bulk assignment without reimport',()=>{
 assert.match(source,/\/api\/dark-restore\/groups\/assign/);
 assert.match(source,/data-act="drselectall"/);
 assert.match(source,/data-act="drmove"/);
 assert.match(source,/pageSize=50/);
 assert.match(source,/credentials, quota, expiry and usage remain intact/);
});
test('Representative group names and legacy paths are escaped',()=>{
 const s=setup();
 const html=s.DarkRestoreGroups.users([{id:'x',group_name:'<img src=x onerror=alert(1)>',legacy_path:'<script>attack</script>',inbound_ids:[],node_ids:[]}]);
 assert.doesNotMatch(html,/<img|<script/);
 assert.match(html,/&lt;img/);
});
test('Restore stays owner-only in navigation',()=>{
 assert.equal(setup().navItems().filter(x=>x[0]==='darkrestore').length,1);
 assert.equal(setup('reseller').navItems().some(x=>x[0]==='darkrestore'),false);
});
test('Mobile form sizes and group styles are loaded',()=>{
 const css=fs.readFileSync(path.join(__dirname,'..','web','dark-restore.css'),'utf8');
 const index=fs.readFileSync(path.join(__dirname,'..','web','index.html'),'utf8');
 assert.match(index,/assets\/dark-restore\.css/);assert.match(css,/font-size:16px/);
 assert.match(css,/\.dr-group-form>\[hidden\]/);assert.match(css,/max-width:360px/);
});
