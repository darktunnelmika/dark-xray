const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');

function context(){
  const data={
    '/api/ledger/credits':[
      {event_id:'direct-owner',owner:'dark',volume_bytes:999,unlimited_units:9,kind:'adjust',reference:'',at:100},
      {event_id:'credit-1',owner:'alpha',volume_bytes:50,unlimited_units:2,kind:'adjust',reference:'',at:111},
      {event_id:'credit-2',owner:'beta',volume_bytes:-10,unlimited_units:0,kind:'adjust',reference:'',at:222}
    ],
    '/api/ledger/traffic':[
      {event_id:'direct-traffic',owner:'dark',client_id:'direct',period:1,up_bytes:100,down_bytes:200,observed_at:300},
      {event_id:'traffic-1',owner:'alpha',client_id:'a1',period:2,up_bytes:10,down_bytes:5,observed_at:333},
      {event_id:'traffic-2',owner:'beta',client_id:'b1',period:4,up_bytes:7,down_bytes:3,observed_at:444}
    ]
  };
  const ctx={
    console,
    financePage:async()=>'',runAction:async()=>{},renderPage:async()=>{},
    state:{
      resellers:[
        {id:'alpha',name:'Alpha',volume_credit_bytes:100,allocated_volume_bytes:40,volume_credit_remaining_bytes:60,
         unlimited_credit:5,allocated_unlimited:3,unlimited_credit_remaining:2,used_bytes:15,lifetime_used_bytes:900,enabled:true},
        {id:'beta',name:'Beta',volume_credit_bytes:80,allocated_volume_bytes:60,volume_credit_remaining_bytes:20,
         unlimited_credit:2,allocated_unlimited:1,unlimited_credit_remaining:1,used_bytes:10,lifetime_used_bytes:600,enabled:true}
      ],
      owners:[{id:'dark',name:'DARK OWNER',volume_credit_bytes:999,used_bytes:300,lifetime_used_bytes:9999}]
    },
    isOwner:()=>true,
    localStorage:{getItem:()=> 'en'},
    api:async url=>data[url]||[],
    e:v=>String(v??''),fa:v=>String(v),bytes:v=>`${v}B`,date:v=>`DATE:${v}`,
    heading:(a,b)=>`<h1>${a}</h1><p>${b}</p>`,empty:t=>`EMPTY:${t}`
  };
  vm.createContext(ctx);
  vm.runInContext(fs.readFileSync(path.join(__dirname,'..','web','finance-v2.js'),'utf8'),ctx,{filename:'finance-v2.js'});
  return ctx;
}

test('owner credit workspace excludes primary-owner rows and focuses representatives',async()=>{
  const ctx=context();
  const html=await ctx.financePage();
  assert.match(html,/Representative Credits/);
  assert.match(html,/All representatives/);
  assert.match(html,/credit-1/);
  assert.match(html,/credit-2/);
  assert.match(html,/traffic-1/);
  assert.doesNotMatch(html,/direct-owner/);
  assert.doesNotMatch(html,/direct-traffic/);
  assert.doesNotMatch(html,/Sale|Refund|Representative money ledger/);
});

test('credit summaries separate sellable capacity from observed traffic',async()=>{
  const ctx=context();
  const html=await ctx.financePage();
  assert.match(html,/Volume credit remaining<\/small><b>80B<\/b>/);
  assert.match(html,/Unlimited credit remaining<\/small><b>3<\/b>/);
  assert.match(html,/Current-period traffic<\/small><b>25B<\/b>/);
  assert.match(html,/Lifetime representative traffic<\/small><b>1500B<\/b>/);
  assert.match(html,/Analytics only; does not spend credit/);
});

test('representative filter scopes credit and traffic ledgers plus position',async()=>{
  const ctx=context();
  await ctx.runAction('fv2filter',{dataset:{key:'representative',value:'alpha'}});
  const html=await ctx.financePage();
  assert.match(html,/credit-1/);
  assert.match(html,/traffic-1/);
  assert.match(html,/60B/);
  assert.match(html,/900B/);
  assert.doesNotMatch(html,/credit-2/);
  assert.doesNotMatch(html,/traffic-2/);
});

test('current credit position shows total allocated and remaining pools',async()=>{
  const ctx=context();
  const html=await ctx.financePage();
  assert.match(html,/Volume allocated/);
  assert.match(html,/Unlimited allocated/);
  assert.match(html,/100B/);
  assert.match(html,/40B/);
  assert.match(html,/60B/);
  assert.match(html,/>5</);
  assert.match(html,/>3</);
  assert.match(html,/>2</);
});

test('owner workspace with no representatives does not fall back to primary-owner accounting',async()=>{
  const ctx=context();
  ctx.state.resellers=[];
  const html=await ctx.financePage();
  assert.match(html,/No representatives yet/);
  assert.doesNotMatch(html,/direct-owner/);
  assert.doesNotMatch(html,/direct-traffic/);
  assert.ok((html.match(/<b>—<\/b>/g)||[]).length>=3);
});
