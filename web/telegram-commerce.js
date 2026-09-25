/* DARK XRAY Telegram Commerce — scoped bot + storefront control. */
(function(){
'use strict';
if(typeof navItems!=='function'||typeof enginePage!=='function')return;
const baseNavItems=navItems,baseEnginePage=enginePage,baseRunAction=runAction;
const TC={data:null};
const L=(en,fa)=>((localStorage.getItem('dark_lang')||'en')==='fa'?fa:en);
const esc=v=>e(String(v??''));
const fmt=(v,c)=>{const code=String(c||'').toUpperCase();const n=Number(v||0).toLocaleString();return code==='IRT'?n+' تومان':code==='IRR'?n+' ریال':n+' '+code;};
const st=(s,ok)=>`<span class="tg-state ${ok?'ok':'warn'}">${esc(s)}</span>`;

enginePages.telegram=[L('Telegram Bot','ربات تلگرام')];
navItems=function(){
 const n=baseNavItems();if(n.some(x=>x[0]==='telegram'))return n;
 const at=Math.max(0,n.findIndex(x=>x[0]==='account'));
 n.splice(at,0,['telegram',L('Telegram Bot','ربات تلگرام'),'link']);return n;
};

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
 </form>
 <div class="tg-meta"><span>@${esc(b.bot_username||'—')}</span><span>${L('Last contact','آخرین تماس')}: ${b.last_seen?date(b.last_seen):'—'}</span></div>
 ${b.last_error?`<div class="notice error">${esc(b.last_error)}</div>`:''}
 </article>`;
}
function priceLine(p){
 const volume=Number(p.volume_bytes||0)>0?bytes(p.volume_bytes):L('Unlimited','نامحدود');
 return `<div class="tg-price"><div><b>${esc(p.label)}</b><small>${volume} · ${p.duration_days} ${L('days','روز')} · ${fmt(p.price_minor,p.currency)}</small></div>${p.active?st(L('ACTIVE','فعال'),true):st(L('OFF','خاموش'),false)}</div>`;
}
function productsCard(products){
 return `<article class="panel tg-card"><div class="tg-head"><div><span class="code-caption">STORE CATALOG</span><h2>${L('Products & pricing','محصولات و قیمت‌گذاری')}</h2></div><button class="btn btn-primary" data-act="tgproductnew">${icon('plus')}${L('Product','محصول')}</button></div>
 <div class="tg-list">${Array.isArray(products)&&products.length?products.map(p=>`<section class="tg-product"><div class="tg-product-head"><div><b>${esc(p.name)}</b><small>${esc(p.kind)} · ${esc(p.id)}</small></div><button class="btn mini" data-act="tgpricenew" data-product="${esc(p.id)}">${icon('plus')}${L('Price','قیمت')}</button></div><p>${esc(p.description||'')}</p><div>${p.prices?.length?p.prices.map(priceLine).join(''):`<div class="tg-empty">${L('No price variants yet.','هنوز قیمت تعریف نشده است.')}</div>`}</div></section>`).join(''):`<div class="tg-empty">${L('No products yet.','هنوز محصولی ساخته نشده است.')}</div>`}</div></article>`;
}
function gatewaysCard(rows){
 return `<article class="panel tg-card"><div class="tg-head"><div><span class="code-caption">PAYMENT ROUTES</span><h2>${L('Payment gateways','درگاه‌های پرداخت')}</h2></div><button class="btn" data-act="tggatewaynew">${icon('plus')}${L('Add','افزودن')}</button></div>
 <div class="tg-list">${Array.isArray(rows)&&rows.length?rows.map(g=>`<div class="tg-gateway"><div><b>${esc(g.label)}</b><small>${esc(g.kind)}${g.plugin?' · '+esc(g.plugin):''}</small></div>${st(g.enabled?L('ACTIVE','فعال'):L('OFF','خاموش'),g.enabled)}</div>`).join(''):`<div class="tg-empty">${L('No payment method configured.','روش پرداختی تعریف نشده است.')}</div>`}</div></article>`;
}
function ordersCard(rows){
 const items=Array.isArray(rows)?rows.slice(0,60):[];
 return `<article class="panel tg-card tg-orders"><div class="tg-head"><div><span class="code-caption">ORDER PIPELINE</span><h2>${L('Recent orders','سفارش‌های اخیر')}</h2></div><button class="btn" data-act="tgrefresh">${icon('refresh')}${L('Refresh','بروزرسانی')}</button></div>
 ${items.length?`<div class="table-wrap"><table class="data-table"><thead><tr><th>${L('Order','سفارش')}</th><th>${L('Buyer','خریدار')}</th><th>${L('Amount','مبلغ')}</th><th>${L('Status','وضعیت')}</th><th>${L('Service','سرویس')}</th><th>${L('Action','عملیات')}</th></tr></thead><tbody>${items.map(o=>`<tr><td class="mono">${esc(o.id)}</td><td class="mono">${esc(o.buyer_telegram_id)}</td><td>${fmt(o.amount_minor,o.currency)}</td><td>${st(o.status,o.status==='provisioned')}</td><td class="mono">${esc(o.client_id||'—')}${o.fulfillment_error?`<small class="tg-error">${esc(o.fulfillment_error)}</small>`:''}</td><td><div class="row-actions">${['awaiting_payment','payment_review'].includes(o.status)?`<button class="btn mini" data-act="tgconfirm" data-order="${esc(o.id)}">${L('Confirm payment','تأیید پرداخت')}</button>`:''}${o.status==='paid'&&!o.client_id?`<button class="btn mini" data-act="tgprovision" data-order="${esc(o.id)}">${L('Provision','ساخت سرویس')}</button>`:''}</div></td></tr>`).join('')}</tbody></table></div>`:`<div class="tg-empty">${L('No orders yet.','هنوز سفارشی وجود ندارد.')}</div>`}</article>`;
}
async function page(){
 const d=await load();
 if(d.bot?.error)return heading(L('Telegram Bot','ربات تلگرام'),L('Bot management and storefront for this panel scope.','مدیریت ربات و فروشگاه همین پنل.'))+`<div class="notice error">${esc(d.bot.error)}</div>`;
 return heading(L('Telegram Bot & Store','ربات تلگرام و فروشگاه'),L('One bot for sales and administration. Admin access is unlocked only for the configured numeric Telegram ID.','یک ربات برای فروش و مدیریت؛ بخش ادمین فقط برای آیدی عددی ثبت‌شده باز می‌شود.'))+
 `<div class="tg-grid">${botCard(d.bot)}${gatewaysCard(d.gateways)}</div>${productsCard(d.products)}${ordersCard(d.orders)}`;
}
enginePage=async function(){if(state.page==='telegram')return page();return baseEnginePage();};

function v(f,n){return String(f.get(n)||'').trim();}
async function productDialog(){
 dialog(L('New product','محصول جدید'),`<div class="form-grid">${field('ID','id','','text','required maxlength="64" dir="ltr"')}${field(L('Name','نام'),'name','','text','required maxlength="128"')}${select(L('Type','نوع'),'kind',[['volume','Volume'],['unlimited','Unlimited'],['multi_location','Multi-location'],['gaming','Gaming']],'volume')}<label>${L('Description','توضیحات')}<textarea class="field-input" name="description" rows="3" maxlength="2000"></textarea></label></div>`,async f=>{
  await api('/api/commerce/products','PUT',{id:v(f,'id'),name:v(f,'name'),description:v(f,'description'),kind:v(f,'kind'),active:true,visible:true});closeDialog();toast(L('Product saved.','محصول ذخیره شد.'));await renderPage();
 });
}
async function priceDialog(product){
 const inboundHint=(state.inbounds||[]).map(x=>`${x.id}:${x.remark||x.tag}`).join(' · ');
 dialog(L('New price variant','قیمت جدید'),`<div class="form-grid">${field('ID','id','','text','required maxlength="64" dir="ltr"')}${field(L('Label','عنوان'),'label','','text','required')}${field(L('Price','قیمت'),'price','0','number','min="0" step="1" required')}${select(L('Currency','واحد'),'currency',[['IRT','Toman / تومان'],['IRR','Rial / ریال'],['USD','USD']],'IRT')}${field(L('Duration days','مدت روز'),'duration','30','number','min="1" max="3650" required')}${field(L('Volume GB · 0 for Unlimited','حجم GB · صفر برای نامحدود'),'volume','30','number','min="0" step="0.01" required')}${field(L('Unlimited units','اعتبار نامحدود'),'unlimited','0','number','min="0" step="1" required')}${field(L('Device / IP limit','حد دستگاه / IP'),'devices','1','number','min="0" max="1000" required')}${field(L('Inbound IDs, comma separated','شناسه اینباندها با کاما'),'inbounds','','text','required dir="ltr" placeholder="1,2"')}<div class="span-2 notice">${esc(inboundHint||L('Create an inbound first.','ابتدا اینباند بساز.'))}</div></div>`,async f=>{
  const ids=v(f,'inbounds').split(',').map(x=>Number(x.trim())).filter(Number.isInteger);
  const volume=Math.round(Number(v(f,'volume'))*gb),unlimited=Number(v(f,'unlimited'));
  if(!ids.length)throw Error(L('At least one inbound is required.','حداقل یک اینباند لازم است.'));
  if(volume===0&&unlimited===0)throw Error(L('Choose volume or Unlimited.','حجم یا نامحدود را مشخص کن.'));
  await api('/api/commerce/products/'+enc(product)+'/prices','PUT',{id:v(f,'id'),label:v(f,'label'),price_minor:Number(v(f,'price')),currency:v(f,'currency'),duration_days:Number(v(f,'duration')),volume_bytes:volume,unlimited_units:unlimited,device_limit:Number(v(f,'devices')),inbound_ids:ids,active:true});
  closeDialog();toast(L('Price saved.','قیمت ذخیره شد.'));await renderPage();
 });
}
async function gatewayDialog(){
 dialog(L('Payment method','روش پرداخت'),`<div class="form-grid">${field('ID','id','card','text','required maxlength="64" dir="ltr"')}${field(L('Label','عنوان'),'label',L('Card payment','کارت به کارت'),'text','required')}${select(L('Mode','حالت'),'kind',[['manual',L('Manual receipt','رسید دستی')],['plugin',L('Gateway plugin','افزونه درگاه')]],'manual')}${field(L('Plugin name','نام افزونه'),'plugin','','text','dir="ltr"')}<label class="span-2">${L('Payment instructions','راهنمای پرداخت')}<textarea class="field-input" name="instructions" rows="5" maxlength="4000"></textarea></label>${field(L('Gateway secret / API key','کلید درگاه'),'secret','','password','autocomplete="new-password"')}</div>`,async f=>{
  await api('/api/commerce/gateways','PUT',{id:v(f,'id'),label:v(f,'label'),kind:v(f,'kind'),enabled:true,instructions:v(f,'instructions'),plugin:v(f,'plugin'),secret:v(f,'secret')||null});closeDialog();toast(L('Payment method saved.','روش پرداخت ذخیره شد.'));await renderPage();
 });
}
runAction=async function(act,el){
 if(act==='tgbottest'){const r=await api('/api/telegram/test','POST',{});toast(`@${r.username||r.name} ${L('is valid.','معتبر است.')}`);TC.data=null;await renderPage();return;}
 if(act==='tgproductnew'){await productDialog();return;}
 if(act==='tgpricenew'){await priceDialog(el.dataset.product);return;}
 if(act==='tggatewaynew'){await gatewayDialog();return;}
 if(act==='tgrefresh'){TC.data=null;await renderPage();return;}
 if(act==='tgconfirm'){if(!confirm(L('Confirm this payment and create the service?','پرداخت تأیید و سرویس ساخته شود؟')))return;const r=await api('/api/commerce/orders/'+enc(el.dataset.order)+'/confirm-payment','POST',{reference:'panel:'+Date.now()});toast(r.provisioned?L('Payment confirmed and service created.','پرداخت تأیید و سرویس ساخته شد.'):L('Payment confirmed; provisioning needs attention.','پرداخت تأیید شد؛ ساخت سرویس نیاز به بررسی دارد.'),!r.provisioned);await renderPage();return;}
 if(act==='tgprovision'){const r=await api('/api/commerce/orders/'+enc(el.dataset.order)+'/provision','POST',{});toast(L('Service provisioned: ','سرویس ساخته شد: ')+(r.client_id||''));await renderPage();return;}
 return baseRunAction(act,el);
};
document.addEventListener('submit',async ev=>{
 const form=ev.target.closest('[data-tg-form="bot"]');if(!form)return;ev.preventDefault();
 const b=form.querySelector('button[type=submit]');if(b)b.disabled=true;
 try{const fd=new FormData(form),payload={enabled:form.elements.enabled.checked,bot_token:v(fd,'bot_token')||null,admin_telegram_id:Number(v(fd,'admin_telegram_id'))};await api('/api/telegram/settings','PUT',payload);toast(L('Telegram bot settings saved.','تنظیمات ربات ذخیره شد.'));TC.data=null;await renderPage();}catch(ex){toast(ex.message,true);}finally{if(b?.isConnected)b.disabled=false;}
});
})();