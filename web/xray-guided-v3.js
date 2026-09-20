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
function wgState(o){const s=o?.settings||{},p=Array.isArray(s.peers)?(s.peers[0]||{}):{};const address=Array.isArray(s.address)?s.address:[],allowed=Array.isArray(p.allowedIPs)?p.allowedIPs:['0.0.0.0/0','::/0'],reserved=Array.isArray(s.reserved)?s.reserved:[];return {secretKey:s.secretKey||'',address:address.join(','),publicKey:p.publicKey||'',endpoint:p.endpoint||'',preSharedKey:p.preSharedKey||'',allowedIPs:allowed.join(','),keepAlive:p.keepAlive||0,mtu:s.mtu||1420,reserved:reserved.join(','),domainStrategy:s.domainStrategy||'',noKernelTun:!!s.noKernelTun};}
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
  blackhole:L('Drop matching traffic. Use it with Routing for ads or blocked destinations.','ترافیک مطابق قانون را قطع می‌کند؛ برای تبلیغات یا مقصدهای مسدود در مسیریابی.'),
  vless:L('Connect to another VLESS server using UUID, transport and optional TLS/REALITY.','اتصال به سرور VLESS دیگر با UUID، ترنسپورت و TLS/REALITY اختیاری.'),
  vmess:L('Connect to a VMess server. Transport and TLS are configured below.','اتصال به سرور VMess؛ ترنسپورت و TLS پایین تنظیم می‌شوند.'),
  trojan:L('Connect to a Trojan server using password and TLS/REALITY transport.','اتصال به Trojan با رمز و TLS/REALITY.'),
  shadowsocks:L('Connect to a Shadowsocks server with cipher and password.','اتصال به Shadowsocks با رمزنگاری و پسورد.'),
  socks:L('Use an upstream SOCKS5 proxy, with optional username/password.','استفاده از پراکسی SOCKS5 بالادست با یوزر/پسورد اختیاری.'),
  http:L('Use an upstream HTTP CONNECT proxy, with optional credentials.','استفاده از پروکسی HTTP بالادست با احراز هویت اختیاری.'),
  wireguard:L('Route traffic through a WireGuard peer. Keys and peer endpoint are required.','عبور ترافیک از همتای WireGuard؛ کلیدها و نقطه اتصال لازم هستند.'),
  dns:L('Send DNS queries through Xray DNS outbound rewriting.','ارسال درخواست‌های DNS از طریق اوتباند DNS خود Xray.'),
  loopback:L('Loop traffic back into an existing inbound tag. Advanced topology use.','برگرداندن ترافیک به یک Inbound موجود؛ مناسب توپولوژی پیشرفته.'),
  hysteria:L('Native Hysteria v2 outbound when supported by the installed Xray core.','Hysteria v2 بومی؛ فقط وقتی هستهٔ نصب‌شده آن را پشتیبانی کند.')
 };
 return map[p]||'';
}
function outboundTags(list,current=''){return [['',L('No chain / direct dial','بدون زنجیره')],...list.filter(x=>x.tag!==current).map(x=>[x.tag,`${x.tag} · ${x.protocol}`])];}
function inboundTags(){return (state.inbounds||[]).map(x=>[x.tag||('inbound-'+x.id),x.remark?`${x.remark} · ${x.tag||x.id}`:x.tag||String(x.id)]);}

function guidedBody(o,list){
 const p=o.protocol||'freedom',sv=firstServer(o),wg=wgState(o),st=streamState(o),settings=o.settings||{},mux=o.mux||{};
 const proto=sel(L('Outbound type','نوع اوتباند'),'protocol',PROTOCOLS,p,L('Choose the purpose first. Fields below change automatically.','اول نوع خروجی را انتخاب کن؛ فیلدهای پایین خودکار تغییر می‌کنند.'));
 const common=`<section class="xv3-section"><div class="xv3-section-head"><b>1 · ${L('Identity & type','هویت و نوع')}</b><small>${L('Give the outbound a unique tag and choose its behavior.','یک تگ یکتا بده و نوع عملکرد را انتخاب کن.')}</small></div><div class="xv3-grid">${fld('Tag','tag',o.tag||'proxy','text','required pattern="[A-Za-z0-9_.-]{1,128}" dir="ltr"',L('Routing and chaining refer to this tag.','مسیریابی و زنجیره با این تگ کار می‌کنند.'))}${proto}<div class="xv3-span-2 xv3-protocol-help" data-xv3-help>${protocolHelp(p)}</div></div></section>`;
 const server=`<section class="xv3-section" data-xv3-protocols="vless vmess trojan shadowsocks socks http hysteria"><div class="xv3-section-head"><b>2 · ${L('Remote server','سرور مقصد')}</b><small>${L('Where this outbound connects.','این اوتباند به کجا وصل می‌شود.')}</small></div><div class="xv3-grid">
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
 const loopback=`<section class="xv3-section" data-xv3-protocols="loopback"><div class="xv3-section-head"><b>2 · Loopback</b><small>${L('Send traffic back to an inbound tag.','ترافیک را به تگ یک اینباند برگردان.')}</small></div><div class="xv3-grid">${sel(L('Inbound tag','تگ اینباند'),'loopInbound',[['','—'],...inboundTags()],settings.inboundTag||'')}</div></section>`;
 const transport=`<section class="xv3-section" data-xv3-stream><div class="xv3-section-head"><b>3 · ${L('Transport & security','ترنسپورت و امنیت')}</b><small>${L('Only fields relevant to the selected transport are shown.','فقط فیلدهای مربوط به ترنسپورت انتخاب‌شده نمایش داده می‌شوند.')}</small></div><div class="xv3-grid">
 ${sel(L('Network','شبکه'),'network',NETWORKS,st.network)}
 ${sel(L('Security','امنیت'),'security',SECURITY,st.security)}
 <div data-xv3-networks="ws httpupgrade xhttp" class="xv3-grid xv3-span-2">${fld('Path','path',st.path,'text','dir="ltr"')}${fld('Host','host',st.host,'text','dir="ltr"')}</div>
 <div data-xv3-networks="grpc" class="xv3-grid xv3-span-2">${fld('serviceName','serviceName',st.serviceName,'text','dir="ltr"')}${fld('authority','authority',st.authority,'text','dir="ltr"')}</div>
 <div data-xv3-networks="xhttp" class="xv3-span-2">${sel('XHTTP mode','xhttpMode',[['auto','auto'],['packet-up','packet-up'],['stream-up','stream-up'],['stream-one','stream-one']],st.xhttpMode)}</div>
 <div data-xv3-networks="kcp" class="xv3-grid xv3-span-2">${fld('MTU','kcpMtu',st.kcpMtu,'number','min="576" max="1460"')}${fld('TTI','kcpTti',st.kcpTti,'number','min="10" max="5000"')}</div>
 <div data-xv3-security="tls" class="xv3-grid xv3-span-2">${fld('Server Name / SNI','sni',st.sni,'text','dir="ltr"')}${fld('ALPN','alpn',st.alpn,'text','dir="ltr" placeholder="h2,http/1.1"')}${sel('Fingerprint','fingerprint',[['chrome','chrome'],['firefox','firefox'],['safari','safari'],['edge','edge'],['randomized','randomized']],st.fingerprint)}<div>${toggle('Allow insecure TLS','allowInsecure',st.allowInsecure,L('Avoid unless the upstream certificate cannot be verified.','فقط وقتی گواهی مقصد قابل اعتبارسنجی نیست استفاده کن.'))}</div></div>
 <div data-xv3-security="reality" class="xv3-grid xv3-span-2">${fld('Server Name / SNI','sniReality',st.sni,'text','dir="ltr"')}${sel('Fingerprint','realityFingerprint',[['chrome','chrome'],['firefox','firefox'],['safari','safari'],['edge','edge'],['randomized','randomized']],st.fingerprint)}${fld('Public Key','realityPublicKey',st.publicKey,'text','dir="ltr"')}${fld('Short ID','realityShortId',st.shortId,'text','dir="ltr"')}${fld('SpiderX','realitySpiderX',st.spiderX,'text','dir="ltr"')}</div>
 </div></section>`;
 const advanced=`<section class="xv3-section"><details class="xv3-advanced"><summary>4 · ${L('Chain & advanced','زنجیره و پیشرفته')}</summary><div class="xv3-grid">
 ${sel(L('Dial through outbound','عبور از اوتباند دیگر'),'dialerProxy',outboundTags(list,o.tag),st.dialerProxy,L('Equivalent to Xray sockopt.dialerProxy. Cycles are rejected by DARK.','معادل dialerProxy در Xray؛ چرخه توسط DARK رد می‌شود.'))}
 ${fld('sendThrough','sendThrough',st.sendThrough,'text','dir="ltr"',L('Optional local source IP. Leave blank for normal routing.','IP مبدا محلی اختیاری؛ معمولاً خالی بماند.'))}
 ${fld(L('Network interface','اینترفیس شبکه'),'sockInterface',st.iface,'text','dir="ltr"')}
 ${fld('SO_MARK','sockMark',st.mark,'number','min="0"')}
 <div>${toggle('TCP Fast Open','tcpFastOpen',st.tcpFastOpen)}</div>
 <div data-xv3-mux>${toggle('Mux','muxEnabled',st.muxEnabled,L('Disabled automatically for VLESS Vision and XHTTP.','برای VLESS Vision و XHTTP نباید فعال باشد.'))}</div>
 <div data-xv3-mux-fields class="xv3-grid xv3-span-2">${fld('Mux concurrency','muxConcurrency',st.muxConcurrency,'number','min="-1" max="1024"')}${fld('XUDP concurrency','xudpConcurrency',st.xudpConcurrency,'number','min="-1" max="1024"')}</div>
 </div></details></section>`;
 const summary=`<section class="xv3-summary"><span>${L('Result','خلاصه')}</span><b data-xv3-summary></b><small>${L('DARK saves first. Use Validate before applying Xray.','DARK ابتدا ذخیره می‌کند؛ قبل از اعمال Xray حتماً اعتبارسنجی کن.')}</small></section>`;
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
 if(p==='loopback'){return {...old,inboundTag:required(fd,'loopInbound',L('Inbound tag','تگ اینباند'))};}
 if(p==='hysteria'){return {...old,address:required(fd,'address',L('Address','آدرس')),port:validPort(fd),version:2};}
 throw Error(L('Unsupported guided protocol. Use Advanced JSON.','این پروتکل در فرم ساده پشتیبانی نمی‌شود؛ از JSON پیشرفته استفاده کن.'));
}
function buildOutbound(fd,original={}){
 const p=String(fd.get('protocol')||'freedom'),tag=required(fd,'tag','Tag');if(!TAG.test(tag))throw Error(L('Tag may contain only letters, numbers, dot, underscore and dash.','تگ فقط می‌تواند شامل حروف، عدد، نقطه، خط تیره و زیرخط باشد.'));
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
 dialog(index==null?L('New outbound · Guided V3','اوتباند جدید · هدایت‌شده V3'):L('Edit outbound · Guided V3','ویرایش اوتباند · هدایت‌شده V3'),guidedBody(original,list),async(fd,form)=>{
   const out=buildOutbound(fd,original);
   if(list.some((x,i)=>i!==index&&x.tag===out.tag))throw Error(L('Outbound tag already exists.','این تگ قبلاً استفاده شده است.'));
   if(index==null||cloneMode)list.push(out);else list[index]=out;
   await api('/api/settings/outbounds','PUT',{value:list});
   toast(L('Outbound saved. Validate before applying Xray.','اوتباند ذخیره شد؛ قبل از اعمال، Xray را اعتبارسنجی کن.'));
   closeDialog();await refresh();
 });
 const form=document.querySelector('#dialog-form');if(form){form._darkOutboundOriginal=clone(original);form.addEventListener('input',()=>syncEditor(form));form.addEventListener('change',()=>syncEditor(form));syncEditor(form);}
}
async function rawOutbound(index){
 const list=(await api('/api/settings/outbounds')).value||[],o=list[index];if(!o)throw Error('Outbound not found');
 dialog(L('Advanced outbound JSON','JSON پیشرفتهٔ اوتباند'),`<div class="notice warning">${L('Advanced mode bypasses the guided form. DARK still validates tag/chaining and Xray Validate should be run before apply.','حالت پیشرفته فرم ساده را دور می‌زند؛ DARK همچنان تگ/زنجیره را بررسی می‌کند و قبل از اعمال باید Xray اعتبارسنجی شود.')}</div>${textarea('Outbound JSON','raw',JSON.stringify(o,null,2),'','dir="ltr" spellcheck="false"')}`,async fd=>{let v;try{v=JSON.parse(String(fd.get('raw')||''));}catch{throw Error(L('Invalid JSON.','JSON نامعتبر است.'));}if(!v||typeof v!=='object'||Array.isArray(v))throw Error(L('Outbound JSON must be an object.','JSON اوتباند باید یک شیء باشد.'));list[index]=v;await api('/api/settings/outbounds','PUT',{value:list});closeDialog();await refresh();});
}
function parseLink(raw){
 raw=String(raw||'').trim();if(!raw)throw Error(L('Paste a share link first.','ابتدا لینک را وارد کن.'));
 if(raw.startsWith('vmess://')){let txt=raw.slice(8).replace(/-/g,'+').replace(/_/g,'/');txt+= '='.repeat((4-txt.length%4)%4);let j;try{j=JSON.parse(atob(txt));}catch{throw Error('Invalid VMess link');}return {tag:decodeURIComponent(j.ps||'vmess-import'),protocol:'vmess',settings:{vnext:[{address:j.add||'',port:Number(j.port)||443,users:[{id:j.id||'',security:j.scy||'auto'}]}]},streamSettings:linkStream({type:j.net||'tcp',security:j.tls||'none',host:j.host||'',path:j.path||'',serviceName:j.path||j.serviceName||'',sni:j.sni||'',fp:j.fp||''})};}
 if(raw.startsWith('vless://')||raw.startsWith('trojan://')){const u=new URL(raw),p=raw.startsWith('vless://')?'vless':'trojan',q=u.searchParams,tag=decodeURIComponent((u.hash||'#'+p+'-import').slice(1));const server={address:u.hostname,port:Number(u.port)||443};let settings=p==='vless'?{...server,id:decodeURIComponent(u.username),flow:q.get('flow')||'',encryption:'none'}:{servers:[{...server,password:decodeURIComponent(u.username)}]};return {tag:tag||p+'-import',protocol:p,settings,streamSettings:linkStream({type:q.get('type')||'tcp',security:q.get('security')||'none',host:q.get('host')||'',path:q.get('path')||'',serviceName:q.get('serviceName')||'',sni:q.get('sni')||'',fp:q.get('fp')||'chrome',pbk:q.get('pbk')||'',sid:q.get('sid')||'',spx:q.get('spx')||'/'})};}
 throw Error(L('Guided import currently supports VLESS, VMess and Trojan links.','ورود ساده فعلاً لینک‌های VLESS، VMess و Trojan را پشتیبانی می‌کند.'));
}
function linkStream(x){const st={network:x.type||'tcp',security:x.security||'none'};if(st.network==='tcp')st.tcpSettings={header:{type:'none'}};if(st.network==='ws')st.wsSettings={path:x.path||'/',host:x.host||''};if(st.network==='grpc')st.grpcSettings={serviceName:x.serviceName||'',multiMode:false};if(st.network==='httpupgrade')st.httpupgradeSettings={path:x.path||'/',host:x.host||''};if(st.network==='xhttp')st.xhttpSettings={path:x.path||'/',host:x.host||'',mode:'auto'};if(st.security==='tls')st.tlsSettings={serverName:x.sni||'',fingerprint:x.fp||'chrome'};if(st.security==='reality')st.realitySettings={serverName:x.sni||'',fingerprint:x.fp||'chrome',publicKey:x.pbk||'',shortId:x.sid||'',spiderX:x.spx||'/'};return st;}
async function importOutbound(){
 dialog(L('Import outbound link','ورود لینک اوتباند'),`<div class="xv3-editor"><section class="xv3-section"><div class="xv3-section-head"><b>${L('Paste share link','لینک را وارد کن')}</b><small>VLESS · VMess · Trojan</small></div>${textarea(L('Share link','لینک'),'share','',L('The link is parsed locally in your browser before saving.','لینک داخل مرورگر شما تجزیه می‌شود و بعد ذخیره می‌شود.'),'dir="ltr" spellcheck="false"')}</section></div>`,async fd=>{const o=parseLink(fd.get('share')),list=(await api('/api/settings/outbounds')).value||[];if(list.some(x=>x.tag===o.tag))o.tag=o.tag+'-'+Date.now().toString(36).slice(-4);closeDialog();await guidedOutboundFromObject(o,list);},L('Continue','ادامه'));
}
async function guidedOutboundFromObject(o,list){
 dialog(L('Review imported outbound','بررسی اوتباند واردشده'),guidedBody(o,list),async(fd,form)=>{const out=buildOutbound(fd,o);if(list.some(x=>x.tag===out.tag))throw Error(L('Outbound tag already exists.','این تگ قبلاً وجود دارد.'));list.push(out);await api('/api/settings/outbounds','PUT',{value:list});closeDialog();await refresh();});
 const form=document.querySelector('#dialog-form');if(form){form.addEventListener('input',()=>syncEditor(form));form.addEventListener('change',()=>syncEditor(form));syncEditor(form);}
}

function listField(label,name,value,help=''){return textarea(label,name,(value||[]).join('\n'),help,'dir="ltr"');}
async function guidedRule(index=null){
 const routing=(await api('/api/settings/routing')).value||{},rules=routing.rules||[],outs=(await api('/api/settings/outbounds')).value||[],bals=routing.balancers||[],r=index==null?{type:'field',outboundTag:outs[0]?.tag||''}:clone(rules[index]);
 const targetType=r.balancerTag?'balancer':'outbound',target=r.balancerTag||r.outboundTag||'';
 const inTags=new Set(r.inboundTag||[]);
 const knownInboundTags=new Set((state.inbounds||[]).map(i=>i.tag||('inbound-'+i.id)));
 const checks=(state.inbounds||[]).map(x=>{const tag=x.tag||('inbound-'+x.id);return `<label class="xv3-check"><input type="checkbox" name="ruleInbound" value="${esc(tag)}" ${inTags.has(tag)?'checked':''}><span>${esc(x.remark||tag)}<small>${esc(tag)}</small></span></label>`;}).join('');
 const attrs=JSON.stringify(r.attrs||{},null,2);
 const protocols=Array.isArray(r.protocol)?r.protocol.join('\n'):String(r.protocol||'').split(',').map(x=>x.trim()).filter(Boolean).join('\n');
 const sourceIPs=r.sourceIP||r.source||[];
 const body=`<div class="xv3-editor"><section class="xv3-section"><div class="xv3-section-head"><b>1 · ${L('Match destination','شرط مقصد')}</b><small>${L('All non-empty conditions must match. Rules run top to bottom.','همهٔ شرط‌های پرشده باید تطبیق پیدا کنند و قوانین از بالا به پایین اجرا می‌شوند.')}</small></div><div class="xv3-grid">
 ${fld(L('Rule name / tag','نام قانون'),'ruleTag',r.ruleTag||'','text','maxlength="128" dir="ltr"',L('Used for debugging and Route Preview.','برای اشکال‌زدایی و پیش‌نمایش مسیر استفاده می‌شود.'))}
 ${sel(L('Network','شبکه'),'ruleNetwork',[['',L('Any','همه')],['tcp','TCP'],['udp','UDP'],['tcp,udp','TCP + UDP']],r.network||'')}
 ${listField(L('Domains / GeoSite','دامنه / GeoSite'),'domain',r.domain||[],L('full:, domain:, keyword:, regexp:, geosite:, ext:','full: / domain: / keyword: / regexp: / geosite: / ext:'))}
 ${listField(L('Target IPs / GeoIP','IP مقصد / GeoIP'),'ip',r.ip||[],L('CIDR, geoip:, ext: and ! inverse forms are supported.','CIDR، geoip:، ext: و حالت معکوس ! پشتیبانی می‌شوند.'))}
 ${fld(L('Target port / range','پورت مقصد / بازه'),'rulePort',r.port||'','text','dir="ltr" placeholder="80,443,1000-2000"')}
 ${listField(L('Detected protocols','پروتکل‌های تشخیص‌داده‌شده'),'ruleProtocol',protocols?protocols.split('\n'):[],L('http, tls, quic, bittorrent — requires compatible sniffing.','http، tls، quic، bittorrent — نیازمند Sniffing سازگار.'))}
 </div></section>
 <section class="xv3-section"><div class="xv3-section-head"><b>2 · ${L('Inbound & identity','ورودی و هویت')}</b><small>${L('Match where traffic came from and which managed user produced it.','منبع ورود ترافیک و کاربر تولیدکننده آن را محدود کن.')}</small></div><div class="xv3-grid">
 ${listField(L('Users / email','کاربران / ایمیل'),'ruleUser',r.user||[],L('Exact values or regexp: patterns.','مقدار دقیق یا regexp:.'))}
 ${textarea(L('Extra inbound tags','تگ اینباند اضافه'),'ruleInboundExtra',(r.inboundTag||[]).filter(x=>!knownInboundTags.has(x)).join('\n'),'','dir="ltr"')}
 ${checks?`<div class="xv3-span-2"><span class="xv3-label">${L('Known inbounds','اینباندهای موجود')}</span><div class="xv3-check-grid">${checks}</div></div>`:''}
 </div></section>
 <section class="xv3-section"><details class="xv3-advanced"><summary>3 · ${L('Source / local / advanced match','مبدأ / محلی / شرط پیشرفته')}</summary><div class="xv3-grid">
 ${listField(L('Source IP / CIDR','IP مبدأ / CIDR'),'ruleSourceIP',sourceIPs,L('Matches the remote source address seen by Xray.','آدرس مبدأ مشاهده‌شده توسط Xray.'))}
 ${fld(L('Source Port','پورت مبدأ'),'ruleSourcePort',r.sourcePort||'','text','dir="ltr" placeholder="1024-65535"')}
 ${listField(L('Local IP','IP محلی'),'ruleLocalIP',r.localIP||[],L('Local inbound destination IP.','IP محلی که Inbound روی آن ترافیک را دریافت کرده.'))}
 ${fld(L('Local Port','پورت محلی'),'ruleLocalPort',r.localPort||'','text','dir="ltr" placeholder="443"')}
 ${fld(L('VLESS Route','مسیر VLESS'),'ruleVlessRoute',r.vlessRoute||'','text','dir="ltr" placeholder="1,14,100-200"')}
 ${listField(L('Local process','Process محلی'),'ruleProcess',r.process||[],L('Linux/Windows local-origin traffic only; self/ and xray/ are supported by Xray.','فقط ترافیک با مبدأ محلی؛ Xray از self/ و xray/ پشتیبانی می‌کند.'))}
 ${textarea(L('HTTP attrs JSON','JSON ویژگی‌های HTTP'),'ruleAttrs',attrs,L('Example: {":method":"GET",":path":"^/api"}','مثال: {":method":"GET",":path":"^/api"}'),'dir="ltr" spellcheck="false"')}
 </div></details></section>
 <section class="xv3-section"><div class="xv3-section-head"><b>4 · ${L('Destination','مقصد')}</b><small>${L('Choose an existing outbound or balancer instead of typing a tag.','به‌جای تایپ تگ، از اوتباند یا بالانسر موجود انتخاب کن.')}</small></div><div class="xv3-grid">${sel(L('Destination type','نوع مقصد'),'targetType',[['outbound',L('Outbound','اوتباند')],['balancer',L('Balancer','بالانسر')]],targetType)}${sel(L('Outbound','اوتباند'),'targetOutbound',outs.map(x=>[x.tag,`${x.tag} · ${x.protocol}`]),targetType==='outbound'?target:(outs[0]?.tag||''))}${sel(L('Balancer','بالانسر'),'targetBalancer',bals.length?bals.map(x=>[x.tag,x.tag]):[['',L('No balancer configured','بالانسری ساخته نشده')]],targetType==='balancer'?target:'')}</div></section>
 <section class="xv3-summary"><span>${L('Rule result','خلاصهٔ قانون')}</span><b data-xv3-rule-summary></b><small>${L('Traffic Engine V4 can preview literal rules without sending traffic. GeoSite/GeoIP/DNS-dependent matches are reported as indeterminate.','موتور ترافیک V4 قوانین صریح را بدون ارسال ترافیک پیش‌نمایش می‌کند؛ موارد وابسته به GeoSite/GeoIP/DNS به‌صورت نامعین گزارش می‌شوند.')}</small></section></div>`;
 dialog(index==null?L('New routing rule · Guided V4','قانون جدید · هدایت‌شده V4'):L('Edit routing rule · Guided V4','ویرایش قانون · هدایت‌شده V4'),body,async(fd)=>{
   const x=clone(r);x.type='field';
   for(const k of ['domain','ip','sourceIP','source','localIP','user','process','inboundTag','port','sourcePort','localPort','vlessRoute','network','protocol','attrs','ruleTag','outboundTag','balancerTag'])delete x[k];
   const putList=(field,key)=>{const v=csv(fd.get(field));if(v.length)x[key]=v;};
   putList('domain','domain');putList('ip','ip');putList('ruleSourceIP','sourceIP');putList('ruleLocalIP','localIP');putList('ruleUser','user');putList('ruleProcess','process');putList('ruleProtocol','protocol');
   const inbounds=[...fd.getAll('ruleInbound'),...csv(fd.get('ruleInboundExtra'))];if(inbounds.length)x.inboundTag=[...new Set(inbounds)];
   for(const [field,key] of [['rulePort','port'],['ruleSourcePort','sourcePort'],['ruleLocalPort','localPort'],['ruleVlessRoute','vlessRoute']]){const v=String(fd.get(field)||'').trim();if(v)x[key]=v;}
   const network=String(fd.get('ruleNetwork')||'');if(network)x.network=network;
   const ruleTag=String(fd.get('ruleTag')||'').trim();if(ruleTag)x.ruleTag=ruleTag;
   const attrsText=String(fd.get('ruleAttrs')||'').trim();if(attrsText){let attrs;try{attrs=JSON.parse(attrsText);}catch{throw Error(L('HTTP attrs must be valid JSON.','Attrs باید JSON معتبر باشد.'));}if(!attrs||Array.isArray(attrs)||typeof attrs!=='object')throw Error(L('HTTP attrs must be a JSON object.','Attrs باید JSON Object باشد.'));x.attrs=attrs;}
   if(fd.get('targetType')==='balancer'){x.balancerTag=required(fd,'targetBalancer','Balancer');}else{x.outboundTag=required(fd,'targetOutbound','Outbound');}
   if(index==null)rules.push(x);else rules[index]=x;routing.rules=rules;await api('/api/settings/routing','PUT',{value:routing});closeDialog();await refresh();
 });
 const form=document.querySelector('#dialog-form');if(form){const editor=form.querySelector('.xv3-editor'),sections=editor?[...editor.querySelectorAll(':scope > .xv3-section')]:[];if(sections.length>=4){editor.insertBefore(sections[3],sections[0]);const advanced=document.createElement('details');advanced.className='xv3-advanced xv3-rule-advanced';advanced.innerHTML='<summary>'+L('Advanced match conditions','شرط‌های پیشرفته')+'</summary>';sections[1].before(advanced);advanced.append(sections[1],sections[2]);}const sync=()=>{const type=form.elements.targetType.value;form.elements.targetOutbound.closest('label').hidden=type!=='outbound';form.elements.targetBalancer.closest('label').hidden=type!=='balancer';const sum=form.querySelector('[data-xv3-rule-summary]');if(sum)sum.textContent=`${type.toUpperCase()} → ${type==='outbound'?form.elements.targetOutbound.value:form.elements.targetBalancer.value||'—'}`;};form.addEventListener('change',sync);sync();}
}
async function guidedBalancer(index=null){
 const routing=(await api('/api/settings/routing')).value||{},bs=routing.balancers||[],outs=(await api('/api/settings/outbounds')).value||[],b=index==null?{tag:'balance',selector:[],strategy:{type:'random'}}:clone(bs[index]),selected=new Set(b.selector||[]);
 const checks=outs.map(o=>`<label class="xv3-check"><input type="checkbox" name="selector" value="${esc(o.tag)}" ${selected.has(o.tag)?'checked':''}><span>${esc(o.tag)}<small>${esc(o.protocol)}</small></span></label>`).join('');
 const body=`<div class="xv3-editor"><section class="xv3-section"><div class="xv3-section-head"><b>1 · ${L('Balancer identity','هویت بالانسر')}</b><small>${L('Selectors are Xray tag prefixes; a selector can match more than one outbound.','گزینشگر در Xray مبتنی بر پیشوند است؛ یک گزینشگر می‌تواند چند اوتباند با ابتدای تگ یکسان را تطبیق دهد.')}</small></div><div class="xv3-grid">${fld(L('Tag','تگ'),'balTag',b.tag||'balance','text','required pattern="[A-Za-z0-9_.-]{1,128}"')}${sel(L('Strategy','استراتژی'),'balStrategy',[['random',L('Random','تصادفی')],['roundRobin',L('Round Robin','چرخشی')],['leastPing',L('Least Ping','کمترین پینگ')]],b.strategy?.type||'random',L('Least Ping automatically requires Observatory.','Least Ping به‌صورت خودکار Observatory را لازم دارد.'))}<div class="xv3-span-2"><span class="xv3-label">${L('Members','اعضا')}</span><div class="xv3-check-grid">${checks||L('Create an outbound first.','ابتدا یک اوتباند بساز.')}</div></div>${sel(L('Fallback outbound','اوتباند جایگزین'),'fallbackTag',[['',L('None','ندارد')],...outs.map(o=>[o.tag,`${o.tag} · ${o.protocol}`])],b.fallbackTag||'')}</div></section><section class="xv3-summary"><span>${L('Balancer behavior','رفتار')}</span><b data-xv3-bal-summary></b><small>${L('leastLoad is intentionally hidden until DARK emits burstObservatory.','leastLoad تا زمان پشتیبانی burstObservatory در DARK نمایش داده نمی‌شود.')}</small></section></div>`;
 dialog(index==null?L('New balancer · Guided V3','بالانسر جدید · هدایت‌شده V3'):L('Edit balancer · Guided V3','ویرایش بالانسر · هدایت‌شده V3'),body,async(fd)=>{const tag=required(fd,'balTag','Tag');if(!TAG.test(tag))throw Error('Invalid balancer tag');const selectors=fd.getAll('selector').map(String);if(!selectors.length)throw Error(L('Choose at least one outbound member.','حداقل یک اوتباند عضو انتخاب کن.'));const strategy=String(fd.get('balStrategy')||'random'),x={...b,tag,selector:selectors,strategy:{...(b.strategy||{}),type:strategy}};const fallback=String(fd.get('fallbackTag')||'');if(fallback)x.fallbackTag=fallback;else delete x.fallbackTag;if(index==null)bs.push(x);else bs[index]=x;routing.balancers=bs;await api('/api/settings/routing','PUT',{value:routing});if(strategy==='leastPing'){const obs=(await api('/api/settings/observatory')).value||{},subjects=[...new Set([...(obs.subjectSelector||[]),...selectors])];await api('/api/settings/observatory','PUT',{value:{...obs,subjectSelector:subjects,probeURL:obs.probeURL||'https://www.gstatic.com/generate_204',probeInterval:obs.probeInterval||'30s',enableConcurrency:obs.enableConcurrency!==false}});}closeDialog();await refresh();});
 const form=document.querySelector('#dialog-form');if(form){const sync=()=>{const sum=form.querySelector('[data-xv3-bal-summary]'),members=form.querySelectorAll('input[name=selector]:checked').length;if(sum)sum.textContent=`${form.elements.balStrategy.value} · ${members} ${L('members','عضو')}`;};form.addEventListener('change',sync);sync();}
}


function dnsServerLines(servers){
 return (servers||[]).map(function(x){return typeof x==='string'?x:JSON.stringify(x);}).join('\n');
}
function parseDnsServers(raw){
 const lines=String(raw||'').split(/\r?\n/).map(function(x){return x.trim();}).filter(Boolean);
 if(!lines.length)throw Error(L('Add at least one DNS server.','حداقل یک DNS Server اضافه کن.'));
 return lines.map(function(x){
  if(x.charAt(0)==='{'){try{const o=JSON.parse(x);if(!o||Array.isArray(o)||typeof o!=='object')throw 0;return o;}catch(_){throw Error(L('One DNS server JSON line is invalid.','یکی از خط‌های JSON مربوط به DNS Server معتبر نیست.'));}}
  return x;
 });
}
function dnsFromForm(fd,old){
 const v=clone(old||{});
 v.servers=parseDnsServers(fd.get('dnsServers'));
 v.queryStrategy=String(fd.get('dnsQueryStrategy')||'UseIP');
 v.disableCache=formBool(fd,'dnsDisableCache');
 v.enableParallelQuery=formBool(fd,'dnsParallel');
 v.disableFallback=formBool(fd,'dnsDisableFallback');
 v.disableFallbackIfMatch=formBool(fd,'dnsDisableFallbackIfMatch');
 v.useSystemHosts=formBool(fd,'dnsUseSystemHosts');
 v.serveStale=formBool(fd,'dnsServeStale');
 v.serveExpiredTTL=Math.max(0,Math.floor(n(fd.get('dnsServeExpiredTTL'),0)));
 const tag=String(fd.get('dnsTag')||'').trim(),client=String(fd.get('dnsClientIp')||'').trim();
 if(tag)v.tag=tag;else delete v.tag;
 if(client)v.clientIp=client;else delete v.clientIp;
 return v;
}
async function guidedDNS(){
 const d=(await api('/api/settings/dns')).value||{};
 const body='<div class="xv3-editor">'
  +'<section class="xv3-section"><div class="xv3-section-head"><b>1 · '+L('DNS behavior','رفتار DNS')+'</b><small>'+L('Common Xray DNS options without raw JSON.','گزینه‌های اصلی DNS بدون نیاز به JSON خام.')+'</small></div><div class="xv3-grid">'
  +sel('queryStrategy','dnsQueryStrategy',[['UseIP','UseIP · IPv4 + IPv6'],['UseIPv4','UseIPv4'],['UseIPv6','UseIPv6'],['UseSystem','UseSystem']],d.queryStrategy||'UseIP',L('Global DNS query family used by Xray.','نوع Query سراسری DNS در Xray.'))
  +fld('Tag','dnsTag',d.tag||'','text','dir="ltr"',L('Optional DNS tag for routing/diagnostics.','تگ اختیاری برای مسیریابی و عیب‌یابی.'))
  +fld('clientIp / ECS','dnsClientIp',d.clientIp||d.clientIP||'','text','dir="ltr" placeholder="1.2.3.4"',L('Optional EDNS Client Subnet source IP.','IP اختیاری برای EDNS Client Subnet.'))
  +fld(L('Stale TTL (seconds)','TTL کش منقضی (ثانیه)'),'dnsServeExpiredTTL',d.serveExpiredTTL||0,'number','min="0" step="1"')
  +'<div>'+toggle(L('Disable cache','غیرفعال‌کردن Cache'),'dnsDisableCache',!!d.disableCache,L('Normally keep cache enabled.','معمولاً Cache روشن بماند.'))+'</div>'
  +'<div>'+toggle(L('Parallel queries','Query موازی'),'dnsParallel',!!d.enableParallelQuery,L('Race eligible upstream DNS servers in parallel.','سرورهای DNS واجدشرایط را هم‌زمان Query می‌کند.'))+'</div>'
  +'<div>'+toggle(L('Disable fallback','غیرفعال‌کردن مسیر جایگزین'),'dnsDisableFallback',!!d.disableFallback)+'</div>'
  +'<div>'+toggle(L('Disable fallback on match','قطع مسیر جایگزین هنگام تطبیق'),'dnsDisableFallbackIfMatch',!!d.disableFallbackIfMatch)+'</div>'
  +'<div>'+toggle(L('Use system hosts','استفاده از Hosts سیستم'),'dnsUseSystemHosts',!!d.useSystemHosts)+'</div>'
  +'<div>'+toggle(L('Serve stale cache','استفاده از کش قدیمی'),'dnsServeStale',!!d.serveStale,L('Only useful while DNS cache is enabled.','فقط وقتی Cache فعال است کاربرد دارد.'))+'</div>'
  +'</div></section>'
  +'<section class="xv3-section"><div class="xv3-section-head"><b>2 · '+L('Upstream DNS servers','سرورهای DNS بالادست')+'</b><small>'+L('One address per line: IP, localhost, tcp://, https:// DoH. Advanced server objects can stay as one JSON object per line.','هر خط یک آدرس: IP، localhost، tcp:// یا DoH. آبجکت‌های پیشرفته هم می‌توانند هرکدام یک JSON در یک خط باشند.')+'</small></div>'
  +textarea(L('DNS servers','DNS Serverها'),'dnsServers',dnsServerLines(d.servers||[]),L('Examples: 1.1.1.1 · 8.8.8.8 · https://1.1.1.1/dns-query','مثال: 1.1.1.1 · 8.8.8.8 · https://1.1.1.1/dns-query'),'dir="ltr" spellcheck="false"')
  +'</section>'
  +'<section class="xv3-summary"><span>'+L('Safe workflow','روند امن')+'</span><b>'+L('Save → Validate → Apply','ذخیره ← اعتبارسنجی ← اعمال')+'</b><small>'+L('Hosts and uncommon DNS fields already present are preserved; use Advanced JSON only for fields not shown here.','Hosts و فیلدهای خاص موجود حفظ می‌شوند؛ برای مواردی که اینجا نیست فقط از JSON پیشرفته استفاده کن.')+'</small></section>'
  +'</div>';
 dialog(L('DNS · Guided V3','DNS · هدایت‌شده V3'),body,async function(fd){await api('/api/settings/dns','PUT',{value:dnsFromForm(fd,d)});toast(L('DNS saved. Validate before applying Xray.','DNS ذخیره شد؛ قبل از اعمال، Xray را اعتبارسنجی کن.'));closeDialog();await refresh();});
}
function routingSettingsFromForm(fd,old){
 const v=clone(old||{});v.domainStrategy=String(fd.get('routeDomainStrategy')||'AsIs');return v;
}
async function guidedRouteSettings(){
 const v=(await api('/api/settings/routing')).value||{};
 const body='<div class="xv3-editor"><section class="xv3-section"><div class="xv3-section-head"><b>'+L('Domain resolution strategy','استراتژی تفکیک دامنه')+'</b><small>'+L('This changes when Xray resolves domains while evaluating IP routing rules.','مشخص می‌کند Xray هنگام بررسی قوانین IP چه زمانی دامنه را تفکیک کند.')+'</small></div><div class="xv3-grid">'
  +sel('domainStrategy','routeDomainStrategy',[['AsIs',L('AsIs · do not resolve for routing','AsIs · برای مسیریابی دامنه را تفکیک نکن')],['IPIfNonMatch',L('IPIfNonMatch · resolve only after no domain rule matches','IPIfNonMatch · فقط بعد از تطبیق‌نکردن قانون دامنه')],['IPOnDemand',L('IPOnDemand · resolve when an IP rule needs it','IPOnDemand · هنگام نیاز قوانین IP')]],v.domainStrategy||'AsIs')
  +'<div class="xv3-span-2 notice">'+L('Existing Rules and Balancers are preserved. AsIs is the safest default unless you intentionally route by GeoIP/CIDR for domain destinations.','قوانین و بالانسرهای فعلی دست‌نخورده می‌مانند. AsIs پیش‌فرض امن است مگر اینکه عمداً مقصدهای دامنه‌ای را با GeoIP/CIDR روت کنی.')+'</div>'
  +'</div></section></div>';
 dialog(L('Routing settings · Guided V3','تنظیمات مسیریابی · هدایت‌شده V3'),body,async function(fd){await api('/api/settings/routing','PUT',{value:routingSettingsFromForm(fd,v)});closeDialog();await refresh();});
}
function validDuration(v){
 return /^(?:\d+(?:ns|us|ms|s|m|h))+$/.test(String(v||''));
}
function observatoryFromForm(fd){
 if(!formBool(fd,'obsEnabled'))return {};
 const selected=fd.getAll('obsSelector').map(String),custom=csv(fd.get('obsCustomSelectors'));
 const subjectSelector=Array.from(new Set(selected.concat(custom)));
 if(!subjectSelector.length)throw Error(L('Choose at least one outbound selector.','حداقل یک گزینشگر برای اوتباند انتخاب کن.'));
 const probeURL=String(fd.get('obsProbeURL')||'').trim();
 let u;try{u=new URL(probeURL);}catch(_){throw Error(L('Probe URL is invalid.','آدرس Probe معتبر نیست.'));}
 if(!['http:','https:'].includes(u.protocol)||!u.hostname||u.username||u.password)throw Error(L('Probe URL must be a normal http/https URL without credentials.','Probe URL باید http/https معتبر و بدون نام کاربری/رمز باشد.'));
 const probeInterval=String(fd.get('obsInterval')||'').trim();
 if(!validDuration(probeInterval))throw Error(L('Probe interval must look like 10s, 1m or 2h45m.','فاصله Probe باید مثل 10s، 1m یا 2h45m باشد.'));
 return {subjectSelector:subjectSelector,probeURL:probeURL,probeInterval:probeInterval,enableConcurrency:formBool(fd,'obsConcurrency')};
}
async function guidedObservatory(){
 const o=(await api('/api/settings/observatory')).value||{},outs=(await api('/api/settings/outbounds')).value||[];
 const existing=o.subjectSelector||[],exact=new Set(outs.map(function(x){return x.tag;}));
 const custom=existing.filter(function(x){return !exact.has(x);});
 const checks=outs.map(function(x){return '<label class="xv3-check"><input type="checkbox" name="obsSelector" value="'+esc(x.tag)+'" '+(existing.includes(x.tag)?'checked':'')+'><span>'+esc(x.tag)+'<small>'+esc(x.protocol)+'</small></span></label>';}).join('');
 const enabled=Object.keys(o).length>0;
 const body='<div class="xv3-editor">'
  +'<section class="xv3-section"><div class="xv3-section-head"><b>1 · '+L('Observatory state','وضعیت Observatory')+'</b><small>'+L('Used by leastPing balancers to measure outbound health/latency.','برای سنجش سلامت و تأخیر اوتباندها در بالانسر نوع leastPing استفاده می‌شود.')+'</small></div>'
  +toggle(L('Enable Observatory','فعال‌سازی Observatory'),'obsEnabled',enabled||!existing.length,L('Uncheck and save to remove Observatory from the generated Xray config.','برای حذف Observatory از کانفیگ Xray تیک را بردار و ذخیره کن.'))
  +'</section>'
  +'<section class="xv3-section"><div class="xv3-section-head"><b>2 · '+L('Outbound selectors','گزینشگرهای اوتباند')+'</b><small>'+L('Xray selectors are prefix-based. Exact outbound tags below are convenient safe picks.','گزینشگر در Xray بر اساس پیشوند است؛ تگ‌های دقیق پایین انتخاب‌های ساده و قابل‌فهم هستند.')+'</small></div>'
  +'<div class="xv3-check-grid">'+(checks||L('Create an outbound first.','ابتدا یک اوتباند بساز.'))+'</div>'
  +textarea(L('Extra prefix selectors','گزینشگر پیشوندی اضافه'),'obsCustomSelectors',custom.join('\n'),L('Optional. Example: proxy- matches proxy-a, proxy-b, ...','اختیاری. مثال: proxy- همهٔ proxy-a و proxy-b و ... را تطبیق می‌دهد.'),'dir="ltr"')
  +'</section>'
  +'<section class="xv3-section"><div class="xv3-section-head"><b>3 · '+L('Probe behavior','رفتار Probe')+'</b></div><div class="xv3-grid">'
  +fld('Probe URL','obsProbeURL',o.probeURL||o.probeUrl||'https://www.gstatic.com/generate_204','url','required dir="ltr"')
  +fld('Probe interval','obsInterval',o.probeInterval||'30s','text','required dir="ltr" placeholder="30s"',L('Go-style duration such as 10s, 1m, 2h45m.','مدت به شکل 10s، 1m یا 2h45m.'))
  +'<div class="xv3-span-2">'+toggle(L('Concurrent probes','پروب هم‌زمان'),'obsConcurrency',o.enableConcurrency!==false,L('Faster with multiple outbounds, but creates a burst of probe requests.','برای چند اوتباند سریع‌تر است ولی پروب‌ها را هم‌زمان ارسال می‌کند.'))+'</div>'
  +'</div></section>'
  +'<section class="xv3-summary"><span>'+L('Balancer note','نکتهٔ بالانسر')+'</span><b>leastPing → Observatory</b><small>'+L('Creating a leastPing balancer already adds its members here automatically.','ساخت بالانسر نوع leastPing اعضای آن را خودکار به Observatory اضافه می‌کند.')+'</small></section>'
  +'</div>';
 dialog('Observatory · Guided V3',body,async function(fd){await api('/api/settings/observatory','PUT',{value:observatoryFromForm(fd)});closeDialog();await refresh();});
}

runAction=async function(act,el){
 if(act==='xv2dnsedit'){await guidedDNS();return;}
 if(act==='xv2routesettings'){await guidedRouteSettings();return;}
 if(act==='xv2obsedit'){await guidedObservatory();return;}
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

globalThis.DarkXrayGuidedV3={buildOutbound,buildSettings,buildStream,parseLink,linkStream,dnsFromForm,routingSettingsFromForm,observatoryFromForm,validDuration,protocols:PROTOCOLS.map(x=>x[0]),openOutbound:guidedOutbound,openRule:guidedRule,openBalancer:guidedBalancer,openObservatory:guidedObservatory,openRouteSettings:guidedRouteSettings,openImport:importOutbound,openRawOutbound:rawOutbound,wgState};
})();