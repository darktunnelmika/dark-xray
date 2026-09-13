#!/usr/bin/env python3
from pathlib import Path
p=Path('backend/manager.py');s=p.read_text()
if '\nimport re\n' not in s:
    anchor='import json\n'
    if anchor not in s:raise SystemExit('manager import anchor missing')
    s=s.replace(anchor,anchor+'import re\n',1)
p.write_text(s)
print('manager re import fixed')
