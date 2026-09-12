/* Pure form -> Xray data mappings, shared by UI and Node tests. No DOM. */
(function(root){
 'use strict';
 const clone=v=>JSON.parse(JSON.stringify(v));
 const list=v=>String(v||'').split(/[\n,]/).map(x=>x.trim()).filter(Boolean);
 const int=(v,min=1,max=65535)=>{const n=Number(v);if(!Number.isInteger(n)||n<min||n>max)throw Error('عدد خارج از محدوده است.');return n;};
 function host(v,old={}){
  if(!v.address||/[\s\/?#@]/.test(v.address))throw Error('آدرس باید IP یا دامنهٔ بدون مسیر باشد.');
  const r={...clone(old),inboundId:int(v.inboundId),address:v.address.trim(),port:int(v.port),remark:v.remark||'',enable:v.enable!==false};
  for(const k of ['sni','host','path','alpn','fingerprint']){if(v[k])r[k]=v[k].trim();else delete r[k];}return r;
 }
 function outbound(v,old={}){
  if(!/^[A-Za-z0-9_.-]{1,128}$/.test(v.tag)||v.tag==='dark-api')throw Error('Tag معتبر و یکتا لازم است.');
  const r={...clone(old),tag:v.tag,protocol:v.protocol};
  const same=old.protocol===v.protocol;
  const oldSettings=same?clone(old.settings||{}):{};
  if(same&&((oldSettings.vnext||[]).length>1||(oldSettings.servers||[]).length>1||
     (oldSettings.vnext?.[0]?.users||oldSettings.servers?.[0]?.users||[]).length>1))throw Error('برای خروجی چندسرور یا چندکاربر، از JSON پیشرفته استفاده کن؛ اطلاعات حذف نمی‌شود.');
  if(!same)delete r.streamSettings;
  if(v.protocol==='freedom')r.settings={...oldSettings,domainStrategy:v.domainStrategy||'AsIs'};
  else if(v.protocol==='blackhole')r.settings={...oldSettings,response:{type:v.response||'none'}};
  else{
   if(!v.address||/[\s\/?#@]/.test(v.address))throw Error('آدرس خروجی معتبر نیست.');
   const port=int(v.port),address=v.address.trim();
   if(['vless','vmess'].includes(v.protocol)){
    if(!/^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i.test(v.credential))throw Error('UUID لازم است.');
    const previous=oldSettings.vnext?.[0]||{},user={...(previous.users?.[0]||{}),id:v.credential};
    if(v.protocol==='vless'){user.encryption='none';if(v.flow)user.flow=v.flow;}else user.security='auto';
    r.settings={...oldSettings,vnext:[{...previous,address,port,users:[user]}]};
   }else if(['trojan','shadowsocks'].includes(v.protocol)){
    if(!v.credential)throw Error('رمز خروجی لازم است.');
    const server={...(oldSettings.servers?.[0]||{}),address,port,password:v.credential};if(v.protocol==='shadowsocks')server.method=v.method||'aes-128-gcm';
    r.settings={...oldSettings,servers:[server]};
   }else if(['socks','http'].includes(v.protocol)){
    const server={...(oldSettings.servers?.[0]||{}),address,port};if(v.username)server.users=[{...(server.users?.[0]||{}),user:v.username,pass:v.credential||''}];else delete server.users;
    r.settings={...oldSettings,servers:[server]};
   }else throw Error('این پروتکل فعلاً از ویرایشگر پیشرفته تنظیم می‌شود.');
   const st=same?clone(old.streamSettings||{}):{};
   st.network=v.network||'tcp';st.security=v.security||'none';
   for(const key of ['tcpSettings','wsSettings','grpcSettings','httpupgradeSettings','xhttpSettings','tlsSettings','realitySettings']){
    if(key!==st.network+'Settings'&&key!==st.security+'Settings')delete st[key];
   }
   if(st.network==='grpc')st.grpcSettings={...(st.grpcSettings||{}),serviceName:v.path||''};
   if(['ws','httpupgrade','xhttp'].includes(st.network)){
    st[st.network+'Settings']={...(st[st.network+'Settings']||{}),path:v.path||'/'};
    if(st.network==='ws')st.wsSettings.headers={...(st.wsSettings.headers||{}),Host:v.host||''};else st[st.network+'Settings'].host=v.host||'';
   }
   if(st.security==='tls')st.tlsSettings={...(st.tlsSettings||{}),serverName:v.sni||'',allowInsecure:false};
   if(st.security==='reality'){
    if(!v.publicKey||!v.sni)throw Error('SNI و کلید عمومی REALITY لازم است.');
    st.realitySettings={...(st.realitySettings||{}),serverName:v.sni,password:v.publicKey,shortId:v.shortId||'',fingerprint:v.fingerprint||'chrome'};
   }
   r.streamSettings=st;
  }
  if(v.dialerProxy){r.streamSettings=r.streamSettings||{};r.streamSettings.sockopt={...(r.streamSettings.sockopt||{}),dialerProxy:v.dialerProxy};}
  else if(r.streamSettings?.sockopt)delete r.streamSettings.sockopt.dialerProxy;
  return r;
 }
 function route(v,old={}){
  const r={...clone(old),type:'field'};for(const key of ['domain','ip','inboundTag','source']){const a=list(v[key]);if(a.length)r[key]=a;else delete r[key];}
  for(const key of ['port','network','protocol']){if(v[key])r[key]=key==='protocol'?list(v[key]):v[key];else delete r[key];}
  delete r.outboundTag;delete r.balancerTag;
  if(!v.destination)throw Error('خروجی یا بالانسر را انتخاب کن.');
  if(v.destination.startsWith('balancer:'))r.balancerTag=v.destination.slice(9);else r.outboundTag=v.destination;
  return r;
 }
 const api={host,outbound,route,list,int};root.DarkForms=api;if(typeof module!=='undefined')module.exports=api;
})(globalThis);
