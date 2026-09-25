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
    '/api/commerce/gateways',
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