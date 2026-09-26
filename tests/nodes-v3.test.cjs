const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');

const src=fs.readFileSync(path.join(__dirname,'..','web','nodes-v2.js'),'utf8');
const inbound=fs.readFileSync(path.join(__dirname,'..','web','inbounds-v3.js'),'utf8');

test('Nodes V5 uses lightweight Pair Code as the primary add workflow',()=>{
  assert.ok(src.includes('pairNodeDialog'));
  assert.ok(src.includes('DXN1....'));
  assert.ok(src.includes('/api/nodes/pair'));
  assert.ok(src.toLowerCase().includes('no second web panel is installed'));
  assert.ok(src.includes("if(act==='nv2new'){await pairNodeDialog()"));
});

test('Node Fleet surfaces online state and Hub desired-state drift',()=>{
  for(const token of ['desired_state','Pending changes','desiredLabel','Deployment'])assert.ok(src.includes(token),token);
  assert.ok(src.includes('Sync Now')||src.includes("L('Sync'"));
});

test('Node Manage keeps daily operations in the Hub',()=>{
  for(const token of ['Health Check','Remote Inbounds','Sync Security','Validate Xray','Restart Xray','Logs','Update to Hub Version','Delete Node'])
    assert.ok(src.includes(token),token);
  for(const route of ['/update/check','/update/start','/logs/'])assert.ok(src.includes(route),route);
});

test('Inbound editor is the primary deployment target selector',()=>{
  for(const token of ['Deployment Targets','name="deployLocal"','name="deployNode"','/deployments','Clients follow this inbound automatically'])
    assert.ok(inbound.includes(token),token);
});

test('Detailed orchestration remains available but is no longer the main surface',()=>{
  for(const token of ['nv5-advanced','Deployment & failover details','Subscription Orchestrator','subscription_included','subscription_reason'])
    assert.ok(src.includes(token),token);
});

test('Node editor can still manage inbound assignments for recovery/admin use',()=>{
  for(const token of ['Inbound deployments','name="inboundIds"',"getAll('inboundIds')"])assert.ok(src.includes(token),token);
});

test('Node editor stores a human-readable location for ping/routing labels',()=>{
  assert.match(src,/Location','لوکیشن/);
  assert.match(src,/\'location\',n\.location/);
  assert.match(src,/location:fd\.get\('location'\)/);
  assert.match(src,/n\.location/);
});


test('Node updater and fresh installer include outbound probe dependencies',()=>{
  const updater=fs.readFileSync(path.join(__dirname,'..','tools','update_node.py'),'utf8');
  const provision=fs.readFileSync(path.join(__dirname,'..','tools','provision_node.py'),'utf8');
  for(const token of ['outbound_probe.py','warp_paths.py']){
    assert.ok(updater.includes(token),`updater missing ${token}`);
    assert.ok(provision.includes(token),`provision missing ${token}`);
  }
});
