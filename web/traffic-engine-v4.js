/* DARK XRAY Traffic Engine V4 — guided Outbound/Routing workspace. */
(function(){
'use strict';
if(typeof enginePage!=='function'||typeof runAction!=='function')return;
const baseEnginePage=enginePage,baseRunAction=runAction;
const L=(en,fa)=>((localStorage.getItem('dark_lang')||'en')==='fa'?fa:en);
state.te4=state.te4||{preview:null,preview_seq:0,config_signature:''};

const esc=v=>e(String(v??''));
function trafficTabs(){
 return `<nav class="te4-tabs">
  <button type="button" data-page="outbounds" class="${state.page==='outbounds'?'active':''}">${icon('arrow')}${L('Outbounds','اوتباندها')}</button>
  <button type="button" data-page="routing" class="${state.page==='routing'?'active':''}">${icon('node')}${L('Routing & Balancers','مسیریابی و بالانسرها')}</button>
  <button type="button" data-act="te4xray">${icon('terminal')}${L('Xray Control','کنترل Xray')}</button>
 </nav>`;
}
async function teData(){
 const [status,outbounds,routing,observatory,core]=await Promise.all([
  api('/api/traffic-engine'),
  api('/api/settings/outbounds').then(x=>x.value||[]),
  api('/api/settings/routing').then(x=>x.value||{}),
  api('/api/settings/observatory').then(x=>x.value||{}),
  api('/api/core/state')
 ]);
 return {status,outbounds,routing,observatory,core};
}
function endpoint(o){
 const s=o?.settings||{},p=String(o?.protocol||'');
 if(p==='vless'||p==='hysteria')return s.address&&s.port?`${s.address}:${s.port}`:'';
 if(p==='vmess')return s.vnext?.[0]?.address?`${s.vnext[0].address}:${s.vnext[0].port||''}`:'';
 if(['trojan','shadowsocks','socks','http'].includes(p))return s.servers?.[0]?.address?`${s.servers[0].address}:${s.servers[0].port||''}`:'';
 if(p==='wireguard')return s.peers?.[0]?.endpoint||'';
 if(p==='loopback')return s.inboundTag||'';
 return '';
}
function metric(label,value,sub=''){return `<div class="te4-metric"><span>${esc(label)}</span><b>${esc(value)}</b>${sub?`<small>${esc(sub)}</small>`:''}</div>`;}
function chip(text,tone=''){return `<span class="te4-chip ${tone}">${esc(text)}</span>`;}
function summary(d){
 const s=d.status,r=s.routing||{},chainCount=(s.outbounds||[]).filter(x=>x.dials_via).length,dirty=!!d.core.dirty;
 return `<section class="te4-summary">
  ${metric(L('Outbounds','اوتباند'),fa((s.outbounds||[]).length),L('first = default','اولی = پیش‌فرض'))}
  ${metric(L('Routing rules','Rule روتینگ'),fa(r.rule_count||0),r.domain_strategy||'AsIs')}
  ${metric(L('Balancers','بالانسر'),fa((s.balancers||[]).length),L('گزینشگر = پیشوند','گزینشگر = پیشوند'))}
  ${metric(L('Chained outbounds','خروجی زنجیره‌ای'),fa(chainCount),L('dialerProxy','dialerProxy'))}
  ${metric(L('Default outbound','خروجی پیش‌فرض'),r.default_outbound||'—',L('no-rule fallback','Fallback بدون Rule'))}
  ${metric(L('Runtime config','پیکربندی اجرا'),dirty?L('DIRTY','اعمال‌نشده'):L('CLEAN','همسان'),d.core.running?L('Xray running','Xray روشن'):L('Xray stopped','Xray خاموش'))}
 </section>`;
}
function warnings(d){
 const rows=d.status.warnings||[];
 return rows.length?`<div class="te4-warnings">${rows.map(x=>`<div class="notice warning">${esc(x)}</div>`).join('')}</div>`:'';
}
function endpointMeta(o){
 const st=o?.streamSettings||{},so=st.sockopt||{},bits=[String(o.protocol||'').toUpperCase()];
 const ep=endpoint(o);if(ep)bits.push(ep);
 if(st.network)bits.push(String(st.network).toUpperCase());
 if(st.security&&st.security!=='none')bits.push(String(st.security).toUpperCase());
 if(so.dialerProxy)bits.push('via '+so.dialerProxy);
 return bits.join(' · ');
}
function dependencyText(m){
 const out=[];
 if(m.rule_refs?.length)out.push(L('Rules','Rule')+' #'+m.rule_refs.join(', #'));
 if(m.balancer_refs?.length)out.push(L('Balancer pools','مجموعه بالانسر')+': '+m.balancer_refs.join(', '));
 if(m.fallback_refs?.length)out.push('Fallback: '+m.fallback_refs.join(', '));
 if(m.dialed_by?.length)out.push(L('Dialed by','Dial شده توسط')+': '+m.dialed_by.join(', '));
 return out;
}
function outboundCard(o,index,meta){
 const deps=dependencyText(meta),chain=meta.dials_via;
 return `<article class="panel te4-out ${meta.default?'default':''}">
  <header><div><small>#${index+1} · ${esc(o.protocol||'')}</small><h3>${esc(o.tag)}</h3></div><div class="te4-badges">${meta.default?chip(L('DEFAULT','پیش‌فرض'),'good'):''}${meta.observed?chip('OBSERVED','info'):''}${chain?chip('CHAIN','warn'):''}</div></header>
  <div class="te4-out-meta"><code>${esc(endpointMeta(o))}</code></div>
  ${chain?`<div class="te4-chain"><span>${L('DIAL PATH','مسیر Dial')}</span><b>${esc(o.tag)} → ${esc(chain)}</b></div>`:''}
  <div class="te4-deps"><span>${L('DEPENDENCIES','وابستگی‌ها')}</span>${deps.length?deps.map(x=>`<small>${esc(x)}</small>`).join(''):`<small>${L('No routing/balancer dependency','وابستگی مسیریابی/بالانسر ندارد')}</small>`}</div>
  <footer>
   ${!meta.default?button(L('Make default','پیش‌فرض شود'),'te4default','check',`data-index="${index}"`,true):''}
   ${button(L('Edit','ویرایش'),'te4outedit','edit',`data-index="${index}"`)}
   ${button(L('Clone','کپی'),'te4outclone','copy',`data-index="${index}"`)}
   ${button(L('Advanced JSON','JSON پیشرفته'),'te4outraw','terminal',`data-index="${index}"`)}
   ${button(L('Delete','حذف'),'xv2outdelete','trash',`data-index="${index}"`)}
  </footer>
 </article>`;
}
function graph(d){
 const rows=d.status.outbounds||[];
 return `<section class="panel te4-graph"><header><div><small>DARK / DEPENDENCY GRAPH</small><h2>${L('Traffic dependency graph','گراف وابستگی ترافیک')}</h2></div></header>
  <div class="te4-graph-list">${rows.map(x=>`<div><b>${esc(x.tag)}</b><span>→</span><code>${esc(x.dials_via||L('network / direct dial','شبکه / Dial مستقیم'))}</code><small>${esc(dependencyText(x).join(' · ')||L('no consumers','بدون مصرف‌کننده'))}</small></div>`).join('')}</div>
  <div class="notice">${L('Deleting an outbound is refused when a routing rule, fallback, dialerProxy chain, or the last candidate of a balancer would be broken.','حذف اوتباند وقتی یک قانون، مسیر جایگزین، زنجیره dialerProxy یا آخرین گزینهٔ یک بالانسر را خراب کند، توسط هستهٔ مدیریتی رد می‌شود.')}</div>
 </section>`;
}
function outboundsPage(d){
 const list=d.outbounds||[],meta=new Map((d.status.outbounds||[]).map(x=>[x.tag,x]));
 return heading(L('Traffic Engine · Outbounds','موتور ترافیک · اوتباندها'),L('Build egress paths, chains and proxy transports with explicit dependency visibility.','مسیرهای خروج، زنجیره‌ها و انتقال پروکسی را با وابستگی‌های کاملاً شفاف مدیریت کن.'),
  `${button(L('Import link','Import لینک'),'te4outimport','download')}${button(L('New outbound','اوتباند جدید'),'te4outnew','plus','',true)}`)+
  `<div class="te4">${trafficTabs()}${summary(d)}${warnings(d)}
   <div class="notice">${L('Xray uses the first outbound when no routing rule matches. Use Make default to change that behavior explicitly.','اگر هیچ قانونی تطبیق نشود، Xray از اولین اوتباند استفاده می‌کند. برای تغییر این رفتار از «پیش‌فرض شود» استفاده کن.')}</div>
   <section class="te4-out-grid">${list.length?list.map((o,i)=>outboundCard(o,i,meta.get(o.tag)||{})).join(''):empty(L('No outbound configured.','اوتباندی تنظیم نشده است.'))}</section>
   ${graph(d)}
  </div>`;
}
function valList(v){return Array.isArray(v)?v.join(', '):String(v||'');}
function conditionRows(r){
 const defs=[
  ['domain',L('Domain','دامنه')],['ip',L('Target IP','IP مقصد')],['port',L('Target port','پورت مقصد')],
  ['sourceIP',L('Source IP','IP مبدا')],['source',L('Source IP','IP مبدا')],['sourcePort',L('Source port','پورت مبدا')],
  ['localIP',L('Local IP','IP محلی')],['localPort',L('Local port','پورت محلی')],['network',L('Network','شبکه')],
  ['user',L('User','کاربر')],['inboundTag',L('Inbound','اینباند')],['protocol',L('Protocol','پروتکل')],
  ['process',L('Process','Process')],['vlessRoute','VLESS Route']
 ];
 const seen=new Set(),rows=[];
 for(const [key,label] of defs){
  if(seen.has(key)||r[key]===undefined||r[key]===''||(Array.isArray(r[key])&&!r[key].length))continue;
  seen.add(key);rows.push(`<span><i>${esc(label)}</i><b>${esc(valList(r[key]))}</b></span>`);
 }
 if(r.attrs&&Object.keys(r.attrs).length)rows.push(`<span><i>HTTP attrs</i><b>${esc(JSON.stringify(r.attrs))}</b></span>`);
 return rows.length?rows.join(''):`<span><i>${L('Match','شرط')}</i><b>${L('Catch-all','همه ترافیک')}</b></span>`;
}
function ruleCard(r,index){
 const target=r.outboundTag?`OUT → ${r.outboundTag}`:`BAL → ${r.balancerTag||'—'}`;
 return `<article class="panel te4-rule"><header><div><small>#${index+1}${r.ruleTag?' · '+esc(r.ruleTag):''}</small><h3>${esc(target)}</h3></div><span class="te4-order">${fa(index+1)}</span></header>
  <div class="te4-conditions">${conditionRows(r)}</div>
  <footer>${button('↑','xv2ruleup','arrow',`data-index="${index}"`)}${button('↓','xv2ruledown','arrow',`data-index="${index}"`)}${button(L('Edit','ویرایش'),'te4ruleedit','edit',`data-index="${index}"`)}${button(L('Delete','حذف'),'xv2ruledelete','trash',`data-index="${index}"`)}</footer>
 </article>`;
}
function balancerCard(b){
 const obsMissing=(b.candidates||[]).filter(x=>!(b.observed_candidates||[]).includes(x));
 return `<article class="panel te4-bal ${obsMissing.length&&b.strategy==='leastPing'?'warn':''}">
  <header><div><small>${esc(String(b.strategy||'random').toUpperCase())}</small><h3>${esc(b.tag)}</h3></div>${b.rule_refs?.length?chip(L('USED','در حال استفاده'),'good'):chip(L('UNUSED','بدون Rule'))}</header>
  <div class="te4-bal-row"><span>${L('PREFIX SELECTORS','گزینشگرهای پیشوندی')}</span><div>${(b.selectors||[]).map(x=>chip(x,'info')).join('')}</div></div>
  <div class="te4-bal-row"><span>${L('MATCHED OUTBOUNDS','اوتباندهای تطبیق‌یافته')}</span><div>${(b.candidates||[]).length?(b.candidates||[]).map(x=>chip(x,(b.observed_candidates||[]).includes(x)?'good':'')).join(''):`<b>—</b>`}</div></div>
  ${b.fallback_tag?`<div class="te4-chain"><span>FALLBACK</span><b>${esc(b.fallback_tag)}</b></div>`:''}
  ${b.strategy==='leastPing'&&obsMissing.length?`<div class="notice warning">${L('Some candidates are outside Observatory selectors: ','بعضی گزینه‌ها خارج از محدوده Observatory هستند: ')}${esc(obsMissing.join(', '))}</div>`:''}
  <footer>${button(L('Edit','ویرایش'),'te4baledit','edit',`data-index="${(state.te4.data?.routing?.balancers||[]).findIndex(x=>x.tag===b.tag)}"`)}${button(L('Delete','حذف'),'xv2baldelete','trash',`data-index="${(state.te4.data?.routing?.balancers||[]).findIndex(x=>x.tag===b.tag)}"`)}</footer>
 </article>`;
}
function previewForm(d){
 const inboundOpts=[['',L('Any inbound','هر Inbound')],...(state.inbounds||[]).map(x=>[x.tag||('inbound-'+x.id),x.remark?x.remark+' · '+(x.tag||x.id):x.tag||String(x.id)])];
 return `<section class="panel te4-preview"><header><div><small>DARK / ROUTE PREVIEW</small><h2>${L('Preview a routing decision','پیش‌نمایش تصمیم مسیریابی')}</h2><p>${L('No traffic is sent. Literal rules are evaluated against the saved config. Geodata/DNS/runtime balancer choices are never guessed.','هیچ ترافیکی ارسال نمی‌شود. قوانین صریح روی پیکربندی ذخیره‌شده بررسی می‌شوند و GeoData/DNS یا انتخاب زندهٔ بالانسر حدس زده نمی‌شود.')}</p></div></header>
  <form id="te4-preview-form">
   <div class="te4-preview-grid">
    <label><span>${L('Domain','دامنه')}</span><input class="field-input" name="domain" dir="ltr" placeholder="example.com"></label>
    <label><span>${L('Target IP','IP مقصد')}</span><input class="field-input" name="ip" dir="ltr" placeholder="1.1.1.1"></label>
    <label><span>${L('Target port','پورت مقصد')}</span><input class="field-input" name="port" type="number" min="0" max="65535" value="443"></label>
    <label><span>${L('Network','شبکه')}</span><select name="network"><option value="tcp">TCP</option><option value="udp">UDP</option></select></label>
    <label><span>${L('Inbound','اینباند')}</span><select name="inbound_tag">${inboundOpts.map(([v,l])=>`<option value="${esc(v)}">${esc(l)}</option>`).join('')}</select></label>
    <label><span>${L('Detected protocol','پروتکل تشخیص‌داده‌شده')}</span><select name="protocol"><option value="">${L('Unknown / any','نامشخص')}</option><option value="http">http</option><option value="tls">tls</option><option value="quic">quic</option><option value="bittorrent">bittorrent</option></select></label>
    <label><span>${L('User / email','کاربر / ایمیل')}</span><input class="field-input" name="user" dir="ltr"></label>
    <details class="te4-preview-advanced"><summary>${L('Advanced source context','زمینهٔ مبدأ پیشرفته')}</summary><div>
      <label><span>Source IP</span><input class="field-input" name="source_ip" dir="ltr"></label>
      <label><span>Source Port</span><input class="field-input" name="source_port" type="number" min="0" max="65535" value="0"></label>
      <label><span>Local IP</span><input class="field-input" name="local_ip" dir="ltr"></label>
      <label><span>Local Port</span><input class="field-input" name="local_port" type="number" min="0" max="65535" value="0"></label>
      <label><span>VLESS Route</span><input class="field-input" name="vless_route" type="number" min="0" max="65535" value="0"></label>
      <label><span>Process</span><input class="field-input" name="process" dir="ltr"></label>
      <label class="wide"><span>HTTP attrs JSON</span><textarea name="attrs" spellcheck="false" dir="ltr" placeholder='{"accept":"text/html"}'></textarea></label>
    </div></details>
   </div>
   <footer><button type="button" class="btn btn-primary" data-act="te4preview">${icon('activity')}${L('Preview route','پیش‌نمایش مسیر')}</button></footer>
  </form>
  <div id="te4-preview-result">${state.te4.preview?previewResult(state.te4.preview):`<div class="te4-preview-empty">${L('Enter a synthetic connection and preview which saved rule would receive it.','یک اتصال فرضی وارد کن تا ببینی کدام Rule ذخیره‌شده آن را می‌گیرد.')}</div>`}</div>
 </section>`;
}
function previewResult(r){
 const tone=r.result==='indeterminate'?'warn':r.result==='matched'?'good':'info';
 let route=[];
 if(r.rule_index)route.push(`RULE #${r.rule_index}`);
 else route.push(L('DEFAULT','پیش‌فرض'));
 if(r.target_type==='balancer')route.push('BAL '+r.target);
 if(r.selected_outbound)route.push('OUT '+r.selected_outbound);
 else if(r.target_type==='balancer')route.push(L('runtime selection','انتخاب زمان اجرا'));
 for(const tag of (r.chain||[]).slice(1))route.push('via '+tag);
 route.push(L('INTERNET / TARGET','اینترنت / مقصد'));
 return `<div class="te4-preview-result ${tone}">
  <header><div><small>CONFIG PREVIEW · ${esc(String(r.result||'').toUpperCase())}</small><h3>${esc(route.join(' → '))}</h3></div>${chip(r.live_core_verified?'LIVE CORE':'SAVED CONFIG',r.live_core_verified?'good':'info')}</header>
  ${r.reason?`<div class="notice ${r.result==='indeterminate'?'warning':''}">${esc(r.reason)}</div>`:''}
  ${r.candidates?.length?`<div class="te4-bal-row"><span>${L('BALANCER CANDIDATES','گزینه‌های بالانسر')}</span><div>${r.candidates.map(x=>chip(x,'info')).join('')}</div></div>`:''}
  <div class="te4-trace">${(r.trace||[]).map(x=>`<span class="${x.result}"><b>#${x.index}</b><i>${esc(x.rule_tag||'')}</i><strong>${esc(x.result)}</strong><code>${esc(x.target||'')}</code></span>`).join('')}</div>
 </div>`;
}
function observatoryCard(d){
 const o=d.status.observatory||{};
 return `<article class="panel te4-observatory"><header><div><small>DARK / OBSERVATORY</small><h2>Observatory</h2></div>${chip(o.enabled?L('CONFIGURED','تنظیم‌شده'):L('OFF','خاموش'),o.enabled?'good':'')}</header>
 <div class="te4-ob-kv"><div><span>subjectSelector</span><b>${esc((o.selectors||[]).join(', ')||'—')}</b></div><div><span>probeURL</span><b>${esc(o.probe_url||'—')}</b></div><div><span>probeInterval</span><b>${esc(o.probe_interval||'—')}</b></div></div>
 <div class="notice">${L('Traffic Engine does not invent live latency/health. leastPing final selection remains a runtime Xray decision unless a verified live API is available.','موتور ترافیک برای تأخیر/سلامت عدد ساختگی نمی‌سازد. انتخاب نهایی leastPing تا وقتی API زنده و تأییدشده‌ای نداشته باشیم، تصمیم زمان اجرای Xray است.')}</div>
 <footer>${button(L('Edit Observatory','ویرایش Observatory'),'te4obsedit','edit','',true)}</footer></article>`;
}
function routingPage(d){
 state.te4.data=d;
 const rules=d.routing.rules||[],bals=d.status.balancers||[];
 return heading(L('Traffic Engine · Routing','موتور ترافیک · مسیریابی'),L('Model the complete traffic decision: match conditions → outbound/balancer → optional dial chain.','تصمیم کامل ترافیک را ببین: شرایط تطبیق ← اوتباند/بالانسر ← زنجیرهٔ اختیاری.'),
  `${button(L('Routing settings','تنظیمات Routing'),'te4routesettings','settings')}${button(L('Add rule','افزودن Rule'),'te4rulenew','plus','',true)}`)+
  `<div class="te4">${trafficTabs()}${summary(d)}${warnings(d)}
   ${previewForm(d)}
   <section class="te4-section"><header><div><small>DARK / RULE ORDER</small><h2>${L('Routing rules','قوانین مسیریابی')}</h2><p>${L('First matching rule wins. Empty conditions mean catch-all.','اولین قانونی که تطبیق پیدا کند اجرا می‌شود؛ شرط خالی یعنی شامل همه.')}</p></div><span>${fa(rules.length)}</span></header>
    <div class="te4-rule-list">${rules.length?rules.map(ruleCard).join(''):empty(L('No routing rules. Xray will use the first outbound.','قانون مسیریابی وجود ندارد؛ Xray از اولین اوتباند استفاده می‌کند.'))}</div>
   </section>
   <section class="te4-section"><header><div><small>DARK / BALANCERS</small><h2>${L('Balancer pools','مجموعه‌های بالانسر')}</h2><p>${L('Selectors use Xray prefix matching, not exact-tag membership.','گزینشگرها در Xray با پیشوند تطبیق داده می‌شوند، نه با عضویت دقیق تگ.')}</p></div><div>${button(L('New balancer','بالانسر جدید'),'te4balnew','plus','',true)}</div></header>
    <div class="te4-bal-grid">${bals.length?bals.map(balancerCard).join(''):empty(L('No balancer configured.','بالانسری تنظیم نشده است.'))}</div>
   </section>
   ${observatoryCard(d)}
  </div>`;
}
enginePage=async function(){
 if(!['outbounds','routing'].includes(state.page))return baseEnginePage();
 const d=await teData(),signature=JSON.stringify({outbounds:d.outbounds,routing:d.routing,observatory:d.observatory});
 if(state.te4.config_signature&&state.te4.config_signature!==signature)state.te4.preview=null;
 state.te4.config_signature=signature;state.te4.data=d;
 return state.page==='outbounds'?outboundsPage(d):routingPage(d);
};

async function makeDefault(index){
 const list=(await api('/api/settings/outbounds')).value||[];
 if(index<=0||index>=list.length)return;
 const tag=list[index]?.tag;if(!confirm(L('Make this the first/default outbound? Unmatched traffic will use it.','این اوتباند اولین/پیش‌فرض شود؟ ترافیکی که با هیچ قانونی تطبیق نکند از آن استفاده می‌کند.')))return;
 const [item]=list.splice(index,1);list.unshift(item);await api('/api/settings/outbounds','PUT',{value:list});toast(L('Default outbound changed to ','اوتباند پیش‌فرض تغییر کرد به ')+tag);await refresh();
}
runAction=async function(act,el){
 const guided=globalThis.DarkXrayGuidedV3;
 if(act.startsWith('te4')&&['te4outnew','te4outedit','te4outclone','te4outimport','te4outraw','te4routesettings','te4rulenew','te4ruleedit','te4balnew','te4baledit','te4obsedit'].includes(act)&&!guided)throw Error(L('Guided Xray editor module is unavailable.','ماژول هدایت‌شدهٔ Xray در دسترس نیست.'));
 if(act==='te4xray'){await go('xray');return;}
 if(act==='te4default'){await makeDefault(Number(el.dataset.index));return;}
 if(act==='te4outnew'){await guided.openOutbound();return;}
 if(act==='te4outedit'){await guided.openOutbound(Number(el.dataset.index));return;}
 if(act==='te4outclone'){await guided.openOutbound(Number(el.dataset.index),true);return;}
 if(act==='te4outimport'){await guided.openImport();return;}
 if(act==='te4outraw'){await guided.openRawOutbound(Number(el.dataset.index));return;}
 if(act==='te4routesettings'){await guided.openRouteSettings();return;}
 if(act==='te4rulenew'){await guided.openRule();return;}
 if(act==='te4ruleedit'){await guided.openRule(Number(el.dataset.index));return;}
 if(act==='te4balnew'){await guided.openBalancer();return;}
 if(act==='te4baledit'){await guided.openBalancer(Number(el.dataset.index));return;}
 if(act==='te4obsedit'){await guided.openObservatory();return;}
 if(act==='te4preview'){await previewFromForm(el.closest('#te4-preview-form'));return;}
 return baseRunAction(act,el);
};

async function previewFromForm(form){
 if(!form)throw Error(L('Route preview form is unavailable.','فرم پیش‌نمایش مسیر در دسترس نیست.'));
 const submit=form.querySelector('[data-act="te4preview"]');if(submit)submit.disabled=true;
 try{
  const fd=new FormData(form),attrsText=String(fd.get('attrs')||'').trim();let attrs={};
  if(attrsText){try{attrs=JSON.parse(attrsText);}catch{throw Error(L('HTTP attrs must be valid JSON.','Attrs باید JSON معتبر باشد.'));}if(!attrs||Array.isArray(attrs)||typeof attrs!=='object')throw Error(L('HTTP attrs must be a JSON object.','Attrs باید JSON Object باشد.'));}
  const body={
   domain:String(fd.get('domain')||'').trim(),ip:String(fd.get('ip')||'').trim(),port:Number(fd.get('port')||0),
   source_ip:String(fd.get('source_ip')||'').trim(),source_port:Number(fd.get('source_port')||0),
   local_ip:String(fd.get('local_ip')||'').trim(),local_port:Number(fd.get('local_port')||0),
   network:String(fd.get('network')||'tcp'),protocol:String(fd.get('protocol')||'').trim(),
   user:String(fd.get('user')||'').trim(),inbound_tag:String(fd.get('inbound_tag')||'').trim(),
   process:String(fd.get('process')||'').trim(),vless_route:Number(fd.get('vless_route')||0),attrs
  };
  state.te4.preview=await api('/api/traffic-engine/preview','POST',body);state.te4.preview_seq=(state.te4.preview_seq||0)+1;
  const box=document.querySelector('#te4-preview-result');if(box)box.innerHTML=previewResult(state.te4.preview);
  return state.te4.preview;
 }finally{if(submit?.isConnected)submit.disabled=false;}
}
globalThis.DarkTrafficEngineV4={previewResult,endpoint,conditionRows,previewFromForm};
})();