#!/usr/bin/env python3
from pathlib import Path


def once(path,old,new):
    p=Path(path);s=p.read_text()
    if old not in s:raise SystemExit(f'anchor missing in {path}: {old[:140]!r}')
    p.write_text(s.replace(old,new,1))

# Explicit distinction: some supported Xray inbound protocols are infrastructure
# listeners, not credential-bearing customer inbounds.
once('backend/core.py',
"""PROTOCOLS = {'vless','vmess','trojan','shadowsocks','socks','http','dokodemo-door','tunnel'}
""",
"""PROTOCOLS = {'vless','vmess','trojan','shadowsocks','socks','http','dokodemo-door','tunnel'}
MANAGED_CLIENT_PROTOCOLS = {'vless','vmess','trojan','shadowsocks','socks','http'}
""")
once('backend/manager.py',
"""from core import CoreEngine, CoreError, EMAIL_RE
""",
"""from core import CoreEngine, CoreError, EMAIL_RE, MANAGED_CLIENT_PROTOCOLS
""")

# Reject an owner IP ceiling that would silently leave existing clients outside
# policy. Unlimited per-client limit (0) also violates a nonzero ceiling.
once('backend/manager.py',
"""            # Restriction changes may not strand already-owned clients silently.
            with self.store.lock:
                rows=self.store.db.execute('SELECT m.inbounds FROM managed_clients m JOIN clients c ON c.id=m.email WHERE c.owner=?',(owner,)).fetchall()
            if any(not set(json.loads(r['inbounds']))<=set(allowed) for r in rows):
                raise PolicyError('Detach or transfer affected clients before removing their inbound access')
            self.store.register_owner(actor,owner,quota_bytes,max_clients,manual)
""",
"""            # Restriction changes may not strand already-owned clients silently.
            with self.store.lock:
                rows=self.store.db.execute('SELECT m.inbounds,c.limit_ip,c.id FROM managed_clients m JOIN clients c ON c.id=m.email WHERE c.owner=? AND m.state!=\'deleted\'',(owner,)).fetchall()
            if any(not set(json.loads(r['inbounds']))<=set(allowed) for r in rows):
                raise PolicyError('Detach or transfer affected clients before removing their inbound access')
            if max_client_ips and any(r['limit_ip']==0 or r['limit_ip']>max_client_ips for r in rows):
                raise PolicyError('Reduce existing client IP limits before lowering the owner max-client-IP policy')
            self.store.register_owner(actor,owner,quota_bytes,max_clients,manual)
""")

# Customer assignment only to credential-bearing protocols. Existing legacy bad
# assignments can still be fixed by submitting a resulting supported set.
once('backend/manager.py',
"""        known={i['id'] for i in self.engine.inbounds()}
        if not set(ids)<=known: raise PolicyError('Inbound no longer exists')
""",
"""        inbounds={i['id']:i for i in self.engine.inbounds()}
        known=set(inbounds)
        if not set(ids)<=known: raise PolicyError('Inbound no longer exists')
        unsupported=[inbounds[i]['protocol'] for i in ids if inbounds[i]['protocol'] not in MANAGED_CLIENT_PROTOCOLS]
        if unsupported:raise PolicyError('Managed clients require credential-bearing inbounds; unsupported: '+','.join(sorted(set(unsupported))))
""")

# Remove stale reset scheduler rows when a client deletion is finalized.
once('backend/manager.py',
"""            with self.store.transaction() as db:
                db.execute(\"UPDATE managed_clients SET op='none',state='deleted',desired='{}',error='',retry_at=0,attempts=0,updated_at=? WHERE email=?\",(time.time(),email))
            return
""",
"""            with self.store.transaction() as db:
                db.execute('DELETE FROM client_cycles WHERE email=?',(email,))
                db.execute(\"UPDATE managed_clients SET op='none',state='deleted',desired='{}',error='',retry_at=0,attempts=0,updated_at=? WHERE email=?\",(time.time(),email))
            return
""")

# UI selection keeps legacy unsupported assignments visible so an operator can
# uncheck them, but does not offer infrastructure inbounds for new assignments.
once('web/live.js',
"""function selection(ids,allowed){return `<div class=\"check-list\">${state.inbounds.filter(i=>!allowed||allowed.includes(i.id)).map(i=>`<label><input name=\"inbound\" type=\"checkbox\" value=\"${i.id}\" ${ids.includes(i.id)?'checked':''}><span>${e(i.remark||i.tag)} <span class=\"mono\">:${i.port} · ${e(i.protocol)}</span></span></label>`).join('')}</div>`;}
""",
"""function selection(ids,allowed){const customerProtocols=new Set(['vless','vmess','trojan','shadowsocks','socks','http']);return `<div class=\"check-list\">${state.inbounds.filter(i=>(!allowed||allowed.includes(i.id))&&(customerProtocols.has(i.protocol)||ids.includes(i.id))).map(i=>`<label><input name=\"inbound\" type=\"checkbox\" value=\"${i.id}\" ${ids.includes(i.id)?'checked':''}><span>${e(i.remark||i.tag)} <span class=\"mono\">:${i.port} · ${e(i.protocol)}${customerProtocols.has(i.protocol)?'':' · NOT FOR CLIENTS'}</span></span></label>`).join('')}</div>`;}
""")

Path('tests/test_policy_consistency.py').write_text(r'''import json
from test_standalone import env,create,IB


def test_owner_ip_ceiling_cannot_strand_existing_clients(env):
    store,engine,m,auth,c=env
    create(c,extra={'limitIp':5})
    r=c.put('/api/owners/dark',json={'name':'DARK','allowed':[1],'max_client_ips':2})
    assert r.status_code==400,r.text
    assert 'Reduce existing client IP limits' in r.text
    # Once the client is made compliant, the owner ceiling can be lowered.
    assert c.patch('/api/clients/dark-test',json={'client':{'limitIp':2}}).status_code==202
    r=c.put('/api/owners/dark',json={'name':'DARK','allowed':[1],'max_client_ips':2})
    assert r.status_code==200,r.text


def test_non_customer_inbound_cannot_receive_managed_client(env):
    store,engine,m,auth,c=env
    ib=dict(IB);ib.update(protocol='dokodemo-door',tag='infra-door',port=19001,settings={'address':'127.0.0.1','port':80,'network':'tcp'})
    r=c.post('/api/inbounds',json=ib);assert r.status_code==200,r.text
    ids=[x['id'] for x in c.get('/api/inbounds').json()]
    r=c.post('/api/clients',json={'owner':'dark','client':{'email':'bad-infra-client'},'inboundIds':[ids[-1]]})
    assert r.status_code==400,r.text
    assert 'credential-bearing' in r.text


def test_client_delete_cleans_periodic_cycle(env):
    store,engine,m,auth,c=env
    create(c,extra={'reset':30})
    with store.lock:assert store.db.execute('SELECT 1 FROM client_cycles WHERE email=?',('dark-test',)).fetchone()
    assert c.post('/api/clients/dark-test/action',json={'action':'delete'}).status_code==202
    with store.lock:assert store.db.execute('SELECT 1 FROM client_cycles WHERE email=?',('dark-test',)).fetchone() is None
''')

once('tests/run-tests.sh',
"""python -m pytest tests/test_destructive_recovery.py -q --junitxml=qa/junit/destructive-recovery.xml
""",
"""python -m pytest tests/test_destructive_recovery.py -q --junitxml=qa/junit/destructive-recovery.xml
python -m pytest tests/test_policy_consistency.py -q --junitxml=qa/junit/policy-consistency.xml
""")
Path('VERSION').write_text('0.7.5-standalone-lab\n')
print('0.7.5 policy consistency patch applied')
