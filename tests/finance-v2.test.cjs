const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');

function context(){
  const data={
    '/api/ledger/money':[
      {event_id:'credit-1',owner:'alpha',amount:100,kind:'credit',reference:'',at:111},
      {event_id:'sale-1',owner:'beta',amount:-25,kind:'sale',reference:'client-b',at:222}
    ],
    '/api/ledger/traffic':[
      {event_id:'traffic-1',owner:'alpha',client_id:'a1',period:2,up_bytes:10,down_bytes:5,observed_at:333},
      {event_id:'traffic-2',owner:'beta',client_id:'b1',period:4,up_bytes:7,down_bytes:3,observed_at:444}
    ]
  };
  const ctx={
    console,
    financePage:async()=>'',runAction:async()=>{},renderPage:async()=>{},
    state:{owners:[{id:'alpha',name:'Alpha',credit:100,used_bytes:15,lifetime_used_bytes:900},{id:'beta',name:'Beta',credit:50,used_bytes:10,lifetime_used_bytes:600}]},
    localStorage:{getItem:()=> 'en'},
    api:async url=>data[url]||[],
    e:v=>String(v??''),fa:v=>String(v),bytes:v=>`${v}B`,date:v=>`DATE:${v}`,
    heading:(a,b)=>`<h1>${a}</h1><p>${b}</p>`,empty:t=>`EMPTY:${t}`
  };
  vm.createContext(ctx);
  vm.runInContext(fs.readFileSync(path.join(__dirname,'..','web','finance-v2.js'),'utf8'),ctx,{filename:'finance-v2.js'});
  return ctx;
}

test('traffic ledger uses observed_at instead of money at field',async()=>{
  const ctx=context();
  const html=await ctx.financePage();
  assert.match(html,/DATE:333/);
  assert.match(html,/DATE:444/);
  assert.match(html,/traffic-1/);
  assert.match(html,/traffic-2/);
});

test('lifetime summary uses authoritative owner total, not the visible ledger window',async()=>{
  const ctx=context();
  const html=await ctx.financePage();
  assert.match(html,/Current-period traffic<\/small><b>25B<\/b>/);
  assert.match(html,/Lifetime observed traffic<\/small><b>1500B<\/b>/);
});

test('finance owner filter scopes both ledgers and authoritative summaries',async()=>{
  const ctx=context();
  await ctx.runAction('fv2filter',{dataset:{key:'owner',value:'alpha'}});
  const html=await ctx.financePage();
  assert.match(html,/credit-1/);
  assert.match(html,/traffic-1/);
  assert.match(html,/900B/);
  assert.doesNotMatch(html,/sale-1/);
  assert.doesNotMatch(html,/traffic-2/);
});

test('money event filter also scopes the financial net summary',async()=>{
  const ctx=context();
  await ctx.runAction('fv2filter',{dataset:{key:'kind',value:'sale'}});
  const html=await ctx.financePage();
  assert.match(html,/Filtered ledger net/);
  assert.match(html,/>-25</);
  assert.match(html,/sale-1/);
  assert.doesNotMatch(html,/credit-1/);
});

test('finance ledger without owners.read never renders unavailable profile totals as zero',async()=>{
  const ctx=context();
  ctx.state.owners=[];
  const html=await ctx.financePage();
  assert.match(html,/Owner profile stats unavailable/);
  assert.match(html,/data-value="alpha"/);
  assert.match(html,/data-value="beta"/);
  assert.match(html,/credit-1/);
  assert.match(html,/traffic-2/);
  // The profile-only KPI values are unavailable rather than fabricated 0s.
  assert.ok((html.match(/<b>—<\/b>/g)||[]).length>=3);
});
