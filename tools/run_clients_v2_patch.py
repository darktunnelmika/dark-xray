#!/usr/bin/env python3
from pathlib import Path
import runpy

runpy.run_path('tools/patch_clients_v2_once.py', run_name='__main__')
p=Path('backend/manager.py')
lines=p.read_text().splitlines()
for i,line in enumerate(lines):
    if "if not 1<=len(name)<=64 or any(ord(ch)<32" in line:
        indent=line[:len(line)-len(line.lstrip())]
        lines[i]=indent+"if not 1<=len(name)<=64 or any(ord(ch)<32 or ch=='/' or ord(ch)==92 for ch in name):raise PolicyError('Invalid group name')"
p.write_text('\n'.join(lines)+'\n')
print('Clients V2 patch normalized')
