#!/usr/bin/env python3
from pathlib import Path


def once(s,old,new):
    if new in s:return s
    if old not in s:raise RuntimeError('anchor missing: '+old[:140])
    return s.replace(old,new,1)

# NodeRegistry: add a real remote inbound deploy call.
p=Path('backend/nodes.py');s=p.read_text()
anchor="""    def remote_inbounds(self,node_id:str)->dict:
        doc,ms=self._request(node_id,'/node/api/inbounds')
        if not isinstance(doc,list):raise PolicyError('Invalid node inbound response')
        return {'latency_ms':ms,'items':doc}
"""
new=anchor+"""
    def deploy_inbound(self,node_id:str,payload:dict)->dict:
        if not isinstance(payload,dict):raise PolicyError('Inbound payload must be an object')
        doc,ms=self._request(node_id,'/node/api/inbounds','POST',payload,12.0)
        if not isinstance(doc,dict) or type(doc.get('id')) is not int:raise PolicyError('Invalid node inbound deploy response')
        return {'latency_ms':ms,'inbound':doc,'applied':False,'next':'validate/restart remote Xray'}
"""
if 'def deploy_inbound' not in s:s=once(s,anchor,new)
p.write_text(s)

# Agent + central routes.
p=Path('backend/server.py');s=p.read_text()
anchor="""    @app.get('/node/api/inbounds')
    def node_inbounds(token_id:str=Depends(node_agent)):
        keys={'id','remark','protocol','port','listen','enable','tag'};out=[]
        for r in engine.inbounds():
            item={k:v for k,v in r.items() if k in keys};st=r.get('streamSettings',{}) if isinstance(r.get('streamSettings'),dict) else {}
            item['network']=st.get('network','tcp');item['security']=st.get('security','none');out.append(item)
        return out
"""
insert=anchor+"""    @app.post('/node/api/inbounds')
    def node_inbound_add(body:dict,token_id:str=Depends(node_agent)):
        writable()
        # Agent tokens can provision validated data-plane inbounds, but they do not
        # mutate reseller ownership/client records. Core apply remains a separate action.
        result=engine.save_inbound(body)
        return result
"""
if "@app.post('/node/api/inbounds')" not in s:s=once(s,anchor,insert)
anchor="""    @app.get('/api/nodes/{node_id}/inbounds')
    def remote_node_inbounds(node_id:str,p:Principal=Depends(owner)):return nodes.remote_inbounds(node_id)
"""
insert=anchor+"""    @app.post('/api/nodes/{node_id}/inbounds')
    def remote_node_deploy_inbound(node_id:str,body:dict,p:Principal=Depends(owner)):
        writable();result=nodes.deploy_inbound(node_id,body)
        manager.audit(p.actor,p.actor.id,'node.inbound.deploy',node_id,str(result['inbound'].get('id','')))
        return result
"""
if "def remote_node_deploy_inbound" not in s:s=once(s,anchor,insert)
p.write_text(s)

# Nodes UI: add a per-node clone local inbound action and dialog.
p=Path('web/nodes-v2.js');s=p.read_text()
old="""${button(L('Inbounds','اینباندها'),'nv2inbounds','server',`data-id=\"${e(n.id)}\"`)}${button(L('Validate','اعتبارسنجی'),'nv2core','check',`data-id=\"${e(n.id)}\" data-core=\"validate\"`)}"""
new="""${button(L('Inbounds','اینباندها'),'nv2inbounds','server',`data-id=\"${e(n.id)}\"`)}${button(L('Clone inbound','کپی اینباند'),'nv2cloneinbound','copy',`data-id=\"${e(n.id)}\"`)}${button(L('Validate','اعتبارسنجی'),'nv2core','check',`data-id=\"${e(n.id)}\" data-core=\"validate\"`)}"""
s=once(s,old,new)
anchor="""async function showNodeInbounds(id){let r=await api('/api/nodes/'+enc(id)+'/inbounds');dialog(L('Remote inbounds','اینباندهای راه‌دور'),`<div class=\"notice\">${L('Read live from the remote DARK node.','به‌صورت Live از DARK نود راه‌دور خوانده شده است.')} · ${r.latency_ms} ms</div><div class=\"nv2-inbounds\">${r.items.length?r.items.map(i=>`<div class=\"nv2-inbound\"><span><b>${e(i.remark||i.tag)}</b><br><small>${e(i.protocol)} · ${e(i.listen||'0.0.0.0')}:${i.port}</small></span><span class=\"tag ${i.enable?'green':'red'}\">${i.enable?'ON':'OFF'}</span></div>`).join(''):empty(L('No inbounds on node.','اینباندی روی نود نیست.'))}</div>`);}
"""
insert=anchor+"""async function cloneInboundToNode(nodeId){if(!state.inbounds.length)throw Error(L('Create a local inbound first.','ابتدا یک اینباند محلی بساز.'));dialog(L('Clone local inbound to node','کپی اینباند محلی روی نود'),`<div class=\"nv2-form\">${select(L('Source inbound','اینباند مبدا'),'source',state.inbounds.map(i=>[i.id,(i.remark||i.tag)+' · '+i.protocol+' :'+i.port]),state.inbounds[0].id)}${field(L('Remote port','پورت راه‌دور'),'port',state.inbounds[0].port,'number','required min=\"1\" max=\"65535\"')}${field(L('Remote remark','نام راه‌دور'),'remark',(state.inbounds[0].remark||'DARK')+' NODE','text','required maxlength=\"200\"')}${field(L('Remote tag','تگ راه‌دور'),'tag',(state.inbounds[0].tag||'dark-in')+'-'+nodeId,'text','required pattern=\"[A-Za-z0-9_.-]+\"')}<div class=\"span-2 notice\">${L('The full inbound transport/REALITY/TLS/sniffing settings are copied. Clients and reseller ownership are never copied. The remote Xray stays dirty until Validate/Restart.','تمام تنظیمات Transport/REALITY/TLS/Sniffing کپی می‌شوند؛ Client و مالکیت نماینده هرگز کپی نمی‌شود. Xray راه‌دور تا Validate/Restart در حالت dirty می‌ماند.')}</div></div>`,async f=>{let src=await api('/api/inbounds/'+enc(f.get('source'))),payload=JSON.parse(JSON.stringify(src));for(const k of ['id','applied'])delete payload[k];payload.port=Number(f.get('port'));payload.remark=f.get('remark').trim();payload.tag=f.get('tag').trim();let r=await api('/api/nodes/'+enc(nodeId)+'/inbounds','POST',payload);closeDialog();toast(`${L('Inbound cloned','اینباند کپی شد')} · #${r.inbound.id} · ${r.latency_ms} ms`);await renderPage();});let sel=$('#dialog-form select[name=\"source\"]');if(sel)sel.addEventListener('change',()=>{let ib=state.inbounds.find(x=>String(x.id)===String(sel.value));if(ib){let form=$('#dialog-form');form.elements.port.value=ib.port;form.elements.remark.value=(ib.remark||'DARK')+' NODE';form.elements.tag.value=(ib.tag||'dark-in')+'-'+nodeId;}});}
"""
if 'async function cloneInboundToNode' not in s:s=once(s,anchor,insert)
old="""if(act==='nv2inbounds'){await showNodeInbounds(el.dataset.id);return;}if(act==='nv2core')"""
new="""if(act==='nv2inbounds'){await showNodeInbounds(el.dataset.id);return;}if(act==='nv2cloneinbound'){await cloneInboundToNode(el.dataset.id);return;}if(act==='nv2core')"""
s=once(s,old,new);p.write_text(s)

# Subscription metadata and visual version consistency.
p=Path('backend/core.py');s=p.read_text();s=s.replace("return {'links':links,'warnings':warnings,'formats':['raw','base64']}","return {'links':links,'warnings':warnings,'formats':['raw','base64','json','clash']}",1);p.write_text(s)
p=Path('web/live.js');s=p.read_text();s=s.replace('v0.6 · STANDALONE','v0.7 · STANDALONE');p.write_text(s)

# Extend existing node/subscription tests.
p=Path('tests/test_nodes_v2.py');s=p.read_text()
extra='''\n\ndef test_agent_can_stage_inbound_without_copying_clients(env):\n store,eng,_,c=env;r=c.post('/api/node-agent/tokens',json={'name':'central-stage','days':10});token=r.json()['token']\n ib={"remark":"REMOTE","listen":"127.0.0.1","port":19831,"protocol":"vless","enable":True,"tag":"remote-stage","settings":{"decryption":"none"},"streamSettings":{"network":"tcp","security":"none"},"sniffing":{}}\n out=c.post('/node/api/inbounds',json=ib,headers={'authorization':'Bearer '+token});assert out.status_code==200,out.text\n assert out.json()['id']==1 and eng.inbound(1)['tag']=='remote-stage'\n assert eng.runtime_state()['dirty'] is True\n'''
if 'test_agent_can_stage_inbound_without_copying_clients' not in s:s+=extra
p.write_text(s)
p=Path('tests/test_subscription_v2.py');s=p.read_text()
needle="raw=c.get(url);assert raw.status_code==200 and raw.content.startswith(b\"vless://\") and raw.headers[\"profile-title\"]==\"DARK TEST\"\n"
repl=needle+" links=c.get('/api/clients/sub-v2/links').json()['engine'];assert set(links['formats'])=={'raw','base64','json','clash'}\n"
if "set(links['formats'])" not in s:s=s.replace(needle,repl,1)
p.write_text(s)
print('Node deploy and v0.7 consistency patch prepared')
