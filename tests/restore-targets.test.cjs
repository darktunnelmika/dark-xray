const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const root=path.join(__dirname,'..'),source=fs.readFileSync(path.join(root,'web/restore-targets.js'),'utf8');
function setup(owner=true){
 const scope={state:{page:'darkrestore'},localStorage:{getItem:()=> 'en'},isOwner:()=>owner,
  e:x=>String(x??'').replace(/</g,'&lt;'),icon:()=>'',document:{addEventListener:()=>{}},
  DarkRestoreGroups:{state:{group:'g1',nodes:[],data:{groups:[{id:'g1',name:'Representative A'}],items:[{id:'r1',group_id:'g1',inbound_ids:[1],node_ids:[],node_mode:'all',include_local:1}]}},groupPayload:()=>({groupId:'g1'})},
  enginePage:async()=>'<section class="panel dr-results"></section>',runAction:async act=>'old:'+act};
 vm.runInNewContext(source,scope);return scope;
}
const form=(value)=>({get:k=>value[k],getAll:k=>value[k]||[],has:k=>!!value[k]});
test('Group editor is visible once and refers to every member, not page selection',async()=>{
 const s=setup(),html=await s.enginePage();assert.match(html,/Edit group destinations/);assert.match(html,/whole group, not just this page/);assert.equal((html.match(/data-act="drgroupmapping"/g)||[]).length,1);
 assert.match(html,/All assigned Nodes/);assert.equal(await s.runAction('another',{}),'old:another');
});
test('Whole inbound sends no Node subset; explicit selection keeps its scope',()=>{
 const s=setup(),read=s.DarkRestoreTargets.readSelection;
 const all=read(form({drInbound:['1'],nodeMode:'all',drNode:['stale-checkbox'],includeLocal:true}));
 assert.equal(all.nodeMode,'all');assert.equal(all.nodeIds.length,0);
 const selected=read(form({drInbound:['1'],nodeMode:'selected',drNode:['am'],includeLocal:false}));
 assert.equal(selected.nodeIds[0],'am');assert.equal(selected.includeLocal,false);
 assert.throws(()=>read(form({drInbound:[],nodeMode:'all'})),/inbound/);
 assert.throws(()=>read(form({drInbound:['1'],nodeMode:'selected'})),/Hub/);
});
test('Representatives cannot open import, per-user or group target controls',async()=>{
 const s=setup(false);assert.doesNotMatch(await s.enginePage(),/drgroupmapping/);
 for(const act of ['drimport','drmap','drgroupmapping'])await assert.rejects(s.runAction(act,{dataset:{id:'g1'}}),/Owner/);
});
test('Preview and explicit confirmation preserve revision safety',()=>{
 assert.match(source,/mapping\/preview/);assert.match(source,/expectedRevision:preview.revision/);
 assert.match(source,/name="confirmTargets" type="checkbox" required/);
 assert.match(source,/nodeMode:'all',includeLocal:true/);
 const index=fs.readFileSync(path.join(root,'web/index.html'),'utf8');
 assert.ok(index.indexOf('assets/dark-restore.js')<index.indexOf('assets/restore-targets.js'));
 assert.ok(index.includes('assets/restore-targets.css'));
});
