/* DARK XRAY Clients V3 — organized workspace, real activity presence and complete QR/share tools. */
(function(){
'use strict';
if(typeof clientsPage!=='function'||typeof runAction!=='function')return;
const previousClientsPage=clientsPage,previousRunAction=runAction;
state.cv3=state.cv3||{presence:'all',sort:'activity',linkModal:null};
const L=(en,fa)=>((localStorage.getItem('dark_lang')||'en')==='fa'?fa:en);
const pct=(used,total)=>total?Math.min(100,Math.max(0,100*Number(used||0)/Number(total))):0;
function statusOf(r){const reasons=r.block_reasons||[];if(reasons.includes('client_manual'))return'disabled';if(reasons.length)return'blocked';if(r.client?.enable===false||r.observed_enable===false)return'disabled';return'active';}
function presenceOf(r){return ['online','idle','offline'].includes(r.presence_state)?r.presence_state:'offline';}
function ago(seconds){if(seconds==null)return L('Never seen','هنوز دیده نشده');seconds=Math.max(0,Number(seconds)||0);if(seconds<60)return L('just now','همین الان');if(seconds<3600)return Math.floor(seconds/60)+'m '+L('ago','قبل');if(seconds<86400)return Math.floor(seconds/3600)+'h '+L('ago','قبل');return Math.floor(seconds/86400)+'d '+L('ago','قبل');}
function presenceBadge(r){const p=presenceOf(r),labels={online:L('ONLINE','آنلاین'),idle:L('IDLE','کم‌فعال'),offline:L('OFFLINE','آفلاین')};return `<div class="cv3-presence ${p}" title="${e(r.activity_at?new Date(r.activity_at*1000).toLocaleString():L('No observed client traffic yet','هنوز ترافیک واقعی از این کاربر دیده نشده'))}"><span class="cv3-pdot"></span><span class="cv3-pline"><i></i></span><b>${labels[p]}</b><small>${e(ago(r.presence_age_seconds))}</small></div>`;}
function chip(label,key,value,current){return `<button type="button" class="cv2-chip ${String(current)===String(value)?'active':''}" data-act="cv3filter" data-key="${e(key)}" data-value="${e(value)}">${e(label)}</button>`;}
function action(ic,title,act,id){return `<button class="cv2-icon" data-act="${act}" data-id="${e(id)}" title="${e(title)}" aria-label="${e(title)}">${icon(ic)}</button>`;}
function inboundNames(r){return (r.inboundIds||[]).map(id=>{const x=state.inbounds.find(i=>i.id===id);return x?(x.remark||x.tag):'#'+id;});}
function filteredV3(){
 let f=state.cv2||{},rows=filtered(state.clients);
 if(f.search!==state.search){state.selected.clear();f.search=state.search;}
 if(f.owner&&f.owner!=='all')rows=rows.filter(x=>x.owner===f.owner);
 if(f.group!==undefined&&f.group!=='all')rows=rows.filter(x=>(x.client?.group||'')===f.group&&(f.groupOwner==='all'||!f.groupOwner||x.owner===f.groupOwner));
 if(f.status&&f.status!=='all')rows=rows.filter(x=>statusOf(x)===f.status);
 if(f.inbound&&f.inbound!=='all')rows=rows.filter(x=>x.inboundIds?.includes(Number(f.inbound)));
 if(state.cv3.presence!=='all')rows=rows.filter(x=>presenceOf(x)===state.cv3.presence);
 const mode=state.cv3.sort;
 rows=[...rows].sort((a,b)=>{
   if(mode==='name')return a.email.localeCompare(b.email);
   if(mode==='traffic')return Number(b.used_bytes||0)-Number(a.used_bytes||0);
   if(mode==='expiry'){const ae=Number(a.client?.expiryTime||0)||Number.MAX_SAFE_INTEGER,be=Number(b.client?.expiryTime||0)||Number.MAX_SAFE_INTEGER;return ae-be;}
   return Number(b.activity_at||0)-Number(a.activity_at||0)||a.email.localeCompare(b.email);
 });
 return rows;
}
function rowV3(r){
 const c=r.client||{},used=Number(r.used_bytes||0),total=Number(c.totalGB||0),progress=pct(used,total);
 const exp=c.expiryTime>0?new Date(c.expiryTime).toLocaleDateString((localStorage.getItem('dark_lang')||'en')==='fa'?'fa-IR':'en-US'):L('Unlimited','نامحدود');
 const group=c.group||L('Ungrouped','بدون گروه'),ins=inboundNames(r);
 return `<div class="cv3-row">
   <div><input type="checkbox" data-select="${e(r.email)}" ${state.selected.has(r.email)?'checked':''} aria-label="${e(r.email)}"></div>
   <button class="cv3-identity" data-act="cv3detail" data-id="${e(r.email)}"><b>${e(r.email)}</b><small>${e(r.owner)} · ${e(group)}</small></button>
   <div class="cv3-state">${presenceBadge(r)}<div class="cv3-policy">${badge(r)}</div></div>
   <div class="cv3-inbounds">${ins.slice(0,2).map(x=>`<span>${e(x)}</span>`).join('')}${ins.length>2?`<small>+${ins.length-2}</small>`:''}</div>
   <div class="cv3-traffic"><b class="mono">${bytes(used)}</b><small>${total?bytes(total):L('Unlimited','نامحدود')}</small>${total?`<div class="cv2-progress"><i style="width:${progress}%"></i></div>`:''}</div>
   <div class="cv3-expiry"><b>${e(exp)}</b><small>IP ${c.limitIp||'∞'} · HWID ${c.limitHwid||'∞'}</small></div>
   <div class="cv2-actions">${action('info',L('Details','جزئیات'),'cv3detail',r.email)}${can('clients.edit',r.owner)?action('edit',L('Edit','ویرایش'),'edit',r.email):''}${can('clients.credentials',r.owner)?action('link',L('Links & QR','لینک و QR'),'cv3links',r.email):''}${can('clients.ip',r.owner)?action('shield','IP / HWID','ips',r.email):''}${can('clients.edit',r.owner)?action('power',c.enable===false?L('Enable','فعال‌سازی'):L('Disable','قطع'),'toggle',r.email):''}</div>
 </div>`;
}
function groupPanel(){
 const groups=state.groups||[],f=state.cv2||{};
 return `<aside class="cv3-side"><article class="panel cv3-side-card"><div class="cv3-side-title"><h3>${L('Organization','سازماندهی')}</h3><span>${groups.length}</span></div>
 <div class="cv2-group ${f.group==='all'?'active':''}" data-act="cv2filter" data-key="group" data-value="all" data-owner="all"><i class="cv2-dot"></i><span>${L('All clients','همه کاربران')}</span><b>${state.clients.length}</b></div>
 <div class="cv2-group ${f.group===''&&f.groupOwner==='all'?'active':''}" data-act="cv2filter" data-key="group" data-value="" data-owner="all"><i class="cv2-dot" style="--group:#64748b"></i><span>${L('Ungrouped','بدون گروه')}</span><b>${state.clients.filter(x=>!x.client?.group).length}</b></div>
 ${groups.map(g=>`<div class="cv2-group ${f.group===g.name&&f.groupOwner===g.owner?'active':''}" data-act="cv2filter" data-key="group" data-value="${e(g.name)}" data-owner="${e(g.owner)}"><i class="cv2-dot" style="--group:${e(g.color||'#22d3ee')}"></i><span>${e(g.name)}</span><b>${g.client_count||0}</b></div>`).join('')}
 <div class="cv3-side-actions">${can('clients.edit')?button(L('Manage groups','مدیریت گروه‌ها'),'cv2groups','users'):''}</div></article>
 <article class="panel cv3-side-card"><div class="cv3-side-title"><h3>${L('Presence','وضعیت اتصال')}</h3></div><div class="cv2-filterbar">${chip(L('All','همه'),'presence','all',state.cv3.presence)}${chip(L('Online','آنلاین'),'presence','online',state.cv3.presence)}${chip(L('Idle','کم‌فعال'),'presence','idle',state.cv3.presence)}${chip(L('Offline','آفلاین'),'presence','offline',state.cv3.presence)}</div></article></aside>`;
}
function options(items,current,label){return items.map(([v,t])=>`<option value="${e(v)}" ${String(v)===String(current)?'selected':''}>${e(t)}</option>`).join('');}
clientsPage=function(){
 if(state.cv2?.view==='groups')return previousClientsPage();
 const rows=filteredV3(),online=state.clients.filter(x=>presenceOf(x)==='online').length,active=state.clients.filter(x=>statusOf(x)==='active').length,blocked=state.clients.filter(x=>statusOf(x)==='blocked').length,totalUsed=state.clients.reduce((a,x)=>a+Number(x.used_bytes||0),0);
 const ownerOpts=[['all',L('All owners','همه مالک‌ها')],...(isOwner()?state.owners.map(o=>[o.id,o.name||o.id]):[])];
 const inboundOpts=[['all',L('All inbounds','همه اینباندها')],...state.inbounds.map(i=>[String(i.id),i.remark||i.tag])];
 const statusOpts=[['all',L('All states','همه وضعیت‌ها')],['active',L('Active','فعال')],['blocked',L('Blocked','محدود')],['disabled',L('Disabled','قطع')]];
 const sortOpts=[['activity',L('Recent activity','آخرین فعالیت')],['name',L('Name','نام')],['traffic',L('Traffic','مصرف')],['expiry',L('Expiry','انقضا')]];
 return heading(L('Clients','کاربران'),L('Live activity, organization, credentials and bulk control in one workspace.','فعالیت زنده، سازماندهی، کانفیگ و کنترل گروهی در یک محیط.'),`${can('clients.create')?button(L('Bulk add','افزودن گروهی'),'cv2bulkadd','users'):''}${can('clients.create')?button(L('New client','کاربر جدید'),'new','plus','',true):''}`)+notices()+`
 <div class="clients-v2 clients-v3">
  <div class="cv2-tabs"><button class="active" data-act="cv2view" data-view="clients">${L('Clients','کاربران')}</button><button data-act="cv2view" data-view="groups">${L('Groups','گروه‌ها')}</button></div>
  <div class="cv3-summary">
   <div class="cv3-stat"><small>${L('TOTAL','کل')}</small><b>${fa(state.clients.length)}</b><i></i></div>
   <div class="cv3-stat live"><small>${L('ONLINE','آنلاین')}</small><b>${fa(online)}</b><i></i></div>
   <div class="cv3-stat"><small>${L('ACTIVE','فعال')}</small><b>${fa(active)}</b><i></i></div>
   <div class="cv3-stat warn"><small>${L('BLOCKED','محدود')}</small><b>${fa(blocked)}</b><i></i></div>
   <div class="cv3-stat"><small>${L('TRAFFIC','مصرف')}</small><b>${bytes(totalUsed)}</b><i></i></div>
  </div>
  <div class="cv3-board"><section class="panel cv3-list">
   <div class="cv3-toolbar">
    <div class="cv3-search-wrap">${icon('search')}<input class="field-input cv2-search" id="search" type="search" placeholder="${L('Search client / owner','جستجوی کاربر / مالک')}" value="${e(state.search)}"></div>
    <select data-cv3-filter="owner">${options(ownerOpts,state.cv2.owner||'all','')}</select>
    <select data-cv3-filter="inbound">${options(inboundOpts,state.cv2.inbound||'all','')}</select>
    <select data-cv3-filter="status">${options(statusOpts,state.cv2.status||'all','')}</select>
    <select id="cv3-sort">${options(sortOpts,state.cv3.sort,'')}</select>
    <button class="btn" data-act="cv3clear">${icon('refresh')}${L('Reset','ریست')}</button>
   </div>
   ${state.selected.size?`<div class="cv2-bulkbar"><b>${state.selected.size} ${L('selected','انتخاب')}</b><div class="spacer"></div>${button(L('Adjust','تغییر گروهی'),'cv2bulkadjust','edit')}${button(L('Attach / detach','اتصال اینباند'),'cv2bulkinbounds','server')}${button(L('Enable / disable / reset','وضعیت'),'bulk','power')}</div>`:''}
   <div class="cv3-head"><div></div><div>${L('Client','کاربر')}</div><div>${L('Live / state','اتصال / وضعیت')}</div><div>${L('Inbounds','اینباندها')}</div><div>${L('Traffic','مصرف')}</div><div>${L('Expiry / limits','انقضا / محدودیت')}</div><div>${L('Actions','عملیات')}</div></div>
   <div class="cv3-rows">${rows.length?rows.map(rowV3).join(''):`<div class="cv2-empty">${L('No clients match these filters.','کاربری با این فیلترها پیدا نشد.')}</div>`}</div>
  </section>${groupPanel()}</div>
 </div>`;
};
async function detailV3(id){
 const r=await api('/api/clients/'+enc(id)),c=r.client||{},ins=inboundNames(r),used=Number(r.used_bytes||0),total=Number(c.totalGB||0),p=pct(used,total);
 const expiry=c.expiryTime>0?new Date(c.expiryTime).toLocaleString():L('Unlimited','نامحدود');
 dialog(L('Client control','کنترل کاربر')+' · '+id,`<div class="cv3-detail">
  <div class="cv3-detail-hero"><div><span class="cv3-kicker">${e(r.owner)} · ${e(c.group||L('Ungrouped','بدون گروه'))}</span><h2>${e(id)}</h2></div>${presenceBadge(r)}</div>
  <div class="cv3-detail-grid">
   <section><small>${L('STATE','وضعیت')}</small><div>${badge(r)}</div><p>${e(r.error||L('No sync error','بدون خطای همگام‌سازی'))}</p></section>
   <section><small>${L('TRAFFIC','مصرف')}</small><b>${bytes(used)} / ${total?bytes(total):L('Unlimited','نامحدود')}</b>${total?`<div class="cv2-progress"><i style="width:${p}%"></i></div>`:''}</section>
   <section><small>${L('EXPIRY','انقضا')}</small><b>${e(expiry)}</b><p>IP ${c.limitIp||'∞'} · HWID ${c.limitHwid||'∞'}</p></section>
   <section><small>${L('INBOUNDS','اینباندها')}</small><div class="cv3-inbound-tags">${ins.map(x=>`<span>${e(x)}</span>`).join('')}</div></section>
  </div>
  <div class="cv3-detail-actions">${can('clients.edit',r.owner)?button(L('Edit client','ویرایش'),'edit','edit',`data-id="${e(id)}"`):''}${can('clients.credentials',r.owner)?button(L('Links & QR','لینک و QR'),'cv3links','link',`data-id="${e(id)}"`,true):''}${can('clients.ip',r.owner)?button('IP / HWID','ips','shield',`data-id="${e(id)}"`):''}</div>
 </div>`);
}
function linkPayload(kind,index){
 const m=state.cv3.linkModal;if(!m)return'';
 if(kind==='sub')return m.result.subscription_url||'';
 const row=m.result.engine?.links?.[Number(index)]||{};return row.uri||'';
}
function renderQrV3(kind='sub',index=0){
 const payload=linkPayload(kind,index),box=document.querySelector('#cv3-qr');if(!box)return;
 state.cv3.linkModal.selected={kind,index:Number(index)||0};
 if(!payload){box.innerHTML=`<div class="cv3-qr-error">${L('No QR payload','داده‌ای برای QR نیست')}</div>`;return;}
 try{const q=qrcode(0,'L');q.addData(payload);q.make();box.innerHTML=q.createSvgTag();const lab=document.querySelector('#cv3-qr-label');if(lab)lab.textContent=kind==='sub'?L('Subscription URL','لینک اشتراک'):(state.cv3.linkModal.result.engine.links[index]?.remark||L('Config link','لینک کانفیگ'));}
 catch(ex){box.innerHTML=`<div class="cv3-qr-error">${e(L('QR generation failed: ','ساخت QR ناموفق: ')+ex.message)}</div>`;}
}
async function linksV3(id){
 const result=await api('/api/clients/'+enc(id)+'/links'),links=result.engine?.links||[];state.cv3.linkModal={id,result,selected:{kind:result.subscription_url?'sub':'link',index:0}};
 const sub=result.subscription_url?`<article class="cv3-link-card featured"><div><small>${L('SUBSCRIPTION','اشتراک')}</small><b>${e(result.subscription_url)}</b></div><div class="cv3-link-actions"><button data-act="cv3copy" data-kind="sub">${icon('copy')}${L('Copy','کپی')}</button><button data-act="cv3qrselect" data-kind="sub">${icon('qr')||icon('grid') }QR</button></div></article>`:'';
 const cards=links.map((x,i)=>`<article class="cv3-link-card"><div><small>#${x.inboundId} · ${e(x.remark||L('Config','کانفیگ'))}</small><b class="mono">${e(x.uri)}</b></div><div class="cv3-link-actions"><button data-act="cv3copy" data-kind="link" data-index="${i}">${icon('copy')}${L('Copy','کپی')}</button><button data-act="cv3qrselect" data-kind="link" data-index="${i}">QR</button></div></article>`).join('');
 const warnings=(result.engine?.warnings||[]).map(x=>`<div class="notice warning">${e(x)}</div>`).join('');
 dialog(L('Links & QR','لینک‌ها و QR')+' · '+id,`<div class="cv3-links"><div class="cv3-link-list">${sub}${cards||empty(L('No generated links.','لینکی ساخته نشده.'))}${warnings}</div><aside class="cv3-qr-panel"><div id="cv3-qr"></div><b id="cv3-qr-label"></b><div class="cv3-qr-actions"><button class="btn" data-act="cv3downloadqr">${L('Download SVG','ذخیره SVG')}</button></div><small>${L('QR contains the exact selected subscription/config link.','QR دقیقاً همان لینک انتخاب‌شده را در خود دارد.')}</small></aside></div>`);
 renderQrV3(result.subscription_url?'sub':'link',0);
}
async function copyV3(text){if(!text)return;try{await navigator.clipboard.writeText(text);toast(L('Copied.','کپی شد.'));}catch(_){const ta=document.createElement('textarea');ta.value=text;document.body.appendChild(ta);ta.select();document.execCommand('copy');ta.remove();toast(L('Copied.','کپی شد.'));}}
function downloadQr(){const svg=document.querySelector('#cv3-qr svg');if(!svg)return toast(L('No QR to download.','QR برای ذخیره وجود ندارد.'),true);const blob=new Blob([svg.outerHTML],{type:'image/svg+xml'}),url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download='dark-xray-'+(state.cv3.linkModal?.id||'client')+'.svg';a.click();setTimeout(()=>URL.revokeObjectURL(url),500);}
runAction=async function(act,el){
 if(act==='cv3detail')return detailV3(el.dataset.id);
 if(act==='cv3links')return linksV3(el.dataset.id);
 if(act==='cv3filter'){const key=el.dataset.key,value=el.dataset.value;if(key==='presence')state.cv3.presence=value;else state.cv2[key]=value;state.selected.clear();return renderPage();}
 if(act==='cv3clear'){state.cv3.presence='all';state.cv3.sort='activity';Object.assign(state.cv2,{owner:'all',group:'all',groupOwner:'all',status:'all',inbound:'all'});state.selected.clear();return renderPage();}
 if(act==='cv3copy')return copyV3(linkPayload(el.dataset.kind,el.dataset.index||0));
 if(act==='cv3qrselect')return renderQrV3(el.dataset.kind,Number(el.dataset.index||0));
 if(act==='cv3downloadqr')return downloadQr();
 return previousRunAction(act,el);
};
document.addEventListener('change',async ev=>{
 const el=ev.target;
 if(el?.dataset?.cv3Filter){state.cv2[el.dataset.cv3Filter]=el.value;state.selected.clear();await renderPage();return;}
 if(el?.id==='cv3-sort'){state.cv3.sort=el.value;await renderPage();}
});
})();