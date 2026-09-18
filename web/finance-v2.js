/* DARK XRAY Finance V3 — representative-centric immutable ledgers. */
(function(){
'use strict';
if(typeof financePage!=='function'||typeof runAction!=='function')return;
const oldRunAction=runAction;
const FV={representative:'all',kind:'all'};
const L=(en,fa)=>((localStorage.getItem('dark_lang')||'en')==='fa'?fa:en);
const moneyTime=r=>Number(r?.at||0);
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
function moneyRows(rows){let out=filterRepresentative(rows);if(FV.kind!=='all')out=out.filter(r=>r.kind===FV.kind);return out;}
function kindBadge(kind){const label={credit:L('Credit','شارژ'),sale:L('Sale','فروش'),refund:L('Refund','بازپرداخت')}[kind]||kind;return `<span class="fv2-kind ${e(kind||'other')}">${e(label)}</span>`;}
function moneyTable(rows){
 rows=moneyRows(rows);
 if(!rows.length)return empty(L('No representative financial entries match these filters.','تراکنش مالی نماینده‌ای مطابق این فیلتر وجود ندارد.'));
 return `<div class="table-wrap"><table class="fv2-table"><thead><tr><th>${L('Time','زمان')}</th><th>${L('Representative','نماینده')}</th><th>${L('Type','نوع')}</th><th>${L('Amount','مبلغ')}</th><th>${L('Reference','مرجع')}</th><th>Event ID</th></tr></thead><tbody>${rows.map(r=>`<tr><td>${date(moneyTime(r))}</td><td><b>${e(repName(r.owner))}</b><small class="fv2-muted">${e(r.owner)}</small></td><td>${kindBadge(r.kind)}</td><td class="mono ${Number(r.amount)<0?'fv2-negative':'fv2-positive'}">${Number(r.amount)>0?'+':''}${fa(r.amount)}</td><td class="mono">${e(r.reference||'—')}</td><td class="mono fv2-event">${e(r.event_id)}</td></tr>`).join('')}</tbody></table></div>`;
}
function trafficTable(rows){
 rows=filterRepresentative(rows);
 if(!rows.length)return empty(L('No representative traffic ledger entries match this filter.','رکورد مصرف نماینده‌ای مطابق این فیلتر وجود ندارد.'));
 return `<div class="table-wrap"><table class="fv2-table"><thead><tr><th>${L('Observed','زمان مشاهده')}</th><th>${L('Representative','نماینده')}</th><th>${L('Client','کاربر')}</th><th>↑</th><th>↓</th><th>${L('Total','مجموع')}</th><th>${L('Period','دوره')}</th><th>Event ID</th></tr></thead><tbody>${rows.map(r=>`<tr><td>${date(trafficTime(r))}</td><td><b>${e(repName(r.owner))}</b><small class="fv2-muted">${e(r.owner)}</small></td><td class="mono">${e(r.client_id)}</td><td class="mono">${bytes(r.up_bytes)}</td><td class="mono">${bytes(r.down_bytes)}</td><td class="mono"><b>${bytes(Number(r.up_bytes||0)+Number(r.down_bytes||0))}</b></td><td class="mono">${fa(r.period)}</td><td class="mono fv2-event">${e(r.event_id)}</td></tr>`).join('')}</tbody></table></div>`;
}
function stat(label,value,sub=''){return `<div class="fv2-stat"><small>${e(label)}</small><b>${e(value)}</b>${sub?`<span>${e(sub)}</span>`:''}</div>`;}
financePage=async function(){
 const money=await api('/api/ledger/money');let traffic=[];try{traffic=await api('/api/ledger/traffic');}catch{}
 const profiles=selectedProfiles(),visibleTraffic=filterRepresentative(traffic),filteredMoney=moneyRows(money);
 const statsAvailable=profiles.length>0;
 const currentUsage=statsAvailable?sum(profiles,o=>o.used_bytes??o.used??0):null;
 const lifetime=statsAvailable?sum(profiles,o=>o.lifetime_used_bytes??0):null;
 const balance=statsAvailable?sum(profiles,o=>o.credit):null;
 const visibleNet=sum(filteredMoney,r=>r.amount);
 const repChips=ownerMode()?[chip(L('All representatives','همه نماینده‌ها'),'representative','all',FV.representative),...reps().map(r=>chip(r.name||r.id,'representative',r.id,FV.representative))].join(''):'';
 const kindChips=[chip(L('All','همه'),'kind','all',FV.kind),chip(L('Credit','شارژ'),'kind','credit',FV.kind),chip(L('Sale','فروش'),'kind','sale',FV.kind),chip(L('Refund','بازپرداخت'),'kind','refund',FV.kind)].join('');
 const missing=L('Representative profile statistics are not available','آمار پروفایل نماینده در دسترس نیست');
 const repControl=ownerMode()?`<div><small>${L('Representative','نماینده')}</small><div class="fv2-chips">${reps().length?repChips:`<span class="fv2-muted">${L('No representatives yet','هنوز نماینده‌ای ساخته نشده')}</span>`}</div></div>`:'';
 return heading(L('Finance & Ledger','دفتر حساب'),L('Representative financial and traffic ledgers; the single primary owner is not a ledger scope.','دفتر مالی و مصرف نماینده‌ها؛ مالک اصلی واحد به‌عنوان محدوده دفتر نمایش داده نمی‌شود.'))+
 `<div class="fv2-shell"><div class="fv2-summary">
 ${stat(L('Representative credit','اعتبار نماینده‌ها'),balance===null?'—':fa(balance),balance===null?missing:L('Current representative balances','موجودی فعلی نماینده‌ها'))}
 ${stat(L('Filtered ledger net','خالص دفتر فیلترشده'),fa(visibleNet),`${fa(filteredMoney.length)} ${L('matching events','رویداد مطابق فیلتر')}`)}
 ${stat(L('Current-period traffic','مصرف دوره جاری'),currentUsage===null?'—':bytes(currentUsage),currentUsage===null?missing:L('Representative quota meter','متر سهمیه نماینده‌ها'))}
 ${stat(L('Lifetime representative traffic','مصرف تاریخی نماینده‌ها'),lifetime===null?'—':bytes(lifetime),lifetime===null?missing:L('Authoritative representative lifetime total','مجموع تاریخی واقعی نماینده‌ها'))}
 </div><article class="panel fv2-controls">${repControl}<div><small>${L('Money event type','نوع رویداد مالی')}</small><div class="fv2-chips">${kindChips}</div></div></article>
 <div class="notice">${L('The primary owner is singular and is not a selectable ledger scope. Representative credit, current traffic periods and immutable history remain separate.','مالک اصلی فقط یک حساب است و در دفتر به‌عنوان محدوده قابل انتخاب نمایش داده نمی‌شود. اعتبار نماینده، دوره مصرف جاری و تاریخچه ماندگار مستقل از هم هستند.')}</div>
 <article class="panel"><div class="panel-head"><div><h2>${L('Representative money ledger','دفتر مالی نمایندگان')}</h2><p>${L('Event IDs are idempotency keys; references connect sales/refunds when available.','Event ID کلید idempotency است؛ Reference ارتباط فروش و بازپرداخت را نشان می‌دهد.')}</p></div><span class="tag">${fa(filteredMoney.length)}</span></div>${moneyTable(money)}</article>
 <article class="panel" style="margin-top:18px"><div class="panel-head"><div><h2>${L('Representative traffic ledger','دفتر مصرف نمایندگان')}</h2><p>${L('Observed timestamps and traffic deltas come from the immutable server ledger.','زمان مشاهده و مصرف از دفتر ماندگار سرور خوانده می‌شود.')}</p></div><span class="tag">${fa(visibleTraffic.length)}</span></div>${trafficTable(traffic)}</article></div>`;
};
runAction=async function(act,el){if(act==='fv2filter'){FV[el.dataset.key]=el.dataset.value;await renderPage();return;}return oldRunAction(act,el);};
})();