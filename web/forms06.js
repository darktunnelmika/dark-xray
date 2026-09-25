'use strict';
/* Native DARK forms; no remote panel or embedded pages. */
const advancedEditor06=sectionForm,enginePage05=enginePage,runAction05=runAction;
async function saveSection06(section,value){await api('/api/settings/'+section,'PUT',{value});closeDialog();toast('در دیتابیس DARK ذخیره شد؛ وضعیت اعمال هسته را بررسی کن.');await refresh();}
function value06(f){return Object.fromEntries(f.entries());}
function rows06(head,rows){return `<div class="table-wrap"><table><thead><tr>${head.map(x=>`<th>${e(x)}</th>`).join('')}</tr></thead><tbody>${rows.join('')}</tbody></table></div>`;}
async function hostForm06(index=null){
 const data=(await api('/api/settings/hosts')).value,r=index===null?{inboundId:state.inbounds[0]?.id,port:443,enable:true}:data[index];
 if(!state.inbounds.length)throw Error('ابتدا اینباند بساز.');
 dialog(index===null?'افزودن هاست':'ویرایش هاست',`<div class="form-grid">${select('اینباند','inboundId',state.inbounds.map(i=>[i.id,i.remark+' : '+i.port]),r.inboundId)}${field('نام خروجی','remark',r.remark||'')}${field('IP / دامنهٔ بیرونی','address',r.address||'','text','required dir="ltr"')}${field('پورت بیرونی','port',r.port,'number','required min="1" max="65535"')}${field('SNI','sni',r.sni||'','text','dir="ltr"')}${field('Host','host',r.host||'','text','dir="ltr"')}${field('Path','path',r.path||'','text','dir="ltr"')}${field('ALPN','alpn',r.alpn||'','text','dir="ltr"')}${field('Fingerprint','fingerprint',r.fingerprint||'chrome')}${select('وضعیت','enable',[['true','فعال'],['false','مخفی از اشتراک']],String(r.enable!==false))}</div><div class="notice">هاست آدرس خروجی اشتراک را تغییر می‌دهد؛ تونل ایجاد نمی‌کند. ترتیب فهرست، ترتیب هاست‌ها در اشتراک است.</div>`,async f=>{
  const v=value06(f);v.enable=v.enable==='true';const row=DarkForms.host(v,r);if(index===null)data.push(row);else data[index]=row;await saveSection06('hosts',data);
 });
}
async function outboundForm06(index=null){
 const data=(await api('/api/settings/outbounds')).value,r=index===null?{tag:'dark-out-'+(data.length+1),protocol:'vless',settings:{}}:data[index];
 const server=r.settings?.vnext?.[0]||r.settings?.servers?.[0]||{},user=server.users?.[0]||{},st=r.streamSettings||{},sec=st.realitySettings||st.tlsSettings||{};
 dialog(index===null?'افزودن اوتباند':'ویرایش اوتباند',`<div class="form-grid">${field('Tag','tag',r.tag,'text','required dir="ltr"')}${select('پروتکل','protocol',['freedom','blackhole','vless','vmess','trojan','shadowsocks','socks','http'].map(x=>[x,x]),r.protocol)}${field('آدرس','address',server.address||'','text','dir="ltr"')}${field('پورت','port',server.port||443,'number','min="1" max="65535"')}${field('UUID یا Password','credential',user.id||server.password||user.pass||'','password','autocomplete="off" dir="ltr"')}${field('نام کاربری SOCKS / HTTP','username',user.user||'')}${select('انتقال','network',['tcp','ws','grpc','httpupgrade','xhttp'].map(x=>[x,x]),st.network||'tcp')}${select('امنیت','security',['none','tls','reality'].map(x=>[x,x]),st.security||'none')}${field('Path / gRPC serviceName','path',st.grpcSettings?.serviceName||st[st.network+'Settings']?.path||'','text','dir="ltr"')}${field('Host','host',st[st.network+'Settings']?.host||st.wsSettings?.headers?.Host||'','text','dir="ltr"')}${field('SNI','sni',sec.serverName||'','text','dir="ltr"')}${field('REALITY public key','publicKey',sec.password||sec.publicKey||'','text','dir="ltr"')}${field('Short ID','shortId',sec.shortId||'')}${field('Fingerprint','fingerprint',sec.fingerprint||'chrome')}${field('Flow','flow',user.flow||'')}${select('روش Shadowsocks','method',['aes-128-gcm','aes-256-gcm','chacha20-poly1305'].map(x=>[x,x]),server.method||'aes-128-gcm')}${field('Freedom domainStrategy','domainStrategy',r.settings?.domainStrategy||'AsIs')}${select('Blackhole response','response',[['none','none'],['http','http']],r.settings?.response?.type||'none')}${select('زنجیره از طریق','dialerProxy',[['','بدون زنجیره'],...data.filter(x=>x.tag!==r.tag).map(x=>[x.tag,x.tag])],st.sockopt?.dialerProxy||'')}</div><div class="notice">فیلدهای هر پروتکل بر اساس انتخابت مصرف می‌شوند. تنظیمات تخصصی و چندسروری از ویرایشگر JSON در دسترس‌اند؛ تست هسته پس از ذخیره لازم است.</div>`,async f=>{const row=DarkForms.outbound(value06(f),r);if(index===null)data.push(row);else data[index]=row;await saveSection06('outbounds',data);});
}
async function routingForm06(index=null){
 const data=(await api('/api/settings/routing')).value,outs=(await api('/api/settings/outbounds')).value,r=index===null?{type:'field'}:data.rules[index];
 const choices=[...outs.map(o=>[o.tag,o.tag]),...(data.balancers||[]).map(o=>['balancer:'+o.tag,'Balancer / '+o.tag])];
 dialog(index===null?'قاعدهٔ مسیر جدید':'ویرایش قاعدهٔ مسیر',`<div class="form-grid">${field('دامنه‌ها · با کاما جدا کن','domain',(r.domain||[]).join(','),'text','dir="ltr" placeholder="domain:example.com,geosite:private"')}${field('IP / CIDR','ip',(r.ip||[]).join(','),'text','dir="ltr"')}${field('تگ اینباندها','inboundTag',(r.inboundTag||[]).join(','),'text','dir="ltr"')}${field('IP مبدأ','source',(r.source||[]).join(','),'text','dir="ltr"')}${field('پورت / بازه','port',r.port||'','text','dir="ltr" placeholder="80,443,1000-2000"')}${select('شبکه','network',[['','همه'],['tcp','TCP'],['udp','UDP'],['tcp,udp','TCP / UDP']],r.network||'')}${field('پروتکل تشخیص‌داده‌شده','protocol',(r.protocol||[]).join(','))}${select('مقصد','destination',choices,r.balancerTag?'balancer:'+r.balancerTag:r.outboundTag||choices[0]?.[0])}</div><div class="notice">قواعد از بالا به پایین بررسی می‌شوند. شرط خالی یعنی قاعدهٔ عمومی؛ GeoIP / GeoSite به فایل‌های متناظر هسته نیاز دارند.</div>`,async f=>{const row=DarkForms.route(value06(f),r);data.rules=data.rules||[];if(index===null)data.rules.push(row);else data.rules[index]=row;await saveSection06('routing',data);});
}
sectionForm=async function(section){
 if(section==='ipguard'){
  const r=(await api('/api/settings/ipguard')).value;
  dialog('سیاست یکپارچهٔ محدودیت IP',`<div class="form-grid">${select('حالت محلی هاب','mode',[['observe','مشاهده و گزارش'],['enforce','اعمال با سرویس مستقل']],r.mode)}${select('حالت نودها','node_mode',[['observe','مشاهده و گزارش'],['enforce','اعمال با Guard محلی نود']],r.node_mode||r.mode)}${field('پنجرهٔ فعالیت (ثانیه)','window_seconds',r.window_seconds,'number','min="10" max="3600" required')}${field('مدت مسدودی (ثانیه)','ban_seconds',r.ban_seconds,'number','min="10" max="86400" required')}${field('IP / CIDRهای معاف · جداشده با کاما','exempt_ips',(r.exempt_ips||[]).join(','),'text','dir="ltr"')}</div><div class="notice warning">اعمال فقط پس از تأیید IP واقعی مبدأ و پورت‌ها در تنظیمات روت امکان‌پذیر است. روی تونل مبهم فعال نکن. تغییر حالت به مشاهده، مسدودی‌های همین سرویس را آزاد می‌کند.</div>`,async f=>{await saveSection06('ipguard',{mode:f.get('mode'),node_mode:f.get('node_mode')||f.get('mode'),window_seconds:num(f,'window_seconds'),ban_seconds:num(f,'ban_seconds'),exempt_ips:DarkForms.list(f.get('exempt_ips'))});});return;
 }
 if(section==='panel'){
  const r=(await api('/api/settings/panel')).value;
  dialog('تنظیمات پنل مستقل',`<div class="form-grid">${field('عنوان','title',r.title||'DARK XRAY')}${field('نشانی پشتیبانی','support_url',r.support_url||'','url','dir="ltr"')}</div><div class="notice">پورت و دامنه از ابزار darkxray domain مدیریت می‌شوند تا دسترسی هنگام تغییر آدرس گم نشود. تنظیم گواهی واقعی با certbot انجام می‌شود.</div>`,async f=>saveSection06('panel',{...r,title:f.get('title'),support_url:f.get('support_url')}));return;
 }
 await advancedEditor06(section);
};
enginePage=async function(){
 const section=state.page;
 if(!['hosts','outbounds','routing'].includes(section))return enginePage05();
 const data=(await api('/api/settings/'+section)).value,items=section==='routing'?data.rules||[]:data;
 const title=enginePages[section][0],kind={hosts:'host',outbounds:'outbound',routing:'route'}[section];
 const rows=items.map((r,i)=>`<tr><td class="mono">${i+1}</td><td><b>${e(section==='hosts'?r.remark||r.address:section==='outbounds'?r.tag:(r.domain||r.ip||r.inboundTag||['همه']).join(', '))}</b><br><span class="mono muted">${e(section==='hosts'?r.address+':'+r.port:section==='outbounds'?r.protocol:r.outboundTag||'Balancer: '+r.balancerTag)}</span></td><td><div class="row-actions">${button('ویرایش',kind+'edit','edit',`data-index="${i}"`)}${button('کپی','rowcopy','copy',`data-index="${i}" data-section="${section}"`)}${button('بالا','rowup','arrow',`data-index="${i}" data-section="${section}"`)}${button('پایین','rowdown','arrow',`data-index="${i}" data-section="${section}"`)}${button('حذف','rowdelete','trash',`data-index="${i}" data-section="${section}"`)}</div></td></tr>`);
 return heading(title,'فرم‌های مستقل DARK؛ تنظیمات پیشرفته هم بدون حذف جزئیات در دسترس‌اند.',button('افزودن',kind+'new','plus','',true)+button('JSON پیشرفته','advanced06','terminal',`data-section="${section}"`))+
 `<div class="notice">داده‌ها مستقیماً در دیتابیس همین پروژه ذخیره می‌شوند. اعمال موفق روی هسته از بخش همگام‌سازی قابل پیگیری است.</div><article class="panel">${items.length?rows06(['ردیف','مشخصات','عملیات'],rows):empty('هنوز موردی ثبت نشده است.')}</article>`;
};
ipPage=async function(){
 const root=isOwner();let status={},events={events:[],bans:[]};
 if(root)status=(await api('/api/ip-status')).engine;
 if(can('clients.ip'))events=await api('/api/ip/events');
 const states={applied:'سیاست بارگذاری و سرویس آماده است',observing:'مشاهده؛ بدون مسدودی',pending:'در انتظار همگام‌سازی',error:'خطا؛ اعمال تأیید نشده'};
 return heading('محدودیت IP و دستگاه','تغییر سقف مشتری به سیاست سرویس محلی متصل است؛ بدون نصب پنل دیگر.',root?button('تنظیم سیاست','ipsettings','settings','',true):'')+
 `<div class="ip-shell"><article class="panel"><span class="code-caption">DARK IP GUARD / LOCAL</span><h2>${e(states[status.state]||'دسترسی محدود')}</h2>${root?`<p>حالت درخواستی: <b>${e(status.requested_mode||status.mode)}</b></p><p>آخرین بررسی: ${date(status.checked_at)}</p><p>مشتری محدود: ${fa(status.limited_clients)}</p><p class="mono">${e((status.loaded_policy_hash||'').slice(0,20))}</p>${status.error?`<div class="notice error">${e(status.error)}</div>`:''}`:''}<div class="notice warning">تأیید فرمان فایروال به معنی تست عبور بسته نیست. برای اتصال تانلی، IP واقعی باید در همین محل اعمال قابل مشاهده باشد.</div></article><article class="panel"><h2>دامنهٔ اثر محدودیت</h2><p>چند اتصال از یک IP یک سهم دارند؛ چند دستگاه پشت NAT ممکن است یک سهم بگیرند. مسدودی IP روی پورت مشترک می‌تواند حساب دیگر با همان IP را هم متأثر کند.</p><p>HWID فقط دریافت اشتراک را محدود می‌کند. شمارش مشترک چندنود هنوز پیاده‌سازی نشده است.</p></article></div>`+
 `<article class="panel" style="margin-top:18px"><h2>مسدودی‌های اخیر</h2>${events.bans.length?rows06(['مشتری','IP','وضعیت / انقضا','عملیات'],events.bans.map(b=>`<tr><td>${e(b.client_id)}</td><td class="mono">${e(b.ip)}</td><td>${e(b.state)} / ${date(b.expires_at)}</td><td>${root?button('رفع مسدودی','unban06','power',`data-ip="${e(b.ip)}"`):'—'}</td></tr>`)):empty('مسدودی ثبت‌شدهٔ فعالی وجود ندارد.')}</article>`+
 `<article class="panel" style="margin-top:18px"><h2>رویدادهای محدودکننده</h2>${events.events.length?rows06(['زمان','مشتری','IP','رویداد'],events.events.slice(0,30).map(r=>`<tr><td>${date(r.at)}</td><td>${e(r.client_id)}</td><td class="mono">${e(r.ip)}</td><td>${e(r.kind)}<br>${e(r.detail)}</td></tr>`)):empty('هنوز رویدادی دریافت نشده است.')}</article><article class="panel" style="margin-top:18px">${clientTable(state.clients.filter(c=>can('clients.ip',c.owner)))}</article>`;
};
runAction=async function(act,el){
 const idx=el?.dataset.index===undefined?null:Number(el.dataset.index);
 if(act==='ipsettings')return sectionForm('ipguard');
 if(act==='advanced06')return advancedEditor06(el.dataset.section);
 if(act==='hostnew'||act==='hostedit')return hostForm06(idx);
 if(act==='outboundnew'||act==='outboundedit')return outboundForm06(idx);
 if(act==='routenew'||act==='routeedit')return routingForm06(idx);
 if(act==='unban06'){
  if(confirm('مسدودی این IP در پورت‌های تأییدشدهٔ DARK آزاد شود؟ ممکن است چند حساب پشت آن باشند.')){await api('/api/ip/unban','POST',{ip:el.dataset.ip});await refresh();}return;
 }
 if(['rowup','rowdown','rowcopy','rowdelete'].includes(act)){
  const section=el.dataset.section,data=(await api('/api/settings/'+section)).value,items=section==='routing'?data.rules:data;
  if(!items[idx])throw Error('ردیف تغییر کرده؛ صفحه را تازه کن.');
  if(act==='rowdelete'){if(!confirm('این ردیف حذف شود؟ وابستگی‌ها در سرور بررسی می‌شوند.'))return;items.splice(idx,1);}
  else if(act==='rowcopy'){const copy=structuredClone(items[idx]);if(section==='outbounds')copy.tag+='-copy-'+Date.now().toString(36);items.splice(idx+1,0,copy);}
  else{const next=idx+(act==='rowup'?-1:1);if(next<0||next>=items.length)return;[items[idx],items[next]]=[items[next],items[idx]];}
  return saveSection06(section,data);
 }
 return runAction05(act,el);
};
