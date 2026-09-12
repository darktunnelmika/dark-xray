#!/usr/bin/env python3
"""EXPLICIT TEST DOUBLE: validates a tiny subset and opens only the loopback API.
This is not Xray, never proxies user traffic, and must never be deployed as a core.
"""
import sys,json,socket,time,signal,os
from pathlib import Path
args=sys.argv[1:]
if args==['version']:
    print('Xray TEST-DOUBLE (not a real Xray binary)');sys.exit(0)
if args[:2]==['api','statsquery']:
    p=Path(os.getcwd())/'stats-fixture.json'
    print(p.read_text() if p.exists() else '{"stat":[]}');sys.exit(0)
if not args or args[0]!='run':sys.exit(2)
cfg=json.loads(Path(args[args.index('-config')+1]).read_text())
if '-test' in args:
    if any(x.get('protocol')=='invalid-test-protocol' for x in cfg['outbounds']):sys.exit(2)
    print('Configuration OK (TEST DOUBLE ONLY)');sys.exit(0)
if any(x.get('settings',{}).get('failTestStartup') for x in cfg['outbounds']):sys.exit(3)
ip,port=cfg['api']['listen'].rsplit(':',1)
s=socket.socket();s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);s.bind((ip,int(port)));s.listen();s.settimeout(.2)
alive=True
def stop(*_):
    global alive
    alive=False
signal.signal(signal.SIGTERM,stop)
while alive:
    try:c,_=s.accept();c.close()
    except socket.timeout:pass
s.close()
