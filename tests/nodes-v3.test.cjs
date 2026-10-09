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
  for(const token of ['desired_state','Pending','desiredLabel','Deployment'])assert.ok(src.includes(token),token);
  assert.ok(src.includes('Sync Now')||src.includes("L('Sync'"));
});

test('Node Manage keeps daily operations in the Hub',()=>{
  for(const token of ['Diagnostics','Remote Inbounds','Sync Security','Validate Xray','Restart Xray','Logs','Update to Hub Version','Delete Node'])
    assert.ok(src.includes(token),token);
  for(const route of ['/update/check','/update/start','/logs/'])assert.ok(src.includes(route),route);
});

test('Inbound editor is the primary deployment target selector',()=>{
  for(const token of ['Deployment Targets + Tunnel Ports','name="deployLocal"','name="deployNode"','/deployments','Direct is always independent'])
    assert.ok(inbound.includes(token),token);
});

test('Inbound deployment cards own per-target Tunnel Port activation',()=>{
  for(const token of ['name="tunnelEnable"','name="tunnelPort"','tunnelPorts','WAITING HOST','PORT MISMATCH','deploymentTunnelPorts'])
    assert.ok(inbound.includes(token),token);
  assert.ok(inbound.includes("runtime='node:'+n.id"));
  assert.ok(inbound.includes('Direct is always independent'));
});
test('Detailed orchestration remains available but is no longer the main surface',()=>{
  for(const token of ['nv5-advanced','Deployment & failover details','Subscription Orchestrator','subscription_included','subscription_reason'])
    assert.ok(src.includes(token),token);
});

test('Node editor can still manage inbound assignments for recovery/admin use',()=>{
  for(const token of ['Inbound deployments','name="inboundIds"',"getAll('inboundIds')"])assert.ok(src.includes(token),token);
});


test('Nodes V6 exposes live resource telemetry without pretending stale data is live',()=>{
  assert.ok(src.includes('NV6_FRESH_SECONDS=45'),'Live node resource samples must tolerate 45s of collector jitter');
  for(const token of ['telemetry_state','telemetry_age_seconds','NV6_FRESH_SECONDS','CPU','RAM','Disk','Uptime','↓ RX','↑ TX','Connections'])
    assert.ok(src.includes(token),token);
  for(const token of ["api('/api/nodes')",'setInterval(nv6RefreshLive,5000)','nv6-not-fresh','STALE','data-nv6-node'])
    assert.ok(src.includes(token),token);
});

test('Nodes V6 diagnostics explicitly excludes tunnel health',()=>{
  for(const token of ['Node Diagnostics','Live Agent/Xray/system diagnostics only','Tunnel health is not tested here'])
    assert.ok(src.includes(token),token);
});

test('Nodes V6 live refresh is read-only and preserves extension actions',()=>{
  assert.ok(!src.includes('/api/nodes/telemetry/refresh'));
  assert.ok(src.includes("document.querySelector('dialog[open]')"));
  assert.ok(src.includes("!String(x.dataset.act||'').startsWith('nv2')"));
});


test('Nodes V7 surfaces health score capacity and fleet alerts',()=>{
  for(const token of ['operational_health','HEALTH SCORE','CAPACITY USED','Node health alerts','nv7FleetAlerts','nv7-capacity-track','data-nv6-summary="warning"','data-nv6-summary="critical"'])
    assert.ok(src.includes(token),token);
  for(const token of ['cpu_high','memory_critical','disk_high','hub_lease_invalid','accounting_checkpoint_stale'])
    assert.ok(src.includes(token),token);
});

test('Nodes V7 keeps tunnel health outside health score and capacity',()=>{
  assert.ok(src.includes('Tunnel health is intentionally excluded'));
  assert.ok(!src.includes('tunnel_critical'));
  assert.ok(!src.includes('tunnel_health'));
});
