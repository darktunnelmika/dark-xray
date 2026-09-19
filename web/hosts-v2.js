/* DARK XRAY Public Endpoints V3 — guided customer-facing endpoint editor. */
(function(){
'use strict';
if(typeof enginePage!=='function'||typeof runAction!=='function')return;
const baseEnginePage=enginePage,baseRunAction=runAction;
state.hv2=state.hv2||{inbound:'all',search:''};
const L=(en,fa)=>((localStorage.getItem('dark_lang')||'en')==='fa'?fa:en);
const clone=v=>JSON.parse(JSON.stringify(v||{}));
const escAttr=v=>e(String(v??'')).replace(/"/g,'&quot;');
function inboundFor(id){return state.inbounds.find(x=>Number(x.id)===Number(id))||null;}
function inboundMeta(i){const st=i?.streamSettings||{};return {protocol:i?.protocol||'',network:i?.network||st.network||'tcp',security:i?.security||st.security||'none',port:Number(i?.port||0),stream:st};}
function endpointMode(h,ib){
 const m=inboundMeta(ib);
 if((h.security&&h.security!=='same')||h.allowInsecure||h.alpn||h.finalMask||h.mihomoIpVersion||(h.excludeFromSubTypes||[]).length||h.overrideSniFromAddress||h.keepSniBlank)return'advanced';
 if(h.sni||h.host||h.path||(h.port&&Number(h.port)!==m.port))return'tunnel';
 return'direct';
}
function sniMode(h){if(h.keepSniBlank)return'blank';if(h.overrideSniFromAddress)return'address';if(h.sni)return'manual';return'inherit';}
function baseSni(ib){const m=inboundMeta(ib),st=m.stream;if(m.security==='reality')return st.realitySettings?.serverName||st.realitySettings?.serverNames?.[0]||'';if(m.security==='tls')return st.tlsSettings?.serverName||'';return'';}
function transportDefaults(ib){
 const m=inboundMeta(ib),st=m.stream;
 if(m.network==='ws')return {host:st.wsSettings?.headers?.Host||'',path:st.wsSettings?.path||'/'};
 if(m.network==='httpupgrade')return {host:st.httpupgradeSettings?.host||'',path:st.httpupgradeSettings?.path||'/'};
 if(m.network==='xhttp')return {host:st.xhttpSettings?.host||'',path:st.xhttpSettings?.path||'/'};
 return {host:'',path:''};
}
function normalizeEndpoint(raw,ib){
 if(!ib)throw Error(L('Choose an inbound.','یک اینباند انتخاب کن.'));
 const m=inboundMeta(ib),mode=raw.mode||'direct';
 const address=String(raw.address||'').trim(),port=Number(raw.port);
 if(!address||address.length>253||/[\s\/?#@]/.test(address))throw Error(L('Endpoint address must be a plain IP or domain.','آدرس Endpoint باید IP یا دامنه ساده باشد.'));
 if(!Number.isInteger(port)||port<1||port>65535)throw Error(L('Endpoint port must be 1..65535.','پورت Endpoint باید بین ۱ تا ۶۵۵۳۵ باشد.'));
 let out={inboundId:Number(ib.id),address,port,remark:String(raw.remark||'').trim(),security:'same',sni:'',overrideSniFromAddress:false,keepSniBlank:false,host:'',path:'',alpn:'',fingerprint:'',allowInsecure:false,finalMask:'',mihomoIpVersion:'',excludeFromSubTypes:[],enable:raw.enable!==false};
 if(mode==='tunnel'||mode==='advanced'){
   const sd=String(raw.sniMode||'inherit');
   if(sd==='manual'){out.sni=String(raw.sni||'').trim();if(!out.sni&&m.security!=='none')throw Error(L('Manual SNI needs a value.','برای SNI دستی باید مقدار وارد شود.'));}
   else if(sd==='address')out.overrideSniFromAddress=true;
   else if(sd==='blank')out.keepSniBlank=true;
   if(['ws','httpupgrade','xhttp'].includes(m.network)){out.host=String(raw.host||'').trim();out.path=String(raw.path||'').trim();}
 }
 if(mode==='advanced'){
   out.security=['same','tls','none'].includes(raw.security)?raw.security:'same';
   out.alpn=String(raw.alpn||'').trim();
   out.fingerprint=String(raw.fingerprint||'').trim();
   out.allowInsecure=!!raw.allowInsecure;
   out.finalMask=String(raw.finalMask||'').trim();
   out.mihomoIpVersion=String(raw.mihomoIpVersion||'');
   out.excludeFromSubTypes=[...new Set(raw.excludeFromSubTypes||[])].filter(x=>['raw','json','clash'].includes(x));
   if(out.finalMask){let fm;try{fm=JSON.parse(out.finalMask);}catch{throw Error(L('Final Mask must be valid JSON.','Final Mask باید JSON معتبر باشد.'));}if(!fm||Array.isArray(fm)||typeof fm!=='object'||!Object.keys(fm).length)throw Error(L('Final Mask must be a non-empty JSON object.','Final Mask باید یک آبجکت JSON غیرخالی باشد.'));out.finalMask=JSON.stringify(fm);}
 }
 return out;
}
function previewModel(h,ib){
 const m=inboundMeta(ib),td=transportDefaults(ib),sec=h.security&&h.security!=='same'?h.security:m.security;
 let sni='';if(sec!=='none'){if(h.keepSniBlank)sni='';else if(h.overrideSniFromAddress)sni=h.address||'';else sni=h.sni||baseSni(ib);}
 const host=h.host||td.host,path=h.path||td.path;
 const params=[['type',m.network],['security',sec]];
 if(sni)params.push(['sni',sni]);
 if(['ws','httpupgrade','xhttp'].includes(m.network)){if(host)params.push(['host',host]);if(path)params.push(['path',path]);}
 if(m.network==='grpc'){const svc=m.stream.grpcSettings?.serviceName||'';if(svc)params.push(['serviceName',svc]);}
 if(h.alpn&&sec!=='none')params.push(['alpn',h.alpn]);
 if(h.fingerprint&&sec!=='none')params.push(['fp',h.fingerprint]);
 if(h.allowInsecure&&sec!=='none')params.push(['allowInsecure','1']);
 const scheme=['vless','trojan','vmess','shadowsocks'].includes(m.protocol)?m.protocol:'xray';
 const credential=m.protocol==='trojan'?'CLIENT-PASSWORD':'CLIENT-CREDENTIAL';
 const hp=(String(h.address||'').includes(':')?'['+h.address+']':h.address)+':'+h.port;
 const query=params.map(([k,v])=>encodeURIComponent(k)+'='+encodeURIComponent(v)).join('&');
 return {mode:h.mode||endpointMode(h,ib),protocol:m.protocol,network:m.network,security:sec,sni,host,path,endpoint:hp,preview:scheme+'://'+credential+'@'+hp+(query?'?'+query:'')};
}
function tags(h,ib){const p=previewModel({...h,mode:endpointMode(h,ib)},ib),t=[p.network.toUpperCase(),p.security.toUpperCase()];if(p.sni)t.push('SNI '+p.sni);if(p.host)t.push('HOST '+p.host);if(p.path)t.push('PATH '+p.path);if(h.allowInsecure)t.push('INSECURE');if(h.excludeFromSubTypes?.length)t.push('EXCLUDE '+h.excludeFromSubTypes.join(','));return t;}
function modeLabel(mode){return mode==='direct'?L('DIRECT','مستقیم'):mode==='tunnel'?L('TUNNEL / CDN','تانل / CDN'):L('ADVANCED','پیشرفته');}
function modePicker(current){return `<div class="hv3-modes">${[['direct',L('Direct','مستقیم'),L('Public IP/domain, inherit inbound transport and security.','IP/دامنه عمومی با تنظیمات خود اینباند.')],['tunnel',L('Tunnel / CDN','تانل / CDN'),L('Change public endpoint and client-facing SNI/Host/Path.','Endpoint بیرونی و SNI/Host/Path مشتری را تنظیم کن.')],['advanced',L('Advanced override','Override پیشرفته'),L('Only for different TLS/security or format-specific behavior.','فقط برای TLS/امنیت متفاوت یا رفتار فرمت‌ها.')]].map(([v,a,b])=>`<label class="${current===v?'active':''}"><input type="radio" name="mode" value="${v}" ${current===v?'checked':''}><span><b>${a}</b><small>${b}</small></span></label>`).join('')}</div>`;}
function pageCard(h,index){
 const ib=inboundFor(h.inboundId),m=inboundMeta(ib),mode=endpointMode(h,ib);
 return `<article class="panel hv3-card ${h.enable===false?'disabled':''}"><header><div><small>${e(modeLabel(mode))} · ${e(m.protocol.toUpperCase())}</small><h3>${e(h.remark||ib?.remark||ib?.tag||('Endpoint '+(index+1)))}</h3></div><span class="tag ${h.enable===false?'red':'green'}">${h.enable===false?L('Disabled','غیرفعال'):L('Active','فعال')}</span></header><div class="hv3-endpoint"><span>${L('CUSTOMER CONNECTS TO','اتصال مشتری')}</span><b class="mono">${e(h.address)}:${e(h.port)}</b></div><div class="hv3-source"><span>${L('Source inbound','اینباند مبدا')}</span><b>${e(ib?.remark||ib?.tag||('#'+h.inboundId))}</b><small>${e(m.protocol)} · ${e(m.network)} · ${e(m.security)} · :${e(m.port)}</small></div><div class="hv2-tags">${tags(h,ib).map(x=>`<span>${e(x)}</span>`).join('')}</div><footer>${button(L('Edit','ویرایش'),'hv2edit','edit',`data-index="${index}"`,true)}${button(L('Clone','کپی'),'hv2clone','copy',`data-index="${index}"`)}${button(L('Delete','حذف'),'hv2delete','trash',`data-index="${index}"`)}</footer></article>`;
}
async function hostsPage(){
 const hosts=(await api('/api/settings/hosts')).value||[],q=(state.hv2.search||'').toLowerCase();
 const rows=hosts.map((h,i)=>({...h,_index:i})).filter(h=>(state.hv2.inbound==='all'||String(h.inboundId)===String(state.hv2.inbound))&&(!q||JSON.stringify(h).toLowerCase().includes(q)));
 return heading(L('Public Endpoints','آدرس‌های عمومی'),L('Define exactly what address, port and client-facing transport DARK publishes in customer links.','دقیقاً مشخص کن DARK چه آدرس، پورت و تنظیمات سمت مشتری را در لینک‌ها منتشر کند.'),button(L('New endpoint','Endpoint جدید'),'hv2new','plus','',true))+
 `<div class="hv2"><div class="notice">${L('Inbound = what Xray listens on. Public Endpoint = what the customer connects to. They can be the same for direct service or different for tunnels/CDNs.','Inbound چیزی است که Xray روی آن Listen می‌کند؛ Public Endpoint چیزی است که مشتری به آن وصل می‌شود. در اتصال مستقیم می‌توانند یکی باشند و در تانل/CDN متفاوت.')}</div><article class="panel hv3-toolbar"><div class="hv2-toolbar"><input id="hv2search" class="field-input" type="search" placeholder="${L('Search endpoint / address / SNI','جستجوی Endpoint / آدرس / SNI')}" value="${e(state.hv2.search)}"><div class="hv2-filter"><button class="${state.hv2.inbound==='all'?'active':''}" data-act="hv2filter" data-id="all">${L('All','همه')}</button>${state.inbounds.map(i=>`<button class="${String(state.hv2.inbound)===String(i.id)?'active':''}" data-act="hv2filter" data-id="${i.id}">${e(i.remark||i.tag)}</button>`).join('')}</div><div class="spacer"></div><span class="tag">${rows.length}</span></div></article><div class="hv2-grid">${rows.length?rows.map(h=>pageCard(h,h._index)).join(''):empty(L('No public endpoints yet. Without one, DARK falls back to the configured server public address.','هنوز Public Endpoint نساخته‌ای. بدون آن، DARK از آدرس عمومی تنظیم‌شده سرور استفاده می‌کند.'))}</div></div>`;
}
enginePage=async function(){if(state.page==='hosts')return hostsPage();return baseEnginePage();};
function editorHTML(h,ib,mode,excluded){
 const m=inboundMeta(ib),td=transportDefaults(ib),sm=sniMode(h);
 return `<div class="hv3-editor"><section class="hv3-step"><header><span>01</span><div><b>${L('Source & mode','مبدا و حالت')}</b><small>${L('Pick the inbound first; DARK shows only controls that can affect its client link.','اول اینباند را انتخاب کن؛ DARK فقط کنترل‌های مؤثر روی لینک همان اینباند را نشان می‌دهد.')}</small></div></header><div class="hv2-form">${select(L('Source inbound','اینباند مبدا'),'inboundId',state.inbounds.map(i=>[i.id,(i.remark||i.tag)+' · '+String(i.protocol).toUpperCase()+' · :'+i.port]),h.inboundId)}${select(L('Status','وضعیت'),'enable',[['true',L('Active','فعال')],['false',L('Disabled','غیرفعال')]],String(h.enable!==false))}<div class="span-2">${modePicker(mode)}</div></div></section>
 <section class="hv3-step"><header><span>02</span><div><b>${L('Customer endpoint','Endpoint مشتری')}</b><small>${L('This address and port are published to the customer.','این آدرس و پورت داخل لینک مشتری منتشر می‌شوند.')}</small></div></header><div class="hv2-form">${field(L('Public / tunnel address','آدرس عمومی / تانل'),'address',h.address||'','text','required dir="ltr" placeholder="edge.example.com"')}${field(L('Public / tunnel port','پورت عمومی / تانل'),'port',h.port||m.port||443,'number','required min="1" max="65535"')}${field(L('Display name','نام نمایشی'),'remark',h.remark||'','text','maxlength="160"')}<div class="hv3-helper"><button type="button" class="btn" data-act="hv3panelhost">${L('Use panel hostname','استفاده از دامنه پنل')}</button><small>${L('Use only when the panel hostname is also the real customer endpoint.','فقط وقتی دامنه پنل همان Endpoint واقعی مشتری است استفاده کن.')}</small></div></div></section>
 <section class="hv3-step" data-hv3-section="tunnel"><header><span>03</span><div><b>${L('Client-facing route','مسیر سمت مشتری')}</b><small>${L('Inbound transport is inherited. Override only what the tunnel/CDN changes.','Transport اینباند حفظ می‌شود؛ فقط چیزی که تانل/CDN عوض می‌کند Override کن.')}</small></div></header><div class="hv2-form"><div class="span-2 hv3-facts"><span>${L('Inbound','اینباند')}: <b>${e(m.protocol.toUpperCase())}</b></span><span>Transport: <b>${e(m.network.toUpperCase())}</b></span><span>Security: <b>${e(m.security.toUpperCase())}</b></span></div><label class="hv3-field" data-hv3-sni><span>SNI</span><select name="sniMode"><option value="inherit" ${sm==='inherit'?'selected':''}>${L('Inherit from inbound','از اینباند')}</option><option value="manual" ${sm==='manual'?'selected':''}>${L('Manual','دستی')}</option><option value="address" ${sm==='address'?'selected':''}>${L('Use endpoint address','از آدرس Endpoint')}</option><option value="blank" ${sm==='blank'?'selected':''}>${L('Keep blank','خالی')}</option></select><small data-hv3-inherited-sni>${baseSni(ib)?L('Inbound SNI: ','SNI اینباند: ')+e(baseSni(ib)):L('Inbound has no SNI.','اینباند SNI ندارد.')}</small></label><div data-hv3-manual-sni>${field(L('Manual SNI','SNI دستی'),'sni',h.sni||'','text','dir="ltr" placeholder="sni.example.com"')}</div><div data-hv3-web>${field('Host',h.host?'host':'host',h.host||td.host||'','text','dir="ltr"')}</div><div data-hv3-web>${field('Path','path',h.path||td.path||'','text','dir="ltr"')}</div></div></section>
 <section class="hv3-step" data-hv3-section="advanced"><header><span>04</span><div><b>${L('Advanced override','Override پیشرفته')}</b><small>${L('Use only when the public endpoint terminates a different security layer or needs format-specific behavior.','فقط وقتی Endpoint بیرونی لایه امنیتی متفاوت یا رفتار خاص فرمت‌ها دارد.')}</small></div></header><div class="hv2-form">${select(L('Security published to client','امنیت منتشرشده برای مشتری'),'security',[['same',L('Same as inbound','مثل اینباند')],['tls','TLS'],['none',L('None / plaintext','بدون TLS')]],h.security||'same')}${field('ALPN','alpn',h.alpn||'','text','dir="ltr" placeholder="h2,http/1.1"')}${field(L('Fingerprint','Fingerprint'),'fingerprint',h.fingerprint||'','text','dir="ltr" placeholder="chrome"')}${select(L('Allow insecure TLS','TLS ناامن'),'allowInsecure',[['false',L('No','خیر')],['true',L('Yes','بله')]],String(!!h.allowInsecure))}${select(L('Mihomo IP version','نسخه IP در Mihomo'),'mihomoIpVersion',[['',L('Default','پیش‌فرض')],['dual','dual'],['ipv4','ipv4'],['ipv6','ipv6'],['ipv4-prefer','ipv4-prefer'],['ipv6-prefer','ipv6-prefer']],h.mihomoIpVersion||'')}<label class="span-2 hv3-field"><span>Final Mask JSON</span><textarea class="field-input" name="finalMask" dir="ltr" rows="3" placeholder='{"tcpPadding":true}'>${e(h.finalMask||'')}</textarea><small>${L('Only emitted when configured; leave blank for normal endpoints.','فقط در صورت نیاز منتشر می‌شود؛ برای Endpoint عادی خالی بگذار.')}</small></label><div class="span-2"><label>${L('Exclude this endpoint from formats','عدم نمایش این Endpoint در فرمت‌ها')}</label><div class="check-list"><label><input type="checkbox" name="excludeFormat" value="raw" ${excluded.has('raw')?'checked':''}><span>Raw / Base64</span></label><label><input type="checkbox" name="excludeFormat" value="json" ${excluded.has('json')?'checked':''}><span>DARK JSON</span></label><label><input type="checkbox" name="excludeFormat" value="clash" ${excluded.has('clash')?'checked':''}><span>Clash / Mihomo</span></label></div></div></div></section>
 <section class="hv3-preview"><header><span>05</span><div><b>${L('Customer link impact','اثر روی لینک مشتری')}</b><small>${L('Credential is masked. Endpoint, transport, security and SNI match the values DARK will publish.','Credential ماسک شده؛ Endpoint، Transport، Security و SNI مطابق خروجی DARK هستند.')}</small></div></header><div data-hv3-preview></div></section></div>`;
}
function formRaw(form){
 const fd=new FormData(form);return {mode:String(fd.get('mode')||'direct'),address:fd.get('address'),port:fd.get('port'),remark:fd.get('remark'),enable:fd.get('enable')==='true',sniMode:fd.get('sniMode'),sni:fd.get('sni'),host:fd.get('host'),path:fd.get('path'),security:fd.get('security'),alpn:fd.get('alpn'),fingerprint:fd.get('fingerprint'),allowInsecure:fd.get('allowInsecure')==='true',finalMask:fd.get('finalMask'),mihomoIpVersion:fd.get('mihomoIpVersion'),excludeFromSubTypes:fd.getAll('excludeFormat')};}
function syncEditor(form){
 const ib=inboundFor(form.elements.inboundId?.value),m=inboundMeta(ib),mode=form.querySelector('input[name=mode]:checked')?.value||'direct',tunnel=mode!=='direct',advanced=mode==='advanced';
 form.querySelectorAll('.hv3-modes label').forEach(x=>x.classList.toggle('active',!!x.querySelector('input:checked')));
 form.querySelector('[data-hv3-section="tunnel"]')?.classList.toggle('hidden',!tunnel);
 form.querySelector('[data-hv3-section="advanced"]')?.classList.toggle('hidden',!advanced);
 form.querySelectorAll('[data-hv3-sni]').forEach(x=>x.classList.toggle('hidden',!tunnel||m.security==='none'));
 const sm=form.elements.sniMode?.value||'inherit';form.querySelector('[data-hv3-manual-sni]')?.classList.toggle('hidden',!tunnel||m.security==='none'||sm!=='manual');
 form.querySelectorAll('[data-hv3-web]').forEach(x=>x.classList.toggle('hidden',!tunnel||!['ws','httpupgrade','xhttp'].includes(m.network)));
 const raw=formRaw(form);let model,err='';
 try{model=normalizeEndpoint(raw,ib);}catch(ex){err=ex.message;model={...raw,inboundId:Number(ib?.id||0),enable:raw.enable};}
 const p=previewModel(model,ib),box=form.querySelector('[data-hv3-preview]');
 if(box)box.innerHTML=err?`<div class="notice warning">${e(err)}</div>`:`<div class="hv3-preview-grid"><div><span>${L('Endpoint','Endpoint')}</span><b class="mono">${e(p.endpoint)}</b></div><div><span>Transport</span><b>${e(p.network.toUpperCase())}</b></div><div><span>Security</span><b>${e(p.security.toUpperCase())}</b></div><div><span>SNI</span><b class="mono">${e(p.sni||'—')}</b></div></div><code>${e(p.preview)}</code>`;
}
async function editHost(index=null,cloneMode=false){
 if(!state.inbounds.length)return toast(L('Create an inbound before adding a public endpoint.','قبل از Public Endpoint یک اینباند بساز.'),true);
 const list=(await api('/api/settings/hosts')).value||[],original=index==null?null:list[index],ib0=inboundFor(original?.inboundId)||state.inbounds[0];
 let h=original?clone(original):{inboundId:ib0.id,address:'',port:ib0.port,remark:'',security:'same',sni:'',overrideSniFromAddress:false,keepSniBlank:false,host:'',path:'',alpn:'',fingerprint:'',allowInsecure:false,finalMask:'',mihomoIpVersion:'',excludeFromSubTypes:[],enable:true};
 if(cloneMode)h.remark=(h.remark||L('Endpoint','Endpoint'))+' COPY';
 const mode=endpointMode(h,ib0),excluded=new Set(h.excludeFromSubTypes||[]);
 dialog(index==null?L('New public endpoint','Public Endpoint جدید'):L('Edit public endpoint','ویرایش Public Endpoint'),editorHTML(h,ib0,mode,excluded),async form=>{
   const ib=inboundFor(form.get('inboundId')),raw={mode:String(form.get('mode')||'direct'),address:form.get('address'),port:form.get('port'),remark:form.get('remark'),enable:form.get('enable')==='true',sniMode:form.get('sniMode'),sni:form.get('sni'),host:form.get('host'),path:form.get('path'),security:form.get('security'),alpn:form.get('alpn'),fingerprint:form.get('fingerprint'),allowInsecure:form.get('allowInsecure')==='true',finalMask:form.get('finalMask'),mihomoIpVersion:form.get('mihomoIpVersion'),excludeFromSubTypes:form.getAll('excludeFormat')};
   const v=normalizeEndpoint(raw,ib);if(index==null||cloneMode)list.push(v);else list[index]=v;
   await api('/api/settings/hosts','PUT',{value:list});closeDialog();toast(L('Public endpoint saved. Customer links now use this endpoint.','Public Endpoint ذخیره شد؛ لینک مشتری از این Endpoint استفاده می‌کند.'));await refresh();
 });
 const form=document.querySelector('#dialog-form');document.querySelector('#overlay .dialog')?.classList.add('hv3-dialog');
 if(form){
   form.addEventListener('change',ev=>{if(ev.target.name==='inboundId'){const ib=inboundFor(ev.target.value);if(ib)form.elements.port.value=ib.port;}syncEditor(form);});
   form.addEventListener('input',()=>syncEditor(form));syncEditor(form);
 }
}
runAction=async function(act,el){
 if(act==='hv2new'){await editHost();return;}
 if(act==='hv2edit'){await editHost(Number(el.dataset.index));return;}
 if(act==='hv2clone'){await editHost(Number(el.dataset.index),true);return;}
 if(act==='hv2delete'){const list=(await api('/api/settings/hosts')).value||[];if(confirm(L('Delete this public endpoint? Customer links will stop publishing it.','این Public Endpoint حذف شود؟ از لینک‌های مشتری حذف می‌شود.'))){list.splice(Number(el.dataset.index),1);await api('/api/settings/hosts','PUT',{value:list});await refresh();}return;}
 if(act==='hv2filter'){state.hv2.inbound=el.dataset.id;await renderPage();return;}
 if(act==='hv3panelhost'){const form=document.querySelector('#dialog-form'),host=location.hostname;if(form&&host){form.elements.address.value=host;syncEditor(form);}return;}
 return baseRunAction(act,el);
};
let ht;document.addEventListener('input',ev=>{if(ev.target.id==='hv2search'){state.hv2.search=ev.target.value;clearTimeout(ht);ht=setTimeout(async()=>{await renderPage();document.querySelector('#hv2search')?.focus();},180);}});
globalThis.DarkHostV3={inboundMeta,endpointMode,sniMode,normalizeEndpoint,previewModel,transportDefaults};
})();
