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
  assert.doesNotMatch(js,/buyer_telegram_id|customer.*volume_credit_bytes/i);
});