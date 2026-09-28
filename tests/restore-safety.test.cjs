const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('web/restore-safety.js','utf8');
function fixture(owner=true,lang='en'){
 const calls=[],ctx={DarkRestoreGroups:{state:{data:null}},state:{page:'darkrestore'},
  enginePage:async()=>'<section class="panel dr-toolbar">',runAction:async(...a)=>calls.push(a),
  isOwner:()=>owner,localStorage:{getItem:()=>lang},e:x=>String(x),enc:encodeURIComponent,
  document:{getElementById:()=>({}),addEventListener(){}},MutationObserver:class{observe(){}},
  queueMicrotask,Date,Map,Set,Number,console,api:async()=>{throw new Error('Unexpected API write/read');}};
 vm.createContext(ctx);vm.runInContext(source,ctx);return{ctx,calls};
}
test('Restore metadata integer rejects blank, fractional, negative and imprecise inputs',()=>{
 const {ctx}=fixture(),parse=ctx.DarkRestoreSafety.integer;
 for(const value of ['',null,'-1','1.5','1e3','9007199254740992'])assert.throws(()=>parse(value));
 assert.equal(parse('0'),0);assert.equal(parse('1000'),1000);
});
test('Restore owner safety actions cannot be triggered by representatives',async()=>{
 const {ctx}=fixture(false);
 await assert.rejects(ctx.runAction('drsreview',{dataset:{id:'rst_example'}}),/Owner/);
 await assert.rejects(ctx.runAction('drssuspend',{dataset:{id:'rst_example'}}),/Owner/);
});
test('Safety labels separate eligibility, offline enforcement, and observed traffic',()=>{
 const {ctx}=fixture();
 assert.equal(ctx.DarkRestoreSafety.label('eligible'),'Eligible');
 assert.equal(ctx.DarkRestoreSafety.label('offline'),'Offline · not confirmed');
 const html=ctx.DarkRestoreSafety.statusPanel({counts:{eligible:1},clients:1,subscription_received:1,traffic_observed:0,
  hub_state:'pending',nodes:[],writes_enabled:true,legacy_unconfirmed:0});
 assert.match(html,/Receiving a subscription is not proof/);
 assert.match(html,/Offline Nodes cannot confirm/);
});
test('Safety has no separate main menu and loads after Restore target editor',()=>{
 const html=fs.readFileSync('web/index.html','utf8');
 assert.ok(html.indexOf('assets/restore-safety.js')>html.indexOf('assets/restore-targets.js'));
 assert.doesNotMatch(source,/navItems\s*=/);
 const {ctx,calls}=fixture();ctx.runAction('unrelated',{});assert.equal(calls.length,1);
});
