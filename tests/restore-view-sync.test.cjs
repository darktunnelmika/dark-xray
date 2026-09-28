const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('web/restore-view-sync.js','utf8');
function fixture(page='darkrestore',owner=true){
 const events=[],input={value:'',selectionStart:7,selectionEnd:7,focus(){events.push('focus');},setSelectionRange(a,b){events.push([a,b]);},dispatchEvent(ev){events.push(ev.type);}};
 const ctx={state:{page,renderSeq:0},DarkRestoreGroups:{state:{query:'latest search'}},isOwner:()=>owner,
  document:{activeElement:input,querySelector:()=>input},Event:class{constructor(type){this.type=type;}},
  enginePage:async()=>'<html-stale-snapshot>',Number};
 ctx.renderPage=async()=>{ctx.state.renderSeq++;await ctx.enginePage();input.value='';return 'rendered';};
 vm.createContext(ctx);vm.runInContext(source,ctx);return{ctx,input,events};
}
test('Restore refresh retains the current filter through the existing input handler',async()=>{
 const {ctx,input,events}=fixture();
 assert.equal(await ctx.renderPage(),'rendered');
 assert.equal(input.value,'latest search');
 assert.equal(events[0],'input');assert.equal(events[1],'focus');assert.deepEqual(events[2],[7,7]);
});
test('View synchronization does not interfere with native pages or reseller roles',async()=>{
 for(const [page,owner] of [['clients',true],['darkrestore',false]]){
  const {ctx,input,events}=fixture(page,owner);await ctx.renderPage();
  assert.equal(input.value,'');assert.equal(events.length,0);
 }
});
