/* DARK XRAY — grouped DARK RESTORE; legacy quota is not DARK usage. */
(function(){
'use strict';
if(typeof navItems!=='function'||typeof enginePage!=='function'||typeof runAction!=='function')return;
const baseNavItems=navItems,baseEnginePage=enginePage,baseRunAction=runAction;
const DR={data:null,nodes:[],group:'',query:'',plan:'all',presence:'all',lifecycle:'active',page:0,selected:new Set()},pageSize=50;
const L=(en,fa)=>(localStorage.getItem('dark_lang')||'en')==='fa'?fa:en;
const esc=v=>e(String(v??''));
const stamp=v=>v?new Date(Number(v)*1000).toLocaleString((localStorage.getItem('dark_lang')||'en')==='fa'?'fa-IR':'en-US'):'—';
const age=v=>v==null?'—':Number(v)<60?Number(v)+'s':Number(v)<3600?Math.floor(Number(v)/60)+'m':Number(v)<86400?Math.floor(Number(v)/3600)+'h':Math.floor(Number(v)/86400)+'d';
const groupTitle=g=>g?.unassigned||g?.id==='grp_ungrouped'?L('Ungrouped · previous imports','بدون گروه · ساب‌های قبلی'):String(g?.name||'');
enginePages.darkrestore=['DARK RESTORE'];
navItems=function(){const n=baseNavItems();if(!isOwner()||n.some(x=>x[0]==='darkrestore'))return n;const i=Math.max(0,n.findIndex(x=>x[0]==='account'));n.splice(i,0,['darkrestore','DARK RESTORE','refresh']);return n;};
async function load(){
 const [data,nodes]=await Promise.all([api('/api/dark-restore'),api('/api/nodes').catch(()=>[])]);
 DR.data=data;DR.nodes=nodes?.items||nodes||[];
 const ids=new Set((data.items||[]).map(x=>x.id));DR.selected=new Set([...DR.selected].filter(x=>ids.has(x)));
 return data;
}
function tag(s){const cls=['verified','ok','ready'].includes(s)?'green':['partial','pending','unchecked'].includes(s)?'amber':'red';return '<span class="tag '+cls+'">'+esc(String(s||'unknown').toUpperCase())+'</span>';}
function inboundPicker(selected=[]){const set=new Set(selected.map(Number));return (state.inbounds||[]).map(x=>'<label class="tg-switch"><input type="checkbox" name="drInbound" value="'+x.id+'" '+(set.has(Number(x.id))?'checked':'')+'><span>'+esc(x.id+' · '+(x.remark||x.tag||'Inbound')+' · :'+x.port)+'</span></label>').join('')||'<div class="notice">'+L('Create an Inbound first.','ابتدا اینباند بساز.')+'</div>';}
function nodePicker(selected=[]){const set=new Set(selected.map(String));return (DR.nodes||[]).map(x=>'<label class="tg-switch"><input type="checkbox" name="drNode" value="'+esc(x.id)+'" '+(set.has(String(x.id))?'checked':'')+'><span>'+esc((x.name||x.id)+' · '+(x.online?'ONLINE':'OFFLINE'))+'</span></label>').join('')||'<div class="notice">'+L('No Nodes registered. Local Inbounds still work.','نودی ثبت نشده؛ اینباندهای لوکال همچنان کار می‌کنند.')+'</div>';}
function groupOptions(selected='',all=false){
 const opts=all?'<option value="">'+L('All groups','همه گروه‌ها')+'</option>':'<option value="__new__">'+L('+ Create a group','+ ایجاد گروه')+'</option>';
 return opts+(DR.data?.groups||[]).map(g=>'<option value="'+esc(g.id)+'" '+(g.id===selected?'selected':'')+'>'+esc(groupTitle(g))+'</option>').join('');
}
function groupFields(){
 const selected=DR.group&&DR.group!=='grp_ungrouped'?DR.group:'__new__';
 return '<div class="dr-group-form"><label>'+L('Representative / migration group','گروه نماینده / مهاجرت')+'<select class="field-input" name="groupChoice" data-dr-group-choice>'+groupOptions(selected)+'</select></label><label data-dr-new-group '+(selected==='__new__'?'':'hidden')+'>'+L('New group name','نام گروه جدید')+'<input class="field-input" name="groupName" maxlength="80" '+(selected==='__new__'?'required':'')+' placeholder="'+esc(L('Example: Representative Ali','مثلاً: نماینده علی'))+'"></label></div>';
}
function groupPayload(fd){const choice=String(fd.get('groupChoice')||'__new__'),name=String(fd.get('groupName')||'').trim();if(choice==='__new__'){if(!name)throw Error(L('Enter the representative or group name.','نام نماینده یا گروه را وارد کن.'));return {groupName:name};}return {groupId:choice};}
function filtered(){const q=DR.query.trim().toLocaleLowerCase();return (DR.data?.items||[]).filter(x=>
 (!DR.group||x.group_id===DR.group)&&
 (DR.plan==='all'||x.plan_type===DR.plan)&&
 (DR.presence==='all'||x.presence_state===DR.presence)&&
 (DR.lifecycle==='all'||(DR.lifecycle==='promoted'?x.promoted:!x.promoted))&&
 (!q||[x.legacy_host,x.legacy_path,x.group_name,x.id,x.core_email,x.promoted_owner].join(' ').toLocaleLowerCase().includes(q)));}
function groupCards(){
 const all=DR.data?.items||[],used=all.reduce((n,x)=>n+Number(x.dark_used||0),0);
 const tile=(id,name,count,migrated,value,g={})=>'<button type="button" class="dr-group '+(DR.group===id?'active':'')+'" data-act="drfiltergroup" data-group="'+esc(id)+'"><b>'+esc(name)+'</b><strong>'+bytes(value)+'</strong><small>'+L('Usage in DARK','مصرف در DARK')+' · '+count+' '+L('clients','کاربر')+' · '+migrated+' '+L('migrated','منتقل‌شده')+'</small><div class="dr-group-meta"><span>📦 '+Number(g.limited||0)+'</span><span>♾ '+Number(g.unlimited||0)+'</span><span>🟢 '+Number(g.online||0)+'</span><span>⇢ '+Number(g.promoted||0)+'</span></div>'+(g.target_total?'<small class="'+(g.target_pending?'amber':'green')+'">'+L('Destinations','مقصدها')+': '+Number(g.target_ready||0)+'/'+Number(g.target_total||0)+(g.target_pending?' · '+L('pending/offline','در انتظار/آفلاین'):' · '+L('ready','آماده'))+'</small>':'')+'</button>';
 const summary={limited:all.filter(x=>x.plan_type==='limited').length,unlimited:all.filter(x=>x.plan_type==='unlimited').length,online:all.filter(x=>x.presence_state==='online').length,promoted:all.filter(x=>x.promoted).length};
 return '<div class="dr-groups">'+tile('',L('All groups','همه گروه‌ها'),all.length,all.filter(x=>x.first_seen>0).length,used,summary)+(DR.data?.groups||[]).filter(g=>!g.unassigned||g.clients>0).map(g=>tile(g.id,groupTitle(g),g.clients,g.migrated,g.dark_used,g)).join('')+'</div>';
}
function presenceSignal(x){
 const p=['online','idle','offline'].includes(x.presence_state)?x.presence_state:'offline';
 const names={online:L('ONLINE','آنلاین'),idle:L('IDLE','کم‌فعال'),offline:L('OFFLINE','آفلاین')};
 return '<div class="dr-live '+p+'"><span class="dr-orb"></span><b>'+names[p]+'</b><small>'+age(x.presence_age_seconds)+'</small></div>';
}
function users(rows){
 if(!rows.length)return '<div class="notice">'+L('No Restore users match this group or search.','کاربری در این گروه یا جست‌وجو پیدا نشد.')+'</div>';
 return '<div class="dr-client-list"><div class="dr-list-head"><span></span><span>'+L('CLIENT','کاربر')+'</span><span>'+L('STATUS','وضعیت')+'</span><span>'+L('PLAN','پلن')+'</span><span>'+L('DARK USAGE','مصرف DARK')+'</span><span>'+L('EXPIRY','انقضا')+'</span><span></span></div>'+rows.map(x=>{
  const name=x.group_id==='grp_ungrouped'?L('Ungrouped','بدون گروه'):x.group_name;
  const limited=x.plan_type!=='unlimited',remaining=limited?bytes(x.remaining||0):L('Unlimited','نامحدود');
  const pct=limited&&Number(x.legacy_total)>0?Math.max(0,Math.min(100,Math.round(Number(x.effective_used||0)/Number(x.legacy_total)*100))):0;
  const expiry=x.legacy_expire?stamp(x.legacy_expire):L('No expiry','بدون انقضا');
  const plan=x.plan_type==='unlimited'?'<span class="dr-plan unlimited">♾ '+L('Unlimited','نامحدود')+'</span>':'<span class="dr-plan limited">📦 '+L('Volume','حجمی')+'</span>';
  const native=x.promoted?'<span class="dr-native">'+L('NATIVE','Native')+' → '+esc(x.promoted_owner)+'</span>':'';
  return '<article class="dr-user '+(x.promoted?'promoted':'')+'"><label class="dr-check"><input type="checkbox" data-dr-select="'+esc(x.id)+'" '+(DR.selected.has(x.id)?'checked':'')+' '+(x.promoted?'disabled':'')+'><span></span></label><button type="button" class="dr-client" data-act="drdetail" data-id="'+esc(x.id)+'"><span class="dr-avatar">'+esc((name||x.legacy_host||'R').slice(0,1).toUpperCase())+'</span><span><b>'+esc(name)+'</b><small class="mono" data-no-i18n>'+esc(x.legacy_host)+'</small><code data-no-i18n>'+esc(x.legacy_path)+'</code></span></button><div class="dr-status">'+presenceSignal(x)+(x.promoted?native:'')+'</div><div class="dr-plan-cell">'+plan+'<small>'+remaining+'</small></div><div class="dr-usage-compact"><strong data-dr-dark-usage>'+bytes(x.dark_used||0)+'</strong><small>'+L('Hub','هاب')+' '+bytes(x.local_used||0)+' · '+L('Nodes','نود')+' '+bytes(x.node_used||0)+'</small>'+(limited?'<div class="dr-meter"><i style="width:'+pct+'%"></i></div>':'')+'</div><div class="dr-expiry"><b>'+esc(expiry)+'</b><small>'+esc(String(x.scan_status||'').toUpperCase())+'</small></div><div class="dr-row-actions"><button type="button" class="btn mini" data-act="drdetail" data-id="'+esc(x.id)+'">'+L('OPEN','بازکردن')+'</button>'+(!x.promoted?'<button type="button" class="btn mini" data-act="drmap" data-id="'+esc(x.id)+'">'+L('MAP','اتصال')+'</button>':'<button type="button" class="btn mini" data-act="clients">'+L('CLIENT','کلاینت')+'</button>')+'</div></article>';
 }).join('')+'</div>';
}
function resultBody(){
 const rows=filtered(),pages=Math.max(1,Math.ceil(rows.length/pageSize));DR.page=Math.max(0,Math.min(DR.page,pages-1));
 const used=rows.reduce((n,x)=>n+Number(x.dark_used||0),0);
 return '<div class="dr-result-head"><b>'+rows.length+' '+L('clients in this filter','کاربر در این فیلتر')+'</b><span>'+L('Usage since migration','مصرف بعد از مهاجرت')+': <strong>'+bytes(used)+'</strong></span><span>'+DR.selected.size+' '+L('selected','انتخاب‌شده')+'</span></div>'+users(rows.slice(DR.page*pageSize,(DR.page+1)*pageSize))+'<div class="dr-pagination"><button type="button" class="btn" data-act="drprev" '+(DR.page===0?'disabled':'')+'>'+L('Previous','قبلی')+'</button><span>'+(DR.page+1)+' / '+pages+'</span><button type="button" class="btn" data-act="drnext" '+(DR.page+1>=pages?'disabled':'')+'>'+L('Next','بعدی')+'</button></div>';
}
function paint(){const el=document.querySelector('.dr-results');if(el)el.innerHTML=resultBody();for(const act of ['drmove','drpromote']){const b=document.querySelector('[data-act="'+act+'"]');if(b)b.disabled=!DR.selected.size;}}
function domains(rows){
 if(!rows.length)return '<div class="notice">'+L('Domains are detected after import.','دامنه‌ها بعد از وارد کردن ساب شناسایی می‌شوند.')+'</div>';
 return '<div class="reseller-list">'+rows.map(d=>'<article class="panel reseller-card"><div class="page-heading"><div><h2 class="mono">'+esc(d.domain)+'</h2><small>'+L('Point A/AAAA to this DARK XRAY server before cutover.','قبل از انتقال رکورد A/AAAA را روی سرور DARK XRAY ست کن.')+'</small></div>'+tag(d.dns_status)+'</div><div class="details"><div><span>DNS</span><b>'+esc(d.dns_status)+'</b></div><div><span>SSL</span><b>'+esc(d.ssl_status)+'</b></div><div><span>'+L('Last check','آخرین بررسی')+'</span><b>'+stamp(d.last_checked)+'</b></div></div><div class="row-actions"><button class="btn" data-act="drdomaincheck" data-domain="'+esc(d.domain)+'">'+icon('refresh')+L('Check DNS / SSL','بررسی DNS / SSL')+'</button></div></article>').join('')+'</div>';
}
async function page(){
 const d=await load(),groups=d.groups||[],group=groups.find(g=>g.id===DR.group);
 return '<div class="dark-restore-v2">'+heading('DARK RESTORE',L('Separate every representative migration; report only traffic used after moving to DARK.','مهاجرت هر نماینده جدا؛ گزارش مصرف فقط مربوط به بعد از انتقال به DARK است.'),'<button class="btn" data-act="drgroupnew">'+icon('plus')+L('New group','گروه جدید')+'</button><button class="btn btn-primary" data-act="drimport">'+icon('plus')+L('Scan / import subscriptions','اسکن / وارد کردن ساب‌ها')+'</button>')+'<div class="notice">'+L('Old usage is excluded from every DARK usage total. It is retained only to preserve the original remaining quota and subscription metadata. Restore groups stay isolated from native Clients until you explicitly promote selected users to a representative.','مصرف قبلی در هیچ‌یک از اعداد مصرف DARK جمع نمی‌شود؛ فقط برای حفظ حجم باقی‌مانده و اطلاعات ساب نگه داشته می‌شود. گروه‌های Restore تا زمانی که خودت کاربران انتخاب‌شده را به نماینده Promote نکنی، از Clients اصلی جدا می‌مانند.')+'</div>'+groupCards()+'<section class="panel dr-toolbar"><label>'+L('Group','گروه')+'<select class="field-input" data-dr-filter-group>'+groupOptions(DR.group,true)+'</select></label><label>'+L('Search imported subscriptions','جست‌وجوی ساب‌های واردشده')+'<input class="field-input" type="search" data-dr-search value="'+esc(DR.query)+'" placeholder="'+esc(L('Group, domain, path or identity','گروه، دامنه، مسیر یا شناسه'))+'"></label><label>'+L('Plan','نوع سرویس')+'<select class="field-input" data-dr-filter-plan><option value="all">'+L('All','همه')+'</option><option value="limited" '+(DR.plan==='limited'?'selected':'')+'>'+L('Volume','حجمی')+'</option><option value="unlimited" '+(DR.plan==='unlimited'?'selected':'')+'>'+L('Unlimited','نامحدود')+'</option></select></label><label>'+L('Presence','وضعیت اتصال')+'<select class="field-input" data-dr-filter-presence><option value="all">'+L('All','همه')+'</option><option value="online" '+(DR.presence==='online'?'selected':'')+'>ONLINE</option><option value="idle" '+(DR.presence==='idle'?'selected':'')+'>IDLE</option><option value="offline" '+(DR.presence==='offline'?'selected':'')+'>OFFLINE</option></select></label><label>'+L('Lifecycle','وضعیت مالکیت')+'<select class="field-input" data-dr-filter-lifecycle><option value="active" '+(DR.lifecycle==='active'?'selected':'')+'>'+L('Restore active','فعال در Restore')+'</option><option value="promoted" '+(DR.lifecycle==='promoted'?'selected':'')+'>'+L('Promoted to native','منتقل‌شده به Native')+'</option><option value="all" '+(DR.lifecycle==='all'?'selected':'')+'>'+L('All','همه')+'</option></select></label><div class="dr-toolbar-buttons"><button class="btn" data-act="drselectall">'+L('Select all filtered','انتخاب همهٔ نتایج')+'</button><button class="btn" data-act="drmove" '+(DR.selected.size?'':'disabled')+'>'+L('Assign selected to group','انتقال به گروه')+'</button><button class="btn btn-primary" data-act="drpromote" '+(DR.selected.size?'':'disabled')+'>'+L('Move to representative Clients','انتقال به Clients نماینده')+'</button>'+(group&&!group.unassigned?'<button class="btn" data-act="drgrouprename" data-id="'+esc(group.id)+'">'+L('Rename group','تغییر نام گروه')+'</button>':'')+'</div></section><section class="panel dr-results">'+resultBody()+'</section><details class="panel dr-domain-panel"><summary>'+L('Domain takeover · DNS / SSL','انتقال دامنه · DNS / SSL')+'</summary>'+domains(d.domains||[])+'</details></div>';
}
enginePage=async function(){if(state.page==='darkrestore')return page();return baseEnginePage();};
async function reload(){DR.data=null;await renderPage();}
async function importDialog(){
 if(!DR.data)await load();
 dialog(L('Import old subscriptions into a group','وارد کردن ساب‌های قدیمی در یک گروه'),'<div class="dr-editor">'+groupFields()+'<label>'+L('Subscription URLs · one per line','لینک‌های ساب · هر خط یک لینک')+'<textarea class="field-input" name="urls" rows="8" dir="ltr" required placeholder="https://sub.example.com/path/token"></textarea></label><div><b>'+L('Target Inbounds','اینباندهای مقصد')+'</b><div class="tg-list">'+inboundPicker([])+'</div></div><div><b>'+L('Optional Nodes','نودهای اختیاری')+'</b><div class="tg-list">'+nodePicker([])+'</div></div><div class="notice">'+L('The new group is saved with this scan. Existing users keep their credentials and DARK usage. Subscriptions already in another named group are reported, not silently moved.','گروه جدید همراه اسکن ذخیره می‌شود. شناسه و مصرف DARK کاربران تکراری حفظ می‌شود. ساب متعلق به گروه نام‌گذاری‌شدهٔ دیگر گزارش می‌شود و بی‌اجازه جابه‌جا نمی‌شود.')+'</div></div>',async fd=>{
  const urls=String(fd.get('urls')||'').split(/\r?\n/).map(x=>x.trim()).filter(Boolean),inboundIds=fd.getAll('drInbound').map(Number),nodeIds=fd.getAll('drNode').map(String),group=groupPayload(fd);
  if(!urls.length)throw Error(L('Paste at least one URL.','حداقل یک لینک وارد کن.'));
  if(!inboundIds.length)throw Error(L('Select at least one Inbound.','حداقل یک اینباند انتخاب کن.'));
  const out=await api('/api/dark-restore/import','POST',{urls,inboundIds,nodeIds,scan:true,...group});
  closeDialog();if(out.group?.id)DR.group=out.group.id;DR.page=0;DR.selected.clear();
  toast(out.created+' '+L('created','ساخته شد')+' · '+out.updated+' '+L('updated','بروزرسانی شد'));
  await reload();
  if(out.conflicts?.length||out.applied===false)dialog(L('Import report','گزارش وارد کردن'),'<div class="notice warning">'+(out.conflicts?.length?out.conflicts.length+' '+L('subscriptions belong to another group and were left unchanged. Use group assignment to move them explicitly.','ساب متعلق به گروه دیگری بود و بدون تغییر ماند؛ برای انتقال از گزینهٔ تغییر گروه استفاده کن.')+'<br>':'')+(out.applied===false?L('Records saved; applying the runtime needs attention: ','اطلاعات ذخیره شد؛ اعمال روی هسته نیاز به بررسی دارد: ')+esc(out.apply_error):'')+'</div>');
 },L('SCAN + IMPORT','اسکن و وارد کردن'));
}
async function groupDialog(id=null){
 const old=(DR.data?.groups||[]).find(g=>g.id===id);
 dialog(id?L('Rename migration group','تغییر نام گروه مهاجرت'):L('New migration group','گروه مهاجرت جدید'),'<label>'+L('Representative / group name','نام نماینده / گروه')+'<input class="field-input" name="name" maxlength="80" required value="'+esc(old?.name||'')+'"></label>',async fd=>{
  const g=await api('/api/dark-restore/groups'+(id?'/'+enc(id):''),id?'PUT':'POST',{name:String(fd.get('name')||'').trim()});
  closeDialog();DR.group=g.id;DR.page=0;DR.selected.clear();await reload();
 });
}
async function assignDialog(ids){
 if(!ids.length)return;
 dialog(L('Assign Restore users to group','تخصیص کاربران ریستور به گروه'),'<div class="dr-editor"><p>'+ids.length+' '+L('selected users. Only their group changes; credentials, quota, expiry and usage remain intact.','کاربر انتخاب شده است. فقط گروه تغییر می‌کند؛ کانفیگ، حجم، انقضا و مصرف حفظ می‌شوند.')+'</p>'+groupFields()+'</div>',async fd=>{
  const p=groupPayload(fd);let gid=p.groupId;
  if(p.groupName)gid=(await api('/api/dark-restore/groups','POST',{name:p.groupName})).id;
  await api('/api/dark-restore/groups/assign','POST',{ids,groupId:gid});
  closeDialog();DR.selected.clear();DR.group=gid;DR.page=0;await reload();
 });
}
async function promoteDialog(ids){
 if(!ids.length)return;
 const reps=await api('/api/dark-restore/representatives');
 if(!reps.length){dialog(L('No representative available','نماینده‌ای موجود نیست'),'<div class="notice warning">'+L('Create and enable a representative first.','ابتدا یک نماینده فعال بساز.')+'</div>');return;}
 const options=reps.map(r=>'<option value="'+esc(r.id)+'" '+(!r.enabled?'disabled':'')+'>'+esc((r.name||r.id)+' · '+r.client_count+'/'+(r.max_clients||'∞')+' · '+bytes(r.remaining_volume_bytes)+' · U '+r.remaining_unlimited)+'</option>').join('');
 dialog(L('Promote Restore users to representative','انتقال کاربران Restore به نماینده'),'<div class="dr-editor"><div class="notice warning">'+ids.length+' '+L('selected Restore users will become native Clients. UUIDs are preserved. Restore history stays read-only and future usage moves to native accounting. Representative credit and inbound scope are enforced.','کاربر انتخاب‌شده به Client اصلی تبدیل می‌شوند. UUID حفظ می‌شود؛ تاریخچه Restore فقط خواندنی می‌ماند و مصرف بعدی وارد حسابداری Native می‌شود. اعتبار و دسترسی اینباند نماینده بررسی می‌شود.')+'</div><label>'+L('Representative','نماینده')+'<select class="field-input" name="representativeId">'+options+'</select></label><label class="tg-switch"><input type="checkbox" name="confirmed" required><span>'+L('I understand this transfer is one-way inside Restore.','تأیید می‌کنم این انتقال داخل Restore یک‌طرفه است.')+'</span></label></div>',async fd=>{
  const out=await api('/api/dark-restore/promote','POST',{ids,representativeId:String(fd.get('representativeId')||'')});
  closeDialog();DR.selected.clear();await reload();
  const failed=(out.items||[]).filter(x=>!x.ok);
  if(failed.length)dialog(L('Promotion report','گزارش انتقال'),'<div class="notice warning"><b>'+out.promoted+' '+L('promoted','منتقل شد')+' · '+out.failed+' '+L('failed','ناموفق')+'</b><br>'+failed.slice(0,20).map(x=>esc(x.id+': '+x.error)).join('<br>')+'</div>');
  else toast(out.promoted+' '+L('Restore users promoted to native Clients.','کاربر به Clients اصلی منتقل شدند.'));
 },L('Promote selected','انتقال انتخاب‌ها'));
}
async function detailDialog(id){
 const d=DR.data||await load(),x=(d.items||[]).find(r=>r.id===id);if(!x)throw Error(L('Restore user not found','کاربر ریستور پیدا نشد'));
 const limited=x.plan_type!=='unlimited',remaining=limited?bytes(x.remaining||0):L('Unlimited','نامحدود');
 const title=(x.group_id==='grp_ungrouped'?L('Ungrouped','بدون گروه'):x.group_name)+' · '+x.legacy_host;
 const body='<div class="dr-detail"><header>'+presenceSignal(x)+'<div><small>'+L('Restore identity','شناسه Restore')+'</small><h3 class="mono">'+esc(x.core_email)+'</h3></div>'+(x.promoted?'<span class="dr-native">NATIVE → '+esc(x.promoted_owner)+'</span>':'')+'</header><div class="dr-detail-grid"><section><span>'+L('PLAN','پلن')+'</span><b>'+(limited?L('Volume','حجمی'):L('Unlimited','نامحدود'))+'</b><small>'+L('Remaining','باقی‌مانده')+': '+remaining+'</small></section><section><span>'+L('DARK USAGE','مصرف DARK')+'</span><b>'+bytes(x.dark_used||0)+'</b><small>'+L('Hub','هاب')+': '+bytes(x.local_used||0)+' · '+L('Nodes','نودها')+': '+bytes(x.node_used||0)+'</small></section><section><span>'+L('EXPIRY','انقضا')+'</span><b>'+(x.legacy_expire?stamp(x.legacy_expire):L('No expiry','بدون انقضا'))+'</b><small>'+L('Migration','مهاجرت')+': '+(x.first_seen?stamp(x.first_seen):L('Waiting for subscription update','منتظر آپدیت ساب'))+'</small></section><section><span>'+L('TARGETS','مقصدها')+'</span><b>IN: '+esc((x.inbound_ids||[]).join(', ')||'—')+'</b><small>'+L('Nodes','نودها')+': '+esc((x.node_ids||[]).length?x.node_ids.join(', '):L('Automatic / Hub','خودکار / هاب'))+'</small></section><section class="wide"><span>'+L('LEGACY SOURCE','مبدا قبلی')+'</span><code>'+esc(x.legacy_host+x.legacy_path)+'</code><small>'+L('Legacy usage excluded from DARK','مصرف قبلی از DARK جداست')+': '+bytes(x.legacy_used||0)+'</small></section></div><footer>'+(!x.promoted?'<button class="btn" data-act="drmap" data-id="'+esc(x.id)+'">'+L('Mapping','اتصال')+'</button><button class="btn" data-act="drmoveone" data-id="'+esc(x.id)+'">'+L('Move group','تغییر گروه')+'</button><button class="btn" data-act="drsreview" data-id="'+esc(x.id)+'">'+L('Review source','بررسی مبدا')+'</button><button class="btn" data-act="drssuspend" data-id="'+esc(x.id)+'">'+(x.service_status==='suspended'?L('Resume','رفع توقف'):L('Suspend','توقف'))+'</button><button class="btn danger" data-act="drdelete" data-id="'+esc(x.id)+'">'+L('Delete','حذف')+'</button>':'<button class="btn" data-act="clients">'+L('Open native Clients','بازکردن Clients')+'</button>')+'</footer></div>';
 dialog(L('Restore client','کاربر Restore')+' · '+title,body);
 document.querySelector('#overlay .dialog')?.classList.add('dr-detail-dialog');
}
async function mappingDialog(id){
 const d=DR.data||await load(),x=(d.items||[]).find(r=>r.id===id);if(!x)throw Error(L('Restore user not found','کاربر ریستور پیدا نشد'));
 dialog(L('Restore mapping','اتصال ریستور'),'<div class="dr-editor"><div><b>Inbounds</b><div class="tg-list">'+inboundPicker(x.inbound_ids||[])+'</div></div><div><b>Nodes</b><div class="tg-list">'+nodePicker(x.node_ids||[])+'</div></div></div>',async fd=>{
  const inboundIds=fd.getAll('drInbound').map(Number),nodeIds=fd.getAll('drNode').map(String);if(!inboundIds.length)throw Error(L('Select at least one Inbound.','حداقل یک اینباند انتخاب کن.'));
  await api('/api/dark-restore/'+enc(id)+'/mapping','PUT',{inboundIds,nodeIds});closeDialog();await reload();
 });
}
document.addEventListener('input',ev=>{if(ev.target.matches('[data-dr-search]')){DR.query=ev.target.value;DR.page=0;paint();}});
document.addEventListener('change',ev=>{
 const el=ev.target;
 if(el.matches('[data-dr-group-choice]')){const label=el.closest('form').querySelector('[data-dr-new-group]');label.hidden=el.value!=='__new__';label.querySelector('input').required=!label.hidden;}
 if(el.matches('[data-dr-filter-group]'))runAction('drfiltergroup',{dataset:{group:el.value}}).catch(ex=>toast(ex.message,true));
 if(el.matches('[data-dr-filter-plan]')){DR.plan=el.value;DR.page=0;DR.selected.clear();paint();}
 if(el.matches('[data-dr-filter-presence]')){DR.presence=el.value;DR.page=0;DR.selected.clear();paint();}
 if(el.matches('[data-dr-filter-lifecycle]')){DR.lifecycle=el.value;DR.page=0;DR.selected.clear();paint();}
 if(el.matches('[data-dr-select]')){el.checked?DR.selected.add(el.dataset.drSelect):DR.selected.delete(el.dataset.drSelect);paint();}
});
runAction=async function(act,el){
 if(act==='drimport')return importDialog();
 if(act==='drgroupnew')return groupDialog();
 if(act==='drgrouprename')return groupDialog(el.dataset.id);
 if(act==='drmove')return assignDialog([...DR.selected]);
 if(act==='drpromote')return promoteDialog([...DR.selected]);
 if(act==='drmoveone')return assignDialog([el.dataset.id]);
 if(act==='drfiltergroup'){DR.group=el.dataset.group||'';DR.page=0;DR.selected.clear();return renderPage();}
 if(act==='drselectall'){const rows=filtered().filter(x=>!x.promoted);if(rows.every(x=>DR.selected.has(x.id)))DR.selected.clear();else rows.forEach(x=>DR.selected.add(x.id));paint();return;}
 if(act==='drprev'||act==='drnext'){DR.page+=act==='drprev'?-1:1;paint();return;}
 if(act==='drdetail')return detailDialog(el.dataset.id);
 if(act==='drmap')return mappingDialog(el.dataset.id);
 if(act==='drdelete'){if(confirm(L('Delete this Restore user? Native users are not affected.','این کاربر ریستور حذف شود؟ کاربران اصلی تغییری نمی‌کنند.'))){await api('/api/dark-restore/'+enc(el.dataset.id),'DELETE');await reload();}return;}
 if(act==='drdomaincheck'){
  const out=await api('/api/dark-restore/domains/'+enc(el.dataset.domain)+'/check','POST',{});DR.data=null;
  if(out.ssl_status!=='ready'&&out.apply_command)dialog(L('SSL issuance','صدور SSL'),'<div class="notice">'+L('Issue the certificate through the root CLI.','صدور گواهی از طریق CLI روت انجام می‌شود.')+'</div><pre class="json-box">'+esc(out.apply_command)+'</pre>');
  else toast(L('Domain and SSL are ready.','دامنه و SSL آماده است.'));
  await renderPage();return;
 }
 return baseRunAction(act,el);
};
globalThis.DarkRestoreGroups={state:DR,filtered,groupPayload,users};
})();
