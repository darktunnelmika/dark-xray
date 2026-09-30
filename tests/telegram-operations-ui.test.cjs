const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const root=path.join(__dirname,'..');
const html=fs.readFileSync(path.join(root,'web','telegram-miniapp.html'),'utf8');
const js=fs.readFileSync(path.join(root,'web','telegram-miniapp.js'),'utf8');
const css=fs.readFileSync(path.join(root,'web','telegram-miniapp.css'),'utf8');
const commerce=fs.readFileSync(path.join(root,'web','telegram-commerce.js'),'utf8');

test('Telegram Operations Mini App uses signed initData and same-origin APIs',()=>{
 assert.match(html,/telegram-web-app\.js/);
 assert.match(js,/Telegram\?\.WebApp/);
 assert.match(js,/\.initData/);
 assert.match(js,/X-Telegram-Init-Data/);
 assert.doesNotMatch(js,/initDataUnsafe.*(?:fetch|Authorization|X-Telegram)/);
 assert.match(js,/\/api\/telegram-miniapp\/bootstrap/);
 assert.match(js,/\/api\/telegram-miniapp\/simple-plans/);
 assert.match(js,/\/api\/telegram-miniapp\/payments/);
 assert.match(js,/\/api\/telegram-miniapp\/support/);
});

test('Mini App contains dashboard plans payment and support workspaces',()=>{
 for(const token of ['dashboard','plans','payments','support','Payment Center','Support Center'])
  assert.ok(js.includes(token),token);
 assert.match(css,/safe-area-inset/);
 assert.match(css,/@media\(min-width:720px\)/);
});

test('web Telegram page exposes Operations V4 centers and hosted crypto setup',()=>{
 assert.match(commerce,/OPERATIONS V4/);
 assert.match(commerce,/PAYMENT CENTER/);
 assert.match(commerce,/SUPPORT CENTER/);
 assert.match(commerce,/Hosted crypto gateway/);
 assert.match(commerce,/\/api\/telegram\/operations\/crypto/);
 assert.match(commerce,/\/api\/telegram\/operations\/payments/);
});


test('Support Center exposes quick reply management without leaking bot secrets',()=>{
 assert.match(commerce,/Quick replies/);
 assert.match(commerce,/tgquickreplies/);
 assert.match(commerce,/support\/quick-replies/);
 assert.doesNotMatch(commerce,/token_enc/);
 assert.doesNotMatch(commerce,/c\.webhook_secret|crypto\.webhook_secret/);
});
