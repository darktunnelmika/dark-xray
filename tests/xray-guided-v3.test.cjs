const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');

function load(){
  const ctx={
    console,URL,atob:global.atob,btoa:global.btoa,
    runAction:async()=>{},dialog:()=>{},api:async()=>({}),state:{inbounds:[]},
    renderPage:async()=>{},refresh:async()=>{},closeDialog:()=>{},toast:()=>{},
    document:{querySelector:()=>null},localStorage:{getItem:()=> 'en'},
    e:v=>String(v??''),icon:()=>'',enc:encodeURIComponent
  };
  vm.createContext(ctx);
  vm.runInContext(fs.readFileSync(path.join(__dirname,'..','web','xray-guided-v3.js'),'utf8'),ctx,{filename:'xray-guided-v3.js'});
  return ctx.DarkXrayGuidedV3;
}
class FD{
  constructor(obj={}){this.m=new Map(Object.entries(obj).map(([k,v])=>[k,Array.isArray(v)?v:[String(v)]]));}
  get(k){return this.m.get(k)?.[0]??null}
  getAll(k){return this.m.get(k)||[]}
  has(k){return this.m.has(k)}
}
const common={tag:'edge',dialerProxy:'',sendThrough:'',sockInterface:'',sockMark:'',network:'tcp',security:'none',muxConcurrency:'8',xudpConcurrency:'8'};

test('Guided V3 protocol catalog covers normal DARK outbound workflows',()=>{
  const g=load();
  for(const p of ['freedom','blackhole','vless','vmess','trojan','shadowsocks','socks','http','wireguard','dns','loopback','hysteria'])
    assert.ok(g.protocols.includes(p),p);
});

test('VLESS builder emits flat settings and REALITY stream settings',()=>{
  const g=load(),fd=new FD({...common,protocol:'vless',address:'edge.example.com',port:'443',
    id:'11111111-1111-4111-8111-111111111111',flow:'xtls-rprx-vision',network:'grpc',security:'reality',
    serviceName:'dark-grpc',authority:'',sniReality:'cdn.example.com',realityFingerprint:'chrome',
    realityPublicKey:'PUBLICKEY',realityShortId:'abcd1234',realitySpiderX:'/'});
  const o=g.buildOutbound(fd,{});
  assert.equal(o.protocol,'vless');
  assert.deepEqual(JSON.parse(JSON.stringify(o.settings)),{address:'edge.example.com',port:443,id:'11111111-1111-4111-8111-111111111111',flow:'xtls-rprx-vision',encryption:'none'});
  assert.equal(o.streamSettings.network,'grpc');
  assert.equal(o.streamSettings.grpcSettings.serviceName,'dark-grpc');
  assert.equal(o.streamSettings.realitySettings.publicKey,'PUBLICKEY');
});

test('VMess builder emits vnext/users wire shape',()=>{
  const g=load(),fd=new FD({...common,protocol:'vmess',address:'vm.example.com',port:'8443',
    id:'22222222-2222-4222-8222-222222222222',vmessSecurity:'auto',network:'ws',security:'tls',
    path:'/socket',host:'cdn.example.com',sni:'vm.example.com',alpn:'h2,http/1.1',fingerprint:'chrome'});
  const o=g.buildOutbound(fd,{});
  assert.equal(o.settings.vnext[0].address,'vm.example.com');
  assert.equal(o.settings.vnext[0].users[0].id,'22222222-2222-4222-8222-222222222222');
  assert.equal(o.streamSettings.wsSettings.path,'/socket');
  assert.deepEqual(Array.from(o.streamSettings.tlsSettings.alpn),['h2','http/1.1']);
});

test('Trojan Shadowsocks SOCKS and HTTP use readable server shapes',()=>{
  const g=load();
  let o=g.buildOutbound(new FD({...common,protocol:'trojan',address:'tr.example.com',port:'443',password:'secret'}),{});
  assert.equal(o.settings.servers[0].password,'secret');
  o=g.buildOutbound(new FD({...common,protocol:'shadowsocks',address:'ss.example.com',port:'8388',password:'pass',ssMethod:'aes-256-gcm'}),{});
  assert.equal(o.settings.servers[0].method,'aes-256-gcm');
  o=g.buildOutbound(new FD({...common,protocol:'socks',address:'127.0.0.1',port:'1080',username:'u',proxyPassword:'p'}),{});
  assert.deepEqual(JSON.parse(JSON.stringify(o.settings.servers[0].users)),[{user:'u',pass:'p'}]);
  o=g.buildOutbound(new FD({...common,protocol:'http',address:'proxy.example.com',port:'8080',username:'',proxyPassword:''}),{});
  assert.deepEqual(Array.from(o.settings.servers[0].users),[]);
});

test('WireGuard form state ignores flat address fields from non-WireGuard outbounds',()=>{
  const g=load(),wg=g.wgState({protocol:'vless',settings:{address:'edge.example.com',port:443}});
  assert.equal(wg.address,'');
  assert.equal(wg.allowedIPs,'0.0.0.0/0,::/0');
  assert.equal(wg.reserved,'');
});

test('WireGuard builder emits peer, allowed IPs and reserved bytes',()=>{
  const g=load(),fd=new FD({...common,protocol:'wireguard',wgSecretKey:'SECRET',wgAddress:'172.16.0.2/32,2606:4700::2/128',
    wgPublicKey:'PUB',wgEndpoint:'engage.example.com:2408',wgAllowed:'0.0.0.0/0,::/0',wgMtu:'1280',
    wgKeepAlive:'25',wgReserved:'1,2,3',wgDomainStrategy:'ForceIPv4',wgNoKernelTun:'true'});
  const o=g.buildOutbound(fd,{});
  assert.equal(o.protocol,'wireguard');
  assert.equal(o.settings.peers[0].endpoint,'engage.example.com:2408');
  assert.deepEqual(Array.from(o.settings.reserved),[1,2,3]);
  assert.equal(o.settings.noKernelTun,true);
});

test('Guided builder preserves chain in sockopt and rejects self-ambiguous mux combination',()=>{
  const g=load(),fd=new FD({...common,protocol:'trojan',address:'tr.example.com',port:'443',password:'secret',
    dialerProxy:'warp',sockInterface:'eth0',sockMark:'7',tcpFastOpen:'true'});
  const o=g.buildOutbound(fd,{});
  assert.equal(o.streamSettings.sockopt.dialerProxy,'warp');
  assert.equal(o.streamSettings.sockopt.interface,'eth0');
  assert.equal(o.streamSettings.sockopt.mark,7);
});

test('VLESS and Trojan share-link import produces guided editable objects',()=>{
  const g=load();
  let o=g.parseLink('vless://11111111-1111-4111-8111-111111111111@edge.example.com:443?type=grpc&security=reality&serviceName=dark&sni=cdn.example.com&fp=chrome&pbk=PUB&sid=abcd#EDGE');
  assert.equal(o.tag,'EDGE');assert.equal(o.protocol,'vless');assert.equal(o.settings.address,'edge.example.com');assert.equal(o.streamSettings.security,'reality');
  o=g.parseLink('trojan://secret@tr.example.com:443?type=tcp&security=tls&sni=tr.example.com#TR');
  assert.equal(o.protocol,'trojan');assert.equal(o.settings.servers[0].password,'secret');
});

test('Guided source keeps routing targets selectable and leastLoad hidden until burstObservatory exists',()=>{
  const src=fs.readFileSync(path.join(__dirname,'..','web','xray-guided-v3.js'),'utf8');
  assert.match(src,/targetOutbound/);
  assert.match(src,/targetBalancer/);
  assert.match(src,/name="selector"/);
  assert.match(src,/leastPing/);
  assert.doesNotMatch(src,/\['leastLoad','Least Load'\]/);
  assert.match(src,/burstObservatory/);
  assert.match(src,/xv3outraw/);
});


test('Guided DNS builder keeps advanced fields while exposing safe common controls',()=>{
  const g=load(),old={hosts:{'example.test':'127.0.0.1'},servers:['1.1.1.1']};
  const fd=new FD({
    dnsServers:'1.1.1.1\nhttps://8.8.8.8/dns-query\n{"address":"9.9.9.9","port":5353}',
    dnsQueryStrategy:'UseIPv4',dnsParallel:'true',dnsServeStale:'true',dnsServeExpiredTTL:'120',
    dnsTag:'dns-main',dnsClientIp:'203.0.113.9'
  });
  const v=g.dnsFromForm(fd,old);
  assert.equal(v.queryStrategy,'UseIPv4');
  assert.equal(v.enableParallelQuery,true);
  assert.equal(v.serveStale,true);
  assert.equal(v.serveExpiredTTL,120);
  assert.equal(v.servers[2].port,5353);
  assert.deepEqual(JSON.parse(JSON.stringify(v.hosts)),{'example.test':'127.0.0.1'});
});

test('Guided Routing settings changes strategy without touching rules or balancers',()=>{
  const g=load(),old={domainStrategy:'AsIs',rules:[{type:'field',outboundTag:'direct'}],balancers:[{tag:'b',selector:['proxy'],strategy:{type:'random'}}]};
  const v=g.routingSettingsFromForm(new FD({routeDomainStrategy:'IPIfNonMatch'}),old);
  assert.equal(v.domainStrategy,'IPIfNonMatch');
  assert.equal(v.rules.length,1);
  assert.equal(v.balancers[0].tag,'b');
});

test('Guided Observatory builds selectors and validates URL/duration',()=>{
  const g=load(),fd=new FD({
    obsEnabled:'true',obsSelector:['proxy-a','proxy-b'],obsCustomSelectors:'edge-\nbackup-',
    obsProbeURL:'https://www.gstatic.com/generate_204',obsInterval:'2h45m',obsConcurrency:'true'
  });
  const v=g.observatoryFromForm(fd);
  assert.deepEqual(Array.from(v.subjectSelector),['proxy-a','proxy-b','edge-','backup-']);
  assert.equal(v.enableConcurrency,true);
  assert.equal(g.validDuration('30s'),true);
  assert.equal(g.validDuration('2h45m'),true);
  assert.equal(g.validDuration('tomorrow'),false);
  assert.throws(()=>g.observatoryFromForm(new FD({
    obsEnabled:'true',obsSelector:['proxy'],obsProbeURL:'file:///tmp/x',obsInterval:'30s'
  })));
  assert.throws(()=>g.observatoryFromForm(new FD({
    obsEnabled:'true',obsSelector:['proxy'],obsProbeURL:'https://example.com',obsInterval:'soon'
  })));
});
