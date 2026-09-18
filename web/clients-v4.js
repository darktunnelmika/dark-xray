/* DARK XRAY Clients V4 — reduced-noise cyber workspace and basic-first client editor. */
(function(){
'use strict';
if(typeof clientsPage!=='function'||typeof runAction!=='function'||typeof clientForm!=='function')return;
const baseClientsPage=clientsPage,baseRunAction=runAction,baseClientForm=clientForm;
state.cv4=state.cv4||{view:'clients',presence:'all',status:'all',owner:'all',inbound:'all',group:'all',sort:'activity',filters:false};
const L=(en,fa)=>((localStorage.getItem('dark_lang')||'en')==='fa'?fa:en);
const pct=(u,t)=>t?Math.min(100,Math.max(0,100*Number(u||0)/Number(t))):0;
const statusOf=r=>{const x=r.block_reasons||[];if(x.includes('client_manual')||r.client?.enable===false||r.observed_enable===false)return'disabled';if(x.length)return'blocked';return'active';};
const presenceOf=r=>['online','idle','offline'].includes(r.presence_state)?r.presence_state:'offline';
const inboundMeta=i=>({network:i.network||i.streamSettings?.network||'tcp',security:i.security||i.streamSettings?.security||'none'});
function ago(s){if(s==null)return L('never','هرگز');s=Math.max(0,Number(s)||0);if(s<60)return L('now','الان');if(s<3600)return Math.floor(s/60)+'m';if(s<86400)return Math.floor(s/3600)+'h';return Math.floor(s/86400)+'d';}
function signal(r){const p=presenceOf(r),names={online:L('ONLINE','آنلاین'),idle:L('IDLE','کم‌فعال'),offline:L('OFFLINE','آفلاین')};return `<div class="cv4-signal ${p}"><span class="cv4-orb"></span><span class="cv4-beam"><i></i></span><b>${names[p]}</b><small>${e(ago(r.presence_age_seconds))}</small></div>`;}
function owners(){return state.owners||[];}
function groups(owner='all'){return (state.groups||[]).filter(g=>owner==='all'||g.owner===owner);}
function ownerOptions(){return owners().filter(o=>isOwner()||o.id===state.me.id).map(o=>[o.id,o.name||o.id]);}
function defaultOwner(){const opts=ownerOptions();if(opts.some(x=>x[0]===state.me.id))return state.me.id;if(opts.length===1)return opts[0][0];return opts[0]?.[0]||state.me.id;}
const groupKey=(owner,name)=>encodeURIComponent(owner)+'|'+encodeURIComponent(name);
function groupMatch(row,key){if(key==='all')return true;if(key==='__ungrouped')return !(row.client?.group||'');const p=String(key).split('|');if(p.length!==2)return false;try{return row.owner===decodeURIComponent(p[0])&&(row.client?.group||'')===decodeURIComponent(p[1]);}catch(_){return false;}}
function inboundNames(r){return (r.inboundIds||[]).map(id=>{const x=state.inbounds.find(i=>i.id===id);return x?(x.remark||x.tag):'#'+id;});}
function filteredV4(){
 let rows=filtered(state.clients);
 const f=state.cv4;
 if(f.owner!=='all')rows=rows.filter(x=>x.owner===f.owner);
 if(f.inbound!=='all')rows=rows.filter(x=>(x.inboundIds||[]).includes(Number(f.inbound)));
 if(f.group!=='all')rows=rows.filter(x=>groupMatch(x,f.group));
 if(f.status!=='all')rows=rows.filter(x=>statusOf(x)===f.status);
 if(f.presence!=='all')rows=rows.filter(x=>presenceOf(x)===f.presence);
 return [...rows].sort((a,b)=>{
  if(f.sort==='name')return a.email.localeCompare(b.email);
  if(f.sort==='traffic')return Number(b.used_bytes||0)-Number(a.used_bytes||0);
  if(f.sort==='expiry'){const ae=Number(a.client?.expiryTime||0)||Number.MAX_SAFE_INTEGER,be=Number(b.client?.expiryTime||0)||Number.MAX_SAFE_INTEGER;return ae-be;}
  return Number(b.activity_at||0)-Number(a.activity_at||0)||a.email.localeCompare(b.email);
 });
}
function opt(items,value){return items.map(([v,l])=>`<option value="${e(v)}" ${String(v)===String(value)?'selected':''}>${e(l)}</option>`).join('');}
function mini(text,act,id,kind=''){return `<button type="button" class="cv4-action ${kind}" data-act="${act}" data-id="${e(id)}">${text}</button>`;}
function row(r){
 const c=r.client||{},used=Number(r.used_bytes||0),total=Number(c.totalGB||0),ins=inboundNames(r),group=c.group||L('Ungrouped','بدون گروه');
 const expiry=c.expiryTime>0?new Date(c.expiryTime).toLocaleDateString((localStorage.getItem('dark_lang')||'en')==='fa'?'fa-IR':'en-US'):L('Unlimited','نامحدود');
 return `<article class="cv4-row">
  <label class="cv4-check"><input type="checkbox" data-select="${e(r.email)}" ${state.selected.has(r.email)?'checked':''}><span></span></label>
  <button type="button" class="cv4-client" data-act="cv4detail" data-id="${e(r.email)}"><span class="cv4-avatar">${e(r.email.slice(0,1).toUpperCase())}</span><span><b>${e(r.email)}</b><small>${e(r.owner)} · ${e(group)}</small></span></button>
  <div class="cv4-live">${signal(r)}<span class="cv4-policy ${statusOf(r)}">${statusOf(r).toUpperCase()}</span></div>
  <div class="cv4-service">${ins.slice(0,2).map(x=>`<span>${e(x)}</span>`).join('')}${ins.length>2?`<small>+${ins.length-2}</small>`:''}</div>
  <div class="cv4-usage"><b class="mono">${bytes(used)}</b><small>${total?bytes(total):L('Unlimited','نامحدود')}</small>${total?`<div class="cv4-meter"><i style="width:${pct(used,total)}%"></i></div>`:''}</div>
  <div class="cv4-expiry"><b>${e(expiry)}</b><small>${statusOf(r)==='active'?L('Service active','سرویس فعال'):statusOf(r)==='blocked'?L('Service limited','سرویس محدود'):L('Service disabled','سرویس قطع')}</small></div>
  <div class="cv4-row-actions">${mini(L('OPEN','بازکردن'),'cv4detail',r.email,'open')}${can('clients.credentials',r.owner)?mini('QR','cv3links',r.email):''}</div>
 </article>`;
}
function stats(){
 const total=state.clients.length,online=state.clients.filter(x=>presenceOf(x)==='online').length,blocked=state.clients.filter(x=>statusOf(x)==='blocked').length,used=state.clients.reduce((n,x)=>n+Number(x.used_bytes||0),0);
 return `<div class="cv4-stats"><button data-act="cv4quick" data-key="presence" data-value="all"><span>${L('TOTAL','کل')}</span><b>${fa(total)}</b></button><button class="online" data-act="cv4quick" data-key="presence" data-value="online"><span>${L('ONLINE','آنلاین')}</span><b>${fa(online)}</b></button><button class="warn" data-act="cv4quick" data-key="status" data-value="blocked"><span>${L('BLOCKED','محدود')}</span><b>${fa(blocked)}</b></button><div><span>${L('TRAFFIC','مصرف')}</span><b>${bytes(used)}</b></div></div>`;
}
function quickTabs(){
 const p=state.cv4.presence,s=state.cv4.status;
 const b=(label,key,value,on)=>`<button class="${on?'active':''}" data-act="cv4quick" data-key="${key}" data-value="${value}">${label}</button>`;
 return `<div class="cv4-quick">${b(L('All','همه'),'presence','all',p==='all'&&s==='all')}${b(L('Online','آنلاین'),'presence','online',p==='online')}${b(L('Idle','کم‌فعال'),'presence','idle',p==='idle')}${b(L('Offline','آفلاین'),'presence','offline',p==='offline')}${b(L('Blocked','محدود'),'status','blocked',s==='blocked')}</div>`;
}
function filters(){
 const f=state.cv4,ownerItems=[['all',L('All owners','همه مالک‌ها')],...owners().map(x=>[x.id,x.name||x.id])],inItems=[['all',L('All inbounds','همه اینباندها')],...state.inbounds.map(x=>[String(x.id),x.remark||x.tag])],groupItems=[['all',L('All groups','همه گروه‌ها')],['__ungrouped',L('Ungrouped','بدون گروه')],...groups(f.owner).map(g=>[groupKey(g.owner,g.name),(f.owner==='all'?((owners().find(o=>o.id===g.owner)?.name||g.owner)+' · '):'')+g.name])],statusItems=[['all',L('All states','همه وضعیت‌ها')],['active',L('Active','فعال')],['disabled',L('Disabled','قطع')],['blocked',L('Blocked','محدود')]];
 return `<div class="cv4-filterdeck ${f.filters?'show':''}"><label><span>${L('Owner','مالک')}</span><select data-cv4-filter="owner">${opt(ownerItems,f.owner)}</select></label><label><span>${L('Inbound','اینباند')}</span><select data-cv4-filter="inbound">${opt(inItems,f.inbound)}</select></label><label><span>${L('Group','گروه')}</span><select data-cv4-filter="group">${opt(groupItems,f.group)}</select></label><label><span>${L('Account state','وضعیت حساب')}</span><select data-cv4-filter="status">${opt(statusItems,f.status)}</select></label></div>`;
}
function clientsView(){
 const rows=filteredV4(),f=state.cv4,sortItems=[['activity',L('Recent activity','آخرین فعالیت')],['name',L('Name','نام')],['traffic',L('Traffic','مصرف')],['expiry',L('Expiry','انقضا')]];
 return heading(L('Clients','کاربران'),L('Clean control deck for live status, service access and customer organization.','مرکز کنترل خلوت برای وضعیت زنده، سرویس و سازماندهی کاربران.'),`${can('clients.create')?button(L('Bulk create','ساخت گروهی'),'cv4bulk','users'):''}${can('clients.create')?button(L('Create client','ساخت کاربر'),'new','plus','',true):''}`)+notices()+`<div class="clients-v4">
  <div class="cv4-nav"><button class="active" data-act="cv4view" data-view="clients"><span>01</span>${L('Clients','کاربران')}</button><button data-act="cv4view" data-view="groups"><span>02</span>${L('Groups','گروه‌ها')}</button></div>
  ${stats()}
  <section class="panel cv4-console">
   <div class="cv4-commandbar"><div class="cv4-search">${icon('search')}<input id="search" type="search" value="${e(state.search)}" placeholder="${L('Search client or owner…','جستجوی کاربر یا مالک…')}"></div>${quickTabs()}<div class="cv4-spacer"></div><select id="cv4-sort">${opt(sortItems,f.sort)}</select><button class="cv4-filter-btn ${f.filters?'active':''}" data-act="cv4filters">${icon('settings')}${L('Filters','فیلتر')}</button><button class="cv4-reset" data-act="cv4reset">${icon('refresh')}</button></div>
   ${filters()}
   ${state.selected.size?`<div class="cv4-bulkbar"><b>${state.selected.size} ${L('selected','انتخاب')}</b><span></span><button data-act="cv2bulkadjust">${L('Adjust','تغییر')}</button><button data-act="cv2bulkinbounds">${L('Inbounds','اینباند')}</button><button data-act="bulk">${L('State / Reset','وضعیت / ریست')}</button></div>`:''}
   <div class="cv4-head"><div></div><div>${L('CLIENT','کاربر')}</div><div>${L('LIVE / STATE','اتصال / وضعیت')}</div><div>${L('SERVICE','سرویس')}</div><div>${L('USAGE','مصرف')}</div><div>${L('EXPIRY / LIMITS','انقضا / محدودیت')}</div><div></div></div>
   <div class="cv4-list">${rows.length?rows.map(row).join(''):`<div class="cv4-empty">${icon('users')}<b>${L('No clients found','کاربری پیدا نشد')}</b><small>${L('Change filters or create a new client.','فیلتر را تغییر بده یا کاربر جدید بساز.')}</small></div>`}</div>
  </section>
 </div>`;
}
function groupsView(){
 const gs=groups(state.cv4.owner);
 return heading(L('Client Groups','گروه‌های کاربران'),L('Organization without mixing reseller ownership.','سازماندهی کاربران بدون قاطی‌شدن مالکیت نماینده‌ها.'),can('clients.edit')?button(L('New group','گروه جدید'),'cv2groupnew','plus','',true):'')+notices()+`<div class="clients-v4"><div class="cv4-nav"><button data-act="cv4view" data-view="clients"><span>01</span>${L('Clients','کاربران')}</button><button class="active" data-act="cv4view" data-view="groups"><span>02</span>${L('Groups','گروه‌ها')}</button></div><div class="cv4-group-grid">${gs.length?gs.map(g=>`<article class="cv4-group-card" style="--g:${e(g.color||'#22d3ee')}"><i></i><header><span>${e(g.owner)}</span><b>${e(g.name)}</b></header><div><strong>${g.client_count||0}</strong><small>${L('clients','کاربر')}</small></div><footer><span>${bytes(g.used_bytes||0)}</span>${g.implicit?`<button data-act="cv2groupregister" data-owner="${e(g.owner)}" data-name="${e(g.name)}">${L('Register','ثبت')}</button>`:`<button data-act="cv2groupdelete" data-owner="${e(g.owner)}" data-name="${e(g.name)}">${L('Delete','حذف')}</button>`}</footer></article>`).join(''):`<div class="cv4-empty">${icon('users')}<b>${L('No groups yet','هنوز گروهی نیست')}</b><small>${L('Create groups only when you need organization.','فقط وقتی لازم داری گروه بساز.')}</small></div>`}</div></div>`;
}
clientsPage=function(){return state.cv4.view==='groups'?groupsView():clientsView();};

function ownerAllowed(owner){const o=owners().find(x=>x.id===owner);return o?.allowed||[];}
function groupOptions(owner){return groups(owner).map(g=>[g.name,g.name]);}
function inboundTiles(ids,owner){
 const allowed=ownerAllowed(owner),list=state.inbounds.filter(i=>!allowed.length||allowed.includes(i.id));
 return `<div class="cv4-inbound-picker">${list.map(i=>{const m=inboundMeta(i),checked=ids.includes(i.id);return `<label class="${checked?'checked':''}"><input type="checkbox" name="inbound" value="${i.id}" ${checked?'checked':''}><span class="cv4-in-dot"></span><span><b>${e(i.remark||i.tag)}</b><small>:${i.port} · ${e(i.protocol)} · ${e(m.network)} / ${e(m.security)}</small></span></label>`;}).join('')}</div>`;
}
function fInput(label,name,value='',type='text',attrs=''){return `<label class="cv4-field"><span>${label}</span><input name="${name}" type="${type}" value="${e(value)}" ${attrs}></label>`;}
function fSelect(label,name,items,value){return `<label class="cv4-field"><span>${label}</span><select name="${name}">${opt(items,value)}</select></label>`;}
function segment(name,items,value){return `<div class="cv4-segments">${items.map(([v,l])=>`<label><input type="radio" name="${name}" value="${v}" ${String(v)===String(value)?'checked':''}><span>${l}</span></label>`).join('')}</div>`;}
function flowCompatible(ids){if(!ids.length)return false;return ids.every(id=>{const i=state.inbounds.find(x=>x.id===id),m=i&&inboundMeta(i);return i?.protocol==='vless'&&['tcp','raw'].includes(m?.network)&&['tls','reality'].includes(m?.security);});}
function decorateEditor(){const d=document.querySelector('#overlay .dialog');if(d)d.classList.add('cv4-editor');}
async function clientFormV4(email=null,inbound=null){
 const detail=email?await api('/api/clients/'+enc(email)):null,c=detail?.client||{},owner=detail?.owner||defaultOwner(),ids=detail?.inboundIds||(inbound?[Number(inbound)]:[]);
 const plan=Number(c.totalGB||0)>0?'limited':'unlimited',expiryMode=Number(c.expiryTime||0)>0?'date':'unlimited',dateValue=c.expiryTime>0?new Date(c.expiryTime).toISOString().slice(0,16):'';
 const ownerField=isOwner()&&ownerOptions().length>1?fSelect(L('Owner','مالک'),'owner',ownerOptions(),owner):`<input type="hidden" name="owner" value="${e(owner)}"><div class="cv4-lockfield"><span>${L('Owner','مالک')}</span><b>${e(owner)}</b></div>`;
 const groupItems=[['',L('Ungrouped','بدون گروه')],...groupOptions(owner)];
 const body=`<div class="cv4-editor-shell">
  <section class="cv4-edit-section"><header><span>01</span><div><b>${L('Identity','هویت')}</b><small>${L('Only what is needed for day-to-day creation.','فقط چیزهایی که برای ساخت روزمره لازم است.')}</small></div></header><div class="cv4-edit-grid">${fInput(L('Client name / ID','نام کاربر'),'email',email||'','text',email?'readonly':'required maxlength="128" dir="ltr"')}${ownerField}${fSelect(L('Group','گروه'),'group',groupItems,c.group||'')}${fSelect(L('State','وضعیت'),'enable',[['true',L('Active','فعال')],['false',L('Disabled','قطع')]],String(c.enable!==false))}</div></section>
  <section class="cv4-edit-section"><header><span>02</span><div><b>${L('Service','سرویس')}</b><small>${L('Select one or more customer inbounds.','یک یا چند اینباند مشتری انتخاب کن.')}</small></div></header><div id="cv4-inbounds">${inboundTiles(ids,owner)}</div></section>
  <section class="cv4-edit-section"><header><span>03</span><div><b>${L('Plan','پلن')}</b><small>${L('Quota, expiry and IP limit.','حجم، انقضا و محدودیت IP.')}</small></div></header><div class="cv4-plan-grid"><div class="cv4-choice"><span>${L('Traffic','حجم')}</span>${segment('planMode',[['unlimited',L('Unlimited','نامحدود')],['limited',L('Limited','حجمی')]],plan)}</div><div data-cv4-quota class="${plan==='limited'?'':'hidden'}">${fInput(L('Quota (GiB)','حجم GiB'),'totalGB',plan==='limited'?(Number(c.totalGB||0)/gb):50,'number','min="0.01" step="0.01"')}</div><div class="cv4-choice"><span>${L('Expiry','انقضا')}</span>${segment('expiryMode',[['unlimited',L('Unlimited','نامحدود')],['days',L('Days','روز')],['date',L('Date','تاریخ')]],expiryMode)}</div><div data-cv4-days class="hidden">${fInput(L('Days from now','تعداد روز'),'expiryDays',30,'number','min="1" max="36500"')}</div><div data-cv4-date class="${expiryMode==='date'?'':'hidden'}">${fInput(L('Expiry date','تاریخ انقضا'),'expiry',dateValue,'datetime-local')}</div>${fInput(L('IP limit · 0 unlimited','محدودیت IP'),'limitIp',c.limitIp??1,'number','min="0" max="1000" required')}</div></section>
  <details class="cv4-advanced"><summary><span>${icon('settings')}</span><div><b>${L('Advanced','پیشرفته')}</b><small>${L('Open only when you need reset rules, notes or XTLS flow.','فقط برای ریست، یادداشت یا Flow باز کن.')}</small></div><i>+</i></summary><div class="cv4-advanced-body">${fSelect(L('Traffic reset','ریست ترافیک'),'resetMode',[['never',L('Never','هرگز')],['daily',L('Daily','روزانه')],['weekly',L('Weekly','هفتگی')],['monthly',L('Monthly','ماهانه')],['interval',L('Custom days','بازه روز')]],(c.resetTraffic&&c.resetTraffic!=='never')?c.resetTraffic:(c.reset>0?'interval':'never'))}${fInput(L('Custom reset days','بازه ریست'),'resetDays',c.reset||0,'number','min="0" max="3650"')}${fInput(L('Monthly reset day','روز ریست ماهانه'),'resetTrafficDay',c.resetTrafficDay||0,'number','min="0" max="31"')}<label class="cv4-field cv4-full"><span>${L('Comment','یادداشت')}</span><textarea name="comment" rows="3">${e(c.comment||'')}</textarea></label><label class="cv4-field" data-cv4-flow><span>XTLS Flow</span><select name="flow"><option value="">${L('Automatic / none','خودکار / بدون Flow')}</option><option value="xtls-rprx-vision" ${c.flow==='xtls-rprx-vision'?'selected':''}>xtls-rprx-vision</option><option value="xtls-rprx-vision-udp443" ${c.flow==='xtls-rprx-vision-udp443'?'selected':''}>xtls-rprx-vision-udp443</option></select><small data-cv4-flow-note></small></label></div></details>
 </div>`;
 dialog(email?L('Edit client','ویرایش کاربر')+' · '+email:L('Create client','ساخت کاربر'),body,async f=>{
   const selected=f.getAll('inbound').map(Number);if(!selected.length)throw Error(L('Select at least one inbound.','حداقل یک اینباند انتخاب کن.'));
   const planMode=f.get('planMode'),expMode=f.get('expiryMode');let expiryTime=0;
   if(expMode==='days')expiryTime=Date.now()+Number(f.get('expiryDays')||0)*86400000;
   else if(expMode==='date'){if(!f.get('expiry'))throw Error(L('Choose an expiry date.','تاریخ انقضا را انتخاب کن.'));expiryTime=new Date(f.get('expiry')+'Z').getTime();}
   let resetMode=f.get('resetMode'),resetDays=Number(f.get('resetDays')||0),resetDay=Number(f.get('resetTrafficDay')||0);
   if(resetMode==='interval'&&resetDays<1)throw Error(L('Custom reset needs at least 1 day.','ریست سفارشی حداقل ۱ روز می‌خواهد.'));
   if(resetMode==='monthly'&&(resetDay<1||resetDay>31))throw Error(L('Monthly reset day must be 1..31.','روز ریست ماهانه باید ۱ تا ۳۱ باشد.'));
   const compatible=flowCompatible(selected),flow=compatible?String(f.get('flow')||''):'';
   const payload={totalGB:planMode==='limited'?Math.round(Number(f.get('totalGB')||0)*gb):0,expiryTime,limitIp:Number(f.get('limitIp')||0),limitHwid:Number(c.limitHwid||0),enable:f.get('enable')==='true',group:String(f.get('group')||''),comment:String(f.get('comment')||''),flow,reset:resetMode==='interval'?resetDays:0,resetTraffic:['daily','weekly','monthly'].includes(resetMode)?resetMode:'never',resetTrafficDay:resetMode==='monthly'?resetDay:0,resetCount:Number(c.resetCount||0)};
   let result;if(email)result=await api('/api/clients/'+enc(email),'PATCH',{client:payload,inboundIds:selected});else{payload.email=String(f.get('email')||'').trim();result=await api('/api/clients','POST',{owner:f.get('owner'),client:payload,inboundIds:selected});}
   closeDialog();toast(L('Client saved.','کاربر ذخیره شد.'));await refresh();
 },email?L('Save changes','ذخیره تغییرات'):L('Create client','ساخت کاربر'));
 decorateEditor();
 const form=document.querySelector('#dialog-form');
 function sync(){
   const pm=form.querySelector('[name=planMode]:checked')?.value||'unlimited',em=form.querySelector('[name=expiryMode]:checked')?.value||'unlimited';
   form.querySelector('[data-cv4-quota]')?.classList.toggle('hidden',pm!=='limited');form.querySelector('[data-cv4-days]')?.classList.toggle('hidden',em!=='days');form.querySelector('[data-cv4-date]')?.classList.toggle('hidden',em!=='date');
   const selected=[...form.querySelectorAll('input[name=inbound]:checked')].map(x=>Number(x.value)),ok=flowCompatible(selected),flow=form.querySelector('[data-cv4-flow]'),note=form.querySelector('[data-cv4-flow-note]');
   if(flow){flow.classList.toggle('disabled',!ok);flow.querySelector('select').disabled=!ok;if(!ok)flow.querySelector('select').value='';}if(note)note.textContent=ok?L('Available for selected VLESS TCP/RAW TLS/REALITY inbounds.','برای اینباندهای VLESS TCP/RAW TLS/REALITY انتخاب‌شده فعال است.'):L('Hidden for gRPC/XHTTP/other incompatible transports.','برای gRPC/XHTTP و انتقال ناسازگار غیرفعال است.');
   form.querySelectorAll('.cv4-inbound-picker label').forEach(l=>l.classList.toggle('checked',!!l.querySelector('input:checked')));
 }
 form.addEventListener('change',ev=>{
   if(ev.target.name==='owner'){const own=ev.target.value;form.querySelector('#cv4-inbounds').innerHTML=inboundTiles([],own);const gs=form.querySelector('[name=group]');gs.innerHTML=opt([['',L('Ungrouped','بدون گروه')],...groupOptions(own)],'');}
   sync();
 });sync();
}
clientForm=clientFormV4;

async function detail(id){
 const r=await api('/api/clients/'+enc(id)),c=r.client||{},ins=inboundNames(r),used=Number(r.used_bytes||0),total=Number(c.totalGB||0),expiry=c.expiryTime>0?new Date(c.expiryTime).toLocaleString():L('Unlimited','نامحدود');
 dialog(L('Client command','فرمان کاربر')+' · '+id,`<div class="cv4-detail"><header><span class="cv4-avatar big">${e(id.slice(0,1).toUpperCase())}</span><div><small>${e(r.owner)} · ${e(c.group||L('Ungrouped','بدون گروه'))}</small><h2>${e(id)}</h2></div>${signal(r)}</header><div class="cv4-detail-grid"><section><span>${L('USAGE','مصرف')}</span><b>${bytes(used)} / ${total?bytes(total):L('Unlimited','نامحدود')}</b>${total?`<div class="cv4-meter"><i style="width:${pct(used,total)}%"></i></div>`:''}</section><section><span>${L('EXPIRY','انقضا')}</span><b>${e(expiry)}</b><small>${statusOf(r)==='active'?L('Service active','سرویس فعال'):statusOf(r)==='blocked'?L('Service limited','سرویس محدود'):L('Service disabled','سرویس قطع')}</small></section><section class="wide"><span>${L('SERVICE','سرویس')}</span><div class="cv4-tags">${ins.map(x=>`<i>${e(x)}</i>`).join('')}</div></section></div><footer>${can('clients.credentials',r.owner)?button(L('QR & Links','QR و لینک'),'cv3links','link',`data-id="${e(id)}"`,true):''}${can('clients.edit',r.owner)?button(L('Edit','ویرایش'),'cv4edit','edit',`data-id="${e(id)}"`):''}</footer></div>`);document.querySelector('#overlay .dialog')?.classList.add('cv4-detail-dialog');
}
async function bulkCreate(){
 const owner=defaultOwner();
 dialog(L('Bulk create','ساخت گروهی'),`<div class="cv4-editor-shell"><section class="cv4-edit-section"><header><span>01</span><div><b>${L('Batch','دسته')}</b><small>${L('Create many clients with one clean template.','چند کاربر با یک الگوی ساده بساز.')}</small></div></header><div class="cv4-edit-grid">${isOwner()&&ownerOptions().length>1?fSelect(L('Owner','مالک'),'owner',ownerOptions(),owner):`<input type="hidden" name="owner" value="${e(owner)}">`}${fInput(L('Quantity','تعداد'),'quantity',10,'number','min="1" max="500" required')}${fInput(L('Prefix','پیشوند'),'prefix','dark-','text','maxlength="64"')}${fInput(L('Start number','شماره شروع'),'first',1,'number','min="0" max="999999"')}</div></section><section class="cv4-edit-section"><header><span>02</span><div><b>${L('Service','سرویس')}</b></div></header><div id="cv4-inbounds">${inboundTiles([],owner)}</div></section><section class="cv4-edit-section"><header><span>03</span><div><b>${L('Plan','پلن')}</b></div></header><div class="cv4-edit-grid">${fInput(L('Quota GiB · 0 unlimited','حجم GiB · صفر نامحدود'),'totalGB',0,'number','min="0" step="0.1"')}${fInput(L('Expiry days · 0 unlimited','روز اعتبار · صفر نامحدود'),'days',0,'number','min="0" max="36500"')}${fInput(L('IP limit · 0 unlimited','محدودیت IP'),'limitIp',1,'number','min="0" max="1000"')}</div></section></div>`,async f=>{const selected=f.getAll('inbound').map(Number);if(!selected.length)throw Error(L('Select at least one inbound.','حداقل یک اینباند انتخاب کن.'));const days=Number(f.get('days')||0),client={totalGB:Math.round(Number(f.get('totalGB')||0)*gb),limitIp:Number(f.get('limitIp')||0)};if(days)client.expiryTime=Date.now()+days*86400000;const out=await api('/api/clients/bulk-create','POST',{owner:f.get('owner'),prefix:f.get('prefix'),postfix:'',first:Number(f.get('first')||0),quantity:Number(f.get('quantity')||0),inboundIds:selected,client});closeDialog();dialog(L('Bulk result','نتیجه ساخت گروهی'),jsonBox(out));await refresh();},L('Create batch','ساخت دسته'));decorateEditor();
}
runAction=async function(act,el){
 const id=el?.dataset?.id;
 if(act==='cv4view'){state.cv4.view=el.dataset.view;state.selected.clear();return renderPage();}
 if(act==='cv4filters'){state.cv4.filters=!state.cv4.filters;return renderPage();}
 if(act==='cv4quick'){if(el.dataset.key==='presence'){state.cv4.presence=el.dataset.value;state.cv4.status='all';}else{state.cv4.status=el.dataset.value;state.cv4.presence='all';}state.selected.clear();return renderPage();}
 if(act==='cv4reset'){Object.assign(state.cv4,{presence:'all',status:'all',owner:'all',inbound:'all',group:'all',sort:'activity'});state.selected.clear();return renderPage();}
 if(act==='cv4detail')return detail(id);
 if(act==='cv4bulk')return bulkCreate();
 if(act==='cv4edit'){closeDialog();return clientFormV4(id);}
 return baseRunAction(act,el);
};
document.addEventListener('change',async ev=>{
 const el=ev.target;
 if(el?.dataset?.cv4Filter){state.cv4[el.dataset.cv4Filter]=el.value;if(el.dataset.cv4Filter==='owner'&&state.cv4.group!=='all'){const valid=new Set(groups(el.value).map(g=>groupKey(g.owner,g.name)));valid.add('__ungrouped');if(!valid.has(state.cv4.group))state.cv4.group='all';}state.selected.clear();await renderPage();}
 if(el?.id==='cv4-sort'){state.cv4.sort=el.value;await renderPage();}
});
})();