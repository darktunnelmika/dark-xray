const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');

function context(){
  const ctx={
    console,
    state:{groups:[],cv2:{},clients:[],owners:[],inbounds:[],selected:new Set(),search:'',me:{id:'dark'},errors:{},page:'clients'},
    clientsPage(){return ''},runAction:async()=>{},load:async()=>{},clientForm(){},
    filtered:x=>x,localStorage:{getItem:()=> 'en'},e:v=>String(v??''),icon:()=>'',badge:()=>'',can:()=>false,
    bytes:v=>String(v),fa:v=>String(v),button:()=>'',heading:()=>'',notices:()=>'',empty:()=>'',isOwner:()=>true,
    gb:1024*1024*1024,select:()=>'',field:()=>'',selection:()=>'',$:()=>null,num:()=>0,inputIds:()=>[],dialog:()=>{},
    closeDialog:()=>{},toast:()=>{},refresh:async()=>{},jsonBox:()=>'',enc:encodeURIComponent,renderPage:async()=>{},api:async()=>[],
    confirm:()=>true
  };
  vm.createContext(ctx);
  const source=fs.readFileSync(path.join(__dirname,'..','web','clients-v2.js'),'utf8');
  vm.runInContext(source,ctx,{filename:'clients-v2.js'});
  return ctx;
}

test('same group name from different resellers stays owner-scoped',()=>{
  const ctx=context();
  ctx.state.clients=[
    {email:'alpha-user',owner:'alpha',client:{group:'Shared',enable:true},inboundIds:[],used_bytes:0,block_reasons:[]},
    {email:'beta-user',owner:'beta',client:{group:'Shared',enable:true},inboundIds:[],used_bytes:0,block_reasons:[]}
  ];
  ctx.state.groups=[{owner:'alpha',name:'Shared',client_count:1},{owner:'beta',name:'Shared',client_count:1}];
  ctx.state.cv2={view:'clients',owner:'all',group:'Shared',groupOwner:'alpha',status:'all',inbound:'all'};
  const html=ctx.clientsPage();
  assert.match(html,/alpha-user/);
  assert.doesNotMatch(html,/beta-user/);
});

test('group click captures owner and incompatible owner filter clears group',async()=>{
  const ctx=context();
  await ctx.runAction('cv2filter',{dataset:{key:'group',value:'Shared',owner:'alpha'}});
  assert.equal(ctx.state.cv2.group,'Shared');
  assert.equal(ctx.state.cv2.groupOwner,'alpha');
  await ctx.runAction('cv2filter',{dataset:{key:'owner',value:'beta'}});
  assert.equal(ctx.state.cv2.owner,'beta');
  assert.equal(ctx.state.cv2.group,'all');
  assert.equal(ctx.state.cv2.groupOwner,'all');
});
