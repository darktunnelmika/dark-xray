const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');

const root=path.join(__dirname,'..');
const js=fs.readFileSync(path.join(root,'web','telegram-commerce.js'),'utf8');
const css=fs.readFileSync(path.join(root,'web','telegram-commerce.css'),'utf8');
const html=fs.readFileSync(path.join(root,'web','index.html'),'utf8');

test('telegram commerce assets are wired into the panel',()=>{
  assert.match(html,/assets\/telegram-commerce\.css/);
  assert.match(html,/assets\/telegram-commerce\.js/);
  assert.match(css,/\.tg-grid/);
  assert.match(css,/@media\(max-width:980px\)/);
});

test('telegram page exposes bot token plus numeric admin id controls',()=>{
  assert.match(js,/bot_token/);
  assert.match(js,/admin_telegram_id/);
  assert.match(js,/\/api\/telegram\/settings/);
  assert.match(js,/\/api\/telegram\/status/);
  assert.match(js,/\/api\/telegram\/test/);
  assert.match(js,/Telegram Bot & Store/);
});

test('store UI is backed only by DARK commerce APIs',()=>{
  for(const endpoint of [
    '/api/commerce/products',
    '/api/commerce/orders'
  ])assert.ok(js.includes(endpoint),endpoint);
  assert.match(js,/tgconfirm/);
  assert.match(js,/tgprovision/);
  assert.doesNotMatch(js,/localStorage\.setItem\([^)]*token/i);
});

test('telegram page is available to owner and representative navigation',()=>{
  assert.match(js,/enginePages\.telegram/);
  assert.match(js,/n\.splice\(at,0,\['telegram'/);
  assert.doesNotMatch(js,/isOwner\(\).*telegram/);
});
test('product v2 exposes activation delivery and security limits',()=>{
  for(const token of ['activation_mode','first_connection','delivery_mode','primary_inbound_id','ip_limit','hwid_limit','sale_limit_per_user'])
    assert.ok(js.includes(token),token);
  assert.match(js,/Subscription \+ Config/);
  assert.match(js,/Start on first connection/);
});

test('manual payment configuration is bot-only and absent from web panel',()=>{
  assert.doesNotMatch(js,/card_number|card_holder|bank_name/);
  assert.doesNotMatch(js,/\/api\/commerce\/gateways/);
  assert.doesNotMatch(js,/tggatewaynew|gatewayDialog/);
  assert.match(js,/BOT-ONLY PAYMENT/);
  assert.match(js,/Manual Payment/);
});

test('forum report center exposes repair flow and setup guidance',()=>{
  assert.match(js,/REPORT CENTER/);
  assert.match(js,/\/api\/telegram\/forum\/repair/);
  assert.match(js,/NOT CONNECTED/);
  assert.match(js,/Refresh status/);
});
test('recovery UI exposes rebind-required state',()=>{
  assert.match(js,/REBIND REQUIRED/);
  assert.match(js,/rebind_required/);
  assert.match(js,/Disaster Recovery state preserved/);
});