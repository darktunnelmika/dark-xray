const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');

function context(){
  const data={
    '/api/ledger/money':[
      {event_id:'direct-owner',owner:'dark',amount:999,kind:'credit',reference:'',at:100},
      {event_id:'credit-1',owner:'alpha',amount:100,kind:'credit',reference:'',at:111},
      {event_id:'sale-1',owner:'beta',amount:-25,kind:'sale',reference:'client-b',at:222}
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
        {id:'alpha',name:'Alpha',credit:100,used_bytes:15,lifetime_used_bytes:900,enabled:true},
        {id:'beta',name:'Beta',credit:50,used_bytes:10,lifetime_used_bytes:600,enabled:true}
      ],
      owners:[{id:'dark',name:'DARK OWNER',credit:999,used_bytes:300,lifetime_used_bytes:9999}]
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

test('owner Finance workspace excludes direct-owner ledger rows and focuses representatives',async()=>{
  const ctx=context();
  const html=await ctx.financePage();
  assert.match(html,/Representative/);
  assert.match(html,/All representatives/);
  assert.match(html,/credit-1/);
  assert.match(html,/sale-1/);
  assert.match(html,/traffic-1/);
  assert.doesNotMatch(html,/direct-owner/);
  assert.doesNotMatch(html,/direct-traffic/);
  assert.doesNotMatch(html,/Owner scope/);
});

test('traffic ledger uses observed_at and representative totals',async()=>{
  const ctx=context();
  const html=await ctx.financePage();
  assert.match(html,/DATE:333/);
  assert.match(html,/DATE:444/);
  assert.match(html,/Current-period traffic<\/small><b>25B<\/b>/);
  assert.match(html,/Lifetime representative traffic<\/small><b>1500B<\/b>/);
});

test('representative filter scopes both ledgers and authoritative representative summaries',async()=>{
  const ctx=context();
  await ctx.runAction('fv2filter',{dataset:{key:'representative',value:'alpha'}});
  const html=await ctx.financePage();
  assert.match(html,/credit-1/);
  assert.match(html,/traffic-1/);
  assert.match(html,/900B/);
  assert.doesNotMatch(html,/sale-1/);
  assert.doesNotMatch(html,/traffic-2/);
});

test('money event filter scopes representative financial net summary',async()=>{
  const ctx=context();
  await ctx.runAction('fv2filter',{dataset:{key:'kind',value:'sale'}});
  const html=await ctx.financePage();
  assert.match(html,/Filtered ledger net/);
  assert.match(html,/>-25</);
  assert.match(html,/sale-1/);
  assert.doesNotMatch(html,/credit-1/);
  assert.doesNotMatch(html,/direct-owner/);
});

test('owner Finance with no representatives does not fall back to primary-owner accounting',async()=>{
  const ctx=context();
  ctx.state.resellers=[];
  const html=await ctx.financePage();
  assert.match(html,/No representatives yet/);
  assert.doesNotMatch(html,/direct-owner/);
  assert.doesNotMatch(html,/direct-traffic/);
  assert.ok((html.match(/<b>—<\/b>/g)||[]).length>=3);
});
