(()=>{'use strict';
const tg=window.Telegram?.WebApp,app=document.getElementById('app');
const qs=new URLSearchParams(location.search),owner=qs.get('owner')||'',marker='/assets/telegram-customer.html',idx=location.pathname.lastIndexOf(marker),BASE=idx>=0?location.pathname.slice(0,idx):'';
let data=null,tab='shop';
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const money=v=>Number(v||0).toLocaleString('fa-IR')+' تومان';
const bytes=n=>{n=Number(n||0);if(!n)return '0 B';const u=['B','KB','MB','GB','TB'];let i=0;while(n>=1024&&i<u.length-1){n/=1024;i++}return (i>1?n.toFixed(n>=10?1:2):Math.round(n))+' '+u[i]};
const date=ms=>!ms?'بدون انقضا':new Date(Number(ms)).toLocaleString('fa-IR');
const copy=async v=>{try{await navigator.clipboard.writeText(String(v));tg?.HapticFeedback?.notificationOccurred('success');return true}catch{return false}};
async function api(path,method='GET',body){
 const sep=path.includes('?')?'&':'?',url=BASE+path+sep+'owner='+encodeURIComponent(owner);
 const r=await fetch(url,{method,headers:{'Content-Type':'application/json','X-Telegram-Init-Data':tg?.initData||''},body:body===undefined?undefined:JSON.stringify(body)});
 const t=await r.text();let d={};try{d=t?JSON.parse(t):{};}catch{d={detail:t}}
 if(!r.ok)throw Error(d.detail||('HTTP '+r.status));return d;
}
const states={
 pending:['در انتظار پرداخت','warn'],awaiting_payment:['در انتظار پرداخت','warn'],payment_review:['بررسی پرداخت','warn'],
 paid:['پرداخت شد / در حال ساخت','warn'],provisioned_waiting_activation:['آماده · منتظر اولین اتصال','ok'],
 provisioned:['فعال','ok'],renewed:['تمدید شد','ok'],payment_rejected:['پرداخت رد شد','bad'],
 cancelled:['لغو شده','bad'],failed_refunded:['ناموفق · مبلغ برگشت','bad']
};
function state(v){const x=states[v]||[v,''];return '<span class="cu-order-state"><i class="cu-dot '+x[1]+'"></i><small>'+esc(x[0])+'</small></span>'}
function nav(){return '<nav class="cu-nav">'+[['home','⌂','خانه'],['shop','◈','خرید'],['services','▣','سرویس‌ها'],['wallet','◉','کیف پول'],['support','◇','پشتیبانی']].map(x=>'<button data-tab="'+x[0]+'" class="'+(tab===x[0]?'active':'')+'"><b>'+x[1]+'</b>'+x[2]+'</button>').join('')+'</nav>'}
function header(){return '<header class="cu-head"><div class="cu-brand"><span class="cu-brand-mark">DX</span><div><small>DARK NETWORK / CUSTOMER NODE</small><h1>DARK XRAY</h1><em>'+esc(data?.identity?.first_name||data?.identity?.username||'Customer')+'</em></div></div><span class="cu-wallet"><small>WALLET</small>'+money(data?.wallet?.balance_minor||0)+'</span></header>'}
function orderRows(limit=5){const rows=data.orders||[];return rows.length?'<div class="cu-list">'+rows.slice(0,limit).map(o=>'<button class="cu-row cu-btn" data-order="'+esc(o.id)+'"><div><b>'+esc(o.product_name||o.product_id)+'</b><small>'+money(o.amount_minor)+' · '+new Date(o.created_at*1000).toLocaleString('fa-IR')+'</small></div>'+state(o.status)+'</button>').join('')+'</div>':'<div class="cu-empty">هنوز سفارشی ثبت نشده است.</div>'}
function serviceSummary(s){const pct=s.unlimited?0:Math.min(100,Math.round((s.used_bytes/Math.max(1,s.total_bytes))*100));return '<button class="cu-row cu-btn" data-service="'+encodeURIComponent(s.id)+'"><div><b>'+esc(s.name)+'</b><small>'+esc(s.id)+'</small><div class="cu-progress"><i style="width:'+pct+'%"></i></div><em>'+(s.unlimited?'نامحدود':bytes(s.remaining_bytes)+' باقی‌مانده')+' · '+date(s.expiry_ms)+'</em></div><span class="cu-pill '+(!s.blocked&&s.enabled?'ok':'warn')+'">'+(!s.blocked&&s.enabled?'فعال':'محدود')+'</span></button>'}
function home(){
 const ref=data.referral||{},rep=data.representative||{};
 return '<section><div class="cu-grid">'+
  '<div class="cu-card cu-stat"><small>کیف پول</small><b>'+money(data.wallet?.balance_minor||0)+'</b><em>'+(data.ledger?.length||0)+' تراکنش اخیر</em></div>'+
  '<div class="cu-card cu-stat"><small>سرویس‌های من</small><b>'+(data.services?.length||0)+'</b><em>'+(data.services||[]).filter(x=>!x.blocked&&x.enabled).length+' فعال</em></div>'+
  '<div class="cu-card cu-stat"><small>زیرمجموعه</small><b>'+(ref.invited||0)+'</b><em>'+money(ref.earned||0)+' پاداش</em></div>'+
 '</div><div class="cu-card"><h2>سفارش‌های اخیر</h2>'+orderRows(4)+'</div>'+
 '<div class="cu-card"><h2>دعوت دوستان</h2><small>پاداش هر اولین خرید موفق: '+money(ref.reward_minor||0)+'</small><div class="cu-link"><code>'+esc(ref.url||'لینک دعوت هنوز آماده نیست')+'</code><div class="cu-actions"><button class="cu-btn" data-copy="'+esc(ref.url||'')+'">کپی لینک</button></div></div></div>'+
 (rep.available?'<div class="cu-card"><h2>پنل نمایندگی</h2><small>'+(rep.subscription?'نمایندگی فعال داری؛ تمدید از پلن‌های منتشرشده انجام می‌شود.':'می‌توانی پنل نمایندگی تعریف‌شده توسط Owner را بخری.')+'</small><button class="cu-btn primary wide" data-rep-open>مشاهده پلن‌های نمایندگی</button></div>':'')+
 '</section>';
}
function shop(){
 const rows=data.products||[];
 return '<section><div class="cu-shop-hero"><div><span>SECURE ACCESS MARKET</span><h2>خرید اشتراک</h2><p>پلن مناسب را انتخاب کن؛ تحویل سرویس و لینک اتصال از همین محیط انجام می‌شود.</p></div><i>ONLINE</i></div>'+
 (rows.length?rows.map(p=>'<div class="cu-card"><h2>'+esc(p.name)+'</h2><small>'+esc(p.description||p.category||'')+'</small><div class="cu-price-grid">'+(p.prices||[]).filter(x=>x.active).map(x=>'<button class="cu-price cu-btn" data-buy-product="'+esc(p.id)+'" data-buy-price="'+esc(x.id)+'"><b>'+esc(x.label)+'</b><small>'+money(x.price_minor)+' · '+x.duration_days+' روز</small><small>'+(x.volume_bytes?bytes(x.volume_bytes):'نامحدود')+' · IP '+x.ip_limit+'</small></button>').join('')+'</div></div>').join(''):'<div class="cu-card cu-empty">فعلاً پلنی برای فروش منتشر نشده است.</div>')+
 '<div class="cu-card"><h2>وضعیت سفارش‌ها</h2>'+orderRows(20)+'</div></section>';
}
function services(){const rows=data.services||[];return '<section><div class="cu-card"><h2>سرویس‌های من</h2><small>مصرف، انقضا، لینک اتصال و تمدید را از همین‌جا مدیریت کن.</small></div><div class="cu-card">'+(rows.length?'<div class="cu-list">'+rows.map(serviceSummary).join('')+'</div>':'<div class="cu-empty">هنوز سرویسی نداری.</div>')+'</div></section>'}
function wallet(){
 const rows=data.ledger||[],pending=(data.orders||[]).filter(x=>['pending','awaiting_payment','payment_review'].includes(x.status)).length;
 return '<section><div class="cu-grid"><div class="cu-card cu-stat"><small>موجودی</small><b>'+money(data.wallet?.balance_minor||0)+'</b></div><div class="cu-card cu-stat"><small>سفارش معلق</small><b>'+pending+'</b></div></div>'+
 '<div class="cu-card"><h2>شارژ کیف پول</h2>'+(data.payments?.manual_topup?'<form id="topup-form" class="cu-form"><label>مبلغ تومان<input name="amount" type="number" min="1000" step="1000" placeholder="200000" required></label><button class="cu-btn primary">دریافت اطلاعات پرداخت</button></form>':'<div class="cu-notice">شارژ کارت‌به‌کارت توسط فروشنده فعال نشده است.</div>')+'</div>'+
 '<div class="cu-card"><h2>تراکنش‌ها</h2><div class="cu-list">'+(rows.length?rows.map(x=>'<div class="cu-row"><div><b>'+esc(x.kind)+'</b><small>'+new Date(x.created_at*1000).toLocaleString('fa-IR')+'</small></div><b>'+(x.delta_minor>=0?'+':'')+money(x.delta_minor)+'</b></div>').join(''):'<div class="cu-empty">تراکنشی ثبت نشده است.</div>')+'</div></div></section>';
}
function support(){
 const rows=data.tickets||[];
 return '<section><div class="cu-card"><h2>پشتیبانی</h2><form id="ticket-form" class="cu-form"><label>موضوع<input name="subject" maxlength="128" required></label><label>پیام<textarea name="message" rows="4" maxlength="4000" required></textarea></label><button class="cu-btn primary">ارسال تیکت</button></form></div>'+
 '<div class="cu-card"><h2>تیکت‌های من</h2><div class="cu-list">'+(rows.length?rows.map(t=>'<button class="cu-row cu-btn" data-ticket="'+t.row_id+'"><div><b>'+esc(t.subject)+'</b><small>'+esc(t.status)+' · '+new Date(t.updated_at*1000).toLocaleString('fa-IR')+'</small></div><span>›</span></button>').join(''):'<div class="cu-empty">تیکتی نداری.</div>')+'</div></div></section>';
}
function render(){if(!data)return;app.innerHTML='<main class="cu-shell">'+header()+nav()+(tab==='home'?home():tab==='shop'?shop():tab==='services'?services():tab==='wallet'?wallet():support())+'</main>';bind()}
async function reload(){data=await api('/api/telegram-customer/bootstrap');render()}
function showError(e){tg?.showAlert?.(e.message||String(e));if(!tg?.showAlert)alert(e.message||String(e))}
async function checkout(product,price){
 try{
  const r=await api('/api/telegram-customer/orders','POST',{product_id:product,price_id:price}),o=r.order,p=r.payments||{};
  let buttons='';
  if(Number(p.wallet?.balance_minor||0)>=Number(o.amount_minor))buttons+='<button class="cu-btn good" data-checkout-wallet="'+esc(o.id)+'">پرداخت از کیف پول</button>';
  if(p.crypto?.enabled)buttons+='<button class="cu-btn primary" data-checkout-crypto="'+esc(o.id)+'" data-gateway="'+esc(p.crypto.id||'crypto')+'">'+esc(p.crypto.label||'Crypto')+'</button>';
  buttons+='<button class="cu-btn" data-tabgo="wallet">شارژ کیف پول</button>';
  app.innerHTML='<main class="cu-shell"><button class="cu-btn cu-back" id="back">← برگشت</button><div class="cu-card"><h2>تأیید سفارش</h2><div class="cu-row"><div><b>'+esc(o.product_id)+'</b><small>شناسه: '+esc(o.id)+'</small></div><b>'+money(o.amount_minor)+'</b></div><div class="cu-actions" style="margin-top:10px">'+buttons+'</div></div></main>';
  document.getElementById('back').onclick=()=>{tab='shop';render()};
  document.querySelector('[data-checkout-wallet]')?.addEventListener('click',async e=>{try{await api('/api/telegram-customer/orders/'+encodeURIComponent(e.currentTarget.dataset.checkoutWallet)+'/pay','POST',{method:'wallet',gateway_id:'crypto'});await reload();await openOrder(e.currentTarget.dataset.checkoutWallet)}catch(ex){showError(ex)}});
  document.querySelector('[data-checkout-crypto]')?.addEventListener('click',async e=>{try{const x=await api('/api/telegram-customer/orders/'+encodeURIComponent(e.currentTarget.dataset.checkoutCrypto)+'/pay','POST',{method:'crypto',gateway_id:e.currentTarget.dataset.gateway});if(x.checkout_url)tg?.openLink?tg.openLink(x.checkout_url):location.href=x.checkout_url;await reload();await openOrder(e.currentTarget.dataset.checkoutCrypto)}catch(ex){showError(ex)}});
  document.querySelector('[data-tabgo]')?.addEventListener('click',()=>{tab='wallet';render()});
 }catch(e){showError(e)}
}
async function openOrder(id){
 try{
  const o=await api('/api/telegram-customer/orders/'+encodeURIComponent(id)),d=o.delivery||{};
  let links='';
  for(const [label,url] of [['Subscription',d.subscription_url],['Main Config',d.main_config],['Portal',d.portal_url]])if(url)links+='<div class="cu-link"><b>'+label+'</b><code>'+esc(url)+'</code><div class="cu-actions"><button class="cu-btn" data-copy="'+esc(url)+'">کپی</button>'+(label==='Subscription'?'<button class="cu-btn" data-qr="'+esc(url)+'">QR</button>':'')+'</div><div class="cu-qr" hidden></div></div>';
  app.innerHTML='<main class="cu-shell"><button class="cu-btn cu-back" id="back">← برگشت</button><div class="cu-card"><h2>'+esc(o.product_name||o.product_id)+'</h2>'+state(o.status)+'<div class="cu-row" style="margin-top:10px"><div><small>مبلغ</small><b>'+money(o.amount_minor)+'</b></div><div><small>شناسه سفارش</small><b>'+esc(o.id)+'</b></div></div>'+links+'<button class="cu-btn wide" id="refresh-order" style="margin-top:10px">بروزرسانی وضعیت</button></div></main>';
  document.getElementById('back').onclick=()=>{tab='shop';render()};document.getElementById('refresh-order').onclick=()=>openOrder(id);bindUtility();
 }catch(e){showError(e)}
}
async function openService(id){
 try{
  const s=await api('/api/telegram-customer/services/'+encodeURIComponent(id)),d=s.delivery||{},pct=s.unlimited?0:Math.min(100,Math.round(s.used_bytes/Math.max(1,s.total_bytes)*100));
  let links='';for(const [label,url] of [['Subscription',d.subscription_url],['Main Config',d.main_config],['Portal',d.portal_url]])if(url)links+='<div class="cu-link"><b>'+label+'</b><code>'+esc(url)+'</code><div class="cu-actions"><button class="cu-btn" data-copy="'+esc(url)+'">کپی</button>'+(label==='Subscription'?'<button class="cu-btn" data-qr="'+esc(url)+'">QR</button>':'')+'</div><div class="cu-qr" hidden></div></div>';
  const renew=(s.renewal_prices||[]).length?'<div class="cu-card"><h2>🔄 تمدید</h2><div class="cu-price-grid">'+s.renewal_prices.map(p=>'<button class="cu-price cu-btn" data-renew-client="'+esc(s.id)+'" data-renew-price="'+esc(p.id)+'"><b>'+esc(p.label)+'</b><small>'+money(p.price_minor)+' · '+p.duration_days+' روز</small></button>').join('')+'</div></div>':'';
  app.innerHTML='<main class="cu-shell"><button class="cu-btn cu-back" id="back">← برگشت</button><div class="cu-card"><h2>'+esc(s.name)+'</h2><small>'+esc(s.id)+'</small><div class="cu-progress"><i style="width:'+pct+'%"></i></div><div class="cu-grid" style="margin-top:10px"><div class="cu-stat"><small>مصرف</small><b>'+bytes(s.used_bytes)+'</b></div><div class="cu-stat"><small>باقی‌مانده</small><b>'+(s.unlimited?'نامحدود':bytes(s.remaining_bytes))+'</b></div><div class="cu-stat"><small>انقضا</small><b style="font-size:10px">'+date(s.expiry_ms)+'</b></div></div>'+links+'</div>'+renew+'</main>';
  document.getElementById('back').onclick=()=>{tab='services';render()};bindUtility();document.querySelectorAll('[data-renew-client]').forEach(b=>b.onclick=()=>renewService(b.dataset.renewClient,b.dataset.renewPrice));
 }catch(e){showError(e)}
}
async function renewService(client,price){
 try{
  const o=await api('/api/telegram-customer/renewals','POST',{client_id:client,price_id:price});
  if(Number(data.wallet?.balance_minor||0)<Number(o.amount_minor)){tg?.showAlert?.('موجودی کیف پول کافی نیست. ابتدا کیف پول را شارژ کن.');tab='wallet';render();return}
  if(!confirm('تمدید با مبلغ '+money(o.amount_minor)+' از کیف پول انجام شود؟'))return;
  await api('/api/telegram-customer/renewals/'+encodeURIComponent(o.id)+'/pay','POST',{});await reload();await openService(client);
 }catch(e){showError(e)}
}
async function openTicket(row){
 try{
  const d=await api('/api/telegram-customer/tickets/'+row),t=d.ticket,msgs=d.messages||[];
  app.innerHTML='<main class="cu-shell"><button class="cu-btn cu-back" id="back">← برگشت</button><div class="cu-card"><h2>'+esc(t.subject)+'</h2><span class="cu-pill">'+esc(t.status)+'</span><div class="cu-msgs">'+msgs.map(m=>'<div class="cu-msg '+esc(m.sender_type)+'"><b>'+(m.sender_type==='customer'?'شما':'پشتیبانی')+'</b><br>'+esc(m.text||('['+(m.file_kind||'file')+']'))+'</div>').join('')+'</div>'+(t.status!=='closed'?'<form id="reply-form" class="cu-form"><label>پاسخ<textarea name="text" rows="4" maxlength="4000" required></textarea></label><button class="cu-btn primary">ارسال پاسخ</button></form><button class="cu-btn bad wide" id="close-ticket" style="margin-top:8px">بستن تیکت</button>':'')+'</div></main>';
  document.getElementById('back').onclick=()=>{tab='support';render()};
  document.getElementById('reply-form')?.addEventListener('submit',async e=>{e.preventDefault();try{await api('/api/telegram-customer/tickets/'+row+'/reply','POST',{text:String(new FormData(e.target).get('text'))});await reload();await openTicket(row)}catch(ex){showError(ex)}});
  document.getElementById('close-ticket')?.addEventListener('click',async()=>{try{await api('/api/telegram-customer/tickets/'+row+'/close','POST',{});await reload();tab='support';render()}catch(ex){showError(ex)}});
 }catch(e){showError(e)}
}
function bindUtility(){document.querySelectorAll('[data-copy]').forEach(b=>b.onclick=async()=>{if(await copy(b.dataset.copy))b.textContent='کپی شد ✓'});document.querySelectorAll('[data-qr]').forEach(b=>b.onclick=()=>{const box=b.closest('.cu-link').querySelector('.cu-qr');box.hidden=!box.hidden;if(!box.hidden&&!box.children.length&&typeof qrcode==='function'){const q=qrcode(0,'L');q.addData(b.dataset.qr);q.make();box.innerHTML=q.createSvgTag()}})}
async function repPlans(){
 const rep=data.representative||{},rows=rep.plans||[];
 app.innerHTML='<main class="cu-shell"><button class="cu-btn cu-back" id="back">← برگشت</button><div class="cu-card"><h2>🏪 '+(rep.subscription?'تمدید نمایندگی':'خرید پنل نمایندگی')+'</h2><small>مشخصات هر پلن ثابت و توسط Owner تعیین شده است.</small></div>'+ (rows.length?rows.map(p=>'<div class="cu-card"><h2>'+esc(p.name)+'</h2><small>'+esc(p.description||'')+'</small><div class="cu-grid" style="margin-top:9px"><div class="cu-stat"><small>قیمت</small><b>'+money(p.price_minor)+'</b></div><div class="cu-stat"><small>مدت</small><b>'+p.duration_days+' روز</b></div><div class="cu-stat"><small>حداکثر Client</small><b>'+(p.max_clients||'∞')+'</b></div><div class="cu-stat"><small>Volume Credit</small><b>'+bytes(p.volume_credit_bytes)+'</b></div></div><button class="cu-btn primary wide" data-rep-plan="'+p.row_id+'" style="margin-top:10px">'+(rep.subscription?'انتخاب برای تمدید':'خرید این پلن')+'</button></div>').join(''):'<div class="cu-card cu-empty">پلنی منتشر نشده است.</div>')+'</main>';
 document.getElementById('back').onclick=()=>{tab='home';render()};document.querySelectorAll('[data-rep-plan]').forEach(b=>b.onclick=()=>repCheckout(Number(b.dataset.repPlan),rep.subscription?'renewal':'purchase'));
}
async function repCheckout(planRow,kind){
 try{
  const o=await api('/api/telegram-customer/representative/orders','POST',{plan_row:planRow,kind});
  if(Number(data.wallet?.balance_minor||0)<Number(o.amount_minor)){tg?.showAlert?.('موجودی کیف پول کافی نیست.');tab='wallet';render();return}
  if(!confirm((kind==='purchase'?'خرید':'تمدید')+' نمایندگی با مبلغ '+money(o.amount_minor)+'؟'))return;
  const r=await api('/api/telegram-customer/representative/orders/'+encodeURIComponent(o.id)+'/pay','POST',{});
  await reload();
  if(r.password){
   app.innerHTML='<main class="cu-shell"><div class="cu-card"><h2>✅ پنل نمایندگی ساخته شد</h2><div class="cu-notice">این رمز فقط برای تحویل اولیه نگه داشته می‌شود. بعد از ذخیره، تأیید کن.</div><p>Username</p><div class="cu-secret">'+esc(r.username)+'</div><p>Password</p><div class="cu-secret">'+esc(r.password)+'</div><div class="cu-actions" style="margin-top:10px"><button class="cu-btn" data-copy="'+esc(r.username)+'">کپی Username</button><button class="cu-btn" data-copy="'+esc(r.password)+'">کپی Password</button><button class="cu-btn primary" id="ack-rep">ذخیره کردم</button></div></div></main>';bindUtility();document.getElementById('ack-rep').onclick=async()=>{await api('/api/telegram-customer/representative/orders/'+encodeURIComponent(o.id)+'/ack','POST',{});await reload();tab='home';render()};
  }else{tg?.showAlert?.('نمایندگی با موفقیت تمدید شد.');tab='home';render()}
 }catch(e){showError(e)}
}
function bind(){
 document.querySelectorAll('[data-tab]').forEach(b=>b.onclick=()=>{tab=b.dataset.tab;render()});bindUtility();
 document.querySelectorAll('[data-order]').forEach(b=>b.onclick=()=>openOrder(b.dataset.order));
 document.querySelectorAll('[data-service]').forEach(b=>b.onclick=()=>openService(decodeURIComponent(b.dataset.service)));
 document.querySelectorAll('[data-buy-product]').forEach(b=>b.onclick=()=>checkout(b.dataset.buyProduct,b.dataset.buyPrice));
 document.querySelectorAll('[data-ticket]').forEach(b=>b.onclick=()=>openTicket(Number(b.dataset.ticket)));
 document.querySelector('[data-rep-open]')?.addEventListener('click',repPlans);
 const top=document.getElementById('topup-form');if(top)top.onsubmit=async e=>{e.preventDefault();try{const r=await api('/api/telegram-customer/topups','POST',{amount_minor:Number(new FormData(top).get('amount'))}),p=r.payment||{};app.innerHTML='<main class="cu-shell"><button class="cu-btn cu-back" id="back">← برگشت</button><div class="cu-card"><h2>💰 شارژ کیف پول</h2><div class="cu-row"><div><small>مبلغ</small><b>'+money(r.topup.amount_minor)+'</b></div></div><p>شماره کارت</p><div class="cu-secret">'+esc(p.card_number||'—')+'</div><p>به نام: '+esc(p.card_holder||'—')+' · '+esc(p.bank_name||'')+'</p><div class="cu-notice">'+esc(p.instructions||'بعد از پرداخت، رسید را از ربات تلگرام ارسال کن.')+'</div><button class="cu-btn primary wide" id="open-bot" style="margin-top:10px">ارسال رسید در ربات</button></div></main>';document.getElementById('back').onclick=async()=>{await reload();tab='wallet';render()};document.getElementById('open-bot').onclick=()=>{if(p.bot_url)tg?.openTelegramLink?tg.openTelegramLink(p.bot_url):location.href=p.bot_url}}catch(ex){showError(ex)}};
 const tf=document.getElementById('ticket-form');if(tf)tf.onsubmit=async e=>{e.preventDefault();try{const fd=new FormData(tf);await api('/api/telegram-customer/tickets','POST',{subject:String(fd.get('subject')),message:String(fd.get('message'))});await reload();tab='support';render()}catch(ex){showError(ex)}};
}
async function boot(){
 try{
  if(!app)throw Error('محل نمایش Mini App پیدا نشد.');
  if(window.__darkTelegramSdkError||!tg)throw Error('اتصال به Telegram Mini App برقرار نشد. ربات را ببند و دوباره از دکمه فروشگاه باز کن.');
  if(!tg.initData)throw Error('فروشگاه باید از داخل دکمه Mini App همین ربات باز شود.');
  if(!owner)throw Error('شناسه فروشگاه مشخص نیست.');
  tg.ready();tg.expand();
  await reload();
 }catch(e){
  const message=e?.message||String(e)||'خطای ناشناخته Mini App';
  if(app)app.innerHTML='<div class="cu-error"><b>⛔ فروشگاه باز نشد</b><br><br>'+esc(message)+'<br><br><button class="cu-btn" onclick="location.reload()">تلاش دوباره</button></div>';
 }
}
window.addEventListener('error',e=>{if(app&&!app.querySelector('.cu-shell'))app.innerHTML='<div class="cu-error"><b>⛔ خطای Mini App</b><br><br>'+esc(e.message||'JavaScript error')+'</div>'});
window.addEventListener('unhandledrejection',e=>{if(app&&!app.querySelector('.cu-shell'))app.innerHTML='<div class="cu-error"><b>⛔ خطای ارتباط فروشگاه</b><br><br>'+esc(e.reason?.message||e.reason||'Request failed')+'</div>'});
boot();
})();