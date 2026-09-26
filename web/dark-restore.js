/* DARK XRAY — DARK RESTORE V1 */
(function(){
'use strict';
if(typeof navItems!=='function'||typeof enginePage!=='function'||typeof runAction!=='function')return;
const baseNavItems=navItems,baseEnginePage=enginePage,baseRunAction=runAction;
const DR={data:null,nodes:[]};
const L=(en,fa)=>(localStorage.getItem('dark_lang')||'en')==='fa'?fa:en;
const esc=v=>e(String(v??''));
const stamp=v=>v?new Date(Number(v)*1000).toLocaleString():'—';
enginePages.darkrestore=['DARK RESTORE'];

navItems=function(){
 const n=baseNavItems();
 if(!isOwner()||n.some(x=>x[0]==='darkrestore'))return n;
 const i=Math.max(0,n.findIndex(x=>x[0]==='account'));
 n.splice(i,0,['darkrestore','DARK RESTORE','refresh']);
 return n;
};

async function load(){
 const [data,nodes]=await Promise.all([api('/api/dark-restore'),api('/api/nodes').catch(()=>[])]);
 DR.data=data;DR.nodes=nodes?.items||nodes||[];return data;
}
function tag(s){
 const cls=['verified','ok','ready'].includes(s)?'green':['partial','pending','unchecked'].includes(s)?'amber':'red';
 return '<span class="tag '+cls+'">'+esc(String(s||'unknown').toUpperCase())+'</span>';
}
function inboundPicker(selected=[]){
 const set=new Set(selected.map(Number));
 return (state.inbounds||[]).map(x=>'<label class="tg-switch"><input type="checkbox" name="drInbound" value="'+x.id+'" '+(set.has(Number(x.id))?'checked':'')+'><span>'+esc(x.id+' · '+(x.remark||x.tag||'Inbound')+' · :'+x.port)+'</span></label>').join('')||'<div class="notice">'+L('Create an Inbound first.','ابتدا Inbound بساز.')+'</div>';
}
function nodePicker(selected=[]){
 const set=new Set(selected.map(String));
 return (DR.nodes||[]).map(x=>'<label class="tg-switch"><input type="checkbox" name="drNode" value="'+esc(x.id)+'" '+(set.has(String(x.id))?'checked':'')+'><span>'+esc((x.name||x.id)+' · '+(x.online?'ONLINE':'OFFLINE'))+'</span></label>').join('')||'<div class="notice">'+L('No Nodes registered. Local Inbounds still work.','نودی ثبت نشده؛ اینباندهای لوکال همچنان کار می‌کنند.')+'</div>';
}
const chosen=name=>[...document.querySelectorAll('#dialog-form input[name="'+name+'"]:checked')].map(x=>x.value);

function domains(rows){
 if(!rows.length)return '<div class="notice">'+L('Domains are detected after import.','دامنه‌ها بعد از Import شناسایی می‌شوند.')+'</div>';
 return '<div class="reseller-list">'+rows.map(d=>'<article class="panel reseller-card"><div class="page-heading"><div><h2 class="mono">'+esc(d.domain)+'</h2><small>'+L('Point A/AAAA to this DARK XRAY server before cutover.','قبل از انتقال رکورد A/AAAA را روی سرور DARK XRAY ست کن.')+'</small></div>'+tag(d.dns_status)+'</div><div class="details"><div><span>DNS</span><b>'+esc(d.dns_status)+'</b></div><div><span>SSL</span><b>'+esc(d.ssl_status)+'</b></div><div><span>'+L('Last check','آخرین بررسی')+'</span><b>'+stamp(d.last_checked)+'</b></div></div><div class="row-actions"><button class="btn" data-act="drdomaincheck" data-domain="'+esc(d.domain)+'">'+icon('refresh')+L('Check DNS / SSL','بررسی DNS / SSL')+'</button></div></article>').join('')+'</div>';
}
function users(rows){
 if(!rows.length)return '<div class="notice">'+L('No legacy subscriptions imported yet.','هنوز ساب قدیمی وارد نشده است.')+'</div>';
 return '<div class="table-wrap"><table><thead><tr><th>SUB</th><th>'+L('Usage','مصرف')+'</th><th>'+L('Expiry','انقضا')+'</th><th>'+L('Targets','مقصد')+'</th><th>'+L('Migration','مهاجرت')+'</th><th></th></tr></thead><tbody>'+rows.map(x=>{
  const legacy=Number(x.legacy_upload||0)+Number(x.legacy_download||0);
  return '<tr><td><b class="mono">'+esc(x.legacy_host)+'</b><br><small class="mono">'+esc(x.legacy_path)+'</small></td><td><b>'+bytes(x.effective_used||0)+'</b><br><small>'+L('Old','قدیمی')+': '+bytes(legacy)+' · DARK: '+bytes(x.dark_used||0)+(x.legacy_total?' · '+L('Remaining','باقی')+': '+bytes(x.remaining||0):'')+'</small></td><td>'+(x.legacy_expire?stamp(x.legacy_expire):L('Unlimited / unknown','نامحدود / نامشخص'))+'</td><td><small>IN: '+esc((x.inbound_ids||[]).join(', ')||'—')+'<br>NODE: '+esc((x.node_ids||[]).join(', ')||L('local','لوکال'))+'</small></td><td>'+tag(x.scan_status)+'<br><small>'+(x.last_seen?L('UPDATED','آپدیت شده')+' · '+stamp(x.last_seen):L('WAITING FOR USER UPDATE','منتظر آپدیت کاربر'))+'</small></td><td><div class="row-actions"><button class="btn mini" data-act="drmap" data-id="'+esc(x.id)+'">'+L('Mapping','اتصال')+'</button><button class="btn mini" data-act="drdelete" data-id="'+esc(x.id)+'">'+L('Delete','حذف')+'</button></div></td></tr>';
 }).join('')+'</tbody></table></div>';
}
async function page(){
 const d=await load(),rows=d.items||[],seen=rows.filter(x=>Number(x.last_seen)>0).length,pct=rows.length?Math.round(seen*100/rows.length):0;
 return heading('DARK RESTORE',L('Keep existing subscription URLs and replace every old config with DARK XRAY configs on the next update.','لینک ساب موجود حفظ می‌شود و در اولین آپدیت تمام کانفیگ‌های قدیمی با کانفیگ‌های DARK XRAY جایگزین می‌شوند.'),'<button class="btn btn-primary" data-act="drimport">'+icon('plus')+L('Import subscriptions','وارد کردن ساب‌ها')+'</button>')+
 '<article class="panel"><div class="page-heading"><div><span class="code-caption">MIGRATION PROGRESS</span><h2>'+seen+' / '+rows.length+'</h2></div><span class="tag '+(pct===100&&rows.length?'green':'amber')+'">'+pct+'%</span></div><p>'+L('A Restore user counts as migrated only after the old subscription URL is updated through DARK XRAY.','کاربر Restore فقط وقتی منتقل‌شده حساب می‌شود که لینک قدیمی را از DARK XRAY آپدیت کند.')+'</p></article>'+
 '<div style="height:18px"></div><article class="panel"><div class="panel-head"><h2>'+L('Domain takeover','انتقال دامنه')+'</h2></div>'+domains(d.domains||[])+'</article>'+
 '<div style="height:18px"></div><article class="panel"><div class="panel-head"><h2>'+L('Restore users','کاربران Restore')+'</h2><span class="tag">'+rows.length+'</span></div><div class="notice">'+L('Restore users are isolated from native Clients and Representatives.','کاربران Restore از کاربران و نمایندگان اصلی پنل کاملاً جدا هستند.')+'</div>'+users(rows)+'</article>';
}
enginePage=async function(){if(state.page==='darkrestore')return page();return baseEnginePage();};

async function importDialog(){
 if(!DR.data)await load();
 dialog(L('Import old subscription URLs','وارد کردن لینک‌های ساب قدیمی'),'<div class="form-grid"><label class="span-2">'+L('Subscription URLs · one per line','لینک‌های ساب · هر خط یک لینک')+'<textarea class="field-input" name="urls" rows="10" dir="ltr" required placeholder="https://sub.example.com/path/token"></textarea></label><div class="span-2"><label>'+L('Target Inbounds','اینباندهای مقصد')+'</label><div class="tg-list">'+inboundPicker([])+'</div></div><div class="span-2"><label>'+L('Optional Nodes','نودهای اختیاری')+'</label><div class="tg-list">'+nodePicker([])+'</div></div><div class="span-2 notice">'+L('Pre-scan imports only usage, total and expiry metadata. Old config content is never imported.','Pre-Scan فقط مصرف، حجم و انقضا را می‌خواند؛ محتوای کانفیگ‌های قدیمی وارد نمی‌شود.')+'</div></div>',async fd=>{
  const urls=String(fd.get('urls')||'').split(/\r?\n/).map(x=>x.trim()).filter(Boolean),inboundIds=chosen('drInbound').map(Number),nodeIds=chosen('drNode');
  if(!urls.length)throw Error(L('Paste at least one URL.','حداقل یک لینک وارد کن.'));
  if(!inboundIds.length)throw Error(L('Select at least one Inbound.','حداقل یک Inbound انتخاب کن.'));
  const out=await api('/api/dark-restore/import','POST',{urls,inboundIds,nodeIds,scan:true});
  closeDialog();DR.data=null;toast(out.created+' '+L('created','ساخته شد')+' · '+out.updated+' '+L('updated','بروزرسانی شد'));await renderPage();
 },L('PRE-SCAN + IMPORT','اسکن و انتقال'));
}
async function mappingDialog(id){
 const d=DR.data||await load(),x=(d.items||[]).find(r=>r.id===id);if(!x)throw Error('Restore user not found');
 dialog(L('Restore mapping','اتصال Restore'),'<div class="form-grid"><div class="span-2"><label>Inbounds</label><div class="tg-list">'+inboundPicker(x.inbound_ids||[])+'</div></div><div class="span-2"><label>Nodes</label><div class="tg-list">'+nodePicker(x.node_ids||[])+'</div></div></div>',async()=>{
  const inboundIds=chosen('drInbound').map(Number),nodeIds=chosen('drNode');if(!inboundIds.length)throw Error(L('Select at least one Inbound.','حداقل یک Inbound انتخاب کن.'));
  await api('/api/dark-restore/'+enc(id)+'/mapping','PUT',{inboundIds,nodeIds});closeDialog();DR.data=null;toast(L('Mapping updated.','اتصال بروزرسانی شد.'));await renderPage();
 });
}
runAction=async function(act,el){
 if(act==='drimport'){await importDialog();return;}
 if(act==='drmap'){await mappingDialog(el.dataset.id);return;}
 if(act==='drdelete'){if(confirm(L('Delete this Restore user? Native users are not affected.','این کاربر Restore حذف شود؟ کاربران اصلی تغییری نمی‌کنند.'))){await api('/api/dark-restore/'+enc(el.dataset.id),'DELETE');DR.data=null;await renderPage();}return;}
 if(act==='drdomaincheck'){
  const out=await api('/api/dark-restore/domains/'+enc(el.dataset.domain)+'/check','POST',{});DR.data=null;
  if(out.ssl_status!=='ready'&&out.apply_command)dialog(L('SSL issuance','صدور SSL'),'<div class="notice">'+L('DNS is checked. Certificate issuance is staged through the root CLI so the web process never receives root access.','DNS بررسی شد. صدور گواهی از CLI روت انجام می‌شود تا Worker وب دسترسی روت نگیرد.')+'</div><pre class="json-box">'+esc(out.apply_command)+'</pre>');
  else toast(L('Domain and SSL are ready.','دامنه و SSL آماده است.'));
  await renderPage();return;
 }
 return baseRunAction(act,el);
};
})();