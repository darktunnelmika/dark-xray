/* Pure helpers for DARK XRAY Inbounds V2. No DOM/API dependencies. */
(function(root){
'use strict';
const clone=v=>JSON.parse(JSON.stringify(v));
const list=v=>String(v??'').split(/[\n,]/).map(x=>x.trim()).filter(Boolean);
const meta=(summary,detail={})=>{
  const st=detail.streamSettings||{};
  return {
    protocol:String((detail.protocol??summary?.protocol??'unknown')).toLowerCase(),
    network:String(st.network||'tcp').toLowerCase(),
    security:String(st.security||'none').toLowerCase(),
    enabled:(detail.enable??summary?.enable)!==false
  };
};
function stats(inbounds=[],clients=[]){
  const ids=new Set(inbounds.map(x=>x.id));
  const related=clients.filter(c=>(c.inboundIds||[]).some(id=>ids.has(id)));
  return {
    total:inbounds.length,
    enabled:inbounds.filter(x=>x.enable!==false).length,
    clients:related.length,
    blocked:related.filter(c=>(c.block_reasons||[]).length).length,
    usage:related.reduce((n,c)=>n+Number(c.used_bytes||0),0)
  };
}
function countClients(id,clients=[]){return clients.filter(c=>(c.inboundIds||[]).includes(id)).length;}
function nextPort(inbounds=[],preferred=443){
  const used=new Set(inbounds.map(x=>Number(x.port)).filter(Number.isInteger));
  let p=Math.min(65535,Math.max(1,Number(preferred)||443));
  while(p<=65535&&used.has(p))p++;
  if(p>65535){p=1;while(p<=65535&&used.has(p))p++;}
  if(p>65535)throw Error('No free port is available');
  return p;
}
function filterRows(inbounds=[],details={},clients=[],filters={}){
  const q=String(filters.query||'').trim().toLowerCase();
  let rows=inbounds.filter(row=>{
    const d=details[row.id]||{},m=meta(row,d);
    if(q&&!`${row.remark||''} ${row.tag||''} ${row.listen||''} ${row.port||''} ${m.protocol} ${m.network} ${m.security}`.toLowerCase().includes(q))return false;
    if(filters.protocol&&filters.protocol!=='all'&&m.protocol!==filters.protocol)return false;
    if(filters.transport&&filters.transport!=='all'&&m.network!==filters.transport)return false;
    if(filters.security&&filters.security!=='all'&&m.security!==filters.security)return false;
    if(filters.status==='online'&&!m.enabled)return false;
    if(filters.status==='offline'&&m.enabled)return false;
    return true;
  });
  const sort=filters.sort||'newest';
  rows=rows.slice().sort((a,b)=>{
    if(sort==='name')return String(a.remark||a.tag||'').localeCompare(String(b.remark||b.tag||''));
    if(sort==='port')return Number(a.port||0)-Number(b.port||0);
    if(sort==='clients')return countClients(b.id,clients)-countClients(a.id,clients);
    return Number(b.id||0)-Number(a.id||0);
  });
  return rows;
}
function template(kind='custom',port=443){
  const base={remark:'DARK INBOUND',protocol:'vless',listen:'0.0.0.0',port:Number(port),enable:true,settings:{decryption:'none'},streamSettings:{network:'tcp',security:'none'},sniffing:{enabled:true,destOverride:['http','tls'],routeOnly:true}};
  if(kind==='vless-reality')return {...clone(base),remark:'DARK REALITY',streamSettings:{network:'tcp',security:'reality',realitySettings:{show:false,target:'',serverNames:[],privateKey:'',shortIds:[],spiderX:'/'}}};
  if(kind==='vless-ws-tls')return {...clone(base),remark:'DARK WS TLS',streamSettings:{network:'ws',security:'tls',wsSettings:{path:'/',headers:{Host:''}},tlsSettings:{serverName:'',certificates:[]}}};
  if(kind==='trojan-reality-grpc')return {...clone(base),remark:'DARK TROJAN',protocol:'trojan',settings:{},streamSettings:{network:'grpc',security:'reality',grpcSettings:{serviceName:'dark-grpc',authority:''},realitySettings:{show:false,target:'',serverNames:[],privateKey:'',shortIds:[],spiderX:'/'}}};
  if(kind==='shadowsocks-tcp')return {...clone(base),remark:'DARK SHADOWSOCKS',protocol:'shadowsocks',settings:{method:'aes-128-gcm'},streamSettings:{network:'tcp',security:'none'}};
  return base;
}
function cloneInbound(detail,port){
  const d=clone(detail||{});delete d.id;delete d.applied;
  d.remark=(d.remark||'DARK inbound')+' COPY';d.tag='';d.port=Number(port);
  return d;
}
const api={clone,list,meta,stats,countClients,nextPort,filterRows,template,cloneInbound};
root.DarkInbound=api;if(typeof module!=='undefined')module.exports=api;
})(globalThis);
