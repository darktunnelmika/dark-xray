/* Owner-only, read-only Operations V3; renders in the active Overview V4. */
(function(){
'use strict';
const L=(en,fa)=>((localStorage.getItem('dark_lang')||'en')==='fa'?fa:en);
const esc=x=>e(String(x??''));
const labels={applied:['Applied','اعمال‌شده'],pending:['Pending','در انتظار'],error:['Error','خطا'],stopped:['Stopped','متوقف'],offline:['Offline','آفلاین'],stale:['Stale','اطلاعات قدیمی'],unknown:['Unknown','نامشخص'],online:['Online','آنلاین'],disabled:['Disabled','غیرفعال'],unconfigured:['Not configured','تنظیم نشده'],not_configured:['Not configured','تنظیم نشده']};
function label(s){const a=labels[s]||labels.unknown;return L(...a);}
function tone(s){return ['error','offline'].includes(s)?'bad':['pending','stale','stopped'].includes(s)?'warn':['applied','online'].includes(s)?'good':'neutral';}
function when(t){return t?new Date(t*1000).toLocaleString((localStorage.getItem('dark_lang')||'en')==='fa'?'fa-IR':'en-GB'):'—';}
function current(){
 const box=state.ov2||{},o=box.overview,account=state.me?.id||state.me?.username||'owner';
 if(state.me?.role!=='owner'||box.overviewOwner!==account)return null;
 return o&&typeof o.generated_at==='number'&&o.hub&&Array.isArray(o.nodes)&&Array.isArray(o.telegram)&&Array.isArray(o.warp)?o:null;
}
function age(o){return o?Date.now()/1000-o.generated_at:Infinity;}
function attention(){
 if(state.me?.role!=='owner')return false;
 const o=current();return !o||age(o)>60||age(o)<0||o.hub.status!=='applied'||o.summary?.nodes_attention>0||o.summary?.bots_attention>0||o.summary?.warp_attention>0;
}
function card(name,status,detail){return '<div class="ov3-service '+tone(status)+'"><i></i><div><b>'+esc(name)+'</b><strong>'+esc(label(status))+'</strong><small>'+esc(detail)+'</small></div></div>';}
function aggregate(rows,get,ignored=[]){
 const values=rows.map(get).filter(x=>!ignored.includes(x));
 if(!values.length)return 'not_configured';
 return ['error','offline','stopped','stale','unknown','pending'].find(x=>values.includes(x))||'applied';
}
function row(name,status,detail,error=''){
 return '<div class="ov3-detail-row"><b>'+esc(name)+'</b><span class="'+tone(status)+'">'+esc(label(status))+'</span><small>'+esc(detail)+'</small>'+(error?'<p class="ov3-error">'+esc(error)+'</p>':'')+'</div>';
}
function render(){
 if(state.me?.role!=='owner')return '';
 const o=current(),stale=age(o)>60||age(o)<0,available=!!o&&!stale;
 let content='';
 if(!available){
  content='<div class="notice warning" role="status">'+L('Service status unavailable or out of date. No healthy state is assumed.','وضعیت سرویس‌ها در دسترس نیست یا قدیمی است؛ سالم‌بودن فرض نمی‌شود.')+'</div>';
 }else{
  const ns=aggregate(o.nodes,x=>x.status),bs=aggregate(o.telegram,x=>x.state,['disabled']),ws=aggregate(o.warp,x=>x.status,['not_configured']);
  content='<div class="ov3-service-grid">'+card(L('Hub / Xray','هاب / Xray'),o.hub.status,L('Last apply: ','آخرین اعمال: ')+when(o.hub.applied_at))+
   card(L('Node apply','اعمال نودها'),ns,o.nodes.length+' '+L('enabled nodes','نود فعال'))+
   card(L('Telegram bots','ربات‌های تلگرام'),bs,o.telegram.filter(x=>x.enabled).length+' '+L('enabled bots','ربات فعال'))+
   card(L('WARP configuration','تنظیمات WARP'),ws,L('Saved/apply state only; connectivity not tested.','فقط وضعیت ذخیره و اعمال؛ اتصال آزمایش نشده است.'))+'</div>';
  const details=row(L('Hub / Xray','هاب / Xray'),o.hub.status,L('Last apply: ','آخرین اعمال: ')+when(o.hub.applied_at),o.hub.last_error)+
   o.nodes.map(x=>row(x.name,x.status,L('Last seen: ','آخرین مشاهده: ')+when(x.last_seen)+' · '+L('Last apply: ','آخرین اعمال: ')+when(x.applied_at),x.last_error)).join('')+
   o.telegram.map(x=>row('@'+(x.username||x.owner),x.state,L('Last seen: ','آخرین مشاهده: ')+when(x.last_seen),x.last_error)).join('')+
   o.warp.map(x=>row(x.name+' · WARP',x.status,(x.endpoint||'—')+' · '+L('Connectivity not tested','اتصال آزمایش نشده'),x.last_error)).join('');
  content+='<details class="ov3-details" data-operations-details><summary>'+L('Server and bot details','جزئیات سرورها و ربات‌ها')+'</summary><div>'+details+'</div></details>';
 }
 return '<section class="ov4-card ov3-services" data-operations-v3><header class="ov4-card-head"><div><span>'+L('SERVICE STATUS','وضعیت سرویس‌ها')+'</span><small>'+L('Observed: ','زمان مشاهده: ')+when(o?.generated_at)+'</small></div><button type="button" class="ov4-small-button" data-act="ov3refresh">'+L('Refresh status','تازه‌سازی وضعیت')+'</button></header>'+content+'</section>';
}
const previous=runAction;
runAction=async function(act,el){
 if(act!=='ov3refresh')return previous(act,el);
 if(state.me?.role!=='owner')return;
 if(el)el.disabled=true;
 const account=state.me?.id||state.me?.username||'owner';
 try{
  const o=await api('/api/operations/overview');
  if(state.me?.role!=='owner'||account!==(state.me?.id||state.me?.username||'owner'))return;
  state.ov2.overview=o;state.ov2.overviewOwner=account;state.ov2.overviewError='';
 }catch{
  state.ov2.overview=null;state.ov2.overviewError='unavailable';
 }finally{if(el?.isConnected)el.disabled=false;}
 await renderPage();
};
globalThis.DarkOperationsV3={render,attention,label};
})();
