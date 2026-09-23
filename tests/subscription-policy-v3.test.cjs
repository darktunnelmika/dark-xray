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

// Execute the production renderer, not just its source-level contract.
function renderedPolicy(status){
  const vm=require('node:vm');
  const ctx=vm.createContext({
    L:en=>en,esc:s=>String(s??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;'),
    card:(title,description,body)=>body,toggle:()=>'',field:()=>'',segment:()=>'',ico:()=>'',
    subFormatCard:()=>'',subStat:()=>'',saveButton:()=>'',fa:String
  });
  const start=src.indexOf('function subscriptionObservedPolicy('),end=src.indexOf('function ipguard(');
  assert.ok(start>=0&&end>start,'Production subscription renderer must exist');
  vm.runInContext(src.slice(start,end)+';globalThis.renderSubscription=subscription;',ctx);
  return ctx.renderSubscription({subscription:{},subscriptionStatus:status,runtimeStatus:{actual:{}}});
}
test('Subscription renders observed traffic scope and actual detection rules',()=>{
  const html=renderedPolicy({traffic_scope:'local_plus_remote_nodes',auto_detect_rules:[{contains:'fixture-client',format:'raw'}]});
  assert.match(html,/subscription-userinfo: global local \+ node traffic/);
  assert.match(html,/data-sub-observed-rules>fixture-client → Raw<\/span>/);
});
test('Missing or failed subscription status is not presented as verified policy',()=>{
  for(const status of [{},{error:'offline',traffic_scope:'local_plus_remote_nodes',auto_detect_rules:[{contains:'clash',format:'clash'}]}]){
    const html=renderedPolicy(status);
    assert.match(html,/subscription-userinfo: Unavailable \/ unverified/);
    assert.match(html,/data-sub-observed-rules>Unavailable \/ unverified<\/span>/);
  }
});
test('Malformed detection rules cannot crash the subscription page or invent defaults',()=>{
  for(const rules of [null,{},[null],[{contains:'clash',format:'toString'}],[{contains:'',format:'clash'}],Array(33).fill({contains:'clash',format:'clash'})]){
    const html=renderedPolicy({traffic_scope:'future_scope',auto_detect_rules:rules});
    assert.match(html,/subscription-userinfo: Unavailable \/ unverified/);
    assert.match(html,/data-sub-observed-rules>Unavailable \/ unverified<\/span>/);
  }
});
test('Observed detection text is escaped and an empty ruleset stays empty',()=>{
  const html=renderedPolicy({auto_detect_rules:[{contains:'<script>alert(1)</script>',format:'raw'}]});
  assert.match(html,/&lt;script&gt;alert\(1\)&lt;\/script&gt; → Raw/);
  assert.doesNotMatch(html,/<script>/);
  assert.match(renderedPolicy({auto_detect_rules:[]}),/data-sub-observed-rules>No automatic rules reported<\/span>/);
});
