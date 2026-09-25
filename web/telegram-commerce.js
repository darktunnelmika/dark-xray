/* DARK XRAY Telegram Commerce V2 — Bot, Forum Report Center and Store. */
(function(){
'use strict';
if(typeof navItems!=='function'||typeof enginePage!=='function')return;
const baseNavItems=navItems,baseEnginePage=enginePage,baseRunAction=runAction;
const TC={data:null};
const L=(en,fa)=>((localStorage.getItem('dark_lang')||'en')==='fa'?fa:en);
const esc=v=>e(String(v??''));
const fmt=(v,c)=>{const code=String(c||'').toUpperCase(),n=Number(v||0).toLocaleString();return code==='IRT'?n+' تومان':code==='IRR'?n+' ریال':n+' '+code;};
const st=(s,ok)=>`<span class="tg-state ${ok?'ok':'warn'}">${esc(s)}</span>`;
const yn=(v)=>v?L('Yes','بله'):L('No','خیر');

enginePages.telegram=[L('Telegram Bot','ربات تلگرام')];
navItems=function(){const n=baseNavItems();if(n.some(x=>x[0]==='telegram'))return n;const at=Math.max(0,n.findIndex(x=>x[0]==='account'));n.splice(at,0,['telegram',L('Telegram Bot','ربات تلگرام'),'link']);return n;};

async function load(){
 const pairs=[['bot','/api/telegram/status'],['products','/api/commerce/products'],['gateways','/api/commerce/gateways'],['orders','/api/commerce/orders']];
 const out={};await Promise.all(pairs.map(async([k,u])=>{try{out[k]=await api(u);}catch(ex){out[k]={error:ex.message};}}));TC.data=out;return out;
}
function botCard(b){
 const online=b.runtime_state==='online',configured=!!b.configured;
 return `<article class="panel tg-card tg-bot">
 <div class="tg-head"><div><span class="code-caption">DARK BOT CORE</span><h2>${L('Telegram bot','ربات تلگرام')}</h2></div>${st(online?L('ONLINE','آنلاین'):b.enabled?L('STARTING / ERROR','در حال شروع / خطا'):L('DISABLED','خاموش'),online)}</div>
 <form data-tg-form="bot" class="tg-form-grid">
 <label><span>${L('Bot token','توکن ربات')}</span><input class="field-input" name="bot_token" type="password" autocomplete="new-password" placeholder="${configured?L('Configured — leave blank to keep','ثبت شده — برای حفظ خالی بگذار'):L('Paste BotFather token','توکن BotFather را وارد کن')}"></label>
 <label><span>${L('Numeric Telegram Admin ID','آیدی عددی ادمین تلگرام')}</span><input class="field-input" name="admin_telegram_id" type="number" min="1" step="1" required value="${esc(b.admin_telegram_id||'')}"></label>
 <label class="tg-switch"><input name="enabled" type="checkbox" ${b.enabled?'checked':''}><span>${L('Enable this bot','فعال‌سازی این ربات')}</span></label>
 <div class="tg-actions"><button class="btn btn-primary" type="submit">${icon('check')}${L('Save bot','ذخیره ربات')}</button><button class="btn" type="button" data-act="tgbottest">${icon('activity')}${L('Test token','تست توکن')}</button></div>
 </form><div class="tg-meta"><span>@${esc(b.bot_username||'—')}</span><span>${L('Last contact','آخرین تماس')}: ${b.last_seen?date(b.last_seen):'—'}</span></div>
 ${b.last_error?`<div class="notice error">${esc(b.last_error)}</div>`:''}</article>`;
}
function forumCard(f={}){
 const ok=!!f.configured;
 return `<article class="panel tg-card"><div class="tg-head"><div><span class="code-caption">REPORT CENTER</span><h2>${L('Forum report center','انجمن گزارش')}</h2></div>${st(ok?L('CONNECTED','متصل'):L('SETUP REQUIRED','نیازمند راه‌اندازی'),ok)}</div>
 <div class="tg-list">${ok?`<div class="tg-gateway"><div><b>${esc(f.title||f.chat_id)}</b><small>ID: ${esc(f.chat_id)} · ${(f.topics||[]).length} Topics</small></div><button class="btn mini" data-act="tgforumrepair">${icon('refresh')}${L('Repair','ترمیم')}</button></div>
 <div class="tg-topic-grid">${(f.topics||[]).map(t=>`<span>${esc(t.name)}</span>`).join('')}</div>`:`<div class="notice warning">${L('Start the bot as Admin. DARK will ask you to select a Forum Supergroup and will create the report topics automatically.','ربات را با ادمین Start کن؛ DARK از داخل تلگرام انجمن را می‌خواهد و Topicهای گزارش را خودکار می‌سازد.')}</div>`}</div></article>`;
}
function priceLine(p){
 const volume=Number(p.volume_bytes||0)>0?bytes(p.volume_bytes):L('Unlimited','نامحدود');
 const act=p.activation_mode==='first_connection'?L('First connection','اولین اتصال'):L('Immediate','فوری');
 const deliver={subscription:'SUB',config:'CONFIG',both:'SUB + CONFIG',portal:'PORTAL'}[p.delivery_mode]||p.delivery_mode;
 return `<div class="tg-price"><div><b>${esc(p.label)}</b><small>${volume} · ${p.duration_days} ${L('days','روز')} · IP ${p.ip_limit} · HWID ${p.hwid_limit} · ${esc(act)} · ${esc(deliver)} · ${fmt(p.price_minor,p.currency)}</small></div>${p.active?st(L('ACTIVE','فعال'),true):st(L('OFF','خاموش'),false)}</div>`;
}
function productsCard(products){
 return `<article class="panel tg-card"><div class="tg-head"><div><span class="code-caption">STORE CATALOG V2</span><h2>${L('Products & pricing','محصولات و قیمت‌گذاری')}</h2></div><button class="btn btn-primary" data-act="tgproductnew">${icon('plus')}${L('Product','محصول')}</button></div>
 <div class="tg-list">${Array.isArray(products)&&products.length?products.map(p=>`<section class="tg-product"><div class="tg-product-head"><div><b>${esc(p.name)}</b><small>${esc(p.category)} · ${esc(p.kind)} · ${esc(p.id)} · ${L('Per-user limit','سقف خرید')}: ${p.sale_limit_per_user||'∞'}</small></div><button class="btn mini" data-act="tgpricenew" data-product="${esc(p.id)}">${icon('plus')}${L('Price','قیمت')}</button></div><p>${esc(p.description||'')}</p><div>${p.prices?.length?p.prices.map(priceLine).join(''):`<div class="tg-empty">${L('No price variants yet.','هنوز قیمت تعریف نشده است.')}</div>`}</div></section>`).join(''):`<div class="tg-empty">${L('No products yet.','هنوز محصولی ساخته نشده است.')}</div>`}</div></article>`;
}
function gatewaysCard(rows){
 return `<article class="panel tg-card"><div class="tg-head"><div><span class="code-caption">MANUAL PAYMENT V1</span><h2>${L('Card payment','پرداخت کارت‌به‌کارت')}</h2></div><button class="btn" data-act="tggatewaynew">${icon('plus')}${L('Add card','افزودن کارت')}</button></div>
 <div class="tg-list">${Array.isArray(rows)&&rows.length?rows.filter(g=>g.kind==='manual').map(g=>`<div class="tg-gateway"><div><b>${esc(g.label)}</b><small>${esc(g.card_number||'—')} · ${esc(g.card_holder||'—')} · ${esc(g.bank_name||'—')}</small></div>${st(g.enabled?L('ACTIVE','فعال'):L('OFF','خاموش'),g.enabled)}</div>`).join(''):`<div class="tg-empty">${L('No card configured.','شماره کارتی تعریف نشده است.')}</div>`}</div></article>`;
}
function ordersCard(rows){
 const items=Array.isArray(rows)?rows.slice(0,60):[];
 return `<article class="panel tg-card tg-orders"><div class="tg-head"><div><span class="code-caption">ORDER PIPELINE</span><h2>${L('Recent orders','سفارش‌های اخیر')}</h2></div><button class="btn" data-act="tgrefresh">${icon('refresh')}${L('Refresh','بروزرسانی')}</button></div>
 ${items.length?`<div class="table-wrap"><table class="data-table"><thead><tr><th>${L('Order','سفارش')}</th><th>${L('Buyer','خریدار')}</th><th>${L('Amount','مبلغ')}</th><th>${L('Status','وضعیت')}</th><th>${L('Policy','سیاست')}</th><th>${L('Service','سرویس')}</th><th>${L('Action','عملیات')}</th></tr></thead><tbody>${items.map(o=>`<tr><td class="mono">${esc(o.id)}</td><td class="mono">${esc(o.buyer_telegram_id)}</td><td>${fmt(o.amount_minor,o.currency)}</td><td>${st(o.status,['provisioned','provisioned_waiting_activation'].includes(o.status))}</td><td><small>${esc(o.activation_mode)} · ${esc(o.delivery_mode)} · IP ${o.ip_limit} / HWID ${o.hwid_limit}</small></td><td class="mono">${esc(o.client_id||'—')}${o.fulfillment_error?`<small class="tg-error">${esc(o.fulfillment_error)}</small>`:''}</td><td><div class="row-actions">${['awaiting_payment','payment_review'].includes(o.status)?`<button class="btn mini" data-act="tgconfirm" data-order="${esc(o.id)}">${L('Confirm payment','تأیید پرداخت')}</button>`:''}${o.status==='paid'&&!o.client_id?`<button class="btn mini" data-act="tgprovision" data-order="${esc(o.id)}">${L('Provision','ساخت سرویس')}</button>`:''}</div></td></tr>`).join('')}</tbody></table></div>`:`<div class="tg-empty">${L('No orders yet.','هنوز سفارشی وجود ندارد.')}</div>`}</article>`;
}
async function page(){
 const d=await load();if(d.bot?.error)return heading(L('Telegram Bot','ربات تلگرام'),'')+`<div class="notice error">${esc(d.bot.error)}</div>`;
 return heading(L('Telegram Bot & Store','ربات تلگرام و فروشگاه'),L('Sales, administration, forum reports and product policy are all scoped to this panel.','فروش، مدیریت، گزارش انجمن و سیاست محصول همگی به همین پنل محدود هستند.'))+
 `<div class="tg-grid">${botCard(d.bot)}${forumCard(d.bot.forum)}</div><div class="tg-grid">${gatewaysCard(d.gateways)}<article class="panel tg-card"><div class="tg-head"><div><span class="code-caption">PRODUCT POLICY</span><h2>${L('Delivery rules','قوانین تحویل')}</h2></div></div><div class="tg-list"><div class="notice">${L('Each price can choose Immediate or First Connection activation and deliver Subscription, Main Config, Both, or Portal.','هر قیمت می‌تواند فعال‌سازی فوری یا اولین اتصال و تحویل ساب، کانفیگ اصلی، هر دو یا پورتال داشته باشد.')}</div></div></article></div>${productsCard(d.products)}${ordersCard(d.orders)}`;
}
enginePage=async function(){if(state.page==='telegram')return page();return baseEnginePage();};
function v(f,n){return String(f.get(n)||'').trim();}
async function productDialog(){
 dialog(L('New product','محصول جدید'),`<div class="form-grid">${field('ID','id','','text','required maxlength="64" dir="ltr"')}${field(L('Name','نام'),'name','','text','required maxlength="128"')}${field(L('Category','دسته‌بندی'),'category','General','text','required maxlength="64"')}${select(L('Type','نوع'),'kind',[['volume','Volume'],['unlimited','Unlimited'],['multi_location','Multi-location'],['gaming','Gaming']],'volume')}${field(L('Per-user purchase limit · 0 unlimited','سقف خرید هر کاربر · صفر نامحدود'),'sale_limit','0','number','min="0" max="100000"')}${select(L('Renewal','تمدید'),'renewal',[['true',L('Allowed','مجاز')],['false',L('Disabled','غیرفعال')]],'true')}${select(L('Add volume','افزایش حجم'),'add_volume',[['true',L('Allowed','مجاز')],['false',L('Disabled','غیرفعال')]],'true')}<label class="span-2">${L('Description','توضیحات')}<textarea class="field-input" name="description" rows="3" maxlength="2000"></textarea></label></div>`,async f=>{
  await api('/api/commerce/products','PUT',{id:v(f,'id'),name:v(f,'name'),description:v(f,'description'),category:v(f,'category'),kind:v(f,'kind'),sale_limit_per_user:Number(v(f,'sale_limit')||0),renewal_enabled:v(f,'renewal')==='true',add_volume_enabled:v(f,'add_volume')==='true',active:true,visible:true});closeDialog();toast(L('Product saved.','محصول ذخیره شد.'));await renderPage();
 });
}
async function priceDialog(product){
 const inboundHint=(state.inbounds||[]).map(x=>`${x.id}:${x.remark||x.tag}`).join(' · ');
 dialog(L('New price variant','قیمت جدید'),`<div class="form-grid">${field('ID','id','','text','required maxlength="64" dir="ltr"')}${field(L('Label','عنوان'),'label','','text','required')}${field(L('Price','قیمت'),'price','0','number','min="0" step="1" required')}${select(L('Currency','واحد'),'currency',[['IRT','Toman / تومان'],['IRR','Rial / ریال'],['USD','USD']],'IRT')}${field(L('Duration days','مدت روز'),'duration','30','number','min="1" max="3650" required')}${field(L('Volume GB · 0 for Unlimited','حجم GB · صفر برای نامحدود'),'volume','30','number','min="0" step="0.01" required')}${field(L('Unlimited units','اعتبار نامحدود'),'unlimited','0','number','min="0" step="1" required')}${field(L('IP limit · 0 unlimited','محدودیت IP'),'ip_limit','1','number','min="0" max="1000" required')}${field(L('HWID limit · 0 unlimited','محدودیت HWID'),'hwid_limit','0','number','min="0" max="1000" required')}${select(L('Activation','فعال‌سازی'),'activation_mode',[['immediate',L('Immediately after payment','فوری بعد از پرداخت')],['first_connection',L('Start on first connection','شروع از اولین اتصال')]],'immediate')}${select(L('Customer delivery','تحویل به مشتری'),'delivery_mode',[['subscription','Subscription'],['config',L('Main config','کانفیگ اصلی')],['both','Subscription + Config'],['portal','Cyber Portal']],'subscription')}${field(L('Inbound IDs, comma separated','شناسه اینباندها با کاما'),'inbounds','','text','required dir="ltr" placeholder="1,2"')}${field(L('Primary inbound ID · optional','اینباند اصلی · اختیاری'),'primary_inbound','','number','min="1" step="1"')}${select('QR','show_qr',[['true',L('Show','نمایش')],['false',L('Hide','مخفی')]],'true')}${select(L('Portal link','لینک پورتال'),'show_portal',[['true',L('Show','نمایش')],['false',L('Hide','مخفی')]],'true')}<div class="span-2 notice">${esc(inboundHint||L('Create an inbound first.','ابتدا اینباند بساز.'))}</div></div>`,async f=>{
  const ids=v(f,'inbounds').split(',').map(x=>Number(x.trim())).filter(Number.isInteger),volume=Math.round(Number(v(f,'volume'))*gb),unlimited=Number(v(f,'unlimited')),primary=Number(v(f,'primary_inbound')||0);
  if(!ids.length)throw Error(L('At least one inbound is required.','حداقل یک اینباند لازم است.'));if(volume===0&&unlimited===0)throw Error(L('Choose volume or Unlimited.','حجم یا نامحدود را مشخص کن.'));if(primary&&!ids.includes(primary))throw Error(L('Primary inbound must be selected.','اینباند اصلی باید داخل لیست انتخاب‌شده باشد.'));
  await api('/api/commerce/products/'+enc(product)+'/prices','PUT',{id:v(f,'id'),label:v(f,'label'),price_minor:Number(v(f,'price')),currency:v(f,'currency'),duration_days:Number(v(f,'duration')),volume_bytes:volume,unlimited_units:unlimited,ip_limit:Number(v(f,'ip_limit')),hwid_limit:Number(v(f,'hwid_limit')),device_limit:null,inbound_ids:ids,activation_mode:v(f,'activation_mode'),delivery_mode:v(f,'delivery_mode'),primary_inbound_id:primary||null,show_qr:v(f,'show_qr')==='true',show_portal:v(f,'show_portal')==='true',active:true});
  closeDialog();toast(L('Price saved.','قیمت ذخیره شد.'));await renderPage();
 });
}
async function gatewayDialog(){
 dialog(L('Card payment','کارت به کارت'),`<div class="form-grid">${field('ID','id','card','text','required maxlength="64" dir="ltr"')}${field(L('Label','عنوان'),'label',L('Card payment','کارت به کارت'),'text','required')}${field(L('Card number','شماره کارت'),'card_number','','text','required maxlength="32" dir="ltr"')}${field(L('Card holder','نام صاحب کارت'),'card_holder','','text','maxlength="128"')}${field(L('Bank','بانک'),'bank_name','','text','maxlength="128"')}<label class="span-2">${L('Payment instructions','راهنمای پرداخت')}<textarea class="field-input" name="instructions" rows="4" maxlength="4000"></textarea></label></div>`,async f=>{
  await api('/api/commerce/gateways','PUT',{id:v(f,'id'),label:v(f,'label'),kind:'manual',enabled:true,card_number:v(f,'card_number'),card_holder:v(f,'card_holder'),bank_name:v(f,'bank_name'),instructions:v(f,'instructions'),plugin:'',secret:null});closeDialog();toast(L('Card saved.','کارت ذخیره شد.'));await renderPage();
 });
}
runAction=async function(act,el){
 if(act==='tgbottest'){const r=await api('/api/telegram/test','POST',{});toast(`@${r.username||r.name} ${L('is valid.','معتبر است.')}`);TC.data=null;await renderPage();return;}
 if(act==='tgforumrepair'){await api('/api/telegram/forum/repair','POST',{});toast(L('Forum topics checked and repaired.','Topicهای انجمن بررسی و ترمیم شدند.'));await renderPage();return;}
 if(act==='tgproductnew'){await productDialog();return;}if(act==='tgpricenew'){await priceDialog(el.dataset.product);return;}if(act==='tggatewaynew'){await gatewayDialog();return;}
 if(act==='tgrefresh'){TC.data=null;await renderPage();return;}
 if(act==='tgconfirm'){if(!confirm(L('Confirm this payment and create the service?','پرداخت تأیید و سرویس ساخته شود؟')))return;const r=await api('/api/commerce/orders/'+enc(el.dataset.order)+'/confirm-payment','POST',{reference:'panel:'+Date.now()});toast(r.provisioned?L('Payment confirmed and service created.','پرداخت تأیید و سرویس ساخته شد.'):L('Payment confirmed; provisioning needs attention.','پرداخت تأیید شد؛ ساخت سرویس نیاز به بررسی دارد.'),!r.provisioned);await renderPage();return;}
 if(act==='tgprovision'){const r=await api('/api/commerce/orders/'+enc(el.dataset.order)+'/provision','POST',{});toast(L('Service provisioned: ','سرویس ساخته شد: ')+(r.client_id||''));await renderPage();return;}
 return baseRunAction(act,el);
};
document.addEventListener('submit',async ev=>{const form=ev.target.closest('[data-tg-form="bot"]');if(!form)return;ev.preventDefault();const b=form.querySelector('button[type=submit]');if(b)b.disabled=true;try{const fd=new FormData(form),payload={enabled:form.elements.enabled.checked,bot_token:v(fd,'bot_token')||null,admin_telegram_id:Number(v(fd,'admin_telegram_id'))};await api('/api/telegram/settings','PUT',payload);toast(L('Telegram bot settings saved.','تنظیمات ربات ذخیره شد.'));TC.data=null;await renderPage();}catch(ex){toast(ex.message,true);}finally{if(b?.isConnected)b.disabled=false;}});
})();