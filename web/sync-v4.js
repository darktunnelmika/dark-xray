/* DARK XRAY Sync / Runtime V4 — guided reconciliation state. */
(function(){
'use strict';
if(typeof syncPage!=='function'||typeof runAction!=='function')return;
const baseRunAction=runAction;
const L=(en,fa)=>((localStorage.getItem('dark_lang')||'en')==='fa'?fa:en);
state.sv4=state.sv4||{view:'issues'};

const reasonMap={
 clean:{tone:'ok',title:['IN SYNC','همسان'],desc:['DARK desired state and managed runtime record are reconciled.','وضعیت موردنظر DARK و رکورد Runtime همگام هستند.']},
 queued:{tone:'info',title:['QUEUED','در صف'],desc:['A durable operation is queued and will be reconciled automatically.','یک عملیات ماندگار در صف است و خودکار تطبیق داده می‌شود.']},
 retry_wait:{tone:'warn',title:['RETRY SCHEDULED','Retry زمان‌بندی‌شده'],desc:['The last apply failed. DARK will retry after the backoff window.','اعمال قبلی خطا داده و DARK بعد از Backoff دوباره تلاش می‌کند.']},
 operation_error:{tone:'warn',title:['RETRY DUE','آماده Retry'],desc:['The operation failed and its automatic retry window is due.','عملیات خطا داده و زمان Retry خودکار آن رسیده است.']},
 uncertain_reset:{tone:'error',title:['UNCERTAIN RESET','ریست نامشخص'],desc:['A destructive reset may have partially completed. DARK will never replay it automatically.','ممکن است ریست مخرب بخشی از کار را انجام داده باشد؛ DARK هرگز آن را خودکار تکرار نمی‌کند.']},
 uncertain_operation:{tone:'error',title:['UNCERTAIN','نتیجه نامشخص'],desc:['The outcome cannot be proven safely. Manual inspection is required.','نتیجه عملیات با اطمینان قابل اثبات نیست و بررسی دستی لازم است.']},
 identity_conflict:{tone:'error',title:['IDENTITY CONFLICT','تعارض هویت'],desc:['The runtime identity no longer matches DARK metadata. Automatic overwrite is refused.','هویت Runtime با متادیتای DARK همسان نیست و overwrite خودکار رد می‌شود.']},
 runtime_missing:{tone:'error',title:['MISSING IN RUNTIME','در Runtime نیست'],desc:['The managed client disappeared from Xray. Automatic recreation is intentionally refused.','کاربر مدیریت‌شده از Xray ناپدید شده و بازسازی خودکار عمداً انجام نمی‌شود.']},
 external_disabled:{tone:'warn',title:['EXTERNAL DISABLE','قطع خارجی'],desc:['Xray was disabled outside DARK. DARK preserves that drift until you explicitly restore control.','Xray خارج از DARK غیرفعال شده و تا تأیید صریح شما همان Drift حفظ می‌شود.']},
 reset_inflight:{tone:'warn',title:['RESET IN FLIGHT','ریست در حال اجرا'],desc:['A destructive reset is currently being finalized.','ریست مخرب در حال نهایی‌شدن است.']}
};
function rm(code){return reasonMap[code]||{tone:'warn',title:[String(code||'UNKNOWN').toUpperCase(),String(code||'نامشخص')],desc:['Unknown reconciliation state.','وضعیت همگام‌سازی ناشناخته است.']};}
function rt(code){
 const m={
  running_clean:['ok','RUNNING / CLEAN','در حال اجرا / همسان','Running generation matches the compiled DARK config.','نسخه در حال اجرا با Config کامپایل‌شده DARK همسان است.'],
  running_dirty:['warn','RUNNING / DIRTY','در حال اجرا / تغییر اعمال‌نشده','Saved configuration differs from the running Xray generation.','Config ذخیره‌شده با نسل در حال اجرای Xray فرق دارد.'],
  stopped_staged:['warn','STOPPED / STAGED','متوقف / آماده اعمال','Configuration exists but no Xray generation is active.','Config آماده است ولی نسل فعالی از Xray اجرا نیست.'],
  stopped_unexpected:['error','STOPPED / RECOVERY','توقف غیرمنتظره','DARK expects Xray to be running but the process is down.','DARK انتظار دارد Xray روشن باشد ولی Process متوقف است.'],
  runtime_error:['error','RUNTIME ERROR','خطای Runtime','The last Xray apply/start recorded an error.','آخرین Apply/Start هسته با خطا ثبت شده است.'],
  stopped_clean:['info','STOPPED','متوقف','Xray is intentionally stopped with no pending generation change.','Xray عمداً متوقف است و تغییر نسل در انتظار ندارد.']
 };
 return m[code]||['warn',String(code||'UNKNOWN').toUpperCase(),String(code||'نامشخص'),'',''];
}
function metric(label,value,sub=''){return `<div class="sy4-metric"><span>${e(label)}</span><b>${e(value)}</b>${sub?`<small>${e(sub)}</small>`:''}</div>`;}
function stateChip(code){const m=rm(code);return `<span class="sy4-chip ${m.tone}">${e(L(m.title[0],m.title[1]))}</span>`;}
function opLabel(op){return ({none:L('None','هیچ'),upsert:L('Upsert','اعمال/ویرایش'),reset:L('Reset traffic','ریست ترافیک'),delete:L('Delete','حذف')})[op]||String(op||'—');}
function itemAction(x){
 const id=e(x.email),act=x.next_action;
 if(act==='retry_now')return button(L('Retry now','Retry همین حالا'),'sy4retry','refresh',`data-id="${id}"`,true);
 if(act==='resolve_reset')return button(L('Resolve reset','حل وضعیت ریست'),'sy4resolve','check',`data-id="${id}"`,true);
 if(act==='restore_missing')return button(L('Restore to runtime','بازسازی در Runtime'),'sy4restore','refresh',`data-id="${id}"`,true);
 if(act==='restore_control')return button(L('Restore DARK control','بازگرداندن کنترل DARK'),'sy4control','power',`data-id="${id}"`,true);
 return button(L('Inspect','بررسی'),'sy4inspect','eye',`data-id="${id}"`);
}
function itemCard(x){
 const m=rm(x.reason_code),retry=x.retry_in_seconds>0?`${fa(x.retry_in_seconds)}s`:'—';
 return `<article class="panel sy4-item ${m.tone}">
  <header><div><h3>${e(x.email)}</h3><small>${e(x.owner||'—')} · ${e(opLabel(x.op))}</small></div>${stateChip(x.reason_code)}</header>
  <p>${e(L(m.desc[0],m.desc[1]))}</p>
  <div class="sy4-item-grid">
   <div><span>${L('STATE','وضعیت')}</span><b>${e(x.state||'—')}</b></div>
   <div><span>${L('ATTEMPTS','تلاش‌ها')}</span><b>${fa(x.attempts||0)}</b></div>
   <div><span>${L('RETRY IN','Retry بعدی')}</span><b>${e(retry)}</b></div>
   <div><span>${L('UPDATED','آخرین تغییر')}</span><b>${date(x.updated_at)}</b></div>
  </div>
  ${x.error?`<div class="sy4-error">${e(x.error)}</div>`:''}
  <footer>${itemAction(x)}</footer>
 </article>`;
}
function filtered(items){
 const v=state.sv4.view;
 if(v==='all')return items;
 if(v==='automatic')return items.filter(x=>x.automatic_retry);
 if(v==='drift')return items.filter(x=>x.drift);
 if(v==='action')return items.filter(x=>x.severity==='error');
 return items.filter(x=>x.reason_code!=='clean');
}
function filterBar(s){
 const defs=[['issues',L('Issues','مشکلات')],['action',L('Action required','نیازمند اقدام')],['automatic',L('Automatic retry','Retry خودکار')],['drift',L('Drift','اختلاف خارجی')],['all',L('All clients','همه کاربران')]];
 return `<div class="sy4-filters">${defs.map(([v,l])=>`<button type="button" data-act="sy4filter" data-view="${v}" class="${state.sv4.view===v?'active':''}">${e(l)}</button>`).join('')}<span>${fa(s.items_total||0)} ${L('managed','مدیریت‌شده')}</span></div>`;
}
function managerCard(s){
 const st=s.manager_state||'stale',tone=st==='healthy'?'ok':st==='error'?'error':'warn';
 return `<article class="panel sy4-control-card ${tone}"><small>01 / RECONCILER</small><h3>${L('Manager loop','چرخه Manager')}</h3><b>${e(String(st).toUpperCase())}</b>
  <div class="sy4-kv"><div><span>${L('Last poll','آخرین Poll')}</span><strong>${date(s.last_poll)}</strong></div><div><span>${L('Poll age','سن Poll')}</span><strong>${s.poll_age_seconds==null?'—':fa(s.poll_age_seconds)+'s'}</strong></div><div><span>${L('Writes','نوشتن')}</span><strong>${s.writes_enabled?L('Enabled','فعال'):L('Read only','فقط خواندنی')}</strong></div></div>
  ${s.error?`<div class="sy4-error">${e(s.error)}</div>`:''}</article>`;
}
function runtimeCode(r){
 if(r.generation_state)return r.generation_state;
 if(r.running&&!r.dirty)return'running_clean';
 if(r.running&&r.dirty)return'running_dirty';
 if(!r.running&&r.desired_running)return'stopped_unexpected';
 if(r.dirty)return'stopped_staged';
 return'stopped_clean';
}
function runtimeCard(s){
 const r=s.runtime||{},m=rt(runtimeCode(r)),action=r.next_action||'none';
 let actions='';
 if(isOwner()&&action==='restart_apply')actions=button(L('Restart / Apply','ری‌استارت / اعمال'),'sy4core','refresh','data-core="restart"',true);
 if(isOwner()&&action==='start_apply')actions=button(L('Start / Apply','شروع / اعمال'),'sy4core','power','data-core="start"',true);
 return `<article class="panel sy4-control-card ${m[0]}"><small>02 / LOCAL XRAY</small><h3>${L('Runtime generation','نسل Runtime')}</h3><b>${e(L(m[1],m[2]))}</b><p>${e(L(m[3],m[4]))}</p>
  <div class="sy4-kv"><div><span>${L('Process','Process')}</span><strong>${r.running?L('Running','در حال اجرا'):L('Stopped','متوقف')}</strong></div><div><span>Dirty</span><strong>${r.dirty?L('Yes','بله'):L('No','خیر')}</strong></div><div><span>${L('Desired','وضعیت مطلوب')}</span><strong>${r.desired_running?L('Running','روشن'):L('Stopped','خاموش')}</strong></div></div>
  ${r.last_error?`<div class="sy4-error">${e(r.last_error)}</div>`:''}${actions?`<footer>${actions}</footer>`:''}</article>`;
}
function queueCard(s){
 const q=s.summary||{};
 const issues=(q.action_required||0)+(q.warnings||0);
 return `<article class="panel sy4-control-card ${q.action_required?'error':issues?'warn':'ok'}"><small>03 / CLIENT QUEUE</small><h3>${L('Reconciliation queue','صف تطبیق کاربران')}</h3><b>${q.action_required?fa(q.action_required)+' '+L('ACTION','اقدام'):issues?fa(issues)+' '+L('ISSUES','مورد'):L('CLEAN','سالم')}</b>
 <div class="sy4-mini-grid">${metric(L('Clean','همسان'),fa(q.clean||0))}${metric(L('Queued','در صف'),fa(q.queued||0))}${metric(L('Retry','Retry'),fa((q.retry_wait||0)+(q.operation_error||0)))}${metric(L('Uncertain','نامشخص'),fa((q.uncertain_reset||0)+(q.uncertain_operation||0)))}${metric(L('Conflict','تعارض'),fa(q.identity_conflict||0))}${metric(L('Missing','گم‌شده'),fa(q.runtime_missing||0))}</div></article>`;
}
function nodeCard(s){
 const n=s.nodes;if(!n)return'';
 const issues=(n.offline||0)+(n.assignment_errors||0)+(n.pending_deploys||0);
 return `<article class="panel sy4-control-card ${issues?'warn':'ok'}"><small>04 / NODES</small><h3>${L('Node synchronization','همگام‌سازی نودها')}</h3><b>${issues?fa(issues)+' '+L('ISSUES','مورد'):L('READY','آماده')}</b>
  <div class="sy4-kv"><div><span>${L('Online','آنلاین')}</span><strong>${fa(n.online||0)} / ${fa(n.total||0)}</strong></div><div><span>${L('Deploy errors','خطای Deploy')}</span><strong>${fa(n.assignment_errors||0)}</strong></div><div><span>${L('Pending deploy','Deploy در انتظار')}</span><strong>${fa(n.pending_deploys||0)}</strong></div></div>
  <footer>${button(L('Open Nodes','بازکردن Nodes'),'sy4nodes','server')}</footer></article>`;
}
syncPage=function(){
 const s=state.sync||{},items=filtered(s.items||[]),q=s.summary||{};
 const issueCount=(q.action_required||0)+(q.warnings||0);
 return heading(L('Sync & Runtime','همگام‌سازی و Runtime'),L('One view for Manager reconciliation, Xray generation, client drift and node deployment state.','یک نمای واحد برای تطبیق Manager، نسل Xray، Drift کاربران و وضعیت Deploy نودها.'),
   isOwner()?button(L('Reconcile now','تطبیق همین حالا'),'sy4force','refresh','',true):'')+
 `<div class="sy4">
   <section class="sy4-control-grid">${managerCard(s)}${runtimeCard(s)}${queueCard(s)}${nodeCard(s)}</section>
   <section class="sy4-summary">
    ${metric(L('Action required','نیازمند اقدام'),fa(q.action_required||0))}
    ${metric(L('Warnings','هشدار'),fa(q.warnings||0))}
    ${metric(L('Automatic retry','Retry خودکار'),fa(q.automatic_retry||0))}
    ${metric(L('External drift','Drift خارجی'),fa(q.external_disabled||0))}
    ${metric(L('Identity conflict','تعارض هویت'),fa(q.identity_conflict||0))}
    ${metric(L('Runtime missing','گم‌شده در Runtime'),fa(q.runtime_missing||0))}
   </section>
   ${filterBar(s)}
   ${s.truncated?`<div class="notice warning">${L('Only the highest-priority 500 reconciliation records are shown; summary counts cover all visible managed clients.','فقط ۵۰۰ رکورد با اولویت بالاتر نمایش داده می‌شود؛ Summary همه کاربران قابل‌مشاهده را پوشش می‌دهد.')}</div>`:''}
   <section class="sy4-items">${items.length?items.map(itemCard).join(''):empty(issueCount?L('No records match this filter.','رکوردی با این فیلتر نیست.'):L('All managed clients are reconciled.','همه کاربران مدیریت‌شده همسان هستند.'))}</section>
   <div class="notice">${L('DARK never auto-replays an uncertain destructive reset, never overwrites an identity conflict, and never recreates a missing runtime client without an explicit owner recovery action.','DARK ریست مخرب نامشخص را خودکار تکرار نمی‌کند، تعارض هویت را overwrite نمی‌کند و کاربر گم‌شده Runtime را بدون Recovery صریح مالک بازسازی نمی‌کند.')}</div>
 </div>`;
};

function identityConfirm(id,kind){
 const restore=kind==='restore';
 const title=restore?L('Restore missing client','بازسازی کاربر گم‌شده'):L('Resolve uncertain reset','حل ریست نامشخص');
 const notice=restore
  ?L('This explicitly recreates the DARK-managed identity in Xray using the existing managed credentials. Type the client identity to continue.','این کار هویت مدیریت‌شده DARK را با Credential فعلی دوباره در Xray می‌سازد. برای ادامه شناسه کاربر را تایپ کن.')
  :L('This accepts the current counters and does NOT replay the destructive reset. Type the client identity to continue.','این کار شمارنده فعلی را می‌پذیرد و ریست مخرب را دوباره اجرا نمی‌کند. برای ادامه شناسه کاربر را تایپ کن.');
 dialog(title+` · ${id}`,`<div class="notice warning">${e(notice)}</div>${field(L('Client identity confirmation','تأیید شناسه کاربر'),'confirmation','','text',`required autocomplete="off" data-expected="${e(id)}"`) }`,async f=>{
   const value=String(f.get('confirmation')||'').trim();if(value!==id)throw Error(L('Client identity does not match.','شناسه کاربر مطابقت ندارد.'));
   const endpoint=restore?'/api/clients/'+enc(id)+'/restore-missing':'/api/clients/'+enc(id)+'/resolve-reset';
   await api(endpoint,'POST',{confirmation:value});await refresh();closeDialog();toast(restore?L('Missing client recovery completed.','Recovery کاربر گم‌شده انجام شد.'):L('Uncertain reset resolved without replay.','ریست نامشخص بدون تکرار حل شد.'));
 });
}
function inspectSync(id){
 const x=(state.sync?.items||[]).find(r=>r.email===id);if(!x)return;
 const m=rm(x.reason_code);
 dialog(L('Sync inspection','بررسی Sync')+` · ${id}`,`<div class="sy4-inspect"><div class="notice ${m.tone==='error'?'error':m.tone==='warn'?'warning':''}"><b>${e(L(m.title[0],m.title[1]))}</b><br>${e(L(m.desc[0],m.desc[1]))}</div>
  <div class="sy4-inspect-grid"><div><span>${L('Operation','عملیات')}</span><b>${e(opLabel(x.op))}</b></div><div><span>${L('State','وضعیت')}</span><b>${e(x.state)}</b></div><div><span>${L('Attempts','تلاش‌ها')}</span><b>${fa(x.attempts||0)}</b></div><div><span>${L('Automatic retry','Retry خودکار')}</span><b>${x.automatic_retry?L('Yes','بله'):L('No','خیر')}</b></div><div><span>${L('External drift','Drift خارجی')}</span><b>${x.drift?L('Yes','بله'):L('No','خیر')}</b></div><div><span>${L('Updated','آخرین تغییر')}</span><b>${date(x.updated_at)}</b></div></div>
  ${x.error?`<pre class="sy4-inspect-error">${e(x.error)}</pre>`:''}</div>`);
}

runAction=async function(act,el){
 const id=el?.dataset?.id;
 if(act==='sy4filter'){state.sv4.view=el.dataset.view;await renderPage();return;}
 if(act==='sy4force'){state.sync=await api('/api/sync','POST',{});toast(L('Reconciliation pass completed.','دور تطبیق انجام شد.'));await renderPage();return;}
 if(act==='sy4retry'){await api('/api/clients/'+enc(id)+'/sync-retry','POST',{});toast(L('Retry requested.','Retry درخواست شد.'));await refresh();return;}
 if(act==='sy4resolve'){identityConfirm(id,'resolve');return;}
 if(act==='sy4restore'){identityConfirm(id,'restore');return;}
 if(act==='sy4control'){await api('/api/clients/'+enc(id)+'/action','POST',{action:'enable'});toast(L('DARK control restored.','کنترل DARK بازگردانده شد.'));await refresh();return;}
 if(act==='sy4inspect'){inspectSync(id);return;}
 if(act==='sy4nodes'){await go('nodes');return;}
 if(act==='sy4core'){
  const core=el.dataset.core;if(core==='restart'&&!confirm(L('Restart Xray and apply the saved generation? Active sessions may disconnect.','Xray ری‌استارت شود و نسل ذخیره‌شده اعمال شود؟ اتصال‌های فعال ممکن است قطع شوند.')))return;
  await api('/api/core/'+core,'POST',{});toast(core==='restart'?L('Xray restarted and applied.','Xray ری‌استارت و اعمال شد.'):L('Xray started and applied.','Xray اجرا و اعمال شد.'));await refresh();return;
 }
 return baseRunAction(act,el);
};
globalThis.DarkSyncV4={open:async()=>go('sync')};
})();