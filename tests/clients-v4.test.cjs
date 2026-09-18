const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');

function context(){
  const ctx={
    console,
    state:{groups:[{owner:'dark',name:'VIP',client_count:1,color:'#22d3ee'}],cv2:{view:'clients'},clients:[],owners:[{id:'dark',name:'DARK',allowed:[]}],inbounds:[],selected:new Set(),search:'',me:{id:'dark'},errors:{},page:'clients'},
    clientsPage(){return 'base'},runAction:async()=>{},clientForm:async()=>{},load:async()=>{},
    filtered:x=>x,localStorage:{getItem:()=> 'en'},e:v=>String(v??''),icon:()=>'<i></i>',badge:()=>'<span>SYNC</span>',can:()=>true,
    bytes:v=>String(v),fa:v=>String(v),button:(t,a,ic,extra='')=>'<button data-act="'+a+'" '+extra+'>'+t+'</button>',heading:()=>'',notices:()=>'',empty:t=>'<div>'+t+'</div>',isOwner:()=>true,
    gb:1024*1024*1024,dialog:()=>{},closeDialog:()=>{},toast:()=>{},refresh:async()=>{},jsonBox:()=>'',enc:encodeURIComponent,renderPage:async()=>{},
    api:async()=>({}),confirm:()=>true,
    document:{addEventListener(){},querySelector(){return null}},setTimeout(){},
  };
  vm.createContext(ctx);
  for(const name of ['clients-v2.js','clients-v3.js','clients-v4.js']){
    vm.runInContext(fs.readFileSync(path.join(__dirname,'..','web',name),'utf8'),ctx,{filename:name});
  }
  return ctx;
}

test('Clients V4 removes the permanent sidebar and reduces row actions',()=>{
  const ctx=context();
  ctx.state.inbounds=[{id:7,remark:'DARK TURKEY',protocol:'vless',port:8899,network:'grpc',security:'reality'}];
  ctx.state.clients=[{email:'mika',owner:'dark',client:{group:'VIP',enable:true,totalGB:0,expiryTime:0,limitIp:1,limitHwid:0},inboundIds:[7],used_bytes:100,presence_state:'online',presence_age_seconds:3,activity_at:100,block_reasons:[],state:'applied',data_plane_state:'running'}];
  const html=ctx.clientsPage();
  assert.match(html,/clients-v4/);
  assert.match(html,/OPEN/);
  assert.match(html,/>QR</);
  assert.match(html,/•••/);
  assert.doesNotMatch(html,/cv3-side/);
  assert.doesNotMatch(html,/Organization/);
});

test('Clients V4 create editor is basic-first and low-frequency fields stay advanced or removed',()=>{
  const src=fs.readFileSync(path.join(__dirname,'..','web','clients-v4.js'),'utf8');
  assert.match(src,/cv4-editor-shell/);
  assert.match(src,/cv4-advanced/);
  assert.match(src,/name="planMode"/);
  assert.match(src,/name="expiryMode"/);
  assert.match(src,/name="limitIp"/);
  assert.doesNotMatch(src,/name="tgId"/);
  assert.doesNotMatch(src,/name="id"/);
  assert.doesNotMatch(src,/Custom UUID/);
});

test('Clients V4 keeps advanced XTLS flow gated by selected transport compatibility',()=>{
  const src=fs.readFileSync(path.join(__dirname,'..','web','clients-v4.js'),'utf8');
  assert.match(src,/function flowCompatible\(ids\)/);
  assert.match(src,/\['tcp','raw'\]\.includes/);
  assert.match(src,/\['tls','reality'\]\.includes/);
  assert.match(src,/flow\.querySelector\('select'\)\.disabled=!ok/);
});

test('Clients V4 command deck keeps group and presence organization without sidebar clutter',()=>{
  const ctx=context();
  ctx.state.clients=[
    {email:'a',owner:'dark',client:{group:'VIP',enable:true},inboundIds:[],used_bytes:0,presence_state:'online',block_reasons:[]},
    {email:'b',owner:'dark',client:{group:'',enable:true},inboundIds:[],used_bytes:0,presence_state:'offline',block_reasons:[]}
  ];
  ctx.state.cv4.presence='online';
  const html=ctx.clientsPage();
  assert.match(html,/>a</);
  assert.doesNotMatch(html,/>b</);
  assert.match(html,/cv4-filterdeck/);
});


test('Clients V4 owner-scoped groups remain isolated and login ID is not assumed to be the owner profile',()=>{
  const src=fs.readFileSync(path.join(__dirname,'..','web','clients-v4.js'),'utf8');
  assert.match(src,/const groupKey=\(owner,name\)=>/);
  assert.match(src,/row\.owner===decodeURIComponent/);
  assert.match(src,/function defaultOwner\(\)/);
  assert.match(src,/if\(opts\.length===1\)return opts\[0\]\[0\]/);
});
