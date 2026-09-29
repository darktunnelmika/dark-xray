/* DARK XRAY Nodes V9 — final fleet operations closeout. */
(function(){
'use strict';
if(typeof enginePage!=='function'||typeof runAction!=='function')return;
const pageBase=enginePage,actionBase=runAction;
const L=(en,fa)=>((localStorage.getItem('dark_lang')||'en')==='fa'?fa:en);
let scheduled=false;
const hubSources=new Map();

const alertLabels={
 telemetry_offline:['Telemetry offline','تله‌متری آفلاین'],telemetry_stale:['Telemetry stale','تله‌متری قدیمی'],
 cpu_high:['CPU high','CPU بالا'],cpu_critical:['CPU critical','CPU بحرانی'],
 memory_high:['RAM high','RAM بالا'],memory_critical:['RAM critical','RAM بحرانی'],
 disk_high:['Disk usage high','مصرف دیسک بالا'],disk_critical:['Disk almost full','دیسک تقریباً پر'],
 load_high:['System load high','لود سیستم بالا'],load_critical:['System overloaded','فشار سیستم بحرانی'],
 xray_not_running:['Xray is not running','Xray در حال اجرا نیست'],xray_error:['Xray error','خطای Xray'],
 hub_lease_invalid:['Hub accounting lease blocked','مجوز حسابداری Hub مسدود'],
 accounting_checkpoint_error:['Accounting checkpoint error','خطای ثبت مصرف'],
 accounting_checkpoint_stale:['Accounting checkpoint delayed','ثبت مصرف با تأخیر'],
 node_error:['Node communication error','خطای ارتباط نود']
};
function nodeById(id){return (state.nv2?.nodes||[]).find(n=>String(n.id)===String(id));}
function dur(value){let s=Math.max(0,Number(value)||0);if(s<60)return Math.floor(s)+'s';if(s<3600)return Math.floor(s/60)+'m';if(s<86400)return Math.floor(s/3600)+'h';return Math.floor(s/86400)+'d '+Math.floor((s%86400)/3600)+'h';}
function pct(value){const n=Number(value);return Number.isFinite(n)?Math.max(0,Math.min(100,n)).toFixed(n>=10?0:1)+'%':'—';}
function alertText(a){
 const pair=alertLabels[a?.code]||[String(a?.code||'Alert'),String(a?.code||'هشدار')];
 let out=L(pair[0],pair[1]);
 if(a?.value!==undefined&&a?.value!==null)out+=' · '+(['telemetry_stale','accounting_checkpoint_stale'].includes(a.code)?dur(a.value):pct(a.value));
 if(a?.started_at)out+=' · '+dur(Date.now()/1000-Number(a.started_at));
 return out;
}
function renderFleetAlerts(){
 const host=document.getElementById('nv7-fleet-health');if(!host||host.dataset.nv9==='1')return;
 const rows=[];
 for(const n of state.nv2?.nodes||[])for(const a of n.operational_health?.alerts||[])if(['warning','critical'].includes(a.severity))rows.push({n,a});
 rows.sort((x,y)=>(x.a.severity==='critical'?0:1)-(y.a.severity==='critical'?0:1));
 if(!rows.length){
  host.innerHTML='<div class="nv7-fleet-ok"><b>'+L('Fleet healthy','ناوگان سالم')+'</b><span>'+L('No active Node health alerts.','هشدار سلامت فعالی برای نودها وجود ندارد.')+'</span></div>';
 }else{
  host.innerHTML='<div class="nv7-fleet-alerts"><header><b>'+L('Node health alerts','هشدارهای سلامت نود')+'</b><span>'+fa(rows.length)+'</span></header><div>'+
   rows.slice(0,8).map(x=>'<button type="button" class="'+e(x.a.severity||'warning')+'" data-act="nv9alert" data-id="'+e(x.n.id)+'"><b>'+e(x.n.name||x.n.id)+'</b> · '+e(alertText(x.a))+'</button>').join('')+
   (rows.length>8?'<span class="more">+'+fa(rows.length-8)+' '+L('more','بیشتر')+'</span>':'')+'</div></div>';
 }
 host.dataset.nv9='1';
}
function decorateSummary(){
 const grid=document.querySelector('.nv7-fleet-head>div');if(!grid)return;
 let item=grid.querySelector('[data-nv9-maintenance-summary]');
 if(!item){item=document.createElement('div');item.setAttribute('data-nv9-maintenance-summary','1');item.innerHTML='<small>'+L('Maintenance','تعمیرات')+'</small><b data-nv9-maintenance-count>0</b>';const offline=grid.querySelector('[data-nv6-summary="offline"]')?.parentElement;offline?.after(item);}
 const count=(state.nv2?.nodes||[]).filter(n=>n.maintenance).length,itemCount=item.querySelector('[data-nv9-maintenance-count]');if(itemCount)itemCount.textContent=fa(count);
}
function maintenanceButton(n){
 return '<button type="button" class="btn nv9-maint-btn" data-act="nv9maintenance" data-id="'+e(n.id)+'" data-on="'+(n.maintenance?'1':'0')+'">'+(n.maintenance?L('Resume','بازگشت'):L('Maintenance','تعمیرات'))+'</button>';
}
function decorateCards(){
 document.querySelectorAll('.nv2-node[data-nv6-node]').forEach(card=>{
  const n=nodeById(card.dataset.nv6Node);if(!n)return;
  card.classList.toggle('nv9-maintenance-card',!!n.maintenance);
  const status=card.querySelector('.nv2-status');
  if(status&&n.maintenance){status.className='nv2-status maintenance';const b=status.querySelector('b');if(b)b.textContent=L('MAINTENANCE','تعمیرات');}
  let notice=card.querySelector('[data-nv9-maintenance]');
  if(n.maintenance&&!notice){notice=document.createElement('div');notice.className='notice warning nv9-maintenance-notice';notice.dataset.nv9Maintenance='1';notice.innerHTML='<b>'+L('MAINTENANCE MODE','حالت تعمیرات')+'</b><small>'+e(n.maintenance_note||L('New subscription routes are paused; Agent/Xray stay running.','انتشار مسیر جدید متوقف است؛ Agent/Xray روشن می‌مانند.'))+'</small>';card.querySelector('.nv6-live-strip')?.before(notice);}
  if(!n.maintenance&&notice)notice.remove();
  const actions=card.querySelector('.nv2-actions');if(actions){
   let buttonEl=actions.querySelector('[data-act="nv9maintenance"]');
   if(!buttonEl){actions.insertAdjacentHTML('afterbegin',maintenanceButton(n));}
   else{buttonEl.dataset.on=n.maintenance?'1':'0';buttonEl.textContent=n.maintenance?L('Resume','بازگشت'):L('Maintenance','تعمیرات');}
  }
 });
 document.querySelectorAll('.nv4-route').forEach(route=>{const spans=route.querySelectorAll('span');spans.forEach(s=>{if(s.textContent.trim()==='NODE_MAINTENANCE')s.textContent=L('MAINTENANCE','تعمیرات');});});
}
function decorateManage(){
 const box=document.querySelector('.nv5-manage-actions>div:last-child');if(!box||box.querySelector('[data-act="nv9maintenance"]'))return;
 const id=box.querySelector('[data-act="nv2probe"]')?.dataset.id,n=nodeById(id);if(n)box.insertAdjacentHTML('afterbegin',maintenanceButton(n));
}
function tstamp(value){return Number(value)>0?date(Number(value)):'—';}
function guardText(guard){
 if(!guard||typeof guard!=='object')return '—';
 const state=String(guard.state||'unknown'),applied=guard.applied===true?L('applied','اعمال‌شده'):L('not applied','اعمال‌نشده');
 return state+' · '+applied;
}
function updateText(n,hub){
 const source=n.health?.installed_source||{},nodeCommit=String(source.commit||''),hubCommit=String(hub?.commit||'');
 if(!nodeCommit||!hubCommit)return L('Unknown','نامشخص');
 return nodeCommit===hubCommit?L('Current','به‌روز'):L('Update available','آپدیت موجود');
}
function opsPanel(n,hub){
 const h=n.health||{},guard=h.guard||{},maint=h.maintenance||{},desired=n.desired_state||{},source=h.installed_source||{};
 return '<section class="nv9-ops-panel"><header>'+L('Operations & Recovery','عملیات و بازیابی')+'</header><div>'+
  '<div><small>'+L('Maintenance','تعمیرات')+'</small><b>'+(n.maintenance?L('ACTIVE','فعال'):L('OFF','خاموش'))+'</b></div>'+
  '<div><small>'+L('Maintenance since','شروع تعمیرات')+'</small><b>'+e(n.maintenance?tstamp(n.maintenance_since):'—')+'</b></div>'+
  '<div><small>'+L('Failures','خطاها')+'</small><b>'+fa(Number(n.failure_count||0))+'</b></div>'+
  '<div><small>'+L('Recoveries','بازیابی‌ها')+'</small><b>'+fa(Number(n.recovery_count||0))+'</b></div>'+
  '<div><small>'+L('Last offline','آخرین آفلاین')+'</small><b>'+e(tstamp(n.last_offline_at))+'</b></div>'+
  '<div><small>'+L('Last recovered','آخرین بازیابی')+'</small><b>'+e(tstamp(n.last_recovered_at))+'</b></div>'+
  '<div><small>'+L('Update status','وضعیت آپدیت')+'</small><b>'+e(updateText(n,hub))+'</b></div>'+
  '<div><small>'+L('Node source','سورس نود')+'</small><b>'+e(String(source.commit||'—').slice(0,12))+'</b></div>'+
  '<div><small>'+L('Guard','محافظ')+'</small><b>'+e(guardText(guard))+'</b></div>'+
  '<div><small>'+L('Source verified','مبدأ تأییدشده')+'</small><b>'+(guard.source_verified===true?L('YES','بله'):guard.source_verified===false?L('NO','خیر'):'—')+'</b></div>'+
  '<div><small>'+L('Desired revision','نسخه مطلوب')+'</small><b>'+e(String(desired.revision||0))+'</b></div>'+
  '<div><small>'+L('Applied revision','نسخه اعمال‌شده')+'</small><b>'+e(String(desired.applied_revision||0))+'</b></div>'+
  '<div><small>'+L('Pending config','تنظیم در انتظار')+'</small><b>'+(desired.pending?L('YES','بله'):L('NO','خیر'))+'</b></div>'+
  '<div><small>'+L('Accounting checkpoint','چک‌پوینت حسابداری')+'</small><b>'+(maint.checkpoint_age_seconds!=null?Number(maint.checkpoint_age_seconds).toFixed(1)+'s':'—')+'</b></div>'+
 '</div>'+(n.maintenance_note?'<p class="nv9-maintenance-note">'+e(n.maintenance_note)+'</p>':'')+'</section>';
}
function alertPanel(n){
 const alerts=n.operational_health?.alerts||[];if(!alerts.length)return '';
 return '<section class="nv9-detail-alerts"><header>'+L('Active alerts','هشدارهای فعال')+'</header><div>'+alerts.map(a=>
  '<article class="'+e(a.severity||'warning')+'"><b>'+e(alertText(a))+'</b><small>'+L('Started','شروع')+': '+e(tstamp(a.started_at))+' · '+L('Last observed','آخرین مشاهده')+': '+e(tstamp(a.last_observed_at))+'</small></article>'
 ).join('')+'</div></section>';
}
function decorateDetail(){
 const root=document.querySelector('[data-nv8-detail]');if(!root||root.querySelector('[data-nv9-detail]'))return;
 const n=nodeById(root.dataset.nv8Detail);if(!n)return;
 const holder=document.createElement('div');holder.dataset.nv9Detail='1';holder.className='nv9-detail-extra';holder.innerHTML=alertPanel(n)+opsPanel(n,hubSources.get(String(n.id)));
 const history=root.querySelector('.nv8-history');history?history.before(holder):root.appendChild(holder);
}
async function cacheHubSource(id){
 try{const doc=await api('/api/nodes/'+enc(id)+'/metrics?window=live');if(doc?.hub_source)hubSources.set(String(id),doc.hub_source);}catch(_ex){}
}
function decorate(){
 if(state.page==='nodes'){decorateSummary();decorateCards();renderFleetAlerts();}
 decorateManage();decorateDetail();
}
function schedule(){if(scheduled)return;scheduled=true;setTimeout(()=>{scheduled=false;decorate();},0);}

const observer=new MutationObserver(schedule);
if(document.body)observer.observe(document.body,{childList:true,subtree:true});

enginePage=async function(){const html=await pageBase();setTimeout(schedule,0);return html;};

runAction=async function(act,el){
 if(act==='nv9maintenance'){
  const id=el.dataset.id,on=el.dataset.on==='1';
  if(on){
   if(!confirm(L('Exit Maintenance Mode and allow this Node into new subscription routes again?','از حالت تعمیرات خارج شود و مسیر این نود دوباره در اشتراک‌های جدید منتشر شود؟')))return;
   await api('/api/nodes/'+enc(id)+'/maintenance','POST',{enabled:false,note:''});closeDialog();toast(L('Node returned to service.','نود به چرخه سرویس برگشت.'));await renderPage();return;
  }
  dialog(L('Maintenance Mode','حالت تعمیرات'),'<div class="notice warning">'+L('Existing connections are not stopped. The Node stays online and monitored, but new subscription/failover routes are paused until you resume it.','اتصال‌های فعلی قطع نمی‌شوند. نود روشن و تحت مانیتورینگ می‌ماند، اما انتشار مسیر جدید تا خروج از تعمیرات متوقف می‌شود.')+'</div>'+field(L('Maintenance note','یادداشت تعمیرات'),'note','','text','maxlength="300"'),async f=>{await api('/api/nodes/'+enc(id)+'/maintenance','POST',{enabled:true,note:String(f.get('note')||'')});closeDialog();toast(L('Maintenance Mode enabled.','حالت تعمیرات فعال شد.'));await renderPage();},L('Enable Maintenance','فعال‌کردن تعمیرات'));return;
 }
 if(act==='nv9alert'){
  const id=el.dataset.id;if(!id)return;
  await cacheHubSource(id);
  await actionBase('nv8details',{dataset:{id}});
  setTimeout(decorate,0);return;
 }
 if(act==='nv8details'){
  const id=el.dataset.id;await cacheHubSource(id);const out=await actionBase(act,el);setTimeout(decorate,0);return out;
 }
 if(act==='nv8range'){const out=await actionBase(act,el);setTimeout(decorate,0);return out;}
 const out=await actionBase(act,el);setTimeout(schedule,0);return out;
};
})();