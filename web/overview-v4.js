/* DARK XRAY Overview V4 — monitoring-first owner dashboard. */
(function(){
'use strict';
if(typeof dashboard!=='function'||typeof load!=='function'||typeof runAction!=='function')return;
const baseDashboard=dashboard,baseLoad=load,baseRunAction=runAction;
const L=(en,fa)=>((localStorage.getItem('dark_lang')||'en')==='fa'?fa:en);
state.ov4=state.ov4||{history:{cpu:[],mem:[],swap:[],disk:[],up:[],down:[],tcp:[],udp:[]},max:60};

function number(v,fallback=0){v=Number(v);return Number.isFinite(v)?v:fallback;}
function pct(v){if(!v||!number(v.total))return 0;return Math.max(0,Math.min(100,100*number(v.current??v.used)/number(v.total)));}
function avg(a){return a.length?a.reduce((x,y)=>x+y,0)/a.length:0;}
function peak(a){return a.length?Math.max(...a):0;}
function push(name,value){let a=state.ov4.history[name];a.push(number(value));if(a.length>state.ov4.max)a.splice(0,a.length-state.ov4.max);}
function sample(){
 const n=state.system?.engine;if(!n)return;
 push('cpu',n.cpu);push('mem',pct(n.mem));push('swap',pct(n.swap));push('disk',pct(n.disk));
 push('up',n.netIO?.up);push('down',n.netIO?.down);
 push('tcp',n.connections?.tcp);push('udp',n.connections?.udp);
}
load=async function(){await baseLoad();if(state.page==='dashboard'&&state.system?.engine)sample();};

function linePoints(values,w,h,maxValue=null){
 const a=(values&&values.length?values:[0]).slice(-60),max=Math.max(1,maxValue||Math.max(...a,1)),den=Math.max(1,a.length-1);
 return a.map((v,i)=>`${(i*w/den).toFixed(1)},${(h-(Math.max(0,number(v))/max)*(h-4)-2).toFixed(1)}`).join(' ');
}
function spark(values,max=100){return `<svg class="ov4-spark" viewBox="0 0 120 32" preserveAspectRatio="none" aria-hidden="true"><polyline points="${linePoints(values,120,32,max)}"/></svg>`;}
function fmtRate(v){return bytes(number(v))+'/s';}
function fmtCpu(v){v=number(v);return v<=0?'<0.1':v.toFixed(1);}
function fmtUptime(sec){sec=Math.max(0,number(sec));let d=Math.floor(sec/86400),h=Math.floor(sec%86400/3600),m=Math.floor(sec%3600/60);return (d?d+'d ':'')+h+'h '+m+'m';}

function commandBar(){
 const n=state.system?.engine||{},core=state.ov2?.core||state.sync?.runtime||{},running=core.state==='running'||n.xray?.state==='running';
 const up=state.updateCenter?.status||{},cand=up.candidate||{},update=cand.update_available;
 let left=`<div class="ov4-status-pill ${running?'good':'bad'}"><i></i><span>Xray · ${e(running?L('Running','در حال اجرا'):L('Stopped','متوقف'))}</span><b>${e(n.xray?.version||state.sync?.engine_version||'—')}</b></div>`;
 left+=`<div class="ov4-status-pill ${update?'warn':'good'}"><i></i><span>${update?L('Update available','آپدیت موجود'):L('Build current','نسخه به‌روز')}</span><b>${e(cand.version||state.me?.version||'')}</b></div>`;
 if(!isOwner())return `<section class="ov4-commandbar"><div class="ov4-command-status">${left}</div></section>`;
 const action=(name,label,ic,danger=false)=>`<button class="ov4-action ${danger?'danger':''}" data-act="${name}">${icon(ic)}<span>${label}</span></button>`;
 return `<section class="ov4-commandbar"><div class="ov4-command-status">${left}</div><div class="ov4-command-actions">
 ${action('ov4restart',L('Restart','ری‌استارت'),'refresh')}${action('ov4stop',L('Stop','توقف'),'power',true)}
 <i class="ov4-sep"></i>${action('ov4logs',L('Logs','لاگ‌ها'),'terminal')}${action('ov4config',L('Config','کانفیگ'),'settings')}
 ${action('ov4backup',L('Backup & Restore','بکاپ و بازیابی'),'download')}<i class="ov4-sep"></i>
 ${action('ov4history',L('System History','تاریخچه سیستم'),'log')}${action('ov4metrics',L('Xray Metrics','متریک Xray'),'activity')}
 </div></section>`;
}

function resourceCard(key,label,value,detail,history,extra=''){
 return `<article class="ov4-resource ov4-resource-${key}">
   <header><span>${icon(key==='cpu'?'activity':key==='ram'?'server':key==='swap'?'refresh':'database')}${label}</span></header>
   <div class="ov4-resource-value">${e(value)}<small>%</small></div>
   <div class="ov4-resource-detail">${e(detail)}</div>
   <div class="ov4-resource-meta"><span>AVG ${e(avg(history).toFixed(1))}%</span><span>PEAK ${e(peak(history).toFixed(1))}%</span></div>
   ${spark(history,100)}${extra}
 </article>`;
}
function resourceRow(){
 const n=state.system?.engine||{},h=state.ov4.history,ci=n.cpuInfo||{},freq=number(ci.mhz);
 return `<section class="ov4-resource-grid">
 ${resourceCard('cpu','CPU',fmtCpu(n.cpu),`${ci.physical||'—'} ${L('Cores','هسته')} / ${ci.logical||'—'}T${freq?' · '+(freq/1000).toFixed(2)+' GHz':''}`,h.cpu)}
 ${resourceCard('ram','RAM',pct(n.mem).toFixed(1),`${bytes(n.mem?.current)} / ${bytes(n.mem?.total)}`,h.mem)}
 ${resourceCard('swap','SWAP',pct(n.swap).toFixed(1),`${bytes(n.swap?.current)} / ${bytes(n.swap?.total)}`,h.swap)}
 ${resourceCard('disk',L('STORAGE','فضای دیسک'),pct(n.disk).toFixed(1),`${bytes(n.disk?.current)} / ${bytes(n.disk?.total)} · ${L('free','آزاد')} ${bytes(n.disk?.free)}`,h.disk)}
 </section>`;
}

function trafficChart(){
 const n=state.system?.engine||{},h=state.ov4.history,max=Math.max(1,...h.up,...h.down),w=900,hgt=250;
 const up=linePoints(h.up,w,hgt,max),down=linePoints(h.down,w,hgt,max);
 const upAvg=avg(h.up),downAvg=avg(h.down);
 return `<article class="ov4-card ov4-traffic-card" id="ov4-traffic">
 <header class="ov4-card-head"><div><span>${L('OVERALL SPEED','سرعت کلی')}</span><small>${L('Live interface rate · real samples only','نرخ زنده اینترفیس · فقط نمونه واقعی')}</small></div>
 <div class="ov4-live-rates"><b class="up">↑ ${fmtRate(n.netIO?.up)}</b><b class="down">↓ ${fmtRate(n.netIO?.down)}</b></div></header>
 <div class="ov4-chart-wrap"><svg viewBox="0 0 ${w} ${hgt}" preserveAspectRatio="none" aria-label="Network traffic chart">
 <defs><linearGradient id="ov4UpFill" x1="0" y1="0" x2="0" y2="1"><stop class="ov4-up-stop" offset="0" stop-opacity=".24"/><stop class="ov4-up-stop" offset="1" stop-opacity="0"/></linearGradient><linearGradient id="ov4DownFill" x1="0" y1="0" x2="0" y2="1"><stop class="ov4-down-stop" offset="0" stop-opacity=".12"/><stop class="ov4-down-stop" offset="1" stop-opacity="0"/></linearGradient></defs>
 <path class="grid" d="M0 50H900M0 100H900M0 150H900M0 200H900"/>
 <polygon class="area up" points="0,250 ${up} 900,250"/><polygon class="area down" points="0,250 ${down} 900,250"/>
 <polyline class="line up" points="${up}"/><polyline class="line down" points="${down}"/></svg></div>
 <footer class="ov4-traffic-foot"><div><span>${L('SENT','ارسال')}</span><b>${bytes(n.netTraffic?.sent)}</b></div><div><span>${L('RECEIVED','دریافت')}</span><b>${bytes(n.netTraffic?.recv)}</b></div><div><span>${L('AVG OVER WINDOW','میانگین بازه')}</span><b>↑ ${fmtRate(upAvg)} · ↓ ${fmtRate(downAvg)}</b></div></footer>
 </article>`;
}
function connectionCard(){
 const n=state.system?.engine||{},c=n.connections||{},h=state.ov4.history,max=Math.max(1,...h.tcp,...h.udp);
 return `<article class="ov4-card ov4-connections"><header class="ov4-card-head"><div><span>${L('CONNECTION STATS','وضعیت اتصال')}</span><small>${c.available===false?L('System socket enumeration unavailable','شمارش سوکت در دسترس نیست'):L('Host inet sockets','سوکت‌های میزبان')}</small></div></header>
 <div class="ov4-connection-total"><b>${c.available===false?'—':fa(c.open||0)}</b><span>${L('open sockets','سوکت باز')}</span></div>
 <div class="ov4-connection-legend"><span class="tcp"><i></i>TCP <b>${c.available===false?'—':fa(c.tcp||0)}</b></span><span class="udp"><i></i>UDP <b>${c.available===false?'—':fa(c.udp||0)}</b></span></div>
 <div class="ov4-connection-chart"><svg viewBox="0 0 420 165" preserveAspectRatio="none"><path class="grid" d="M0 40H420M0 80H420M0 120H420"/><polyline class="tcp" points="${linePoints(h.tcp,420,165,max)}"/><polyline class="udp" points="${linePoints(h.udp,420,165,max)}"/></svg></div>
 </article>`;
}
function telemetryStrip(){
 const n=state.system?.engine||{},addresses=(n.addresses||[]).slice(0,3);
 return `<section class="ov4-telemetry-strip">
 <div><span>${icon('activity')} ${L('UPTIME','آپ‌تایم')}</span><section><p><small>XRAY</small><b>${fmtUptime(n.xray?.uptime)}</b></p><p><small>OS</small><b>${fmtUptime(n.uptime)}</b></p></section></div>
 <div><span>${icon('server')} ${L('PANEL','پنل')}</span><section><p><small>RAM</small><b>${bytes(n.panel?.mem)}</b></p><p><small>${L('THREADS','تردها')}</small><b>${fa(n.panel?.threads||0)}</b></p></section></div>
 <div class="ov4-addresses"><span>${icon('globe')} IP ADDRESSES</span><section>${addresses.length?addresses.map(a=>`<p><small>${e(a.interface)}</small><b class="mono">${e(a.address)}</b></p>`).join(''):`<p><b class="muted">${L('No non-loopback address reported','آدرس غیرلوپ‌بک گزارش نشده')}</b></p>`}</section></div>
 </section>`;
}

function darkSummary(){
 const clients=state.clients||[],active=clients.filter(x=>x.client?.enable!==false&&!x.block_reasons?.length).length,blocked=clients.filter(x=>x.block_reasons?.length).length;
 const nodes=state.ov2?.nodes||[],enabled=nodes.filter(n=>n.enabled),online=enabled.filter(n=>n.online).length;
 const reps=state.resellers||[],activeReps=reps.filter(r=>r.enabled).length,n=state.system?.engine||{};
 const item=(label,value,sub,act='')=>`<button class="ov4-summary-item" ${act?`data-act="${act}"`:'disabled'}><span>${label}</span><b>${value}</b><small>${sub}</small></button>`;
 return `<section class="ov4-summary-grid">
 ${item(L('ACTIVE CLIENTS','کاربران فعال'),fa(active),`${clients.length} ${L('total','کل')}`,'ov4clients')}
 ${item(L('BLOCKED','محدود'),fa(blocked),L('policy / quota','سیاست / سهمیه'),'ov4clients')}
 ${item(L('INBOUNDS','اینباندها'),fa(state.inbounds?.length||0),'Xray','ov4config')}
 ${item(L('NODES ONLINE','نود آنلاین'),`${online}/${enabled.length}`,L('multi-node','چندنودی'),'ov4nodes')}
 ${item(L('REPRESENTATIVES','نمایندگان'),fa(activeReps),`${reps.length} ${L('total','کل')}`,'ov4reps')}
 ${item(L('XRAY MEMORY','حافظه Xray'),bytes(n.xray?.mem),e(n.xray?.state||'—'),'ov4metrics')}
 </section>`;
}

function nodeCard(){
 if(!isOwner())return '';
 const nodes=state.ov2?.nodes||[],enabled=nodes.filter(n=>n.enabled),online=enabled.filter(n=>n.online),offline=enabled.filter(n=>!n.online),errors=nodes.filter(n=>n.last_error||(n.assignments||[]).some(a=>a.last_error));
 return `<article class="ov4-card ov4-node-card"><header class="ov4-card-head"><div><span>${L('NODE FLEET','ناوگان نودها')}</span><small>${L('Remote DARK agents','عامل‌های راه‌دور DARK')}</small></div><button class="ov4-link" data-act="ov4nodes">${L('Manage','مدیریت')} →</button></header>
 <div class="ov4-node-kpis"><div><b class="good">${online.length}</b><span>${L('Online','آنلاین')}</span></div><div><b class="${offline.length?'bad':''}">${offline.length}</b><span>${L('Offline','آفلاین')}</span></div><div><b class="${errors.length?'warn':''}">${errors.length}</b><span>${L('Sync errors','خطای همگام‌سازی')}</span></div></div>
 <div class="ov4-node-list">${nodes.length?nodes.slice(0,5).map(n=>`<div><i class="${!n.enabled?'off':n.online?'good':'bad'}"></i><span><b>${e(n.name||n.id)}</b><small>${e((n.inboundIds||[]).length)} ${L('inbounds','اینباند')}${n.last_latency_ms?' · '+e(n.last_latency_ms)+' ms':''}</small></span><em>${n.enabled?(n.online?L('Online','آنلاین'):L('Offline','آفلاین')):L('Disabled','غیرفعال')}</em></div>`).join(''):`<div class="ov4-empty">${L('No remote nodes registered yet.','هنوز نودی ثبت نشده است.')}</div>`}</div></article>`;
}

function backupCard(){
 const b=state.ov2?.backup||{};
 return `<article class="ov4-card ov4-backup-card"><header class="ov4-card-head"><div><span>${L('BACKUP & RECOVERY','بکاپ و بازیابی')}</span><small>${L('Rollback-safe operations','عملیات امن با قابلیت بازگشت')}</small></div><button class="ov4-link" data-act="ov4backup">${L('Open','بازکردن')} →</button></header>
 <div class="ov4-backup-main"><div><span>${L('Database snapshot','اسنپ‌شات دیتابیس')}</span><b>${b.database_bytes===undefined?'—':bytes(b.database_bytes)}</b></div><div><span>${L('Managed clients','کاربران')}</span><b>${b.managed_clients===undefined?'—':fa(b.managed_clients)}</b></div></div>
 <div class="ov4-safe"><i></i><span>${b.restore_isolated===false?L('Restore isolation unavailable','ایزوله‌سازی بازیابی در دسترس نیست'):L('Isolated restore enforced','بازیابی ایزوله اجباری است')}</span></div>
 <div class="ov4-card-actions"><a class="ov4-small-button" href="${appUrl(b.database_download||'/api/backup')}">${icon('download')}${L('Download snapshot','دانلود اسنپ‌شات')}</a><button class="ov4-small-button" data-act="ov2restorehelp">${icon('refresh')}${L('Verify / Restore','بررسی / بازیابی')}</button></div></article>`;
}

function repsCard(){
 if(!isOwner())return '';
 const reps=state.resellers||[];
 return `<article class="ov4-card ov4-reps-card"><header class="ov4-card-head"><div><span>${L('REPRESENTATIVES','نمایندگان')}</span><small>${L('Unified representative accounts','حساب‌های یکپارچه نمایندگی')}</small></div><button class="ov4-link" data-act="ov4reps">${L('Manage','مدیریت')} →</button></header>
 <div class="ov4-reps-list">${reps.length?reps.slice(0,5).map(r=>`<div><i class="${r.enabled?'good':'off'}"></i><span><b>${e(r.name||r.id)}</b><small>${fa(r.client_count||0)} ${L('clients','کاربر')} · ${r.quota_bytes?bytes(r.quota_bytes):'∞'}</small></span><em>${r.enabled?L('Active','فعال'):L('Disabled','غیرفعال')}</em></div>`).join(''):`<div class="ov4-empty">${L('No representatives yet.','هنوز نماینده‌ای ساخته نشده است.')}</div>`}</div></article>`;
}

function healthCard(){
 const core=state.ov2?.core||state.sync?.runtime||{},nodes=state.ov2?.nodes||[],enabled=nodes.filter(n=>n.enabled),offline=enabled.filter(n=>!n.online),errors=nodes.filter(n=>n.last_error),bans=state.ov2?.ip?.bans?.length||0,up=state.updateCenter||{},rows=[];
 const add=(kind,title,detail)=>rows.push({kind,title,detail});
 if(core.last_error)add('bad',L('Runtime error','خطای محیط اجرا'),core.last_error);
 if(core.dirty)add('warn',L('Config pending apply','کانفیگ در انتظار اعمال'),L('Saved state differs from running Xray.','وضعیت ذخیره‌شده با Xray در حال اجرا متفاوت است.'));
 if(offline.length)add('bad',L('Nodes offline','نود آفلاین'),offline.map(n=>n.name||n.id).join(', '));
 if(errors.length)add('warn',L('Node sync errors','خطای همگام‌سازی نود'),errors.map(n=>n.name||n.id).join(', '));
 if(bans)add('warn',L('IP Guard bans','بن‌های IP Guard'),fa(bans));
 if(up.error)add('bad',L('Update broker error','خطای Update Broker'),up.error);
 if(!rows.length)add('good',L('All monitored systems nominal','همه سیستم‌های مانیتورشده سالم هستند'),L('No active runtime, node or update alert.','هشدار فعالی برای محیط اجرا، نود یا آپدیت وجود ندارد.'));
 return `<article class="ov4-card ov4-health-card"><header class="ov4-card-head"><div><span>${L('HEALTH & ALERTS','سلامت و هشدارها')}</span><small>${L('Operational issues first','اولویت با مشکلات عملیاتی')}</small></div><button class="ov4-link" data-act="ov4doctor">Doctor →</button></header><div class="ov4-alert-list">${rows.slice(0,5).map(r=>`<div class="${r.kind}"><i></i><span><b>${e(r.title)}</b><small>${e(r.detail)}</small></span></div>`).join('')}</div></article>`;
}
const activityScopes={
 settings:['Settings','تنظیمات'],client:['Client','کاربر'],reseller:['Representative','نماینده'],owner:['Owner','مالک'],
 inbound:['Inbound','اینباند'],node:['Node','نود'],security:['Security','امنیت'],system:['System','سیستم'],
 core:['Xray Core','هسته Xray'],update:['Update','آپدیت'],backup:['Backup','بکاپ'],auth:['Account','حساب'],
 subscription:['Subscription','اشتراک']
};
const activityVerbs={
 create:['Created','ساخت'],update:['Updated','بروزرسانی'],delete:['Deleted','حذف'],edit:['Edited','ویرایش'],
 add:['Added','افزودن'],remove:['Removed','حذف'],enable:['Enabled','فعال‌سازی'],disable:['Disabled','غیرفعال‌سازی'],
 reset:['Reset','ریست'],start:['Started','اجرا'],stop:['Stopped','توقف'],restart:['Restarted','راه‌اندازی مجدد'],
 login:['Signed in','ورود'],logout:['Signed out','خروج'],sync:['Synchronized','همگام‌سازی'],apply:['Applied','اعمال'],
 check:['Checked','بررسی'],download:['Downloaded','دانلود'],restore:['Restored','بازیابی']
};
function activityLabel(code){
 const raw=String(code||''),parts=raw.split('.').filter(Boolean);
 if((localStorage.getItem('dark_lang')||'en')!=='fa'){
   const scope=activityScopes[parts[0]],verb=activityVerbs[parts[1]];
   return scope&&verb?scope[0]+' · '+verb[0]:raw;
 }
 const scope=activityScopes[parts[0]],verb=activityVerbs[parts[1]];
 if(scope&&verb)return scope[1]+' · '+verb[1];
 if(scope)return scope[1]+' · '+L('Event','رویداد');
 return L('System event','رویداد سیستمی');
}
function activityCard(){
 const rows=(state.ov2?.audit||[]).slice(0,7);
 return `<article class="ov4-card ov4-activity-card"><header class="ov4-card-head"><div><span>${L('RECENT ACTIVITY','فعالیت‌های اخیر')}</span><small>${L('Audit-backed events','رویدادهای مبتنی بر گزارش عملیات')}</small></div><button class="ov4-link" data-act="ov4history">${L('View all','همه')} →</button></header><div class="ov4-activity-list">${rows.length?rows.map(r=>`<div><time>${date(r.at)}</time><span>${e(r.actor)}</span><b title="${e(r.action)}">${e(activityLabel(r.action))}</b><em>${e(r.target)}</em></div>`).join(''):`<div class="ov4-empty">${L('No recent audit events.','رویداد اخیر وجود ندارد.')}</div>`}</div></article>`;
}

dashboard=function(){
 if(!state.me)return baseDashboard();
 return heading(L('System Overview','نمای کلی سیستم'),L('Live host, Xray and DARK control-plane telemetry.','تله‌متری زندهٔ میزبان، Xray و لایهٔ کنترل DARK.'))+notices()+
 `<div class="ov4">${commandBar()}${resourceRow()}<section class="ov4-main-grid">${trafficChart()}${connectionCard()}</section>${telemetryStrip()}${darkSummary()}<section class="ov4-management-grid">${nodeCard()}<div id="dark-update-center-slot"></div></section><section class="ov4-lower-grid">${backupCard()}${repsCard()}${healthCard()}${activityCard()}</section></div>`;
};

runAction=async function(act,el){
 if(act==='ov4restart'){if(confirm(L('Restart Xray? Active sessions can disconnect.','Xray ری‌استارت شود؟ اتصال‌های فعال ممکن است قطع شوند.'))){await api('/api/core/restart','POST',{});toast(L('Xray restarted.','Xray ری‌استارت شد.'));await refresh();}return;}
 if(act==='ov4stop'){if(confirm(L('Stop Xray? Proxy traffic will stop until it is started again.','Xray متوقف شود؟ ترافیک پروکسی تا اجرای مجدد قطع می‌شود.'))){await api('/api/core/stop','POST',{});toast(L('Xray stopped.','Xray متوقف شد.'));await refresh();}return;}
 if(act==='ov4logs'){if(globalThis.DarkSettingsV2?.open){await globalThis.DarkSettingsV2.open('operations');return;}await go('settings');return;}
 if(act==='ov4config'){await go('xray');return;}
 if(act==='ov4backup'){await go('backup');return;}
 if(act==='ov4history'){if(globalThis.DarkSettingsV2?.open){await globalThis.DarkSettingsV2.open('operations');return;}await go('settings');return;}
 if(act==='ov4nodes'){await go('nodes');return;}
 if(act==='ov4reps'){await go('resellers');return;}
 if(act==='ov4clients'){await go('clients');return;}
 if(act==='ov4doctor'){return baseRunAction('ov2doctorhelp',el);}
 if(act==='ov4metrics'){document.querySelector('#ov4-traffic')?.scrollIntoView({behavior:'smooth',block:'center'});return;}
 return baseRunAction(act,el);
};
})();