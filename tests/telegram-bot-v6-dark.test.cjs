const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs');
const runtime=fs.readFileSync('backend/telegram_runtime.py','utf8');
const customer=fs.readFileSync('backend/telegram_customer_runtime.py','utf8');

test('Bot V6 customer terminal exposes live branded home and clean primary actions',()=>{
  for(const token of ['DARK XRAY / CUSTOMER TERMINAL','● ONLINE · SECURE ACCESS','⚡ خرید سرویس','📦 سرویس‌های من','💳 کیف پول'])
    assert.ok(runtime.includes(token),token);
  assert.match(runtime,/'text':'فروشگاه'/);
  assert.doesNotMatch(runtime,/customer_app='◈ DARK Mini App'/);
});

test('Bot V6 shop is category driven and shows compact product economics',()=>{
  for(const token of ['DARK XRAY / SECURE MARKET','shopcat:','shopall','_price_brief','از {money','بازگشت به فروشگاه'])
    assert.ok(customer.includes(token),token);
});

test('Bot V6 purchase requires explicit confirmation before order creation',()=>{
  const b=customer.indexOf("if data.startswith('b:')");
  const buy=customer.indexOf("if data.startswith('buy:')",b);
  const wallet=customer.indexOf("if data.startswith('g:')",buy);
  assert.ok(b>0&&buy>b&&wallet>buy);
  assert.doesNotMatch(customer.slice(b,buy),/create_order\(/);
  assert.match(customer.slice(b,buy),/تأیید و ساخت سفارش/);
  assert.match(customer.slice(buy,wallet),/create_order\(/);
  assert.match(customer.slice(buy,wallet),/پرداخت از کیف پول/);
});

test('DARK Store V6 keeps common product creation simple with presets',()=>{
  for(const token of ['DARK STORE / V6','⚡ ساخت سریع محصول','⚙️ ساخت پیشرفته','stsname:multiturbo','stscat:turbo','10 GB','20 GB','50 GB','100 GB','12 ماه'])
    assert.ok(runtime.includes(token),token);
  assert.match(runtime,/activation_mode':'first_connection'/);
  assert.match(runtime,/delivery_mode':'subscription'/);
  assert.match(runtime,/show_qr':True/);
  assert.match(runtime,/show_portal':True/);
});

test('first-connect customer promise remains visible in delivery and product review',()=>{
  assert.match(runtime,/مدت سرویس از اولین اتصال واقعی شروع می‌شود/);
  assert.match(runtime,/شروع زمان: اولین اتصال واقعی/);
});

test('customer account surfaces share the DARK V6 language',()=>{
  for(const token of ['◉ DARK WALLET','▣ DARK SERVICES','◇ DARK SUPPORT','◆ DARK REPRESENTATIVE MARKET','⌂ منوی اصلی'])
    assert.ok(customer.includes(token),token);
  assert.match(customer,/شروع زمان: با اولین اتصال واقعی/);
});
