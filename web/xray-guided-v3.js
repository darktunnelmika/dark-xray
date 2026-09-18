/* DARK XRAY Xray Guided V3 — protocol-aware Outbound / Routing / Balancer editors. */
(function(){
'use strict';
if(typeof runAction!=='function'||typeof dialog!=='function'||typeof api!=='function')return;
const baseRunAction=runAction;
const L=(en,fa)=>((localStorage.getItem('dark_lang')||'en')==='fa'?fa:en);
const esc=v=>e(String(v??''));
const clone=v=>JSON.parse(JSON.stringify(v??{}));
const csv=v=>String(v||'').split(/[\n,]/).map(x=>x.trim()).filter(Boolean);
const n=(v,d=0)=>{const x=Number(v);return Number.isFinite(x)?x:d;};
const TAG=/^[A-Za-z0-9_.-]{1,128}$/;
const UUID=/^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const STREAM=new Set(['vless','vmess','trojan','shadowsocks']);
const PROTOCOLS=[
 ['freedom',L('Direct / Freedom','مستقیم / Freedom')],
 ['blackhole',L('Block / Blackhole','مسدود / Blackhole')],
 ['vless','VLESS'],['vmess','VMess'],['trojan','Trojan'],['shadowsocks','Shadowsocks'],
 ['socks','SOCKS5'],['http','HTTP Proxy'],['wireguard','WireGuard'],['dns','DNS'],
 ['loopback','Loopback'],['hysteria','Hysteria v2']
];
const NETWORKS=[['tcp','TCP'],['ws','WebSocket'],['grpc','gRPC'],['httpupgrade','HTTPUpgrade'],['xhttp','XHTTP'],['kcp','mKCP']];
const SECURITY=[['none',L('None','بدون امنیت')],['tls','TLS'],['reality','REALITY']];

function fld(label,name,value='',type='text',attrs='',help=''){
 return `<label class="xv3-field"><span>${label}</span><input class="field-input" name="${name}" type="${type}" value="${esc(value)}" ${attrs}>${help?`<small>${help}</small>`:''}</label>`;
}
function sel(label,name,options,value,help=''){
 return `<label class="xv3-field"><span>${label}</span><select name="${name}">${options.map(([v,l])=>`<option value="${esc(v)}" ${String(v)===String(value)?'selected':''}>${esc(l)}</option>`).join('')}</select>${help?`<small>${help}</small>`:''}</label>`;
}
function textarea(label,name,value='',help='',attrs=''){
 return `<label class="xv3-field xv3-span-2"><span>${label}</span><textarea name="${name}" class="xv3-textarea" ${attrs}>${esc(value)}</textarea>${help?`<small>${help}</small>`:''}</label>`;
}
function toggle(label,name,on,help=''){
 return `<label class="xv3-toggle"><span><b>${label}</b>${help?`<small>${help}</small>`:''}</span><input type="checkbox" name="${name}" value="true" ${on?'checked':''}></label>`;
}
function firstServer(o){
 const s=o?.settings||{},p=o?.protocol;
 if(p==='vless')return {address:s.address||s.vnext?.[0]?.address||'',port:s.port||s.vnext?.[0]?.port||443,id:s.id||s.vnext?.[0]?.users?.[0]?.id||'',flow:s.flow||s.vnext?.[0]?.users?.[0]?.flow||'',encryption:s.encryption||'none'};
 if(p==='vmess'){const x=s.vnext?.[0]||{},u=x.users?.[0]||{};return {address:x.address||'',port:x.port||443,id:u.id||'',vmessSecurity:u.security||'auto'};}
 const x=s.servers?.[0]||{},u=x.users?.[0]||{};
 return {address:x.address||s.address||'',port:x.port||s.port||443,password:x.password||'',method:x.method||'',username:u.user||'',proxyPassword:u.pass||''};
}
function wgState(o){const s=o?.settings||{},p=s.peers?.[0]||{};return {secretKey:s.secretKey||'',address:(s.address||[]).join(','),publicKey:p.publicKey||'',endpoint:p.endpoint||'',preSharedKey:p.preSharedKey||'',allowedIPs:(p.allowedIPs||['0.0.0.0/0','::/0']).join(','),keepAlive:p.keepAlive||0,mtu:s.mtu||1420,reserved:(s.reserved||[]).join(','),domainStrategy:s.domainStrategy||'',noKernelTun:!!s.noKernelTun};}
function streamState(o){const st=o?.streamSettings||{},net=st.network||'tcp',sec=st.security||'none',so=st.sockopt||{};return {
 network:net,security:sec,
 host:st.wsSettings?.host||st.wsSettings?.headers?.Host||st.httpupgradeSettings?.host||st.xhttpSettings?.host||'',
 path:st.wsSettings?.path||st.httpupgradeSettings?.path||st.xhttpSettings?.path||'/',
 serviceName:st.grpcSettings?.serviceName||'',authority:st.grpcSettings?.authority||'',xhttpMode:st.xhttpSettings?.mode||'auto',
 kcpMtu:st.kcpSettings?.mtu||1350,kcpTti:st.kcpSettings?.tti||20,
 sni:st.tlsSettings?.serverName||st.realitySettings?.serverName||'',
 alpn:(st.tlsSettings?.alpn||[]).join(','),fingerprint:st.tlsSettings?.fingerprint||st.realitySettings?.fingerprint||'chrome',
 allowInsecure:!!st.tlsSettings?.allowInsecure,
 publicKey:st.realitySettings?.publicKey||'',shortId:st.realitySettings?.shortId||'',spiderX:st.realitySettings?.spiderX||'/',
 dialerProxy:so.dialerProxy||'',iface:so.interface||'',mark:so.mark??'',tcpFastOpen:!!so.tcpFastOpen,
 sendThrough:o?.sendThrough||'',muxEnabled:!!o?.mux?.enabled,muxConcurrency:o?.mux?.concurrency??8,xudpConcurrency:o?.mux?.xudpConcurrency??8
};}

function protocolHelp(p){
 const map={
  freedom:L('Send traffic directly from this VPS. Best default for normal egress.','ترافیک را مستقیم از همین VPS خارج می‌کند؛ انتخاب پیش‌فرض برای خروجی عادی.'),
  blackhole:L('Drop matching traffic. Use it with Routing for ads or blocked destinations.','ترافیک مطابق Rule را قطع می‌کند؛ برای تبلیغات یا مقصدهای مسدود در Routing.'),
  vless:L('Connect to another VLESS server using UUID, transport and optional TLS/REALITY.','اتصال به سرور VLESS دیگر با UUID، ترنسپورت و TLS/REALITY اختیاری.'),
  vmess:L('Connect to a VMess server. Transport and TLS are configured below.','اتصال به سرور VMess؛ ترنسپورت و TLS پایین تنظیم می‌شوند.'),
  trojan:L('Connect to a Trojan server using password and TLS/REALITY transport.','اتصال به Trojan با رمز و TLS/REALITY.'),
  shadowsocks:L('Connect to a Shadowsocks server with cipher and password.','اتصال به Shadowsocks با رمزنگاری و پسورد.'),
  socks:L('Use an upstream SOCKS5 proxy, with optional username/password.','استفاده از پراکسی SOCKS5 بالادست با یوزر/پسورد اختیاری.'),
  http:L('Use an upstream HTTP CONNECT proxy, with optional credentials.','استفاده از HTTP Proxy بالادست با احراز هویت اختیاری.'),
  wireguard:L('Route traffic through a WireGuard peer. Keys and peer endpoint are required.','عبور ترافیک از Peer وایرگارد؛ کلیدها و Endpoint لازم هستند.'),
  dns:L('Send DNS queries through Xray DNS outbound rewriting.','ارسال درخواست‌های DNS از طریق DNS Outbound خود Xray.'),
  loopback:L('Loop traffic back into an existing inbound tag. Advanced topology use.','برگرداندن ترافیک به یک Inbound موجود؛ مناسب توپولوژی پیشرفته.'),
  hysteria:L('Native Hysteria v2 outbound when supported by the installed Xray core.','Hysteria v2 بومی؛ فقط وقتی Core نصب‌شده آن را پشتیبانی کند.')
 };
 return map[p]||'';
}
function outboundTags(list,current=''){return [['',L('No chain / direct dial','بدون زنجیره')],...list.filter(x=>x.tag!==current).map(x=>[x.tag,`${x.tag} · ${x.protocol}`])];}
function inboundTags(){return (state.inbounds||[]).map(x=>[x.tag||('inbound-'+x.id),x.remark?`${x.remark} · ${x.tag||x.id}`:x.tag||String(x.id)]);}

function guidedBody(o,list){
 const p=o.protocol||'freedom',sv=firstServer(o),wg=wgState(o),st=streamState(o),settings=o.settings||{},mux=o.mux||{};
 const proto=sel(L('Outbound type','نوع Outbound'),'protocol',PROTOCOLS,p,L('Choose the purpose first. Fields below change automatically.','اول نوع خروجی را انتخاب کن؛ فیلدهای پایین خودکار تغییر می‌کنند.'));
 const common=`<section class="xv3-section"><div class="xv3-section-head"><b>1 · ${L('Identity & type','هویت و نوع')}</b><small>${L('Give the outbound a unique tag and choose its behavior.','یک Tag یکتا بده و نوع عملکرد را انتخاب کن.')}</small></div><div class="xv3-grid">${fld('Tag','tag',o.tag||'proxy','text','required pattern="[A-Za-z0-9_.-]{1,128}" dir="ltr"',L('Routing and chaining refer to this tag.','Routing و Chain با این Tag کار می‌کنند.'))}${proto}<div class="xv3-span-2 xv3-protocol-help" data-xv3-help>${protocolHelp(p)}</div></div></section>`;
 const server=`<section class="xv3-section" data-xv3-protocols="vless vmess trojan shadowsocks socks http hysteria"><div class="xv3-section-head"><b>2 · ${L('Remote server','سرور مقصد')}</b><small>${L('Where this outbound connects.','این Outbound به کجا وصل می‌شود.')}</small></div><div class="xv3-grid">
 ${fld(L('Address / domain','آدرس / دامنه'),'address',sv.address,'text','dir="ltr"',L('Example: edge.example.com or 1.2.3.4','مثال: edge.example.com یا 1.2.3.4'))}
 ${fld(L('Port','پورت'),'port',sv.port,'number','min="1" max="65535"')}
 <div data-xv3-protocols="vless vmess" class="xv3-grid xv3-span-2">
  ${fld('UUID','id',sv.id||'','text','dir="ltr"',L('Client UUID on the remote server.','UUID کاربر روی سرور مقصد.'))}
  <div data-xv3-protocols="vless">${sel('Flow','flow',[['',L('None','بدون Flow')],['xtls-rprx-vision','xtls-rprx-vision']],sv.flow||'')}</div>
  <div data-xv3-protocols="vmess">${sel('VMess security','vmessSecurity',[['auto','auto'],['aes-128-gcm','aes-128-gcm'],['chacha20-poly1305','chacha20-poly1305'],['none','none']],sv.vmessSecurity||'auto')}</div>
 </div>
 <div data-xv3-protocols="trojan shadowsocks" class="xv3-span-2">${fld(L('Password','رمز'),'password',sv.password||'','password','autocomplete="off" dir="ltr"')}</div>
 <div data-xv3-protocols="shadowsocks" class="xv3-span-2">${sel(L('Cipher / method','روش رمزنگاری'),'ssMethod',[
 ['2022-blake3-aes-128-gcm','2022-blake3-aes-128-gcm'],['2022-blake3-aes-256-gcm','2022-blake3-aes-256-gcm'],['aes-128-gcm','aes-128-gcm'],['aes-256-gcm','aes-256-gcm'],['chacha20-poly1305','chacha20-poly1305']
 ],sv.method||'2022-blake3-aes-128-gcm')}</div>
 <div data-xv3-protocols="socks http" class="xv3-grid xv3-span-2">
  ${fld(L('Username (optional)','نام کاربری (اختیاری)'),'username',sv.username||'','text','dir="ltr"')}
  ${fld(L('Password (optional)','رمز (اختیاری)'),'proxyPassword',sv.proxyPassword||'','password','autocomplete="off" dir="ltr"')}
 </div>
 </div></section>`;
 const freedom=`<section class="xv3-section" data-xv3-protocols="freedom"><div class="xv3-section-head"><b>2 · Direct</b><small>${L('Normally leave strategy on AsIs.','معمولاً AsIs را تغییر نده.')}</small></div><div class="xv3-grid">${sel('domainStrategy','freedomStrategy',[['AsIs','AsIs'],['UseIP','UseIP'],['UseIPv4','UseIPv4'],['UseIPv6','UseIPv6']],settings.domainStrategy||'AsIs')}</div></section>`;
 const blackhole=`<section class="xv3-section" data-xv3-protocols="blackhole"><div class="xv3-section-head"><b>2 · Blackhole</b><small>${L('Silently drop or return a simple HTTP response.','قطع بی‌صدا یا پاسخ HTTP ساده.')}</small></div><div class="xv3-grid">${sel(L('Response','پاسخ'),'blackholeResponse',[['none',L('Silent drop','قطع بی‌صدا')],['http','HTTP response']],settings.response?.type||'none')}</div></section>`;
 const wireguard=`<section class="xv3-section" data-xv3-protocols="wireguard"><div class="xv3-section-head"><b>2 · WireGuard</b><small>${L('One peer per guided outbound; Advanced JSON remains available for multi-peer configurations.','در فرم ساده یک Peer؛ برای چند Peer از JSON پیشرفته استفاده کن.')}</small></div><div class="xv3-grid">
 ${fld(L('Private / Secret Key','کلید خصوصی'),'wgSecretKey',wg.secretKey,'password','autocomplete="off" dir="ltr"')}
 ${fld(L('Local addresses','آدرس‌های محلی'),'wgAddress',wg.address,'text','dir="ltr"',L('Comma separated CIDRs, e.g. 172.16.0.2/32','CIDRها با کاما، مثل 172.16.0.2/32'))}
 ${fld(L('Peer public key','کلید عمومی Peer'),'wgPublicKey',wg.publicKey,'text','dir="ltr"')}
 ${fld(L('Peer endpoint','Endpoint Peer'),'wgEndpoint',wg.endpoint,'text','dir="ltr" placeholder="host:port"')}
 ${fld(L('Pre-shared key (optional)','PSK اختیاری'),'wgPsk',wg.preSharedKey,'password','autocomplete="off" dir="ltr"')}
 ${fld(L('Allowed IPs','Allowed IPs'),'wgAllowed',wg.allowedIPs,'text','dir="ltr"')}
 ${fld('MTU','wgMtu',wg.mtu,'number','min="576" max="9000"')}
 ${fld('KeepAlive','wgKeepAlive',wg.keepAlive,'number','min="0" max="65535"')}
 ${fld(L('Reserved bytes','Reserved bytes'),'wgReserved',wg.reserved,'text','dir="ltr"',L('Optional: 3 comma-separated integers for WARP-style peers.','اختیاری: سه عدد برای Peerهای سبک WARP.'))}
 ${sel('domainStrategy','wgDomainStrategy',[['','Default'],['ForceIP','ForceIP'],['ForceIPv4','ForceIPv4'],['ForceIPv6','ForceIPv6'],['ForceIPv4v6','ForceIPv4v6'],['ForceIPv6v4','ForceIPv6v4']],wg.domainStrategy)}
 <div class="xv3-span-2">${toggle('noKernelTun','wgNoKernelTun',wg.noKernelTun,L('Use userspace WireGuard mode when supported by Xray.','استفاده از WireGuard userspace در صورت پشتیبانی Core.'))}</div>
 </div></section>`;
 const dns=`<section class="xv3-section" data-xv3-protocols="dns"><div class="xv3-section-head"><b>2 · DNS</b><small>${L('Rewrite DNS outbound destination when needed.','در صورت نیاز مقصد DNS را بازنویسی کن.')}</small></div><div class="xv3-grid">${sel('rewriteNetwork','dnsNetwork',[['','Default'],['tcp','TCP'],['udp','UDP']],settings.rewriteNetwork||'')}${fld('rewriteAddress','dnsAddress',settings.rewriteAddress||'','text','dir="ltr"')}${fld('rewritePort','dnsPort',settings.rewritePort||53,'number','min="1" max="65535"')}${fld('userLevel','dnsLevel',settings.userLevel||0,'number','min="0"')}</div></section>`;
 const loopback=`<section class="xv3-section" data-xv3-protocols="loopback"><div class="xv3-section-head"><b>2 · Loopback</b><small>${L('Send traffic back to an inbound tag.','ترافیک را به Tag یک Inbound برگردان.')}</small></div><div class="xv3-grid">${sel(L('Inbound tag','Tag اینباند'),'loopInbound',[['','—'],...inboundTags()],settings.inboundTag||'')}</div></section>`;
 const transport=`<section class="xv3-section" data-xv3-stream><div class="xv3-section-head"><b>3 · ${L('Transport & security','ترنسپورت و امنیت')}</b><small>${L('Only fields relevant to the selected transport are shown.','فقط فیلدهای مربوط به ترنسپورت انتخاب‌شده نمایش داده می‌شوند.')}</small></div><div class="xv3-grid">
 ${sel(L('Network','شبکه'),'network',NETWORKS,st.network)}
 ${sel(L('Security','امنیت'),'security',SECURITY,st.security)}
 <div data-xv3-networks="ws httpupgrade xhttp" class="xv3-grid xv3-span-2">${fld('Path','path',st.path,'text','dir="ltr"')}${fld('Host','host',st.host,'text','dir="ltr"')}</div>
 <div data-xv3-networks="grpc" class="xv3-grid xv3-span-2">${fld('serviceName','serviceName',st.serviceName,'text','dir="ltr"')}${fld('authority','authority',st.authority,'text','dir="ltr"')}</div>
 <div data-xv3-networks="xhttp" class="xv3-span-2">${sel('XHTTP mode','xhttpMode',[['auto','auto'],['packet-up','packet-up'],['stream-up','stream-up'],['stream-one','stream-one']],st.xhttpMode)}</div>
 <div data-xv3-networks="kcp" class="xv3-grid xv3-span-2">${fld('MTU','kcpMtu',st.kcpMtu,'number','min="576" max="1460"')}${fld('TTI','kcpTti',st.kcpTti,'number','min="10" max="5000"')}</div>
 <div data-xv3-security="tls" class="xv3-grid xv3-span-2">${fld('Server Name / SNI','sni',st.sni,'text','dir="ltr"')}${fld('ALPN','alpn',st.alpn,'text','dir="ltr" placeholder="h2,http/1.1"')}${sel('Fingerprint','fingerprint',[['chrome','chrome'],['firefox','firefox'],['safari','safari'],['edge','edge'],['randomized','randomized']],st.fingerprint)}<div>${toggle('Allow insecure TLS','allowInsecure',st.allowInsecure,L('Avoid unless the upstream certificate cannot be verified.','فقط وقتی گواهی مقصد قابل Verify نیست استفاده کن.'))}</div></div>
 <div data-xv3-security="reality" class="xv3-grid xv3-span-2">${fld('Server Name / SNI','sniReality',st.sni,'text','dir="ltr"')}${sel('Fingerprint','realityFingerprint',[['chrome','chrome'],['firefox','firefox'],['safari','safari'],['edge','edge'],['randomized','randomized']],st.fingerprint)}${fld('Public Key','realityPublicKey',st.publicKey,'text','dir="ltr"')}${fld('Short ID','realityShortId',st.shortId,'text','dir="ltr"')}${fld('SpiderX','realitySpiderX',st.spiderX,'text','dir="ltr"')}</div>
 </div></section>`;
 const advanced=`<section class="xv3-section"><details class="xv3-advanced"><summary>4 · ${L('Chain & advanced','زنجیره و پیشرفته')}</summary><div class="xv3-grid">
 ${sel(L('Dial through outbound','عبور از Outbound دیگر'),'dialerProxy',outboundTags(list,o.tag),st.dialerProxy,L('Equivalent to Xray sockopt.dialerProxy. Cycles are rejected by DARK.','معادل dialerProxy در Xray؛ چرخه توسط DARK رد می‌شود.'))}
 ${fld('sendThrough','sendThrough',st.sendThrough,'text','dir="ltr"',L('Optional local source IP. Leave blank for normal routing.','IP مبدا محلی اختیاری؛ معمولاً خالی بماند.'))}
 ${fld(L('Network interface','اینترفیس شبکه'),'sockInterface',st.iface,'text','dir="ltr"')}
 ${fld('SO_MARK','sockMark',st.mark,'number','min="0"')}
 <div>${toggle('TCP Fast Open','tcpFastOpen',st.tcpFastOpen)}</div>
 <div data-xv3-mux>${toggle('Mux','muxEnabled',st.muxEnabled,L('Disabled automatically for VLESS Vision and XHTTP.','برای VLESS Vision و XHTTP نباید فعال باشد.'))}</div>
 <div data-xv3-mux-fields class="xv3-grid xv3-span-2">${fld('Mux concurrency','muxConcurrency',st.muxConcurrency,'number','min="-1" max="1024"')}${fld('XUDP concurrency','xudpConcurrency',st.xudpConcurrency,'number','min="-1" max="1024"')}</div>
 </div></details></section>`;
 const summary=`<section class="xv3-summary"><span>${L('Result','خلاصه')}</span><b data-xv3-summary></b><small>${L('DARK saves first. Use Validate before applying Xray.','DARK اول ذخیره می‌کند؛ قبل از اعمال Xray حتماً Validate کن.')}</small></section>`;
 return `<div class="xv3-editor">${common}${server}${freedom}${blackhole}${wireguard}${dns}${loopback}${transport}${advanced}${summary}</div>`;
}

function formBool(fd,name){return fd.get(name)==='true'||fd.has(name);}
function required(fd,name,label){const v=String(fd.get(name)||'').trim();if(!v)throw Error(label+' '+L('is required.','الزامی است.'));return v;}
function validPort(fd,name='port'){const p=n(fd.get(name));if(!Number.isInteger(p)||p<1||p>65535)throw Error(L('Port must be between 1 and 65535.','پورت باید بین ۱ تا ۶۵۵۳۵ باشد.'));return p;}
function buildStream(fd,original){
 const network=String(fd.get('network')||'tcp'),security=String(fd.get('security')||'none'),old=clone(original||{}),st={...old,network,security};
 for(const k of ['tcpSettings','kcpSettings','wsSettings','grpcSettings','httpupgradeSettings','xhttpSettings','tlsSettings','realitySettings'])delete st[k];
 if(network==='tcp')st.tcpSettings={...(old.network==='tcp'?old.tcpSettings:{}),header:{...((old.tcpSettings||{}).header||{}),type:'none'}};
 if(network==='ws')st.wsSettings={...(old.network==='ws'?old.wsSettings:{}),path:String(fd.get('path')||'/'),host:String(fd.get('host')||'').trim()};
 if(network==='grpc')st.grpcSettings={...(old.network==='grpc'?old.grpcSettings:{}),serviceName:String(fd.get('serviceName')||'').trim(),authority:String(fd.get('authority')||'').trim(),multiMode:false};
 if(network==='httpupgrade')st.httpupgradeSettings={...(old.network==='httpupgrade'?old.httpupgradeSettings:{}),path:String(fd.get('path')||'/'),host:String(fd.get('host')||'').trim()};
 if(network==='xhttp')st.xhttpSettings={...(old.network==='xhttp'?old.xhttpSettings:{}),path:String(fd.get('path')||'/'),host:String(fd.get('host')||'').trim(),mode:String(fd.get('xhttpMode')||'auto')};
 if(network==='kcp')st.kcpSettings={...(old.network==='kcp'?old.kcpSettings:{}),mtu:n(fd.get('kcpMtu'),1350),tti:n(fd.get('kcpTti'),20)};
 if(security==='tls')st.tlsSettings={...(old.security==='tls'?old.tlsSettings:{}),serverName:String(fd.get('sni')||'').trim(),alpn:csv(fd.get('alpn')),fingerprint:String(fd.get('fingerprint')||'chrome'),allowInsecure:formBool(fd,'allowInsecure')};
 if(security==='reality')st.realitySettings={...(old.security==='reality'?old.realitySettings:{}),serverName:required(fd,'sniReality','SNI'),fingerprint:String(fd.get('realityFingerprint')||'chrome'),publicKey:required(fd,'realityPublicKey','REALITY Public Key'),shortId:String(fd.get('realityShortId')||'').trim(),spiderX:String(fd.get('realitySpiderX')||'/')};
 const so={...(old.sockopt||{})},via=String(fd.get('dialerProxy')||'').trim(),iface=String(fd.get('sockInterface')||'').trim(),mark=String(fd.get('sockMark')||'').trim();
 if(via)so.dialerProxy=via;else delete so.dialerProxy;
 if(iface)so.interface=iface;else delete so.interface;
 if(mark)so.mark=n(mark);else delete so.mark;
 if(formBool(fd,'tcpFastOpen'))so.tcpFastOpen=true;else delete so.tcpFastOpen;
 if(Object.keys(so).length)st.sockopt=so;else delete st.sockopt;
 return st;
}
function buildSettings(fd,p,old={}){
 if(p==='freedom'){const x={...old},v=String(fd.get('freedomStrategy')||'AsIs');if(v&&v!=='AsIs')x.domainStrategy=v;else delete x.domainStrategy;return x;}
 if(p==='blackhole'){const v=String(fd.get('blackholeResponse')||'none');return v==='http'?{...old,response:{type:'http'}}:{};}
 if(p==='vless'){const id=required(fd,'id','UUID');if(!UUID.test(id))throw Error(L('VLESS UUID is invalid.','UUID مربوط به VLESS معتبر نیست.'));return {...old,address:required(fd,'address',L('Address','آدرس')),port:validPort(fd),id,flow:String(fd.get('flow')||''),encryption:'none'};}
 if(p==='vmess'){const id=required(fd,'id','UUID');if(!UUID.test(id))throw Error(L('VMess UUID is invalid.','UUID مربوط به VMess معتبر نیست.'));const prev=old.vnext?.[0]||{},user=prev.users?.[0]||{};return {...old,vnext:[{...prev,address:required(fd,'address',L('Address','آدرس')),port:validPort(fd),users:[{...user,id,security:String(fd.get('vmessSecurity')||'auto')}]}]};}
 if(p==='trojan'){const prev=old.servers?.[0]||{};return {...old,servers:[{...prev,address:required(fd,'address',L('Address','آدرس')),port:validPort(fd),password:required(fd,'password',L('Password','رمز'))}]};}
 if(p==='shadowsocks'){const prev=old.servers?.[0]||{};return {...old,servers:[{...prev,address:required(fd,'address',L('Address','آدرس')),port:validPort(fd),password:required(fd,'password',L('Password','رمز')),method:String(fd.get('ssMethod')||'2022-blake3-aes-128-gcm')}]};}
 if(p==='socks'||p==='http'){const prev=old.servers?.[0]||{},username=String(fd.get('username')||'').trim(),pass=String(fd.get('proxyPassword')||'');const server={...prev,address:required(fd,'address',L('Address','آدرس')),port:validPort(fd),users:username?[{user:username,pass}]:[]};return {...old,servers:[server]};}
 if(p==='wireguard'){const peer=(old.peers||[])[0]||{},reserved=csv(fd.get('wgReserved')).map(Number);if(reserved.some(x=>!Number.isInteger(x)||x<0||x>255))throw Error(L('Reserved bytes must be integers from 0 to 255.','Reserved باید عدد صحیح بین ۰ تا ۲۵۵ باشد.'));const out={...old,secretKey:required(fd,'wgSecretKey',L('WireGuard secret key','کلید خصوصی WireGuard')),address:csv(fd.get('wgAddress')),mtu:n(fd.get('wgMtu'),1420),noKernelTun:formBool(fd,'wgNoKernelTun'),peers:[{...peer,publicKey:required(fd,'wgPublicKey',L('Peer public key','کلید عمومی Peer')),endpoint:required(fd,'wgEndpoint','Endpoint'),allowedIPs:csv(fd.get('wgAllowed')).length?csv(fd.get('wgAllowed')):['0.0.0.0/0','::/0']}]};const psk=String(fd.get('wgPsk')||'').trim(),ka=n(fd.get('wgKeepAlive'));if(psk)out.peers[0].preSharedKey=psk;else delete out.peers[0].preSharedKey;if(ka)out.peers[0].keepAlive=ka;else delete out.peers[0].keepAlive;if(reserved.length)out.reserved=reserved;else delete out.reserved;const ds=String(fd.get('wgDomainStrategy')||'');if(ds)out.domainStrategy=ds;else delete out.domainStrategy;return out;}
 if(p==='dns'){return {...old,rewriteNetwork:String(fd.get('dnsNetwork')||''),rewriteAddress:String(fd.get('dnsAddress')||'').trim(),rewritePort:n(fd.get('dnsPort'),53),userLevel:n(fd.get('dnsLevel'),0)};}
 if(p==='loopback'){return {...old,inboundTag:required(fd,'loopInbound',L('Inbound tag','Tag اینباند'))};}
 if(p==='hysteria'){return {...old,address:required(fd,'address',L('Address','آدرس')),port:validPort(fd),version:2};}
 throw Error(L('Unsupported guided protocol. Use Advanced JSON.','این پروتکل در فرم ساده پشتیبانی نمی‌شود؛ از JSON پیشرفته استفاده کن.'));
}
function buildOutbound(fd,original={}){
 const p=String(fd.get('protocol')||'freedom'),tag=required(fd,'tag','Tag');if(!TAG.test(tag))throw Error(L('Tag may contain only letters, numbers, dot, underscore and dash.','Tag فقط می‌تواند شامل حروف، عدد، نقطه، خط تیره و زیرخط باشد.'));
 const same=original.protocol===p,out=same?clone(original):{};
 out.tag=tag;out.protocol=p;out.settings=buildSettings(fd,p,same?(original.settings||{}):{});
 if(STREAM.has(p)){
  const st=buildStream(fd,same?(original.streamSettings||{}):{});
  if(st.security==='reality'&&!['vless','trojan'].includes(p))throw Error(L('REALITY guided mode is available only for VLESS or Trojan.','REALITY در فرم ساده فقط برای VLESS یا Trojan فعال است.'));
  out.streamSettings=st;
 }else delete out.streamSettings;
 const send=String(fd.get('sendThrough')||'').trim();if(send)out.sendThrough=send;else delete out.sendThrough;
 if(STREAM.has(p)){
  const muxEnabled=formBool(fd,'muxEnabled'),flow=p==='vless'?String(fd.get('flow')||''):'',net=String(fd.get('network')||'tcp');
  if(muxEnabled&&(flow||net==='xhttp'))throw Error(L('Mux cannot be enabled with VLESS Vision flow or XHTTP.','Mux با VLESS Vision یا XHTTP قابل فعال‌سازی نیست.'));
  out.mux={...(same?original.mux:{}),enabled:muxEnabled,concurrency:n(fd.get('muxConcurrency'),8),xudpConcurrency:n(fd.get('xudpConcurrency'),8)};
 }else delete out.mux;
 return out;
}
function summaryFromForm(form){
 const fd=new FormData(form),p=String(fd.get('protocol')||''),tag=String(fd.get('tag')||'—'),addr=String(fd.get('address')||''),port=String(fd.get('port')||''),net=String(fd.get('network')||''),sec=String(fd.get('security')||''),via=String(fd.get('dialerProxy')||'');
 let x=`${tag} · ${p}`;if(addr)x+=` → ${addr}${port?':'+port:''}`;if(STREAM.has(p))x+=` · ${net}${sec&&sec!=='none'?' + '+sec:''}`;if(via)x+=` · via ${via}`;return x;
}
function syncEditor(form){
 const p=form.elements.protocol?.value||'freedom',net=form.elements.network?.value||'tcp',sec=form.elements.security?.value||'none';
 form.querySelectorAll('[data-xv3-protocols]').forEach(el=>el.hidden=!el.dataset.xv3Protocols.split(/\s+/).includes(p));
 form.querySelectorAll('[data-xv3-networks]').forEach(el=>el.hidden=!el.dataset.xv3Networks.split(/\s+/).includes(net));
 form.querySelectorAll('[data-xv3-security]').forEach(el=>el.hidden=el.dataset.xv3Security!==sec);
 const stream=form.querySelector('[data-xv3-stream]');if(stream)stream.hidden=!STREAM.has(p);
 const help=form.querySelector('[data-xv3-help]');if(help)help.textContent=protocolHelp(p);
 if(form.elements.security){
  [...form.elements.security.options].forEach(o=>{if(o.value==='reality')o.disabled=!['vless','trojan'].includes(p);});
  if(form.elements.security.value==='reality'&&!['vless','trojan'].includes(p)){form.elements.security.value='none';}
 }
 const mux=form.querySelector('[data-xv3-mux]');if(mux)mux.hidden=!STREAM.has(p);
 const fields=form.querySelector('[data-xv3-mux-fields]');if(fields)fields.hidden=!STREAM.has(p)||!form.elements.muxEnabled?.checked;
 const sum=form.querySelector('[data-xv3-summary]');if(sum)sum.textContent=summaryFromForm(form);
}
async function guidedOutbound(index=null,cloneMode=false){
 const list=(await api('/api/settings/outbounds')).value||[];
 let original=index==null?{tag:'proxy',protocol:'freedom',settings:{}}:clone(list[index]);
 if(!PROTOCOLS.some(x=>x[0]===original.protocol)){await rawOutbound(index);return;}
 if(cloneMode)original.tag=(original.tag||'out')+'-copy';
 dialog(index==null?L('New outbound · Guided V3','Outbound جدید · Guided V3'):L('Edit outbound · Guided V3','ویرایش Outbound · Guided V3'),guidedBody(original,list),async(fd,form)=>{
   const out=buildOutbound(fd,original);
   if(list.some((x,i)=>i!==index&&x.tag===out.tag))throw Error(L('Outbound tag already exists.','این Tag قبلاً استفاده شده است.'));
   if(index==null||cloneMode)list.push(out);else list[index]=out;
   await api('/api/settings/outbounds','PUT',{value:list});
   toast(L('Outbound saved. Validate before applying Xray.','Outbound ذخیره شد؛ قبل از اعمال Xray Validate کن.'));
   closeDialog();await refresh();
 });
 const form=document.querySelector('#dialog-form');if(form){form._darkOutboundOriginal=clone(original);form.addEventListener('input',()=>syncEditor(form));form.addEventListener('change',()=>syncEditor(form));syncEditor(form);}
}
async function rawOutbound(index){
 const list=(await api('/api/settings/outbounds')).value||[],o=list[index];if(!o)throw Error('Outbound not found');
 dialog(L('Advanced outbound JSON','JSON پیشرفته Outbound'),`<div class="notice warning">${L('Advanced mode bypasses the guided form. DARK still validates tag/chaining and Xray Validate should be run before apply.','حالت پیشرفته فرم ساده را دور می‌زند؛ DARK همچنان Tag/Chain را بررسی می‌کند و قبل از اعمال باید Xray Validate اجرا شود.')}</div>${textarea('Outbound JSON','raw',JSON.stringify(o,null,2),'','dir="ltr" spellcheck="false"')}`,async fd=>{let v;try{v=JSON.parse(String(fd.get('raw')||''));}catch{throw Error(L('Invalid JSON.','JSON نامعتبر است.'));}if(!v||typeof v!=='object'||Array.isArray(v))throw Error(L('Outbound JSON must be an object.','JSON Outbound باید Object باشد.'));list[index]=v;await api('/api/settings/outbounds','PUT',{value:list});closeDialog();await refresh();});
}
function parseLink(raw){
 raw=String(raw||'').trim();if(!raw)throw Error(L('Paste a share link first.','ابتدا لینک را وارد کن.'));
 if(raw.startsWith('vmess://')){let txt=raw.slice(8).replace(/-/g,'+').replace(/_/g,'/');txt+= '='.repeat((4-txt.length%4)%4);let j;try{j=JSON.parse(atob(txt));}catch{throw Error('Invalid VMess link');}return {tag:decodeURIComponent(j.ps||'vmess-import'),protocol:'vmess',settings:{vnext:[{address:j.add||'',port:Number(j.port)||443,users:[{id:j.id||'',security:j.scy||'auto'}]}]},streamSettings:linkStream({type:j.net||'tcp',security:j.tls||'none',host:j.host||'',path:j.path||'',serviceName:j.path||j.serviceName||'',sni:j.sni||'',fp:j.fp||''})};}
 if(raw.startsWith('vless://')||raw.startsWith('trojan://')){const u=new URL(raw),p=raw.startsWith('vless://')?'vless':'trojan',q=u.searchParams,tag=decodeURIComponent((u.hash||'#'+p+'-import').slice(1));const server={address:u.hostname,port:Number(u.port)||443};let settings=p==='vless'?{...server,id:decodeURIComponent(u.username),flow:q.get('flow')||'',encryption:'none'}:{servers:[{...server,password:decodeURIComponent(u.username)}]};return {tag:tag||p+'-import',protocol:p,settings,streamSettings:linkStream({type:q.get('type')||'tcp',security:q.get('security')||'none',host:q.get('host')||'',path:q.get('path')||'',serviceName:q.get('serviceName')||'',sni:q.get('sni')||'',fp:q.get('fp')||'chrome',pbk:q.get('pbk')||'',sid:q.get('sid')||'',spx:q.get('spx')||'/'})};}
 throw Error(L('Guided import currently supports VLESS, VMess and Trojan links.','Import ساده فعلاً لینک‌های VLESS، VMess و Trojan را پشتیبانی می‌کند.'));
}
function linkStream(x){const st={network:x.type||'tcp',security:x.security||'none'};if(st.network==='tcp')st.tcpSettings={header:{type:'none'}};if(st.network==='ws')st.wsSettings={path:x.path||'/',host:x.host||''};if(st.network==='grpc')st.grpcSettings={serviceName:x.serviceName||'',multiMode:false};if(st.network==='httpupgrade')st.httpupgradeSettings={path:x.path||'/',host:x.host||''};if(st.network==='xhttp')st.xhttpSettings={path:x.path||'/',host:x.host||'',mode:'auto'};if(st.security==='tls')st.tlsSettings={serverName:x.sni||'',fingerprint:x.fp||'chrome'};if(st.security==='reality')st.realitySettings={serverName:x.sni||'',fingerprint:x.fp||'chrome',publicKey:x.pbk||'',shortId:x.sid||'',spiderX:x.spx||'/'};return st;}
async function importOutbound(){
 dialog(L('Import outbound link','Import لینک Outbound'),`<div class="xv3-editor"><section class="xv3-section"><div class="xv3-section-head"><b>${L('Paste share link','لینک را وارد کن')}</b><small>VLESS · VMess · Trojan</small></div>${textarea(L('Share link','لینک'),'share','',L('The link is parsed locally in your browser before saving.','لینک داخل مرورگر شما Parse می‌شود و بعد ذخیره می‌شود.'),'dir="ltr" spellcheck="false"')}</section></div>`,async fd=>{const o=parseLink(fd.get('share')),list=(await api('/api/settings/outbounds')).value||[];if(list.some(x=>x.tag===o.tag))o.tag=o.tag+'-'+Date.now().toString(36).slice(-4);closeDialog();await guidedOutboundFromObject(o,list);},L('Continue','ادامه'));
}
async function guidedOutboundFromObject(o,list){
 dialog(L('Review imported outbound','بررسی Outbound واردشده'),guidedBody(o,list),async(fd,form)=>{const out=buildOutbound(fd,o);if(list.some(x=>x.tag===out.tag))throw Error(L('Outbound tag already exists.','این Tag قبلاً وجود دارد.'));list.push(out);await api('/api/settings/outbounds','PUT',{value:list});closeDialog();await refresh();});
 const form=document.querySelector('#dialog-form');if(form){form.addEventListener('input',()=>syncEditor(form));form.addEventListener('change',()=>syncEditor(form));syncEditor(form);}
}

function listField(label,name,value,help=''){return textarea(label,name,(value||[]).join('\n'),help,'dir="ltr"');}
async function guidedRule(index=null){
 const routing=(await api('/api/settings/routing')).value||{},rules=routing.rules||[],outs=(await api('/api/settings/outbounds')).value||[],bals=routing.balancers||[],r=index==null?{type:'field',outboundTag:outs[0]?.tag||''}:clone(rules[index]);
 const targetType=r.balancerTag?'balancer':'outbound',target=r.balancerTag||r.outboundTag||'';
 const inTags=new Set(r.inboundTag||[]);
 const checks=(state.inbounds||[]).map(x=>{const tag=x.tag||('inbound-'+x.id);return `<label class="xv3-check"><input type="checkbox" name="ruleInbound" value="${esc(tag)}" ${inTags.has(tag)?'checked':''}><span>${esc(x.remark||tag)}<small>${esc(tag)}</small></span></label>`;}).join('');
 const body=`<div class="xv3-editor"><section class="xv3-section"><div class="xv3-section-head"><b>1 · ${L('Match traffic','شرط ترافیک')}</b><small>${L('Leave a field empty when it should not limit the rule.','هر شرطی لازم نیست خالی بگذار.')}</small></div><div class="xv3-grid">${listField(L('Domains / GeoSite','دامنه / GeoSite'),'domain',r.domain||[],L('Examples: geosite:google, domain:example.com','مثال: geosite:google یا domain:example.com'))}${listField(L('IPs / GeoIP','IP / GeoIP'),'ip',r.ip||[],L('Examples: geoip:private, 1.1.1.0/24','مثال: geoip:private یا CIDR'))}${fld(L('Port / range','پورت / بازه'),'rulePort',r.port||'','text','dir="ltr" placeholder="80,443,1000-2000"')}${sel(L('Network','شبکه'),'ruleNetwork',[['',L('Any','همه')],['tcp','TCP'],['udp','UDP'],['tcp,udp','TCP + UDP']],r.network||'')}${fld(L('Detected protocol','پروتکل تشخیص‌داده‌شده'),'ruleProtocol',r.protocol||'','text','dir="ltr" placeholder="http,tls,bittorrent"')}${textarea(L('Extra inbound tags','Inbound Tag اضافه'),'ruleInboundExtra',(r.inboundTag||[]).filter(x=>!new Set((state.inbounds||[]).map(i=>i.tag||('inbound-'+i.id))).has(x)).join('\n'),'','dir="ltr"')}${checks?`<div class="xv3-span-2"><span class="xv3-label">${L('Known inbounds','اینباندهای موجود')}</span><div class="xv3-check-grid">${checks}</div></div>`:''}</div></section>
 <section class="xv3-section"><div class="xv3-section-head"><b>2 · ${L('Destination','مقصد')}</b><small>${L('Choose an existing outbound or balancer instead of typing a tag.','به‌جای تایپ Tag، از Outbound یا Balancer موجود انتخاب کن.')}</small></div><div class="xv3-grid">${sel(L('Destination type','نوع مقصد'),'targetType',[['outbound','Outbound'],['balancer','Balancer']],targetType)}${sel('Outbound','targetOutbound',outs.map(x=>[x.tag,`${x.tag} · ${x.protocol}`]),targetType==='outbound'?target:(outs[0]?.tag||''))}${sel('Balancer','targetBalancer',bals.length?bals.map(x=>[x.tag,x.tag]):[['',L('No balancer configured','بالانسری ساخته نشده')]],targetType==='balancer'?target:'')}</div></section>
 <section class="xv3-summary"><span>${L('Rule result','خلاصه Rule')}</span><b data-xv3-rule-summary></b><small>${L('Rules are evaluated from top to bottom.','Ruleها از بالا به پایین بررسی می‌شوند.')}</small></section></div>`;
 dialog(index==null?L('New routing rule · Guided V3','Rule جدید · Guided V3'):L('Edit routing rule · Guided V3','ویرایش Rule · Guided V3'),body,async(fd,form)=>{const x=clone(r);x.type='field';for(const k of ['domain','ip','inboundTag','port','network','protocol','outboundTag','balancerTag'])delete x[k];const domains=csv(fd.get('domain')),ips=csv(fd.get('ip')),inbounds=[...fd.getAll('ruleInbound'),...csv(fd.get('ruleInboundExtra'))];if(domains.length)x.domain=domains;if(ips.length)x.ip=ips;if(inbounds.length)x.inboundTag=[...new Set(inbounds)];const port=String(fd.get('rulePort')||'').trim(),network=String(fd.get('ruleNetwork')||''),protocol=String(fd.get('ruleProtocol')||'').trim();if(port)x.port=port;if(network)x.network=network;if(protocol)x.protocol=protocol;if(fd.get('targetType')==='balancer'){const v=required(fd,'targetBalancer','Balancer');x.balancerTag=v;}else{x.outboundTag=required(fd,'targetOutbound','Outbound');}if(index==null)rules.push(x);else rules[index]=x;routing.rules=rules;await api('/api/settings/routing','PUT',{value:routing});closeDialog();await refresh();});
 const form=document.querySelector('#dialog-form');if(form){const sync=()=>{const type=form.elements.targetType.value;form.elements.targetOutbound.closest('label').hidden=type!=='outbound';form.elements.targetBalancer.closest('label').hidden=type!=='balancer';const sum=form.querySelector('[data-xv3-rule-summary]');if(sum)sum.textContent=`${type.toUpperCase()} → ${type==='outbound'?form.elements.targetOutbound.value:form.elements.targetBalancer.value||'—'}`;};form.addEventListener('change',sync);sync();}
}
async function guidedBalancer(index=null){
 const routing=(await api('/api/settings/routing')).value||{},bs=routing.balancers||[],outs=(await api('/api/settings/outbounds')).value||[],b=index==null?{tag:'balance',selector:[],strategy:{type:'random'}}:clone(bs[index]),selected=new Set(b.selector||[]);
 const checks=outs.map(o=>`<label class="xv3-check"><input type="checkbox" name="selector" value="${esc(o.tag)}" ${selected.has(o.tag)?'checked':''}><span>${esc(o.tag)}<small>${esc(o.protocol)}</small></span></label>`).join('');
 const body=`<div class="xv3-editor"><section class="xv3-section"><div class="xv3-section-head"><b>1 · ${L('Balancer identity','هویت Balancer')}</b><small>${L('Selectors are outbound tags that participate in this pool.','Selectorها همان Outboundهای عضو این Pool هستند.')}</small></div><div class="xv3-grid">${fld('Tag','balTag',b.tag||'balance','text','required pattern="[A-Za-z0-9_.-]{1,128}"')}${sel(L('Strategy','استراتژی'),'balStrategy',[['random','Random'],['roundRobin','Round Robin'],['leastPing','Least Ping']],b.strategy?.type||'random',L('Least Ping automatically requires Observatory.','Least Ping به‌صورت خودکار Observatory را لازم دارد.'))}<div class="xv3-span-2"><span class="xv3-label">${L('Members','اعضا')}</span><div class="xv3-check-grid">${checks||L('Create an outbound first.','ابتدا یک Outbound بساز.')}</div></div>${sel(L('Fallback outbound','Fallback Outbound'),'fallbackTag',[['',L('None','ندارد')],...outs.map(o=>[o.tag,`${o.tag} · ${o.protocol}`])],b.fallbackTag||'')}</div></section><section class="xv3-summary"><span>${L('Balancer behavior','رفتار')}</span><b data-xv3-bal-summary></b><small>${L('leastLoad is intentionally hidden until DARK emits burstObservatory.','leastLoad تا زمان پشتیبانی burstObservatory در DARK نمایش داده نمی‌شود.')}</small></section></div>`;
 dialog(index==null?L('New balancer · Guided V3','Balancer جدید · Guided V3'):L('Edit balancer · Guided V3','ویرایش Balancer · Guided V3'),body,async(fd)=>{const tag=required(fd,'balTag','Tag');if(!TAG.test(tag))throw Error('Invalid balancer tag');const selectors=fd.getAll('selector').map(String);if(!selectors.length)throw Error(L('Choose at least one outbound member.','حداقل یک Outbound عضو انتخاب کن.'));const strategy=String(fd.get('balStrategy')||'random'),x={...b,tag,selector:selectors,strategy:{...(b.strategy||{}),type:strategy}};const fallback=String(fd.get('fallbackTag')||'');if(fallback)x.fallbackTag=fallback;else delete x.fallbackTag;if(index==null)bs.push(x);else bs[index]=x;routing.balancers=bs;await api('/api/settings/routing','PUT',{value:routing});if(strategy==='leastPing'){const obs=(await api('/api/settings/observatory')).value||{},subjects=[...new Set([...(obs.subjectSelector||[]),...selectors])];await api('/api/settings/observatory','PUT',{value:{...obs,subjectSelector:subjects,probeURL:obs.probeURL||'https://www.gstatic.com/generate_204',probeInterval:obs.probeInterval||'30s',enableConcurrency:obs.enableConcurrency!==false}});}closeDialog();await refresh();});
 const form=document.querySelector('#dialog-form');if(form){const sync=()=>{const sum=form.querySelector('[data-xv3-bal-summary]'),members=form.querySelectorAll('input[name=selector]:checked').length;if(sum)sum.textContent=`${form.elements.balStrategy.value} · ${members} ${L('members','عضو')}`;};form.addEventListener('change',sync);sync();}
}

runAction=async function(act,el){
 if(act==='xv2outnew'){await guidedOutbound();return;}
 if(act==='xv2outedit'){await guidedOutbound(Number(el.dataset.index));return;}
 if(act==='xv2outclone'){await guidedOutbound(Number(el.dataset.index),true);return;}
 if(act==='xv3outraw'){await rawOutbound(Number(el.dataset.index));return;}
 if(act==='xv3outimport'){await importOutbound();return;}
 if(act==='xv2rulenew'){await guidedRule();return;}
 if(act==='xv2ruleedit'){await guidedRule(Number(el.dataset.index));return;}
 if(act==='xv2balnew'){await guidedBalancer();return;}
 if(act==='xv2baledit'){await guidedBalancer(Number(el.dataset.index));return;}
 return baseRunAction(act,el);
};

globalThis.DarkXrayGuidedV3={buildOutbound,buildSettings,buildStream,parseLink,linkStream,protocols:PROTOCOLS.map(x=>x[0])};
})();