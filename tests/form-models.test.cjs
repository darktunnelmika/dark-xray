/* Actual pure JavaScript data transformations. No browser/rendering claim. */
'use strict';
const {test}=require('node:test');const a=require('node:assert/strict');const f=require('../web/form-models.js');
const id='12345678-1234-4234-9234-123456789012';
const v={tag:'dark-out',protocol:'vless',address:'example.test',port:443,credential:id,network:'grpc',security:'none',path:'dark-service'};
test('gRPC serviceName survives native outbound save',()=>a.equal(f.outbound(v).streamSettings.grpcSettings.serviceName,'dark-service'));
test('gRPC authority/advanced fields not deleted by same-transport editing',()=>a.equal(f.outbound(v,{protocol:'vless',streamSettings:{network:'grpc',grpcSettings:{authority:'cdn.test',multiMode:true}}}).streamSettings.grpcSettings.authority,'cdn.test'));
test('native WS form retains extra headers',()=>a.equal(f.outbound({...v,network:'ws',path:'/ws',host:'cdn.test'},{protocol:'vless',streamSettings:{network:'ws',wsSettings:{headers:{Extra:'kept'}}}}).streamSettings.wsSettings.headers.Extra,'kept'));
test('Trojan form emits password, not UUID',()=>{const o=f.outbound({...v,protocol:'trojan',credential:'password',security:'tls',sni:'example.test'});a.equal(o.settings.servers[0].password,'password');a.equal(o.settings.vnext,undefined);});
test('freedom strategy uses native form',()=>a.equal(f.outbound({tag:'direct',protocol:'freedom',domainStrategy:'UseIP'}).settings.domainStrategy,'UseIP'));
test('routing matches survive editing',()=>{const r=f.route({domain:'example.test\ngeosite:test',destination:'direct'},{type:'field',user:['alice']});a.deepEqual(r.domain,['example.test','geosite:test']);a.deepEqual(r.user,['alice']);});
test('balancer target replaces outbound, not both',()=>{const r=f.route({destination:'balancer:pool'},{outboundTag:'old'});a.equal(r.balancerTag,'pool');a.equal(r.outboundTag,undefined);});
test('disabled host remains disabled',()=>a.equal(f.host({inboundId:1,address:'vpn.test',port:443,enable:false}).enable,false));
test('unsafe address rejected',()=>a.throws(()=>f.host({inboundId:1,address:'https://vpn.test/path',port:443})));
test('invalid UUID rejected',()=>a.throws(()=>f.outbound({...v,credential:'------------------------------------'})));
test('port outside allowed range rejected',()=>a.throws(()=>f.outbound({...v,port:70000})));
test('multi-server editor refuses lossy flattening',()=>a.throws(()=>f.outbound(v,{protocol:'vless',settings:{vnext:[{},{ }]}}),/JSON/));
test('nested server properties retained',()=>a.equal(f.outbound(v,{protocol:'vless',settings:{vnext:[{extraField:true,users:[{level:3}]}]}}).settings.vnext[0].users[0].level,3));
test('outbound chain tag emitted as sockopt',()=>a.equal(f.outbound({...v,dialerProxy:'edge'}).streamSettings.sockopt.dialerProxy,'edge'));
