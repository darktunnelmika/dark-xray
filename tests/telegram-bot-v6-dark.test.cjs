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
  assert.match(runtime,/زمان سرویس هنوز شروع نشده؛ با اولین اتصال واقعی فعال می‌شود/);
  assert.match(runtime,/شروع زمان: اولین اتصال واقعی/);
});

test('customer account surfaces share the DARK V6 language',()=>{
  for(const token of ['◉ DARK WALLET','▣ DARK SERVICES','◇ DARK SUPPORT','◆ DARK REPRESENTATIVE MARKET','⌂ منوی اصلی'])
    assert.ok(customer.includes(token),token);
  assert.match(customer,/شروع زمان: با اولین اتصال واقعی/);
});

test('Bot V6 Stage 2 exposes owner Action Center and filtered order operations',()=>{
  for(const token of ['DARK ACTION CENTER','ops_action','ops_attention','ordlist:pending','ordlist:waiting','ordlist:failed','DARK SERVICE WATCH'])
    assert.ok(runtime.includes(token),token);
});

test('Bot V6 Stage 2 keeps deduplicated service health notifications',()=>{
  for(const token of ['telegram_customer_notifications','notify_service_health','expiry24:','expiry72:','volume20:','volume5:','DARK SERVICE ALERT'])
    assert.ok(runtime.includes(token),token);
  assert.match(runtime,/last_notification_scan>=300/);
});

test('Bot V6 Stage 3 exposes representative control and durable Broadcast Center',()=>{
  for(const token of ['DARK REP CONTROL','DARK REPRESENTATIVES','repctl:','repclients:','DARK BROADCAST CENTER','bcseg:all','bcseg:active','bcseg:expiring','bcseg:low','telegram_broadcasts','telegram_broadcast_recipients','process_broadcasts'])
    assert.ok(runtime.includes(token),token);
  assert.match(runtime,/📣 اعلان‌ها/);
});

test('Bot V6 Stage 3 keeps broadcasts scoped and queued rather than synchronous fanout',()=>{
  assert.match(runtime,/row\.get\('owner'\)!=self\.owner:continue/);
  assert.match(runtime,/LIMIT 15/);
  assert.match(runtime,/status IN \('queued','running'\)/);
  assert.match(runtime,/telegram\.broadcast_complete/);
});

test('Bot V6 Stage 4 exposes Customer CRM, 360 view and direct messaging',()=>{
  for(const token of ['DARK CUSTOMER CRM','CUSTOMER 360','cuslist:active','cuslist:online','cuslist:expiring','cuslist:blocked','cusorders:','custickets:','clmsg:','DARK MESSAGE','telegram.crm_message'])
    assert.ok(runtime.includes(token),token);
});

test('Bot V6 Stage 4 keeps CRM owner scoped',()=>{
  assert.match(runtime,/r\.get\('owner'\)==self\.owner/);
  assert.match(runtime,/WHERE owner=\? AND telegram_id=\?/);
  assert.match(runtime,/WHERE o\.owner=\? AND o\.buyer_telegram_id=\?/);
});

test('Bot V6 Stage 5 connects purchase, first-connect, alerts and renewal lifecycle',()=>{
  for(const token of ['DARK SERVICE READY','WAITING FIRST CONNECTION','DARK SERVICE ACTIVATED','FIRST CONNECTION VERIFIED','DARK RENEW COMPLETE'])
    assert.ok(runtime.includes(token)||customer.includes(token),token);
  assert.match(runtime,/usvclink:/);
  assert.match(runtime,/usvcrenew:/);
  assert.match(runtime,/DARK SERVICE ALERT/);
});

test('Bot V6 Stage 6 exposes Support Center V2 quick replies and CRM bridge',()=>{
  for(const token of ['DARK SUPPORT CENTER','SUPPORT TICKET','asupquick:','asupcrm:','Customer 360','telegram.support_quick'])
    assert.ok(customer.includes(token)||runtime.includes(token),token);
  assert.match(customer,/حل شد \+ بستن/);
  assert.match(customer,/Subscription را در برنامه بروزرسانی کن/);
});
