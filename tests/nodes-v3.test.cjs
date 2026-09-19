const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');

const src=fs.readFileSync(path.join(__dirname,'..','web','nodes-v2.js'),'utf8');

test('Node add/edit exposes per-node inbound assignment picker',()=>{
  assert.match(src,/name="inboundIds"/);
  assert.match(src,/getAll\('inboundIds'\)/);
  assert.match(src,/Select at least one inbound for this node/);
  assert.match(src,/Only selected inbounds and their attached clients are mirrored/);
});

test('Node workflow replaces manual clone with assignment sync',()=>{
  assert.match(src,/'nv2sync'/);
  assert.match(src,/\/api\/nodes\/'.*\/sync/);
  assert.doesNotMatch(src,/nv2cloneinbound/);
  assert.doesNotMatch(src,/Clone local inbound to node/);
});

test('Node cards expose assigned inbound names and sync state',()=>{
  assert.match(src,/function assignedNames\(n\)/);
  assert.match(src,/ASSIGNMENT STATE/);
  assert.match(src,/n\.assignments/);
  assert.match(src,/n\.assignments/);
  assert.match(src,/last_error/);
});


test('Node cards expose Central traffic and recovery state',()=>{
  assert.match(src,/Central traffic/);
  assert.match(src,/Traffic sync/);
  assert.match(src,/Recoveries/);
  assert.match(src,/charged_bytes/);
  assert.doesNotMatch(src,/Remote traffic accounting is still node-local/);
});


test('Node editor exposes failover address priority and security sync',()=>{
  assert.match(src,/,\'dataAddress\',/);
  assert.match(src,/,\'priority\',/);
  assert.match(src,/name="failoverEnabled"/);
  assert.match(src,/nv2security/);
  assert.match(src,/Security synchronized/);
  assert.match(src,/failover_reason/);
  assert.match(src,/source_verified/);
});


test('Nodes V4 exposes subscription orchestrator and explicit inclusion reasons',()=>{
  assert.match(src,/Subscription Orchestrator/);
  assert.match(src,/\/api\/nodes\/orchestration/);
  assert.match(src,/IN SUBSCRIPTION/);
  assert.match(src,/NOT DEPLOYED/);
  assert.match(src,/NODE OFFLINE/);
  assert.match(src,/FAILOVER OFF/);
  assert.match(src,/SYNC ERROR/);
  assert.match(src,/subscription_included/);
  assert.match(src,/subscription_reason/);
});

test('Node failover help explains source inbound port isolation from Public Endpoint',()=>{
  assert.match(src,/failover port is the source inbound port/i);
  assert.match(src,/not the primary Public Endpoint tunnel\/CDN port/i);
  assert.match(src,/data_port/);
});
