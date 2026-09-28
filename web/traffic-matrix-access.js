/* DARK XRAY: discoverable owner entry points into the existing Traffic Matrix. */
(function(){
'use strict';
if(typeof navItems!=='function'||typeof enginePage!=='function'||typeof inboundPage!=='function'||typeof runAction!=='function')return;
const pageId='trafficmatrix';
const L=(en,fa)=>((localStorage.getItem('dark_lang')||'en')==='fa'?fa:en);
const title=()=>L('WARP / AdBlock','WARP / حذف تبلیغ');
const previousNav=navItems,previousEnginePage=enginePage,previousInboundPage=inboundPage,previousAction=runAction;
enginePages[pageId]=[title()];
navItems=function(){
 const rows=previousNav();
 if(isOwner()&&!rows.some(x=>x[0]===pageId)){
  const index=rows.findIndex(x=>x[0]==='inbounds');
  rows.splice(index<0?1:index+1,0,[pageId,title(),'activity']);
 }
 return rows;
};
function accessBanner(){
 return '<section class="tm-access-banner" aria-label="'+e(title())+'"><div><b>'+e(title())+'</b><p>'+L('Select an inbound to manage every server and Direct / Tunnel path.','اینباند را انتخاب کن؛ مسیر مستقیم و تانل هر سرور از همین بخش مدیریت می‌شود.')+'</p></div><button type="button" class="btn btn-primary" data-page="'+pageId+'">'+icon('activity')+L('Open WARP / AdBlock','بازکردن WARP / حذف تبلیغ')+'</button></section>';
}
inboundPage=function(){
 const html=previousInboundPage();
 return isOwner()?accessBanner()+html:html;
};
async function workspace(){
 if(!isOwner())return '<div class="notice error">'+L('Owner access required.','دسترسی مالک لازم است.')+'</div>';
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
 if(state.page===pageId)return workspace();
 return previousEnginePage();
};
runAction=async function(act,el){
 if(act!=='tmaccessopen')return previousAction(act,el);
 if(!isOwner())throw Error(L('Owner access required.','دسترسی مالک لازم است.'));
 const id=Number(el?.dataset.id);
 if(!Number.isSafeInteger(id)||id<1)throw Error(L('Select a valid inbound.','یک اینباند معتبر انتخاب کن.'));
 if(typeof globalThis.DarkTrafficMatrix?.open!=='function')throw Error(L('Traffic Matrix did not load. Reload this page to load its module.','ماژول ماتریس مسیر بارگذاری نشده؛ صفحه را دوباره باز کن.'));
 return globalThis.DarkTrafficMatrix.open(id);
};
globalThis.DarkTrafficMatrixAccess={ready:true,page:pageId};
})();
