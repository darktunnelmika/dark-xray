#!/usr/bin/env python3
from pathlib import Path


def once(path,old,new):
    p=Path(path);s=p.read_text()
    if old not in s:raise SystemExit(f'anchor missing in {path}: {old[:160]!r}')
    p.write_text(s.replace(old,new,1))

# ---------------------------------------------------------------------------
# Server-side Host validation. Only fields that DARK actually consumes are
# introduced here; no decorative Sanaei parity fields.
# ---------------------------------------------------------------------------
once('backend/core.py',
"""                for key in ('remark','sni','host','path','alpn','fingerprint'):
                    if key in host and (not isinstance(host[key],str) or len(host[key])>4096):raise CoreError('Invalid host '+key)
""",
"""                for key in ('remark','sni','host','path','alpn','fingerprint','security','finalMask','mihomoIpVersion'):
                    if key in host and (not isinstance(host[key],str) or len(host[key])>8192):raise CoreError('Invalid host '+key)
                security=host.get('security','same') or 'same'
                if security not in ('same','tls','none'):raise CoreError('Host security override must be same, tls or none')
                host['security']=security
                for key in ('allowInsecure','overrideSniFromAddress','keepSniBlank'):
                    if key in host and type(host[key]) is not bool:raise CoreError('Host '+key+' must be boolean')
                    host.setdefault(key,False)
                if host['overrideSniFromAddress'] and host['keepSniBlank']:raise CoreError('Host cannot both derive SNI from address and keep SNI blank')
                excluded=host.get('excludeFromSubTypes',[])
                if not isinstance(excluded,list) or len(excluded)>3 or any(x not in ('raw','json','clash') for x in excluded) or len(set(excluded))!=len(excluded):
                    raise CoreError('Host format exclusions must contain unique raw/json/clash values')
                host['excludeFromSubTypes']=excluded
                ipver=host.get('mihomoIpVersion','')
                if ipver not in ('','dual','ipv4','ipv6','ipv4-prefer','ipv6-prefer'):raise CoreError('Invalid Mihomo host IP version')
                fm=host.get('finalMask','').strip()
                if fm:
                    try:fm_obj=json.loads(fm)
                    except Exception:raise CoreError('Host FinalMask must be valid JSON')
                    if not isinstance(fm_obj,dict) or not fm_obj:raise CoreError('Host FinalMask must be a nonempty JSON object')
                    host['finalMask']=json.dumps(fm_obj,separators=(',',':'),ensure_ascii=False)
                else:host['finalMask']=''
                alpn=host.get('alpn','').strip()
                if alpn:
                    parts=[x.strip() for x in alpn.split(',') if x.strip()]
                    if not parts or len(parts)>16 or any(len(x)>64 or any(ord(ch)<33 or ord(ch)>126 for ch in x) for x in parts):raise CoreError('Invalid host ALPN list')
                    host['alpn']=','.join(dict.fromkeys(parts))
""")

# ---------------------------------------------------------------------------
# Replace link rendering with a format-aware endpoint projection.
# ---------------------------------------------------------------------------
p=Path('backend/core.py');s=p.read_text();start=s.index('    def links(self,email:str)->dict:\n');end=s.index('    @staticmethod\n    def _clash_proxy',start)
new_links=r'''    def links(self,email:str,fmt:str='raw')->dict:
        if fmt not in ('raw','base64','json','clash'):raise CoreError('Unsupported link format',status=400)
        host_format='raw' if fmt=='base64' else fmt
        d=self.client_detail(email);c=d['client'];links=[];warnings=[]
        for i in d['inboundIds']:
            ib=self.inbound(i)
            if not ib['enable']:continue
            configured=[h for h in self.section('hosts') if h['inboundId']==i]
            hs=[h for h in configured if h.get('enable',True) and host_format not in h.get('excludeFromSubTypes',[])] if configured else [{}]
            for host in hs:
                address=host.get('address',self.config.public_address);port=host.get('port',ib['port'])
                proto=ib['protocol'];sub=self.section('subscription');base_remark=host.get('remark',ib['remark'])
                label=sub.get('remark_template','{remark} | {email}').replace('{remark}',base_remark).replace('{email}',email).replace('{protocol}',proto.upper())
                st=ib['streamSettings'];net=st.get('network','tcp');base_sec=st.get('security','none')
                force=host.get('security','same') or 'same';sec=base_sec if force=='same' else force
                q={'type':net,'security':sec}
                base_security=st.get('realitySettings' if base_sec=='reality' else 'tlsSettings',{}) if base_sec!='none' else {}
                security=base_security if sec==base_sec else {}
                sni=''
                if not host.get('keepSniBlank',False) and sec!='none':
                    if host.get('overrideSniFromAddress',False):sni=address
                    elif host.get('sni'):sni=host['sni']
                    elif sec==base_sec:sni=security.get('serverName') or next(iter(security.get('serverNames',[])),'')
                if sni:q['sni']=sni
                if sec=='reality':
                    key=security.get('privateKey','')
                    if not key: warnings.append('REALITY private key missing for '+ib['tag']);continue
                    try:
                        from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
                        from cryptography.hazmat.primitives import serialization
                        pub=X25519PrivateKey.from_private_bytes(base64.urlsafe_b64decode(key+'='*((4-len(key)%4)%4))).public_key()
                        q['pbk']=base64.urlsafe_b64encode(pub.public_bytes(serialization.Encoding.Raw,serialization.PublicFormat.Raw)).decode().rstrip('=')
                    except (ValueError,TypeError):warnings.append('Invalid REALITY key for '+ib['tag']);continue
                    q['sid']=next(iter(security.get('shortIds',[])),'')
                    meta=ib.get('panelMeta',{}).get('reality',{}) if isinstance(ib.get('panelMeta',{}),dict) else {}
                    q['fp']=host.get('fingerprint') or meta.get('fingerprint','chrome')
                    spider=meta.get('spiderX','')
                    if spider:q['spx']=spider
                elif sec=='tls' and host.get('fingerprint'):
                    q['fp']=host['fingerprint']
                alpn=host.get('alpn','')
                if not alpn and sec==base_sec and isinstance(security.get('alpn'),list):alpn=','.join(str(x) for x in security['alpn'] if x)
                if sec!='none' and alpn:q['alpn']=alpn
                if sec!='none' and host.get('allowInsecure',False):q['allowInsecure']='1'
                if host.get('finalMask'):q['fm']=host['finalMask']
                if net in ('ws','httpupgrade','xhttp'):
                    ns=st.get(net+'Settings',{});q['path']=host.get('path') or ns.get('path','/')
                    q['host']=host.get('host') or ns.get('host',ns.get('headers',{}).get('Host',''))
                    if net=='xhttp':q['mode']=ns.get('mode','auto')
                if net=='grpc':q['serviceName']=st.get('grpcSettings',{}).get('serviceName','')
                hp=('['+address+']' if ':' in address else address)+':'+str(port)
                if proto=='vless':
                    q['encryption']=c.get('encryption') or 'none'
                    if c.get('flow'):q['flow']=c['flow']
                    uri='vless://'+c['id']+'@'+hp+'?'+urlencode(q)+'#'+quote(label)
                elif proto=='trojan':uri='trojan://'+quote(c['password'],safe='')+'@'+hp+'?'+urlencode(q)+'#'+quote(label)
                elif proto=='vmess':
                    ob={'v':'2','ps':label,'add':address,'port':str(port),'id':c['id'],'aid':'0','scy':c.get('security','auto'),
                        'net':net,'type':'none','host':q.get('host',''),'path':q.get('serviceName',q.get('path','')),'tls':sec if sec!='none' else '','sni':sni}
                    if q.get('fp'):ob['fp']=q['fp']
                    if q.get('alpn'):ob['alpn']=q['alpn']
                    if q.get('allowInsecure'):ob['allowInsecure']=True
                    if q.get('fm'):ob['fm']=q['fm']
                    uri='vmess://'+base64.b64encode(json.dumps(ob,separators=(',',':'),ensure_ascii=False).encode()).decode()
                elif proto=='shadowsocks':
                    method=ib['settings'].get('method','aes-128-gcm')
                    if method.startswith('2022-') or net not in ('tcp','raw') or sec!='none':
                        warnings.append('Shadowsocks transport/2022 export not yet supported');continue
                    user=base64.urlsafe_b64encode((method+':'+c['password']).encode()).decode().rstrip('=');uri='ss://'+user+'@'+hp+'#'+quote(label)
                else:warnings.append('No subscription generator for '+proto);continue
                meta={}
                if host.get('mihomoIpVersion'):meta['mihomoIpVersion']=host['mihomoIpVersion']
                links.append({'inboundId':i,'remark':label,'uri':uri,'hostMeta':meta})
        return {'links':links,'warnings':warnings,'formats':['raw','base64','json','clash']}

'''
s=s[:start]+new_links+s[end:]
# Replace Clash converter whole function by boundary.
start=s.index('    @staticmethod\n    def _clash_proxy',s.index(new_links[:40]));end=s.index('    @staticmethod\n    def _yaml',start)
new_clash=r'''    @staticmethod
    def _clash_proxy(uri:str,name:str,meta:dict|None=None)->dict:
        from urllib.parse import urlsplit,parse_qs,unquote
        meta=meta if isinstance(meta,dict) else {}
        def finish(out):
            ipver=meta.get('mihomoIpVersion','')
            if ipver:out['ip-version']=ipver
            return out
        if uri.startswith('vmess://'):
            raw=uri[8:];doc=json.loads(base64.b64decode(raw+'='*((4-len(raw)%4)%4)).decode())
            out={'name':name,'type':'vmess','server':doc['add'],'port':int(doc['port']),'uuid':doc['id'],'alterId':int(doc.get('aid',0)),'cipher':doc.get('scy','auto'),'udp':True}
            net=doc.get('net','tcp');out['network']=net
            if doc.get('tls'):out['tls']=True
            if doc.get('sni'):out['servername']=doc['sni']
            if doc.get('fp'):out['client-fingerprint']=doc['fp']
            if doc.get('allowInsecure'):out['skip-cert-verify']=True
            if doc.get('alpn'):out['alpn']=[x for x in str(doc['alpn']).split(',') if x]
            if net=='ws':out['ws-opts']={'path':doc.get('path','/'),'headers':{'Host':doc.get('host','')}}
            if net=='grpc':out['grpc-opts']={'grpc-service-name':doc.get('path','')}
            return finish(out)
        p=urlsplit(uri);q={k:v[-1] for k,v in parse_qs(p.query).items()};proto=p.scheme
        if proto=='ss':
            user=p.username or ''
            try:creds=base64.urlsafe_b64decode(user+'='*((4-len(user)%4)%4)).decode();method,password=creds.split(':',1)
            except Exception:raise CoreError('Cannot convert Shadowsocks link to Clash')
            return finish({'name':name,'type':'ss','server':p.hostname,'port':p.port,'cipher':method,'password':password,'udp':True})
        if proto not in ('vless','trojan'):raise CoreError('Unsupported Clash proxy protocol')
        out={'name':name,'type':proto,'server':p.hostname,'port':p.port,'udp':True}
        if proto=='vless':out['uuid']=unquote(p.username or '')
        else:out['password']=unquote(p.username or '')
        net=q.get('type','tcp');sec=q.get('security','none');out['network']=net
        if sec in ('tls','reality'):out['tls']=True
        if q.get('sni'):out['servername']=q['sni']
        if q.get('flow'):out['flow']=q['flow']
        if q.get('fp'):out['client-fingerprint']=q['fp']
        if q.get('allowInsecure')=='1':out['skip-cert-verify']=True
        if q.get('alpn'):out['alpn']=[x for x in q['alpn'].split(',') if x]
        if sec=='reality':out['reality-opts']={'public-key':q.get('pbk',''),'short-id':q.get('sid','')}
        if net=='ws':out['ws-opts']={'path':q.get('path','/'),'headers':{'Host':q.get('host','')}}
        if net=='grpc':out['grpc-opts']={'grpc-service-name':q.get('serviceName','')}
        if net=='xhttp':out['xhttp-opts']={'path':q.get('path','/'),'mode':q.get('mode','auto')}
        return finish(out)

'''
s=s[:start]+new_clash+s[end:]
p.write_text(s)

# Subscription renders hosts per format and passes host-only Mihomo metadata.
once('backend/core.py',
"""        settings=self.section('subscription');result=self.links(email)
""",
"""        settings=self.section('subscription');result=self.links(email,'raw' if fmt=='base64' else fmt)
""")
once('backend/core.py',
"""            proxies=[]
            for item in result['links']:proxies.append(self._clash_proxy(item['uri'],item['remark']))
""",
"""            proxies=[]
            for item in result['links']:proxies.append(self._clash_proxy(item['uri'],item['remark'],item.get('hostMeta')))
""")

# ---------------------------------------------------------------------------
# Hosts V3 UI. Existing CSS remains adequate; form adds only effective fields.
# ---------------------------------------------------------------------------
once('web/hosts-v2.js',
"""/* DARK XRAY Hosts V2 — client-facing endpoint overrides that actually affect generated links. */
""",
"""/* DARK XRAY Hosts V3 — format-aware endpoint overrides consumed by generated links/subscriptions. */
""")
once('web/hosts-v2.js',
"""function htags(h){let t=[];if(h.sni)t.push('SNI '+h.sni);if(h.host)t.push('Host '+h.host);if(h.path)t.push('Path '+h.path);if(h.alpn)t.push('ALPN '+h.alpn);if(h.fingerprint)t.push('FP '+h.fingerprint);return t;}
""",
"""function htags(h){let t=[];if(h.security&&h.security!=='same')t.push('SEC '+h.security.toUpperCase());if(h.sni)t.push('SNI '+h.sni);if(h.overrideSniFromAddress)t.push('SNI=ADDRESS');if(h.keepSniBlank)t.push('SNI BLANK');if(h.host)t.push('Host '+h.host);if(h.path)t.push('Path '+h.path);if(h.alpn)t.push('ALPN '+h.alpn);if(h.fingerprint)t.push('FP '+h.fingerprint);if(h.allowInsecure)t.push('INSECURE');if(h.mihomoIpVersion)t.push('MIHOMO '+h.mihomoIpVersion);if(h.finalMask)t.push('FINAL MASK');if(h.excludeFromSubTypes?.length)t.push('EXCLUDE '+h.excludeFromSubTypes.join(','));return t;}
""")
p=Path('web/hosts-v2.js');s=p.read_text();start=s.index('async function editHost(index=null,clone=false){');end=s.index('runAction=async function',start)
new_edit=r'''async function editHost(index=null,clone=false){
 let list=(await api('/api/settings/hosts')).value||[],h=index==null?{inboundId:state.inbounds[0]?.id||0,address:'',port:443,remark:'',sni:'',host:'',path:'',alpn:'',fingerprint:'chrome',security:'same',allowInsecure:false,overrideSniFromAddress:false,keepSniBlank:false,finalMask:'',mihomoIpVersion:'',excludeFromSubTypes:[],enable:true}:JSON.parse(JSON.stringify(list[index]));if(clone)h.remark=(h.remark||'HOST')+' COPY';const excluded=new Set(h.excludeFromSubTypes||[]);
 dialog(index==null?L('New host','هاست جدید'):L('Edit host','ویرایش هاست'),`<div class="hv2-form">${select(L('Inbound','اینباند'),'inboundId',state.inbounds.map(i=>[i.id,(i.remark||i.tag)+' :'+i.port]),h.inboundId)}${select(L('Status','وضعیت'),'enable',[['true',L('Enabled','فعال')],['false',L('Disabled','غیرفعال')]],String(h.enable!==false))}${field(L('Public / tunnel address','آدرس عمومی / تانل'),'address',h.address||'','text','required dir="ltr"')}${field(L('Public / tunnel port','پورت عمومی / تانل'),'port',h.port||443,'number','required min="1" max="65535"')}${field(L('Remark','نام نمایشی'),'remark',h.remark||'')}${select(L('Security override','Override امنیت'), 'security', [['same',L('Same as inbound','مثل اینباند')],['tls','TLS'],['none',L('None / plaintext','بدون TLS')]],h.security||'same')}${field('SNI','sni',h.sni||'','text','dir="ltr"')}${select(L('SNI from endpoint address','SNI از آدرس Endpoint'),'overrideSniFromAddress',[['false',L('No','خیر')],['true',L('Yes','بله')]],String(!!h.overrideSniFromAddress))}${select(L('Keep SNI blank','SNI خالی بماند'),'keepSniBlank',[['false',L('No','خیر')],['true',L('Yes','بله')]],String(!!h.keepSniBlank))}${field('Host','host',h.host||'','text','dir="ltr"')}${field('Path','path',h.path||'','text','dir="ltr"')}${field('ALPN','alpn',h.alpn||'','text','dir="ltr" placeholder="h2,http/1.1"')}${field(L('Fingerprint','فینگرپرینت'),'fingerprint',h.fingerprint||'chrome','text','dir="ltr"')}${select(L('Allow insecure TLS','اجازه TLS ناامن'),'allowInsecure',[['false',L('No','خیر')],['true',L('Yes','بله')]],String(!!h.allowInsecure))}${select(L('Mihomo IP version','نسخه IP در Mihomo'),'mihomoIpVersion',[['',L('Default','پیش‌فرض')],['dual','dual'],['ipv4','ipv4'],['ipv6','ipv6'],['ipv4-prefer','ipv4-prefer'],['ipv6-prefer','ipv6-prefer']],h.mihomoIpVersion||'')}<label class="span-2">Final Mask JSON<textarea class="field-input" name="finalMask" dir="ltr" rows="3" placeholder='{"tcpPadding":true}'>${e(h.finalMask||'')}</textarea></label><div class="span-2"><label>${L('Exclude host from subscription formats','عدم نمایش Host در فرمت‌ها')}</label><div class="check-list"><label><input type="checkbox" name="excludeFormat" value="raw" ${excluded.has('raw')?'checked':''}><span>Raw / Base64</span></label><label><input type="checkbox" name="excludeFormat" value="json" ${excluded.has('json')?'checked':''}><span>DARK JSON</span></label><label><input type="checkbox" name="excludeFormat" value="clash" ${excluded.has('clash')?'checked':''}><span>Clash / Mihomo</span></label></div></div><div class="span-2 notice">${L('Security override is for a real front/tunnel endpoint that terminates a different security layer. FinalMask is emitted as the fm share-link parameter; Mihomo IP version is emitted only in Clash. Mux/Sockopt/ECH are intentionally not shown until DARK has a native consumer for them.','Override امنیت فقط برای Endpoint واقعی است که لایه امنیتی متفاوتی terminate می‌کند. FinalMask واقعاً به پارامتر fm لینک می‌رود و نسخه IP فقط در Clash/Mihomo اعمال می‌شود. Mux/Sockopt/ECH تا وقتی مصرف‌کننده واقعی نداشته باشند نمایش داده نمی‌شوند.')}</div></div>`,async f=>{let v={inboundId:Number(f.get('inboundId')),address:String(f.get('address')||'').trim(),port:Number(f.get('port')),remark:String(f.get('remark')||'').trim(),security:String(f.get('security')||'same'),sni:String(f.get('sni')||'').trim(),overrideSniFromAddress:f.get('overrideSniFromAddress')==='true',keepSniBlank:f.get('keepSniBlank')==='true',host:String(f.get('host')||'').trim(),path:String(f.get('path')||'').trim(),alpn:String(f.get('alpn')||'').trim(),fingerprint:String(f.get('fingerprint')||'').trim(),allowInsecure:f.get('allowInsecure')==='true',finalMask:String(f.get('finalMask')||'').trim(),mihomoIpVersion:String(f.get('mihomoIpVersion')||''),excludeFromSubTypes:f.getAll('excludeFormat'),enable:f.get('enable')==='true'};if(index==null||clone)list.push(v);else list[index]=v;await api('/api/settings/hosts','PUT',{value:list});closeDialog();toast(L('Host saved.','هاست ذخیره شد.'));await refresh();});}
'''
s=s[:start]+new_edit+s[end:];p.write_text(s)

# ---------------------------------------------------------------------------
# Regression tests.
# ---------------------------------------------------------------------------
Path('tests/test_hosts_v3.py').write_text(r'''import json
from urllib.parse import urlsplit,parse_qs
from test_settings_v2 import env,IB


def setup_client(c,email='host-v3-user',ib=None):
    r=c.post('/api/inbounds',json=ib or IB);assert r.status_code==200,r.text
    iid=r.json()['id']
    r=c.post('/api/clients',json={'owner':'dark','client':{'email':email},'inboundIds':[iid]});assert r.status_code==202,r.text
    return iid,r.json()['subscription_url']


def host(iid,**kw):
    out={'inboundId':iid,'address':'edge.example.test','port':443,'remark':'EDGE','security':'tls','sni':'tls.example.test','overrideSniFromAddress':False,'keepSniBlank':False,'host':'cdn.example.test','path':'/edge','alpn':'h2,http/1.1','fingerprint':'chrome','allowInsecure':True,'finalMask':'{"tcpPadding":true}','mihomoIpVersion':'ipv4-prefer','excludeFromSubTypes':[],'enable':True}
    out.update(kw);return out


def test_host_v3_raw_link_consumes_security_tls_controls_and_finalmask(env):
    store,engine,c=env;iid,url=setup_client(c)
    h=host(iid);r=c.put('/api/settings/hosts',json={'value':[h]});assert r.status_code==200,r.text
    r=c.get(url+'?format=raw');assert r.status_code==200,r.text
    p=urlsplit(r.text.strip());q={k:v[-1] for k,v in parse_qs(p.query).items()}
    assert p.hostname=='edge.example.test' and p.port==443
    assert q['security']=='tls' and q['sni']=='tls.example.test'
    assert q['allowInsecure']=='1' and q['alpn']=='h2,http/1.1'
    assert json.loads(q['fm'])=={'tcpPadding':True}


def test_host_v3_clash_consumes_mihomo_ip_alpn_and_skip_verify(env):
    store,engine,c=env;iid,url=setup_client(c)
    assert c.put('/api/settings/hosts',json={'value':[host(iid)]}).status_code==200
    r=c.get(url+'?format=clash');assert r.status_code==200,r.text
    assert '"skip-cert-verify": true' in r.text
    assert '"ip-version": "ipv4-prefer"' in r.text
    assert '"alpn":' in r.text and '"h2"' in r.text and '"http/1.1"' in r.text


def test_host_v3_format_exclusion_has_no_direct_fallback(env):
    store,engine,c=env;iid,url=setup_client(c)
    assert c.put('/api/settings/hosts',json={'value':[host(iid,excludeFromSubTypes=['clash'])]}).status_code==200
    assert c.get(url+'?format=raw').status_code==200
    r=c.get(url+'?format=clash');assert r.status_code==503


def test_host_v3_sni_address_and_blank_modes(env):
    store,engine,c=env;iid,url=setup_client(c)
    h=host(iid,overrideSniFromAddress=True,sni='ignored.example')
    assert c.put('/api/settings/hosts',json={'value':[h]}).status_code==200
    q=parse_qs(urlsplit(c.get(url+'?format=raw').text.strip()).query);assert q['sni'][-1]=='edge.example.test'
    h=host(iid,keepSniBlank=True,sni='ignored.example',overrideSniFromAddress=False)
    assert c.put('/api/settings/hosts',json={'value':[h]}).status_code==200
    q=parse_qs(urlsplit(c.get(url+'?format=raw').text.strip()).query);assert 'sni' not in q


def test_host_v3_force_none_drops_reality_only_params(env):
    store,engine,c=env
    keys=c.post('/api/keys/x25519',json={}).json()
    ib=dict(IB);ib['port']=19051;ib['tag']='reality-host-test';ib['streamSettings']={'network':'tcp','security':'reality','realitySettings':{'privateKey':keys['privateKey'],'target':'example.com:443','serverNames':['example.com'],'shortIds':[keys['shortId']]}}
    iid,url=setup_client(c,'reality-host-user',ib)
    assert c.put('/api/settings/hosts',json={'value':[host(iid,security='none',allowInsecure=False,alpn='',finalMask='')]}).status_code==200
    q=parse_qs(urlsplit(c.get(url+'?format=raw').text.strip()).query)
    assert q['security'][-1]=='none'
    for key in ('pbk','sid','spx','sni','fp','allowInsecure'):assert key not in q


def test_host_v3_validation_rejects_fake_or_conflicting_controls(env):
    store,engine,c=env;iid,_=setup_client(c)
    cases=[host(iid,security='reality'),host(iid,overrideSniFromAddress=True,keepSniBlank=True),host(iid,finalMask='[]'),host(iid,mihomoIpVersion='magic'),host(iid,excludeFromSubTypes=['clash','clash'])]
    for value in cases:
        r=c.put('/api/settings/hosts',json={'value':[value]});assert r.status_code==422,(value,r.text)
''')

once('tests/run-tests.sh',
"""python -m pytest tests/test_subscription_path.py -q --junitxml=qa/junit/subscription-path.xml
""",
"""python -m pytest tests/test_subscription_path.py -q --junitxml=qa/junit/subscription-path.xml
python -m pytest tests/test_hosts_v3.py -q --junitxml=qa/junit/hosts-v3.xml
""")
Path('VERSION').write_text('0.8.2-standalone-lab\n')
print('0.8.2 Hosts V3 patch applied')
