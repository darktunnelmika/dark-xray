/* DARK XRAY replacement workspace. Every remote mutation is an explicit click.
 * No Pair Code, token, consent, or review hash is stored in browser storage.
 */
(function(){
'use strict';
if(typeof enginePage!=='function'||typeof runAction!=='function')return;
const pageBase=enginePage,actionBase=runAction;
const L=(en,fa)=>(localStorage.getItem('dark_lang')||'en')==='fa'?fa:en;
const idOK=v=>typeof v==='string'&&/^[0-9a-f]{32}$/.test(v);
const hashOK=v=>typeof v==='string'&&/^[0-9a-f]{64}$/.test(v);
const esc=v=>e(String(v??'—'));
let active=null;
const names={
 none:['New replacement','جایگزینی جدید'],pending:['Preparation pending','آماده‌سازی در انتظار'],
 rotating:['Credential handoff pending','تعویض توکن در انتظار'],prepared:['Candidate prepared','سرور جایگزین آماده است'],
 committing:['Binding confirmation pending','ثبت جایگزینی در انتظار'],cancelling:['Disposal pending','لغو و کنارگذاری در انتظار'],
 committed:['Binding saved; service not activated','جایگزینی ثبت شد؛ سرویس هنوز فعال نیست'],
 staging:['Configuration not yet confirmed','استقرار تنظیمات هنوز تأیید نشده'],staged:['Configuration staged while stopped','تنظیمات در حالت خاموش مستقر شد'],
 starting:['Start pending; target may already be running','شروع در انتظار؛ هدف ممکن است روشن شده باشد'],
 stopping:['Stop pending; target may still be running','توقف در انتظار؛ هدف ممکن است هنوز روشن باشد'],
 paused:['Stop confirmed; review again before starting','توقف تأیید شد؛ پیش از شروع بازبینی کن'],
 activated:['Replacement completed','جایگزینی تکمیل شد'],cancelled:['Candidate discarded; reinstall or reset before reuse','هدف کنار گذاشته شد؛ استفاده مجدد نیاز به نصب یا بازنشانی دارد']
};
const consent={
 acceptUnconfirmedOldServer:['I understand the old VPS has not been confirmed stopped. I must retire it separately.','می‌دانم توقف VPS قبلی تأیید نشده و باید آن را جداگانه بازنشسته کنم.'],
 acceptUnreportedTraffic:['I accept that traffic never reported by the old server cannot be reconstructed. Recorded usage is preserved.','می‌پذیرم مصرف گزارش‌نشدهٔ سرور قبلی قابل بازسازی نیست؛ مصرف ثبت‌شده حفظ می‌شود.'],
 confirmStart:['Start the replacement Xray. Direct clients may connect immediately, before Hub publication finishes.','Xray جایگزین شروع شود؛ مشتری مستقیم ممکن است همان لحظه، قبل از ثبت نهایی در پنل، وصل شود.'],
 acceptEndpointResponsibility:['I reviewed the Direct address. After verified Start, Hub subscription hosts for this Node switch to Direct; external DNS and tunnels are not changed.','آدرس مستقیم را بررسی کردم. پس از فعال‌سازی تأییدشده، هاست‌های اشتراک این نود به مستقیم تبدیل می‌شوند؛ DNS و تانل‌های بیرونی تغییر نمی‌کنند.'],
 discardCandidate:['Discard this unused candidate and revoke its pairing credentials. Reuse requires reinstall or authorized local token reset.','این هدفِ استفاده‌نشده کنار گذاشته و اعتبار اتصال آن باطل شود؛ استفاده مجدد نیاز به نصب یا بازنشانی مجاز توکن روی خودش دارد.'],
 confirmStop:['Stop this pending activation. Connections on the replacement may disconnect; the old VPS is not changed.','این فعال‌سازی در انتظار متوقف شود؛ اتصال‌های هدف ممکن است قطع شوند؛ VPS قبلی تغییر نمی‌کند.']
};
function opener(id){return `<button type="button" class="btn" data-act="nr-open" data-id="${esc(id)}">${L('Replace server','جایگزینی سرور')}</button>`;}
enginePage=async function(){
 const html=await pageBase();
 if(state.page!=='nodes'||!isOwner())return html;
 const template=document.createElement('template');template.innerHTML=html;
 template.content.querySelectorAll('.nv2-actions').forEach(box=>{
  const manage=box.querySelector('[data-act="nv2edit"]');
  if(manage)box.insertAdjacentHTML('beforeend',opener(manage.dataset.id));
 });
 return template.innerHTML;
};
runAction=async function(act,el){
 if(act==='nr-open'){await open(el.dataset.id,el);return;}
 const result=await actionBase(act,el);
 if(act==='nv2edit'&&isOwner()){
  const box=document.querySelector('.nv5-manage-actions');
  if(box&&!box.querySelector('[data-act="nr-open"]'))box.insertAdjacentHTML('beforeend',opener(el.dataset.id));
 }
 return result;
};
function phase(s){
 const a=s.doc?.attempt;if(!a)return 'none';
 if(s.activation)return s.activation.phase;
 if(s.deployment)return s.deployment.configuration_staged===true?'staged':'staging';
 return a.phase;
}
function checks(keys){return `<fieldset class="nr-consents"><legend>${L('Explicit confirmations','تأییدهای صریح')}</legend>${keys.map(k=>`<label><input type="checkbox" name="${k}"><span>${esc(L(...consent[k]))}</span></label>`).join('')}</fieldset>`;}
function buttonFor(action,en,fa,keys=[]){return `<button type="button" class="btn btn-primary" data-nr="${action}" data-consents="${keys.join(',')}" ${keys.length?'disabled':''}>${L(en,fa)}</button>`;}
const startKeys=['confirmStart','acceptEndpointResponsibility','acceptUnconfirmedOldServer','acceptUnreportedTraffic'];
const commitKeys=['acceptUnconfirmedOldServer','acceptUnreportedTraffic'];
function allowed(s){
 const a=s.doc?.attempt,p=phase(s),out=['refresh'];
 if(!s.doc||state.me?.writes_enabled!==true)return out;
 if(!a||p==='cancelled')return out.concat('prepare');
 if(['pending','rotating'].includes(p)&&a.source_current===true)out.push('retry');
 if(a.prepared===true||p==='committing')out.push('commit');
 if(['pending','rotating','prepared','cancelling'].includes(p))out.push('cancel');
 if(a.phase==='committed'&&a.binding_current===true){
  if(!s.activation||p==='paused')out.push('stage','review');
  if(s.review?.review_ready===true||p==='starting'&&hashOK(s.doc.activation_resume?.review_hash))out.push('start');
  if(['starting','stopping'].includes(p))out.push('pause');
  if(s.activation?.activation_completed===true)out.push('prepare');
 }
 return out;
}
function endpoints(plan){
 if(!plan)return '';
 const hosts=Array.isArray(plan.hosts)?plan.hosts:[],ibs=Array.isArray(plan.inbounds)?plan.inbounds:[];
 const changes=Array.isArray(plan.planned_direct_host_changes)?plan.planned_direct_host_changes:[];
 const actionLabel={tunnel_to_direct:L('Old tunnel host → Direct','هاست تانل قدیمی ← مستقیم'),
  add_direct:L('Create new Direct host','ساخت هاست مستقیم جدید'),
  disable_retired_tunnel:L('Disable old tunnel host','غیرفعال کردن هاست تانل قبلی'),
  clear_retired_tunnel_port:L('Clear obsolete tunnel port','پاک‌سازی پورت تانل قبلی'),
  old_direct_ip_to_new_direct:L('Replace retired Direct IP','جایگزینی آی‌پی مستقیم قبلی')};
 return `<section class="nr-plan"><h3>${L('New Node · Direct only','نود جدید · فقط مستقیم')}</h3>
 <p><strong>${L('New address','آدرس جدید')}:</strong> <bdi>${esc(plan.node_data_address)}</bdi></p>
 <div class="nr-table"><table><thead><tr><th>${L('Inbound','اینباند')}</th><th>${L('Protocol','پروتکل')}</th><th>${L('Direct port','پورت مستقیم')}</th></tr></thead><tbody>${ibs.map(i=>`<tr><td>${esc(i.inbound_id)}</td><td>${esc(i.protocol)}</td><td>${esc(i.port)}</td></tr>`).join('')}</tbody></table></div>
 <h4>${L('Automatic Host changes after verified Start','تغییر خودکار هاست پس از فعال‌سازی تأییدشده')}</h4>
 ${changes.length?`<ul>${changes.map(x=>`<li>${esc(actionLabel[x.action]||x.action)} · <bdi>${esc(x.address)}:${esc(x.port)}</bdi></li>`).join('')}</ul>`:`<p>${L('Current Direct hosts will be preserved.','هاست‌های مستقیم فعلی حفظ می‌شوند.')}</p>`}
 <details><summary>${L('Previous hosts belonging to this Node','هاست‌های قدیمی همین نود')}</summary>${hosts.length?`<ul>${hosts.map(h=>`<li><bdi>${esc(h.address)}:${esc(h.port)}</bdi> · ${esc(h.remark)} · ${esc(h.endpointType||'direct')}</li>`).join('')}</ul>`:`<p>${L('None','موردی نیست')}</p>`}</details>
 <p>${L('Old tunnel endpoints will not be reused. Add a new tunnel later in Inbound settings. External DNS and tunnels stay unchanged.','هاست‌های تانل قدیمی دوباره استفاده نمی‌شوند. تانل جدید را بعداً در تنظیمات اینباند اضافه کن. DNS و خود تانل‌های بیرونی تغییر نمی‌کنند.')}</p>
 <p>${L('This is an address review, not a WAN/DNS test.','این بازبینی آدرس است؛ نه تست اتصال یا DNS.')}</p></section>`;
}

function paint(s){
 if(!s.live)return;
 const a=s.doc?.attempt,p=phase(s),actions=allowed(s),pair=names[p]||['Unrecognized state; refresh only','وضعیت ناشناخته؛ فقط تازه‌سازی'];
 const step=p==='none'?0:['pending','rotating','prepared','cancelling'].includes(p)?1:['committing','committed'].includes(p)?2:['staging','staged'].includes(p)?3:p==='activated'?5:4;
 const labels=[['Select target','انتخاب هدف'],['Prepare','آماده‌سازی'],['Save binding','ثبت جایگزینی'],['Stage stopped','استقرار خاموش'],['Review / Start','بازبینی و شروع'],['Receipt','رسید']];
 const completed=p==='activated'&&s.activation?.activation_completed===true;
 let body=completed?
 `<section class="nr-new-primary" data-nr-phase="${esc(p)}"><h3>${L('Replace this Node with a new server','جایگزینی این نود با سرور جدید')}</h3>
 <p>${L('Enter the Pair Code from a different freshly installed VPS to start a new replacement. The existing Node remains active until the new replacement is explicitly reviewed and confirmed.','برای شروع جایگزینی جدید، کد اتصال یک سرور تازه‌نصب‌شدهٔ دیگر را وارد کن. نود فعلی تا زمانی که جایگزینی جدید را بررسی و تأیید نکنی فعال می‌ماند.')}</p></section>`:
 `<ol class="nr-steps">${labels.map((x,i)=>`<li ${i===step?'aria-current="step"':''}>${i+1}. ${L(...x)}</li>`).join('')}</ol>
 <div class="notice warning">${L('Users and recorded usage stay on the Hub. Closing this window does not cancel, stop, or roll back an operation. DNS, tunnels and the old VPS are not changed here.','کاربران و مصرف ثبت‌شده در پنل می‌مانند. بستن این پنجره عملیات را لغو، متوقف یا برنمی‌گرداند. DNS، تانل و VPS قبلی اینجا تغییر نمی‌کنند.')}</div>
 <p role="status" aria-live="polite" data-nr-phase="${esc(p)}"><strong>${s.doc?esc(L(...pair)):L('Status unavailable','وضعیت در دسترس نیست')}</strong></p>`;
 if(a&&!completed)body+=`<dl class="nr-meta"><dt>${L('Attempt','عملیات')}</dt><dd><bdi>${esc(a.attempt_id)}</bdi></dd><dt>${L('Candidate HTTPS','آدرس مدیریتی هدف')}</dt><dd><bdi>${esc(a.target_origin)}</bdi></dd><dt>${L('Customer address','آدرس مشتری')}</dt><dd><bdi>${esc(a.data_address)}</bdi></dd></dl>`;
 const failure=s.activation?.last_error||s.deployment?.last_error||a?.last_error;
 if(failure)body+=`<p class="notice warning">${L('Not confirmed. Diagnostic code:','تأیید نشده. کد تشخیصی:')} <bdi>${esc(failure)}</bdi></p>`;
 if(s.error)body+=`<p class="notice warning" role="alert">${esc(s.error)}</p>`;
 if(a&&(a.source_current===false||a.phase==='committed'&&a.binding_current!==true))body+=`<p class="notice warning">${L('The installation changed. Old confirmations cannot be reused.','هویت نصب تغییر کرده؛ تأییدهای قدیمی قابل استفاده نیستند.')}</p>`;
 if(actions.includes('prepare'))body+=`<section class="nr-prepare"><h3>${L('Prepare new server','آماده‌سازی سرور جدید')}</h3>
 <p>${L('Run install-node.sh on the NEW VPS, then paste its fresh Pair Code. Do not re-use an old consumed Pair Code or add it as a separate Node first.','روی سرور جدید install-node.sh را اجرا کن و کد اتصال تازهٔ آن را وارد کن. کد مصرف‌شدهٔ قبلی را دوباره استفاده نکن و سرور را اول به‌صورت نود جداگانه اضافه نکن.')}</p>
 <label for="nr-code">${L('New Pair Code (sensitive)','کد اتصال جدید (محرمانه)')}</label>
 <textarea id="nr-code" dir="ltr" autocomplete="off" spellcheck="false" maxlength="4096" rows="3" placeholder="DXN1.…"></textarea>
 ${buttonFor('prepare',completed?'Begin new replacement':'Prepare candidate',completed?'شروع جایگزینی جدید':'آماده‌سازی هدف')}</section>`;
 if(completed)body+=`<details class="nr-previous-receipt"><summary>${L('Previous replacement receipt (history)','رسید جایگزینی قبلی (سابقه)')}</summary>
 <section class="nr-completed">
 <h3>${L('Previous replacement was completed','جایگزینی قبلی تکمیل شده')}</h3>
 <p>${L('Attempt','عملیات')}: <bdi>${esc(a.attempt_id)}</bdi></p>
 <p>${L('Previous destination','مقصد قبلی')}: <bdi>${esc(a.target_origin)}</bdi></p>
 <p>${s.activation.service_activated===true?
 L('Last saved Node observation was running (not a WAN test).','آخرین وضعیت ثبت‌شدهٔ نود فعال بوده (تست اینترنت نیست).'):
 L('Last saved readiness was not confirmed; check Nodes for current health.','سلامت لحظه‌ای در آخرین گزارش تأیید نشده؛ وضعیت فعلی را در Nodes ببین.')}</p>
 <p>${L('The completed attempt is historical; use the new Pair Code field above to replace again.','این عملیات قبلی صرفاً سابقه است؛ برای جایگزینی دوباره از کد جدید در بالا استفاده کن.')}</p>
 </section></details>`;
 if(actions.includes('retry'))body+=buttonFor('retry','Retry the saved preparation','ادامهٔ آماده‌سازی ذخیره‌شده');
 if(actions.includes('commit'))body+=`<section><h3>${L('Save the replacement binding','ثبت سرور جایگزین')}</h3><p>${L('This retires the old binding but leaves the replacement disabled. It is not customer service activation.','ارتباط مدیریتی قبلی بازنشسته می‌شود ولی جایگزین غیرفعال می‌ماند؛ این فعال‌سازی سرویس مشتری نیست.')}</p>${checks(commitKeys)}${buttonFor('commit','Confirm replacement binding','تأیید ثبت جایگزینی',commitKeys)}</section>`;
 if(actions.includes('stage'))body+=`<section>${buttonFor('review','Check Direct and continue','بررسی مستقیم و ادامه')} ${buttonFor('stage','Recheck stopped setup','بررسی دوبارهٔ تنظیمات خاموش')}</section>`;
 body+=endpoints(s.review?.endpoints);
 if(actions.includes('start'))body+=`<section>${p==='starting'?`<p>${L('Continue the existing saved Start; do not create a new activation. The target may already be serving direct clients.','همان Start ذخیره‌شده پیگیری می‌شود؛ فعال‌سازی جدید نساز. هدف ممکن است همین حالا به مشتری مستقیم سرویس بدهد.')}</p>`:''}${checks(startKeys)}${buttonFor('start',p==='starting'?'Retry the same Start':'Confirm Start',p==='starting'?'پیگیری همان فرمان شروع':'تأیید شروع',startKeys)}</section>`;
 if(actions.includes('pause'))body+=`<section>${checks(['confirmStop'])}${buttonFor('pause',p==='stopping'?'Retry saved Stop':'Stop pending activation',p==='stopping'?'پیگیری توقف ذخیره‌شده':'توقف فعال‌سازی در انتظار',['confirmStop'])}</section>`;
 if(actions.includes('cancel'))body+=`<details><summary>${L('Discard unused candidate','کنارگذاشتن هدف استفاده‌نشده')}</summary>${checks(['discardCandidate'])}${buttonFor('cancel','Confirm candidate disposal','تأیید کنارگذاشتن هدف',['discardCandidate'])}</details>`;
 if(state.me?.writes_enabled!==true)body+=`<p class="notice">${L('Read-only mode. All mutation actions are disabled.','حالت فقط خواندنی؛ همهٔ عملیات تغییردهنده غیرفعال‌اند.')}</p>`;
 s.body.innerHTML=body;
 s.box.querySelectorAll('[data-nr]').forEach(b=>b.disabled=s.busy||!allowed(s).includes(b.dataset.nr)||(b.dataset.consents||'').split(',').filter(Boolean).some(k=>!s.box.querySelector(`[name="${k}"]`)?.checked));
 s.body.setAttribute('aria-busy',String(s.busy));
}
function authorized(s){return s.live&&state.me?.role==='owner'&&state.me.id===s.owner;}
async function read(s){
 const doc=await api(s.url+'/current');
 if(!authorized(s))return;
 if(!doc||doc.node_id!==s.id||!Object.hasOwn(doc,'attempt')||doc.attempt&&(!idOK(doc.attempt.attempt_id)||doc.attempt.node_id!==s.id))throw Error('Invalid status');
 let deployment=null,activation=null;
 if(doc.attempt?.phase==='committed'){
  const path=s.url+'/'+doc.attempt.attempt_id;
  if(doc.has_deployment===true)deployment=await api(path+'/deployment');
  if(doc.has_activation===true)activation=await api(path+'/activation');
  for(const r of [deployment,activation])if(r&&(r.node_id!==s.id||r.attempt_id!==doc.attempt.attempt_id))throw Error('Mismatched status');
 }
 if(authorized(s)){s.doc=doc;s.deployment=deployment;s.activation=activation;}
}
function ambiguous(){return L('The result is not confirmed. Refresh saved status before another action; do not assume the request failed or repeat setup.','نتیجه تأیید نیست. پیش از اقدام بعدی وضعیت ذخیره‌شده را تازه‌سازی کن؛ شکست عملیات یا نیاز به راه‌اندازی مجدد را فرض نکن.');}
async function perform(s,action){
 if(!authorized(s)||s.busy||!allowed(s).includes(action))return;
 const a=s.doc?.attempt,path=a?s.url+'/'+a.attempt_id:s.url;
 let suffix=action,body={},post=true;
 const checked=k=>s.box.querySelector(`[name="${k}"]`)?.checked===true;
 if(action==='refresh'){post=false;}
 else if(action==='prepare'){
  const code=s.box.querySelector('#nr-code')?.value.trim()||'';
  if(!/^DXN1\.[A-Za-z0-9_-]{1,3800}$/.test(code)){s.error=L('Enter a valid DXN1 Pair Code.','یک کد اتصال معتبر DXN1 وارد کن.');paint(s);return;}
  suffix='prepare';body={code};s.box.querySelector('#nr-code').value='';
 }else if(action==='commit'){
  if(!commitKeys.every(checked))return;
  body={sourceBindingId:a.source_binding_id,...Object.fromEntries(commitKeys.map(k=>[k,checked(k)]))};
 }else if(action==='cancel'){
  if(!checked('discardCandidate'))return;body={discardCandidate:true};
 }else if(action==='stage'||action==='review'){
  body={bindingId:a.committed_binding_id};suffix=action==='review'?'activation/review':'stage';
 }else if(action==='start'){
  if(!startKeys.every(checked))return;
  const hash=s.review?.review_hash||s.doc.activation_resume?.review_hash;
  if(!hashOK(hash))return;
  body={bindingId:a.committed_binding_id,reviewHash:hash,...Object.fromEntries(startKeys.map(k=>[k,checked(k)]))};suffix='activation/start';
 }else if(action==='pause'){
  if(!checked('confirmStop'))return;body={bindingId:a.committed_binding_id,confirmStop:true};suffix='activation/pause';
 }
 s.busy=true;s.error='';s.review=null;paint(s);
 try{
  const result=post?await api((action==='prepare'?s.url:path)+'/'+suffix,'POST',body):null;
  body=null;
  await read(s);
  if(authorized(s)&&action==='review'&&result?.review_ready===true&&idOK(result.binding_id)&&hashOK(result.review_hash)&&result.attempt_id===s.doc?.attempt?.attempt_id&&result.binding_id===s.doc.attempt.committed_binding_id)s.review=result;
 }catch(_error){
  if(authorized(s)){s.doc=null;s.deployment=null;s.activation=null;s.error=ambiguous();}
 }finally{
  s.busy=false;
  if(!authorized(s)){if(s.live)s.box.close();return;}
  paint(s);s.box.querySelector('[data-nr-phase]')?.setAttribute('tabindex','-1');s.box.querySelector('[data-nr-phase]')?.focus();
 }
}
async function open(id,trigger){
 if(!isOwner())return;
 if(active?.live)active.box.close();
 if(typeof closeDialog==='function')closeDialog();
 const box=document.createElement('dialog');box.className='nr-dialog';box.setAttribute('aria-labelledby','nr-title');box.dir=(localStorage.getItem('dark_lang')||'en')==='fa'?'rtl':'ltr';
 box.innerHTML=`<header><div><small>DARK XRAY</small><h2 id="nr-title">${L('Replace node server','جایگزینی سرور نود')} · <bdi>${esc(id)}</bdi></h2></div><button type="button" class="btn" data-nr-close>${L('Close','بستن')}</button></header><div class="nr-body"></div><footer><button type="button" class="btn" data-nr="refresh">${L('Refresh saved status','تازه‌سازی وضعیت ذخیره‌شده')}</button><small>${L('No automatic remote actions','بدون عملیات خودکار روی سرور')}</small></footer>`;
 document.body.append(box);
 const s={id,owner:state.me.id,url:'/api/nodes/'+enc(id)+'/replacement',box,body:box.querySelector('.nr-body'),live:true,busy:false,doc:null,review:null,deployment:null,activation:null,error:''};active=s;
 box.addEventListener('close',()=>{s.live=false;s.review=null;s.doc=null;box.remove();if(trigger?.isConnected)trigger.focus();if(active===s)active=null;});
 box.querySelector('[data-nr-close]').onclick=()=>box.close();
 box.addEventListener('click',ev=>{const b=ev.target.closest('[data-nr]');if(b){ev.preventDefault();void perform(s,b.dataset.nr);}});
 box.addEventListener('change',()=>{box.querySelectorAll('[data-consents]').forEach(b=>b.disabled=s.busy||!allowed(s).includes(b.dataset.nr)||(b.dataset.consents||'').split(',').filter(Boolean).some(k=>!box.querySelector(`[name="${k}"]`)?.checked));});
 box.addEventListener('submit',ev=>ev.preventDefault());
 box.showModal();paint(s);await perform(s,'refresh');
}
window.DarkNodeReplacement={open};
})();
