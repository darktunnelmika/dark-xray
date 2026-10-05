(()=>{'use strict';
const tg=window.Telegram?.WebApp;
const app=document.getElementById('app');
const qs=new URLSearchParams(location.search),owner=qs.get('owner')||'';
const marker='/assets/telegram-miniapp.html',idx=location.pathname.lastIndexOf(marker),BASE=idx>=0?location.pathname.slice(0,idx):'';
let data=null,tab='dashboard';
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const money=v=>Number(v||0).toLocaleString('fa-IR')+' تومان';
async function api(path,method='GET',body){
 const r=await fetch(BASE+path+(path.includes('?')?'&':'?')+'owner='+encodeURIComponent(owner),{method,headers:{'Content-Type':'application/json','X-Telegram-Init-Data':tg?.initData||''},body:body===undefined?undefined:JSON.stringify(body)});
 const t=await r.text();let d={};try{d=t?JSON.parse(t):{};}catch{d={detail:t}}
 if(!r.ok)throw Error(d.detail||('HTTP '+r.status));return d;
}
function nav(){return '<nav class="ma-nav">'+[['dashboard','داشبورد'],['plans','پلن‌ها'],['payments','پرداخت'],['support','پشتیبانی']].map(x=>'<button data-tab="'+x[0]+'" class="'+(tab===x[0]?'active':'')+'">'+x[1]+'</button>').join('')+'</nav>'}
function stat(label,value,sub=''){return '<div class="ma-card ma-stat"><small>'+esc(label)+'</small><b>'+esc(value)+'</b>'+(sub?'<span class="ma-muted">'+esc(sub)+'</span>':'')+'</div>'}
function dashboard(){
 const d=data.dashboard||{},top=d.top_products||[];
 return '<section class="ma-section"><div class="ma-grid">'+
  stat('فروش امروز',money(d.revenue_today),(d.paid_today||0)+' / '+(d.orders_today||0)+' سفارش')+
  stat('فروش ۷ روز',money(d.revenue_7d),(d.conversion_7d||0)+'% conversion')+
  stat('پرداخت نیازمند بررسی',d.pending_payments||0)+stat('مانده کیف پول',money(d.wallet_liability))+
  stat('پلن منتشرشده',d.published_plans||0)+stat('تیکت باز',d.support_open||0,(d.support_urgent||0)+' فوری')+
 '</div><div class="ma-card"><h2>Top Plans · 30 Days</h2><div class="ma-list">'+(top.length?top.map(x=>'<div class="ma-row"><div><b>'+esc(x.name)+'</b><small>'+x.sales+' فروش</small></div><b>'+money(x.revenue_minor)+'</b></div>').join(''):'<span class="ma-muted">هنوز فروش ثبت نشده.</span>')+'</div></div>'+
 '<div class="ma-card"><h2>Gateway</h2><div class="ma-top"><span class="ma-pill">Crypto '+(data.crypto?.enabled&&data.crypto?.configured?'ON':'OFF')+'</span><span class="ma-pill">'+esc(data.dashboard?.role||'')+'</span></div></div></section>';
}
function plans(){
 const inbounds=data.inbounds||[],plans=data.plans||[];
 return '<section class="ma-section"><div class="ma-card"><h2>➕ ساخت پلن فروش</h2><form id="plan-form" class="ma-form">'+
 '<label>نام پلن<input name="name" required maxlength="128" placeholder="Turbo 50GB"></label>'+
 '<div class="ma-grid"><label>نوع<select name="plan_type"><option value="volume">حجمی</option>'+(data.dashboard?.unlimited_plan_allowed?'<option value="unlimited">نامحدود</option>':'')+'</select></label><label>قیمت تومان<input name="price" type="number" min="0" required></label></div>'+
 '<div class="ma-grid"><label>روز<input name="days" type="number" value="30" min="1" max="3650" required></label><label>حجم GB<input name="volume" type="number" value="50" min="1" max="1000000"></label></div>'+
 '<label>IP Limit<input name="ip" type="number" value="1" min="1" max="1000" required></label>'+
 '<div><small class="ma-muted">لوکیشن‌ها</small><div class="ma-checks">'+inbounds.map(x=>'<label class="ma-check"><input type="checkbox" name="inbound" value="'+x.id+'"><span>'+esc(x.name)+' · :'+x.port+'</span></label>').join('')+'</div></div>'+
 '<button class="ma-btn primary" type="submit">انتشار پلن</button></form></div>'+
 '<div class="ma-card"><h2>پلن‌های فروش</h2><div class="ma-list">'+(plans.length?plans.map(p=>'<div class="ma-row"><div><b>'+esc(p.name)+'</b><small>'+esc(p.kind)+' · '+(p.active&&p.visible?'منتشر':'Draft')+'</small></div><span class="ma-pill">'+(p.prices?.length||0)+' variant</span></div>').join(''):'<span class="ma-muted">پلنی وجود ندارد.</span>')+'</div></div></section>';
}
function payments(){
 const rows=data.payments||[];
 return '<section class="ma-section"><div class="ma-card"><h2>Payment Center</h2><div class="ma-list">'+(rows.length?rows.slice(0,50).map(x=>'<div class="ma-row"><div><b>'+(x.kind==='topup'?'شارژ کیف پول':'سفارش')+' · '+money(x.amount_minor)+'</b><small>'+esc(x.status)+' · Telegram '+esc(x.buyer_telegram_id||'—')+'</small></div>'+(x.reviewable?'<div class="ma-actions"><button class="ma-btn good" data-pay="approve" data-kind="'+x.kind+'" data-row="'+x.row_id+'">تأیید</button><button class="ma-btn bad" data-pay="reject" data-kind="'+x.kind+'" data-row="'+x.row_id+'">رد</button></div>':'<span class="ma-pill">'+esc(x.gateway_id||'')+'</span>')+'</div>').join(''):'<span class="ma-muted">پرداختی ثبت نشده.</span>')+'</div></div></section>';
}
function support(){
 const rows=data.support||[];
 const icon={urgent:'🔴',high:'🟠',normal:'🔵',low:'⚪'};
 return '<section class="ma-section"><div class="ma-card"><h2>Support Center</h2><form id="support-contact-form" class="ma-form"><label>پیوی پشتیبانی<input name="support_url" maxlength="500" placeholder="@username" value="'+esc(data.support_url||'')+'"></label><button class="ma-btn primary">ذخیره پیوی</button></form><div class="ma-list">'+(rows.length?rows.slice(0,50).map(t=>'<button class="ma-row ma-btn" data-ticket="'+t.row_id+'"><div><b>'+ (icon[t.priority]||'🔵')+' '+esc(t.subject)+'</b><small>'+esc(t.status)+' · '+(t.message_count||0)+' پیام · '+(t.assigned_to?esc(t.assigned_to):'بدون مسئول')+'</small></div><span>›</span></button>').join(''):'<span class="ma-muted">تیکتی وجود ندارد.</span>')+'</div></div></section>';
}
function render(){
 if(!data)return;
 app.innerHTML='<main class="ma-shell"><header class="ma-head"><div><h1>DARK Telegram Operations</h1><small>'+esc(owner)+'</small></div><span class="ma-badge">V4</span></header>'+nav()+
 (tab==='dashboard'?dashboard():tab==='plans'?plans():tab==='payments'?payments():support())+'</main>';
 bind();
 document.getElementById('support-contact-form')?.addEventListener('submit',async e=>{e.preventDefault();try{await api('/api/telegram-miniapp/support-contact','PUT',{support_url:new FormData(e.target).get('support_url')});await reload()}catch(ex){if(tg?.showAlert)tg.showAlert(ex.message);else alert(ex.message)}});
}
async function reload(){data=await api('/api/telegram-miniapp/bootstrap');render()}
async function openTicket(row){
 const d=await api('/api/telegram-miniapp/support/'+row),t=d.ticket||{},msgs=d.messages||[];
 const quick=data.quick_replies||[];
 app.innerHTML='<main class="ma-shell"><button class="ma-btn" id="back">← برگشت</button><div class="ma-card"><h2>'+esc(t.subject)+'</h2><div class="ma-top"><span class="ma-pill">'+esc(t.priority||'normal')+'</span><span class="ma-pill">'+esc(t.status||'')+'</span></div><div class="ma-ticket-messages">'+msgs.map(m=>'<div class="ma-msg '+esc(m.sender_type)+'"><b>'+(m.sender_type==='admin'?'مدیریت':'مشتری')+'</b><br>'+esc(m.text||('['+(m.file_kind||'file')+']'))+'</div>').join('')+'</div>'+
 '<form id="reply-form" class="ma-form"><label>پاسخ<textarea name="text" rows="4" required></textarea></label><div class="ma-top">'+quick.filter(x=>x.active).slice(0,6).map(x=>'<button type="button" class="ma-btn" data-quick="'+esc(x.body)+'">'+esc(x.title)+'</button>').join('')+'</div><button class="ma-btn primary">ارسال پاسخ</button></form>'+
 '<div class="ma-grid"><label class="ma-form">اولویت<select id="priority"><option value="low">کم</option><option value="normal">عادی</option><option value="high">زیاد</option><option value="urgent">فوری</option></select></label><label class="ma-form">مسئول<input id="assigned" value="'+esc(t.assigned_to||'')+'"></label></div><div class="ma-actions"><button class="ma-btn" id="save-meta">ذخیره Triage</button><button class="ma-btn bad" id="close-ticket">بستن تیکت</button></div></div></main>';
 document.getElementById('priority').value=t.priority||'normal';
 document.getElementById('back').onclick=()=>render();
 document.querySelectorAll('[data-quick]').forEach(b=>b.onclick=()=>{document.querySelector('#reply-form textarea').value=b.dataset.quick});
 document.getElementById('reply-form').onsubmit=async e=>{e.preventDefault();const text=new FormData(e.target).get('text');await api('/api/telegram-miniapp/support/'+row+'/reply','POST',{text:String(text)});await reload();await openTicket(row)};
 document.getElementById('save-meta').onclick=async()=>{await api('/api/telegram-miniapp/support/'+row+'/meta','PUT',{priority:document.getElementById('priority').value,assigned_to:document.getElementById('assigned').value});await reload();await openTicket(row)};
 document.getElementById('close-ticket').onclick=async()=>{await api('/api/telegram-miniapp/support/'+row+'/close','POST',{});await reload()};
}
function bind(){
 document.querySelectorAll('[data-tab]').forEach(b=>b.onclick=()=>{tab=b.dataset.tab;render()});
 const form=document.getElementById('plan-form');if(form)form.onsubmit=async e=>{e.preventDefault();const fd=new FormData(form),ids=fd.getAll('inbound').map(Number);if(!ids.length){tg?.showAlert?.('حداقل یک لوکیشن انتخاب کن.');return}const type=String(fd.get('plan_type'));await api('/api/telegram-miniapp/simple-plans','POST',{name:String(fd.get('name')),plan_type:type,price_minor:Number(fd.get('price')),duration_days:Number(fd.get('days')),volume_gb:type==='unlimited'?0:Number(fd.get('volume')),ip_limit:Number(fd.get('ip')),inbound_ids:ids,published:true});await reload();tab='plans';render()};
 document.querySelectorAll('[data-pay]').forEach(b=>b.onclick=async()=>{await api('/api/telegram-miniapp/payments/'+b.dataset.kind+'/'+b.dataset.row+'/'+b.dataset.pay,'POST',{});await reload();tab='payments';render()});
 document.querySelectorAll('[data-ticket]').forEach(b=>b.onclick=()=>openTicket(b.dataset.ticket));
}
async function boot(){
 try{
  if(!tg||!tg.initData)throw Error('این Mini App باید از داخل ربات Telegram باز شود.');
  if(!owner)throw Error('Owner scope مشخص نیست.');
  tg.ready();tg.expand();await reload();
 }catch(e){app.innerHTML='<div class="ma-error">⛔ '+esc(e.message)+'</div>'}
}
boot();
})();