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

test('Bot V6 Stage 7 exposes Growth Center and explicit retention campaigns',()=>{
  for(const token of ['DARK GROWTH CENTER','SALES + RETENTION','RETENTION POOLS','growtpl:pending','growtpl:expired','growtpl:expiring','growtpl:low','GROWTH CAMPAIGN PREVIEW'])
    assert.ok(runtime.includes(token),token);
  assert.match(runtime,/📈 رشد و فروش/);
  assert.match(runtime,/broadcast_review/);
  assert.match(runtime,/قرار دادن در صف/);
});

test('Bot V6 Stage 7 retention segments stay owner scoped and never auto-charge',()=>{
  assert.match(runtime,/FROM commerce_orders WHERE owner=\? AND created_at<=\?/);
  assert.match(runtime,/row\.get\('owner'\)!=self\.owner:continue/);
  assert.doesNotMatch(runtime.slice(runtime.indexOf('def growth_campaign_preview'),runtime.indexOf('def _broadcast_targets')),/pay_purchase\(|pay_renewal\(|_debit_tx/);
});

test('Bot V6 Stage 8 exposes self-service Service Doctor and smart support ticket',()=>{
  for(const token of ['DARK SERVICE DOCTOR','usvcdiag:','usvchelp:','Service Doctor Snapshot','Smart Ticket','بررسی هوشمند سرویس'])
    assert.ok(customer.includes(token),token);
  assert.match(customer,/WAITING FIRST CONNECTION/);
  assert.match(customer,/ACTION REQUIRED · EXPIRED/);
  assert.match(customer,/ACTION REQUIRED · VOLUME FINISHED/);
});

test('Bot V6 Stage 8 doctor stays account scoped and does not probe tunnels',()=>{
  const start=customer.indexOf('def _service_doctor_snapshot');
  const end=customer.indexOf('def customer_connection',start);
  const block=customer.slice(start,end);
  assert.match(block,/_customer_client_row/);
  assert.doesNotMatch(block,/\/api\/nodes|tunnel|restart|probe/);
});

test('Bot V6 Stage 9 exposes notification preferences and consent-aware campaigns',()=>{
  for(const token of ['DARK NOTIFICATION CONTROL','nprefs','nptoggle:service','nptoggle:marketing','telegram_customer_preferences'])
    assert.ok(runtime.includes(token)||customer.includes(token),token);
  assert.match(runtime,/marketing_enabled/);
  assert.match(runtime,/service_alerts_enabled/);
  assert.match(runtime,/marketing_opt_out/);
  assert.match(runtime,/kind TEXT NOT NULL DEFAULT 'operational'/);
  assert.match(runtime,/skipped INTEGER NOT NULL DEFAULT 0/);
});

test('Bot V6 Stage 9 keeps essential messaging separate from optional alerts and marketing',()=>{
  assert.match(runtime,/notification_preferences\(owner,telegram_id\)\['service_alerts_enabled'\]/);
  assert.match(runtime,/growth_campaign_preview[\s\S]*marketing=True/);
  assert.match(runtime,/\'kind\':\'marketing\'/);
  assert.match(runtime,/\'kind\':\'operational\'/);
  assert.match(customer,/پیام‌های ضروری خرید، پرداخت، اولین اتصال و پشتیبانی همیشه ارسال می‌شوند/);
});

test('Bot V6 Stage 10 exposes Bot Health and Telegram Surface Recovery Center',()=>{
  for(const token of ['DARK BOT HEALTH','configure_telegram_surface','bothealth','botrepair','botrepairok','telegram.bot_surface_repair','Repair Telegram Surface'])
    assert.ok(runtime.includes(token),token);
  assert.match(runtime,/getWebhookInfo/);
  assert.match(runtime,/setMyCommands/);
  assert.match(runtime,/setChatMenuButton/);
  assert.match(runtime,/getChatMenuButton/);
});

test('Bot V6 Stage 10 startup and manual repair share one Telegram surface contract',()=>{
  const run=runtime.slice(runtime.indexOf('def run(self):'),runtime.indexOf('def bot_config',runtime.indexOf('def run(self):')));
  assert.match(run,/configure_telegram_surface/);
  const repair=runtime.slice(runtime.indexOf('def repair_telegram_surface'),runtime.indexOf('def admin_settings'));
  assert.match(repair,/configure_telegram_surface/);
  assert.match(repair,/self\.runtime\.wake\(\)/);
});
