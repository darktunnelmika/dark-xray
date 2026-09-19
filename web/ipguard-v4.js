/* DARK XRAY Security Center V4 — Native nftables + global Local/Node policy. */
(function(){
'use strict';
if(typeof ipPage!=='function'||typeof runAction!=='function')return;
const baseRunAction=runAction;
const L=(en,fa)=>((localStorage.getItem('dark_lang')||'en')==='fa'?fa:en);

function stateBadge(code){
 const map={
  global_ip_quota:['IP BLOCK','مسدود IP','red'],
  global_device_quota:['DEVICE BLOCK','مسدود دستگاه','red'],
  client_manual:['MANUAL OFF','قطع دستی','amber'],
  client_quota:['TRAFFIC','اتمام حجم','amber'],
  expired:['EXPIRED','منقضی','amber'],
  owner_manual:['OWNER OFF','مالک غیرفعال','red'],
  owner_account_disabled:['OWNER LOCK','حساب مالک بسته','red'],
  engine_manual_or_external_disable:['ENGINE OFF','هسته غیرفعال','red']
 };
 const row=map[code]||[String(code||'').toUpperCase(),String(code||''),''];
 return `<span class="ip4-badge ${row[2]}">${e(L(row[0],row[1]))}</span>`;
}
function metric(label,value,sub=''){return `<div class="ip4-metric"><span>${e(label)}</span><b>${e(value)}</b>${sub?`<small>${e(sub)}</small>`:''}</div>`;}
function countBar(current,limit,kind){
 const limited=Number(limit)>0,over=limited&&Number(current)>Number(limit),ratio=limited?Math.min(100,Math.round((Number(current)/Math.max(1,Number(limit)))*100)):0;
 return `<div class="ip4-count ${over?'over':''}"><div><b>${fa(current)}</b><span>/ ${limited?fa(limit):'∞'}</span></div><i><u style="width:${limited?ratio:0}%"></u></i><small>${kind}</small></div>`;
}
function clientRow(x){
 const reasons=(x.block_reasons||[]),blocked=x.global_ip_block||x.global_device_block;
 return `<article class="panel ip4-client ${blocked?'blocked':''}">
  <header><div><h3>${e(x.email)}</h3><small>${e(x.owner)} · ${e(x.presence_state||'offline')}</small></div><div class="ip4-badges">${reasons.length?reasons.map(stateBadge).join(''):`<span class="ip4-badge green">${L('CLEAR','سالم')}</span>`}</div></header>
  <div class="ip4-client-counts">
   ${countBar(x.global_ip_count||0,x.limit_ip||0,L('GLOBAL IP','IP سراسری'))}
   ${countBar(x.global_device_count||0,x.limit_hwid||0,'HWID')}
  </div>
  <div class="ip4-source-grid">
   <div><span>${L('Local IP','IP محلی')}</span><b>${fa(x.local_ip_count||0)}</b></div>
   <div><span>${L('Node IP','IP نود')}</span><b>${fa(x.remote_ip_count||0)}</b></div>
   <div><span>${L('Local devices','دستگاه محلی')}</span><b>${fa(x.local_device_count||0)}</b></div>
   <div><span>${L('Node devices','دستگاه نود')}</span><b>${fa(x.remote_device_count||0)}</b></div>
  </div>
  <footer><button class="btn" data-act="ip4inspect" data-id="${e(x.email)}">${icon('eye')}${L('Inspect security','بررسی امنیت')}</button></footer>
 </article>`;
}
function eventRow(x){
 const kind=x.kind||'',cls=kind==='violation'?'warn':kind==='enforcement_failed'?'error':'';
 return `<div class="ip4-event ${cls}"><div><b>${e(kind)}</b><small>${e(x.client_id)} · ${e(x.node||'local')}</small></div><code>${e(x.ip||'—')}</code><span>${date(x.at)}</span><p>${e(x.detail||'')}</p></div>`;
}
function banRow(x){
 return `<div class="ip4-ban"><div><b>${e(x.ip)}</b><small>${e(x.client_id)} · ${e(x.node||'local')}</small></div><span>${L('until','تا')} ${date(x.expires_at)}</span>${isOwner()?button(L('Unban','رفع بن'),'ip4unban','lock',`data-ip="${e(x.ip)}"`,true):''}</div>`;
}
function architecture(d){
 const g=d.guard||{},a=d.architecture||{},nodes=d.nodes;
 const applied=!!g.applied,verified=!!g.source_verified;
 return `<section class="ip4-architecture">
  <article class="panel ip4-arch-card"><small>${L('01 / OBSERVE','۰۱ / مشاهده')}</small><h3>${L('Verified source observer','مشاهده‌گر IP مبدأ')}</h3><b class="${verified?'ok':'warn'}">${verified?L('VERIFIED DIRECT','مستقیم تأییدشده'):L('OBSERVE ONLY','فقط مشاهده')}</b><p>${L('DARK reads Xray access events and counts recent distinct source IPs.','DARK رویدادهای Access هسته را می‌خواند و IPهای متمایز اخیر را می‌شمارد.')}</p></article>
  <article class="panel ip4-arch-card"><small>${L('02 / LOCAL ENFORCE','۰۲ / اعمال محلی')}</small><h3>${L('Native nftables Guard','گارد بومی nftables')}</h3><b class="${applied?'ok':'warn'}">${applied?L('APPLIED','اعمال‌شده'):e(String(g.state||'OBSERVE').toUpperCase())}</b><p>${L('Root-owned DARK broker can block only approved Xray data ports on this host.','کارگزار روت DARK فقط پورت‌های دیتای Xray تأییدشدهٔ همین سرور را مسدود می‌کند.')}</p></article>
  <article class="panel ip4-arch-card"><small>${L('03 / GLOBAL POLICY','۰۳ / سیاست سراسری')}</small><h3>${L('Local + Node aggregation','تجمیع محلی + نود')}</h3><b class="ok">${L('CENTRAL POLICY','سیاست مرکزی')}</b><p>${L('Verified observations from assigned nodes are combined and can block the client service globally.','مشاهده‌های تأییدشده نودها تجمیع می‌شوند و می‌توانند سرویس کاربر را سراسری مسدود کنند.')}</p></article>
  <article class="panel ip4-arch-card"><small>${L('04 / NODE TELEMETRY','۰۴ / تله‌متری نود')}</small><h3>${L('Security telemetry','تله‌متری امنیت')}</h3><b class="${nodes&&nodes.source_verified===nodes.total&&nodes.total?'ok':'warn'}">${nodes?fa(nodes.source_verified)+' / '+fa(nodes.total):'—'}</b><p>${L('Fresh / source-verified node security reports. Stale telemetry never creates a new block.','گزارش‌های تازه و دارای مبدأ تأییدشدهٔ نود؛ تله‌متری قدیمی مسدودی جدید ایجاد نمی‌کند.')}</p></article>
 </section>`;
}
async function securityPage(){
 const d=await api('/api/security-center'),s=d.summary||{},g=d.guard||{};
 state.ip4=d;
 const warning=!g.source_verified?`<div class="notice warning">${L('Direct packet source is not verified on this host. Local automatic nftables bans stay fail-safe/off until root explicitly verifies the source path.','مبدأ واقعی بسته روی این سرور تأیید نشده؛ بن خودکار nftables در حالت ایمن خاموش می‌ماند تا مسیر مبدأ توسط روت تأیید شود.')}</div>`:'';
 const err=g.error?`<div class="notice error">${e(g.error)}</div>`:'';
 return heading(L('DARK Security Center','مرکز امنیت DARK'),L('Native IP/HWID policy across Local Xray and DARK nodes.','سیاست بومی IP/HWID بین Xray محلی و نودهای DARK.'),
  isOwner()?button(L('Guard settings','تنظیمات Guard'),'ip4settings','settings','',true):'')+
 `<div class="ip4">${warning}${err}
   ${architecture(d)}
   <section class="ip4-summary">
    ${metric(L('IP-limited clients','کاربران محدود IP'),fa(s.ip_limited||0))}
    ${metric(L('Global IP blocks','بلاک سراسری IP'),fa(s.ip_blocked||0))}
    ${metric(L('HWID-limited clients','کاربران محدود HWID'),fa(s.hwid_limited||0))}
    ${metric(L('Device blocks','بلاک دستگاه'),fa(s.device_blocked||0))}
    ${metric(L('Active local nft bans','بن محلی nft فعال'),fa(s.active_local_bans||0))}
    ${metric(L('Recent violations','تخلف اخیر'),fa(s.recent_violations||0))}
   </section>
   <section class="ip4-section"><header><div><small>DARK / CLIENT SECURITY MATRIX</small><h2>${L('Client policy state','وضعیت سیاست کاربران')}</h2></div><span>${fa((d.clients||[]).length)}</span></header>
    <div class="ip4-clients">${(d.clients||[]).length?(d.clients||[]).map(clientRow).join(''):empty(L('No clients visible in this security scope.','کاربری در این محدوده امنیتی نیست.'))}</div>
   </section>
   <section class="ip4-two">
    <article class="panel ip4-stream"><header><div><small>DARK / EVENTS</small><h3>${L('Recent policy events','رویدادهای اخیر سیاست')}</h3></div></header><div>${(d.events||[]).length?(d.events||[]).map(eventRow).join(''):empty(L('No recent IP policy events.','رویداد IP اخیر وجود ندارد.'))}</div></article>
    <article class="panel ip4-stream"><header><div><small>DARK / NFTABLES</small><h3>${L('Active local bans','بن‌های فعال محلی')}</h3></div></header><div>${(d.bans||[]).length?(d.bans||[]).map(banRow).join(''):empty(L('No active DARK nftables ban.','بن فعال DARK nftables وجود ندارد.'))}</div></article>
   </section>
   <div class="notice">${L('Local nftables bans are host-local. Multi-node policy is enforced centrally by blocking the client across synchronized DARK runtime; DARK never claims a remote firewall ban unless that host applied one itself.','بن nftables فقط روی همان سرور اعمال می‌شود. سیاست چندنودی به‌صورت مرکزی با مسدودکردن کاربر در محیط اجرای همگام DARK اعمال می‌شود؛ DARK بدون اعمال واقعی روی نود هرگز ادعای بن فایروال راه‌دور نمی‌کند.')}</div>
 </div>`;
}
ipPage=securityPage;

async function inspectClient(id){
 const global=await api('/api/clients/'+enc(id)+'/security-global');
 const localIps=global.local_ips||[],remoteIps=global.remote_ips||[],localDevices=global.local_devices||[],remoteDevices=global.remote_devices||[];
 const ipRows=[...localIps.map(x=>({...x,source:'LOCAL'})),...remoteIps.map(x=>({...x,source:'NODE '+x.node_id}))];
 const devRows=[...localDevices.map(x=>({...x,source:'LOCAL',device_id:x.id})),...remoteDevices.map(x=>({...x,source:'NODE '+x.node_id}))];
 dialog(L('Security inspection','بررسی امنیت')+' · '+id,`<div class="ip4-dialog">
  <section><header><h3>${L('Global policy decision','تصمیم سیاست سراسری')}</h3></header><div class="ip4-dialog-kv">
   <div><span>IP</span><b>${fa(global.ip_count||0)} / ${global.limit_ip?fa(global.limit_ip):'∞'}</b></div>
   <div><span>HWID</span><b>${fa(global.device_count||0)} / ${global.limit_hwid?fa(global.limit_hwid):'∞'}</b></div>
   <div><span>${L('IP enforceable','قابل اعمال IP')}</span><b>${global.ip_enforceable?L('YES','بله'):L('NO / STALE','خیر / ناقص')}</b></div>
   <div><span>${L('Assigned nodes','نودهای مرتبط')}</span><b>${e((global.nodes||[]).join(', ')||'—')}</b></div>
  </div></section>
  <section><header><h3>${L('Observed source IPs','IPهای مشاهده‌شده')}</h3><div><button type="button" class="btn" data-act="ip4clearips" data-id="${e(id)}">${L('Clear history','پاک‌کردن تاریخچه')}</button></div></header>
   <div class="ip4-detail-list">${ipRows.length?ipRows.map(x=>`<div><span class="ip4-source">${e(x.source)}</span><code>${e(x.ip)}</code><small>${date(x.last_seen||x.lastSeen)}</small></div>`).join(''):empty(L('No IP observations.','IP ثبت‌شده‌ای نیست.'))}</div></section>
  <section><header><h3>${L('Registered devices','دستگاه‌های ثبت‌شده')}</h3><div><button type="button" class="btn" data-act="ip4cleardevices" data-id="${e(id)}">${L('Clear devices','پاک‌کردن دستگاه‌ها')}</button></div></header>
   <div class="ip4-detail-list">${devRows.length?devRows.map(x=>`<div><span class="ip4-source">${e(x.source)}</span><b>${e(x.device_os||x.deviceOs||'Unknown')} · ${e(x.model||'—')}</b><small>${date(x.last_seen||x.lastSeen)}</small></div>`).join(''):empty(L('No registered devices.','دستگاهی ثبت نشده.'))}</div></section>
  <div class="notice">${L('Clearing IP history is not an nftables unban. Use Unban only for an active local firewall ban.','پاک‌کردن تاریخچه IP رفع بن nftables نیست؛ برای بن فعال محلی از Unban استفاده کن.')}</div>
 </div>`);
}
runAction=async function(act,el){
 if(act==='ip4settings'){await DarkSettingsV2?.open('ipguard');return;}
 if(act==='ip4inspect'){await inspectClient(el.dataset.id);return;}
 if(act==='ip4unban'){if(confirm(L('Release this IP from the local DARK nftables guard?','این IP از Guard محلی nftables آزاد شود؟'))){await api('/api/ip/unban','POST',{ip:el.dataset.ip});toast(L('Local nftables ban released.','بن محلی nftables آزاد شد.'));await renderPage();}return;}
 if(act==='ip4clearips'){if(confirm(L('Clear Local + Node IP observation history for this client? This is not a firewall unban.','تاریخچهٔ IP محلی و نود این کاربر پاک شود؟ این کار رفع بن فایروال نیست.'))){await api('/api/clients/'+enc(el.dataset.id)+'/ips','DELETE');closeDialog();await renderPage();}return;}
 if(act==='ip4cleardevices'){if(confirm(L('Clear Local + Node registered devices for this client?','دستگاه‌های ثبت‌شدهٔ محلی + نود این کاربر پاک شوند؟'))){await api('/api/clients/'+enc(el.dataset.id)+'/devices','DELETE');closeDialog();await renderPage();}return;}
 return baseRunAction(act,el);
};
globalThis.DarkSecurityCenterV4={open:async()=>go('ipguard')};
})();