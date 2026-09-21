/* DARK XRAY: explicit sequence repair after an old Hub backup.
 * Saved receipts are not live observations. No automatic repair, sync or Start.
 */
(function(){
'use strict';
if(typeof enginePage!=='function'||typeof runAction!=='function')return;
const pageBase=enginePage,actionBase=runAction;
const esc=v=>e(String(v??'—'));
const hex=(v,n)=>typeof v==='string'&&new RegExp('^[0-9a-f]{'+n+'}$').test(v);
const seq=v=>Number.isSafeInteger(v)&&v>=0;
const text=(s,en,fa)=>s.lang==='fa'?fa:en;
const language=()=>localStorage.getItem('dark_lang')||'en';
const reasons={
 recovery_incomplete:['An earlier recovery is incomplete.','بازیابی قبلی هنوز تکمیل نشده است.'],
 agent_command_ahead:['The node has newer commands than this Hub.','فرمان‌های نود از اطلاعات پنل جدیدترند.'],
 command_identity_conflict:['The same command number has different identities.','یک شمارهٔ فرمان با دو هویت متفاوت ثبت شده است.'],
 agent_command_behind_ack:['The node is behind a command previously acknowledged by the Hub.','نود از فرمانی که پنل قبلاً تأییدش را داشته عقب‌تر است.'],
 agent_configuration_ahead:['The node has newer configuration than this Hub.','تنظیمات نود از اطلاعات پنل جدیدترند.'],
 configuration_hash_conflict:['The same configuration number has different content.','یک شمارهٔ تنظیمات با محتوای متفاوت ثبت شده است.'],
 agent_configuration_behind_ack:['The node is behind configuration previously acknowledged by the Hub.','نود از تنظیماتی که پنل قبلاً تأییدشان را داشته عقب‌تر است.']
};
const confirmations={
 acknowledgeServiceInterruption:['I authorize stopping this node. Active customer connections may disconnect.','توقف این نود را تأیید می‌کنم؛ اتصال‌های فعال مشتری‌ها ممکن است قطع شوند.'],
 acknowledgeBackupMayBeStale:['I understand that missing users and usage are not reconstructed. I will review the restored data before a separate sync and Start.','می‌دانم کاربران و مصرفِ غایب از بکاپ بازسازی نمی‌شوند؛ پیش از همگام‌سازی و شروع جداگانه، اطلاعات بازیابی‌شده را بررسی می‌کنم.']
};
const failures={
 'Verify and pin the current Agent installation before recovery':['Verify the current node installation first.','ابتدا هویت نصب فعلی نود را تأیید کن.'],
 'No Hub configuration snapshot; use explicit enrolment/rebuild instead':['No saved configuration exists in this Hub. Use the enrolment or rebuild workflow.','در این پنل نسخه‌ای از تنظیمات وجود ندارد؛ مسیر اتصال یا بازسازی نود لازم است.'],
 'Resolve pending Node replacement before sequence recovery':['Complete the pending server replacement first. Recovery cannot override it.','ابتدا جایگزینی سرورِ در انتظار را تعیین تکلیف کن؛ بازیابی آن را نادیده نمی‌گیرد.'],
 recovery_protocol_or_identity_unavailable:['The node must support recovery and match the saved installation identity.','نود باید از بازیابی پشتیبانی کند و هویتش با نصب ذخیره‌شده یکی باشد.'],
 recovery_contact_or_identity_failed:['The node connection or installation identity could not be verified.','ارتباط با نود یا هویت نصب آن تأیید نشد.'],
 recovery_review_changed_or_not_required:['The review changed or repair is no longer needed. Compare again.','بازبینی تغییر کرده یا تعمیر دیگر لازم نیست؛ دوباره مقایسه کن.'],
 recovery_review_changed:['The checkpoints changed during review. Compare again.','نسخه‌ها هنگام بازبینی تغییر کردند؛ دوباره مقایسه کن.'],
 recovery_sequence_exhausted:['The sequence range is exhausted. Do not reset it manually.','بازهٔ شماره‌ها تمام شده است؛ آن را دستی ریست نکن.']
};
let active=null;
function opener(id){return `<button type="button" class="btn" data-act="nrec-open" data-id="${esc(id)}">${text({lang:language()},'Recover after backup','بازیابی بعد از بکاپ')}</button>`;}
enginePage=async function(){
 const html=await pageBase();
 if(state.page!=='nodes'||!isOwner())return html;
 const fragment=document.createElement('template');fragment.innerHTML=html;
 fragment.content.querySelectorAll('.nv2-actions').forEach(box=>{
  const manage=box.querySelector('[data-act="nv2edit"]');
  if(manage&&!box.querySelector('[data-act="nrec-open"]'))box.insertAdjacentHTML('beforeend',opener(manage.dataset.id));
 });
 return fragment.innerHTML;
};
runAction=async function(action,el){
 if(action==='nrec-open'){await open(el.dataset.id,el);return;}
 const result=await actionBase(action,el);
 if(action==='nv2edit'&&isOwner()){
  const box=document.querySelector('.nv5-manage-actions');
  if(box&&!box.querySelector('[data-act="nrec-open"]'))box.insertAdjacentHTML('beforeend',opener(el.dataset.id));
 }
 return result;
};
function validStatus(doc,id){
 if(!doc||doc.node_id!==id||doc.status_scope!=='saved_recovery_receipt'||doc.live_state_verified!==false
     ||doc.configuration_applied!==false||doc.usage_reconciled!==false||doc.service_started!==false)return false;
 if(doc.phase==='not_started')return doc.recovery_completed===false&&!doc.attempt_id;
 if(!['pending','recovered_stopped'].includes(doc.phase)||!hex(doc.attempt_id,32)||!hex(doc.binding_id,32)
     ||typeof doc.binding_current!=='boolean'||typeof doc.node_enabled_now!=='boolean'
     ||typeof doc.activation_held!=='boolean'||typeof doc.last_error!=='string'
     ||!seq(doc.command_revision)||!seq(doc.rebased_revision))return false;
 return doc.recovery_completed===(doc.phase==='recovered_stopped')
     &&doc.activation_held===(doc.phase==='pending'&&doc.binding_current);
}
function validReview(doc,id){
 return doc&&doc.node_id===id&&hex(doc.binding_id,32)&&hex(doc.review_hash,64)
  &&typeof doc.recovery_needed==='boolean'&&Array.isArray(doc.reasons)
  &&doc.reasons.every(r=>typeof r==='string'&&Object.hasOwn(reasons,r))
  &&doc.recovery_needed===(doc.reasons.length>0)
  &&['hub_command_revision','agent_command_revision','hub_configuration_revision','agent_configuration_revision'].every(k=>seq(doc[k]))
  &&['running','stopped'].includes(doc.agent_core_state)
  &&doc.configuration_will_be_applied===false&&doc.node_will_remain_disabled===true
  &&doc.usage_will_be_reconstructed===false&&doc.backup_contents_require_operator_review===true;
}
function authorized(s){return s.live&&state.me?.role==='owner'&&state.me.id===s.owner;}
function allowed(s){
 const actions=['refresh'];
 if(!s.doc)return actions;
 actions.push('review'); // Read-only comparison is also available in read-only panel mode.
 if(state.me?.writes_enabled!==true)return actions;
 if(s.doc.phase==='pending'&&s.doc.binding_current===true)actions.push('retry');
 if(s.review?.recovery_needed===true)actions.push('stop');
 return actions;
}
function button(s,action,en,fa,confirm=false){
 return `<button type="button" class="btn ${confirm?'btn-primary':''}" data-nrec="${action}" ${confirm?'data-requires-consent disabled':''}>${text(s,en,fa)}</button>`;
}
function paint(s){
 if(!s.live)return;
 const doc=s.doc,review=s.review,actions=allowed(s),phase=doc?.phase||'unavailable';
 const label=phase==='pending'?text(s,'Recovery pending; Stop is not independently confirmed.','بازیابی در انتظار است؛ توقف مستقل تأیید نشده است.'):
  phase==='recovered_stopped'?text(s,'Saved receipt: Stop and sequence repair were confirmed.','رسید ذخیره‌شده: توقف و تعمیر شماره‌ها تأیید شده بود.'):
  phase==='not_started'?text(s,'No saved recovery for this installation.','بازیابی ذخیره‌شده‌ای برای این نصب وجود ندارد.'):
  text(s,'Saved status unavailable.','وضعیت ذخیره‌شده در دسترس نیست.');
 let html=`<p class="notice warning">${text(s,'This repairs command and configuration numbering on a known node; it is not a full backup restore. No configuration is sent and no Start or Restart is performed.','این فرایند شمارهٔ فرمان‌ها و تنظیمات یک نود شناخته‌شده را تعمیر می‌کند؛ بازیابی کامل بکاپ نیست. تنظیماتی ارسال نمی‌شود و Start یا Restart انجام نمی‌شود.')}</p>
 <p role="status" aria-live="polite" data-nrec-phase="${phase}" tabindex="-1"><strong>${esc(label)}</strong></p>`;
 if(doc?.attempt_id)html+=`<dl class="nr-meta"><dt>${text(s,'Attempt','عملیات')}</dt><dd><bdi>${esc(doc.attempt_id)}</bdi></dd><dt>${text(s,'Saved Stop revision','نسخهٔ فرمان توقف ذخیره‌شده')}</dt><dd>${doc.command_revision}</dd><dt>${text(s,'Pending configuration revision','نسخهٔ تنظیماتِ در انتظار')}</dt><dd>${doc.rebased_revision}</dd></dl>`;
 if(doc?.last_error)html+=`<p class="notice warning">${text(s,'Diagnostic code:','کد تشخیصی:')} <bdi>${esc(doc.last_error)}</bdi></p>`;
 if(doc?.binding_current===false)html+=`<p class="notice warning">${text(s,'The installation changed. This receipt cannot control the new installation.','نصب تغییر کرده است؛ این رسید اجازهٔ کنترل نصب جدید را نمی‌دهد.')}</p>`;
 if(phase==='recovered_stopped')html+=`<section data-nrec-receipt><p>${text(s,'This historical receipt does not prove that the node is stopped now. Refreshing or reopening never stops it again.','این رسید تاریخی اثبات نمی‌کند که نود همین حالا خاموش است. تازه‌سازی یا بازکردن پنجره، آن را دوباره متوقف نمی‌کند.')}</p><p>${text(s,'Review restored users, expiry, limits and usage before a separate synchronization and Start. Missing data has not been recovered; configuration is still unapplied by this operation.','پیش از همگام‌سازی و شروع جداگانه، کاربران، انقضا، محدودیت‌ها و مصرف بازیابی‌شده را بررسی کن. اطلاعات غایب بازسازی نشده‌اند و این عملیات تنظیمات را اعمال نکرده است.')}</p></section>`;
 if(s.error)html+=`<p role="alert" class="notice warning">${esc(s.error)}</p>`;
 if(actions.includes('review'))html+=`<section><p>${text(s,'Compare reads fresh node checkpoints without changing the node. It does not automatically identify a restore or authorize recovery.','مقایسه، نسخه‌های فعلی نود را بدون تغییر آن می‌خواند. تشخیص خودکارِ برگرداندن بکاپ یا مجوز بازیابی نیست.')}</p>${button(s,'review','Compare Hub and node','مقایسهٔ پنل و نود')}</section>`;
 if(review)html+=`<section class="nrec-review" data-nrec-needed="${review.recovery_needed}"><h3>${text(s,'Checkpoint comparison','مقایسهٔ نسخه‌ها')}</h3><div class="nr-table"><table><thead><tr><th>${text(s,'Checkpoint','بخش')}</th><th>${text(s,'Hub','پنل')}</th><th>${text(s,'Node','نود')}</th></tr></thead><tbody>
 <tr><th>${text(s,'Command','فرمان')}</th><td data-nrec-hub-command>${review.hub_command_revision}</td><td>${review.agent_command_revision}</td></tr><tr><th>${text(s,'Configuration','تنظیمات')}</th><td>${review.hub_configuration_revision}</td><td>${review.agent_configuration_revision}</td></tr></tbody></table></div>
 <p>${text(s,'Core state observed at comparison:','وضعیت هسته هنگام مقایسه:')} ${review.agent_core_state==='running'?text(s,'running','روشن'):text(s,'stopped','خاموش')}</p>
 ${review.recovery_needed?`<ul>${review.reasons.map(r=>`<li>${esc(text(s,...reasons[r]))}</li>`).join('')}</ul>`:`<p>${text(s,'No sequence conflict was found. No repair is offered; ordinary pending work is not a restore.','تعارض شماره‌ها پیدا نشد؛ تعمیر پیشنهاد نمی‌شود. کار معمولیِ در انتظار به معنی بازیابی بکاپ نیست.')}</p>`}</section>`;
 if(actions.includes('retry')||actions.includes('stop'))html+=`<section><p>${text(s,'The repair saves a conditional Stop and keeps the node disabled. If contact fails, it may still be serving until Stop is verified. Closing this window is not cancellation.','تعمیر، یک توقف شرطی ثبت می‌کند و نود را غیرفعال نگه می‌دارد. اگر ارتباط قطع شود، ممکن است تا تأیید توقف همچنان سرویس بدهد. بستن پنجره لغو عملیات نیست.')}</p><fieldset class="nr-consents"><legend>${text(s,'Required confirmations','تأییدهای لازم')}</legend>
 ${Object.entries(confirmations).map(([key,words])=>`<label><input type="checkbox" name="${key}"><span>${esc(text(s,...words))}</span></label>`).join('')}</fieldset>
 ${actions.includes('stop')?button(s,'stop','Confirm repair and Stop','تأیید تعمیر و توقف',true):''}
 ${actions.includes('retry')?button(s,'retry','Retry the saved Stop','پیگیری همان توقف ذخیره‌شده',true):''}</section>`;
 if(state.me?.writes_enabled!==true&&doc)html+=`<p>${text(s,'Read-only mode: comparison and saved status only.','حالت فقط‌خواندنی: فقط مقایسه و مشاهدهٔ وضعیت ذخیره‌شده.')}</p>`;
 s.body.innerHTML=html;s.body.setAttribute('aria-busy',String(s.busy));
 s.box.querySelectorAll('[data-nrec]').forEach(b=>{b.disabled=s.busy||!actions.includes(b.dataset.nrec)||b.hasAttribute('data-requires-consent');});
}
async function read(s){
 const doc=await api(s.url+'/current');
 if(!authorized(s))return;
 if(!validStatus(doc,s.id))throw Error('Invalid saved recovery status');
 s.doc=doc;
}
async function perform(s,action){
 if(!authorized(s)||s.busy||!allowed(s).includes(action))return;
 const checked=Object.keys(confirmations).every(key=>s.box.querySelector(`[name="${key}"]`)?.checked===true);
 if(['stop','retry'].includes(action)&&!checked)return;
 const review=s.review,attempt=s.doc?.attempt_id;
 s.busy=true;s.review=null;s.error='';paint(s);
 try{
  let result;
  if(action==='review')result=await api(s.url+'/review','POST',{});
  if(action==='stop')await api(s.url+'/stop','POST',{bindingId:review.binding_id,reviewHash:review.review_hash,acknowledgeServiceInterruption:true,acknowledgeBackupMayBeStale:true});
  if(action==='retry')await api(s.url+'/'+attempt+'/retry','POST',{});
  if(!authorized(s))return;
  await read(s);
  if(authorized(s)&&action==='review'){
   if(!validReview(result,s.id))throw Error('Invalid checkpoint review');
   s.review=result;
  }
 }catch(error){
  if(authorized(s)){s.doc=null;s.review=null;const known=Object.hasOwn(failures,error?.message)?failures[error.message]:null;s.error=(known?text(s,...known)+' ':'')+text(s,'Result not confirmed. Read saved status before another action. The request may have been applied; do not delete recovery data or assume the node stopped.','نتیجه تأیید نشده است. پیش از اقدام بعدی وضعیت ذخیره‌شده را بخوان. ممکن است درخواست اعمال شده باشد؛ اطلاعات بازیابی را حذف نکن و خاموش‌شدن نود را فرض نکن.');}
 }finally{
  s.busy=false;
  if(!authorized(s)){if(s.live)s.box.close();return;}
  paint(s);s.box.querySelector('[data-nrec-phase]')?.focus();
 }
}
async function open(id,trigger){
 if(!isOwner()||typeof id!=='string'||!id)return;
 if(active?.live)active.box.close();
 if(typeof closeDialog==='function')closeDialog();
 const box=document.createElement('dialog'),lang=language();
 box.className='nr-dialog nrec-dialog';box.dir=lang==='fa'?'rtl':'ltr';box.setAttribute('aria-labelledby','nrec-title');
 box.innerHTML=`<header><div><small>DARK XRAY</small><h2 id="nrec-title">${text({lang},'Recover node after backup','بازیابی نود بعد از بکاپ')}</h2><small><bdi>${esc(id)}</bdi></small></div><button type="button" class="btn" data-nrec-close>${text({lang},'Close','بستن')}</button></header><div class="nr-body nrec-body"></div><footer><button type="button" class="btn" data-nrec="refresh">${text({lang},'Read saved status','خواندن وضعیت ذخیره‌شده')}</button><small>${text({lang},'No automatic repair or Start','بدون تعمیر یا شروع خودکار')}</small></footer>`;
 document.body.append(box);
 const s={id,lang,owner:state.me.id,url:'/api/nodes/'+enc(id)+'/recovery',box,body:box.querySelector('.nrec-body'),doc:null,review:null,error:'',busy:false,live:true};active=s;
 box.addEventListener('close',()=>{s.live=false;s.doc=null;s.review=null;box.remove();if(active===s)active=null;if(trigger?.isConnected)trigger.focus();});
 box.querySelector('[data-nrec-close]').onclick=()=>box.close();
 box.addEventListener('click',ev=>{const b=ev.target.closest('[data-nrec]');if(b){ev.preventDefault();void perform(s,b.dataset.nrec);}});
 box.addEventListener('change',()=>{
  const checked=Object.keys(confirmations).every(key=>box.querySelector(`[name="${key}"]`)?.checked===true);
  box.querySelectorAll('[data-requires-consent]').forEach(b=>b.disabled=s.busy||!allowed(s).includes(b.dataset.nrec)||!checked);
 });
 box.addEventListener('submit',ev=>ev.preventDefault());box.showModal();paint(s);await perform(s,'refresh');
}
window.DarkNodeRecovery={open};
})();
