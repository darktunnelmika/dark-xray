const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');

const src=fs.readFileSync(path.join(__dirname,'..','web','settings-v2.js'),'utf8');
const css=fs.readFileSync(path.join(__dirname,'..','web','settings-v2.css'),'utf8');

test('Subscription Policy V3 consumes live backend status instead of guessing behavior',()=>{
  assert.match(src,/\/api\/subscription\/status/);
  assert.match(src,/subscriptionStatus/);
  assert.match(src,/traffic_scope/);
  assert.match(src,/device_policy/);
  assert.match(src,/auto_detect_rules/);
});

test('Subscription UI explains explicit format resolution and real output formats',()=>{
  assert.match(src,/No \?format: Clash\/Mihomo User-Agent/);
  for(const label of ['Base64','Raw','Clash / Mihomo','DARK JSON'])assert.match(src,new RegExp(label.replace('/','\\/')));
  assert.match(src,/Explicit \?format always wins/);
  assert.match(src,/Announcement is included here in the response body/);
});

test('Subscription UI exposes global traffic and per-client HWID semantics',()=>{
  assert.match(src,/global local \+ node traffic/);
  assert.match(src,/x-hwid/);
  assert.match(src,/limitHwid = 0/);
  assert.match(src,/clients_with_hwid_limit/);
  assert.match(src,/clients_at_device_limit/);
  assert.match(src,/subscription-clients/);
});

test('Subscription preview updates URL remark and resolver without saving',()=>{
  assert.match(src,/function syncSubscriptionPreview\(form\)/);
  assert.match(src,/data-sub-url/);
  assert.match(src,/data-sub-remark-preview/);
  assert.match(src,/data-sub-resolver/);
  assert.match(src,/replaceAll\('\{protocol\}'/);
});

test('Subscription V3 has dedicated compact status styles',()=>{
  assert.match(css,/Settings V3 — customer subscription output policy/);
  assert.match(css,/\.sv3-formats/);
  assert.match(css,/\.sv3-substats/);
  assert.match(css,/\.sv3-live-url/);
});
