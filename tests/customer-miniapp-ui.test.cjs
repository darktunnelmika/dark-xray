const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const root=path.join(__dirname,'..');
const html=fs.readFileSync(path.join(root,'web','telegram-customer.html'),'utf8');
const js=fs.readFileSync(path.join(root,'web','telegram-customer.js'),'utf8');
const css=fs.readFileSync(path.join(root,'web','telegram-customer.css'),'utf8');
const runtime=fs.readFileSync(path.join(root,'backend','telegram_runtime.py'),'utf8');

test('Customer Mini App is wired to Telegram signed initData and customer-only APIs',()=>{
 assert.match(html,/telegram-web-app\.js/);
 assert.match(html,/vendor-qr\.js/);
 assert.match(js,/Telegram\?\.WebApp/);
 assert.match(js,/\.initData/);
 assert.match(js,/X-Telegram-Init-Data/);
 assert.match(js,/\/api\/telegram-customer\/bootstrap/);
 assert.match(js,/\/api\/telegram-customer\/orders/);
 assert.match(js,/\/api\/telegram-customer\/services/);
 assert.match(js,/\/api\/telegram-customer\/renewals/);
 assert.match(js,/\/api\/telegram-customer\/topups/);
 assert.match(js,/\/api\/telegram-customer\/tickets/);
 assert.doesNotMatch(js,/\/api\/telegram\/operations\//);
});

test('Customer Mini App covers purchase services wallet support referral and representative purchase',()=>{
 for(const token of ['خرید اشتراک','سرویس‌های من','کیف پول','پشتیبانی','دعوت دوستان','پنل نمایندگی','Payment'])
   assert.ok(js.includes(token)||html.includes(token),token);
 assert.match(js,/data-checkout-wallet/);
 assert.match(js,/data-checkout-crypto/);
 assert.match(js,/renewService/);
 assert.match(js,/repCheckout/);
 assert.match(js,/receipt_channel/);
});

test('Customer Mini App is mobile first and uses persistent five-tab navigation',()=>{
 assert.match(css,/safe-area-inset-bottom/);
 assert.match(css,/grid-template-columns:repeat\(5,1fr\)/);
 assert.match(css,/@media\(min-width:720px\)/);
 assert.match(js,/\['home','⌂','خانه'\]/);
 assert.match(js,/\['support','🎫','پشتیبانی'\]/);
});

test('customer bot keyboard exposes WebApp launcher for owner and representative bots',()=>{
 assert.match(runtime,/customer_mini_app_url/);
 assert.match(runtime,/'📱 فروشگاه'/);
 assert.match(runtime,/web_app/);
});
