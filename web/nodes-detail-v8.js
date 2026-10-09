/* DARK XRAY Nodes V8 — detail workspace and bounded Hub-side history. */
(function(){
'use strict';
if(typeof enginePage!=='function'||typeof runAction!=='function')return;
const pageBase=enginePage,actionBase=runAction;
const L=(en,fa)=>((localStorage.getItem('dark_lang')||'en')==='fa'?fa:en);
let detailTimer=0,detailBusy=false,detailState={id:'',range:'live'};

function btn(id){
 return '<button type="button" class="btn" data-act="nv8details" data-id="'+e(id)+'">'+L('Details','جزئیات')+'</button>';
}
enginePage=async function(){
 const html=await pageBase();
 if(state.page!=='nodes'||!isOwner())return html;
 const t=document.createElement('template');t.innerHTML=html;
 t.content.querySelectorAll('.nv2-actions').forEach(box=>{
  const manage=box.querySelector('[data-act="nv2edit"]');
  if(manage&&!box.querySelector('[data-act="nv8details"]'))box.insertAdjacentHTML('afterbegin',btn(manage.dataset.id));
 });
 return t.innerHTML;
};

function num(v){const n=Number(v);return Number.isFinite(n)?n:null;}
function pct(v){const n=num(v);return n===null?'—':Math.max(0,Math.min(100,n)).toFixed(n>=10?0:1)+'%';}
function duration(v){let s=Math.max(0,Number(v)||0);if(s<60)return Math.floor(s)+'s';if(s<3600)return Math.floor(s/60)+'m';if(s<86400)return Math.floor(s/3600)+'h';return Math.floor(s/86400)+'d '+Math.floor((s%86400)/3600)+'h';}
function rate(v){const n=num(v);return n===null?'—':bytes(Math.max(0,n))+'/s';}
function fresh(n){return n&&n.telemetry_state==='fresh'&&Number(n.telemetry_age_seconds)<=45;}
function live(n,v,fmt){return fresh(n)&&v!==undefined&&v!==null?(fmt||String)(v):'—';}
function metric(label,value){return '<div><small>'+e(label)+'</small><b>'+e(value===undefined||value===null?'—':value)+'</b></div>';}
function healthLabel(s){return s==='healthy'?L('HEALTHY','سالم'):s==='warning'?L('WARNING','هشدار'):s==='critical'?L('CRITICAL','بحرانی'):L('DISABLED','غیرفعال');}

function path(points,key,maxValue){
 const vals=(points||[]).map(p=>num(p&&p[key])),valid=vals.filter(v=>v!==null);
 if(!valid.length)return '';
 const max=maxValue==null?Math.max(1,...valid):maxValue,span=Math.max(1,vals.length-1);
 return vals.map((v,i)=>{
  if(v===null)return null;
  const x=6+308*(i/span),y=78-70*Math.max(0,Math.min(1,v/max));
  return x.toFixed(1)+','+y.toFixed(1);
 }).filter(Boolean).join(' ');
}
function chart(title,points,series,maxValue){
 const letters=['a','b','c'],latest=points.length?points[points.length-1]:null;
 const legend=series.map((s,i)=>{
  const v=latest?num(latest[s.key]):null,label=v===null?'—':(s.format?s.format(v):String(Math.round(v)));
  return '<span class="nv8-legend-'+letters[i]+'"><i></i>'+e(s.label)+' <b>'+e(label)+'</b></span>';
 }).join('');
 const polylines=series.map((s,i)=>{
  const d=path(points,s.key,maxValue);
  return d?'<polyline class="nv8-line nv8-line-'+letters[i]+'" points="'+d+'"></polyline>':'';
 }).join('');
 return '<article class="nv8-chart"><header><b>'+e(title)+'</b><div>'+legend+'</div></header>'+
  (points.length?'<svg viewBox="0 0 320 86" preserveAspectRatio="none" role="img" aria-label="'+e(title)+'"><line x1="6" y1="8" x2="314" y2="8"></line><line x1="6" y1="43" x2="314" y2="43"></line><line x1="6" y1="78" x2="314" y2="78"></line>'+polylines+'</svg>':'<div class="nv8-no-history">'+L('No history samples yet.','هنوز نمونه تاریخچه‌ای ثبت نشده است.')+'</div>')+
  '</article>';
}
function tabs(id,range){
 return '<div class="nv8-range-tabs">'+[['live','LIVE'],['1h','1H'],['24h','24H']].map(row=>
  '<button type="button" class="btn '+(range===row[0]?'btn-primary':'')+'" data-act="nv8range" data-id="'+e(id)+'" data-range="'+row[0]+'">'+row[1]+'</button>'
 ).join('')+'</div>';
}
function inboundNames(n){
 return (n.assignments||[]).map(a=>{
  const ib=(state.inbounds||[]).find(x=>Number(x.id)===Number(a.local_inbound_id));
  return ib?(ib.remark||ib.tag):'#'+a.local_inbound_id;
 });
}
function detailBody(doc){
 const n=doc.node||{},h=n.health||{},sys=h.system||{},core=h.core||{},ops=n.operational_health||{},mem=sys.memory||{},disk=sys.disk||{},net=sys.network||{},conn=sys.connections||{},cpu=sys.cpu_info||{},source=h.installed_source||{},lease=h.hub_lease||{},maint=h.maintenance||{},points=doc.points||[],assigned=inboundNames(n);
 const addresses=(sys.addresses||[]).map(x=>'<span><b>'+e(x.interface||'')+'</b>'+e(x.address||'')+'</span>').join('');
 const score=ops.score==null?'—':ops.score+'/100',cap=ops.capacity_percent==null?'—':pct(ops.capacity_percent);
 const history='<section class="nv8-history"><div class="nv8-section-head"><div><small>PERFORMANCE HISTORY</small><h3>'+L('Performance history','تاریخچه عملکرد')+'</h3></div>'+tabs(n.id,doc.window||'live')+'</div><div class="nv8-chart-grid">'+
  chart('CPU / RAM',points,[{key:'cpu',label:'CPU',format:pct},{key:'memory_percent',label:'RAM',format:pct}],100)+
  chart(L('Health / Capacity','سلامت / ظرفیت'),points,[{key:'health_score',label:L('Health','سلامت'),format:v=>Math.round(v)+'/100'},{key:'capacity_percent',label:L('Capacity','ظرفیت'),format:pct}],100)+
  chart('RX / TX',points,[{key:'rx_bps',label:'RX',format:rate},{key:'tx_bps',label:'TX',format:rate}],null)+
  chart(L('Connections','اتصال‌ها'),points,[{key:'connections',label:L('Open','باز'),format:v=>fa(Math.round(v))}],null)+
  '</div><p class="nv8-history-note">'+e(L('Hub history uses Agent/Xray/system health samples only. Tunnel/WARP/path health is excluded.','تاریخچه Hub فقط از نمونه‌های Health عامل/Xray/سیستم استفاده می‌کند. سلامت Tunnel/WARP/مسیر خارج است.'))+'</p></section>';
 return '<div class="nv8-detail" data-nv8-detail="'+e(n.id)+'"><section class="nv8-hero"><div><small>'+e(n.id||'')+'</small><h2>'+e(n.name||n.id||'Node')+'</h2><p>'+e(n.origin||'')+' · '+e(n.data_address||'—')+'</p></div><div class="nv8-hero-state '+e(ops.state||'disabled')+'"><b>'+e(score)+'</b><span>'+e(healthLabel(ops.state))+'</span><small>'+L('Capacity','ظرفیت')+' '+e(cap)+'</small></div></section>'+
 '<div class="nv8-detail-grid">'+
 '<section><header>'+L('System','سیستم')+'</header><div>'+
 metric('CPU',live(n,sys.cpu,pct))+metric(L('Cores','هسته'),live(n,cpu.logical,String))+metric(L('Load 1m','لود ۱ دقیقه'),live(n,(sys.loads||[])[0],v=>Number(v).toFixed(2)))+
 metric('RAM',live(n,mem.percent??sys.memory_percent,pct))+metric(L('RAM used','RAM مصرفی'),live(n,mem.used,bytes))+metric(L('RAM total','RAM کل'),live(n,mem.total,bytes))+
 metric(L('Disk','دیسک'),live(n,disk.percent??sys.disk_percent,pct))+metric(L('Disk free','فضای آزاد'),live(n,disk.free,bytes))+metric(L('Uptime','آپ‌تایم'),live(n,sys.uptime,duration))+'</div></section>'+
 '<section><header>'+L('Network','شبکه')+'</header><div>'+
 metric('RX',live(n,net.down_bps,rate))+metric('TX',live(n,net.up_bps,rate))+metric(L('Total RX','کل RX'),live(n,net.recv,bytes))+metric(L('Total TX','کل TX'),live(n,net.sent,bytes))+
 metric(L('Connections','اتصال‌ها'),live(n,conn.open,v=>fa(v)))+metric('TCP',live(n,conn.tcp,v=>fa(v)))+metric('UDP',live(n,conn.udp,v=>fa(v)))+metric(L('Latency','تأخیر'),fresh(n)&&n.last_latency_ms?n.last_latency_ms+' ms':'—')+metric(L('Hostname','نام میزبان'),fresh(n)?sys.hostname||'—':'—')+
 '</div>'+(addresses?'<div class="nv8-address-list">'+addresses+'</div>':'')+'</section>'+
 '<section><header>'+L('Runtime & Accounting','اجرا و حسابداری')+'</header><div>'+
 metric(L('Agent','عامل'),fresh(n)?h.version||source.version||'—':'—')+metric('Xray',fresh(n)?core.version||'—':'—')+metric(L('Xray state','وضعیت Xray'),fresh(n)?core.state||'—':'—')+
 metric(L('Hub lease','مجوز Hub'),fresh(n)?(lease.valid?L('Active','فعال'):L('Blocked','مسدود')):'—')+metric(L('Checkpoint','چک‌پوینت'),fresh(n)&&maint.checkpoint_age_seconds!=null?Number(maint.checkpoint_age_seconds).toFixed(1)+'s':'—')+
 metric(L('Tracked traffic','ترافیک ثبت‌شده'),bytes(Number(n.traffic_current_bytes||0)))+metric(L('Managed clients','کاربران مدیریت‌شده'),fa(Number(h.managed_clients||0)))+metric(L('Source','سورس'),source.commit?String(source.commit).slice(0,12):'—')+metric(L('Last report','آخرین گزارش'),n.telemetry_age_seconds==null?'—':duration(n.telemetry_age_seconds))+
 '</div></section>'+
 '<section><header>'+L('Deployment','استقرار')+'</header><div>'+
 metric(L('Assigned inbounds','اینباندهای تخصیص'),fa(assigned.length))+metric(L('Desired revision','نسخه مطلوب'),String(n.desired_state?.revision||0))+metric(L('Applied revision','نسخه اعمال‌شده'),String(n.desired_state?.applied_revision||0))+metric(L('Pending','در انتظار'),n.desired_state?.pending?L('Yes','بله'):L('No','خیر'))+
 metric(L('Security sync','همگام امنیت'),n.security?.last_sync?date(n.security.last_sync):'—')+metric(L('Traffic sync','همگام ترافیک'),n.traffic_last_sync?date(n.traffic_last_sync):'—')+
 '</div>'+(assigned.length?'<div class="nv8-assigned-list">'+assigned.map(x=>'<span>'+e(x)+'</span>').join('')+'</div>':'')+'</section>'+
 '</div>'+history+'</div>';
}
async function fetchDetail(id,range){return api('/api/nodes/'+enc(id)+'/metrics?window='+enc(range));}
async function refreshDetail(){
 if(detailBusy||!detailState.id||document.hidden)return;
 const root=document.querySelector('[data-nv8-detail]');
 if(!root){if(detailTimer)clearInterval(detailTimer);detailTimer=0;detailState={id:'',range:'live'};return;}
 detailBusy=true;
 try{const doc=await fetchDetail(detailState.id,detailState.range);root.outerHTML=detailBody(doc);}catch(_ex){}finally{detailBusy=false;}
}
function armDetail(){if(detailTimer)clearInterval(detailTimer);detailTimer=setInterval(refreshDetail,5000);}
async function openDetail(id,range){
 const doc=await fetchDetail(id,range||'live');detailState={id:id,range:range||'live'};
 dialog(L('Node Details','جزئیات نود'),detailBody(doc),null);armDetail();
}

runAction=async function(act,el){
 if(act==='nv8details'){await openDetail(el.dataset.id,'live');return;}
 if(act==='nv8range'){
  const id=el.dataset.id,range=el.dataset.range;detailState={id,range};
  const doc=await fetchDetail(id,range),root=document.querySelector('[data-nv8-detail]');
  if(root)root.outerHTML=detailBody(doc);
  return;
 }
 return actionBase(act,el);
};
})();