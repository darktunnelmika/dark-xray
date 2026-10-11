const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');

const src=fs.readFileSync(path.join(__dirname,'..','web','nodes-closeout-v9.js'),'utf8');
const css=fs.readFileSync(path.join(__dirname,'..','web','nodes-closeout-v9.css'),'utf8');
const index=fs.readFileSync(path.join(__dirname,'..','web','index.html'),'utf8');

test('Nodes V9 loads after V8 and preserves previous wrappers',()=>{
 const v8=index.indexOf('assets/nodes-detail-v8.js'),v9=index.indexOf('assets/nodes-closeout-v9.js');
 assert.ok(v8>0&&v9>v8);
 for(const token of ['pageBase=enginePage','actionBase=runAction','return out','MutationObserver'])
  assert.ok(src.includes(token),token);
});

test('Nodes V9 maintenance is explicit and does not issue core stop or tunnel probes',()=>{
 for(const token of ['nv9maintenance','/maintenance','Existing connections are not stopped','new subscription/failover routes are paused'])
  assert.ok(src.includes(token),token);
 assert.equal(src.includes('data-core="stop"'),false);
 assert.equal(src.toLowerCase().includes('tunnel health check'),false);
});

test('Nodes V9 alerts drill into details and expose alert lifecycle timing',()=>{
 for(const token of ['nv9alert','started_at','last_observed_at','Active alerts','Last observed','cacheHubSource'])
  assert.ok(src.includes(token),token);
});

test('Nodes V9 detail includes operations recovery update guard and accounting state',()=>{
 for(const token of ['Failures','Recoveries','Last offline','Last recovered','Update status','Guard','Desired revision','Applied revision','Accounting checkpoint'])
  assert.ok(src.includes(token),token);
});

test('Nodes V9 mobile polish keeps actions and detail grids responsive',()=>{
 for(const token of ['@media(max-width:650px)','nv2-actions','nv9-ops-panel','dialog:has(.nv8-detail)','nv7-fleet-alerts'])
  assert.ok(css.includes(token),token);
});

test('Nodes LIVE requires fresh system metrics independent of transport lease ACK',()=>{
 const cards=fs.readFileSync(path.join(__dirname,'..','web','nodes-v2.js'),'utf8');
 const detail=fs.readFileSync(path.join(__dirname,'..','web','nodes-detail-v8.js'),'utf8');
 for(const token of ['health_sample_state','health_sample_age_seconds','nv6SampleState','nv6SampleAge'])
  assert.ok(cards.includes(token),'missing Node card sample guard: '+token);
 for(const token of ['health_sample_state','health_sample_age_seconds'])
  assert.ok(detail.includes(token),'missing Node detail sample guard: '+token);
 assert.ok(cards.includes('n.telemetry_state'),'Network status remains independent from CPU sample');
});
