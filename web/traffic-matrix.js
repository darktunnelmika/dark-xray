/* DARK XRAY Traffic Matrix — runtime + Direct/Tunnel routing workspace. */
(function(){
'use strict';
if(typeof runAction!=='function'||typeof api!=='function'||typeof dialog!=='function')return;
const baseRunAction=runAction;
state.trafficMatrix=state.trafficMatrix||{doc:null,pings:{}};
const L=(en,fa)=>((localStorage.getItem('dark_lang')||'en')==='fa'?fa:en);
const esc=v=>e(String(v??''));
function policyLabel(v){
 const map={normal:L('Normal / Direct','عادی / مستقیم'),warp_ai:'WARP AI',warp_all:L('WARP All','WARP همه'),adblock:L('AdBlock','حذف تبلیغ'),warp_ai_adblock:'WARP AI + AdBlock',warp_all_adblock:L('WARP All + AdBlock','WARP همه + حذف تبلیغ'),custom:L('Custom / Advanced Routing','سفارشی / Routing پیشرفته')};
 return map[v]||v;
}
function policyOptions(value){
 return ['normal','warp_ai','warp_all','adblock','warp_ai_adblock','warp_all_adblock','custom'].map(function(v){
  return '<option value="'+esc(v)+'" '+(v===value?'selected':'')+'>'+esc(policyLabel(v))+'</option>';
 }).join('');
}
function keyFor(r){return [r.serverId,r.inboundId,r.accessPath].join('|');}
function pingView(r){
 const p=state.trafficMatrix.pings[keyFor(r)];
 if(!p)return '<span class="tm-ping idle">'+L('Not tested','تست نشده')+'</span>';
 if(p.testing)return '<span class="tm-ping testing">…</span>';
 const delay=p.delayMs==null?'—':Math.round(Number(p.delayMs))+' ms';
 const loss=p.lossPercent==null?'—':p.lossPercent+'%';
 return '<span class="tm-ping '+(p.ok?'ok':'bad')+'"><b>'+(p.ok?'PASS':'FAIL')+'</b><small>'+delay+' · '+loss+' · '+(p.listenerReady?L('listener ok','لیسنر سالم'):L('listener down','لیسنر قطع'))+'</small></span>';
}
function pathLabel(v){return v==='tunnel'?L('Tunnel','تانل'):L('Direct','مستقیم');}
function routeRow(r){
 const warpOn=String(r.policy||'').startsWith('warp_'),adblockOn=String(r.policy||'').includes('adblock');
 return '<div class="tm-route" data-matrix-key="'+esc(keyFor(r))+'">'+
  '<div class="tm-path"><span class="tm-path-icon '+esc(r.accessPath)+'">'+(r.accessPath==='tunnel'?'T':'D')+'</span><div><b>'+esc(pathLabel(r.accessPath))+'</b><small class="mono">:'+esc(r.port)+' · '+esc(r.serverId)+'</small></div></div>'+
  '<label class="tm-policy"><span>'+L('Policy','سیاست مسیر')+'</span><select data-matrix-policy>'+policyOptions(r.policy)+'</select><small class="tm-policy-state"><i class="'+(warpOn?'on':'')+'">WARP '+(warpOn?'ON':'OFF')+'</i><i class="'+(adblockOn?'on':'')+'">AdBlock '+(adblockOn?'ON':'OFF')+'</i></small></label>'+
  '<div class="tm-health">'+pingView(r)+'</div>'+
  '<div class="tm-actions"><button type="button" class="btn mini" data-act="tmprobe" data-id="'+r.inboundId+'" data-server="'+esc(r.serverId)+'" data-path="'+esc(r.accessPath)+'">'+icon('activity')+L('Ping','پینگ')+'</button>'+
  '<button type="button" class="btn btn-primary mini" data-act="tmapply" data-id="'+r.inboundId+'" data-server="'+esc(r.serverId)+'" data-path="'+esc(r.accessPath)+'">'+icon('check')+L('Apply','اعمال')+'</button></div></div>';
}
function serverCard(rows){
 const first=rows[0],server=first.server||{},same=rows.every(function(x){return x.policy===first.policy;}),both=same?first.policy:'normal',
  warp=rows.some(function(x){return x.warpReady;}),pending=rows.some(function(x){return x.warpPending;}),paths=rows.map(function(x){return x.accessPath;}).join(',');
 const warpButton=pending
  ?'<button type="button" class="btn btn-primary mini" data-act="tmwarpscan" data-id="'+first.inboundId+'" data-server="'+esc(first.serverId)+'">'+icon('activity')+L('Scan & Select WARP','اسکن و انتخاب WARP')+'</button>'
  :warp
   ?'<button type="button" class="btn mini" data-act="tmwarpscan" data-id="'+first.inboundId+'" data-server="'+esc(first.serverId)+'">'+icon('activity')+L('WARP Paths','مسیرهای WARP')+'</button>'
   :'<button type="button" class="btn mini" data-act="tmwarpcreate" data-id="'+first.inboundId+'" data-server="'+esc(first.serverId)+'">'+icon('plus')+L('Register WARP','ثبت WARP')+'</button>';
 const warpState=pending?'<span class="tag amber">'+L('WARP · SELECT ROUTE','WARP · انتخاب مسیر')+'</span>':warp?'<span class="tag green">'+L('WARP · ACTIVE','WARP · فعال')+'</span>':'';
 return '<article class="tm-server"><header><div><b>'+esc(server.name||first.serverId)+'</b><small class="mono">'+esc(server.address||'—')+' · '+(server.online===false?L('Offline','آفلاین'):L('Online','آنلاین'))+'</small>'+warpState+'</div>'+
  '<div class="tm-server-tools"><select data-matrix-both-policy>'+policyOptions(both)+'</select>'+
  '<button type="button" class="btn mini" data-act="tmapplyboth" data-id="'+first.inboundId+'" data-server="'+esc(first.serverId)+'" data-paths="'+esc(paths)+'">'+L('Apply both','اعمال روی هر دو')+'</button>'+
  '<button type="button" class="btn mini" data-act="tmprobeall" data-id="'+first.inboundId+'" data-server="'+esc(first.serverId)+'" data-paths="'+esc(paths)+'">'+icon('activity')+L('Test all','تست همه')+'</button>'+warpButton+
  '</div></header><div class="tm-routes">'+rows.map(routeRow).join('')+'</div></article>';
}
async function openMatrix(id){
 const d=await api('/api/traffic-matrix?inboundId='+encodeURIComponent(id));
 state.trafficMatrix.doc=d;
 const groups=[];
 (d.rows||[]).forEach(function(r){let g=groups.find(function(x){return x[0].serverId===r.serverId;});if(!g){g=[];groups.push(g);}g.push(r);});
 const body='<div class="tm-shell"><div class="tm-hero"><div><span>DARK TRAFFIC MATRIX</span><h3>'+esc(d.remark||('Inbound '+id))+'</h3><p>'+L('Route every Runtime + Direct/Tunnel path without hand-writing Xray rules.','هر مسیر Runtime + Direct/Tunnel را بدون Rule دستی مدیریت کن.')+'</p></div>'+
  '<div class="tm-legend"><span><i class="direct"></i>Direct</span><span><i class="tunnel"></i>Tunnel</span></div></div>'+
  '<div class="notice">'+L('WARP and AdBlock are controlled here. Outbounds and Routing remain advanced engine tools.','WARP و AdBlock از همین بخش کنترل می‌شوند؛ Outbounds و Routing فقط ابزار پیشرفته موتور باقی می‌مانند.')+'</div>'+
  '<div class="tm-servers">'+(groups.length?groups.map(serverCard).join(''):'<div class="iv3-empty-inline">'+L('No deployed runtime paths.','هیچ مسیر استقرار فعالی وجود ندارد.')+'</div>')+'</div></div>';
 dialog(L('Traffic Matrix','ماتریس مسیر')+' · '+(d.remark||id),body);
 const node=document.querySelector('#overlay .dialog');if(node)node.classList.add('traffic-matrix-dialog');
}
async function applyOne(el){
 const row=el.closest('.tm-route'),policy=String(row&&row.querySelector('[data-matrix-policy]')?row.querySelector('[data-matrix-policy]').value:'normal');
 const id=Number(el.dataset.id),server=String(el.dataset.server||'hub'),path=String(el.dataset.path||'direct');
 const body='<div class="tm-apply-preview"><div><span>'+L('Server','سرور')+'</span><b>'+esc(server)+'</b></div><div><span>'+L('Access path','مسیر دسترسی')+'</span><b>'+esc(pathLabel(path))+'</b></div><div><span>'+L('Policy','سیاست')+'</span><b>'+esc(policyLabel(policy))+'</b></div><label class="tg-switch"><input type="checkbox" name="confirmed" required><span>'+L('Apply this route policy','این سیاست مسیر اعمال شود')+'</span></label></div>';
 dialog(L('Preview Traffic Matrix change','پیش‌نمایش تغییر ماتریس'),body,async fd=>{
  if(!fd.has('confirmed'))throw Error(L('Confirm the route change.','تغییر مسیر را تأیید کن.'));
  const result=await api('/api/traffic-matrix','POST',{inboundId:id,server,accessPath:path,policy});
  closeDialog();showApplyState((result.rows||[]).find(x=>x.serverId===server&&x.accessPath===path)?.applyState);await openMatrix(id);
 },L('Apply route','اعمال مسیر'));
}
async function applyBoth(el){
 const card=el.closest('.tm-server'),policy=String(card&&card.querySelector('[data-matrix-both-policy]')?card.querySelector('[data-matrix-both-policy]').value:'normal');
 const paths=String(el.dataset.paths||'direct').split(',').filter(Boolean),id=Number(el.dataset.id),server=String(el.dataset.server||'hub');
 const body='<div class="tm-apply-preview"><div><span>'+L('Server','سرور')+'</span><b>'+esc(server)+'</b></div><div><span>'+L('Access paths','مسیرها')+'</span><b>'+esc(paths.map(pathLabel).join(' + '))+'</b></div><div><span>'+L('Policy','سیاست')+'</span><b>'+esc(policyLabel(policy))+'</b></div><label class="tg-switch"><input type="checkbox" name="confirmed" required><span>'+L('Apply this policy to all shown access paths','این سیاست روی همه مسیرهای نمایش‌داده‌شده اعمال شود')+'</span></label></div>';
 dialog(L('Preview batch route change','پیش‌نمایش تغییر گروهی مسیر'),body,async fd=>{
  if(!fd.has('confirmed'))throw Error(L('Confirm the route change.','تغییر مسیر را تأیید کن.'));
  const result=await api('/api/traffic-matrix/batch','POST',{inboundId:id,server,accessPaths:paths,policy});
  closeDialog();showApplyState((result.rows||[]).find(x=>x.serverId===server)?.applyState);await openMatrix(id);
 },L('Apply all paths','اعمال همه مسیرها'));
}
async function probePath(id,server,path){
 const key=[server,id,path].join('|');state.trafficMatrix.pings[key]={testing:true};
 const box=document.querySelector('[data-matrix-key="'+CSS.escape(key)+'"] .tm-health');if(box)box.innerHTML='<span class="tm-ping testing">…</span>';
 try{const r=await api('/api/traffic-matrix/probe','POST',{inboundId:Number(id),server:String(server),accessPath:String(path),attempts:2});state.trafficMatrix.pings[key]=r;if(box)box.innerHTML=pingView({serverId:server,inboundId:Number(id),accessPath:path});return r;}
 catch(ex){state.trafficMatrix.pings[key]={ok:false,listenerReady:false,delayMs:null,lossPercent:null,error:ex.message};if(box)box.innerHTML=pingView({serverId:server,inboundId:Number(id),accessPath:path});throw ex;}
}
async function probeAll(el){
 const id=Number(el.dataset.id),server=String(el.dataset.server||'hub'),paths=String(el.dataset.paths||'direct').split(',').filter(Boolean);
 let failed=0;for(const path of paths){try{await probePath(id,server,path);}catch{failed++;}}
 toast(failed?L('Some Traffic Matrix tests failed.','بعضی تست‌های ماتریس ناموفق بودند.'):L('All Traffic Matrix paths passed.','همه مسیرهای ماتریس سالم هستند.'),!!failed);
}
async function createWarp(el){
 const id=Number(el.dataset.id),server=String(el.dataset.server||'hub');
 toast(L('Registering WARP… no route will be activated yet.','در حال ثبت WARP… هنوز هیچ مسیری فعال نمی‌شود.'));
 const r=await api('/api/traffic-matrix/warp/create','POST',{server:server});
 if(!r.pendingRegistration||r.manualSelectionRequired!==true)throw Error(L('WARP registration did not enter manual-selection mode.','ثبت WARP وارد حالت انتخاب دستی نشد.'));
 toast(L('WARP registered. Scan and choose the route yourself.','WARP ثبت شد؛ مسیر را اسکن و خودت انتخاب کن.'));
 await scanWarp({dataset:{id:String(id),server}});
}
async function scanWarp(el){
 const id=Number(el.dataset.id),server=String(el.dataset.server||'hub');
 toast(L('Scanning WARP paths from the selected server…','در حال اسکن مسیرهای WARP از سرور انتخاب‌شده…'));
 const r=await api('/api/traffic-matrix/warp/scan','POST',{server:server});
 if(r.productionTrafficMutation!==false)throw Error(L('Unsafe WARP scan response: production mutation was not explicitly false.','پاسخ اسکن WARP امن نیست؛ عدم تغییر Production صریحاً تأیید نشده است.'));
 const ready=(r.items||[]).filter(x=>x.ready),best=ready[0]||null;
 const rows=(r.items||[]).map(function(x,index){
  const location=[x.country,x.colo].filter(Boolean).join(' · ')||L('Unknown location','موقعیت نامشخص');
  const delay=x.delayMs==null?'—':Math.round(Number(x.delayMs))+' ms';
  const loss=x.lossPercent==null?'—':x.lossPercent+'%';
  const jitter=x.jitterMs==null?'—':Math.round(Number(x.jitterMs))+' ms';
  const status=x.selected?'<span class="tag green">'+L('ACTIVE','فعال')+'</span>':x.candidateDefault?'<span class="tag amber">'+L('REGISTERED DEFAULT','پیش‌فرض ثبت')+'</span>':'';
  const pick=x.ready&&!x.selected?'<button type="button" class="btn mini" data-act="tmwarpuse" data-id="'+id+'" data-server="'+esc(server)+'" data-endpoint="'+esc(x.endpoint)+'" data-location="'+esc(location)+'" data-delay="'+esc(delay)+'" data-loss="'+esc(loss)+'" data-jitter="'+esc(jitter)+'">'+L('Select','انتخاب')+'</button>':'';
  return '<div class="tm-warp-path '+(x.ready?'ready':'failed')+'"><div><b class="mono">'+esc(x.endpoint)+'</b><small>'+esc(location)+(x.egressIp?' · '+esc(x.egressIp):'')+'</small></div>'+
   '<span><b>'+delay+'</b><small>Ping</small></span><span><b>'+loss+'</b><small>Loss</small></span><span><b>'+jitter+'</b><small>Jitter</small></span>'+
   '<span class="tag '+(x.status==='healthy'?'green':x.status==='degraded'?'amber':'red')+'">'+(x.status==='healthy'?L('Healthy','سالم'):x.status==='degraded'?L('Degraded','کیفیت پایین'):L('Failed','ناموفق'))+'</span><div>'+status+pick+'</div></div>';
 }).join('');
 const note=r.pendingRegistration
  ?'<div class="notice warning">'+L('This WARP registration is pending. Scanning does not change production traffic. Select one route below to activate it.','این ثبت WARP در انتظار انتخاب است. اسکن هیچ ترافیک Production را تغییر نمی‌دهد؛ یکی از مسیرهای زیر را برای فعال‌سازی انتخاب کن.')+'</div>'
  :'<div class="notice">'+L('Scanning is read-only. Your current WARP route stays active until you explicitly select another route.','اسکن فقط خواندنی است؛ مسیر فعلی WARP تا وقتی مسیر دیگری را صریحاً انتخاب نکنی فعال می‌ماند.')+'</div>';
 const bestButton=best?'<label class="tg-switch"><input type="checkbox" data-warp-auto-best><span>'+L('Auto Best · explicit','بهترین خودکار · اختیاری')+'</span></label><button type="button" class="btn" hidden data-warp-best-preview data-act="tmwarpuse" data-auto="1" data-id="'+id+'" data-server="'+esc(server)+'" data-endpoint="'+esc(best.endpoint)+'" data-location="'+esc([best.country,best.colo].filter(Boolean).join(' · ')||'Cloudflare edge')+'" data-delay="'+esc(best.delayMs==null?'—':Math.round(Number(best.delayMs))+' ms')+'" data-loss="'+esc(best.lossPercent==null?'—':best.lossPercent+'%')+'" data-jitter="'+esc(best.jitterMs==null?'—':Math.round(Number(best.jitterMs))+' ms')+'">'+L('Preview best route','پیش‌نمایش بهترین مسیر')+'</button>':'';
 dialog(L('WARP Route Scanner','اسکن مسیر WARP'),note+'<div class="tm-warp-head"><b>'+esc((r.server?.name||server))+'</b><small>'+L('Manual selection is the default. Auto Best only proposes a result from this scan; Apply still requires confirmation. Latency is HTTP through WARP, not ICMP.','پیش‌فرض انتخاب دستی است؛ Auto Best فقط از همین اسکن پیشنهاد می‌دهد و اعمال نیاز به تأیید دارد. تأخیر از HTTP داخل WARP است، نه ICMP.')+'</small><div>'+bestButton+'<button type="button" class="btn" data-act="tmwarpscan" data-id="'+id+'" data-server="'+esc(server)+'">'+L('Rescan','اسکن دوباره')+'</button></div></div><div class="tm-warp-list">'+(rows||'<div class="notice warning">'+L('No WARP path result.','نتیجه‌ای برای مسیر WARP وجود ندارد.')+'</div>')+'</div>');
 const node=document.querySelector('#overlay .dialog');if(node)node.classList.add('warp-scan-dialog');
 const auto=document.querySelector('[data-warp-auto-best]'),preview=document.querySelector('[data-warp-best-preview]');
 if(auto&&preview)auto.addEventListener('change',()=>{preview.hidden=!auto.checked;});
}
function showApplyState(value){
 const status=value?.status||'pending';
 toast(status==='applied'?L('Applied to the selected server.','روی سرور انتخاب‌شده اعمال شد.'):status==='error'?L('Apply error: ','خطای اعمال: ')+(value?.error||''):L('Saved; runtime acknowledgement is pending.','ذخیره شد؛ تأیید اعمال سرور در انتظار است.'),status==='error');
}
async function useWarp(el){
 const id=Number(el.dataset.id),server=String(el.dataset.server||'hub'),endpoint=String(el.dataset.endpoint||'');
 const location=String(el.dataset.location||'Cloudflare WARP'),delay=String(el.dataset.delay||'—'),loss=String(el.dataset.loss||'—'),jitter=String(el.dataset.jitter||'—');
 const automatic=String(el.dataset.auto||'')==='1';
 const body='<div class="tm-warp-confirm"><div class="notice '+(automatic?'warning':'')+'">'+
  (automatic?L('You explicitly chose Auto Best. DARK picked this route only from the scan results currently shown.','تو صریحاً Auto Best را انتخاب کردی؛ DARK فقط از نتایج همین اسکن این مسیر را برگزیده است.'):L('Review the selected WARP route before applying it.','قبل از اعمال، مسیر WARP انتخاب‌شده را بررسی کن.'))+
  '</div><div class="tm-warp-confirm-grid"><div><span>'+L('Server','سرور')+'</span><b>'+esc(server)+'</b></div><div><span>'+L('Location','موقعیت')+'</span><b>'+esc(location)+'</b></div><div><span>Endpoint</span><code>'+esc(endpoint)+'</code></div><div><span>Ping</span><b>'+esc(delay)+'</b></div><div><span>Loss</span><b>'+esc(loss)+'</b></div><div><span>Jitter</span><b>'+esc(jitter)+'</b></div></div><label class="tg-switch"><input type="checkbox" name="confirmed" required><span>'+L('Apply exactly this WARP route to the selected runtime','دقیقاً همین مسیر WARP روی سرور انتخاب‌شده اعمال شود')+'</span></label></div>';
 dialog(automatic?L('Confirm Auto Best WARP','تأیید Auto Best WARP'):L('Confirm WARP route','تأیید مسیر WARP'),body,async fd=>{
  if(!fd.has('confirmed'))throw Error(L('Confirm the selected WARP route.','مسیر WARP انتخاب‌شده را تأیید کن.'));
  const result=await api('/api/traffic-matrix/warp/endpoint','POST',{server,endpoint});
  closeDialog();showApplyState(result.applyState);
  if(Number.isSafeInteger(id)&&id>0)await openMatrix(id);else await go('trafficmatrix');
 },L('Apply selected route','اعمال مسیر انتخاب‌شده'));
}
runAction=async function(act,el){
 if(act==='tmopen')return openMatrix(Number(el.dataset.id));
 if(act==='tmapply')return applyOne(el);
 if(act==='tmapplyboth')return applyBoth(el);
 if(act==='tmprobe')return probePath(Number(el.dataset.id),String(el.dataset.server||'hub'),String(el.dataset.path||'direct'));
 if(act==='tmprobeall')return probeAll(el);
 if(act==='tmwarpcreate')return createWarp(el);
 if(act==='tmwarpscan')return scanWarp(el);
 if(act==='tmwarpuse')return useWarp(el);
 return baseRunAction(act,el);
};
globalThis.DarkTrafficMatrix={open:openMatrix,probe:probePath};
})();