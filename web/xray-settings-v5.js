/* Server-first Xray Settings. Reuses existing scoped policies and isolated WARP scanner. */
(function(){
'use strict';
if(typeof enginePage!=='function'||typeof runAction!=='function'||typeof api!=='function')return;
const previousPage=enginePage,previousAction=runAction;
const S=state.xraySettingsV5=state.xraySettingsV5||{server:'',inbound:0,path:'',advanced:false,data:null};
const L=(en,fa)=>((localStorage.getItem('dark_lang')||'en')==='fa'?fa:en);
const esc=x=>e(String(x??''));
const routes=[['normal','Direct'],['warp_ai','WARP · AI'],['warp_all','WARP · All']];
function policyFrom(route,adblock){
 if(!routes.some(x=>x[0]===route))throw Error(L('Select a simple route first.','ابتدا یک مسیر ساده انتخاب کن.'));
 return adblock?(route==='normal'?'adblock':route+'_adblock'):route;
}
function baseRoute(policy){return policy==='adblock'?'normal':String(policy||'normal').replace(/_adblock$/,'');}
function badge(value){
 const status=['applied','pending','error'].includes(value?.status)?value.status:'pending';
 const label=status==='applied'?L('Applied','اعمال‌شده'):status==='error'?L('Error','خطا'):L('Pending','در انتظار');
 return '<span class="x5-state '+status+'">'+label+'</span>'+(value?.error?'<small class="x5-error">'+esc(value.error)+'</small>':'');
}
function navigation(active){
 return '<nav class="xray-settings-nav" aria-label="Xray Settings">'+[
  ['xray',L('Core & DNS','هسته و DNS')],['outbounds',L('Outbounds','اوتباندها')],
  ['routing',L('Routing','روتینگ')],['trafficmatrix',L('WARP / AdBlock','WARP / حذف تبلیغ')]
 ].map(([id,label])=>'<button type="button" data-page="'+id+'" class="'+(active===id?'active':'')+'"'+(active===id?' aria-current="page"':'')+'>'+esc(label)+'</button>').join('')+'</nav>';
}
function selectBox(label,key,rows,value){
 return '<label class="x5-field"><span>'+label+'</span><select data-x5-select="'+key+'"><option value="">'+L('Select…','انتخاب کن…')+'</option>'+rows.map(x=>'<option value="'+esc(x[0])+'" '+(String(value)===String(x[0])?'selected':'')+'>'+esc(x[1])+'</option>').join('')+'</select></label>';
}
function serverBox(d){
 return '<section class="panel x5-context">'+selectBox(L('1 · Select Server','۱ · انتخاب سرور'),'server',d.servers.map(x=>[x.id,(x.name||x.id)+(x.online===false?' · OFFLINE':'')]),S.server)+
  '<div class="x5-runtime">'+(d.target?'<b>'+esc(d.target.name)+'</b><small>'+esc(d.target.address)+'</small>'+badge(d.target.applyState):L('No server selected. Opening this page does not scan or change routes.','سروری انتخاب نشده است؛ بازکردن صفحه نه اسکن می‌کند و نه مسیر را تغییر می‌دهد.'))+'</div></section>';
}
function warpBox(d){
 if(!d.target)return '';
 const warp=d.warp||{},available=warp.registered||warp.pendingRegistration,offline=d.target.online===false;
 return '<section class="panel x5-warp"><header><div><small>WARP · '+esc(d.target.name)+'</small><h3>'+L('Scan → Select → Preview → Apply','اسکن ← انتخاب ← پیش‌نمایش ← اعمال')+'</h3></div><span class="x5-manual">'+L('Manual selection','انتخاب دستی')+'</span></header>'+
  '<div class="x5-current"><span>'+L('Saved endpoint','Endpoint ذخیره‌شده')+'</span><code>'+esc(warp.endpoint||'—')+'</code>'+badge(warp.applyState)+'</div>'+
  (warp.pendingRegistration?'<div class="notice warning">'+L('Registration is pending. No new route is active until you select and apply it.','ثبت WARP در انتظار است؛ هیچ مسیر جدیدی تا انتخاب و اعمال فعال نمی‌شود.')+'</div>':'')+
  (!available?'<p>'+L('Register once, then scan real WARP endpoints from this server. Registration never activates its default endpoint.','یک‌بار ثبت کن و سپس Endpointهای واقعی WARP را از همین سرور اسکن کن. ثبت، Endpoint پیش‌فرض را فعال نمی‌کند.')+'</p>':'')+
  '<div class="x5-actions"><button type="button" class="btn btn-primary" data-act="'+(available?'x5warpscan':'x5warpregister')+'" '+(offline?'disabled':'')+'>'+L(available?'Scan WARP Paths / Change Route':'Register WARP',available?'اسکن مسیرهای WARP / تغییر مسیر':'ثبت WARP')+'</button><button type="button" class="btn" data-act="x5refresh">'+L('Refresh status','به‌روزرسانی وضعیت')+'</button></div>'+
  '<small>'+L('Scan origin: ','مبدأ اسکن: ')+esc(d.target.name)+' · '+esc(d.target.address)+' · '+L('Scan is isolated; existing traffic is not rerouted.','اسکن جداست و مسیر ترافیک فعلی را تغییر نمی‌دهد.')+'</small></section>';
}
function routingBox(d){
 if(!d.target)return '';
 const rows=d.matrix?.rows?.filter(x=>x.serverId===S.server)||[];
 const row=rows.find(x=>x.accessPath===S.path),inbound=d.inbounds.find(x=>Number(x.id)===S.inbound);
 let body='<div class="x5-grid">'+selectBox(L('2 · Select Inbound','۲ · انتخاب اینباند'),'inbound',d.inbounds.map(x=>[x.id,(x.remark||x.tag||'Inbound')+' · #'+x.id]),S.inbound);
 if(inbound)body+=selectBox(L('Access path','مسیر دسترسی'),'path',rows.map(x=>[x.accessPath,(x.accessPath==='direct'?'Direct':'Tunnel')+' · :'+x.port]),S.path);
 body+='</div>';
 if(inbound&&!rows.length)body+='<div class="notice warning">'+L('This inbound is not deployed on the selected server. Choose another inbound or server.','این اینباند روی سرور انتخابی مستقر نیست؛ اینباند یا سرور دیگری انتخاب کن.')+'</div>';
 if(row){
  const route=baseRoute(row.policy),custom=route==='custom';
  body+='<div class="x5-grid"><label class="x5-field"><span>'+L('3 · Outbound / Route','۳ · اوتباند / مسیر')+'</span><select data-x5-route>'+(custom?'<option value="custom" selected>'+L('Existing Advanced Routing','مسیریابی پیشرفتهٔ فعلی')+'</option>':'')+routes.map(x=>'<option value="'+x[0]+'" '+(x[0]===route?'selected':'')+'>'+x[1]+'</option>').join('')+'</select></label>'+
   '<label class="x5-toggle"><input type="checkbox" data-x5-adblock '+(String(row.policy).includes('adblock')?'checked':'')+'><span>Ad-block</span><small>'+L('Block advertising domains','مسدودسازی دامنه‌های تبلیغاتی')+'</small></label></div>'+
   '<div class="x5-saved"><span>'+L('Saved policy: ','سیاست ذخیره‌شده: ')+esc(row.policy)+'</span>'+badge(row.applyState)+'</div>'+
   '<div class="x5-actions"><button type="button" class="btn" data-act="x5routeping" '+(d.target.online===false?'disabled':'')+'>'+L('Ping selected outbound','پینگ اوتباند انتخابی')+'</button><button type="button" class="btn btn-primary" data-act="x5preview" '+(d.target.online===false?'disabled':'')+'>'+L('Preview','پیش‌نمایش')+'</button></div><div data-x5-ping aria-live="polite"></div>'+
   '<small>'+L('Ping source: ','مبدأ پینگ: ')+esc(d.target.name)+' · '+L('Outbound test only; no tunnel health test.','فقط تست اوتباند؛ بدون تست سلامت تونل.')+'</small>';
 }
 return '<section class="panel x5-routing"><header><h3>'+L('Routing / Ad-block','مسیریابی / حذف تبلیغ')+'</h3></header>'+body+'</section>';
}
async function load(){
 const [servers,inbounds]=await Promise.all([api('/api/xray-settings/servers'),api('/api/inbounds')]);
 if(!Array.isArray(servers.items)||!Array.isArray(inbounds))throw Error(L('Invalid settings response.','پاسخ تنظیمات نامعتبر است.'));
 const target=servers.items.find(x=>x.id===S.server);
 if(!target){S.server='';S.inbound=0;S.path='';}
 if(!inbounds.some(x=>Number(x.id)===S.inbound)){S.inbound=0;S.path='';}
 const [warp,matrix]=await Promise.all([target?api('/api/traffic-matrix/warp?server='+encodeURIComponent(S.server)):null,
  target&&S.inbound?api('/api/traffic-matrix?inboundId='+S.inbound):null]);
 const paths=(matrix?.rows||[]).filter(x=>x.serverId===S.server);
 if(!paths.some(x=>x.accessPath===S.path))S.path='';
 return S.data={servers:servers.items,inbounds,target,warp,matrix};
}
enginePage=async function(){
 const page=state.page;
 if(!['trafficmatrix','routing'].includes(page))return previousPage();
 if(!isOwner())return '<div class="notice error">'+L('Owner access required.','دسترسی مالک لازم است.')+'</div>';
 if(page==='routing'&&S.advanced)return '<button type="button" class="btn" data-act="x5simple">'+L('Back to server-scoped routing','بازگشت به مسیریابی سرور انتخابی')+'</button><div class="notice warning">'+L('Advanced rules are shared engine rules, not a rule scoped by the server selector.','قوانین پیشرفته قوانین مشترک موتور هستند، نه قوانین محدود به انتخاب سرور این صفحه.')+'</div>'+await previousPage();
 try{
  const d=await load();
  return '<div class="xray-settings-workspace" data-xray-section="'+page+'">'+navigation(page)+heading(page==='trafficmatrix'?'WARP / Ad-block':L('Routing','مسیریابی'),L('Select the server first. Every change is reviewed before Apply.','ابتدا سرور را انتخاب کن؛ هر تغییر قبل از اعمال پیش‌نمایش دارد.'))+'<div class="x5-actions"><button type="button" class="btn" data-act="x5advanced">'+L('Advanced shared rules','قوانین مشترک پیشرفته')+'</button></div><div class="x5-workspace">'+serverBox(d)+(page==='trafficmatrix'?warpBox(d):'')+routingBox(d)+'</div></div>';
 }catch(ex){return '<div class="xray-settings-workspace">'+navigation(page)+'<div class="notice error" role="alert">'+esc(ex.message)+'</div><button type="button" class="btn" data-act="x5refresh">'+L('Retry','تلاش دوباره')+'</button></div>';}
};
function context(){
 const d=S.data,row=d?.matrix?.rows?.find(x=>x.serverId===S.server&&x.accessPath===S.path);
 if(!S.server||!row||Number(row.inboundId)!==S.inbound)throw Error(L('Select server, inbound and access path.','سرور، اینباند و مسیر دسترسی را انتخاب کن.'));
 const route=document.querySelector('[data-x5-route]')?.value;
 const adblock=!!document.querySelector('[data-x5-adblock]')?.checked;
 return {d,row,route,policy:policyFrom(route,adblock),adblock};
}
async function preview(){
 const {d,row,route,policy,adblock}=context(),server=S.server,inboundId=S.inbound,accessPath=S.path;
 const inbound=d.inbounds.find(x=>Number(x.id)===inboundId),original=row.policy;
 const body='<div class="x5-preview"><div><span>'+L('Server','سرور')+'</span><b>'+esc(d.target.name)+' · '+esc(server)+'</b></div><div><span>Inbound</span><b>'+esc(inbound.remark||inbound.tag||inboundId)+'</b></div><div><span>'+L('Access path','مسیر دسترسی')+'</span><b>'+esc(accessPath)+' · :'+esc(row.port)+'</b></div><div><span>Route</span><b>'+esc(route)+'</b></div><div><span>Ad-block</span><b>'+(adblock?'ON':'OFF')+'</b></div><div class="notice warning">'+L('Apply may reload this server’s Xray runtime. Listener ports, deployments and tunnel settings are not edited.','اعمال ممکن است Xray همین سرور را دوباره بارگذاری کند؛ پورت‌ها، محل استقرار و تنظیمات تونل ویرایش نمی‌شوند.')+'</div><label class="x5-toggle"><input type="checkbox" name="confirmed" required><span>'+L('Apply exactly this change','دقیقاً همین تغییر اعمال شود')+'</span></label></div>';
 dialog(L('Preview routing change','پیش‌نمایش تغییر مسیریابی'),body,async fd=>{
  if(!fd.has('confirmed'))throw Error(L('Confirm this change.','این تغییر را تأیید کن.'));
  const current=await api('/api/traffic-matrix?inboundId='+inboundId);
  const same=current.rows?.find(x=>x.serverId===server&&x.accessPath===accessPath);
  if(!same||same.policy!==original||same.port!==row.port)throw Error(L('The route changed. Close and preview again.','مسیر تغییر کرده است؛ ببند و دوباره پیش‌نمایش بگیر.'));
  const result=await api('/api/traffic-matrix','POST',{inboundId,server,accessPath,policy});
  const status=result.rows?.find(x=>x.serverId===server&&x.accessPath===accessPath)?.applyState?.status||'pending';
  closeDialog();toast(status==='applied'?L('Applied to selected server.','روی سرور انتخاب‌شده اعمال شد.'):status==='error'?L('Apply error. Review runtime status.','خطای اعمال؛ وضعیت سرور را بررسی کن.'):L('Saved; awaiting runtime acknowledgement.','ذخیره شد؛ در انتظار تأیید سرور.'),status==='error');
  await renderPage();
 },L('Apply','اعمال'));
}
async function routePing(){
 const {d,route}=context(),server=S.server,tag=route==='normal'?'direct':'warp';
 const box=document.querySelector('[data-x5-ping]');
 if(box)box.textContent=L('Testing from ','در حال تست از ')+d.target.name;
 try{
  const r=await api('/api/traffic-engine/outbound/probe','POST',{server,tag,attempts:2});
  const source=r.server?.name||d.target.name,location=[r.egress?.country,r.egress?.colo].filter(Boolean).join(' · ');
  if(box&&S.server===server)box.textContent=source+' → '+tag+' · '+(r.success?Math.round(r.delayMs)+' ms':'Failed')+' · '+(location||L('Unknown location','موقعیت نامشخص'))+(r.error?' · '+r.error:'');
 }catch(ex){if(box)box.textContent=d.target.name+' · '+ex.message;throw ex;}
}
runAction=async function(act,el){
 if(!act.startsWith('x5'))return previousAction(act,el);
 if(!isOwner())throw Error(L('Owner access required.','دسترسی مالک لازم است.'));
 if(act==='x5refresh')return renderPage();
 if(act==='x5advanced'){S.advanced=true;return go('routing');}
 if(act==='x5simple'){S.advanced=false;return go('routing');}
 if(act==='x5preview')return preview();
 if(act==='x5routeping')return routePing();
 if(act==='x5warpscan'||act==='x5warpregister'){
  if(!S.server)throw Error(L('Select a server first.','ابتدا سرور را انتخاب کن.'));
  return previousAction(act==='x5warpscan'?'tmwarpscan':'tmwarpcreate',{dataset:{id:'0',server:S.server}});
 }
 return previousAction(act,el);
};
document.addEventListener('change',event=>{
 const key=event.target?.dataset?.x5Select;if(!key)return;
 if(key==='server'){S.server=String(event.target.value||'');S.inbound=0;S.path='';}
 if(key==='inbound'){S.inbound=Number(event.target.value)||0;S.path='';}
 if(key==='path')S.path=String(event.target.value||'');
 renderPage();
});
globalThis.DarkXraySettingsV5={policyFrom,baseRoute,badge,manualDefault:true};
})();
