/* DARK XRAY Credits & Ledger V4 — representative resource credits + immutable traffic. */
(function(){
'use strict';
if(typeof financePage!=='function'||typeof runAction!=='function')return;
const oldRunAction=runAction;
const FV={representative:'all'};
const L=(en,fa)=>((localStorage.getItem('dark_lang')||'en')==='fa'?fa:en);
const creditTime=r=>Number(r?.at||0);
const trafficTime=r=>Number(r?.observed_at||0);
const sum=(rows,fn)=>rows.reduce((a,r)=>a+Number(fn(r)||0),0);
const ownerMode=()=>typeof isOwner==='function'?isOwner():true;
const reps=()=>Array.isArray(state.resellers)?state.resellers:[];
const profileRows=()=>ownerMode()?reps():(Array.isArray(state.owners)?state.owners:[]);
const repName=id=>reps().find(r=>r.id===id)?.name||state.owners?.find(o=>o.id===id)?.name||id;
function chip(label,key,value,current){return `<button type="button" class="fv2-chip ${String(current)===String(value)?'active':''}" data-act="fv2filter" data-key="${e(key)}" data-value="${e(value)}">${e(label)}</button>`;}
function representativeIds(){return new Set(reps().map(r=>r.id));}
function filterRepresentative(rows){
 if(!ownerMode())return rows;
 const ids=representativeIds();
 let out=rows.filter(r=>ids.has(r.owner));
 return FV.representative==='all'?out:out.filter(r=>r.owner===FV.representative);
}
function selectedProfiles(){
 const rows=profileRows();
 if(!ownerMode())return rows;
 return FV.representative==='all'?rows:rows.filter(r=>r.id===FV.representative);
}
function signedBytes(v){v=Number(v||0);return (v>0?'+':v<0?'-':'')+bytes(Math.abs(v));}
function signedUnits(v){v=Number(v||0);return (v>0?'+':'')+fa(v);}
function creditTable(rows){
 rows=filterRepresentative(rows);
 if(!rows.length)return empty(L('No representative resource-credit events match this filter.','رویداد اعتبار نماینده‌ای مطابق این فیلتر وجود ندارد.'));
 return `<div class="table-wrap"><table class="fv2-table"><thead><tr><th>${L('Time','زمان')}</th><th>${L('Representative','نماینده')}</th><th>${L('Volume Credit','اعتبار حجمی')}</th><th>${L('Unlimited Credit','اعتبار نامحدود')}</th><th>${L('Type','نوع')}</th><th>${L('Event ID','شناسه رویداد')}</th></tr></thead><tbody>${rows.map(r=>`<tr><td>${date(creditTime(r))}</td><td><b>${e(repName(r.owner))}</b><small class="fv2-muted">${e(r.owner)}</small></td><td class="mono ${Number(r.volume_bytes)<0?'fv2-negative':'fv2-positive'}">${signedBytes(r.volume_bytes)}</td><td class="mono ${Number(r.unlimited_units)<0?'fv2-negative':'fv2-positive'}">${signedUnits(r.unlimited_units)}</td><td><span class="fv2-kind credit">${e(r.kind||'adjust')}</span></td><td class="mono fv2-event">${e(r.event_id)}</td></tr>`).join('')}</tbody></table></div>`;
}
function positionTable(rows){
 if(!rows.length)return empty(L('No representative profiles match this filter.','پروفایل نماینده‌ای مطابق این فیلتر وجود ندارد.'));
 return `<div class="table-wrap"><table class="fv2-table"><thead><tr><th>${L('Representative','نماینده')}</th><th>${L('Volume total','حجم کل')}</th><th>${L('Volume allocated','حجم رزروشده')}</th><th>${L('Volume remaining','حجم باقی‌مانده')}</th><th>${L('Unlimited total','نامحدود کل')}</th><th>${L('Unlimited allocated','نامحدود رزروشده')}</th><th>${L('Unlimited remaining','نامحدود باقی‌مانده')}</th></tr></thead><tbody>${rows.map(r=>`<tr><td><b>${e(r.name||r.id)}</b><small class="fv2-muted">${e(r.id)}</small></td><td class="mono">${bytes(r.volume_credit_bytes||0)}</td><td class="mono">${bytes(r.allocated_volume_bytes||0)}</td><td class="mono"><b>${bytes(r.volume_credit_remaining_bytes||0)}</b></td><td class="mono">${fa(r.unlimited_credit||0)}</td><td class="mono">${fa(r.allocated_unlimited||0)}</td><td class="mono"><b>${fa(r.unlimited_credit_remaining||0)}</b></td></tr>`).join('')}</tbody></table></div>`;
}
function trafficTable(rows){
 rows=filterRepresentative(rows);
 if(!rows.length)return empty(L('No representative traffic ledger entries match this filter.','رکورد مصرف نماینده‌ای مطابق این فیلتر وجود ندارد.'));
 return `<div class="table-wrap"><table class="fv2-table"><thead><tr><th>${L('Observed','زمان مشاهده')}</th><th>${L('Representative','نماینده')}</th><th>${L('Client','کاربر')}</th><th>↑</th><th>↓</th><th>${L('Total','مجموع')}</th><th>${L('Period','دوره')}</th><th>Event ID</th></tr></thead><tbody>${rows.map(r=>`<tr><td>${date(trafficTime(r))}</td><td><b>${e(repName(r.owner))}</b><small class="fv2-muted">${e(r.owner)}</small></td><td class="mono">${e(r.client_id)}</td><td class="mono">${bytes(r.up_bytes)}</td><td class="mono">${bytes(r.down_bytes)}</td><td class="mono"><b>${bytes(Number(r.up_bytes||0)+Number(r.down_bytes||0))}</b></td><td class="mono">${fa(r.period)}</td><td class="mono fv2-event">${e(r.event_id)}</td></tr>`).join('')}</tbody></table></div>`;
}
function stat(label,value,sub=''){return `<div class="fv2-stat"><small>${e(label)}</small><b>${e(value)}</b>${sub?`<span>${e(sub)}</span>`:''}</div>`;}
financePage=async function(){
 const credits=await api('/api/ledger/credits');let traffic=[];try{traffic=await api('/api/ledger/traffic');}catch{}
 const profiles=selectedProfiles(),visibleTraffic=filterRepresentative(traffic),visibleCredits=filterRepresentative(credits);
 const statsAvailable=profiles.length>0;
 const currentUsage=statsAvailable?sum(profiles,o=>o.used_bytes??o.used??0):null;
 const lifetime=statsAvailable?sum(profiles,o=>o.lifetime_used_bytes??0):null;
 const volumeRemaining=statsAvailable?sum(profiles,o=>o.volume_credit_remaining_bytes??0):null;
 const unlimitedRemaining=statsAvailable?sum(profiles,o=>o.unlimited_credit_remaining??0):null;
 const repChips=ownerMode()?[chip(L('All representatives','همه نماینده‌ها'),'representative','all',FV.representative),...reps().map(r=>chip(r.name||r.id,'representative',r.id,FV.representative))].join(''):'';
 const missing=L('Representative profile statistics are not available','آمار پروفایل نماینده در دسترس نیست');
 const repControl=ownerMode()?`<div><small>${L('Representative','نماینده')}</small><div class="fv2-chips">${reps().length?repChips:`<span class="fv2-muted">${L('No representatives yet','هنوز نماینده‌ای ساخته نشده')}</span>`}</div></div>`:'';
 return heading(L('Representative Credits & Ledger','اعتبار و دفتر نمایندگان'),L('Sellable Volume/Unlimited credits stay separate from observed Xray traffic and Telegram Commerce pricing.','اعتبار قابل‌فروش حجمی/نامحدود از مصرف واقعی Xray و قیمت‌های Telegram Commerce جداست.'))+
 `<div class="fv2-shell"><div class="fv2-summary">
 ${stat(L('Volume credit remaining','اعتبار حجمی باقی‌مانده'),volumeRemaining===null?'—':bytes(volumeRemaining),volumeRemaining===null?missing:L('Assignable limited-service capacity','ظرفیت قابل تخصیص سرویس حجمی'))}
 ${stat(L('Unlimited credit remaining','اعتبار نامحدود باقی‌مانده'),unlimitedRemaining===null?'—':fa(unlimitedRemaining),unlimitedRemaining===null?missing:L('Assignable unlimited-service slots','تعداد سرویس نامحدود قابل تخصیص'))}
 ${stat(L('Current-period traffic','مصرف واقعی دوره'),currentUsage===null?'—':bytes(currentUsage),currentUsage===null?missing:L('Analytics only; does not spend credit','فقط آمار؛ از اعتبار کم نمی‌کند'))}
 ${stat(L('Lifetime representative traffic','مصرف تاریخی نماینده‌ها'),lifetime===null?'—':bytes(lifetime),lifetime===null?missing:L('Immutable observed traffic history','تاریخچه ماندگار مصرف مشاهده‌شده'))}
 </div><article class="panel fv2-controls">${repControl}</article>
 <div class="notice">${L('A limited client reserves its configured quota from Volume Credit. An unlimited client reserves one Unlimited Credit. Disabling a client does not release its reservation; deleting it or changing its plan does.','کاربر حجمی به اندازهٔ حجم پلن از اعتبار حجمی رزرو می‌کند و کاربر نامحدود یک واحد اعتبار نامحدود می‌گیرد. قطع‌کردن کاربر رزرو را آزاد نمی‌کند؛ حذف یا تغییر پلن آن را آزاد می‌کند.')}</div>
 <article class="panel"><div class="panel-head"><div><h2>${L('Current representative credit position','وضعیت فعلی اعتبار نمایندگان')}</h2><p>${L('Total, allocated and remaining sellable capacity.','ظرفیت کل، رزروشده و باقی‌مانده قابل‌فروش.')}</p></div><span class="tag">${fa(profiles.length)}</span></div>${positionTable(profiles)}</article>
 <article class="panel" style="margin-top:18px"><div class="panel-head"><div><h2>${L('Resource credit ledger','دفتر تغییرات اعتبار')}</h2><p>${L('Every manual credit adjustment has an idempotent Event ID.','هر تغییر دستی اعتبار یک شناسهٔ رویداد یکتای تکرارپذیر دارد.')}</p></div><span class="tag">${fa(visibleCredits.length)}</span></div>${creditTable(credits)}</article>
 <article class="panel" style="margin-top:18px"><div class="panel-head"><div><h2>${L('Observed traffic ledger','دفتر مصرف واقعی')}</h2><p>${L('Observed traffic is historical analytics and never automatically consumes sellable credit.','مصرف مشاهده‌شده فقط تاریخچه و آمار است و اعتبار قابل‌فروش را خودکار کم نمی‌کند.')}</p></div><span class="tag">${fa(visibleTraffic.length)}</span></div>${trafficTable(traffic)}</article></div>`;
};
runAction=async function(act,el){if(act==='fv2filter'){FV[el.dataset.key]=el.dataset.value;await renderPage();return;}return oldRunAction(act,el);};
})();