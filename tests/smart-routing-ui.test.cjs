const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');

const src=fs.readFileSync('web/xray-v2.js','utf8');
const inbound=fs.readFileSync('web/inbounds-v3.js','utf8');

test('smart routing UI exposes preview endpoints and action',()=>{
  assert.match(src,/\/api\/smart-routing\/plan/);
  assert.match(src,/\/api\/smart-routing\/preview/);
  assert.match(src,/xv7smartpreview/);
  assert.match(src,/Smart WARP AI/);
  assert.match(src,/Smart Adblock/);
  assert.match(src,/Smart WARP AI · Outbound/);
  assert.match(src,/xv7smartoutpreview/);
});

test('smart routing preview function remains non-applying',()=>{
  const start=src.indexOf('async function smartRoutingPreview');
  const end=src.indexOf('async function routeSettings',start);
  assert.ok(start>=0 && end>start);
  const body=src.slice(start,end);
  assert.doesNotMatch(body,/saveSection\(/);
  assert.doesNotMatch(body,/\/api\/core\/restart/);
  assert.doesNotMatch(body,/\/api\/settings\/routing[^\n]*PUT/);
  assert.match(body,/does not change production traffic/);
});

test('inbound Smart WARP tab exposes ranked scan table and preview selection',()=>{
  assert.match(inbound,/data-v3-pane="smartwarp"/);
  assert.match(inbound,/\/api\/smart-routing\/warp-scan/);
  for(const label of ['Node','Region','Ping','Loss','Jitter','Status','Select']){
    assert.match(inbound,new RegExp("\\b"+label+"\\b"));
  }
  assert.match(inbound,/warp-scan-editor/);
  assert.match(inbound,/selectedWarpTag/);
});

test('inbound Smart WARP scan and selection do not apply or restart',()=>{
  const start=inbound.indexOf('async function scanWarpInEditor');
  const end=inbound.indexOf('async function openWarpScan',start);
  assert.ok(start>=0 && end>start);
  const body=inbound.slice(start,end);
  assert.doesNotMatch(body,/\/api\/inbounds/);
  assert.doesNotMatch(body,/\/api\/settings\/routing/);
  assert.doesNotMatch(body,/\/api\/core\/restart/);
  assert.doesNotMatch(body,/saveEditor\(/);
  assert.match(body,/preview only/i);
});

test('reviewed Smart Routing flow separates validation, staged rollout and rollback',()=>{
  for(const endpoint of ['/api/smart-routing/validate','/api/smart-routing/review',
    '/api/smart-routing/revisions','/api/smart-routing/safety-check','/api/smart-routing/rollouts',
    '/api/smart-routing/rollout/start','/api/smart-routing/rollback']){
    assert.match(src,new RegExp(endpoint.replaceAll('/','\\/')));
  }
  const reviewStart=src.indexOf('async function showSmartRoutingReview');
  const reviewEnd=src.indexOf('async function smartRoutingPreview',reviewStart);
  const reviewBody=src.slice(reviewStart,reviewEnd);
  assert.match(reviewBody,/REVIEW SMART ROUTING/);
  assert.doesNotMatch(reviewBody,/APPLY SMART ROUTING/);
  assert.match(src,/Start staged rollout/);
  assert.match(src,/START STAGED ROLLOUT/);
  assert.match(src,/xv7smartrollout/);
  assert.match(src,/xv7smartrollback/);
});

test('Smart Network setup keeps explicit per-node role assignment but hides region jargon from the main flow',()=>{
  assert.match(src,/nodeCandidates/);
  assert.match(src,/nodeRoles/);
  assert.match(src,/name="warpNodeIds"/);
  assert.match(src,/name="adblockNodeIds"/);
  assert.match(src,/Choose Nodes/);
  assert.match(src,/Pick where each feature should run/);
  assert.match(src,/Region suggestions are guidance/);
});


test('Smart Routing Safety Gate locks apply until a live pass exists',()=>{
  assert.match(src,/xv7smartsafety/);
  assert.match(src,/async function runSmartSafety/);
  assert.match(src,/maxLossPercent:20/);
  assert.match(src,/maxLatencyMs:1200/);
  assert.match(src,/maxJitterMs:350/);
  assert.match(src,/Safety Gate/);
  assert.match(src,/r\.safetyPassed\?button\(L\('Start staged rollout'/);
});

test('Stage 7.1 staged rollout UI shows canary batch hub-last and automatic rollback',()=>{
  assert.match(src,/async function startSmartRollout/);
  assert.match(src,/Canary → Verify → Batch → Hub last/);
  assert.match(src,/automatic rollback/);
  assert.match(src,/smartRollouts/);
  assert.match(src,/progressPercent/);
  assert.match(src,/healthyNodes/);
  assert.match(src,/observationSeconds:5/);
  assert.match(src,/Observation/);
  assert.match(src,/\/api\/smart-routing\/rollout\//);
});


test('Stage 7.3 rollout controls expose pause resume abort timeline and history',()=>{
  for(const action of ['xv7rolloutpause','xv7rolloutresume','xv7rolloutabort','xv7rollouttimeline']){
    assert.match(src,new RegExp(action));
  }
  assert.match(src,/async function controlSmartRollout/);
  assert.match(src,/\/api\/smart-routing\/rollout\/.*\+action/);
  assert.match(src,/\/api\/smart-routing\/rollout\/.*timeline/);
  assert.match(src,/PAUSE STAGED ROLLOUT/);
  assert.match(src,/RESUME STAGED ROLLOUT/);
  assert.match(src,/ABORT STAGED ROLLOUT/);
  assert.match(src,/Rollout history/);
  assert.match(src,/Rollout timeline & telemetry/);
  assert.match(src,/health_sample/);
  assert.match(src,/warp_probe/);
  assert.match(src,/controlState/);
});

test('Stage 7.3 live rollout modal keeps control and telemetry visible',()=>{
  const start=src.indexOf('function rolloutLiveHTML');
  const end=src.indexOf('function rolloutEventText',start);
  assert.ok(start>=0 && end>start);
  const body=src.slice(start,end);
  assert.match(body,/xv7rolloutpause/);
  assert.match(body,/xv7rolloutresume/);
  assert.match(body,/xv7rolloutabort/);
  assert.match(body,/timeline/);
});


test('WARP lives inside Xray Outbounds with simple one-click controls',()=>{
  assert.match(src,/function warpSimpleCard/);
  assert.match(src,/Create WARP/);
  assert.match(src,/AI Only/);
  assert.match(src,/All Traffic/);
  assert.match(src,/Rotate IP/);
  assert.match(src,/xvwarpcreate/);
  assert.match(src,/xvwarptest/);
  assert.match(src,/xvwarpmode/);
  assert.match(src,/\/api\/warp\/status/);
  assert.match(src,/\/api\/warp\/create/);
  assert.match(src,/\/api\/warp\/scan/);
  assert.match(src,/\/api\/warp\/rotate/);
  assert.match(src,/\/api\/warp\/mode/);
  assert.doesNotMatch(src,/state\.page==='smart'/);
  const tabs=src.slice(src.indexOf('function xtabs()'),src.indexOf('function xcard'));
  assert.match(tabs,/Outbounds/);
  assert.match(tabs,/Routing/);
  assert.doesNotMatch(tabs,/Smart WARP/);
});
