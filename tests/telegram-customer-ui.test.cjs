const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const root=path.join(__dirname,'..');
const html=fs.readFileSync(path.join(root,'web','telegram-customer.html'),'utf8');
const js=fs.readFileSync(path.join(root,'web','telegram-customer.js'),'utf8');
const css=fs.readFileSync(path.join(root,'web','telegram-customer.css'),'utf8');
const runtime=fs.readFileSync(path.join(root,'backend','telegram_runtime.py'),'utf8');

test('Customer Mini App V5 uses Telegram signed initData and no panel token',()=>{
 assert.match(html,/telegram-web-app\.js/);
 assert.match(html,/vendor-qr\.js/);
 assert.match(js,/X-Telegram-Init-Data/);
 assert.match(js,/Telegram\?\.WebApp/);
 assert.doesNotMatch(js,/dark_session|Authorization\s*:/i);
 assert.match(runtime,/📱 پنل من/);
 assert.match(runtime,/customer_mini_app_url/);
});

test('Customer V5 covers purchase services wallet support referral and representative UX',()=>{
 for(const token of ['/api/telegram-customer/orders','/api/telegram-customer/services/',
  '/api/telegram-customer/wallet/topups','/api/telegram-customer/support',
  '/api/telegram-customer/representative/orders','زیرمجموعه','خرید پنل نمایندگی'])
   assert.ok(js.includes(token),token);
 assert.match(js,/پرداخت/);
 assert.match(js,/QR/);
});

test('My Services V5 exposes renewal and volume add-on without direct renewal gateway',()=>{
 assert.match(js,/\/renew/);
 assert.match(js,/volume-addons/);
 assert.match(js,/خرید حجم اضافه/);
 assert.doesNotMatch(js,/renewals\/.*gateway/);
});

test('Customer Mini App is mobile-safe',()=>{
 assert.match(css,/safe-area-inset/);
 assert.match(css,/\.cu-nav/);
 assert.match(css,/@media\(min-width:720px\)/);
});
