#!/usr/bin/env python3
"""Temporary repository migration helper for Settings V2."""
from pathlib import Path


def once(s, old, new):
    if new in s:
        return s
    if old not in s:
        raise RuntimeError('migration anchor not found: '+old[:100])
    return s.replace(old,new,1)

# backend/core.py
p=Path('backend/core.py');s=p.read_text()
s=once(s,"SECTIONS = {'outbounds','routing','dns','policy','observatory','hosts','panel','ipguard'}","SECTIONS = {'outbounds','routing','dns','policy','observatory','hosts','panel','runtime','subscription','ipguard'}")
old="""        defaults={'outbounds':[{'tag':'direct','protocol':'freedom','settings':{}},{'tag':'block','protocol':'blackhole','settings':{}}],
                  'routing':{'domainStrategy':'AsIs','rules':[]},'dns':{'servers':['1.1.1.1']},'policy':{},
                  'observatory':{},'hosts':[],'panel':{'title':'DARK XRAY','support_url':''},
                  'ipguard':{'mode':'observe','window_seconds':self.config.ip_window_seconds,
                             'ban_seconds':self.config.ip_ban_seconds,'exempt_ips':self.config.ip_exempt_ips}}
"""
new="""        origin=urlsplit(self.config.public_origin);access_mode='domain_tls' if origin.scheme=='https' else 'ssh'
        defaults={'outbounds':[{'tag':'direct','protocol':'freedom','settings':{}},{'tag':'block','protocol':'blackhole','settings':{}}],
                  'routing':{'domainStrategy':'AsIs','rules':[]},'dns':{'servers':['1.1.1.1']},'policy':{},
                  'observatory':{},'hosts':[],
                  'panel':{'title':'DARK XRAY','support_url':'','language':'en','timezone':'UTC','page_size':50,
                           'session_max_age_minutes':480,'datepicker':'gregorian','density':'comfortable','reduced_motion':False},
                  'runtime':{'access_mode':access_mode,'bind_port':self.config.bind_port,'public_address':self.config.public_address,
                             'poll_seconds':self.config.poll_seconds,'core_autostart':self.config.core_autostart,
                             'domain':(origin.hostname or '') if access_mode=='domain_tls' else '','acme_email':''},
                  'subscription':{'enabled':True,'default_format':'base64','profile_update_interval_hours':6,
                                  'remark_template':'{remark} | {email}','support_url':''},
                  'ipguard':{'mode':'observe','window_seconds':self.config.ip_window_seconds,
                             'ban_seconds':self.config.ip_ban_seconds,'exempt_ips':self.config.ip_exempt_ips}}
"""
s=once(s,old,new)
if "Panel settings shape is incomplete" not in s:
    marker="        if name=='ipguard':\n"
    insert=r"""        if name=='panel':
            allowed={'title','support_url','language','timezone','page_size','session_max_age_minutes','datepicker','density','reduced_motion'}
            if set(value)!=allowed:raise CoreError('Panel settings shape is incomplete or contains unknown fields')
            if not isinstance(value['title'],str) or not 1<=len(value['title'])<=80:raise CoreError('Invalid panel title')
            support=value['support_url']
            if not isinstance(support,str) or len(support)>500:raise CoreError('Invalid support URL')
            if support:
                u=urlsplit(support)
                if u.scheme not in ('http','https') or not u.hostname or u.username or u.password:raise CoreError('Support URL must be http/https without credentials')
            if value['language'] not in ('en','fa'):raise CoreError('Unsupported panel language')
            if value['datepicker'] not in ('gregorian','jalalian'):raise CoreError('Unsupported calendar')
            if value['density'] not in ('comfortable','compact') or type(value['reduced_motion']) is not bool:raise CoreError('Invalid appearance settings')
            for key,low,high in [('page_size',10,1000),('session_max_age_minutes',60,525600)]:
                if type(value[key]) is not int or not low<=value[key]<=high:raise CoreError('Invalid '+key)
            try:
                from zoneinfo import ZoneInfo
                ZoneInfo(value['timezone'])
            except Exception:raise CoreError('Invalid IANA timezone')
        if name=='runtime':
            allowed={'access_mode','bind_port','public_address','poll_seconds','core_autostart','domain','acme_email'}
            if set(value)!=allowed:raise CoreError('Runtime settings shape is incomplete or contains unknown fields')
            if value['access_mode'] not in ('ssh','domain_tls'):raise CoreError('Invalid access mode')
            for key,low,high in [('bind_port',1024,65535),('poll_seconds',1,3600)]:
                if type(value[key]) is not int or not low<=value[key]<=high:raise CoreError('Invalid '+key)
            if type(value['core_autostart']) is not bool:raise CoreError('core_autostart must be boolean')
            addr=value['public_address']
            if not isinstance(addr,str) or not addr or len(addr)>253 or any(c in addr for c in '/?#@ \r\n\t'):raise CoreError('Invalid public proxy address')
            reserved=(set(self.config.protected_ports)-{self.config.bind_port})|{22,self.config.xray_api_port}
            if value['bind_port'] in reserved or any(i['port']==value['bind_port'] for i in self.inbounds()):raise CoreError('Panel port collides with a protected or data port')
            domain=value['domain'];email=value['acme_email']
            if not isinstance(domain,str) or len(domain)>253 or not isinstance(email,str) or len(email)>254:raise CoreError('Invalid domain/ACME values')
            if value['access_mode']=='domain_tls':
                if not re.fullmatch(r'(?=.{1,253}$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}',domain):raise CoreError('Domain + TLS mode requires a valid ASCII domain')
                if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',email):raise CoreError('Domain + TLS mode requires a valid ACME email')
            elif domain or email:raise CoreError('Domain and ACME email must be empty in SSH mode')
        if name=='subscription':
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
    if marker not in s:raise RuntimeError('ipguard validation anchor missing')
    s=s.replace(marker,insert+marker,1)
s=once(s,"        return {'saved':True,'applied':False,'runtime':self.runtime_state()}\n","        result={'saved':True,'applied':False,'runtime':self.runtime_state()}\n        if name=='runtime':result.update(requires_root_apply=True,apply_command='sudo darkxray settings-apply')\n        return result\n")
old="""                address=host.get('address',self.config.public_address);port=host.get('port',ib['port']);label=host.get('remark',ib['remark'])+' | '+email
                proto=ib['protocol'];st=ib['streamSettings'];net=st.get('network','tcp');sec=st.get('security','none')
"""
new="""                address=host.get('address',self.config.public_address);port=host.get('port',ib['port'])
                proto=ib['protocol'];sub=self.section('subscription');base_remark=host.get('remark',ib['remark'])
                label=sub.get('remark_template','{remark} | {email}').replace('{remark}',base_remark).replace('{email}',email).replace('{protocol}',proto.upper())
                st=ib['streamSettings'];net=st.get('network','tcp');sec=st.get('security','none')
"""
s=once(s,old,new)
if "settings=self.section('subscription')" not in s:
    start=s.index("    def subscription(self,email:str,fmt:str)->tuple[bytes,dict]:")
    end=s.index("\n    def close(self):",start)
    method=r'''    def subscription(self,email:str,fmt:str)->tuple[bytes,dict]:
        if fmt not in ('raw','base64'):raise CoreError('This release exports raw/base64; other formats are not silently substituted',status=400)
        settings=self.section('subscription')
        result=self.links(email)
        if result['warnings']:raise CoreError('Subscription would be incomplete: '+'; '.join(result['warnings']),status=422)
        body='\n'.join(r['uri'] for r in result['links']).encode()
        if not body:raise CoreError('No enabled supported connection',status=503)
        if fmt=='base64':body=base64.b64encode(body)
        with self.store.lock:r=self.store.db.execute('SELECT * FROM core_clients WHERE email=?',(email,)).fetchone()
        c=json.loads(r['body']);headers={'Content-Type':'text/plain; charset=utf-8','profile-update-interval':str(settings.get('profile_update_interval_hours',6)),
            'subscription-userinfo':f"upload={r['up']}; download={r['down']}; total={c.get('totalGB',0)}; expire={max(0,c.get('expiryTime',0)//1000)}"}
        support=settings.get('support_url','')
        if support:headers['support-url']=support
        return body,headers
'''
    s=s[:start]+method+s[end:]
p.write_text(s)

# backend/server.py
p=Path('backend/server.py');s=p.read_text()
s=once(s,"        response=JSONResponse({'id':p.actor.id,'role':p.actor.role,'csrf':p.csrf,'permissions':p.actor.permissions})\n        response.set_cookie(COOKIE,token,max_age=8*3600,httponly=True,secure=config.secure_cookie,samesite='strict',path='/')\n","        response=JSONResponse({'id':p.actor.id,'role':p.actor.role,'csrf':p.csrf,'permissions':p.actor.permissions})\n        session_minutes=int(engine.section('panel').get('session_max_age_minutes',480))\n        response.set_cookie(COOKIE,token,max_age=session_minutes*60,httponly=True,secure=config.secure_cookie,samesite='strict',path='/')\n")
s=once(s,"            'poll_seconds':config.poll_seconds,'engine_version':engine.version,'independent':True,'test_engine':config.test_engine}\n","            'poll_seconds':config.poll_seconds,'engine_version':engine.version,'independent':True,'test_engine':config.test_engine,\n            'ui':engine.section('panel')}\n")
s=once(s,"        fmt=request.query_params.get('format','base64')\n        body,headers=engine.subscription(row['email'],fmt)\n","        sub=engine.section('subscription')\n        if not sub.get('enabled',True):raise HTTPException(404)\n        fmt=request.query_params.get('format') or sub.get('default_format','base64')\n        body,headers=engine.subscription(row['email'],fmt)\n")
old="""        result=engine.save_section(section,body['value']);manager.tick(suppress=True)
        manager.audit(p.actor,p.actor.id,'settings.update',section);return result

    @app.get('/api/core/state')
"""
new="""        result=engine.save_section(section,body['value'])
        if section in {'outbounds','routing','dns','policy','observatory','hosts','ipguard'}:manager.tick(suppress=True)
        manager.audit(p.actor,p.actor.id,'settings.update',section);return result

    @app.get('/api/runtime-config')
    def runtime_config(p:Principal=Depends(owner)):
        desired=engine.section('runtime');origin=urlsplit(config.public_origin);mode='domain_tls' if origin.scheme=='https' else 'ssh'
        actual={'access_mode':mode,'bind_host':config.bind_host,'bind_port':config.bind_port,'public_address':config.public_address,
                'public_origin':config.public_origin,'poll_seconds':config.poll_seconds,'core_autostart':config.core_autostart,
                'domain':(origin.hostname or '') if mode=='domain_tls' else '','tls_enabled':bool(config.tls_certificate and config.tls_private_key),
                'tls_certificate':config.tls_certificate,'secure_cookie':config.secure_cookie,'xray_api_port':config.xray_api_port,
                'direct_source_verified':config.direct_source_verified,'guard_socket':config.guard_socket}
        compare=('access_mode','bind_port','public_address','poll_seconds','core_autostart','domain')
        pending={k:{'from':actual.get(k),'to':desired.get(k)} for k in compare if actual.get(k)!=desired.get(k)}
        return {'actual':actual,'desired':desired,'pending':pending,'apply_command':'sudo darkxray settings-apply'}

    @app.get('/api/core/state')
"""
s=once(s,old,new);p.write_text(s)

# wrapper and menu
p=Path('darkxray');s=p.read_text();s=once(s,' guard-enable) shift; exec "$PY" "$ROOT/tools/guard-enable.py" "$@" ;;\n selftest) shift; exec "$PY" "$ROOT/tools/smoke-real.py" "$@" ;;\n',' guard-enable) shift; exec "$PY" "$ROOT/tools/guard-enable.py" "$@" ;;\n settings-apply) shift; exec "$PY" "$ROOT/tools/settings_apply.py" --config "$CONFIG" --data "$DATA" "$@" ;;\n selftest) shift; exec "$PY" "$ROOT/tools/smoke-real.py" "$@" ;;\n');p.write_text(s)
p=Path('tools/menu.py');s=p.read_text();s=once(s,"  4) Reset owner password\n  5) Show access URL / SSH tunnel\n  0) Back''')\n","  4) Reset owner password\n  5) Show access URL / SSH tunnel\n  6) Preview / apply staged Web Settings\n  0) Back''')\n");s=once(s,"        elif x=='5':\n            port=c.get('bind_port',2087);print('Panel URL:',endpoint(c));print(f'SSH tunnel: ssh -L {port}:127.0.0.1:{port} root@SERVER -p SSH_PORT');pause()\n","        elif x=='5':\n            port=c.get('bind_port',2087);print('Panel URL:',endpoint(c));print(f'SSH tunnel: ssh -L {port}:127.0.0.1:{port} root@SERVER -p SSH_PORT');pause()\n        elif x=='6':\n            run([COMMAND,'settings-apply','--dry-run'])\n            if need_root() and confirm('Apply the staged Web Settings and restart services if required?'):\n                run([COMMAND,'settings-apply'])\n            pause()\n");p.write_text(s)

# web wiring and native 8-char account policy
p=Path('web/live.js');s=p.read_text().replace('حداقل ۱۲ کاراکتر','حداقل ۸ کاراکتر').replace('minlength="12"','minlength="8"');p.write_text(s)
p=Path('web/index.html');s=p.read_text();
if '/assets/settings-v2.css' not in s:s=s.replace('<link rel="stylesheet" href="/assets/inbounds-v3.css">','<link rel="stylesheet" href="/assets/inbounds-v3.css"><link rel="stylesheet" href="/assets/settings-v2.css">')
s=s.replace('<script defer src="/assets/runtime-policy.js"></script>','')
if '/assets/settings-v2.js' not in s:s=s.replace('<script defer src="/assets/forms06.js"></script>','<script defer src="/assets/forms06.js"></script><script defer src="/assets/settings-v2.js"></script>')
p.write_text(s)

# test / CI wiring
p=Path('tests/run-tests.sh');s=p.read_text();needle="python -m pytest tests/test_v06.py -q --junitxml=qa/junit/v06.xml\n";
if 'tests/test_settings_v2.py' not in s:s=s.replace(needle,needle+"python -m pytest tests/test_settings_v2.py -q --junitxml=qa/junit/settings-v2.xml\n",1)
p.write_text(s)
p=Path('.github/workflows/ci.yml');s=p.read_text();s=s.replace('python -m py_compile tools/menu.py tools/update.py tools/provision.py backend/server.py backend/policy_auth.py','python -m py_compile tools/menu.py tools/update.py tools/provision.py tools/settings_apply.py backend/server.py backend/policy_auth.py backend/reality_scan.py');
if 'node --check web/settings-v2.js' not in s:s=s.replace('node --check web/inbounds-v3.js','node --check web/inbounds-v3.js\n          node --check web/settings-v2.js')
s=s.replace("          grep -q '/assets/runtime-policy.js' web/index.html\n",'')
if "'/assets/settings-v2.js'" not in s:s=s.replace("          grep -q '/assets/inbounds-v3.js' web/index.html\n", "          grep -q '/assets/inbounds-v3.js' web/index.html\n          grep -q '/assets/settings-v2.css' web/index.html\n          grep -q '/assets/settings-v2.js' web/index.html\n          grep -q \"settings-apply\" darkxray\n")
p.write_text(s)
