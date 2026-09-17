#!/usr/bin/env python3
"""Real Linux kernel/nftables packet smoke in disposable network namespaces.

This test never edits the host namespace ruleset. It proves the exact NftFirewall
rules against real TCP/UDP packets, native nft timeouts and explicit unban.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
from dark_policy import PolicyError
from guardd import BrokerConfig,NftFirewall,TABLE

SRC_IP='1.1.1.1'
DST_IP='1.1.1.2'
DATA_PORT=19443
MGMT_PORT=2222


def run(args:list[str],check:bool=True,**kw):
    return subprocess.run([str(x) for x in args],check=check,text=True,capture_output=True,**kw)


def nft_path()->str:
    path=shutil.which('nft') or ''
    if path not in {'/usr/sbin/nft','/sbin/nft','/usr/bin/nft'}:
        raise RuntimeError('Supported nft executable not found')
    return path


def config()->BrokerConfig:
    return BrokerConfig.from_dict({
        'allowed_uid':1001,
        'allowed_ports':[DATA_PORT],
        'protected_ports':[22,MGMT_PORT,10085],
        'direct_source_verified':True,
        'max_ban_seconds':60,
        'nft_binary':nft_path(),
    })


def inner(action:str)->int:
    fw=NftFirewall(config())
    if action=='ban':
        fw.bootstrap()
        out=fw.dispatch({'operation':'ban','ip':SRC_IP,'ports':[DATA_PORT],'seconds':10},1001)
        print(json.dumps({'action':'ban','result':out,'status':fw.status()}))
        return 0
    if action=='unban':
        fw.ready=True
        out=fw.dispatch({'operation':'unban','ip':SRC_IP},1001)
        print(json.dumps({'action':'unban','result':out,'status':fw.status()}))
        return 0
    if action=='foreign':
        # The caller creates a same-name table with a foreign marker first.
        try:fw.bootstrap()
        except PolicyError as ex:
            if 'foreign table' not in str(ex):raise
            print(json.dumps({'action':'foreign','refused':True,'error':str(ex)}))
            return 0
        raise RuntimeError('Foreign same-name nftables table was not refused')
    raise RuntimeError('Unknown inner action')


SERVER=r'''import socket,sys,threading
ip=sys.argv[1];data=int(sys.argv[2]);mgmt=int(sys.argv[3])

def tcp(port):
 s=socket.socket();s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);s.bind((ip,port));s.listen(20)
 while True:
  c,_=s.accept()
  try:c.recv(64);c.sendall(b'OK')
  except OSError:pass
  finally:c.close()

def udp(port):
 s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);s.bind((ip,port))
 while True:
  data,peer=s.recvfrom(2048);s.sendto(b'OK',peer)
threading.Thread(target=tcp,args=(data,),daemon=True).start()
threading.Thread(target=tcp,args=(mgmt,),daemon=True).start()
threading.Thread(target=udp,args=(data,),daemon=True).start()
threading.Event().wait()
'''

PROBE=r'''import socket,sys
host=sys.argv[1];port=int(sys.argv[2]);proto=sys.argv[3]
try:
 if proto=='tcp':
  s=socket.create_connection((host,port),timeout=1.2);s.settimeout(1.2);s.sendall(b'x');ok=s.recv(8)==b'OK';s.close()
 else:
  s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);s.settimeout(1.2);s.sendto(b'x',(host,port));ok=s.recvfrom(8)[0]==b'OK';s.close()
 raise SystemExit(0 if ok else 2)
except OSError:raise SystemExit(1)
'''


def probe(ns:str,port:int,proto:str,expected:bool)->dict:
    cp=run(['ip','netns','exec',ns,sys.executable,'-c',PROBE,DST_IP,str(port),proto],check=False,timeout=4)
    ok=cp.returncode==0
    if ok!=expected:
        raise RuntimeError(f'{proto.upper()} probe port {port} expected {expected}, got rc={cp.returncode}: {(cp.stderr or cp.stdout)[-300:]}')
    return {'protocol':proto,'port':port,'reachable':ok}


def setup_ns(src:str,dst:str,a:str,b:str)->None:
    run(['ip','netns','add',src]);run(['ip','netns','add',dst])
    run(['ip','link','add',a,'type','veth','peer','name',b])
    run(['ip','link','set',a,'netns',src]);run(['ip','link','set',b,'netns',dst])
    for ns,dev,addr in ((src,a,SRC_IP+'/30'),(dst,b,DST_IP+'/30')):
        run(['ip','netns','exec',ns,'ip','link','set','lo','up'])
        run(['ip','netns','exec',ns,'ip','addr','add',addr,'dev',dev])
        run(['ip','netns','exec',ns,'ip','link','set',dev,'up'])


def outer(report:Path)->int:
    if os.geteuid()!=0:raise RuntimeError('Root is required for network namespace packet validation')
    for cmd in ('ip','nft'):
        if not shutil.which(cmd):raise RuntimeError(cmd+' is required')
    suffix=str(os.getpid())[-5:]
    src='dxs'+suffix;dst='dxd'+suffix;a='vsa'+suffix;b='vsb'+suffix
    proc=None
    result={'kernel_packet_tested':True,'host_namespace_rules_modified':False,'tcp_drop_verified':False,
            'udp_drop_verified':False,'management_port_preserved':False,'native_timeout_verified':False,
            'explicit_unban_verified':False,'foreign_table_refused':False,'passed':False,'checks':[]}
    try:
        setup_ns(src,dst,a,b)
        proc=subprocess.Popen(['ip','netns','exec',dst,sys.executable,'-c',SERVER,DST_IP,str(DATA_PORT),str(MGMT_PORT)],
                              stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,text=True)
        time.sleep(.35)
        if proc.poll() is not None:raise RuntimeError('Namespace test server failed: '+(proc.stderr.read() if proc.stderr else ''))
        result['checks'] += [probe(src,DATA_PORT,'tcp',True),probe(src,DATA_PORT,'udp',True),probe(src,MGMT_PORT,'tcp',True)]

        run(['ip','netns','exec',dst,sys.executable,str(Path(__file__).resolve()),'--inner','ban'])
        result['checks'] += [probe(src,DATA_PORT,'tcp',False),probe(src,DATA_PORT,'udp',False),probe(src,MGMT_PORT,'tcp',True)]
        result['tcp_drop_verified']=True;result['udp_drop_verified']=True;result['management_port_preserved']=True

        time.sleep(10.6)
        result['checks'] += [probe(src,DATA_PORT,'tcp',True),probe(src,DATA_PORT,'udp',True)]
        result['native_timeout_verified']=True

        run(['ip','netns','exec',dst,sys.executable,str(Path(__file__).resolve()),'--inner','ban'])
        probe(src,DATA_PORT,'tcp',False)
        run(['ip','netns','exec',dst,sys.executable,str(Path(__file__).resolve()),'--inner','unban'])
        result['checks'] += [probe(src,DATA_PORT,'tcp',True),probe(src,DATA_PORT,'udp',True)]
        result['explicit_unban_verified']=True

        deleted=run(['ip','netns','exec',dst,nft_path(),'delete','table','inet',TABLE],check=False)
        if deleted.returncode:
            raise RuntimeError('Could not remove DARK test table before foreign-owner check: '+(deleted.stderr or deleted.stdout)[-500:])
        script=f'add table inet {TABLE} {{ comment "FOREIGN TEST TABLE"; }}\n'
        created=run(['ip','netns','exec',dst,nft_path(),'-f','-'],check=False,input=script)
        if created.returncode:
            raise RuntimeError('Could not create foreign-owner nft test table: '+(created.stderr or created.stdout)[-500:])
        run(['ip','netns','exec',dst,sys.executable,str(Path(__file__).resolve()),'--inner','foreign'])
        result['foreign_table_refused']=True
        result['passed']=all(result[k] for k in ('tcp_drop_verified','udp_drop_verified','management_port_preserved',
                                                  'native_timeout_verified','explicit_unban_verified','foreign_table_refused'))
    finally:
        if proc and proc.poll() is None:
            proc.terminate()
            try:proc.wait(timeout=3)
            except subprocess.TimeoutExpired:proc.kill();proc.wait()
        for ns in (src,dst):run(['ip','netns','del',ns],check=False)
        report.parent.mkdir(parents=True,exist_ok=True)
        report.write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result,indent=2))
    return 0 if result['passed'] else 1


def main()->int:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--inner',choices=['ban','unban','foreign'])
    p.add_argument('--report',type=Path,default=Path('qa/kernel-firewall.json'))
    a=p.parse_args()
    return inner(a.inner) if a.inner else outer(a.report)


if __name__=='__main__':raise SystemExit(main())
