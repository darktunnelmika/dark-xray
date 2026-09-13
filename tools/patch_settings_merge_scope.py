#!/usr/bin/env python3
from pathlib import Path
p=Path('backend/core.py');s=p.read_text()
old="""        saved=json.loads(r[0]);base=copy.deepcopy(defaults[name])
        if isinstance(base,dict) and isinstance(saved,dict):
            base.update(saved);return base
        return saved
"""
new="""        saved=json.loads(r[0])
        if name in {'panel','runtime','subscription','ipguard'} and isinstance(saved,dict):
            base=copy.deepcopy(defaults[name]);base.update(saved);return base
        return saved
"""
if new not in s:
    assert old in s
    s=s.replace(old,new,1)
p.write_text(s)
