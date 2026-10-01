/* DARK XRAY: one owner Xray Settings workspace, existing engines and policies. */
(function(){
'use strict';
if(typeof navItems!=='function'||typeof enginePage!=='function'||typeof runAction!=='function')return;
const pageId='trafficmatrix';
const groupedPages=new Set(['xray','outbounds','routing',pageId]);
const L=(en,fa)=>((localStorage.getItem('dark_lang')||'en')==='fa'?fa:en);
const title=()=>L('WARP / AdBlock','WARP / حذف تبلیغ');
const settingsTitle=()=>L('Xray Settings','تنظیمات Xray');
const previousNav=navItems,previousEnginePage=enginePage,previousAction=runAction;
state.warpCenter=state.warpCenter||{server:'hub',items:[],scannedServer:''};
const WC=state.warpCenter;
const flag=code=>{const c=String(code||'').toUpperCase();return /^[A-Z]{2}$/.test(c)?String.fromCodePoint(...[...c].map(x=>127397+x.charCodeAt(0))):'🌐';};
enginePages[pageId]=[title()];
function sections(){return [
 ['xray',L('Core & DNS','هسته و DNS'),'terminal'],
 ['outbounds',L('Outbounds','اوتباندها'),'arrow'],
 ['routing',L('Routing','روتینگ'),'node'],
 [pageId,title(),'activity']
];}
// Internal page IDs remain compatible with existing shortcuts and editors.
// Only the primary sidebar is consolidated; no API or routing state changes.
navItems=function(){
 return previousNav().filter(row=>!['outbounds','routing',pageId].includes(row[0])&&
   (row[0]!=='xray'||isOwner())).map(row=>row[0]==='xray'?[row[0],settingsTitle(),row[2]]:row);
};
if(typeof shell==='function'){
 const previousShell=shell;
 shell=function(){
  const result=previousShell();
  if(isOwner()&&groupedPages.has(state.page)){
   document.querySelectorAll('.sidebar .nav-btn').forEach(button=>{
    const active=button.dataset.page==='xray';
    button.classList.toggle('active',active);
    if(active)button.setAttribute('aria-current','page');else button.removeAttribute('aria-current');
   });
   const breadcrumb=document.querySelector('.topbar .breadcrumb b');
   const section=sections().find(row=>row[0]===state.page);
   if(breadcrumb)breadcrumb.textContent=settingsTitle()+(state.page==='xray'?'':' / '+section[1]);
  }
  return result;
 };
}
function sectionNav(active){
 return '<nav class="xray-settings-nav" aria-label="'+e(settingsTitle())+'">'+sections().map(([id,label,ic])=>
  '<button type="button" data-page="'+id+'" class="'+(active===id?'active':'')+'"'+
  (active===id?' aria-current="page"':'')+'>'+icon(ic)+'<span>'+e(label)+'</span></button>'
 ).join('')+'</nav>';
}
function runtimeOptions(items){
 return (items||[]).map(r=>'<option value="'+e(r.id)+'" '+(r.id===WC.server?'selected':'')+' '+(r.online===false?'disabled':'')+'>'+e((r.kind==='hub'?'🖥️ ':'🌍 ')+(r.name||r.id)+(r.online===false?' · OFFLINE':''))+'</option>').join('');
}
function warpRows(){
 const rows=WC.scannedServer===WC.server?WC.items:[];
 if(!rows.length)return '<div class="xw-empty">'+L('No scan results yet. Select a runtime and scan WARP paths.','هنوز نتیجه‌ای نیست؛ سرور را انتخاب کن و مسیرهای WARP را اسکن کن.')+'</div>';
 return '<div class="xw-results">'+rows.map(x=>{
  const location=[flag(x.country),x.country,x.colo].filter(Boolean).join(' ');
  return '<article class="xw-path '+(x.ready?'ready':'failed')+'"><div class="xw-path-id"><b class="mono">'+e(x.endpoint)+'</b><small>'+e(location||'Cloudflare WARP')+(x.egressIp?' · '+e(x.egressIp):'')+'</small></div><div><span>PING</span><b>'+(x.delayMs==null?'—':Math.round(Number(x.delayMs))+' ms')+'</b></div><div><span>LOSS</span><b>'+(x.lossPercent==null?'—':e(x.lossPercent)+'%')+'</b></div><div><span>JITTER</span><b>'+(x.jitterMs==null?'—':Math.round(Number(x.jitterMs))+' ms')+'</b></div><div class="xw-path-state"><span class="tag '+(x.ready?'green':'red')+'">'+(x.ready?L('READY','آماده'):L('FAILED','ناموفق'))+'</span>'+(x.selected?'<span class="tag green">'+L('SELECTED','انتخاب‌شده')+'</span>':'')+'</div><div>'+(x.ready&&!x.selected?'<button type="button" class="btn btn-primary mini" data-act="xwuse" data-endpoint="'+e(x.endpoint)+'">'+L('Select & Apply','انتخاب و اعمال')+'</button>':'')+'</div></article>';
 }).join('')+'</div>';
}
function warpCenter(runtimes,status){
 const stateLabel=status.state==='ready'?L('READY / APPLIED','آماده / اعمال‌شده'):status.state==='awaiting_selection'?L('AWAITING PATH SELECTION','منتظر انتخاب مسیر'):L('NOT CREATED','ساخته نشده');
 const tone=status.state==='ready'?'good':status.state==='awaiting_selection'?'warn':'';
 return '<section class="panel xw-center"><header><div><small>WARP RUNTIME CENTER</small><h2>'+L('Manual WARP path selection','انتخاب دستی مسیر WARP')+'</h2><p>'+L('Scan on the exact HUB/Node you choose. DARK never auto-selects a path unless you explicitly press Auto Best.','اسکن دقیقاً روی HUB/Node انتخابی انجام می‌شود؛ DARK هیچ مسیری را خودکار انتخاب نمی‌کند مگر خودت Auto Best را بزنی.')+'</p></div><span class="xw-state '+tone+'">'+stateLabel+'</span></header><div class="xw-controls"><label><span>'+L('Test from runtime','تست از سرور')+'</span><select data-xw-runtime>'+runtimeOptions(runtimes)+'</select></label><div class="xw-current"><span>'+L('Current path','مسیر فعلی')+'</span><b class="mono">'+e(status.endpoint||status.candidateEndpoint||'—')+'</b><small>'+(status.selectionConfirmed?L('Verified and selected','تأیید و انتخاب شده'):status.registered?L('Profile exists but no path is selected','پروفایل ساخته شده ولی هنوز مسیری انتخاب نشده'):L('Create WARP first','ابتدا WARP را بساز'))+'</small></div><div class="xw-actions">'+(!status.registered?'<button type="button" class="btn btn-primary" data-act="xwcreate">'+L('Create + Scan','ساخت + اسکن')+'</button>':'<button type="button" class="btn btn-primary" data-act="xwscan">'+L('Scan WARP Paths','اسکن مسیرهای WARP')+'</button><button type="button" class="btn" data-act="xwauto">'+L('Auto Best · Optional','بهترین خودکار · اختیاری')+'</button>')+'</div></div>'+(status.state==='awaiting_selection'?'<div class="notice warning">'+L('WARP is registered for testing only. Production WARP policies stay blocked until you select and apply one verified path.','WARP فقط برای تست ثبت شده؛ تا یک مسیر سالم را انتخاب و اعمال نکنی، سیاست‌های WARP در Production مسدود می‌مانند.')+'</div>':'')+warpRows()+'</section>';
}
async function workspace(){
 const [rows,runtimeDoc]=await Promise.all([api('/api/inbounds'),api('/api/traffic-matrix/runtimes')]);
 if(!Array.isArray(rows))throw Error(L('Invalid inbound list.','فهرست اینباندها نامعتبر است.'));
 const runtimes=runtimeDoc?.items||[];
 if(!runtimes.some(r=>r.id===WC.server))WC.server=runtimes[0]?.id||'hub';
 const status=await api('/api/traffic-matrix/warp?server='+encodeURIComponent(WC.server));
 const headingHtml=heading(title(),L('Choose a runtime for WARP scanning, then use Traffic Matrix only to assign WARP/AdBlock policy to an inbound.','برای اسکن WARP سرور را انتخاب کن؛ سپس Traffic Matrix فقط برای اعمال سیاست WARP/AdBlock روی اینباند استفاده می‌شود.'));
 const cards=rows.map(row=>{
  const id=Number(row.id);
  if(!Number.isSafeInteger(id)||id<1)throw Error(L('Invalid inbound identity.','شناسه اینباند نامعتبر است.'));
  return '<article class="panel tm-access-card"><div><b data-no-i18n>'+e(row.remark||row.tag||('Inbound '+id))+'</b><small class="mono" data-no-i18n>#'+id+' · '+e(row.protocol||'')+' · :'+e(row.port||'')+'</small></div><button type="button" class="btn btn-primary tm-access-open" data-act="tmaccessopen" data-id="'+id+'">'+icon('activity')+L('Open Traffic Matrix','بازکردن ماتریس مسیر')+'</button></article>';
 }).join('');
 return '<div class="tm-access-workspace">'+headingHtml+warpCenter(runtimes,status)+'<section class="xw-policy-section"><div class="xw-section-head"><div><small>INBOUND POLICY</small><h2>'+L('WARP / AdBlock policy per inbound','سیاست WARP / حذف تبلیغ برای هر اینباند')+'</h2></div></div><div class="notice">'+L('Opening a matrix never changes traffic. Choose Direct/Tunnel policy and press Apply inside the matrix.','بازکردن ماتریس مسیر ترافیک را تغییر نمی‌دهد؛ داخل ماتریس سیاست Direct/Tunnel را انتخاب و Apply کن.')+'</div><div class="tm-access-grid">'+(cards||empty(L('No inbounds are available yet.','هنوز اینباندی موجود نیست.')))+'</div></section></div>';
}

enginePage=async function(){
 const active=state.page;
 if(!groupedPages.has(active))return previousEnginePage();
 if(!isOwner())return '<div class="notice error">'+L('Owner access required.','دسترسی مالک لازم است.')+'</div>';
 // Old V2 shortcut state must not select a second, legacy traffic editor.
 if(active==='xray'&&['outbounds','routing'].includes(state.xv2?.tab))state.xv2.tab='general';
 const html=active===pageId?await workspace():await previousEnginePage();
 return '<div class="xray-settings-workspace" data-xray-section="'+active+'">'+sectionNav(active)+html+'</div>';
};
document.addEventListener('change',ev=>{
 const el=ev.target;if(!el.matches('[data-xw-runtime]'))return;
 WC.server=String(el.value||'hub');WC.items=[];WC.scannedServer='';renderPage().catch(ex=>toast(ex.message,true));
});
async function scanWarpCenter(){
 const r=await api('/api/traffic-matrix/warp/scan','POST',{server:WC.server});
 WC.items=r.items||[];WC.scannedServer=WC.server;await renderPage();return r;
}
runAction=async function(act,el){
 if(act==='xwcreate'){
  const r=await api('/api/traffic-matrix/warp/create','POST',{server:WC.server});WC.items=r.items||[];WC.scannedServer=WC.server;
  toast(L('WARP profile created. Select a verified path to activate it.','پروفایل WARP ساخته شد؛ برای فعال‌سازی یک مسیر سالم را انتخاب کن.'));await renderPage();return;
 }
 if(act==='xwscan'){await scanWarpCenter();return;}
 if(act==='xwuse'){
  const endpoint=String(el.dataset.endpoint||'');await api('/api/traffic-matrix/warp/endpoint','POST',{server:WC.server,endpoint});
  toast(L('Selected WARP path verified and applied.','مسیر WARP انتخاب، تأیید و اعمال شد.'));await scanWarpCenter();return;
 }
 if(act==='xwauto'){
  if(!confirm(L('Automatically choose the best currently verified WARP path on this runtime?','بهترین مسیر سالم فعلی WARP روی این سرور به‌صورت خودکار انتخاب شود؟')))return;
  await api('/api/traffic-matrix/warp/auto','POST',{server:WC.server});toast(L('Best verified WARP path selected.','بهترین مسیر سالم WARP انتخاب شد.'));await scanWarpCenter();return;
 }
 if(act==='xv2tab'&&['outbounds','routing'].includes(el?.dataset.tab)){
  if(!isOwner())throw Error(L('Owner access required.','دسترسی مالک لازم است.'));
  return go(el.dataset.tab);
 }
 if(act!=='tmaccessopen')return previousAction(act,el);
 if(!isOwner())throw Error(L('Owner access required.','دسترسی مالک لازم است.'));
 const id=Number(el?.dataset.id);
 if(!Number.isSafeInteger(id)||id<1)throw Error(L('Select a valid inbound.','یک اینباند معتبر انتخاب کن.'));
 if(typeof globalThis.DarkTrafficMatrix?.open!=='function')throw Error(L('Traffic Matrix did not load. Reload this page to load its module.','ماژول ماتریس مسیر بارگذاری نشده؛ صفحه را دوباره باز کن.'));
 return globalThis.DarkTrafficMatrix.open(id);
};
globalThis.DarkTrafficMatrixAccess={ready:true,page:pageId,parentPage:'xray',grouped:true};
})();
