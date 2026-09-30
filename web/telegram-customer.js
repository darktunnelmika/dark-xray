(()=>{'use strict';
const tg=window.Telegram?.WebApp,app=document.getElementById('app'),sheet=document.getElementById('sheet'),sheetBody=document.getElementById('sheetBody');
const qs=new URLSearchParams(location.search),owner=qs.get('owner')||'',marker='/assets/telegram-customer.html',idx=location.pathname.lastIndexOf(marker),BASE=idx>=0?location.pathname.slice(0,idx):'';
let data=null,tab='home';
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const money=(v,c='IRT')=>Number(v||0).toLocaleString('fa-IR')+(String(c).toUpperCase()==='IRT'?' تومان':' '+String(c).toUpperCase());
const bytes=v=>{if(v==null)return'نامحدود';let n=Number(v||0),u=['B','KB','MB','GB','TB'],i=0;while(n>=1024&&i<u.length-1){n/=1024;i++}return(n>=10||i===0?Math.round(n):n.toFixed(1))+' '+u[i]};
const date=v=>!v?'—':new Date(Number(v)>1e12?Number(v):Number(v)*1000).toLocaleString('fa-IR');
function toast(msg){const x=document.createElement('div');x.className='cu-toast';x.textContent=msg;document.body.append(x);setTimeout(()=>x.remove(),2400)}
async function api(path,method='GET',body){
 const join=path.includes('?')?'&':'?';
 const r=await fetch(BASE+path+join+'owner='+encodeURIComponent(owner),{method,headers:{'Content-Type':'application/json','X-Telegram-Init-Data':tg?.initData||''},body:body===undefined?undefined:JSON.stringify(body)});
 const t=await r.text();let d={};try{d=t?JSON.parse(t):{}}catch{d={detail:t}}if(!r.ok)throw Error(d.detail||('HTTP '+r.status));return d;
}
function nav(){return '<nav class="cu-nav">'+[['home','🏠','خانه'],['shop','🛍','خرید'],['services','📦','سرویس‌ها'],['wallet','💰','کیف پول'],['support','🎫','پشتیبانی']].map(x=>'<button data-tab="'+x[0]+'" class="'+(tab===x[0]?'active':'')+'"><span>'+x[1]+'</span>'+x[2]+'</button>').join('')+'</nav>'}
function stat(label,value,sub=''){return '<div class="cu-card cu-stat"><small>'+esc(label)+'</small><b>'+esc(value)+'</b>'+(sub?'<span class="cu-muted">'+esc(sub)+'</span>':'')+'</div>'}
function statusClass(s){return['active','provisioned','renewed','provisioned_waiting_activation','answered'].includes(s)?'active':['rejected','payment_rejected','cancelled','blocked','expired'].includes(s)?'bad':''}
function openSheet(html){sheetBody.innerHTML='<div class="cu-sheet-handle"></div>'+html;sheet.hidden=false;bindSheet()}
function closeSheet(){sheet.hidden=true;sheetBody.innerHTML=''}
function home(){
 const w=data.wallet.wallet,services=data.services||[],orders=data.orders||[],ref=data.referral||{},rep=data.representative||{};
 const active=services.filter(x=>x.status==='active'||x.status==='waiting_activation').length;
 let repHtml='';
 if(rep.available){
   if(rep.subscription){repHtml='<div class="cu-card"><div class="cu-section-title"><h2>🏪 نمایندگی من</h2><span class="cu-status '+statusClass(rep.subscription.status)+'">'+esc(rep.subscription.status)+'</span></div><div class="cu-row"><div><b>'+esc(rep.subscription.representative_id)+'</b><small>انقضا: '+date(rep.subscription.expires_at)+'</small></div><button class="cu-btn" data-rep-market>تمدید</button></div></div>'}
   else if((rep.plans||[]).length){repHtml='<div class="cu-card"><div class="cu-section-title"><h2>🏪 خرید پنل نمایندگی</h2><button class="cu-btn" data-rep-market>مشاهده پلن‌ها</button></div><span class="cu-muted">پلن‌ها توسط Owner تعریف شده‌اند و مشخصات فنی قابل تغییر نیست.</span></div>'}
 }
 return '<section><div class="cu-card cu-hero"><small>سلام '+esc(data.user.first_name||data.user.username||'')+'</small><br><strong>'+money(w.balance_minor,w.currency)+'</strong><p>موجودی کیف پول DARK</p></div>'+
 '<div class="cu-grid">'+stat('سرویس فعال',active,services.length+' سرویس')+stat('سفارش اخیر',orders.length?orders[0].label:'بدون سفارش',orders.length?money(orders[0].amount_minor,orders[0].currency):'')+stat('زیرمجموعه موفق',ref.qualified||0,money(ref.earned||0))+'</div>'+
 '<div class="cu-card"><div class="cu-section-title"><h2>سفارش‌های اخیر</h2><button class="cu-btn" data-refresh>↻</button></div><div class="cu-list">'+(orders.length?orders.slice(0,5).map(o=>'<div class="cu-row"><div><b>'+esc(o.product_name||o.product_id)+'</b><small>'+esc(o.label)+' · '+money(o.amount_minor,o.currency)+'</small></div><span class="cu-status '+statusClass(o.status)+'">'+esc(o.phase)+'</span></div>').join(''):'<div class="cu-empty">هنوز سفارشی نداری.</div>')+'</div></div>'+
 '<div class="cu-card"><div class="cu-section-title"><h2>👥 زیرمجموعه‌گیری</h2><button class="cu-btn" data-copy="'+esc(ref.invite_url||'')+'">کپی لینک</button></div><div class="cu-row"><div><b>'+Number(ref.invited||0).toLocaleString('fa-IR')+' دعوت</b><small>پاداش هر خرید اول: '+money(ref.reward_minor||0)+'</small></div><button class="cu-btn" data-share>اشتراک</button></div></div>'+repHtml+'</section>';
}
function shop(){
 const rows=data.shop||[];
 return '<section><div class="cu-card"><div class="cu-section-title"><h2>🛍 خرید اشتراک</h2><button class="cu-btn" data-refresh>↻</button></div><span class="cu-muted">پلن را انتخاب کن؛ بعد روش پرداخت را مشخص می‌کنی.</span></div>'+
 (rows.length?rows.map(p=>'<div class="cu-card cu-plan"><div><b>'+esc(p.name)+'</b><small class="cu-muted"> · '+esc(p.category)+'</small></div><p class="cu-muted">'+esc(p.description||'')+'</p>'+p.prices.map(x=>'<div class="cu-price"><div><b>'+esc(x.label)+'</b><small>'+(x.unlimited?'نامحدود':bytes(x.volume_bytes))+' · '+x.duration_days+' روز · IP '+x.ip_limit+'</small></div><button class="cu-btn primary" data-buy-product="'+esc(p.id)+'" data-buy-price="'+esc(x.id)+'" data-buy-name="'+esc(p.name+' · '+x.label)+'" data-buy-amount="'+x.price_minor+'" data-buy-currency="'+esc(x.currency)+'">'+money(x.price_minor,x.currency)+'</button></div>').join('')+'</div>').join(''):'<div class="cu-card cu-empty">فعلاً پلنی برای فروش منتشر نشده است.</div>')+'</section>';
}
function services(){
 const rows=data.services||[];
 return '<section><div class="cu-card"><div class="cu-section-title"><h2>📦 سرویس‌های من</h2><button class="cu-btn" data-refresh>↻</button></div></div><div class="cu-list">'+(rows.length?rows.map(s=>{const pct=s.unlimited?0:Math.min(100,Math.round((Number(s.used_bytes||0)/Math.max(1,Number(s.quota_bytes||0)))*100));return '<button class="cu-card cu-btn cu-service" data-service="'+s.row_id+'"><div class="cu-section-title"><div><b>'+esc(s.product_name)+'</b><small class="cu-muted">'+esc(s.id)+'</small></div><span class="cu-status '+statusClass(s.status)+'">'+esc(s.status)+'</span></div><div class="cu-row"><div><small>مصرف</small><b>'+bytes(s.used_bytes)+' / '+(s.unlimited?'∞':bytes(s.quota_bytes))+'</b></div><div><small>انقضا</small><b>'+date(s.expiry_time)+'</b></div></div>'+(s.unlimited?'':'<div class="cu-progress"><i style="width:'+pct+'%"></i></div>')+'</button>'}).join(''):'<div class="cu-card cu-empty">هنوز سرویسی به این Telegram ID متصل نیست.</div>')+'</div></section>';
}
function wallet(){
 const w=data.wallet.wallet,ledger=data.wallet.ledger||[],tops=data.wallet.topups||[];
 return '<section><div class="cu-card cu-hero"><small>موجودی کیف پول</small><br><strong>'+money(w.balance_minor,w.currency)+'</strong></div>'+
 '<div class="cu-card"><h2>➕ شارژ کیف پول</h2><div class="cu-actions"><button class="cu-btn" data-topup="100000">100 هزار</button><button class="cu-btn" data-topup="200000">200 هزار</button><button class="cu-btn" data-topup="500000">500 هزار</button><button class="cu-btn" data-topup-custom>مبلغ دلخواه</button></div></div>'+
 '<div class="cu-card"><h2>تراکنش‌ها</h2><div class="cu-list">'+(ledger.length?ledger.slice(0,25).map(x=>'<div class="cu-row"><div><b>'+(Number(x.delta_minor)>=0?'+':'')+money(x.delta_minor,x.currency)+'</b><small>'+esc(x.kind)+' · '+date(x.created_at)+'</small></div></div>').join(''):'<div class="cu-empty">تراکنشی ثبت نشده است.</div>')+'</div></div>'+
 (tops.length?'<div class="cu-card"><h2>شارژهای اخیر</h2>'+tops.slice(0,10).map(x=>'<div class="cu-row"><div><b>'+money(x.amount_minor,x.currency)+'</b><small>'+date(x.created_at)+'</small></div><span class="cu-status '+statusClass(x.status)+'">'+esc(x.status)+'</span></div>').join('')+'</div>':'')+'</section>';
}
function support(){
 const rows=data.support||[];
 return '<section><div class="cu-card"><div class="cu-section-title"><h2>🎫 پشتیبانی</h2><div class="cu-actions"><button class="cu-btn primary" data-new-ticket>تیکت جدید</button><button class="cu-btn" data-refresh>↻</button></div></div><span class="cu-muted">پاسخ جدید از طریق خود ربات Telegram هم بهت اطلاع داده می‌شود.</span></div><div class="cu-list">'+(rows.length?rows.map(t=>'<button class="cu-card cu-btn" data-ticket="'+t.row_id+'"><div class="cu-section-title"><div><b>'+esc(t.subject)+'</b><small class="cu-muted">'+date(t.updated_at)+'</small></div><span class="cu-status '+statusClass(t.status)+'">'+esc(t.status)+'</span></div></button>').join(''):'<div class="cu-card cu-empty">هنوز تیکتی نداری.</div>')+'</div></section>';
}
function render(){
 if(!data)return;
 app.innerHTML='<main class="cu-shell"><header class="cu-head"><div><h1>DARK Customer</h1><small>@'+esc(data.bot_username||'bot')+' · '+esc(data.user.telegram_id)+'</small></div><span class="cu-pill">V5</span></header>'+(tab==='home'?home():tab==='shop'?shop():tab==='services'?services():tab==='wallet'?wallet():support())+'</main>'+nav();bind();
}
async function reload(){data=await api('/api/telegram-customer/bootstrap');render()}
function paymentSheet(meta){
 const methods=data.payment_methods||[],wallet=data.wallet.wallet;
 openSheet('<h2>تأیید خرید</h2><div class="cu-card"><b>'+esc(meta.name)+'</b><small class="cu-muted">مبلغ: '+money(meta.amount,meta.currency)+'</small></div><div class="cu-list">'+methods.map(m=>{let sub=m.kind==='wallet'?'موجودی: '+money(m.balance_minor,m.currency):m.kind==='crypto'?'پرداخت مستقیم کریپتو':'کارت‌به‌کارت و ارسال رسید در ربات';let disabled=m.kind==='wallet'&&Number(wallet.balance_minor)<Number(meta.amount);return '<button class="cu-row cu-btn" '+(disabled?'disabled':'')+' data-pay-method="'+esc(m.id)+'" data-product="'+esc(meta.product)+'" data-price="'+esc(meta.price)+'"><div><b>'+esc(m.label)+'</b><small>'+esc(sub)+'</small></div><span>›</span></button>'}).join('')+'</div>');
}
async function payPurchase(btn){
 btn.disabled=true;
 try{
  const order=await api('/api/telegram-customer/orders','POST',{product_id:btn.dataset.product,price_id:btn.dataset.price});
  const method=btn.dataset.payMethod;
  if(method==='wallet'){
    const r=await api('/api/telegram-customer/orders/'+order.id+'/wallet','POST',{});
    closeSheet();toast(r.activation_pending?'سرویس آماده شد؛ زمان از اولین اتصال شروع می‌شود.':'خرید با موفقیت انجام شد.');await reload();tab='services';render();return;
  }
  const r=await api('/api/telegram-customer/orders/'+order.id+'/gateway','POST',{gateway_id:method});
  if(r.checkout_url){
    openSheet('<h2>🪙 پرداخت آنلاین</h2><div class="cu-card"><b>سفارش '+esc(order.id)+'</b><small class="cu-muted">بعد از پرداخت به Mini App برگرد و وضعیت را تازه کن.</small></div><button class="cu-btn primary" data-open-url="'+esc(r.checkout_url)+'">رفتن به درگاه</button><button class="cu-btn ghost" data-close-sheet>بستن</button>');
  }else{
    openSheet('<h2>💳 کارت‌به‌کارت</h2><div class="cu-card"><div class="cu-row"><div><small>شماره کارت</small><b dir="ltr">'+esc(r.card_number||'—')+'</b></div><button class="cu-btn" data-copy="'+esc(r.card_number||'')+'">کپی</button></div><div class="cu-row"><div><small>به نام</small><b>'+esc(r.card_holder||'—')+'</b></div></div><p class="cu-muted">'+esc(r.instructions||'')+'</p></div><button class="cu-btn primary" data-open-bot>ارسال رسید در ربات</button>');
  }
  await reload();
 }catch(e){toast(e.message)}finally{btn.disabled=false}
}
async function serviceSheet(row){
 try{
  const s=await api('/api/telegram-customer/services/'+row);
  const d=s.delivery||{},links=[['Subscription',d.subscription_url],['Main Config',d.main_config],['Portal',d.portal_url]].filter(x=>x[1]);
  let qrPayload=String(d.subscription_url||d.main_config||d.portal_url||'');
  openSheet('<h2>'+esc(s.product_name)+'</h2><div class="cu-grid">'+stat('باقی‌مانده',s.unlimited?'نامحدود':bytes(s.remaining_bytes))+stat('مصرف',bytes(s.used_bytes))+stat('انقضا',date(s.expiry_time))+'</div>'+
  '<div class="cu-card"><h2>🔗 اتصال</h2>'+links.map(x=>'<div class="cu-row"><div><b>'+x[0]+'</b><div class="cu-link">'+esc(x[1])+'</div></div><button class="cu-btn" data-copy="'+esc(x[1])+'">کپی</button></div>').join('')+(qrPayload?'<button class="cu-btn" data-show-qr="'+esc(qrPayload)+'">نمایش QR</button><div id="serviceQr" class="cu-qr"></div>':'')+'</div>'+
  (s.renewal_prices?.length?'<div class="cu-card"><h2>🔄 تمدید با کیف پول</h2>'+s.renewal_prices.map(p=>'<div class="cu-price"><div><b>'+esc(p.label)+'</b><small>'+p.duration_days+' روز · '+bytes(p.volume_bytes)+'</small></div><button class="cu-btn primary" data-renew-row="'+row+'" data-renew-price="'+esc(p.id)+'">'+money(p.price_minor,p.currency)+'</button></div>').join('')+'</div>':''));
 }catch(e){toast(e.message)}
}
async function renew(btn){
 if(!confirm('تمدید از موجودی کیف پول انجام شود؟'))return;
 btn.disabled=true;try{const o=await api('/api/telegram-customer/services/'+btn.dataset.renewRow+'/renew','POST',{price_id:btn.dataset.renewPrice});await api('/api/telegram-customer/renewals/'+o.id+'/wallet','POST',{});closeSheet();toast('تمدید انجام شد.');await reload();tab='services';render()}catch(e){toast(e.message)}finally{btn.disabled=false}
}
async function topup(amount){
 try{const r=await api('/api/telegram-customer/wallet/topups','POST',{amount_minor:Number(amount)}),p=r.payment;openSheet('<h2>💰 شارژ کیف پول</h2><div class="cu-card"><div class="cu-row"><div><small>مبلغ</small><b>'+money(r.topup.amount_minor,r.topup.currency)+'</b></div></div><div class="cu-row"><div><small>شماره کارت</small><b dir="ltr">'+esc(p.card_number||'—')+'</b></div><button class="cu-btn" data-copy="'+esc(p.card_number||'')+'">کپی</button></div><div class="cu-row"><div><small>به نام</small><b>'+esc(p.card_holder||'—')+'</b></div></div><p class="cu-muted">'+esc(p.instructions||'')+'</p></div><button class="cu-btn primary" data-open-bot>ارسال رسید در ربات</button>');await reload()}catch(e){toast(e.message)}
}
async function ticketSheet(row){
 try{const d=await api('/api/telegram-customer/support/'+row),t=d.ticket||{},msgs=d.messages||[];openSheet('<h2>'+esc(t.subject)+'</h2><div class="cu-actions"><span class="cu-status '+statusClass(t.status)+'">'+esc(t.status)+'</span><span class="cu-pill">اولویت '+esc(t.priority||'normal')+'</span></div><div class="cu-chat">'+msgs.map(m=>'<div class="cu-msg '+esc(m.sender_type)+'">'+esc(m.text||('['+(m.file_kind||'file')+']'))+'</div>').join('')+'</div>'+(t.status!=='closed'?'<form id="ticketReply" class="cu-form"><label>پاسخ<textarea name="text" rows="3" required maxlength="4000"></textarea></label><button class="cu-btn primary">ارسال</button><button type="button" class="cu-btn bad" data-close-ticket="'+row+'">بستن تیکت</button></form>':''));
 }catch(e){toast(e.message)}
}
function repMarket(){
 const r=data.representative||{},plans=r.plans||[];
 openSheet('<h2>🏪 '+(r.subscription?'تمدید نمایندگی':'خرید پنل نمایندگی')+'</h2>'+(r.subscription?'<div class="cu-card"><b>'+esc(r.subscription.representative_id)+'</b><small class="cu-muted">انقضا: '+date(r.subscription.expires_at)+'</small></div>':'')+'<div class="cu-list">'+(plans.length?plans.map(p=>'<div class="cu-card"><b>'+esc(p.name)+'</b><p class="cu-muted">'+esc(p.description||'')+'</p><div class="cu-grid">'+stat('مدت',p.duration_days+' روز')+stat('اعتبار حجمی',bytes(p.volume_credit_bytes))+stat('نامحدود',p.unlimited_credit)+stat('Max Clients',p.max_clients||'∞')+'</div><button class="cu-btn primary" data-rep-buy="'+p.row_id+'" data-rep-kind="'+(r.subscription?'renewal':'purchase')+'">'+money(p.price_minor,p.currency)+'</button></div>').join(''):'<div class="cu-empty">پلن قابل خریدی وجود ندارد.</div>')+'</div>');
}
async function repBuy(btn){
 if(!confirm('پرداخت از کیف پول و ادامه؟'))return;btn.disabled=true;
 try{const o=await api('/api/telegram-customer/representative/orders','POST',{plan_row:Number(btn.dataset.repBuy),kind:btn.dataset.repKind});const r=await api('/api/telegram-customer/representative/orders/'+o.id+'/wallet','POST',{});if(r.password){openSheet('<h2>✅ پنل نمایندگی ساخته شد</h2><div class="cu-secret"><small>Panel</small><code>'+esc(r.panel_url||'')+'</code><small>Username</small><code>'+esc(r.username)+'</code><small>Password</small><code>'+esc(r.password)+'</code></div><p class="cu-muted">این رمز فقط تا زمانی که تأیید کنی ذخیره شده است.</p><button class="cu-btn primary" data-rep-saved="'+esc(o.id)+'">اطلاعات را ذخیره کردم</button>')}else{closeSheet();toast('نمایندگی تمدید شد.');await reload()}}catch(e){toast(e.message)}finally{btn.disabled=false}
}
function bind(){
 document.querySelectorAll('[data-tab]').forEach(b=>b.onclick=()=>{tab=b.dataset.tab;render()});
 document.querySelectorAll('[data-refresh]').forEach(b=>b.onclick=async()=>{await reload();toast('بروزرسانی شد')});
 document.querySelectorAll('[data-buy-product]').forEach(b=>b.onclick=()=>paymentSheet({product:b.dataset.buyProduct,price:b.dataset.buyPrice,name:b.dataset.buyName,amount:Number(b.dataset.buyAmount),currency:b.dataset.buyCurrency}));
 document.querySelectorAll('[data-service]').forEach(b=>b.onclick=()=>serviceSheet(b.dataset.service));
 document.querySelectorAll('[data-topup]').forEach(b=>b.onclick=()=>topup(Number(b.dataset.topup)));
 document.querySelectorAll('[data-topup-custom]').forEach(b=>b.onclick=()=>openSheet('<h2>مبلغ دلخواه</h2><form id="customTopup" class="cu-form"><label>تومان<input name="amount" type="number" min="1000" required></label><button class="cu-btn primary">ادامه</button></form>'));
 document.querySelectorAll('[data-ticket]').forEach(b=>b.onclick=()=>ticketSheet(b.dataset.ticket));
 document.querySelectorAll('[data-new-ticket]').forEach(b=>b.onclick=()=>openSheet('<h2>تیکت جدید</h2><form id="newTicket" class="cu-form"><label>موضوع<input name="subject" maxlength="128" required></label><label>پیام<textarea name="message" rows="5" maxlength="4000" required></textarea></label><button class="cu-btn primary">ارسال</button></form>'));
 document.querySelectorAll('[data-rep-market]').forEach(b=>b.onclick=repMarket);
 document.querySelectorAll('[data-copy]').forEach(b=>b.onclick=async()=>{const v=b.dataset.copy;if(!v)return;try{await navigator.clipboard.writeText(v);toast('کپی شد')}catch{toast('کپی نشد')}});
 document.querySelectorAll('[data-share]').forEach(b=>b.onclick=()=>{const u=data.referral?.invite_url||'';if(u)tg?.openTelegramLink?.('https://t.me/share/url?url='+encodeURIComponent(u))});
}
function bindSheet(){
 sheet.querySelectorAll('[data-close-sheet]').forEach(b=>b.onclick=closeSheet);
 sheet.querySelectorAll('[data-pay-method]').forEach(b=>b.onclick=()=>payPurchase(b));
 sheet.querySelectorAll('[data-open-url]').forEach(b=>b.onclick=()=>tg?.openLink?.(b.dataset.openUrl,{try_instant_view:false}));
 sheet.querySelectorAll('[data-open-bot]').forEach(b=>b.onclick=()=>{if(data.bot_username)tg?.openTelegramLink?.('https://t.me/'+data.bot_username);else tg?.close?.()});
 sheet.querySelectorAll('[data-copy]').forEach(b=>b.onclick=async()=>{try{await navigator.clipboard.writeText(b.dataset.copy||'');toast('کپی شد')}catch{toast('کپی نشد')}});
 sheet.querySelectorAll('[data-show-qr]').forEach(b=>b.onclick=()=>{const box=document.getElementById('serviceQr');if(!box||typeof qrcode!=='function')return;try{const q=qrcode(0,'L');q.addData(b.dataset.showQr);q.make();box.innerHTML=q.createSvgTag()}catch{toast('QR در دسترس نیست')}});
 sheet.querySelectorAll('[data-renew-row]').forEach(b=>b.onclick=()=>renew(b));
 sheet.querySelectorAll('[data-close-ticket]').forEach(b=>b.onclick=async()=>{await api('/api/telegram-customer/support/'+b.dataset.closeTicket+'/close','POST',{});closeSheet();await reload();toast('تیکت بسته شد')});
 sheet.querySelectorAll('[data-rep-buy]').forEach(b=>b.onclick=()=>repBuy(b));
 sheet.querySelectorAll('[data-rep-saved]').forEach(b=>b.onclick=async()=>{await api('/api/telegram-customer/representative/orders/'+b.dataset.repSaved+'/credentials-saved','POST',{});closeSheet();await reload();toast('ثبت شد')});
 const ct=document.getElementById('customTopup');if(ct)ct.onsubmit=e=>{e.preventDefault();topup(Number(new FormData(ct).get('amount')))};
 const nt=document.getElementById('newTicket');if(nt)nt.onsubmit=async e=>{e.preventDefault();const fd=new FormData(nt);try{await api('/api/telegram-customer/support','POST',{subject:String(fd.get('subject')),message:String(fd.get('message'))});closeSheet();await reload();tab='support';render();toast('تیکت ارسال شد')}catch(x){toast(x.message)}};
 const tr=document.getElementById('ticketReply');if(tr)tr.onsubmit=async e=>{e.preventDefault();const row=sheet.querySelector('[data-close-ticket]')?.dataset.closeTicket||'';try{await api('/api/telegram-customer/support/'+row+'/reply','POST',{text:String(new FormData(tr).get('text'))});await reload();await ticketSheet(row)}catch(x){toast(x.message)}};
}
async function boot(){
 try{
  if(!tg||!tg.initData)throw Error('این صفحه باید از داخل ربات Telegram باز شود.');
  if(!owner)throw Error('Bot scope مشخص نیست.');
  tg.ready();tg.expand();await reload();
 }catch(e){app.innerHTML='<div class="cu-error">⛔ '+esc(e.message)+'</div>'}
}
boot();
})();