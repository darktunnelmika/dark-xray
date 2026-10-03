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

test('Customer Mini App supports signed Bot Profile / Main Mini App launch without auth bypass',()=>{
 assert.match(js,/function launchInitData\(\)/);
 assert.match(js,/tgWebAppData/);
 assert.match(js,/waitForTelegramLaunch/);
 assert.match(js,/Mini App را از پروفایل یا منوی همین ربات/);
 assert.doesNotMatch(js,/فروشگاه باید از داخل دکمه Mini App همین ربات باز شود/);
 assert.match(js,/X-Telegram-Init-Data':launchInitData\(\)/);
});

test('Customer Mini App covers purchase services wallet support and representative purchase without referral UI',()=>{
 for(const token of ['خرید اشتراک','سرویس‌های من','کیف پول','پشتیبانی','پنل نمایندگی'])
   assert.ok(js.includes(token)||html.includes(token),token);
 assert.match(js,/data-checkout-wallet/);
 assert.match(js,/data-checkout-crypto/);
 assert.match(js,/renewService/);
 assert.match(js,/repCheckout/);
 assert.match(js,/openTelegramLink/);
 assert.match(js,/ارسال رسید در ربات/);
 assert.doesNotMatch(js,/دعوت دوستان|زیرمجموعه|ref\.url|ref\.invited|ref\.earned/);
 assert.match(js,/ACCOUNT OVERVIEW/);
 assert.match(css,/\.cu-command/);
 assert.match(css,/backdrop-filter:blur\(22px\)/);
});

test('Customer Mini App is mobile first and uses persistent five-tab navigation',()=>{
 assert.match(css,/safe-area-inset-bottom/);
 assert.match(css,/grid-template-columns:repeat\(5,1fr\)/);
 assert.match(css,/@media\(min-width:720px\)/);
 assert.match(js,/\['home','⌂','خانه'\]/);
 assert.match(js,/\['support','◇','پشتیبانی'\]/);
 assert.match(css,/body,#app,\.cu-shell\{color:#eaf8ff!important\}/);
 assert.match(css,/\.cu-form input,.cu-form select,.cu-form textarea/);
});

test('customer bot keyboard exposes WebApp launcher for owner and representative bots',()=>{
 assert.match(runtime,/customer_mini_app_url/);
 assert.match(runtime,/'📱 فروشگاه'/);
 assert.match(runtime,/web_app/);
});
