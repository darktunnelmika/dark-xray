const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');

function context(){
  const ctx={
    console,
    state:{inbounds:[],hv2:{inbound:'all',search:''}},
    enginePage:async()=>'',runAction:async()=>{},
    localStorage:{getItem:()=> 'en'},
    e:v=>String(v??''),fa:v=>String(v),
    button:()=>'',heading:()=>'',empty:()=>'',select:()=>'',field:()=>'',api:async()=>({value:[]}),
    dialog:()=>{},toast:()=>{},refresh:async()=>{},renderPage:async()=>{},confirm:()=>true,
    document:{addEventListener(){},querySelector(){return null}},
    location:{hostname:'panel.example.test'},setTimeout(){},clearTimeout(){},encodeURIComponent
  };
  vm.createContext(ctx);
  vm.runInContext(fs.readFileSync(path.join(__dirname,'..','web','hosts-v2.js'),'utf8'),ctx,{filename:'hosts-v2.js'});
  return ctx;
}
const inbound={
  id:7,remark:'EDGE IN',protocol:'vless',port:443,
  streamSettings:{network:'ws',security:'tls',wsSettings:{path:'/origin',headers:{Host:'origin.example'}},tlsSettings:{serverName:'origin-sni.example'}}
};

test('Public Endpoints V3 direct mode strips accidental client overrides',()=>{
  const ctx=context(),M=ctx.DarkHostV3;
  const out=M.normalizeEndpoint({
    mode:'direct',address:'direct.example.test',port:443,remark:'DIRECT',enable:true,
    sniMode:'manual',sni:'wrong.example',host:'wrong.example',path:'/wrong',
    security:'none',alpn:'h2',fingerprint:'chrome',allowInsecure:true,
    finalMask:'{"tcpPadding":true}',mihomoIpVersion:'ipv4',excludeFromSubTypes:['clash']
  },inbound);
  assert.equal(out.security,'same');
  assert.equal(out.sni,'');
  assert.equal(out.host,'');
  assert.equal(out.path,'');
  assert.equal(out.allowInsecure,false);
  assert.deepEqual(Array.from(out.excludeFromSubTypes),[]);
});

test('Tunnel mode keeps only client-facing SNI Host and Path overrides',()=>{
  const ctx=context(),M=ctx.DarkHostV3;
  const out=M.normalizeEndpoint({
    mode:'tunnel',address:'cdn.example.test',port:8443,remark:'CDN',enable:true,
    sniMode:'manual',sni:'tls-front.example',host:'cdn-host.example',path:'/edge',
    security:'none',alpn:'h3',fingerprint:'firefox',allowInsecure:true,
    finalMask:'{"x":1}',mihomoIpVersion:'ipv6',excludeFromSubTypes:['raw']
  },inbound);
  assert.equal(out.security,'same');
  assert.equal(out.sni,'tls-front.example');
  assert.equal(out.host,'cdn-host.example');
  assert.equal(out.path,'/edge');
  assert.equal(out.alpn,'');
  assert.equal(out.fingerprint,'');
  assert.equal(out.finalMask,'');
  assert.deepEqual(Array.from(out.excludeFromSubTypes),[]);
});

test('Advanced mode preserves explicit security and format behavior',()=>{
  const ctx=context(),M=ctx.DarkHostV3;
  const out=M.normalizeEndpoint({
    mode:'advanced',address:'front.example.test',port:443,remark:'ADV',enable:true,
    sniMode:'address',sni:'',host:'front-host.example',path:'/x',
    security:'tls',alpn:'h2,http/1.1',fingerprint:'chrome',allowInsecure:true,
    finalMask:'{"tcpPadding":true}',mihomoIpVersion:'ipv4-prefer',
    excludeFromSubTypes:['clash','json','clash']
  },inbound);
  assert.equal(out.security,'tls');
  assert.equal(out.overrideSniFromAddress,true);
  assert.equal(out.allowInsecure,true);
  assert.equal(out.finalMask,'{"tcpPadding":true}');
  assert.deepEqual(Array.from(out.excludeFromSubTypes),['clash','json']);
});

test('Preview inherits inbound transport defaults when endpoint does not override them',()=>{
  const ctx=context(),M=ctx.DarkHostV3;
  const out=M.normalizeEndpoint({mode:'direct',address:'direct.example.test',port:443,remark:'',enable:true},inbound);
  const p=M.previewModel(out,inbound);
  assert.equal(p.network,'ws');
  assert.equal(p.security,'tls');
  assert.equal(p.sni,'origin-sni.example');
  assert.equal(p.host,'origin.example');
  assert.equal(p.path,'/origin');
  assert.match(p.preview,/direct\.example\.test:443/);
  assert.match(p.preview,/type=ws/);
  assert.match(p.preview,/security=tls/);
});

test('Guided editor labels the distinction between inbound and public endpoint',()=>{
  const src=fs.readFileSync(path.join(__dirname,'..','web','hosts-v2.js'),'utf8');
  assert.match(src,/Inbound = what Xray listens on/);
  assert.match(src,/Public Endpoint = what the customer connects to/);
  assert.match(src,/Direct/);
  assert.match(src,/Tunnel \/ CDN/);
  assert.match(src,/Advanced override/);
  assert.match(src,/Customer link impact/);
  assert.match(src,/Credential is masked/);
});
