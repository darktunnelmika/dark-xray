const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');

const src=fs.readFileSync(path.join(__dirname,'..','web','nodes-v2.js'),'utf8');
const inbound=fs.readFileSync(path.join(__dirname,'..','web','inbounds-v3.js'),'utf8');

test('Nodes V5 uses lightweight Pair Code as the primary add workflow',()=>{
  assert.match(src,/pairNodeDialog/);
  assert.match(src,/DXN1..../);
  assert.match(src,//api/nodes/pair/);
  assert.match(src,/No second web panel is installed|no second web panel is installed/i);
  assert.match(src,/if\(act==='nv2new'\)\{await pairNodeDialog\(\)/);
});

test('Node Fleet surfaces online state and Hub desired-state drift',()=>{
  assert.match(src,/desired_state/);
  assert.match(src,/Pending changes/);
  assert.match(src,/desiredLabel/);
  assert.match(src,/Deployment/);
  assert.match(src,/Sync Now|Sync/);
});

test('Node Manage keeps daily operations in the Hub',()=>{
  for(const token of ['Health Check','Remote Inbounds','Sync Security','Validate Xray','Restart Xray','Logs','Update to Hub Version','Delete Node'])
    assert.match(src,new RegExp(token));
  assert.match(src,//update/check/);
  assert.match(src,//update/start/);
  assert.match(src,//logs//);
});

test('Inbound editor is the primary deployment target selector',()=>{
  assert.match(inbound,/Deployment Targets/);
  assert.match(inbound,/name="deployLocal"/);
  assert.match(inbound,/name="deployNode"/);
  assert.match(inbound,//api/inbounds/'.*/deployments/);
  assert.match(inbound,/Clients follow this inbound automatically/);
});

test('Detailed orchestration remains available but is no longer the main surface',()=>{
  assert.match(src,/nv5-advanced/);
  assert.match(src,/Deployment & failover details/);
  assert.match(src,/Subscription Orchestrator/);
  assert.match(src,/subscription_included/);
  assert.match(src,/subscription_reason/);
});

test('Node editor can still manage inbound assignments for recovery/admin use',()=>{
  assert.match(src,/Inbound deployments/);
  assert.match(src,/name="inboundIds"/);
  assert.match(src,/getAll\('inboundIds'\)/);
});
