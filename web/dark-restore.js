/* DARK XRAY — grouped DARK RESTORE; legacy quota is not DARK usage. */
(function(){
'use strict';
if(typeof navItems!=='function'||typeof enginePage!=='function'||typeof runAction!=='function')return;
const baseNavItems=navItems,baseEnginePage=enginePage,baseRunAction=runAction;
const DR={data:null,nodes:[],group:'',query:'',page:0,selected:new Set()},pageSize=50;
const L=(en,fa)=>(localStorage.getItem('dark_lang')||'en')==='fa'?fa:en;
const esc=v=>e(String(v??''));
const stamp=v=>v?new Date(Number(v)*1000).toLocaleString((localStorage.getItem('dark_lang')||'en')==='fa'?'fa-IR':'en-US'):'—';
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
function filtered(){const q=DR.query.trim().toLocaleLowerCase();return (DR.data?.items||[]).filter(x=>(!DR.group||x.group_id===DR.group)&&(!q||[x.legacy_host,x.legacy_path,x.group_name,x.id].join(' ').toLocaleLowerCase().includes(q)));}
function groupCards(){
 const all=DR.data?.items||[],used=all.reduce((n,x)=>n+Number(x.dark_used||0),0);
 const tile=(id,name,count,migrated,value)=>'<button type="button" class="dr-group '+(DR.group===id?'active':'')+'" data-act="drfiltergroup" data-group="'+esc(id)+'"><b>'+esc(name)+'</b><strong>'+bytes(value)+'</strong><small>'+L('Usage in DARK','مصرف در DARK')+' · '+count+' '+L('clients','کاربر')+' · '+migrated+' '+L('migrated','منتقل‌شده')+'</small></button>';
 return '<div class="dr-groups">'+tile('',L('All groups','همه گروه‌ها'),all.length,all.filter(x=>x.first_seen>0).length,used)+(DR.data?.groups||[]).filter(g=>!g.unassigned||g.clients>0).map(g=>tile(g.id,groupTitle(g),g.clients,g.migrated,g.dark_used)).join('')+'</div>';
}
function users(rows){
 if(!rows.length)return '<div class="notice">'+L('No Restore users match this group or search.','کاربری در این گروه یا جست‌وجو پیدا نشد.')+'</div>';
 return '<div class="dr-users">'+rows.map(x=>{
  const name=x.group_id==='grp_ungrouped'?L('Ungrouped','بدون گروه'):x.group_name;
  return '<article class="dr-user"><label class="dr-check"><input type="checkbox" data-dr-select="'+esc(x.id)+'" '+(DR.selected.has(x.id)?'checked':'')+' aria-label="'+esc(L('Select Restore user','انتخاب کاربر ریستور'))+'"></label><div class="dr-identity"><b data-no-i18n>'+esc(name)+'</b><small class="mono" data-no-i18n>'+esc(x.legacy_host)+'</small><code data-no-i18n title="'+esc(x.legacy_path)+'">'+esc(x.legacy_path)+'</code></div><div class="dr-usage"><span>'+L('Usage in DARK','مصرف در DARK')+'</span><strong data-dr-dark-usage>'+bytes(x.dark_used||0)+'</strong><small>'+L('Hub','هاب')+': '+bytes(x.local_used||0)+' · '+L('Nodes','نودها')+': '+bytes(x.node_used||0)+'</small><small>'+L('Remaining','باقی‌مانده')+': '+(x.legacy_total?bytes(x.remaining||0):L('Unlimited / unknown','نامحدود / نامشخص'))+'</small><details><summary>'+L('Previous quota metadata','اطلاعات سهمیهٔ قبلی')+'</summary><small>'+L('Legacy usage (excluded)','مصرف قدیمی (جدا از گزارش DARK)')+': '+bytes(x.legacy_used||0)+'<br>'+L('Original total','حجم کل قبلی')+': '+bytes(x.legacy_total||0)+'</small></details></div><div class="dr-migration">'+tag(x.scan_status)+'<small>'+L('Expiry','انقضا')+': '+(x.legacy_expire?stamp(x.legacy_expire):L('Unlimited / unknown','نامحدود / نامشخص'))+'</small><small>'+L('Migration started','شروع مهاجرت')+': '+(x.first_seen?stamp(x.first_seen):L('Waiting for subscription update','منتظر آپدیت ساب'))+'</small><small>IN: '+esc((x.inbound_ids||[]).join(', ')||'—')+'</small></div><div class="dr-actions"><button type="button" class="btn mini" data-act="drmap" data-id="'+esc(x.id)+'">'+L('Mapping','اتصال')+'</button><button type="button" class="btn mini" data-act="drmoveone" data-id="'+esc(x.id)+'">'+L('Group','گروه')+'</button><button type="button" class="btn mini" data-act="drdelete" data-id="'+esc(x.id)+'">'+L('Delete','حذف')+'</button></div></article>';
 }).join('')+'</div>';
}
function resultBody(){
 const rows=filtered(),pages=Math.max(1,Math.ceil(rows.length/pageSize));DR.page=Math.max(0,Math.min(DR.page,pages-1));
 const used=rows.reduce((n,x)=>n+Number(x.dark_used||0),0);
 return '<div class="dr-result-head"><b>'+rows.length+' '+L('clients in this filter','کاربر در این فیلتر')+'</b><span>'+L('Usage since migration','مصرف بعد از مهاجرت')+': <strong>'+bytes(used)+'</strong></span><span>'+DR.selected.size+' '+L('selected','انتخاب‌شده')+'</span></div>'+users(rows.slice(DR.page*pageSize,(DR.page+1)*pageSize))+'<div class="dr-pagination"><button type="button" class="btn" data-act="drprev" '+(DR.page===0?'disabled':'')+'>'+L('Previous','قبلی')+'</button><span>'+(DR.page+1)+' / '+pages+'</span><button type="button" class="btn" data-act="drnext" '+(DR.page+1>=pages?'disabled':'')+'>'+L('Next','بعدی')+'</button></div>';
}
function paint(){const el=document.querySelector('.dr-results');if(el)el.innerHTML=resultBody();const move=document.querySelector('[data-act="drmove"]');if(move)move.disabled=!DR.selected.size;}
function domains(rows){
 if(!rows.length)return '<div class="notice">'+L('Domains are detected after import.','دامنه‌ها بعد از وارد کردن ساب شناسایی می‌شوند.')+'</div>';
 return '<div class="reseller-list">'+rows.map(d=>'<article class="panel reseller-card"><div class="page-heading"><div><h2 class="mono">'+esc(d.domain)+'</h2><small>'+L('Point A/AAAA to this DARK XRAY server before cutover.','قبل از انتقال رکورد A/AAAA را روی سرور DARK XRAY ست کن.')+'</small></div>'+tag(d.dns_status)+'</div><div class="details"><div><span>DNS</span><b>'+esc(d.dns_status)+'</b></div><div><span>SSL</span><b>'+esc(d.ssl_status)+'</b></div><div><span>'+L('Last check','آخرین بررسی')+'</span><b>'+stamp(d.last_checked)+'</b></div></div><div class="row-actions"><button class="btn" data-act="drdomaincheck" data-domain="'+esc(d.domain)+'">'+icon('refresh')+L('Check DNS / SSL','بررسی DNS / SSL')+'</button></div></article>').join('')+'</div>';
}
async function page(){
 const d=await load(),groups=d.groups||[],group=groups.find(g=>g.id===DR.group);
 return '<div class="dark-restore-v2">'+heading('DARK RESTORE',L('Separate every representative migration; report only traffic used after moving to DARK.','مهاجرت هر نماینده جدا؛ گزارش مصرف فقط مربوط به بعد از انتقال به DARK است.'),'<button class="btn" data-act="drgroupnew">'+icon('plus')+L('New group','گروه جدید')+'</button><button class="btn btn-primary" data-act="drimport">'+icon('plus')+L('Scan / import subscriptions','اسکن / وارد کردن ساب‌ها')+'</button>')+'<div class="notice">'+L('Old usage is excluded from every DARK usage total. It is retained only to preserve the original remaining quota and subscription metadata. Groups here do not create native representatives or mix their clients.','مصرف قبلی در هیچ‌یک از اعداد مصرف DARK جمع نمی‌شود؛ فقط برای حفظ حجم باقی‌مانده و اطلاعات ساب نگه داشته می‌شود. این گروه‌ها مستقل از نمایندگان و کاربران اصلی پنل هستند.')+'</div>'+groupCards()+'<section class="panel dr-toolbar"><label>'+L('Group','گروه')+'<select class="field-input" data-dr-filter-group>'+groupOptions(DR.group,true)+'</select></label><label>'+L('Search imported subscriptions','جست‌وجوی ساب‌های واردشده')+'<input class="field-input" type="search" data-dr-search value="'+esc(DR.query)+'" placeholder="'+esc(L('Group, domain or subscription path','گروه، دامنه یا مسیر ساب'))+'"></label><div class="dr-toolbar-buttons"><button class="btn" data-act="drselectall">'+L('Select all filtered','انتخاب همهٔ نتایج')+'</button><button class="btn btn-primary" data-act="drmove" '+(DR.selected.size?'':'disabled')+'>'+L('Assign selected to group','انتقال انتخاب‌ها به گروه')+'</button>'+(group&&!group.unassigned?'<button class="btn" data-act="drgrouprename" data-id="'+esc(group.id)+'">'+L('Rename group','تغییر نام گروه')+'</button>':'')+'</div></section><section class="panel dr-results">'+resultBody()+'</section><details class="panel dr-domain-panel"><summary>'+L('Domain takeover · DNS / SSL','انتقال دامنه · DNS / SSL')+'</summary>'+domains(d.domains||[])+'</details></div>';
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
 if(el.matches('[data-dr-select]')){el.checked?DR.selected.add(el.dataset.drSelect):DR.selected.delete(el.dataset.drSelect);paint();}
});
runAction=async function(act,el){
 if(act==='drimport')return importDialog();
 if(act==='drgroupnew')return groupDialog();
 if(act==='drgrouprename')return groupDialog(el.dataset.id);
 if(act==='drmove')return assignDialog([...DR.selected]);
 if(act==='drmoveone')return assignDialog([el.dataset.id]);
 if(act==='drfiltergroup'){DR.group=el.dataset.group||'';DR.page=0;DR.selected.clear();return renderPage();}
 if(act==='drselectall'){const rows=filtered();if(rows.every(x=>DR.selected.has(x.id)))DR.selected.clear();else rows.forEach(x=>DR.selected.add(x.id));paint();return;}
 if(act==='drprev'||act==='drnext'){DR.page+=act==='drprev'?-1:1;paint();return;}
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
