/* Restore Safety: additive group status and explicit review; no new main-menu item. */
(function(){
'use strict';
if(!globalThis.DarkRestoreGroups||typeof enginePage!=='function'||typeof runAction!=='function')return;
const DR=DarkRestoreGroups.state,basePage=enginePage,baseAction=runAction;
const L=(en,fa)=>(localStorage.getItem('dark_lang')||'en')==='fa'?fa:en;
const esc=v=>e(String(v??''));
function label(s){return ({eligible:L('Eligible','مجاز'),needs_review:L('Needs source review','نیازمند بررسی مبدا'),expired:L('Expired','منقضی'),exhausted:L('Volume exhausted','اتمام حجم'),suspended:L('Suspended','متوقف'),promoted:L('Promoted / native','منتقل‌شده / Native'),identity_missing:L('Identity missing','شناسهٔ هسته موجود نیست'),synced:L('Applied / acknowledged','اعمال و تأیید شده'),pending:L('Waiting for apply','در انتظار اعمال'),offline:L('Offline · not confirmed','آفلاین · تأیید نشده'),stopped:L('Core stopped','هسته متوقف'),unverified:L('Not verified','تأیید نشده')})[s]||s;}
function badge(s){return '<span class="tag '+(['eligible','synced','promoted'].includes(s)?'green':['pending','needs_review','unverified'].includes(s)?'amber':'red')+'">'+esc(label(s))+'</span>';}
function statusPanel(s){
 const counts=s.counts||{};
 return '<section class="panel drs-panel"><h3>'+L('Migration safety','ایمنی مهاجرت')+'</h3><div class="drs-counts">'+['eligible','needs_review','expired','exhausted','suspended','promoted'].map(k=>'<div><span>'+esc(label(k))+'</span><strong>'+Number(counts[k]||0)+'</strong></div>').join('')+'</div><div class="drs-delivery"><b>'+s.subscription_received+' '+L('received the new subscription','ساب جدید را دریافت کرده‌اند')+'</b><b>'+s.traffic_observed+' '+L('have recorded DARK traffic','مصرف ثبت‌شده در DARK دارند')+'</b></div><p>'+L('Receiving a subscription is not proof of a working customer connection. Recorded traffic is a separate observation.','دریافت ساب، اثبات اتصال موفق مشتری نیست. مصرف ثبت‌شده یک مشاهدهٔ جداست.')+'</p><details><summary>'+L('Runtime enforcement status','وضعیت اعمال روی سرورها')+'</summary><div class="drs-runtime"><span>HUB</span>'+badge(s.hub_state)+'</div>'+(s.nodes||[]).map(n=>'<div class="drs-runtime"><span data-no-i18n>'+esc(n.name)+'</span>'+badge(n.state)+'</div>').join('')+'<p>'+L('Limits use periodic Hub reconciliation. Offline Nodes cannot confirm new restrictions until they reconnect; quota cutoff is not byte-exact.','محدودیت‌ها با بررسی دوره‌ای هاب اعمال می‌شوند. اعمال محدودیت جدید روی نود آفلاین تا اتصال مجدد قابل تأیید نیست؛ قطع مصرف بایت‌به‌بایت نیست.')+'</p></details>'+(!s.writes_enabled?'<div class="notice warning">'+L('Runtime writes are disabled. Restrictions cannot currently be applied.','نوشتن تنظیمات هسته غیرفعال است؛ محدودیت جدید فعلاً قابل اعمال نیست.')+'</div>':'')+(s.legacy_unconfirmed?'<details class="drs-legacy"><summary>'+s.legacy_unconfirmed+' '+L('previously imported source snapshots','رکورد از اسکن‌های قبلی')+'</summary><p>'+L('Saved quota and expiry are retained. Old scans did not record which header fields were present; these are not labelled as newly verified. Review saved data when needed; do not rescan a domain that already points to DARK.','حجم و انقضای ذخیره‌شده حفظ شده‌اند. در اسکن‌های قدیمی کامل‌بودن فیلدهای هدر ثبت نشده بود؛ این‌ها تأیید تازه محسوب نمی‌شوند. در صورت نیاز اطلاعات ذخیره‌شده را بررسی کن؛ دامنه‌ای که به DARK منتقل شده را دوباره اسکن نکن.')+'</p></details>':'')+'</section>';
}
enginePage=async function(){
 const page=state.page,html=await basePage();
 if(page!=='darkrestore'||!isOwner())return html;
 let extra='';
 try{extra=statusPanel(await api('/api/dark-restore/safety'+(DR.group?'?groupId='+enc(DR.group):'')));}
 catch(ex){extra='<div class="notice warning">'+L('Safety status could not be loaded: ','وضعیت ایمنی دریافت نشد: ')+esc(ex.message)+'</div>';}
 return html.replace('<section class="panel dr-toolbar">',extra+'<section class="panel dr-toolbar">');
};
function decorate(){
 if(state.page!=='darkrestore'||!DR.data||!isOwner())return;
 const byId=new Map((DR.data.items||[]).map(x=>[x.id,x]));
 for(const card of document.querySelectorAll('.dr-user')){
  const id=card.querySelector('[data-dr-select]')?.dataset.drSelect,x=byId.get(id);if(!x)continue;
  const mark=x.id+'|'+x.metadata_revision+'|'+x.service_status+'|'+(localStorage.getItem('dark_lang')||'en');
  if(card.dataset.drsState===mark)continue;card.dataset.drsState=mark;
  card.querySelector('.drs-user-status')?.remove();card.querySelector('.drs-user-actions')?.remove();
  const unknown=x.service_status==='needs_review',status=document.createElement('div');status.className='drs-user-status';
  status.innerHTML=badge(x.service_status)+(x.metadata_state==='manual'?'<small>'+L('Source metadata confirmed by owner','اطلاعات مبدا با تأیید مالک')+'</small>':'')+(unknown?'<small>'+L('No usable config is published until all four source fields are known.','تا مشخص‌شدن چهار فیلد مبدا، کانفیگ قابل استفاده منتشر نمی‌شود.')+'</small>':'');
  card.querySelector('.dr-migration')?.prepend(status);
  const sourceTag=card.querySelector('.dr-migration>.tag');
  if(sourceTag){sourceTag.textContent=x.metadata_state==='legacy_saved'?L('Saved source snapshot','اطلاعات ذخیره‌شدهٔ مبدا'):x.metadata_state==='manual'?L('Owner reviewed','بررسی‌شده توسط مالک'):x.metadata_state==='verified'?L('Complete source scan','اسکن کامل مبدا'):L('Incomplete source data','اطلاعات ناقص مبدا');sourceTag.classList.toggle('green',['manual','verified'].includes(x.metadata_state));sourceTag.classList.toggle('amber',!['manual','verified'].includes(x.metadata_state));}
  const migration=card.querySelectorAll('.dr-migration>small');
  if(migration.length>0)migration[0].textContent=L('Expiry: ','انقضا: ')+(unknown?L('Unknown · review required','نامشخص · نیازمند بررسی'):x.legacy_expire?new Date(x.legacy_expire*1000).toLocaleString():L('No expiry','بدون انقضا'));
  if(migration.length>1)migration[1].textContent=L('New subscription received: ','دریافت ساب جدید: ')+(x.subscription_received?new Date(x.first_seen*1000).toLocaleString():L('Not yet','هنوز دریافت نشده'));
  const usage=card.querySelectorAll('.dr-usage>small');
  if(usage.length>1)usage[1].textContent=L('Remaining: ','باقی‌مانده: ')+(unknown?L('Unknown · review required','نامشخص · نیازمند بررسی'):x.legacy_total?bytes(x.remaining||0):L('Unlimited','نامحدود'));
  if(x.service_status!=='promoted'){
   const actions=document.createElement('div');actions.className='drs-user-actions';
   actions.innerHTML='<button type="button" class="btn mini" data-act="drsreview" data-id="'+esc(id)+'">'+L('Review source data','بررسی اطلاعات مبدا')+'</button><button type="button" class="btn mini" data-act="drssuspend" data-id="'+esc(id)+'">'+(x.service_status==='suspended'?L('Resume','رفع توقف'):L('Suspend','توقف'))+'</button>';
   card.querySelector('.dr-actions')?.append(actions);
  }
 }
 for(const small of document.querySelectorAll('.dr-group small')){
  const original=small.textContent,revised=original.replace(' migrated',' received subscription').replace('منتقل‌شده','ساب دریافت کرده');
  if(revised!==original)small.textContent=revised;
 }
}
let queued=false;
new MutationObserver(()=>{if(queued||state.page!=='darkrestore')return;queued=true;queueMicrotask(()=>{queued=false;decorate();});}).observe(document.getElementById('app'),{childList:true,subtree:true});
async function fresh(id){const data=await api('/api/dark-restore');DR.data=data;const x=(data.items||[]).find(x=>x.id===id);if(!x)throw Error(L('Restore user not found.','کاربر ریستور پیدا نشد.'));return x;}
function dateInput(seconds){if(!seconds)return '';const d=new Date(seconds*1000),p=n=>String(n).padStart(2,'0');return d.getFullYear()+'-'+p(d.getMonth()+1)+'-'+p(d.getDate())+'T'+p(d.getHours())+':'+p(d.getMinutes())+':'+p(d.getSeconds());}
function integer(value){const s=String(value??'').trim();if(!/^[0-9]+$/.test(s))throw Error(L('Enter an explicit non-negative whole number.','یک عدد صحیح نامنفی را صریحاً وارد کن.'));const n=Number(s);if(!Number.isSafeInteger(n))throw Error(L('Value exceeds the exact browser integer range.','عدد از محدودهٔ دقیق مرورگر بزرگ‌تر است.'));return n;}
async function finish(out){closeDialog();DR.data=null;await renderPage();if(out.applied===false)dialog(L('Saved; enforcement pending','ذخیره شد؛ اعمال محدودیت در انتظار است'),'<div class="notice warning">'+L('Runtime apply failed. The saved decision will be retried by reconciliation.','اعمال روی هسته موفق نشد؛ تصمیم ذخیره شده و در بررسی دوره‌ای دوباره تلاش می‌شود.')+'</div>');else toast(L('Saved. Runtime status shows Node propagation separately.','ذخیره شد؛ وضعیت اعمال روی نودها جداگانه نمایش داده می‌شود.'));}
async function review(id){
 const x=await fresh(id),unknown=x.service_status==='needs_review';
 const names={upload:L('Previous upload · bytes','آپلود در مبدا · بایت'),download:L('Previous download · bytes','دانلود در مبدا · بایت'),total:L('Original quota · bytes; 0 = unlimited','سهمیهٔ کل مبدا · بایت؛ صفر = نامحدود')};
 const fields=['upload','download','total'].map(k=>'<label>'+names[k]+'<input class="field-input" inputmode="numeric" name="'+k+'" pattern="[0-9]+" required value="'+(unknown?'':esc(x['legacy_'+k]))+'"><small>'+L('Saved value: ','مقدار ذخیره‌شده: ')+bytes(x['legacy_'+k]||0)+'</small></label>').join('');
 dialog(L('Review original source metadata','بررسی اطلاعات پنل مبدا'),'<div class="drs-review"><div class="notice warning">'+L('These values belong to the OLD panel. DARK usage remains unchanged. Editing the original quota or expiry can change access for this user on every deployed runtime.','این اعداد متعلق به پنل قبلی هستند. مصرف DARK تغییری نمی‌کند. ویرایش سهمیه یا انقضای مبدا ممکن است دسترسی همین کاربر را روی همهٔ سرورها تغییر دهد.')+'</div>'+fields+'<label>'+L('Original expiry · local time','انقضای مبدا · ساعت محلی')+'<input class="field-input" type="datetime-local" step="1" name="expiresAt" value="'+(unknown?'':dateInput(x.legacy_expire))+'" '+(!unknown&&!x.legacy_expire?'disabled':'required')+'></label><label class="tg-switch"><input type="checkbox" name="noExpiry" data-drs-no-expiry '+(!unknown&&!x.legacy_expire?'checked':'')+'><span>'+L('The source explicitly has no expiry','در مبدا صریحاً بدون انقضا است')+'</span></label><label>'+L('Review note','یادداشت بررسی')+'<input class="field-input" name="note" required minlength="5" maxlength="240"></label><label class="tg-switch"><input type="checkbox" name="confirmed" required><span>'+L('I verified all values against the original source, not DARK consumption.','همهٔ اعداد را با اطلاعات مبدا بررسی کردم؛ این اعداد مصرف DARK نیستند.')+'</span></label></div>',async fd=>{
  const expire=fd.has('noExpiry')?0:Math.floor(new Date(String(fd.get('expiresAt')||'')).getTime()/1000);
  if(!Number.isSafeInteger(expire)||expire<0)throw Error(L('Enter the source expiry or explicitly select no expiry.','انقضای مبدا را وارد کن یا بدون انقضا را صریحاً انتخاب کن.'));
  const payload={upload:integer(fd.get('upload')),download:integer(fd.get('download')),total:integer(fd.get('total')),expire,note:String(fd.get('note')||'').trim(),confirmed:fd.has('confirmed'),expectedRevision:x.metadata_revision};
  await finish(await api('/api/dark-restore/'+enc(id)+'/metadata','PUT',payload));
 },L('Confirm source metadata','تأیید اطلاعات مبدا'));
}
async function suspension(id){
 const x=await fresh(id),suspended=x.service_status!=='suspended';
 dialog(suspended?L('Suspend Restore user','توقف کاربر ریستور'):L('Resume Restore user','رفع توقف کاربر ریستور'),'<div class="notice warning">'+L('This changes only the manual suspension. Expired, exhausted or incomplete subscriptions stay blocked. UUIDs, links and recorded usage are retained.','این عملیات فقط توقف دستی را تغییر می‌دهد. کاربر منقضی، بدون حجم یا با اطلاعات ناقص همچنان مسدود می‌ماند. شناسه، لینک و مصرف ثبت‌شده حفظ می‌شوند.')+'</div><label class="tg-switch"><input type="checkbox" name="confirmed" required><span>'+L('I confirm this access change','تغییر دسترسی را تأیید می‌کنم')+'</span></label>',async()=>finish(await api('/api/dark-restore/'+enc(id)+'/suspension','PUT',{suspended,expectedRevision:x.metadata_revision})));
}
document.addEventListener('change',ev=>{if(ev.target.matches('[data-drs-no-expiry]')){const input=ev.target.closest('form').querySelector('[name="expiresAt"]');input.disabled=ev.target.checked;input.required=!ev.target.checked;}});
runAction=async function(act,el){if(act==='drsreview'||act==='drssuspend'){if(!isOwner())throw Error('Owner access required');return act==='drsreview'?review(el.dataset.id):suspension(el.dataset.id);}return baseAction(act,el);};
globalThis.DarkRestoreSafety={label,integer,dateInput,statusPanel,ready:true};
})();
