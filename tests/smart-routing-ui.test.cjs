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


test('WARP lives inside Xray Outbounds as independent per-server profiles',()=>{
  assert.match(src,/function warpProfilesPanel/);
  assert.match(src,/function warpProfileRow/);
  assert.match(src,/\/api\/warp\/profiles/);
  assert.match(src,/Create WARP/);
  assert.match(src,/data-server/);
  assert.match(src,/xvwarpcreate/);
  assert.match(src,/xvwarpedit/);
  assert.match(src,/xvwarptest/);
  assert.match(src,/xvwarpmode/);
  assert.match(src,/\/api\/warp\/status/);
  assert.match(src,/\/api\/warp\/create/);
  assert.match(src,/\/api\/warp\/scan/);
  assert.match(src,/\/api\/warp\/rotate/);
  assert.match(src,/\/api\/warp\/mode/);
  assert.match(src,/filter\(o=>String\(o\.tag\|\|''\)!=='warp'\)/);
  assert.doesNotMatch(src,/state\.page==='smart'/);
  const tabs=src.slice(src.indexOf('function xtabs()'),src.indexOf('function xcard'));
  assert.match(tabs,/Outbounds/);
  assert.match(tabs,/Routing/);
  assert.doesNotMatch(tabs,/Smart WARP/);
});

test('WARP path scan lives inside Create/Edit for exactly one server',()=>{
  assert.match(src,/async function scanSmartWarpPage/);
  assert.match(src,/\/api\/warp\/endpoints\/scan/);
  assert.match(src,/Only this server profile is scanned/);
  assert.match(src,/xvwarpedit/);
  assert.match(src,/xvwarptest/);
  assert.match(src,/xvwarprotate/);
  assert.match(src,/await scanSmartWarpPage\(server\)/);
  const rowStart=src.indexOf('function warpProfileRow');
  const rowEnd=src.indexOf('function warpProfilesPanel',rowStart);
  const rowBody=src.slice(rowStart,rowEnd);
  assert.doesNotMatch(rowBody,/xv7warpscan/);
  assert.doesNotMatch(rowBody,/Scan WARP/);
});

test('Routing main view is compact and keeps smart rollout cards out of sight',()=>{
  const start=src.indexOf('function routing(d)');
  const end=src.indexOf('function balancers',start);
  assert.ok(start>=0&&end>start);
  const body=src.slice(start,end);
  assert.match(body,/routing-compact/);
  assert.match(body,/route-line/);
  assert.match(body,/Add Rule/);
  assert.match(body,/routing-advanced/);
  assert.doesNotMatch(body,/smartRoutingCard\(d\)/);
  assert.doesNotMatch(body,/smartRoutingRevisionCard\(d\)/);
  assert.doesNotMatch(body,/smartRoutingRolloutCard\(d\)/);
});

test('Routing editor keeps common fields simple and advanced match collapsed',()=>{
  const start=src.indexOf('async function editRule');
  const end=src.indexOf('async function mutateRule',start);
  const body=src.slice(start,end);
  assert.match(body,/Domains \/ GeoSite/);
  assert.match(body,/Send to/);
  assert.match(body,/route-edit-advanced/);
  assert.match(body,/Advanced match/);
  assert.match(body,/\.\.\.r,type:'field'/);
});


test('Outbound list exposes Sanaei-style ping per row and Ping All',()=>{
  assert.match(src,/function outboundPingCell/);
  assert.match(src,/xvoutping/);
  assert.match(src,/xvoutpingall/);
  assert.match(src,/Ping All/);
  assert.match(src,/\/api\/outbounds\/test/);
  assert.match(src,/outbound-ping-slot/);
});

test('WARP scanner shows multiple endpoint routes and lets operator select one',()=>{
  const start=src.indexOf('async function scanSmartWarpPage');
  const end=src.indexOf('async function smartRoutingOutboundPreview',start);
  const body=src.slice(start,end);
  assert.match(body,/\/api\/warp\/endpoints\/scan/);
  assert.match(body,/warp-path-row/);
  assert.match(body,/Ping/);
  assert.match(body,/Loss/);
  assert.match(body,/Jitter/);
  assert.match(body,/xvwarpuse/);
  assert.doesNotMatch(body,/\/api\/smart-routing\/warp-scan/);
  assert.match(src,/\/api\/warp\/endpoint/);
});


test('Outbound and WARP tests require an explicit runtime server target',()=>{
  assert.match(src,/function serverLabel/);
  assert.match(src,/async function pickRuntimeServer/);
  assert.match(src,/The test or WARP action runs physically on the selected Hub\/Node/);
  assert.match(src,/server\}\)/);
  assert.match(src,/\/api\/runtime-targets/);
  assert.match(src,/\/api\/outbounds\/test/);
  assert.match(src,/\/api\/warp\/scan/);
  assert.match(src,/\/api\/warp\/endpoints\/scan/);
  assert.match(src,/server-test-badge/);
});

test('Routing editor exposes real Server + Inbound scope and rows display both',()=>{
  assert.match(src,/routingServerLabel/);
  assert.match(src,/routingInboundLabel/);
  assert.match(src,/\/api\/routing\/scopes/);
  assert.match(src,/\/api\/runtime-inbounds/);
  assert.match(src,/pickRuntimeServer/);
  assert.match(src,/inboundScope/);
  assert.match(src,/route-server/);
  assert.match(src,/route-inbound/);
  assert.match(src,/dark-user-/);
});

test('WARP activation asks for server and inbound scope before apply',()=>{
  const start=src.indexOf('async function setWarpSimpleMode');
  const end=src.indexOf('async function rotateWarpSimple',start);
  const body=src.slice(start,end);
  assert.match(body,/pickRuntimeServer/);
  assert.match(body,/pickRuntimeInbound/);
  assert.match(body,/inboundIds/);
  assert.match(body,/verification/);
  assert.match(src,/All inbounds on this server/);
});


test('WARP server rows show heartbeat state, real server IP and Direct/Tunnel coverage',()=>{
  assert.match(src,/function accessPathLabel/);
  assert.match(src,/DIRECT \+ TUNNEL/);
  const start=src.indexOf('function warpProfileRow');
  const end=src.indexOf('function warpProfilesPanel',start);
  const body=src.slice(start,end);
  assert.match(body,/p\.server\?\.address/);
  assert.match(body,/p\.server\?\.online/);
  assert.match(body,/Online','آنلاین/);
  assert.match(body,/Offline','آفلاین/);
  assert.match(body,/accessPathLabel\(p\.accessPaths/);
  assert.doesNotMatch(body,/\(p\.addresses\|\|\[\]\)\[0\]/);
});


test('WARP row can connect its outbound directly to an inbound',()=>{
  assert.match(src,/xvwarproute/);
  assert.match(src,/L\('Route','روتینگ'\)/);
  assert.match(src,/editRule\(null,String\(el\.dataset\.server\|\|''\),'out:warp'\)/);
});

test('Routing new rule defaults to a real server and supports preset WARP target',()=>{
  const start=src.indexOf('async function editRule');
  const end=src.indexOf('async function mutateRule',start);
  const body=src.slice(start,end);
  assert.match(body,/presetServer/);
  assert.match(body,/presetTarget/);
  assert.match(body,/defaultServer=profiles\.find\(x=>x\.registered\)\?\.serverId/);
  assert.match(body,/currentScope=index==null\?\(presetServer\|\|defaultServer\)/);
  assert.match(body,/currentTarget=presetTarget/);
  assert.match(body,/Connect Outbound to Inbound/);
  assert.match(body,/Outbound connected to inbound/);
});


test('Smart Adblock is independent per server and does not require WARP',()=>{
  assert.match(src,/function adblockProfilesPanel/);
  assert.match(src,/function adblockProfileRow/);
  assert.match(src,/\/api\/adblock\/profiles/);
  assert.match(src,/\/api\/adblock\/status/);
  assert.match(src,/\/api\/adblock\/mode/);
  assert.match(src,/xvadblockedit/);
  assert.match(src,/xvadblockoff/);
  assert.match(src,/No WARP profile is required/);
  assert.doesNotMatch(src,/data-warp-adblock/);
});

test('Smart Adblock activation uses Server + Inbound scope and verification',()=>{
  const start=src.indexOf('async function configureAdblock');
  const end=src.indexOf('async function disableAdblock',start);
  const body=src.slice(start,end);
  assert.match(body,/pickRuntimeServer/);
  assert.match(body,/pickRuntimeInbound/);
  assert.match(body,/inboundIds/);
  assert.match(body,/verification/);
  assert.match(body,/enabled:true/);
});
