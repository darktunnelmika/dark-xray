#!/usr/bin/env python3
from pathlib import Path

def once(s,old,new):
    if new in s:return s
    if old not in s:raise RuntimeError('anchor missing: '+old[:120])
    return s.replace(old,new,1)

p=Path('backend/core.py');s=p.read_text()
old="""                  'subscription':{'enabled':True,'default_format':'base64','profile_update_interval_hours':6,
                                  'remark_template':'{remark} | {email}','support_url':''},
"""
new="""                  'subscription':{'enabled':True,'default_format':'base64','auto_detect':True,'profile_update_interval_hours':6,
                                  'remark_template':'{remark} | {email}','support_url':'','profile_title':'DARK XRAY',
                                  'profile_url':'','announce':''},
"""
s=once(s,old,new)
old="""        if name=='subscription':
            allowed={'enabled','default_format','profile_update_interval_hours','remark_template','support_url'}
            if set(value)!=allowed:raise CoreError('Subscription settings shape is incomplete or contains unknown fields')
            if type(value['enabled']) is not bool or value['default_format'] not in ('raw','base64'):raise CoreError('Invalid subscription mode')
            if type(value['profile_update_interval_hours']) is not int or not 1<=value['profile_update_interval_hours']<=168:raise CoreError('Invalid subscription update interval')
            tmpl=value['remark_template']
            if not isinstance(tmpl,str) or not 1<=len(tmpl)<=200:raise CoreError('Invalid remark template')
            if any(x not in {'remark','email','protocol'} for x in re.findall(r'{([^{}]+)}',tmpl)):raise CoreError('Unknown remark-template variable')
            support=value['support_url']
            if not isinstance(support,str) or len(support)>500:raise CoreError('Invalid subscription support URL')
            if support:
                u=urlsplit(support)
                if u.scheme not in ('http','https') or not u.hostname or u.username or u.password:raise CoreError('Subscription support URL must be http/https without credentials')
                try:support.encode('ascii')
                except UnicodeEncodeError:raise CoreError('Subscription support URL must be ASCII/punycode')
"""
new="""        if name=='subscription':
            allowed={'enabled','default_format','auto_detect','profile_update_interval_hours','remark_template','support_url','profile_title','profile_url','announce'}
            if set(value)!=allowed:raise CoreError('Subscription settings shape is incomplete or contains unknown fields')
            if type(value['enabled']) is not bool or type(value['auto_detect']) is not bool or value['default_format'] not in ('raw','base64','json','clash'):raise CoreError('Invalid subscription mode')
            if type(value['profile_update_interval_hours']) is not int or not 1<=value['profile_update_interval_hours']<=168:raise CoreError('Invalid subscription update interval')
            tmpl=value['remark_template']
            if not isinstance(tmpl,str) or not 1<=len(tmpl)<=200:raise CoreError('Invalid remark template')
            if any(x not in {'remark','email','protocol'} for x in re.findall(r'{([^{}]+)}',tmpl)):raise CoreError('Unknown remark-template variable')
            for key in ('support_url','profile_url'):
                val=value[key]
                if not isinstance(val,str) or len(val)>500:raise CoreError('Invalid subscription URL')
                if val:
                    u=urlsplit(val)
                    if u.scheme not in ('http','https') or not u.hostname or u.username or u.password:raise CoreError('Subscription URL must be http/https without credentials')
                    try:val.encode('ascii')
                    except UnicodeEncodeError:raise CoreError('Subscription URL must be ASCII/punycode')
            if not isinstance(value['profile_title'],str) or not 1<=len(value['profile_title'])<=120:raise CoreError('Invalid subscription profile title')
            if not isinstance(value['announce'],str) or len(value['announce'])>2000:raise CoreError('Invalid subscription announcement')
"""
s=once(s,old,new)
old="""    def subscription(self,email:str,fmt:str)->tuple[bytes,dict]:
        if fmt not in ('raw','base64'):raise CoreError('This release exports raw/base64; other formats are not silently substituted',status=400)
        settings=self.section('subscription')
        result=self.links(email)
        if result['warnings']:raise CoreError('Subscription would be incomplete: '+'; '.join(result['warnings']),status=422)
        body='\\n'.join(r['uri'] for r in result['links']).encode()
        if not body:raise CoreError('No enabled supported connection',status=503)
        if fmt=='base64':body=base64.b64encode(body)
        with self.store.lock:r=self.store.db.execute('SELECT * FROM core_clients WHERE email=?',(email,)).fetchone()
        c=json.loads(r['body']);headers={'Content-Type':'text/plain; charset=utf-8','profile-update-interval':str(settings.get('profile_update_interval_hours',6)),
            'subscription-userinfo':f\"upload={r['up']}; download={r['down']}; total={c.get('totalGB',0)}; expire={max(0,c.get('expiryTime',0)//1000)}\"}
        support=settings.get('support_url','')
        if support:headers['support-url']=support
        return body,headers
"""
new="""    @staticmethod
    def _clash_proxy(uri:str,name:str)->dict:
        from urllib.parse import urlsplit,parse_qs,unquote
        if uri.startswith('vmess://'):
            raw=uri[8:];doc=json.loads(base64.b64decode(raw+'='*((4-len(raw)%4)%4)).decode())
            out={'name':name,'type':'vmess','server':doc['add'],'port':int(doc['port']),'uuid':doc['id'],'alterId':int(doc.get('aid',0)),'cipher':doc.get('scy','auto'),'udp':True}
            net=doc.get('net','tcp');out['network']=net
            if doc.get('tls'):out['tls']=True
            if doc.get('sni'):out['servername']=doc['sni']
            if net=='ws':out['ws-opts']={'path':doc.get('path','/'),'headers':{'Host':doc.get('host','')}}
            if net=='grpc':out['grpc-opts']={'grpc-service-name':doc.get('path','')}
            return out
        p=urlsplit(uri);q={k:v[-1] for k,v in parse_qs(p.query).items()};proto=p.scheme
        if proto=='ss':
            user=p.username or ''
            try:creds=base64.urlsafe_b64decode(user+'='*((4-len(user)%4)%4)).decode();method,password=creds.split(':',1)
            except Exception:raise CoreError('Cannot convert Shadowsocks link to Clash')
            return {'name':name,'type':'ss','server':p.hostname,'port':p.port,'cipher':method,'password':password,'udp':True}
        if proto not in ('vless','trojan'):raise CoreError('Unsupported Clash proxy protocol')
        out={'name':name,'type':proto,'server':p.hostname,'port':p.port,'udp':True}
        if proto=='vless':out['uuid']=unquote(p.username or '')
        else:out['password']=unquote(p.username or '')
        net=q.get('type','tcp');sec=q.get('security','none');out['network']=net
        if sec in ('tls','reality'):out['tls']=True
        if q.get('sni'):out['servername']=q['sni']
        if q.get('flow'):out['flow']=q['flow']
        if q.get('fp'):out['client-fingerprint']=q['fp']
        if sec=='reality':out['reality-opts']={'public-key':q.get('pbk',''),'short-id':q.get('sid','')}
        if net=='ws':out['ws-opts']={'path':q.get('path','/'),'headers':{'Host':q.get('host','')}}
        if net=='grpc':out['grpc-opts']={'grpc-service-name':q.get('serviceName','')}
        if net=='xhttp':out['xhttp-opts']={'path':q.get('path','/'),'mode':q.get('mode','auto')}
        return out

    @staticmethod
    def _yaml(value,level:int=0)->str:
        pad='  '*level
        if isinstance(value,dict):
            lines=[]
            for k,v in value.items():
                key=json.dumps(str(k),ensure_ascii=False)
                if isinstance(v,(dict,list)):lines.append(f'{pad}{key}:\\n'+CoreEngine._yaml(v,level+1))
                else:lines.append(f'{pad}{key}: {CoreEngine._yaml(v,0).strip()}')
            return '\\n'.join(lines)
        if isinstance(value,list):
            lines=[]
            for v in value:
                if isinstance(v,(dict,list)):
                    nested=CoreEngine._yaml(v,level+1).splitlines();lines.append(pad+'- '+nested[0].lstrip());lines.extend(nested[1:])
                else:lines.append(pad+'- '+CoreEngine._yaml(v,0).strip())
            return '\\n'.join(lines)
        if value is True:return 'true'
        if value is False:return 'false'
        if value is None:return 'null'
        if isinstance(value,(int,float)):return str(value)
        return json.dumps(str(value),ensure_ascii=False)

    def subscription(self,email:str,fmt:str)->tuple[bytes,dict]:
        if fmt not in ('raw','base64','json','clash'):raise CoreError('Unsupported subscription format',status=400)
        settings=self.section('subscription');result=self.links(email)
        if result['warnings']:raise CoreError('Subscription would be incomplete: '+'; '.join(result['warnings']),status=422)
        links=[r['uri'] for r in result['links']]
        if not links:raise CoreError('No enabled supported connection',status=503)
        content_type='text/plain; charset=utf-8'
        if fmt in ('raw','base64'):
            body='\\n'.join(links).encode();body=base64.b64encode(body) if fmt=='base64' else body
        elif fmt=='json':
            body=json.dumps({'version':1,'title':settings.get('profile_title','DARK XRAY'),'client':email,'announce':settings.get('announce',''),'links':result['links']},ensure_ascii=False,indent=2).encode();content_type='application/json; charset=utf-8'
        else:
            proxies=[]
            for item in result['links']:proxies.append(self._clash_proxy(item['uri'],item['remark']))
            names=[p['name'] for p in proxies];doc={'proxies':proxies,'proxy-groups':[{'name':'DARK AUTO','type':'select','proxies':names}], 'rules':['MATCH,DARK AUTO']}
            body=(self._yaml(doc)+'\\n').encode();content_type='application/yaml; charset=utf-8'
        with self.store.lock:r=self.store.db.execute('SELECT * FROM core_clients WHERE email=?',(email,)).fetchone()
        c=json.loads(r['body']);headers={'Content-Type':content_type,'profile-update-interval':str(settings.get('profile_update_interval_hours',6)),
            'profile-title':settings.get('profile_title','DARK XRAY'),
            'subscription-userinfo':f\"upload={r['up']}; download={r['down']}; total={c.get('totalGB',0)}; expire={max(0,c.get('expiryTime',0)//1000)}\"}
        support=settings.get('support_url','');profile=settings.get('profile_url','')
        if support:headers['support-url']=support
        if profile:headers['profile-web-page-url']=profile
        return body,headers
"""
s=once(s,old,new)
p.write_text(s)

p=Path('backend/server.py');s=p.read_text()
old="""        sub=engine.section('subscription')
        if not sub.get('enabled',True):raise HTTPException(404)
        fmt=request.query_params.get('format') or sub.get('default_format','base64')
        body,headers=engine.subscription(row['email'],fmt)
"""
new="""        sub=engine.section('subscription')
        if not sub.get('enabled',True):raise HTTPException(404)
        fmt=request.query_params.get('format')
        if not fmt:
            ua=request.headers.get('user-agent','').lower()
            if sub.get('auto_detect',True) and any(x in ua for x in ('clash','mihomo')):fmt='clash'
            else:fmt=sub.get('default_format','base64')
        body,headers=engine.subscription(row['email'],fmt)
"""
s=once(s,old,new);p.write_text(s)

# Expand Settings V2 subscription controls and save payload.
p=Path('web/settings-v2.js');s=p.read_text()
s=s.replace("segment('default_format',s.default_format||'base64',[['base64','Base64'],['raw','Raw']])","segment('default_format',s.default_format||'base64',[['base64','Base64'],['raw','Raw'],['clash','Clash / Mihomo'],['json','DARK JSON']])")
old="""${field(L('Profile update interval (hours)','بازه آپدیت پروفایل (ساعت)'),'profile_update_interval_hours',s.profile_update_interval_hours||6,'number','', 'min=\"1\" max=\"168\" required')}</div>`)}${card(L('Link naming','نام‌گذاری لینک‌ها'),L('Variables: {remark}, {email}, {protocol}.','متغیرها: {remark}، {email}، {protocol}.'),`<div class=\"sv2-form-grid\">${field(L('Remark template','قالب نام'),'remark_template',s.remark_template||'{remark} | {email}','text','', 'maxlength=\"200\"')}${field(L('Support URL','لینک پشتیبانی'),'support_url',s.support_url||'','url','', 'dir=\"ltr\"')}</div>`) }${saveButton()}</form>`;}
"""
new="""${field(L('Profile update interval (hours)','بازه آپدیت پروفایل (ساعت)'),'profile_update_interval_hours',s.profile_update_interval_hours||6,'number','', 'min=\"1\" max=\"168\" required')}<div class=\"sv2-span-2\">${toggle(L('Auto detect Clash / Mihomo','تشخیص خودکار Clash / Mihomo'),'auto_detect',s.auto_detect!==false,L('Only known Clash/Mihomo user agents are switched automatically.','فقط User-Agentهای شناخته‌شده Clash/Mihomo خودکار تغییر می‌کنند.'))}</div></div>`)}${card(L('Profile identity','هویت پروفایل'),L('Metadata returned with every subscription response.','متادیتایی که همراه پاسخ اشتراک برمی‌گردد.'),`<div class=\"sv2-form-grid\">${field(L('Profile title','عنوان پروفایل'),'profile_title',s.profile_title||'DARK XRAY','text','', 'maxlength=\"120\"')}${field(L('Profile page URL','لینک صفحه پروفایل'),'profile_url',s.profile_url||'','url','', 'dir=\"ltr\"')}${field(L('Support URL','لینک پشتیبانی'),'support_url',s.support_url||'','url','', 'dir=\"ltr\"')}<label class=\"sv2-field sv2-span-2\"><span>${L('Announcement','اعلان')}</span><textarea class=\"field-input\" name=\"announce\" maxlength=\"2000\" rows=\"3\">${esc(s.announce||'')}</textarea></label></div>`)}${card(L('Link naming','نام‌گذاری لینک‌ها'),L('Variables: {remark}, {email}, {protocol}.','متغیرها: {remark}، {email}، {protocol}.'),`<div class=\"sv2-form-grid\">${field(L('Remark template','قالب نام'),'remark_template',s.remark_template||'{remark} | {email}','text','', 'maxlength=\"200\"')}</div>`) }${saveButton()}</form>`;}
"""
if old not in s:raise RuntimeError('settings subscription markup anchor missing')
s=s.replace(old,new,1)
old="""async function saveSubscription(form){const s={...(SV.data.subscription||{})};s.enabled=formValue(form,'enabled')==='true';s.default_format=formValue(form,'default_format');s.profile_update_interval_hours=n(formValue(form,'profile_update_interval_hours'),6);s.remark_template=formValue(form,'remark_template');s.support_url=formValue(form,'support_url').trim();await api('/api/settings/subscription','PUT',{value:s});toast(L('Subscription settings saved.','تنظیمات اشتراک ذخیره شد.'));}
"""
new="""async function saveSubscription(form){const s={...(SV.data.subscription||{})};s.enabled=formValue(form,'enabled')==='true';s.default_format=formValue(form,'default_format');s.auto_detect=formValue(form,'auto_detect')==='true';s.profile_update_interval_hours=n(formValue(form,'profile_update_interval_hours'),6);s.remark_template=formValue(form,'remark_template');s.support_url=formValue(form,'support_url').trim();s.profile_title=formValue(form,'profile_title').trim();s.profile_url=formValue(form,'profile_url').trim();s.announce=formValue(form,'announce').trim();await api('/api/settings/subscription','PUT',{value:s});toast(L('Subscription settings saved.','تنظیمات اشتراک ذخیره شد.'));}
"""
s=once(s,old,new);p.write_text(s)

p=Path('tests/run-tests.sh');s=p.read_text();needle="python -m pytest tests/test_clients_v2.py -q --junitxml=qa/junit/clients-v2.xml\n"
if 'test_subscription_v2.py' not in s:s=s.replace(needle,needle+"python -m pytest tests/test_subscription_v2.py -q --junitxml=qa/junit/subscription-v2.xml\n")
p.write_text(s)
Path('tests/test_subscription_v2.py').write_text('''import base64,json,pytest\nfrom fastapi.testclient import TestClient\nfrom auth import Auth\nfrom core import Config,CoreEngine\nfrom dark_policy import Store,Actor\nfrom manager import Manager\nfrom server import make_app\nOWNER=Actor("dark","owner",{})\nIB={"remark":"SUBV2","listen":"127.0.0.1","port":19701,"protocol":"vless","enable":True,"tag":"subv2","settings":{"decryption":"none"},"streamSettings":{"network":"tcp","security":"none"},"sniffing":{}}\n@pytest.fixture\ndef env(tmp_path):\n store=Store(tmp_path/"d.sqlite3");cfg=Config(xray_binary=str(tmp_path/"missing"),xray_assets=str(tmp_path),public_address="vpn.test",test_engine=True);eng=CoreEngine(cfg,store,tmp_path/"runtime");m=Manager(store,eng);auth=Auth(store,tmp_path/"secret.key");auth.bootstrap("dark","Test!OnlyPassword123");m.owner_put(OWNER,"dark",name="DARK",allowed=[])\n with TestClient(make_app(m,auth,background=False),base_url=cfg.public_origin) as c:\n  r=c.post("/api/auth/login",json={"username":"dark","password":"Test!OnlyPassword123"});c.headers["X-Dark-CSRF"]=r.json()["csrf"];ib=c.post("/api/inbounds",json=IB).json();u=c.post("/api/clients",json={"owner":"dark","client":{"email":"sub-v2"},"inboundIds":[ib["id"]]}).json();yield c,u["subscription_url"]\n store.close()\n\ndef test_formats_and_headers(env):\n c,url=env;s=c.get("/api/settings/subscription").json()["value"];s.update(default_format="raw",auto_detect=True,profile_title="DARK TEST",profile_url="https://profile.test",announce="hello");assert c.put("/api/settings/subscription",json={"value":s}).status_code==200\n raw=c.get(url);assert raw.status_code==200 and raw.content.startswith(b"vless://") and raw.headers["profile-title"]=="DARK TEST"\n b64=c.get(url+"?format=base64");assert base64.b64decode(b64.content).startswith(b"vless://")\n js=c.get(url+"?format=json");doc=js.json();assert doc["title"]=="DARK TEST" and doc["links"][0]["uri"].startswith("vless://")\n clash=c.get(url+"?format=clash");assert clash.status_code==200 and 'DARK AUTO' in clash.text and 'vless' in clash.text\n\ndef test_clash_user_agent_auto_detect(env):\n c,url=env;s=c.get("/api/settings/subscription").json()["value"];s["default_format"]="base64";s["auto_detect"]=True;assert c.put("/api/settings/subscription",json={"value":s}).status_code==200\n r=c.get(url,headers={"user-agent":"mihomo/1.19"});assert r.headers["content-type"].startswith("application/yaml") and 'DARK AUTO' in r.text\n\ndef test_invalid_format_is_not_silently_substituted(env):\n c,url=env;assert c.get(url+"?format=singbox").status_code==400\n''')
print('Subscription V2 patch applied')
