const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');

const live=fs.readFileSync('web/live.js','utf8');
const finance=fs.readFileSync('web/finance-v2.js','utf8');
const server=fs.readFileSync('backend/server.py','utf8');
const commerce=fs.readFileSync('backend/bot_commerce.py','utf8');

test('telegram commerce workspace is wired into the panel navigation',()=>{
 assert.match(live,/\['bot',uiText\('Telegram Bot'/);
 assert.match(live,/case'bot':html=await botPage\(\)/);
 assert.match(live,/\/api\/bot\/settings/);
 assert.match(live,/\/api\/shop\/products/);
 assert.match(live,/\/api\/shop\/payment-methods/);
 assert.match(live,/\/api\/shop\/orders/);
});

test('bot activation keeps token secret in UI and backend response model',()=>{
 assert.match(live,/field\(uiText\(s\.configured\?'New token/);
 assert.match(live,/'password'/);
 assert.match(commerce,/token_hint/);
 assert.doesNotMatch(commerce,/return \{[^}]*"token": token/s);
 assert.match(commerce,/self\.auth\.cipher\.encrypt/);
});

test('webhook and payment fulfillment are explicit and verified',()=>{
 assert.match(server,/@app\.post\('\/telegram\/\{public_id\}'\)/);
 assert.match(server,/x-telegram-bot-api-secret-token/);
 assert.match(commerce,/confirm_manual/);
 assert.match(commerce,/fulfillment_failed/);
 assert.match(commerce,/Selected gateway adapter is not configured yet/);
});

test('representative credit ledger copy no longer claims prices do not exist',()=>{
 assert.match(finance,/Telegram Commerce/);
 assert.doesNotMatch(finance,/No customer sale price is stored in DARK/);
});
