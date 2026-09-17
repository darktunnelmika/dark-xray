const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');

function context(){
  const ctx={
    console,
    state:{groups:[],cv2:{view:'clients',owner:'all',group:'all',groupOwner:'all',status:'all',inbound:'all',search:''},clients:[],owners:[],inbounds:[],selected:new Set(),search:'',me:{id:'dark'},errors:{},page:'clients'},
    clientsPage(){return 'base'},runAction:async()=>{},load:async()=>{},clientForm(){},
    filtered:x=>x,localStorage:{getItem:()=> 'en'},e:v=>String(v??''),icon:()=>'<i></i>',badge:()=>'<span class="tag">OK</span>',can:()=>true,
    bytes:v=>String(v),fa:v=>String(v),button:(t,a)=>'<button data-act="'+a+'">'+t+'</button>',heading:()=>'',notices:()=>'',empty:t=>'<div>'+t+'</div>',isOwner:()=>true,
    gb:1024*1024*1024,select:()=>'',field:()=>'',selection:()=>'',$:()=>null,num:()=>0,inputIds:()=>[],dialog:()=>{},
    closeDialog:()=>{},toast:()=>{},refresh:async()=>{},jsonBox:()=>'',enc:encodeURIComponent,renderPage:async()=>{},api:async()=>[],
    qrcode:()=>({addData(){},make(){},createSvgTag(){return '<svg></svg>'}}),
    navigator:{clipboard:{writeText:async()=>{}}},document:{addEventListener(){},querySelector(){return null},createElement(){return {select(){},remove(){},click(){}}},body:{appendChild(){}}},
    Blob:function(){},URL:{createObjectURL:()=>'',revokeObjectURL(){}},setTimeout(){},confirm:()=>true
  };
  vm.createContext(ctx);
  for(const name of ['clients-v2.js','clients-v3.js']){
    vm.runInContext(fs.readFileSync(path.join(__dirname,'..','web',name),'utf8'),ctx,{filename:name});
  }
  return ctx;
}

test('Clients V3 renders real online indicator and organizational columns',()=>{
  const ctx=context();
  ctx.state.clients=[{email:'alpha',owner:'dark',client:{group:'VIP',enable:true,totalGB:1000,expiryTime:0},inboundIds:[7],used_bytes:100,presence_state:'online',presence_age_seconds:12,activity_at:123,block_reasons:[],state:'applied',data_plane_state:'running'}];
  ctx.state.inbounds=[{id:7,remark:'Turkey Tunnel'}];
  const html=ctx.clientsPage();
  assert.match(html,/cv3-presence online/);
  assert.match(html,/ONLINE/);
  assert.match(html,/Turkey Tunnel/);
  assert.match(html,/VIP/);
});

test('Clients V3 presence filter is based on presence_state',async()=>{
  const ctx=context();
  ctx.state.clients=[
    {email:'on',owner:'dark',client:{enable:true},inboundIds:[],used_bytes:0,presence_state:'online',block_reasons:[],state:'applied'},
    {email:'off',owner:'dark',client:{enable:true},inboundIds:[],used_bytes:0,presence_state:'offline',block_reasons:[],state:'applied'}
  ];
  ctx.state.cv3.presence='online';
  const html=ctx.clientsPage();
  assert.match(html,/>on</);
  assert.doesNotMatch(html,/>off</);
});

test('QR path is selectable per exact subscription or generated config link',()=>{
  const src=fs.readFileSync(path.join(__dirname,'..','web','clients-v3.js'),'utf8');
  assert.match(src,/function linkPayload\(kind,index\)/);
  assert.match(src,/m\.result\.engine\?\.links\?\.\[Number\(index\)\]/);
  assert.match(src,/q\.addData\(payload\)/);
  assert.match(src,/Download SVG/);
});
