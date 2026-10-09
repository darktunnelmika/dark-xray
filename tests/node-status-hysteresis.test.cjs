const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');

const web=path.join(__dirname,'..','web');
const backend=path.join(__dirname,'..','backend');
const nodes=fs.readFileSync(path.join(web,'nodes-v2.js'),'utf8');
const detail=fs.readFileSync(path.join(web,'nodes-detail-v8.js'),'utf8');
const controller=fs.readFileSync(path.join(backend,'nodes.py'),'utf8');
const lease=fs.readFileSync(path.join(backend,'node_lease_sync.py'),'utf8');

test('Nodes cards and detail share 45s three-health-probe telemetry window',()=>{
 assert.match(nodes,/const NV6_FRESH_SECONDS=45;/);
 assert.match(detail,/Number\(n\.telemetry_age_seconds\)<=45/);
 assert.match(controller,/age<=45 else 'stale'/);
 assert.match(controller,/age<180 and not unavailable/);
 assert.match(nodes,/nv6LiveTimer=setInterval\(nv6RefreshLive,5000\)/);
});
test('One transient probe failure never removes a deployed inbound',()=>{
 assert.match(controller,/int\(node\.get\('failure_count'\) or 0\)>=3/);
 assert.match(controller,/transport_stable=not NodeRegistry\._transport_unavailable\(node\)/);
 assert.match(controller,/if confirmed:\s*self\._sync_active_alerts/);
 assert.match(controller,/remote_node_inbounds WHERE node_id=\? AND local_inbound_id=\?/);
 assert.match(controller,/\bdef set_inbound_assignment\(/);
});
test('Lease remains strict despite faster unchanged-state grant',()=>{
 assert.match(lease,/require_accounted_snapshot\(/);
 assert.match(lease,/verify_hub_accounting_policy\(/);
 assert.match(lease,/current\.get\('applied_hash'\) != state\['hash'\]/);
 assert.match(lease,/if \(current\.get\('pending'\)/);
 assert.match(lease,/nodes\.sync_desired_state\(node_id, state/);
 assert.match(lease,/Node has not acknowledged current quota\/configuration/);
});
