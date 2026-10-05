/* Owner-only node ingress -> encrypted final exit workspace. */
(function(){
'use strict';
const oldNav=navItems,oldPage=enginePage,oldAction=runAction;
const L=(en,fa)=>(localStorage.getItem('dark_lang')||'en')==='fa'?fa:en;
const S={data:null,busy:false,source:''};
const stamp=t=>t?new Date(t*1000).toLocaleString((localStorage.getItem('dark_lang')||'en')==='fa'?'fa-IR':'en-US'):'—';
const writable=()=>isOwner()&&state.me?.writes_enabled===true;
const url=r=>'/api/nodes/'+enc(r.sourceNodeId)+'/exits/'+r.sourceInboundId;
const phase=r=>({disabled:L('Disabled','خاموش'),enabled:L('Enabled','فعال'),enabling:L('Enable pending','در انتظار فعال‌سازی'),disabling:L('Disable pending','در انتظار خاموش‌شدن'),deleting:L('Delete pending','در انتظار حذف')}[r.phase]||r.phase);
enginePages.swap=['DARK SWAP'];
navItems=function(){const n=oldNav();if(isOwner()&&!n.some(x=>x[0]==='swap')){const i=n.findIndex(x=>x[0]==='nodes');n.splice(i<0?n.length:i+1,0,['swap','DARK SWAP','node']);}return n;};
function action(label,act,r,disabled=false){return `<button class="btn" data-act="${act}" data-source="${e(r.sourceNodeId)}" data-inbound="${r.sourceInboundId}" ${disabled?'disabled':''}>${label}</button>`;}
function nodeLabel(id){return S.data.nodes.find(n=>n.id===id)?.name||id;}
function nodeHealth(id){const n=S.data.nodes.find(n=>n.id===id);return n?`${e(nodeLabel(id))} · ${e(n.telemetry_state||'offline')} · ${L('last seen','آخرین مشاهده')}: ${e(stamp(n.last_seen))}`:L('Missing node','نود حذف‌شده');}
function routeCard(r){
 const p=r.lastProbe,source=r.sourceState||{},exit=r.exitState||{},pending=source.pending||exit.pending;
 const errors=[r.configurationError,source.last_error,exit.last_error].filter(Boolean);
 return `<article class="swap-card" data-swap-route="${e(r.sourceNodeId)}:${r.sourceInboundId}"><div class="swap-card-head"><h2 dir="ltr">${e(nodeLabel(r.sourceNodeId))} #${r.sourceInboundId} → ${e(nodeLabel(r.exitNodeId))} #${r.exitInboundId}</h2><span class="tag ${r.phase==='enabled'&&!errors.length&&!pending?'green':'amber'}" data-swap-phase>${e(phase(r))}</span></div><p>${nodeHealth(r.sourceNodeId)}</p><p>${nodeHealth(r.exitNodeId)}</p><p>${L('Configuration ACK (source / exit)','تأیید تنظیمات (مبدأ / مقصد)')}: ${source.applied_revision||0}/${source.revision||0} · ${exit.applied_revision||0}/${exit.revision||0} ${pending?L('· pending','· در انتظار'):''}</p>${errors.length?`<div class="notice error">${errors.map(e).join('<br>')}</div>`:''}<div class="swap-probe">${p?`${p.success?L('Last probe succeeded','آخرین تست موفق'):L('Last probe failed','آخرین تست ناموفق')} · ${e(stamp(p.checkedAt))}<br>${L('Observed exit IP','IP خروجی مشاهده‌شده')}: <b dir="ltr">${e(p.exitIp||'—')}</b> · ${p.delayMs==null?'—':e(p.delayMs)+' ms'}${p.error?'<br>'+e(p.error):''}`:L('No path probe yet; node health is not proof of the data path.','مسیر هنوز تست نشده؛ سلامت نود به معنی تأیید مسیر اینترنت نیست.')}</div>${writable()?`<div class="swap-actions">${r.phase!=='deleting'?action(L('Enable / retry','فعال‌سازی / تلاش مجدد'),'swapenable',r)+action(L('Disable / retry','خاموش / تلاش مجدد'),'swapdisable',r):''}${action(L('Test exit IP','تست IP خروجی'),'swapprobe',r,r.phase!=='enabled'||pending||!!r.configurationError)}${action(L('Edit destination','ویرایش مقصد'),'swapedit',r,r.phase!=='disabled')}${action(L('Delete / retry','حذف / تلاش مجدد'),'swapdelete',r,!['disabled','deleting'].includes(r.phase))}</div>`:''}</article>`;
}
enginePage=async function(){
 if(state.page!=='swap')return oldPage();
 if(!isOwner())return heading('DARK SWAP',L('Owner only','فقط مالک'));
 const ownerId=state.me.id,data=await api('/api/swap');
 if(!isOwner()||state.me?.id!==ownerId)return '';
 S.data=data;
 return heading('DARK SWAP',L('Strong tunnel ingress → your selected internet exit','ورودی با تونل قوی ← خروج اینترنت از لوکیشن انتخابی'),writable()?'<button class="btn primary" data-act="swapnew">'+L('+ New route','+ مسیر جدید')+'</button>':'')+`<div class="notice">${L('Example: Iran → Netherlands tunnel → Germany → internet with Germany IP. One destination per source inbound; use separate source inbounds for multiple exit locations. Customer links stay unchanged.','مثال: ایران ← تونل هلند ← آلمان ← اینترنت با IP آلمان. هر اینباند مبدأ یک مقصد دارد؛ برای چند لوکیشن خروجی، اینباندهای مبدأ جدا انتخاب کن. لینک مشتری تغییر نمی‌کند.')}</div><div class="notice warning">${L('Saves are disabled. Enable may reconnect sessions. No direct fallback if the exit fails. Usage is billed once at ingress. Probes test source → exit, not the Iran tunnel; their result is a dated observation, not continuous health.','مسیر پس از ذخیره خاموش است. فعال‌سازی ممکن است اتصال‌ها را دوباره برقرار کند. خرابی مقصد خروج مستقیم جایگزین ندارد. مصرف فقط در مبدأ حساب می‌شود. تست مربوط به مبدأ تا مقصد است، نه تونل ایران؛ نتیجه مربوط به زمان تست است، نه سلامت لحظه‌ای.')}</div><div class="swap-toolbar"><button class="btn" data-act="swaprefresh">${L('Refresh status','تازه‌سازی وضعیت')}</button><span>${data.routes.length} ${L('routes','مسیر')} · ${data.routes.filter(r=>r.phase==='enabled').length} ${L('enabled','فعال')}</span></div><div class="swap-routes">${data.routes.map(routeCard).join('')||'<div class="notice">'+L('No routes. Existing customer routing is unchanged.','مسیر SWAP تعریف نشده؛ مسیر فعلی مشتری‌ها تغییر نکرده است.')+'</div>'}</div>`;
};
async function routeForm(existing=null){
 if(!writable())return;
 const ownerId=state.me.id,data=await api('/api/swap');if(!writable()||state.me.id!==ownerId)return;
 S.data=data;
 const nodes=data.nodes.filter(n=>n.enabled),byId=new Map(data.inbounds.map(i=>[i.id,i]));
 const exits=n=>n.inboundIds.map(id=>byId.get(id)).filter(i=>i&&i.enable!==false&&i.protocol==='vless'&&i.security==='reality'&&['tcp','raw','grpc'].includes(i.network)&&['0.0.0.0','::',''].includes(i.listen)&&n.data_address);
 const nodeOptions=nodes.filter(n=>n.inboundIds.length).map(n=>`<option value="${e(n.id)}">${e(n.name||n.id)}</option>`).join('');
 if(!nodeOptions){toast(L('Assign inbounds to enabled nodes first.','ابتدا اینباند را به نودهای فعال تخصیص بده.'),true);return;}
 dialog('DARK SWAP · '+L(existing?'Edit destination':'New route',existing?'ویرایش مقصد':'مسیر جدید'),`<div class="swap-form"><label>${L('Source node (strong Iran tunnel)','نود مبدأ (دارای تونل قوی ایران)')}<select name="swapSource" ${existing?'disabled':''}>${nodeOptions}</select></label><label>${L('Source inbound','اینباند مبدأ')}<select name="swapInbound" ${existing?'disabled':''}></select></label><label>${L('Exit node (final IP)','نود مقصد (IP نهایی)')}<select name="swapDestination"></select></label><label>${L('Exit inbound','اینباند مقصد')}<select name="swapExitInbound"></select></label></div><div class="notice">${L('Destination requires a public VLESS Reality TCP/RAW or gRPC inbound. Saving never enables customer traffic.','مقصد باید اینباند عمومی VLESS Reality با TCP/RAW یا gRPC داشته باشد. ذخیره مسیر، ترافیک مشتری را فعال نمی‌کند.')}</div>`,async(fd,form)=>{
  if(!form.isConnected||!writable()||state.me.id!==ownerId)return;
  const source=existing?.sourceNodeId||String(fd.get('swapSource')),inbound=existing?.sourceInboundId||Number(fd.get('swapInbound'));
  if(!fd.get('swapDestination')||!fd.get('swapExitInbound')||!inbound)throw Error(L('No compatible destination selected.','مقصد سازگار انتخاب نشده است.'));
  await api(url({sourceNodeId:source,sourceInboundId:inbound}),'PUT',{exitNodeId:String(fd.get('swapDestination')),exitInboundId:Number(fd.get('swapExitInbound'))});
  if(!form.isConnected||!writable()||state.me.id!==ownerId)return;
  closeDialog();toast(L('Saved disabled. Enable when ready.','ذخیره شد؛ خاموش است.'));await go('swap');
 });
 const form=document.querySelector('#dialog-form'),src=form.querySelector('[name=swapSource]'),sib=form.querySelector('[name=swapInbound]'),dst=form.querySelector('[name=swapDestination]'),dib=form.querySelector('[name=swapExitInbound]');
 const ibOptions=list=>list.map(i=>`<option value="${i.id}">#${i.id} · ${e(i.remark||i.tag)} · ${e(i.network||'')} :${i.port}</option>`).join('');
 const fillExit=()=>{const node=nodes.find(n=>n.id===dst.value);dib.innerHTML=node?ibOptions(exits(node)):'';};
 const fillSource=()=>{const node=nodes.find(n=>n.id===src.value);sib.innerHTML=ibOptions((node?.inboundIds||[]).map(id=>byId.get(id)).filter(i=>i&&i.enable!==false));dst.innerHTML=nodes.filter(n=>n.id!==src.value&&exits(n).length).map(n=>`<option value="${e(n.id)}">${e(n.name||n.id)}</option>`).join('');fillExit();};
 if(existing||S.source)src.value=existing?.sourceNodeId||S.source;
 fillSource();if(existing){sib.value=String(existing.sourceInboundId);dst.value=existing.exitNodeId;fillExit();dib.value=String(existing.exitInboundId);}
 src.addEventListener('change',fillSource);dst.addEventListener('change',fillExit);
}
runAction=async function(act,el){
 if(act==='nv2exit'){if(!isOwner())return;S.source=el.dataset.id;closeDialog();await go('swap');await routeForm();return;}
 if(!act.startsWith('swap'))return oldAction(act,el);
 if(!isOwner()||S.busy)return;
 if(act==='swaprefresh'){await renderPage();return;}
 if(!writable())return;
 if(act==='swapnew'){S.source='';await routeForm();return;}
 const r=S.data?.routes.find(r=>r.sourceNodeId===el.dataset.source&&r.sourceInboundId===Number(el.dataset.inbound));if(!r)return;
 if(act==='swapedit'){await routeForm(r);return;}
 const prompts={swapenable:L('Enable this route? Active sessions may reconnect.','مسیر فعال شود؟ اتصال‌های جاری ممکن است دوباره برقرار شوند.'),swapdisable:L('Disable and return to previous routing?','مسیر خاموش شود و مسیر قبلی برگردد؟'),swapdelete:L('Delete this disabled route and revoke its internal destination credential?','مسیر خاموش حذف شود و دسترسی داخلی مقصد لغو شود؟')};
 if(prompts[act]&&!confirm(prompts[act]))return;
 const ownerId=state.me.id;S.busy=true;el.disabled=true;
 try{if(act==='swapprobe')await api(url(r)+'/probe','POST',{});else if(act==='swapdelete')await api(url(r),'DELETE');else if(['swapenable','swapdisable'].includes(act))await api(url(r),'POST',{enabled:act==='swapenable'});}
 finally{S.busy=false;if(isOwner()&&state.me.id===ownerId&&state.page==='swap')await renderPage();}
};
})();
