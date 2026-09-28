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
async function workspace(){
 const rows=await api('/api/inbounds');
 if(!Array.isArray(rows))throw Error(L('Invalid inbound list.','فهرست اینباندها نامعتبر است.'));
 const headingHtml=heading(title(),L('Choose an inbound. WARP, AdBlock and path tests stay in its Traffic Matrix.','یک اینباند انتخاب کن؛ WARP، حذف تبلیغ و تست مسیرها در همان ماتریس مسیر قرار دارند.'));
 const cards=rows.map(row=>{
  const id=Number(row.id);
  if(!Number.isSafeInteger(id)||id<1)throw Error(L('Invalid inbound identity.','شناسه اینباند نامعتبر است.'));
  return '<article class="panel tm-access-card"><div><b data-no-i18n>'+e(row.remark||row.tag||('Inbound '+id))+'</b><small class="mono" data-no-i18n>#'+id+' · '+e(row.protocol||'')+' · :'+e(row.port||'')+'</small></div><button type="button" class="btn btn-primary tm-access-open" data-act="tmaccessopen" data-id="'+id+'">'+icon('activity')+L('Open Traffic Matrix','بازکردن ماتریس مسیر')+'</button></article>';
 }).join('');
 return '<div class="tm-access-workspace">'+headingHtml+'<div class="notice">'+L('Opening this page or a matrix does not enable WARP or change traffic routes.','بازکردن این صفحه یا ماتریس، WARP را فعال نمی‌کند و مسیر ترافیک را تغییر نمی‌دهد.')+'</div><div class="tm-access-grid">'+(cards||empty(L('No inbounds are available yet.','هنوز اینباندی موجود نیست.')))+'</div></div>';
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
runAction=async function(act,el){
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
