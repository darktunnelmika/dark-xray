const test=require('node:test');
const assert=require('node:assert/strict');
const M=require('../web/inbounds-models.js');

test('nextPort skips occupied ports while keeping low data ports valid',()=>{
 assert.equal(M.nextPort([{port:443},{port:444}],443),445);
 assert.equal(M.nextPort([{port:2020}],2020),2021);
});

test('filters use detailed transport/security without mutating rows',()=>{
 const rows=[{id:1,remark:'A',protocol:'vless',port:443,enable:true},{id:2,remark:'B',protocol:'trojan',port:8443,enable:true}];
 const details={1:{protocol:'vless',streamSettings:{network:'grpc',security:'reality'}},2:{protocol:'trojan',streamSettings:{network:'tcp',security:'tls'}}};
 const out=M.filterRows(rows,details,[],{protocol:'vless',transport:'grpc',security:'reality',status:'all',sort:'newest'});
 assert.deepEqual(out.map(x=>x.id),[1]);
 assert.equal(rows[0].remark,'A');
});

test('templates keep client credentials out of inbound settings',()=>{
 for(const kind of ['vless-reality','vless-ws-tls','trojan-reality-grpc','shadowsocks-tcp','custom']){
  const row=M.template(kind,2020);
  assert.equal(row.port,2020);
  assert.equal('clients' in row.settings,false);
  assert.equal('accounts' in row.settings,false);
 }
});

test('clone removes database id and uses caller supplied port',()=>{
 const c=M.cloneInbound({id:9,remark:'DARK',tag:'tag',port:443,settings:{},streamSettings:{},sniffing:{}},2050);
 assert.equal(c.id,undefined);
 assert.equal(c.port,2050);
 assert.equal(c.tag,'');
 assert.match(c.remark,/COPY$/);
});
