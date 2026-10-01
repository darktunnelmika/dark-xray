/* DARK XRAY Representative Marketplace V2 — guided plans + operations. */
(function(){
'use strict';
if(typeof navItems!=='function'||typeof enginePage!=='function'||typeof runAction!=='function')return;
const baseNavItems=navItems,baseEnginePage=enginePage,baseRunAction=runAction;
const RM={plans:null,subs:null,orders:null,summary:null};
const L=(en,fa)=>((localStorage.getItem('dark_lang')||'en')==='fa'?fa:en);
const esc=v=>e(String(v??''));
const fmt=v=>Number(v||0).toLocaleString()+' تومان';
const isPrimaryOwner=()=>state.me?.role==='owner';

enginePages.repplans=[L('Representative Plans','پلن‌های نمایندگی')];
navItems=function(){
 const n=baseNavItems();
 if(!isPrimaryOwner()||n.some(x=>x[0]==='repplans'))return n;
 const at=Math.max(0,n.findIndex(x=>x[0]==='account'));
 n.splice(at,0,['repplans',L('Representative Plans','پلن‌های نمایندگی'),'users']);
 return n;
};

async function loadPlans(){
 if(!isPrimaryOwner()){RM.plans=[];return RM.plans;}
 RM.plans=await api('/api/representative-marketplace/plans');
 return RM.plans;
}
async function loadOrders(){
 if(!isPrimaryOwner()){RM.orders=[];return RM.orders;}
 RM.orders=await api('/api/representative-marketplace/orders');return RM.orders;
}
async function loadSummary(){
 if(!isPrimaryOwner()){RM.summary={};return RM.summary;}
 RM.summary=await api('/api/representative-marketplace/summary');return RM.summary;
}
async function loadSubs(){
 if(!isPrimaryOwner()){RM.subs=[];return RM.subs;}
 RM.subs=await api('/api/representative-marketplace/subscriptions');
 return RM.subs;
}
function stateTag(p){
 if(p.active&&p.visible)return '<span class="tag green">PUBLISHED</span>';
 if(p.active)return '<span class="tag">HIDDEN</span>';
 return '<span class="tag">OFF</span>';
}
function card(p){
 return `<section class="tg-product">
   <div class="tg-product-head">
    <div><b>${esc(p.name)}</b><small>${esc(p.id)} · ${fmt(p.price_minor)} · ${p.duration_days} ${L('days','روز')}</small></div>
    <div class="row-actions">${stateTag(p)}
     <button class="btn mini" data-act="repplanedit" data-plan="${esc(p.id)}">${L('Edit','ویرایش')}</button>
     <button class="btn mini" data-act="repplandelete" data-plan="${esc(p.id)}">${L('Delete / Archive','حذف / آرشیو')}</button>
    </div>
   </div>
   <div class="tg-price"><small>${L('Volume credit','اعتبار حجمی')}: ${bytes(p.volume_credit_bytes)} · Unlimited: ${p.unlimited_credit} · ${L('Max clients','حداکثر کلاینت')}: ${p.max_clients||'∞'}</small></div>
   <div class="tg-price"><small>IP/HWID: ${p.max_client_ips}/${p.max_client_hwid} · Bot: ${p.bot_allowed?'ON':'OFF'} · Renewal: ${p.renewal_enabled?'ON':'OFF'} · Inbounds: ${esc((p.allowed_inbounds||[]).join(', '))}</small></div>
   <p>${esc(p.description||'')}</p>
  </section>`;
}
async function page(){
 if(!isPrimaryOwner())return heading(L('Representative Plans','پلن‌های نمایندگی'),'')+'<div class="notice error">'+L('Only the primary Owner can manage representative plans.','فقط Owner اصلی می‌تواند پلن‌های نمایندگی را مدیریت کند.')+'</div>';
 const [plans,subs,orders,summary]=await Promise.all([loadPlans(),loadSubs(),loadOrders(),loadSummary()]);
 return heading(L('Representative Marketplace','مارکت نمایندگی'),
   L('You define the complete plan. Customers can only select and pay for published plans.','تمام مشخصات پلن را Owner تعیین می‌کند؛ مشتری فقط پلن منتشرشده را انتخاب و پرداخت می‌کند.'))+
 `<div class="repv2-stats"><div><small>${L('Active','فعال')}</small><b>${summary.active||0}</b></div><div><small>${L('Expiring · 7d','انقضا · ۷ روز')}</small><b>${summary.expiring_7d||0}</b></div><div><small>${L('Suspended','تعلیق')}</small><b>${summary.suspended||0}</b></div><div><small>${L('Pending orders','سفارش در انتظار')}</small><b>${summary.pending_orders||0}</b></div></div><article class="panel tg-card">
   <div class="tg-head"><div><span class="code-caption">OWNER-DEFINED ONLY</span><h2>${L('Representative plans','پلن‌های نمایندگی')}</h2></div>
    <button class="btn btn-primary" data-act="repplannew">${icon('plus')}${L('New plan','پلن جدید')}</button>
   </div>
   <div class="notice">${L('Price, duration, credits, client caps, prefix, IP/HWID caps, Inbounds and bot permission are fixed by the Owner. The buyer cannot customize them.','قیمت، مدت، اعتبارها، سقف کلاینت، Prefix، سقف IP/HWID، Inboundها و مجوز Bot فقط توسط Owner تعیین می‌شوند و خریدار امکان تغییرشان را ندارد.')}</div>
   <div class="tg-list">${plans.length?plans.map(card).join(''):'<div class="tg-empty">'+L('No plans yet.','هنوز پلنی ساخته نشده است.')+'</div>'}</div>
  </article>
  <article class="panel tg-card">
   <div class="tg-head"><div><span class="code-caption">SUBSCRIPTIONS</span><h2>${L('Representative subscriptions','اشتراک‌های نمایندگی')}</h2></div></div>
   <div class="tg-list">${subs.length?subs.map(x=>`<div class="tg-price"><div><b>${esc(x.representative_id)}</b><small>Telegram ${esc(x.buyer_telegram_id)} · ${esc(x.plan_id)}</small></div><small>${x.status==='active'?'ACTIVE':'SUSPENDED'} · ${new Date(Number(x.expires_at||0)*1000).toLocaleString()}</small></div>`).join(''):'<div class="tg-empty">'+L('No representative subscriptions yet.','هنوز اشتراک نمایندگی ساخته نشده است.')+'</div>'}</div>
  </article>
  <article class="panel tg-card"><div class="tg-head"><div><span class="code-caption">ORDERS</span><h2>${L('Order operations','عملیات سفارش‌ها')}</h2></div></div><div class="tg-list">${orders.length?orders.slice(0,50).map(x=>`<div class="tg-price repv2-order"><div><b>${esc(x.kind.toUpperCase())} · ${fmt(x.amount_minor)}</b><small>Telegram ${esc(x.buyer_telegram_id)} · ${esc(x.plan_id)} · ${esc(x.representative_id||'—')}</small></div><div><span class="tag ${['provisioned','renewed'].includes(x.status)?'green':x.status==='failed_refunded'?'red':''}">${esc(x.status)}</span>${x.fulfillment_error?`<small class="repv2-error">${esc(x.fulfillment_error)}</small>`:''}</div></div>`).join(''):'<div class="tg-empty">'+L('No marketplace orders yet.','هنوز سفارشی ثبت نشده است.')+'</div>'}</div></article>`;
}
enginePage=async function(){if(state.page==='repplans')return page();return baseEnginePage();};

function val(fd,name){return String(fd.get(name)||'').trim();}
function checkedInboundIds(){
 return [...document.querySelectorAll('#dialog-form input[name="repInbound"]:checked')].map(x=>Number(x.value));
}
function inboundPicker(selected){
 const set=new Set(selected||[]);
 if(!(state.inbounds||[]).length)return `<div class="notice">${L('Create Inbounds first.','ابتدا Inbound بساز.')}</div>`;
 return `<div class="tg-list">${state.inbounds.map(x=>`<label class="tg-switch"><input type="checkbox" name="repInbound" value="${x.id}" ${set.has(x.id)?'checked':''}><span>${esc(x.id+' · '+(x.remark||x.tag||'Inbound')+' · :'+x.port)}</span></label>`).join('')}</div>`;
}
function repPlanMonth(days){return ({30:1,60:2,90:3,180:6,365:12})[Number(days)]||1;}
function newRepPlanId(){return 'rp_'+crypto.randomUUID().replaceAll('-','').slice(0,12);}
function repPlanReview(draft,newId){
 const names=(state.inbounds||[]).filter(x=>draft.allowed_inbounds.includes(Number(x.id))).map(x=>x.remark||x.tag||('#'+x.id));
 dialog(L('4 · Review & publish','۴ · بررسی و انتشار'),`<div class="repv2-review"><div><small>${L('Plan','پلن')}</small><b>${esc(draft.name)}</b></div><div><small>${L('Price','قیمت')}</small><b>${fmt(draft.price_minor)}</b></div><div><small>${L('Duration','مدت')}</small><b>${draft.duration_days} ${L('days','روز')}</b></div><div><small>${L('Volume credit','اعتبار حجمی')}</small><b>${bytes(draft.volume_credit_bytes)}</b></div><div><small>${L('Unlimited credit','اعتبار نامحدود')}</small><b>${draft.unlimited_credit}</b></div><div><small>${L('Max clients','حداکثر کلاینت')}</small><b>${draft.max_clients||'∞'}</b></div><div class="span-2"><small>${L('Inbounds','اینباندها')}</small><b>${esc(names.join(' · '))}</b></div></div>`,async()=>{await api('/api/representative-marketplace/plans/'+enc(newId),'PUT',draft);closeDialog();RM.plans=null;toast(L('Representative plan published.','پلن نمایندگی منتشر شد.'));await renderPage();},L('Publish','انتشار'));
}
function newPlanWizard(){
 const id=newRepPlanId(),d={name:'',description:'',price_minor:0,currency:'IRT',duration_days:30,volume_credit_bytes:0,unlimited_credit:0,max_clients:0,prefix:'rep_',max_client_ips:0,max_client_hwid:0,allowed_inbounds:[],bot_allowed:true,renewal_enabled:true,active:true,visible:true};
 const shell=(n,title,body)=>`<div class="repv2-wizard"><div class="repv2-progress"><b>${L('Step','مرحله')} ${n}/3</b><span>${esc(title)}</span></div>${body}</div>`;
 const one=()=>dialog(L('New representative plan','ساخت پلن نمایندگی'),shell(1,L('Plan & price','پلن و قیمت'),`<div class="form-grid">${field(L('Name','نام'),'name','','text','required maxlength="128"')}${field(L('Price · Toman','قیمت · تومان'),'price',0,'number','min="0" required')}${select(L('Duration','مدت'),'days',[['30',L('30 days','۳۰ روز')],['60',L('60 days','۶۰ روز')],['90',L('90 days','۹۰ روز')],['180',L('180 days','۱۸۰ روز')],['365',L('365 days','۳۶۵ روز')]])}</div>`),fd=>{d.name=val(fd,'name');d.price_minor=Number(val(fd,'price'));d.duration_days=Number(val(fd,'days'));closeDialog();two();},L('Next','بعدی'));
 const two=()=>dialog(L('New representative plan','ساخت پلن نمایندگی'),shell(2,L('Credits & capacity','اعتبار و ظرفیت'),`<div class="form-grid">${field(L('Volume credit GB','اعتبار حجمی GB'),'volume',0,'number','min="0" step="0.01" required')}${field(L('Unlimited credit','اعتبار نامحدود'),'unlimited',0,'number','min="0" max="1000000" required')}${field(L('Max clients · 0 unlimited','حداکثر کلاینت · صفر نامحدود'),'max_clients',0,'number','min="0" max="1000000" required')}<details class="span-2 tg-plan-advanced"><summary>${L('Advanced limits','محدودیت‌های پیشرفته')}</summary><div class="form-grid">${field(L('Max client IP · 0 unlimited','حداکثر IP هر کلاینت'),'max_ip',0,'number','min="0" max="1000"')}${field(L('Max client HWID · 0 unlimited','حداکثر HWID هر کلاینت'),'max_hwid',0,'number','min="0" max="1000"')}${select(L('Independent bot','ربات مستقل'),'bot',[['true',L('Allowed','مجاز')],['false',L('Disabled','غیرفعال')]],'true')}${select(L('Renewal','تمدید'),'renewal',[['true',L('Allowed','مجاز')],['false',L('Disabled','غیرفعال')]],'true')}</div></details></div>`),fd=>{d.volume_credit_bytes=Math.round(Number(val(fd,'volume'))*gb);d.unlimited_credit=Number(val(fd,'unlimited'));d.max_clients=Number(val(fd,'max_clients'));d.max_client_ips=Number(val(fd,'max_ip')||0);d.max_client_hwid=Number(val(fd,'max_hwid')||0);d.bot_allowed=val(fd,'bot')!=='false';d.renewal_enabled=val(fd,'renewal')!=='false';closeDialog();three();},L('Next','بعدی'));
 const three=()=>dialog(L('New representative plan','ساخت پلن نمایندگی'),shell(3,L('Allowed locations','لوکیشن‌های مجاز'),`<div>${inboundPicker([])}</div>`),()=>{d.allowed_inbounds=checkedInboundIds();if(!d.allowed_inbounds.length)throw Error(L('Select at least one Inbound.','حداقل یک Inbound انتخاب کن.'));closeDialog();repPlanReview(d,id);},L('Review','بررسی'));
 one();
}
async function planDialog(planId=''){
 const plans=RM.plans||await loadPlans(),p=plans.find(x=>x.id===planId)||{},newId=planId||newRepPlanId();
 dialog(planId?L('Edit representative plan','ویرایش پلن نمایندگی'):L('New representative plan','ساخت پلن نمایندگی'),
 `<div class="form-grid">
  ${field(L('Name','نام'),'name',p.name||'','text','required maxlength="128"')}
  ${field(L('Price · Toman','قیمت · تومان'),'price',p.price_minor||0,'number','min="0" step="1" required')}
  ${select(L('Duration','مدت'),'duration_months',[['1',L('1 month','۱ ماه')],['2',L('2 months','۲ ماه')],['3',L('3 months','۳ ماه')],['6',L('6 months','۶ ماه')],['12',L('12 months','۱۲ ماه')]],String(repPlanMonth(p.duration_days||30)))}
  ${field(L('Volume credit GB','اعتبار حجمی GB'),'volume',Number(p.volume_credit_bytes||0)/gb,'number','min="0" step="0.01" required')}
  ${field(L('Unlimited credit','اعتبار نامحدود'),'unlimited',p.unlimited_credit||0,'number','min="0" max="1000000" required')}
  ${field(L('Max clients · 0 unlimited','حداکثر کلاینت · صفر نامحدود'),'max_clients',p.max_clients||0,'number','min="0" max="1000000" required')}
  <div class="span-2"><label>${L('Allowed Inbounds','اینباندهای مجاز')}</label>${inboundPicker(p.allowed_inbounds||[])}</div>
  <details class="span-2 tg-plan-advanced"><summary>${L('Advanced settings','تنظیمات پیشرفته')}</summary><div class="form-grid">
   ${field(L('Prefix base','پیشوند پایه'),'prefix',p.prefix||'rep_','text','maxlength="32" dir="ltr"')}
   ${field(L('Max client IP · 0 unlimited','حداکثر IP هر کلاینت'),'max_ip',p.max_client_ips||0,'number','min="0" max="1000"')}
   ${field(L('Max client HWID · 0 unlimited','حداکثر HWID هر کلاینت'),'max_hwid',p.max_client_hwid||0,'number','min="0" max="1000"')}
   ${select(L('Independent bot','ربات مستقل'),'bot_allowed',[['true',L('Allowed','مجاز')],['false',L('Disabled','غیرفعال')]],String(p.bot_allowed!==false))}
   ${select(L('Renewal','تمدید'),'renewal',[['true',L('Allowed','مجاز')],['false',L('Disabled','غیرفعال')]],String(p.renewal_enabled!==false))}
   ${select(L('Published','انتشار'),'published',[['true',L('Published','منتشر')],['false',L('Draft / hidden','پیش‌نویس / مخفی')]],String(p.active!==false&&p.visible!==false))}
   <label class="span-2">${L('Description','توضیحات')}<textarea class="field-input" name="description" rows="3" maxlength="2000">${esc(p.description||'')}</textarea></label>
  </div></details>
 </div>`,async fd=>{
   const ids=checkedInboundIds();if(!ids.length)throw Error(L('Select at least one Inbound.','حداقل یک Inbound انتخاب کن.'));
   const months=Number(val(fd,'duration_months')),duration_days=({1:30,2:60,3:90,6:180,12:365})[months]||30;
   const published=val(fd,'published')==='true';
   await api('/api/representative-marketplace/plans/'+enc(newId),'PUT',{
    name:val(fd,'name'),description:val(fd,'description'),price_minor:Number(val(fd,'price')),currency:'IRT',
    duration_days,volume_credit_bytes:Math.round(Number(val(fd,'volume'))*gb),
    unlimited_credit:Number(val(fd,'unlimited')),max_clients:Number(val(fd,'max_clients')),prefix:val(fd,'prefix')||'rep_',
    max_client_ips:Number(val(fd,'max_ip')),max_client_hwid:Number(val(fd,'max_hwid')),allowed_inbounds:ids,
    bot_allowed:val(fd,'bot_allowed')==='true',renewal_enabled:val(fd,'renewal')==='true',
    active:published,visible:published
   });
   closeDialog();RM.plans=null;toast(L('Representative plan saved.','پلن نمایندگی ذخیره شد.'));await renderPage();
 });
}

runAction=async function(act,el){
 if(act==='repplannew'){newPlanWizard();return;}
 if(act==='repplanedit'){await planDialog(el.dataset.plan);return;}
 if(act==='repplandelete'){
  if(!confirm(L('Delete/archive this plan? Existing purchase history will be preserved.','این پلن حذف/آرشیو شود؟ تاریخچه خرید حفظ می‌شود.')))return;
  await api('/api/representative-marketplace/plans/'+enc(el.dataset.plan),'DELETE');
  RM.plans=null;toast(L('Plan removed from sale.','پلن از فروش خارج شد.'));await renderPage();return;
 }
 return baseRunAction(act,el);
};
})();