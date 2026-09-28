/* DARK RESTORE target editor: whole inbound inheritance or explicit runtime subset. */
(function(){
'use strict';
if(!globalThis.DarkRestoreGroups||typeof enginePage!=='function'||typeof runAction!=='function')return;
const DR=DarkRestoreGroups.state,previousEngine=enginePage,previousAction=runAction;
const L=(en,fa)=>(localStorage.getItem('dark_lang')||'en')==='fa'?fa:en;
const esc=x=>e(String(x??''));
let catalog=null;
const modeLabel=v=>v==='all'?L('Whole inbound + its Nodes','کل اینباند + نودهای متصل'):L('Selected Nodes','نودهای انتخاب‌شده');
const rowSelection=r=>({inboundIds:r.inbound_ids||[],nodeIds:r.node_ids||[],nodeMode:r.node_mode||'selected',includeLocal:r.include_local!==0&&r.include_local!==false});
const emptySelection=()=>({inboundIds:[],nodeIds:[],nodeMode:'all',includeLocal:true});
function selectionText(v){
 const names=v.nodeMode==='all'?L('All assigned Nodes (including future assignments)','همه نودهای متصل، شامل نودهایی که بعداً متصل شوند'):(v.nodeIds||[]).map(id=>(DR.nodes||[]).find(n=>n.id===id)?.name||id).join(' · ')||L('No Nodes','بدون نود');
 return 'IN: '+(v.inboundIds||[]).join(', ')+' · '+(v.includeLocal?L('Hub when deployed','هاب در صورت استقرار'):L('No Hub','بدون هاب'))+' · '+names;
}
function groupSummary(){
 const group=(DR.data?.groups||[]).find(g=>g.id===DR.group);
 if(!group)return '';
 const rows=(DR.data?.items||[]).filter(r=>r.group_id===group.id),variants=[...new Set(rows.map(r=>JSON.stringify(rowSelection(r))))];
 return '<section class="panel drm-group-summary"><div><h3>'+esc(L('Group destinations','مقصدهای گروه'))+' · <span data-no-i18n>'+esc(group.name)+'</span></h3><p>'+rows.length+' '+L('clients; edits apply to the whole group, not just this page.','کاربر؛ ویرایش روی کل گروه اعمال می‌شود، نه فقط این صفحه.')+'</p>'+(variants.length>1?'<div class="notice warning">'+L('This group currently has mixed mappings. Preview before replacing them.','این گروه مقصدهای یکسان ندارد؛ قبل از جایگزینی پیش‌نمایش را بررسی کن.')+'</div>':'')+variants.slice(0,5).map(v=>'<small>'+esc(selectionText(JSON.parse(v)))+'</small>').join('')+'</div><button type="button" class="btn btn-primary" data-act="drgroupmapping" data-id="'+esc(group.id)+'">'+icon('settings')+L('Edit group destinations','ویرایش مقصدهای گروه')+'</button></section>';
}
enginePage=async function(){
 const page=state.page,html=await previousEngine();
 if(page!=='darkrestore'||!isOwner())return html;
 return html.replace('<section class="panel dr-results">',groupSummary()+'<section class="panel dr-results">');
};
async function loadCatalog(){catalog=await api('/api/dark-restore/targets');return catalog;}
function targetFields(v){
 const ids=new Set(v.inboundIds),nodes=new Set(v.nodeIds);
 return '<section class="drm-targets"><h3>'+L('Subscription destinations','مقصدهای ساب')+'</h3><div><b>'+L('Inbounds','اینباندها')+'</b><div class="tg-list">'+catalog.inbounds.map(i=>'<label class="tg-switch"><input type="checkbox" name="drInbound" value="'+i.id+'" '+(ids.has(i.id)?'checked':'')+'><span data-no-i18n>'+esc(i.name)+' · #'+i.id+' · :'+i.port+'</span></label>').join('')+'</div></div><label>'+L('Node selection mode','حالت انتخاب نود')+'<select class="field-input" name="nodeMode" data-drm-mode><option value="all" '+(v.nodeMode==='all'?'selected':'')+'>'+modeLabel('all')+'</option><option value="selected" '+(v.nodeMode==='selected'?'selected':'')+'>'+modeLabel('selected')+'</option></select></label><label class="tg-switch"><input type="checkbox" name="includeLocal" '+(v.includeLocal?'checked':'')+'><span>'+L('Include Hub if this inbound is deployed locally','هاب هم باشد، اگر اینباند روی هاب فعال است')+'</span></label><div class="drm-node-set" data-drm-node-picker '+(v.nodeMode==='all'?'hidden':'')+'>'+catalog.nodes.map(n=>'<label class="tg-switch"><input type="checkbox" name="drNode" value="'+esc(n.id)+'" '+(nodes.has(n.id)?'checked':'')+'><span data-no-i18n>'+esc(n.name)+' · '+esc(n.address)+'</span></label>').join('')+'</div><div class="notice">'+L('Whole inbound follows its assigned Nodes automatically. Direct and configured Tunnel routes are delivered only when their runtime is ready.','حالت کل اینباند، نودهای متصل به همان اینباند را خودکار دنبال می‌کند. مسیر مستقیم و تانل تنظیم‌شده، فقط برای مقصد آماده وارد ساب می‌شوند.')+'</div><div class="drm-preview-list" data-drm-target-preview></div></section>';
}
function readSelection(fd){
 const inboundIds=fd.getAll('drInbound').map(Number),nodeMode=String(fd.get('nodeMode')||'all'),nodeIds=nodeMode==='all'?[]:fd.getAll('drNode').map(String),includeLocal=fd.has('includeLocal');
 if(!inboundIds.length)throw Error(L('Select at least one inbound.','حداقل یک اینباند انتخاب کن.'));
 if(nodeMode==='selected'&&!includeLocal&&!nodeIds.length)throw Error(L('Select Hub or at least one Node.','هاب یا حداقل یک نود را انتخاب کن.'));
 return {inboundIds,nodeIds,nodeMode,includeLocal};
}
function targetList(rows){
 if(!rows.length)return '<div class="notice warning">'+L('No assigned destinations match this selection.','مقصد متصل‌شده‌ای با این انتخاب وجود ندارد.')+'</div>';
 return rows.map(t=>'<div class="drm-target"><span data-no-i18n>#'+t.inboundId+' · '+esc(t.name)+' · '+esc(t.address)+'</span><b class="'+(t.ready?'green':'amber')+'">'+(t.ready?L('Ready','آماده'):L('Pending / offline','در انتظار / آفلاین'))+'</b></div>').join('');
}
function paintTargets(){
 const form=document.querySelector('#dialog-form'),box=form?.querySelector('[data-drm-target-preview]');if(!box||!catalog)return;
 const fd=new FormData(form),ids=new Set(fd.getAll('drInbound').map(Number)),chosen=new Set(fd.getAll('drNode').map(String)),all=fd.get('nodeMode')==='all',local=fd.has('includeLocal'),rows=[];
 const picker=form.querySelector('[data-drm-node-picker]');if(picker)picker.hidden=all;
 for(const input of form.querySelectorAll('[name="drNode"]')){
  const n=catalog.nodes.find(x=>x.id===input.value);input.disabled=!n?.inboundIds.some(id=>ids.has(id));
 }
 for(const i of catalog.inbounds){if(!ids.has(i.id))continue;
  if(local&&i.local)rows.push({inboundId:i.id,name:catalog.hub.name,address:catalog.hub.address,ready:i.enabled});
  for(const n of catalog.nodes)if(n.inboundIds.includes(i.id)&&(all||chosen.has(n.id)))rows.push({inboundId:i.id,name:n.name,address:n.address,ready:i.enabled&&n.readyInboundIds.includes(i.id)});
 }
 box.innerHTML=targetList(rows);
}
function markEditor(){const d=document.querySelector('#overlay .dialog');if(d)d.classList.add('drm-dialog');paintTargets();}
async function reload(){DR.data=null;await renderPage();}
function report(out){
 if(out.applied===false)dialog(L('Saved; runtime needs attention','ذخیره شد؛ هسته نیاز به بررسی دارد'),'<div class="notice warning">'+esc(out.apply_error)+'</div>');
 else toast(L('Destinations saved. Customers must update their existing subscriptions.','مقصدها ذخیره شدند؛ مشتری‌ها همان ساب قبلی را آپدیت کنند.'));
}
async function groupEditor(id){
 const info=await api('/api/dark-restore/groups/'+enc(id)+'/mapping');catalog=info.catalog;
 const v=info.mapping||info.defaultMapping||{...emptySelection(),inboundIds:[...new Set(info.variants.flatMap(x=>x.inboundIds))]};
 dialog(L('Edit group destinations','ویرایش مقصدهای گروه')+' · '+info.name,'<div class="dr-editor"><div class="notice">'+info.clients+' '+L('clients in this group. UUIDs, links, quota, expiry and usage will not be reset.','کاربر در این گروه. شناسه، لینک، حجم، انقضا و مصرف ریست نمی‌شوند.')+'</div>'+(info.mixed?'<div class="notice warning">'+L('Mixed current mappings will be replaced only after your confirmation.','مقصدهای متفاوت فعلی فقط پس از تأیید تو جایگزین می‌شوند.')+'</div>':'')+targetFields(v)+'</div>',async fd=>{
  const value=readSelection(fd),preview=await api('/api/dark-restore/groups/'+enc(id)+'/mapping/preview','POST',value);
  dialog(L('Confirm group-wide change','تأیید تغییر کل گروه')+' · '+preview.name,'<div class="dr-editor"><div class="notice warning">'+preview.clients+' '+L('clients will use this mapping, including clients on other pages.','کاربر از این مقصدها استفاده می‌کنند، شامل کاربران صفحه‌های دیگر.')+'</div><p>'+esc(selectionText(preview.mapping))+'</p>'+targetList(preview.targets)+'<label class="tg-switch"><input name="confirmTargets" type="checkbox" required><span>'+L('Apply these destinations to the entire group; keep all client identities and usage.','این مقصدها روی کل گروه اعمال شود؛ شناسه و مصرف همه کاربران حفظ شود.')+'</span></label></div>',async()=>{
   const out=await api('/api/dark-restore/groups/'+enc(id)+'/mapping','PUT',{...preview.mapping,expectedRevision:preview.revision});
   closeDialog();await reload();report(out);
  },L('Apply to whole group','اعمال روی کل گروه'));markEditor();
 },L('Preview changes','پیش‌نمایش تغییرات'));markEditor();
}
async function userEditor(id){
 await loadCatalog();const data=await api('/api/dark-restore'),r=(data.items||[]).find(x=>x.id===id);
 if(!r)throw Error(L('Restore user not found.','کاربر ریستور پیدا نشد.'));
 dialog(L('Restore destinations','مقصدهای ریستور'),'<div class="dr-editor">'+targetFields(rowSelection(r))+'</div>',async fd=>{
  const out=await api('/api/dark-restore/'+enc(id)+'/mapping','PUT',readSelection(fd));closeDialog();await reload();report(out);
 });markEditor();
}
async function importEditor(){
 await loadCatalog();if(!DR.data)DR.data=await api('/api/dark-restore');
 const selected=DR.group&&DR.group!=='grp_ungrouped'?DR.group:'__new__';let v=emptySelection();
 if(selected!=='__new__'){const info=await api('/api/dark-restore/groups/'+enc(selected)+'/mapping');v=info.mapping||info.defaultMapping||v;}
 const options='<option value="__new__">'+L('+ Create a group','+ ایجاد گروه')+'</option>'+DR.data.groups.map(g=>'<option value="'+esc(g.id)+'" '+(g.id===selected?'selected':'')+'>'+esc(g.name)+'</option>').join('');
 const fields='<div class="dr-group-form"><label>'+L('Representative / migration group','گروه نماینده / مهاجرت')+'<select name="groupChoice" class="field-input" data-dr-group-choice>'+options+'</select></label><label data-dr-new-group '+(selected==='__new__'?'':'hidden')+'>'+L('New group name','نام گروه جدید')+'<input class="field-input" name="groupName" maxlength="80" '+(selected==='__new__'?'required':'')+'></label></div>';
 dialog(L('Import old subscriptions into a group','وارد کردن ساب‌های قدیمی در یک گروه'),'<div class="dr-editor">'+fields+'<label>'+L('Subscription URLs · one per line','لینک‌های ساب · هر خط یک لینک')+'<textarea class="field-input" name="urls" rows="8" dir="ltr" required></textarea></label>'+targetFields(v)+'</div>',async fd=>{
  const urls=String(fd.get('urls')||'').split(/\r?\n/).map(x=>x.trim()).filter(Boolean),value=readSelection(fd),group=DarkRestoreGroups.groupPayload(fd);
  if(!urls.length)throw Error(L('Paste at least one URL.','حداقل یک لینک وارد کن.'));
  const out=await api('/api/dark-restore/import','POST',{urls,...value,...group,scan:true});
  closeDialog();if(out.group?.id)DR.group=out.group.id;DR.page=0;DR.selected.clear();await reload();
  toast(out.created+' '+L('created','ساخته شد')+' · '+out.updated+' '+L('updated','بروزرسانی شد'));
  if(out.conflicts?.length||out.applied===false)dialog(L('Import report','گزارش وارد کردن'),'<div class="notice warning">'+(out.conflicts?.length?out.conflicts.length+' '+L('subscriptions belong to another group and were left unchanged.','ساب متعلق به گروه دیگری بود و تغییر نکرد.')+'<br>':'')+esc(out.apply_error||'')+'</div>');
 },L('SCAN + IMPORT','اسکن و وارد کردن'));markEditor();
}
document.addEventListener('change',ev=>{if(ev.target.closest('.drm-targets'))paintTargets();});
runAction=async function(act,el){
 if(['drgroupmapping','drmap','drimport'].includes(act)){
  if(!isOwner())throw Error(L('Owner access required.','دسترسی مالک لازم است.'));
  if(act==='drgroupmapping')return groupEditor(el.dataset.id);
  if(act==='drmap')return userEditor(el.dataset.id);
  return importEditor();
 }
 return previousAction(act,el);
};
globalThis.DarkRestoreTargets={ready:true,readSelection,rowSelection,groupSummary};
})();
