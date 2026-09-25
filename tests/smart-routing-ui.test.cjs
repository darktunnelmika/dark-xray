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
  assert.match(body,/does not save settings or restart Xray/);
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