const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');

const traffic=fs.readFileSync(path.join(__dirname,'..','web','traffic-engine-v4.js'),'utf8');
const guided=fs.readFileSync(path.join(__dirname,'..','web','xray-guided-v3.js'),'utf8');
const css=fs.readFileSync(path.join(__dirname,'..','web','traffic-engine-v4.css'),'utf8');
const index=fs.readFileSync(path.join(__dirname,'..','web','index.html'),'utf8');

test('Traffic Engine V4 consumes canonical dependency and preview APIs',()=>{
  assert.match(traffic,/\/api\/traffic-engine/);
  assert.match(traffic,/\/api\/traffic-engine\/preview/);
  assert.match(traffic,/rule_refs/);
  assert.match(traffic,/balancer_refs/);
  assert.match(traffic,/fallback_refs/);
  assert.match(traffic,/dialed_by/);
});

test('Traffic Engine V4 exposes explicit default-outbound control',()=>{
  assert.match(traffic,/Make default/);
  assert.match(traffic,/te4default/);
  assert.match(traffic,/list\.splice\(index,1\)/);
  assert.match(traffic,/list\.unshift\(item\)/);
  assert.match(traffic,/first outbound when no routing rule matches/);
});

test('Traffic Engine V4 never presents static preview as live core truth',()=>{
  assert.match(traffic,/SAVED CONFIG/);
  assert.match(traffic,/live_core_verified/);
  assert.match(traffic,/Geodata\/DNS\/runtime balancer choices are never guessed/);
  assert.match(traffic,/indeterminate/);
  assert.doesNotMatch(traffic,/LIVE VERIFIED ROUTE/);
});

test('Guided Routing V4 exposes full common Xray rule context',()=>{
  for(const name of ['ruleTag','ruleSourceIP','ruleSourcePort','ruleLocalIP','ruleLocalPort','ruleUser','ruleProcess','ruleVlessRoute','ruleAttrs'])
    assert.match(guided,new RegExp(name));
  assert.match(guided,/putList\('ruleProtocol','protocol'\)/);
  assert.match(guided,/HTTP attrs must be valid JSON/);
});

test('Balancer UI explains Xray prefix-selector semantics and actual candidates',()=>{
  assert.match(guided,/Selectors are Xray tag prefixes/);
  assert.match(traffic,/PREFIX SELECTORS/);
  assert.match(traffic,/MATCHED OUTBOUNDS/);
  assert.match(traffic,/observed_candidates/);
});

test('Traffic Engine V4 assets are loaded after Guided V3',()=>{
  assert.match(index,/traffic-engine-v4\.css/);
  assert.match(index,/xray-guided-v3\.js"><\/script><script defer src="assets\/traffic-engine-v4\.js/);
  assert.match(css,/DARK XRAY Traffic Engine V4/);
  assert.match(css,/\.te4-preview/);
  assert.match(css,/\.te4-graph/);
});
