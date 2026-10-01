const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');

const root=path.join(__dirname,'..');
const js=fs.readFileSync(path.join(root,'web','representative-marketplace.js'),'utf8');
const html=fs.readFileSync(path.join(root,'web','index.html'),'utf8');

test('representative marketplace owner asset is wired',()=>{
  assert.match(html,/assets\/representative-marketplace\.js/);
  assert.match(js,/enginePages\.repplans/);
  assert.match(js,/Representative Marketplace/);
});

test('representative plan management is owner-only in UI',()=>{
  assert.match(js,/state\.me\?\.role==='owner'/);
  assert.match(js,/Only the primary Owner/);
  assert.match(js,/\/api\/representative-marketplace\/plans/);
});

test('owner controls every technical representative plan field',()=>{
  for(const token of ['price_minor','duration_days','volume_credit_bytes','unlimited_credit',
    'max_clients','prefix','max_client_ips','max_client_hwid','allowed_inbounds',
    'bot_allowed','renewal_enabled','active','visible'])assert.ok(js.includes(token),token);
});

test('customer customization is explicitly absent from owner marketplace page',()=>{
  assert.match(js,/buyer cannot customize/i);
  assert.doesNotMatch(js,/name="buyer_telegram_id"|name="customer.*volume_credit_bytes"/i);
});
test('owner page shows representative subscriptions and expiry state',()=>{
  assert.match(js,/representative-marketplace\/subscriptions/);
  assert.match(js,/Representative subscriptions/);
  assert.match(js,/expires_at/);
});
test('V2 uses guided creation and owner operations center',()=>{
 for(const token of ['newPlanWizard','Step','shell(1,','shell(2,','shell(3,','Review & publish','representative-marketplace/orders','representative-marketplace/summary','Order operations'])assert.ok(js.includes(token),token);
});
test('V2 creation keeps technical limits advanced while buyer remains immutable',()=>{
 const a=js.indexOf('function newPlanWizard(){'),b=js.indexOf('async function planDialog(',a),x=js.slice(a,b);
 assert.match(x,/Advanced limits/);assert.match(x,/Max client IP/);assert.match(x,/Max client HWID/);assert.doesNotMatch(x,/buyer_telegram_id/);
});