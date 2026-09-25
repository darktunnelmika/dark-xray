/* DARK XRAY Representative Marketplace V1 — Owner-defined plans only. */
(function(){
'use strict';
if(typeof navItems!=='function'||typeof enginePage!=='function'||typeof runAction!=='function')return;
const baseNavItems=navItems,baseEnginePage=enginePage,baseRunAction=runAction;
const RM={plans:null};
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
 const plans=await loadPlans();
 return heading(L('Representative Marketplace','مارکت نمایندگی'),
   L('You define the complete plan. Customers can only select and pay for published plans.','تمام مشخصات پلن را Owner تعیین می‌کند؛ مشتری فقط پلن منتشرشده را انتخاب و پرداخت می‌کند.'))+
 `<article class="panel tg-card">
   <div class="tg-head"><div><span class="code-caption">OWNER-DEFINED ONLY</span><h2>${L('Representative plans','پلن‌های نمایندگی')}</h2></div>
    <button class="btn btn-primary" data-act="repplannew">${icon('plus')}${L('New plan','پلن جدید')}</button>
   </div>
   <div class="notice">${L('Price, duration, credits, client caps, prefix, IP/HWID caps, Inbounds and bot permission are fixed by the Owner. The buyer cannot customize them.','قیمت، مدت، اعتبارها، سقف کلاینت، Prefix، سقف IP/HWID، Inboundها و مجوز Bot فقط توسط Owner تعیین می‌شوند و خریدار امکان تغییرشان را ندارد.')}</div>
   <div class="tg-list">${plans.length?plans.map(card).join(''):'<div class="tg-empty">'+L('No plans yet.','هنوز پلنی ساخته نشده است.')+'</div>'}</div>
  </article>`;
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
async function planDialog(planId=''){
 const plans=RM.plans||await loadPlans(),p=plans.find(x=>x.id===planId)||{};
 const readonly=planId?'readonly':'required maxlength="64" dir="ltr"';
 dialog(planId?L('Edit representative plan','ویرایش پلن نمایندگی'):L('New representative plan','پلن نمایندگی جدید'),
 `<div class="form-grid">
  ${field('ID','id',p.id||'','text',readonly)}
  ${field(L('Name','نام'),'name',p.name||'','text','required maxlength="128"')}
  ${field(L('Price · Toman','قیمت · تومان'),'price',p.price_minor||0,'number','min="0" step="1" required')}
  ${field(L('Duration days','مدت روز'),'duration',p.duration_days||30,'number','min="1" max="3650" required')}
  ${field(L('Volume credit GB','اعتبار حجمی GB'),'volume',Number(p.volume_credit_bytes||0)/gb,'number','min="0" step="0.01" required')}
  ${field(L('Unlimited credit','اعتبار نامحدود'),'unlimited',p.unlimited_credit||0,'number','min="0" max="1000000" required')}
  ${field(L('Max clients · 0 unlimited','حداکثر کلاینت · صفر نامحدود'),'max_clients',p.max_clients||0,'number','min="0" max="1000000" required')}
  ${field(L('Prefix base','پیشوند پایه'),'prefix',p.prefix||'rep_','text','maxlength="32" dir="ltr"')}
  ${field(L('Max client IP · 0 unlimited','حداکثر IP هر کلاینت'),'max_ip',p.max_client_ips||0,'number','min="0" max="1000"')}
  ${field(L('Max client HWID · 0 unlimited','حداکثر HWID هر کلاینت'),'max_hwid',p.max_client_hwid||0,'number','min="0" max="1000"')}
  ${select(L('Independent bot','ربات مستقل'),'bot_allowed',[['true',L('Allowed','مجاز')],['false',L('Disabled','غیرفعال')]],String(p.bot_allowed!==false))}
  ${select(L('Renewal','تمدید'),'renewal',[['true',L('Allowed','مجاز')],['false',L('Disabled','غیرفعال')]],String(p.renewal_enabled!==false))}
  ${select(L('Active','فعال'),'active',[['true',L('Yes','بله')],['false',L('No','خیر')]],String(p.active!==false))}
  ${select(L('Visible to customers','نمایش به مشتری'),'visible',[['true',L('Yes','بله')],['false',L('No','خیر')]],String(p.visible!==false))}
  <label class="span-2">${L('Description','توضیحات')}<textarea class="field-input" name="description" rows="3" maxlength="2000">${esc(p.description||'')}</textarea></label>
  <div class="span-2"><label>${L('Allowed Inbounds','اینباندهای مجاز')}</label>${inboundPicker(p.allowed_inbounds||[])}</div>
 </div>`,async fd=>{
   const ids=checkedInboundIds();
   if(!ids.length)throw Error(L('Select at least one Inbound.','حداقل یک Inbound انتخاب کن.'));
   const id=val(fd,'id');
   await api('/api/representative-marketplace/plans/'+enc(id),'PUT',{
    name:val(fd,'name'),description:val(fd,'description'),price_minor:Number(val(fd,'price')),currency:'IRT',
    duration_days:Number(val(fd,'duration')),volume_credit_bytes:Math.round(Number(val(fd,'volume'))*gb),
    unlimited_credit:Number(val(fd,'unlimited')),max_clients:Number(val(fd,'max_clients')),prefix:val(fd,'prefix'),
    max_client_ips:Number(val(fd,'max_ip')),max_client_hwid:Number(val(fd,'max_hwid')),allowed_inbounds:ids,
    bot_allowed:val(fd,'bot_allowed')==='true',renewal_enabled:val(fd,'renewal')==='true',
    active:val(fd,'active')==='true',visible:val(fd,'visible')==='true'
   });
   closeDialog();RM.plans=null;toast(L('Representative plan saved.','پلن نمایندگی ذخیره شد.'));await renderPage();
 });
}
runAction=async function(act,el){
 if(act==='repplannew'){await planDialog();return;}
 if(act==='repplanedit'){await planDialog(el.dataset.plan);return;}
 if(act==='repplandelete'){
  if(!confirm(L('Delete/archive this plan? Existing purchase history will be preserved.','این پلن حذف/آرشیو شود؟ تاریخچه خرید حفظ می‌شود.')))return;
  await api('/api/representative-marketplace/plans/'+enc(el.dataset.plan),'DELETE');
  RM.plans=null;toast(L('Plan removed from sale.','پلن از فروش خارج شد.'));await renderPage();return;
 }
 return baseRunAction(act,el);
};
})();