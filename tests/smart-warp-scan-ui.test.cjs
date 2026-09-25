const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');

const src=fs.readFileSync('web/inbounds-v3.js','utf8');

test('Inbounds exposes isolated Smart WARP scan',()=>{
  assert.match(src,/data-v3-action="warp-scan"/);
  assert.match(src,/\/api\/smart-routing\/warp-scan/);
  assert.match(src,/async function openWarpScan/);
  assert.match(src,/Production Xray is not restarted/);
});

test('Smart WARP scan UI does not mutate routing or restart Xray',()=>{
  const start=src.indexOf('async function openWarpScan');
  const end=src.indexOf('async function generateKeys',start);
  assert.ok(start>=0);
  const body=src.slice(start,end>start?end:src.indexOf('function closeV3',start));
  assert.doesNotMatch(body,/\/api\/settings\/[^'\"]+['\"],['\"]PUT/);
  assert.doesNotMatch(body,/\/api\/core\/restart/);
  assert.doesNotMatch(body,/saveEditor\(/);
  assert.match(body,/warp-scan/);
});

test('Inbound editor Smart WARP tab scans and selects preview state only',()=>{
  assert.match(src,/\['smartwarp','Smart WARP'\]/);
  assert.match(src,/data-v3-pane="smartwarp"/);
  assert.match(src,/data-v3-action="warp-scan-editor"/);
  assert.match(src,/data-v3-action="warp-select"/);
  assert.match(src,/selectedWarpTag/);
  const buildStart=src.indexOf('function buildBody');
  const buildEnd=src.indexOf('function syncForm',buildStart);
  const build=src.slice(buildStart,buildEnd);
  assert.doesNotMatch(build,/selectedWarpTag/);
  assert.doesNotMatch(build,/smartWarp/);
});
