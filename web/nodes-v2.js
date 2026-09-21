/* DARK XRAY Nodes V4 — deployment and subscription orchestration. */
(function(){
'use strict';
if(typeof enginePage!=='function'||typeof runAction!=='function')return;
const baseEnginePage=enginePage,baseRunAction=runAction;
const controlBusy=new Set();
const L=(en,fa)=>((localStorage.getItem('dark_lang')||'en')==='fa'?fa:en);
state.nv2=state.nv2||{nodes:[],orchestration:null,nodeError:'',orchestrationError:''};

async function loadNodes(){
 let nodes=[],orchestration=null,nodeError='',orchestrationError='';
 try{nodes=await api('/api/nodes');}catch(ex){nodeError=ex.message||String(ex);}
 try{orchestration=await api('/api/nodes/orchestration');}catch(ex){orchestrationError=ex.message||String(ex);}
 state.nv2={nodes,orchestration,nodeError,orchestrationError};return state.nv2;
}
function healthMetric(label,value){return `<div><small>${e(label)}</small><b>${e(value??'—')}</b></div>`;}
function assignedNames(n){return (n.inboundIds||[]).map(id=>{let ib=state.inbounds.find(x=>x.id===id);return ib?(ib.remark||ib.tag):'#'+id;});}
const reasonLabels={
 control_pending:['COMMAND PENDING','فرمان در انتظار'],control_stopped:['STOP REQUESTED','توقف درخواست شده'],
 ready:['IN SUBSCRIPTION','داخل اشتراک'],not_deployed:['NOT DEPLOYED','مستقر نشده'],sync_error:['SYNC ERROR','خطای همگام‌سازی'],
 node_disabled:['NODE DISABLED','نود غیرفعال'],failover_disabled:['FAILOVER OFF','فیل‌اور خاموش'],
 data_address_missing:['NO DATA ADDRESS','آدرس داده ندارد'],node_offline:['NODE OFFLINE','نود آفلاین'],no_assignments:['NO ASSIGNMENTS','بدون تخصیص']
};
function reasonLabel(code){const pair=reasonLabels[code]||[String(code||'UNKNOWN').toUpperCase(),String(code||'نامشخص')];return L(pair[0],pair[1]);}
function reasonClass(code){return code==='ready'?'ready':code==='sync_error'?'error':['not_deployed','node_offline'].includes(code)?'warn':'off';}
function assignmentChip(a){
 const ib=state.inbounds.find(x=>x.id===Number(a.local_inbound_id)),name=ib?(ib.remark||ib.tag):'#'+a.local_inbound_id;
 return `<span class="nv4-assignment ${reasonClass(a.failover_reason)}"><b>${e(name)}</b><small>${e(a.deployment_state||'pending')} · ${e(reasonLabel(a.failover_reason))}</small></span>`;
}
function controlBanner(n){
 const c=n.control||{};if(!c.persisted)return '';
 const action=String(c.action||'').toUpperCase(),phase=c.pending?L('COMMAND PENDING','فرمان در انتظار'):L('LAST COMMAND ACKNOWLEDGED','آخرین فرمان تأیید شده');
 const hint=c.pending?L('Saved on the Hub; not confirmed as executed. Disabled nodes wait until enabled.','در Hub ذخیره شده؛ اجرای آن هنوز تأیید نشده است. نود غیرفعال تا فعال‌سازی مجدد منتظر می‌ماند.'):L('This is a command receipt, not a live health check.','این وضعیت، رسید فرمان است؛ نه بررسی زندهٔ سلامت.');
 return `<div class="notice ${c.pending?'warning':''}" data-node-control="${c.pending?'pending':'acknowledged'}"><b>${e(phase)} · ${e(action)} · r${e(c.revision)}</b><p>${e(hint)}</p>${c.last_error?`<div class="nv2-error">${e(c.last_error)}</div>`:''}</div>`;
}
function controlMessage(r,action){
 if(r.queued||r.control?.pending){
  if(r.delivery_state==='unsupported_agent')return L('Command saved, not executed: update the Node Agent to support ordered control.','فرمان ذخیره شد، اجرا نشد: برای کنترل نسخه‌دار، Agent نود را آپدیت کن.');
  if(r.delivery_state==='identity_mismatch')return L('Command saved, not executed: Node identity does not match.','فرمان ذخیره شد، اجرا نشد: شناسهٔ نود مطابقت ندارد.');
  if(r.delivery_state==='disabled')return L('Command saved; delivery is paused until the Node is enabled.','فرمان ذخیره شد؛ ارسال آن تا فعال‌سازی نود متوقف است.');
  if(r.delivery_state==='configuration_pending')return L('Command saved; waiting for configuration synchronization before resume.','فرمان ذخیره شد؛ برای شروع، ابتدا باید تنظیمات همگام شود.');
  return L('Command saved and awaiting Node acknowledgement; the monitor will retry.','فرمان ذخیره شد و در انتظار تأیید نود است؛ مانیتور دوباره تلاش می‌کند.');
 }
 if(action==='validate'&&r.validated)return L('Remote configuration validation completed.','اعتبارسنجی تنظیمات راه‌دور انجام شد.');
 if(r.executed===true)return L('Node acknowledged the requested core action.','نود اجرای فرمان هسته را تأیید کرد.');
 return L('No execution acknowledgement for this request; refresh Node status.','برای این درخواست تأیید اجرا دریافت نشد؛ وضعیت نود را تازه‌سازی کن.');
}
function nodeCard(n){
 const h=n.health||{},core=h.core||{},status=!n.enabled?'disabled':n.online?'online':n.last_error?'error':'offline';
 const desired=n.desired_state||{},assigned=assignedNames(n),pending=!!desired.pending;
 const desiredLabel=desired.last_error?L('ERROR','خطا'):pending?L('PENDING r','در انتظار r')+String(desired.revision||0):desired.revision?L('SYNCED','همگام'):L('NOT DEPLOYED','مستقر نشده');
 return `<article class="panel nv2-node"><div class="nv2-head"><div><h3>${e(n.name)}</h3><small>${e(n.origin)} · ${e(n.data_address||'—')}</small></div><div class="nv2-status ${status}"><i></i><b>${e(status)}</b></div></div>
 <div class="nv2-metrics">${healthMetric(L('Latency','تأخیر'),n.last_latency_ms?`${n.last_latency_ms} ms`:'—')}${healthMetric('Xray',core.state||'—')}${healthMetric(L('Inbounds','اینباندها'),assigned.length)}${healthMetric(L('Deployment','استقرار'),desiredLabel)}</div>
 ${controlBanner(n)}
 ${assigned.length?`<div class="nv2-assigned"><span>${L('DEPLOYED / ASSIGNED','تخصیص اینباند')}</span><div>${assigned.map(x=>`<span class="nv4-assignment ${pending?'warn':'ready'}"><b>${e(x)}</b></span>`).join('')}</div></div>`:''}
 ${n.last_error?`<div class="nv2-error">${e(n.last_error)}</div>`:''}${desired.last_error?`<div class="nv2-error">${e(desired.last_error)}</div>`:''}
 <div class="nv2-actions">${n.enabled?button(L('Sync','همگام‌سازی'),'nv2sync','refresh',`data-id="${e(n.id)}"`,pending):''}${button(L('Manage','مدیریت'),'nv2edit','settings',`data-id="${e(n.id)}"`,true)}${button(L('Check','بررسی'),'nv2probe','activity',`data-id="${e(n.id)}"`)}</div></article>`;
}
function tokenList(tokens){const now=Date.now()/1000;return tokens.length?`<div class="xv2-list">${tokens.map(t=>{const active=!!t.enabled&&Number(t.expires_at)>now,stateLabel=!t.enabled?L('Revoked','باطل'):active?L('Enabled','فعال'):L('Expired','منقضی');return `<div class="xv2-item"><div class="xv2-item-head"><div><b>${e(t.name)}</b><br><small>${e(t.id)} · ${stateLabel} · ${L('expires','انقضا')} ${date(t.expires_at)}</small></div>${active?button(L('Revoke','ابطال'),'nv2tokenrevoke','trash',`data-id="${e(t.id)}"`):''}</div></div>`;}).join('')}</div>`:empty(L('No agent tokens on this server.','توکن عاملی روی این سرور وجود ندارد.'));}

function primaryEndpoint(p){return `<span class="nv4-primary"><b class="mono">${e(p.address)}:${e(p.port)}</b><small>${e(p.remark||'')}</small></span>`;}
function routeCard(r){
 const label=reasonLabel(r.subscription_reason),cls=reasonClass(r.subscription_reason);
 return `<article class="nv4-route ${cls}"><header><div><b>${e(r.name)}</b><small>${e(r.node_id)} · P${e(r.priority)} · ${r.latency_ms?e(r.latency_ms)+' ms':'—'}</small></div><span>${e(label)}</span></header>
 <div class="nv4-route-grid"><div><small>${L('FAILOVER ENDPOINT','نقطه اتصال فیل‌اور')}</small><b class="mono">${e(r.data_address||'—')}:${e(r.data_port)}</b></div><div><small>${L('DEPLOYMENT','استقرار')}</small><b>${e(r.deployment_state)}</b></div><div><small>${L('REMOTE INBOUND','اینباند راه‌دور')}</small><b>${r.remote_inbound_id?'#'+e(r.remote_inbound_id):'—'}</b></div><div><small>${L('LAST SYNC','آخرین همگام‌سازی')}</small><b>${r.last_sync?date(r.last_sync):'—'}</b></div></div>
 ${r.last_error?`<div class="nv2-error">${e(r.last_error)}</div>`:''}
 <footer>${button(L('Sync this node','همگام‌سازی این نود'),'nv2sync','refresh',`data-id="${e(r.node_id)}"`,r.subscription_reason!=='ready')}${button(L('Edit node','ویرایش نود'),'nv2edit','edit',`data-id="${e(r.node_id)}"`)}</footer></article>`;
}
function orchestrationBoard(doc){
 if(!doc)return `<article class="panel nv4-orch-empty"><b>${L('Subscription orchestration unavailable.','هماهنگ‌سازی اشتراک در دسترس نیست.')}</b></article>`;
 const s=doc.summary||{},rows=doc.inbounds||[];
 return `<section class="nv4-orchestrator"><div class="nv4-orch-head"><div><span>${L("DARK XRAY / NODE ORCHESTRATOR","DARK XRAY / هماهنگ‌ساز نود")}</span><h2>${L('Subscription Orchestrator','هماهنگ‌ساز اشتراک')}</h2><p>${L('See exactly which node routes are deployed and which ones are actually emitted into customer subscriptions.','دقیقاً ببین کدام مسیر نود مستقر شده و کدام مسیر واقعاً داخل اشتراک مشتری منتشر می‌شود.')}</p></div><div class="nv4-orch-summary">${healthMetric(L('Nodes','نود'),s.nodes||0)}${healthMetric(L('Inbounds','اینباند'),s.inbounds||0)}${healthMetric(L('Assigned routes','مسیر تخصیص'),s.assigned_routes||0)}${healthMetric(L('Deployed','مستقرشده'),s.deployed_routes||0)}${healthMetric(L('In subscription','داخل اشتراک'),s.subscription_routes||0)}</div></div>
 <div class="notice">${L('Primary routes come from Public Endpoints. Failover routes use the node Data Address plus the source inbound port. Only routes marked IN SUBSCRIPTION are delivered to customers.','مسیر اصلی از نقاط اتصال عمومی می‌آید. مسیر فیل‌اور از آدرس دادهٔ نود به‌علاوه پورت اینباند مبدأ استفاده می‌کند. فقط مسیرهای «داخل اشتراک» به مشتری تحویل داده می‌شوند.')}</div>
 <div class="nv4-inbound-list">${rows.length?rows.map(inboundOrchestration).join(''):empty(L('No inbounds to orchestrate.','اینباندی برای Orchestration وجود ندارد.'))}</div></section>`;
}
function inboundOrchestration(row){
 const routes=row.routes||[],ready=routes.filter(x=>x.subscription_included).length;
 return `<article class="panel nv4-inbound"><header><div><small>#${e(row.inbound_id)} · ${e(String(row.protocol||'').toUpperCase())} · ${e(String(row.network||'').toUpperCase())} / ${e(String(row.security||'').toUpperCase())}</small><h3>${e(row.remark)}</h3></div><div class="nv4-inbound-stats"><span>${fa(row.client_count||0)} ${L('clients','کاربر')}</span><span class="${ready?'ready':'off'}">${fa(ready)} ${L('failover routes','مسیر فیل‌اور')}</span></div></header>
 <div class="nv4-primary-line"><span>${L('PRIMARY / PUBLIC ENDPOINTS','مسیر اصلی / نقطه اتصال عمومی')}</span><div>${(row.primary_endpoints||[]).map(primaryEndpoint).join('')}</div></div>
 <div class="nv4-routes">${routes.length?routes.map(routeCard).join(''):`<div class="nv4-no-route"><b>${L('No node assigned to this inbound.','هیچ نودی به این اینباند اختصاص داده نشده.')}</b><small>${L('Edit a node and assign this inbound when you want remote deployment or failover.','برای استقرار یا فیل‌اور، از ویرایش نود این اینباند را به نود اختصاص بده.')}</small></div>`}</div></article>`;
}

async function nodesPage(){
 let d=await loadNodes(),errs='';
 if(d.nodeError)errs+=`<div class="notice error">${L('Node registry could not be loaded: ','فهرست نودها دریافت نشد: ')}${e(d.nodeError)}</div>`;
 const online=d.nodes.filter(x=>x.online).length,pending=d.nodes.filter(x=>x.desired_state?.pending||x.control?.pending).length;
 return heading(L('Nodes','نودها'),L('Install the lightweight agent once, pair it here, then manage deployments from this Hub.','Agent سبک را یک‌بار نصب و Pair کن؛ بعد همه استقرارها را از همین Hub مدیریت کن.'),button(L('Add Node','افزودن نود'),'nv2new','plus','',true))+
 `<div class="nv2">${errs}<section class="nv5-fleet-head"><div>${healthMetric(L('Nodes','نودها'),d.nodes.length)}${healthMetric(L('Online','آنلاین'),online)}${healthMetric(L('Pending changes','تغییر در انتظار'),pending)}</div><p>${L('Nodes do not need a second control panel. Inbounds, clients, traffic and security are owned by this Hub.','نودها پنل دوم لازم ندارند؛ اینباند، کاربر، ترافیک و امنیت از همین Hub مدیریت می‌شود.')}</p></section>
 <section><div class="nv4-section-title"><div><small>DARK NODE FLEET</small><h2>${L('Servers','سرورها')}</h2></div><span>${fa(d.nodes.length)}</span></div><div class="nv2-grid">${d.nodes.length?d.nodes.map(nodeCard).join(''):empty(L('No nodes yet. Install the lightweight Node Agent and paste its Pair Code.','هنوز نودی اضافه نشده؛ Agent سبک را نصب و Pair Code را اینجا وارد کن.'))}</div></section>
 <details class="panel nv5-advanced"><summary>${L('Deployment & failover details','جزئیات استقرار و فیل‌اور')}</summary>${d.orchestrationError?`<div class="notice warning">${e(d.orchestrationError)}</div>`:`${orchestrationBoard(d.orchestration)}`}</details></div>`;
}
enginePage=async function(){if(state.page==='nodes')return nodesPage();return baseEnginePage();};
function inboundPicker(selected=[]){return `<div class="nv2-picker">${state.inbounds.map(ib=>{let on=selected.includes(ib.id);return `<label class="${on?'active':''}"><input type="checkbox" name="inboundIds" value="${ib.id}" ${on?'checked':''}><span class="nv2-pick-dot"></span><span><b>${e(ib.remark||ib.tag)}</b><small>#${ib.id} · ${e(ib.protocol)} · ${e(ib.network||'tcp')} / ${e(ib.security||'none')} · :${ib.port}</small></span></label>`;}).join('')}</div>`;}
function manageActions(n){
 return `${controlBanner(n)}<div class="span-2 nv5-manage-actions"><div class="nv2-form-title"><b>${L('Remote operations','عملیات راه‌دور')}</b><small>${L('Daily Node operations stay in the Hub; SSH is only for recovery.','عملیات روزمره Node از Hub انجام می‌شود؛ SSH فقط برای بازیابی است.')}</small></div><div>${button(L('Health Check','بررسی سلامت'),'nv2probe','activity',`data-id="${e(n.id)}"`)}${button(L('Sync Now','همگام‌سازی'),'nv2sync','refresh',`data-id="${e(n.id)}"`)}${button(L('Remote Inbounds','اینباندهای Node'),'nv2inbounds','server',`data-id="${e(n.id)}"`)}${button(L('Sync Security','همگام‌سازی امنیت'),'nv2security','shield',`data-id="${e(n.id)}"`)}${button(L('Validate Xray','اعتبارسنجی Xray'),'nv2core','check',`data-id="${e(n.id)}" data-core="validate"`)}${button(L('Start Xray','شروع Xray'),'nv2core','play',`data-id="${e(n.id)}" data-core="start"`)}${button(L('Stop Xray','توقف Xray'),'nv2core','stop',`data-id="${e(n.id)}" data-core="stop"`)}${button(L('Restart Xray','ری‌استارت Xray'),'nv2core','refresh',`data-id="${e(n.id)}" data-core="restart"`)}${button(L('Logs','لاگ‌ها'),'nv2logs','log',`data-id="${e(n.id)}"`)}${button(L('Update to Hub Version','آپدیت به نسخه Hub'),'nv2update','download',`data-id="${e(n.id)}"`,true)}${button(L('Delete Node','حذف Node'),'nv2delete','trash',`data-id="${e(n.id)}"`)}</div></div>`;
}
async function nodeDialog(id){
 if(!isOwner())return;
 const ownerId=state.me?.id,rotation=await api('/api/nodes/'+enc(id)+'/credentials/current');
 const validRotation=r=>r&&r.node_id===id&&['not_started','pending','rotating','completed'].includes(r.phase)&&typeof r.pending==='boolean'&&typeof r.rotated==='boolean'&&typeof r.binding_current==='boolean'&&typeof r.credential_current==='boolean'&&typeof r.binding_id==='string'&&/^[0-9a-f]{32}$/.test(r.binding_id)&&(r.phase==='not_started'?!r.pending&&!r.rotated:(typeof r.attempt_id==='string'&&/^[0-9a-f]{32}$/.test(r.attempt_id)&&r.pending===(r.phase!=='completed')&&r.rotated===(r.phase==='completed')));
 if(!validRotation(rotation))throw Error(L('Invalid credential handoff status.','وضعیت تعویض توکن نامعتبر است.'));
 if(!isOwner()||state.me?.id!==ownerId)return;
 let submitting=false;
 const n=state.nv2.nodes.find(x=>x.id===id);if(!n)throw Error(L('Node not found.','نود پیدا نشد.'));
 dialog(L('Manage Node','مدیریت نود'),`<div class="nv2-form">${rotation.pending?`<div class="span-2 notice warning">${L('A token change is waiting for confirmation. Keep the token field empty and explicitly resume it; no other settings will be saved by retry.','تعویض توکن در انتظار تأیید است. توکن را خالی بگذار و ادامه را تأیید کن؛ با ادامه، تنظیمات دیگر ذخیره نمی‌شوند.')}${rotation.last_error?`<p>${e(rotation.last_error)}</p>`:''}<label><input type="checkbox" name="retryCredential">${L('Resume the saved token change','ادامهٔ تعویض توکن ذخیره‌شده')}</label></div>`:''}<div class="span-2 notice">${L('Change the token separately from name, address and inbound edits. A timeout is not proof that rotation failed.','توکن را جدا از نام، آدرس و اینباندها تغییر بده. قطع پاسخ به معنی انجام‌نشدن تعویض نیست.')}</div>${field(L('Node ID','شناسه نود'),'id',n.id,'text','readonly')}${field(L('Display name','نام نمایشی'),'name',n.name||'','text','required maxlength="128"')}${field(L('HTTPS Origin','آدرس HTTPS'),'origin',n.origin||'','url','required dir="ltr"')}${field(L('Data address','آدرس اتصال مشتری'),'dataAddress',n.data_address||'','text','maxlength="253" dir="ltr"')}${field(L('Rotate agent token','تعویض توکن Agent'),'token','','password',L('Leave blank to keep the current token.','برای حفظ توکن فعلی خالی بگذار.'),'placeholder="dkn_..."')}${field(L('Failover priority','اولویت فیل‌اور'),'priority',n.priority||100,'number','', 'required min="1" max="1000"')}<label>${L('Enabled','فعال')}<select name="enabled"><option value="true" ${n.enabled?'selected':''}>${L('Yes','بله')}</option><option value="false" ${!n.enabled?'selected':''}>${L('No','خیر')}</option></select></label><label>${L('Failover','فیل‌اور')}<select name="failoverEnabled"><option value="true" ${n.failover_enabled?'selected':''}>${L('Yes','بله')}</option><option value="false" ${!n.failover_enabled?'selected':''}>${L('No','خیر')}</option></select></label><div class="span-2"><div class="nv2-form-title"><b>${L('Inbound deployments','استقرار اینباندها')}</b><small>${L('Inbound editor can also change these targets. Clients follow their inbound automatically.','از داخل اینباند هم می‌توان این مقصدها را تغییر داد؛ کاربران خودکار از اینباند پیروی می‌کنند.')}</small></div>${state.inbounds.length?inboundPicker(n.inboundIds||[]):`<div class="notice">${L('No inbounds exist yet. The node can stay paired until you create one.','هنوز اینباندی ساخته نشده؛ نود می‌تواند Pair باقی بماند تا بعداً اینباند بسازی.')}</div>`}${manageActions(n)}</div></div>`,state.me?.writes_enabled===true?async (fd,form)=>{
   if(!form.isConnected||!isOwner()||state.me?.id!==ownerId||state.me?.writes_enabled!==true)return;
   if(submitting)return;submitting=true;
   try{
   const retry=fd.get('retryCredential');
   if(retry){
     if(!rotation.pending||!rotation.binding_current||fd.get('token'))throw Error(L('Keep token empty and select the pending handoff.','توکن را خالی بگذار و عملیات در انتظار را انتخاب کن.'));
     const checkbox=form.querySelector('[name=retryCredential]');if(checkbox)checkbox.checked=false;
     const r=await api('/api/nodes/'+enc(id)+'/credentials/'+enc(rotation.attempt_id)+'/retry','POST',{confirmRetry:true});
     if(!form.isConnected||!isOwner()||state.me?.id!==ownerId||state.me?.writes_enabled!==true)return;
     if(!validRotation(r)||r.attempt_id!==rotation.attempt_id||r.phase!=='completed'||r.rotated!==true||r.pending!==false||r.binding_current!==true||r.credential_current!==true)throw Error(L('Token change is not confirmed. Reopen Manage Node and resume saved status.','تعویض توکن تأیید نشده؛ مدیریت نود را دوباره باز کن و وضعیت ذخیره‌شده را ادامه بده.'));
     closeDialog();toast(L('Node token change confirmed.','تعویض توکن نود تأیید شد.'));await renderPage();return;
   }
   if(rotation.pending)throw Error(L('Resolve the saved token change first.','ابتدا تعویض توکن ذخیره‌شده را تکمیل کن.'));
   const inboundIds=fd.getAll('inboundIds').map(Number),body={name:fd.get('name'),origin:fd.get('origin'),dataAddress:fd.get('dataAddress'),priority:Number(fd.get('priority')),enabled:fd.get('enabled')==='true',failoverEnabled:fd.get('failoverEnabled')==='true',inboundIds,keep_token:!fd.get('token')};
   if(fd.get('token'))body.token=fd.get('token');
   let r;
   try{r=await api('/api/nodes/'+enc(id),'PATCH',body);}
   finally{const input=form.querySelector('[name=token]');if(input)input.value='';}
   if(!form.isConnected||!isOwner()||state.me?.id!==ownerId||state.me?.writes_enabled!==true)return;
   if(body.token&&(!validRotation(r)||r.phase!=='completed'||r.rotated!==true||r.pending!==false||r.binding_current!==true||r.credential_current!==true))throw Error(L('Token change is not confirmed. Reopen Manage Node and resume saved status.','تعویض توکن تأیید نشده؛ مدیریت نود را دوباره باز کن و وضعیت ذخیره‌شده را ادامه بده.'));
   closeDialog();toast(body.token?L('Node token change confirmed.','تعویض توکن نود تأیید شد.'):L('Node settings saved.','تنظیمات نود ذخیره شد.'));await renderPage();
   }finally{submitting=false;}
 }:null);
 const form=$('#dialog-form');if(form)form.addEventListener('change',ev=>{if(ev.target.name==='inboundIds')ev.target.closest('label')?.classList.toggle('active',ev.target.checked);});
}
async function pairNodeDialog(){
 if(!isOwner())return;
 const ownerId=state.me?.id, saved=await api('/api/nodes/pairings');
 const valid=x=>x&&typeof x.attempt_id==='string'&&/^[0-9a-f]{32}$/.test(x.attempt_id)&&typeof x.node_id==='string'&&typeof x.origin==='string'&&typeof x.name==='string'&&x.paired===false&&['pending','rotating','cancelling'].includes(x.phase);
 if(!saved||!Array.isArray(saved.items)||!saved.items.every(valid)||new Set(saved.items.map(x=>x.attempt_id)).size!==saved.items.length)throw Error(L('Invalid saved pairing status.','وضعیت ذخیره‌شدهٔ اتصال نامعتبر است.'));
 if(!isOwner()||state.me?.id!==ownerId)return;
 const items=saved.items,canWrite=state.me?.writes_enabled===true;let submitting=false;
 dialog(L('Pair lightweight Node','اتصال Node سبک'),`<div class="nv5-pair"><div class="notice">${L('On the new VPS run install-node.sh. It prints one DXN1 Pair Code. Paste that code below; no second web panel is installed.','روی VPS جدید install-node.sh را اجرا کن. یک Pair Code با DXN1 می‌دهد؛ همان را اینجا وارد کن و پنل وب دوم نصب نمی‌شود.')}</div>
 ${items.length?`<label>${L('Saved pending connection','اتصال ذخیره‌شدهٔ در انتظار')}<select name="resume"><option value="">${L('Use a new Pair Code below','استفاده از کد اتصال جدید')}</option>${items.map(x=>`<option value="${e(x.attempt_id)}">${e(x.name)} · ${e(x.origin)}${x.phase==='cancelling'?' · '+L('CANCELLATION PENDING','لغو در انتظار تأیید'):''}</option>`).join('')}</select></label>
 <label>${L('Action for selected connection','اقدام برای اتصال انتخاب‌شده')}<select name="pairAction"><option value="resume">${L('Resume enrollment','ادامهٔ ثبت نود')}</option><option value="cancel">${L('Cancel / continue cancellation','لغو / پیگیری لغو')}</option></select></label>
 <label><input type="checkbox" name="confirmRetry">${L('I authorize continuing the selected saved connection with its retained credential.','ادامهٔ اتصال ذخیره‌شدهٔ انتخاب‌شده با اطلاعات ارتباطی محفوظ را تأیید می‌کنم.')}</label>
 <div class="notice warning">${L('Cancellation is not Node deletion. Before token rotation is dispatched, it only withdraws the saved request and leaves the Pair Code unchanged. After dispatch, the Hub must revoke the saved credentials and confirm the empty target is stopped. Offline or unknown results keep the reservation and recovery data.','لغو، حذف نود نیست. پیش از ارسال تعویض توکن فقط درخواست ذخیره‌شده کنار گذاشته می‌شود و کد اتصال تغییر نمی‌کند. پس از ارسال، پنل باید توکن‌های محفوظ را بی‌اعتبار و توقف هدف خالی را تأیید کند. در حالت قطع ارتباط یا نتیجهٔ نامعلوم، رزرو و اطلاعات بازیابی حفظ می‌شوند.')}</div>
 <label><input type="checkbox" name="confirmCancel">${L('I authorize cancelling the selected pending enrollment, not a registered Node.','لغو فرایند افزودن نیمه‌کارهٔ انتخاب‌شده را تأیید می‌کنم؛ نه حذف یک نود ثبت‌شده.')}</label>
 <label><input type="checkbox" name="acknowledgeCredentialReset">${L('I understand that after remote disposal the old Pair Code is unusable; reuse requires reinstall or an authorized local token reset.','می‌دانم بعد از کنارگذاشتن هدف از راه دور، کد قبلی قابل استفاده نیست؛ استفادهٔ دوباره نیازمند نصب مجدد یا بازنشانی مجاز توکن روی سرور است.')}</label>`:''}
 <label><span>${L('Pair Code (new connections only)','کد اتصال (فقط برای اتصال جدید)')}</span><textarea class="field-input" name="code" rows="6" dir="ltr" autocomplete="off" placeholder="DXN1...."></textarea></label><div class="notice">${L('Opening this dialog only reads saved operations. A pending response is not completion; every retry or cancellation requires explicit confirmation.','بازکردن این پنجره فقط عملیات ذخیره‌شده را می‌خواند. پاسخ در انتظار، پایان عملیات نیست؛ ادامه یا لغو همیشه تأیید صریح می‌خواهد.')}</div></div>`,canWrite?async (fd,form)=>{
   if(submitting||!form.isConnected||!isOwner()||state.me?.id!==ownerId||state.me?.writes_enabled!==true)return;
   const attempt=String(fd.get('resume')||''),code=String(fd.get('code')||'').trim(),action=String(fd.get('pairAction')||'resume');
   const selected=items.find(x=>x.attempt_id===attempt),cancelling=action==='cancel';
   if(!['resume','cancel'].includes(action))throw Error(L('Invalid connection action.','اقدام اتصال نامعتبر است.'));
   if(cancelling){
    if(!selected||code||!fd.get('confirmCancel')||!fd.get('acknowledgeCredentialReset')||fd.get('confirmRetry'))throw Error(L('Select a saved connection, leave Pair Code empty and confirm both cancellation warnings. Clear retry consent.','اتصال ذخیره‌شده را انتخاب کن، کد را خالی بگذار و هر دو هشدار لغو را تأیید کن. تأیید ادامهٔ ثبت را بردار.'));
   }else{
    if(fd.get('confirmCancel')||fd.get('acknowledgeCredentialReset'))throw Error(L('Choose cancellation or clear its confirmations before enrollment.','برای لغو، اقدام لغو را انتخاب کن؛ برای ثبت نود تأییدهای لغو را بردار.'));
    if(attempt&&(!selected||!fd.get('confirmRetry')||code))throw Error(L('Select a saved connection, leave Pair Code empty and explicitly confirm retry.','یک اتصال ذخیره‌شده انتخاب کن، کد را خالی بگذار و ادامه را صریح تأیید کن.'));
    if(selected?.phase==='cancelling')throw Error(L('Cancellation has started. Select cancellation to continue it; enrollment cannot resume.','لغو شروع شده است؛ برای پیگیری، اقدام لغو را انتخاب کن. ثبت نود قابل ادامه نیست.'));
    if(!attempt&&!code)throw Error(L('Enter a Pair Code.','کد اتصال را وارد کن.'));
   }
   submitting=true;
   try{
    let r;
    try{r=cancelling?await api('/api/nodes/pairings/'+enc(attempt)+'/cancel','POST',{confirmCancel:true,acknowledgeCredentialReset:true}):attempt?await api('/api/nodes/pairings/'+enc(attempt)+'/retry','POST',{confirmRetry:true}):await api('/api/nodes/pair','POST',{code});}
    finally{
     const input=form.querySelector?.('[name=code]');if(input)input.value='';
     for(const name of ['confirmRetry','confirmCancel','acknowledgeCredentialReset']){const box=form.querySelector?.('[name='+name+']');if(box)box.checked=false;}
    }
    if(!form.isConnected||!isOwner()||state.me?.id!==ownerId||state.me?.writes_enabled!==true)return;
    if(cancelling){
     const discarded=r?.outcome==='discarded_after_rotation';
     if(!r||r.attempt_id!==attempt||r.node_id!==selected.node_id||r.origin!==selected.origin||r.phase!=='cancelled'||r.cancelled!==true||r.paired!==false||r.registration_current!==false||r.status_scope!=='saved_pairing_cancellation_receipt'||r.live_state_verified!==false||r.credentials_retained_in_journal!==false||r.reservation_released!==true||!['withdrawn_before_rotation','discarded_after_rotation'].includes(r.outcome)||['pair_code_consumed','candidate_discarded','candidate_requires_reinstall','credential_revocation_confirmed','remote_idle_confirmed'].some(k=>r[k]!==discarded))throw Error(L('Cancellation is not confirmed. Recovery data remains reserved; reopen Add Node and explicitly continue cancellation.','لغو تأیید نشده است. اطلاعات بازیابی و رزرو محفوظ‌اند؛ افزودن نود را دوباره باز کن و لغو را صریح پیگیری کن.'));
     closeDialog();toast(discarded?L('Enrollment cancelled; target disposal was confirmed. Reinstall or locally reset its token before reuse.','ثبت نود لغو و کنارگذاشتن هدف تأیید شد. پیش از استفادهٔ دوباره، نصب مجدد یا بازنشانی محلی توکن لازم است.'):L('Saved enrollment withdrawn without contacting the target. The Pair Code was not revoked.','درخواست ثبت بدون تماس با هدف کنار گذاشته شد. کد اتصال بی‌اعتبار نشده است.'));await renderPage();return;
    }
    if(r?.paired!==true||r.phase!=='paired'||r.pair_code_consumed!==true||r.registration_current!==true||typeof r.node_id!=='string'||!r.node_id||!r.node||r.node.id!==r.node_id||typeof r.attempt_id!=='string'||!/^[0-9a-f]{32}$/.test(r.attempt_id)||(attempt&&r.attempt_id!==attempt))throw Error(L('Connection is not confirmed. Reopen Add Node to resume its saved operation; do not delete recovery data.','اتصال تأیید نشده است. افزودن نود را دوباره باز کن و عملیات ذخیره‌شده را ادامه بده؛ اطلاعات بازیابی را حذف نکن.'));
    closeDialog();toast(`${L('Node paired','نود متصل شد')} · ${r.node.name||r.node.id}`);await renderPage();
   }finally{submitting=false;}
 }:null,L('Apply selected connection action','اجرای اقدام اتصال'));
 const form=typeof $==='function'?$('#dialog-form'):null;
 form?.addEventListener('change',ev=>{
  if(['resume','pairAction'].includes(ev.target.name))for(const name of ['confirmRetry','confirmCancel','acknowledgeCredentialReset']){const box=form.querySelector('[name='+name+']');if(box)box.checked=false;}
 });
}
async function createToken(){dialog(L('New node agent token','توکن عامل جدید'),`<div class="nv2-form">${field(L('Token name','نام توکن'),'name','central','text','required maxlength="64"')}${field(L('Validity days','اعتبار روز'),'days',365,'number','required min="1" max="3650"')}</div>`,async f=>{let r=await api('/api/node-agent/tokens','POST',{name:f.get('name'),days:Number(f.get('days'))});await renderPage();dialog(L('Copy this token now','این توکن را همین حالا کپی کن'),`<div class="notice warning">${L('The plaintext token will never be shown again.','متن اصلی توکن دیگر نمایش داده نمی‌شود.')}</div><div class="nv2-token">${e(r.token)}</div>`);});}
async function showNodeInbounds(id){let r=await api('/api/nodes/'+enc(id)+'/inbounds');dialog(L('Remote inbounds','اینباندهای راه‌دور'),`<div class="notice">${L('Read live from the remote DARK node.','به‌صورت زنده از نود راه‌دور DARK خوانده شده است.')} · ${r.latency_ms} ms</div><div class="nv2-inbounds">${r.items.length?r.items.map(i=>`<div class="nv2-inbound"><span><b>${e(i.remark||i.tag)}</b><br><small>${e(i.protocol)} · ${e(i.listen||'0.0.0.0')}:${i.port}</small></span><span class="tag ${i.enable?'green':'red'}">${i.enable?L('ON','روشن'):L('OFF','خاموش')}</span></div>`).join(''):empty(L('No inbounds on node.','اینباندی روی نود نیست.'))}</div>`);}
async function showNodeLogs(id,kind='process'){
 const r=await api('/api/nodes/'+enc(id)+'/logs/'+kind+'?limit=400');
 const tabs=`<div class="nv5-log-tabs"><button type="button" class="btn" data-act="nv2logkind" data-id="${e(id)}" data-kind="process">Process</button><button type="button" class="btn" data-act="nv2logkind" data-id="${e(id)}" data-kind="error">Error</button><button type="button" class="btn" data-act="nv2logkind" data-id="${e(id)}" data-kind="access">Access</button></div>`;
 dialog(L('Node logs','لاگ‌های Node'),tabs+`<pre class="terminal nv5-node-log">${e((r.lines||[]).join('\n')||L('No log lines.','لاگی ثبت نشده است.'))}</pre>`,null);
}
async function refreshAfterRemote(fn){try{return await fn();}finally{if(state.page==='nodes')await renderPage();}}
runAction=async function(act,el){
 if(act==='nv2new'){await pairNodeDialog();return;}if(act==='nv2edit'){await nodeDialog(el.dataset.id);return;}
 if(act==='nv2probe'){await refreshAfterRemote(async()=>{let r=await api('/api/nodes/'+enc(el.dataset.id)+'/probe','POST',{});toast(`${L('Node online','نود آنلاین')} · ${r.latency_ms} ms`);});return;}
 if(act==='nv2inbounds'){await refreshAfterRemote(()=>showNodeInbounds(el.dataset.id));return;}
 if(act==='nv2sync'){await refreshAfterRemote(async()=>{let r=await api('/api/nodes/'+enc(el.dataset.id)+'/sync','POST',{});if(r.queued||r.sync_deferred){toast(controlMessage(r));return;}toast(`${L('Node synchronized','نود همگام شد')} · ${(r.items||[]).length} ${L('inbounds','اینباند')} · ${bytes(r.traffic?.charged_bytes||0)} ${L('new traffic','ترافیک جدید')}`);});return;}
 if(act==='nv2security'){await refreshAfterRemote(async()=>{let r=await api('/api/nodes/'+enc(el.dataset.id)+'/security','POST',{});toast(`${L('Security synchronized','امنیت همگام شد')} · ${r.node.ips} IP · ${r.node.devices} ${L('devices','دستگاه')}`);});return;}
 if(act==='nv2core'){
  const action=el.dataset.core,id=el.dataset.id;
  if(!['validate','start','stop','restart'].includes(action)||controlBusy.has(id))return;
  if(action!=='validate'&&!confirm(action==='stop'?L('Stop Xray on this Node? Sessions will disconnect; use Start to resume.','Xray این نود متوقف شود؟ اتصال‌ها قطع می‌شوند؛ برای ادامه از شروع استفاده کن.'):action==='restart'?L('Restart Xray on the remote node? Active sessions may disconnect.','Xray روی نود راه‌دور ری‌استارت شود؟ اتصال‌ها ممکن است قطع شوند.'):L('Start Xray on this Node?','Xray روی این نود شروع شود؟')))return;
  controlBusy.add(id);
  try{await refreshAfterRemote(async()=>{const r=await api('/api/nodes/'+enc(id)+'/core/'+action,'POST',{});toast(controlMessage(r,action));});}
  finally{controlBusy.delete(id);}
  return;
 }
 if(act==='nv2logs'){await showNodeLogs(el.dataset.id);return;}
 if(act==='nv2logkind'){await showNodeLogs(el.dataset.id,el.dataset.kind);return;}
 if(act==='nv2update'){const id=el.dataset.id;await refreshAfterRemote(async()=>{const checked=await api('/api/nodes/'+enc(id)+'/update/check','POST',{}),u=checked.update||{},candidate=u.candidate||{};if(!candidate.ready)throw Error(L('Node update candidate is not verified by CI.','نسخه آپدیت Node توسط CI تأیید نشده است.'));if(!candidate.update_available){toast(L('Node is already on the Hub version.','Node همین نسخه Hub را دارد.'));return;}if(!confirm(L('Update this Node to the exact Hub commit? The Node Agent and Xray may restart briefly.','این Node به SHA دقیق Hub آپدیت شود؟ Agent و Xray ممکن است کوتاه ری‌استارت شوند.')))return;await api('/api/nodes/'+enc(id)+'/update/start','POST',{});toast(L('Node update started. Health will recover automatically after restart.','آپدیت Node شروع شد؛ بعد از ری‌استارت وضعیت سلامت خودکار برمی‌گردد.'));});return;}
 if(act==='nv2delete'){if(confirm(L('Delete node registry entry? The remote node is not uninstalled.','رکورد نود حذف شود؟ خود نود حذف نصب نمی‌شود.'))){await api('/api/nodes/'+enc(el.dataset.id),'DELETE');await renderPage();}return;}
 if(act==='nv2tokennew'){await createToken();return;}if(act==='nv2tokenrevoke'){if(confirm(L('Revoke this agent token immediately?','این توکن عامل فوراً باطل شود؟'))){await api('/api/node-agent/tokens/'+enc(el.dataset.id),'DELETE');await renderPage();}return;}
 return baseRunAction(act,el);
};
})();
