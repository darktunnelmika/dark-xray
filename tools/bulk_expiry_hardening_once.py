#!/usr/bin/env python3
from pathlib import Path

server=Path('backend/server.py')
s=server.read_text(encoding='utf-8')
old="""                if body.add_days:\n                    current=int(c.get('expiryTime',0));base=current if current>now_ms else now_ms\n                    patch['expiryTime']=max(1000,base+body.add_days*86400000)\n"""
new="""                if body.add_days:\n                    current=int(c.get('expiryTime',0))\n                    if current==0 and body.add_days<0:\n                        raise PolicyError('A no-expiry client cannot be reduced with negative bulk days; set an explicit expiry or disable it instead')\n                    base=current if current>now_ms else now_ms\n                    patch['expiryTime']=max(1000,base+body.add_days*86400000)\n"""
if new not in s:
    if old not in s:raise SystemExit('bulk expiry anchor missing')
    server.write_text(s.replace(old,new,1),encoding='utf-8')

tests=Path('tests/test_clients_v2.py')
t=tests.read_text(encoding='utf-8')
name='test_bulk_negative_days_preserves_no_expiry_semantics'
if name not in t:
    marker='\ndef test_groups_are_owner_scoped(env):\n'
    if marker not in t:raise SystemExit('clients test marker missing')
    block='''\ndef test_bulk_negative_days_preserves_no_expiry_semantics(env):
 _,_,_,c=env;i=seed(c)
 r=c.post("/api/clients",json={"owner":"dark","client":{"email":"no-expiry","totalGB":1073741824},"inboundIds":[i]});assert r.status_code==202
 before=c.get("/api/clients/no-expiry").json();assert before["client"]["expiryTime"]==0
 r=c.post("/api/clients/bulk-adjust",json={"emails":["no-expiry"],"add_days":-1});assert r.status_code==200
 out=r.json();assert out["changed"]==0 and "no-expiry" in out["items"][0]["error"]
 after=c.get("/api/clients/no-expiry").json();assert after["client"]["expiryTime"]==0
 r=c.post("/api/clients/bulk-adjust",json={"emails":["no-expiry"],"add_days":1});assert r.status_code==200 and r.json()["changed"]==1
 assert c.get("/api/clients/no-expiry").json()["client"]["expiryTime"]>0
'''
    tests.write_text(t.replace(marker,block+marker,1),encoding='utf-8')
