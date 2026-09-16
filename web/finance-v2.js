/* DARK XRAY Finance V2 — trustworthy owner-scoped ledger workspace. */
(function(){
'use strict';
if(typeof financePage!=='function'||typeof runAction!=='function')return;
const oldRunAction=runAction;
const FV={owner:'all',kind:'all'};
const L=(en,fa)=>((localStorage.getItem('dark_lang')||'en')==='fa'?fa:en);
const moneyTime=r=>Number(r?.at||0);
const trafficTime=r=>Number(r?.observed_at||0);
const ownerName=id=>state.owners?.find(o=>o.id===id)?.name||id;
const sum=(rows,fn)=>rows.reduce((a,r)=>a+Number(fn(r)||0),0);
function chip(label,key,value,current){return `<button type="button" class="fv2-chip ${String(current)===String(value)?'active':''}" data-act="fv2filter" data-key="${e(key)}" data-value="${e(value)}">${e(label)}</button>`;}
function filterOwner(rows){return FV.owner==='all'?rows:rows.filter(r=>r.owner===FV.owner);}
function selectedOwners(){const rows=state.owners||[];return FV.owner==='all'?rows:rows.filter(o=>o.id===FV.owner);}
function moneyRows(rows){let out=filterOwner(rows);if(FV.kind!=='all')out=out.filter(r=>r.kind===FV.kind);return out;}
function kindBadge(kind){const label={credit:L('Credit','شارژ'),sale:L('Sale','فروش'),refund:L('Refund','بازپرداخت')}[kind]||kind;return `<span class="fv2-kind ${e(kind||'other')}">${e(label)}</span>`;}
function moneyTable(rows){rows=moneyRows(rows);if(!rows.length)return empty(L('No financial entries match these filters.','تراکنش مالی مطابق این فیلتر وجود ندارد.'));return `<div class="table-wrap"><table class="fv2-table"><thead><tr><th>${L('Time','زمان')}</th><th>${L('Owner','مالک')}</th><th>${L('Type','نوع')}</th><th>${L('Amount','مبلغ')}</th><th>${L('Reference','مرجع')}</th><th>Event ID</th></tr></thead><tbody>${rows.map(r=>`<tr><td>${date(moneyTime(r))}</td><td><b>${e(ownerName(r.owner))}</b><small class="fv2-muted">${e(r.owner)}</small></td><td>${kindBadge(r.kind)}</td><td class="mono ${Number(r.amount)<0?'fv2-negative':'fv2-positive'}">${Number(r.amount)>0?'+':''}${fa(r.amount)}</td><td class="mono">${e(r.reference||'—')}</td><td class="mono fv2-event">${e(r.event_id)}</td></tr>`).join('')}</tbody></table></div>`;}
function trafficTable(rows){rows=filterOwner(rows);if(!rows.length)return empty(L('No traffic ledger entries match this owner.','رکورد مصرفی برای این مالک وجود ندارد.'));return `<div class="table-wrap"><table class="fv2-table"><thead><tr><th>${L('Observed','زمان مشاهده')}</th><th>${L('Owner','مالک')}</th><th>${L('Client','کاربر')}</th><th>↑</th><th>↓</th><th>${L('Total','مجموع')}</th><th>${L('Period','دوره')}</th><th>Event ID</th></tr></thead><tbody>${rows.map(r=>`<tr><td>${date(trafficTime(r))}</td><td><b>${e(ownerName(r.owner))}</b><small class="fv2-muted">${e(r.owner)}</small></td><td class="mono">${e(r.client_id)}</td><td class="mono">${bytes(r.up_bytes)}</td><td class="mono">${bytes(r.down_bytes)}</td><td class="mono"><b>${bytes(Number(r.up_bytes||0)+Number(r.down_bytes||0))}</b></td><td class="mono">${fa(r.period)}</td><td class="mono fv2-event">${e(r.event_id)}</td></tr>`).join('')}</tbody></table></div>`;}
function stat(label,value,sub=''){return `<div class="fv2-stat"><small>${e(label)}</small><b>${e(value)}</b>${sub?`<span>${e(sub)}</span>`:''}</div>`;}
financePage=async function(){
 const money=await api('/api/ledger/money');let traffic=[];try{traffic=await api('/api/ledger/traffic');}catch{}
 const owners=state.owners||[],scopeOwners=selectedOwners(),visibleTraffic=filterOwner(traffic),filteredMoney=moneyRows(money);
 const ownerStatsAvailable=FV.owner==='all'?owners.length>0:scopeOwners.length>0;
 const currentUsage=ownerStatsAvailable?sum(scopeOwners,o=>o.used_bytes??o.used??0):null;
 const lifetime=ownerStatsAvailable?sum(scopeOwners,o=>o.lifetime_used_bytes??0):null;
 const balance=ownerStatsAvailable?sum(scopeOwners,o=>o.credit):null;
 const visibleNet=sum(filteredMoney,r=>r.amount);
 // finance.read can intentionally exist without owners.read. Keep ledger filtering
 // usable by deriving anonymous owner IDs from visible ledger rows, but never
 // invent profile stats that were not authorized/loaded.
 const ids=[...new Set([...owners.map(o=>o.id),...money.map(r=>r.owner),...traffic.map(r=>r.owner)].filter(Boolean))];
 const ownerChips=[chip(L('All owners','همه مالک‌ها'),'owner','all',FV.owner),...ids.map(id=>chip(ownerName(id),'owner',id,FV.owner))].join('');
 const kindChips=[chip(L('All','همه'),'kind','all',FV.kind),chip(L('Credit','شارژ'),'kind','credit',FV.kind),chip(L('Sale','فروش'),'kind','sale',FV.kind),chip(L('Refund','بازپرداخت'),'kind','refund',FV.kind)].join('');
 const missing=L('Owner profile stats unavailable for this permission scope','آمار پروفایل مالک در این سطح دسترسی در دسترس نیست');
 return heading(L('Finance & Ledger','دفتر حساب'),L('Immutable financial and traffic events, separated from customer quota resets.','رویدادهای مالی و مصرفی ماندگار؛ مستقل از ریست حجم کاربران.'))+`<div class="fv2-shell"><div class="fv2-summary">${stat(L('Visible credit balance','اعتبار قابل مشاهده'),balance===null?'—':fa(balance),balance===null?missing:L('Current owner balances','موجودی فعلی مالک‌ها'))}${stat(L('Filtered ledger net','خالص دفتر فیلترشده'),fa(visibleNet),`${fa(filteredMoney.length)} ${L('matching ledger events','رویداد مطابق فیلتر')}`)}${stat(L('Current-period traffic','مصرف دوره جاری'),currentUsage===null?'—':bytes(currentUsage),currentUsage===null?missing:L('Authoritative owner quota meter','متر واقعی سهمیه نماینده'))}${stat(L('Lifetime observed traffic','مصرف تاریخی مشاهده‌شده'),lifetime===null?'—':bytes(lifetime),lifetime===null?missing:L('Authoritative owner lifetime total','مجموع تاریخی واقعی نماینده'))}</div><article class="panel fv2-controls"><div><small>${L('Owner scope','محدوده مالک')}</small><div class="fv2-chips">${ownerChips}</div></div><div><small>${L('Money event type','نوع رویداد مالی')}</small><div class="fv2-chips">${kindChips}</div></div></article><div class="notice">${L('Credit balance and traffic quota are separate. Period reset changes only the current owner traffic period; historical ledger rows stay intact. Tables show the latest server ledger window, while profile totals appear only when owner stats are authorized.','اعتبار مالی و سهمیه ترافیک مستقل‌اند. شروع دوره جدید فقط مصرف دوره جاری نماینده را صفر می‌کند و رکوردهای تاریخی حذف نمی‌شوند. جدول‌ها پنجره آخر دفتر سرور را نشان می‌دهند و مجموع‌های پروفایل فقط وقتی نمایش داده می‌شوند که دسترسی آمار مالک وجود داشته باشد.')}</div><article class="panel"><div class="panel-head"><div><h2>${L('Money ledger','دفتر مالی')}</h2><p>${L('Event IDs are idempotency keys; references link sales/refunds where available.','Event ID کلید idempotency است؛ Reference ارتباط فروش/بازپرداخت را نشان می‌دهد.')}</p></div><span class="tag">${fa(filteredMoney.length)}</span></div>${moneyTable(money)}</article><article class="panel" style="margin-top:18px"><div class="panel-head"><div><h2>${L('Traffic ledger','دفتر مصرف')}</h2><p>${L('Timestamp is the real observed_at value from the immutable traffic ledger.','زمان از observed_at واقعی دفتر مصرف خوانده می‌شود.')}</p></div><span class="tag">${fa(visibleTraffic.length)}</span></div>${trafficTable(traffic)}</article></div>`;
};
runAction=async function(act,el){if(act==='fv2filter'){FV[el.dataset.key]=el.dataset.value;await renderPage();return;}return oldRunAction(act,el);};
})();
