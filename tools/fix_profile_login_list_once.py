#!/usr/bin/env python3
from pathlib import Path
p=Path('tools/menu.py');s=p.read_text()
old="""            logins={r[0] for r in db.execute(\"SELECT id FROM api_admins WHERE role='owner'\")}
            return [r[0] for r in db.execute('SELECT id FROM owner_profiles ORDER BY id') if r[0] not in logins]"""
new="""            logins={r[0] for r in db.execute(\"SELECT id FROM api_admins\")}
            return [r[0] for r in db.execute('SELECT id FROM owner_profiles ORDER BY id') if r[0] not in logins]"""
if old not in s:raise SystemExit('profile/login anchor missing')
p.write_text(s.replace(old,new,1))
print('profile/login list fixed')
