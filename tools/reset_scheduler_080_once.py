#!/usr/bin/env python3
from pathlib import Path


def once(path,old,new):
    p=Path(path);s=p.read_text()
    if old not in s:raise SystemExit(f'anchor missing in {path}: {old[:160]!r}')
    p.write_text(s.replace(old,new,1))

# ---------------------------------------------------------------------------
# Manager: persistent schedule mode/day + timezone-aware monthly calculation.
# ---------------------------------------------------------------------------
once('backend/manager.py',
"""import copy
import hashlib
import json
""",
"""import calendar
import copy
import datetime
import hashlib
import json
""")
once('backend/manager.py',
"""import uuid
from typing import Any
""",
"""import uuid
from typing import Any
from zoneinfo import ZoneInfo
""")

once('backend/manager.py',
"""            CREATE TABLE IF NOT EXISTS client_cycles(
              email TEXT PRIMARY KEY,days INTEGER NOT NULL,next_at REAL NOT NULL,
              completed INTEGER NOT NULL DEFAULT 0,max_resets INTEGER NOT NULL DEFAULT 0);
""",
"""            CREATE TABLE IF NOT EXISTS client_cycles(
              email TEXT PRIMARY KEY,days INTEGER NOT NULL,next_at REAL NOT NULL,
              completed INTEGER NOT NULL DEFAULT 0,max_resets INTEGER NOT NULL DEFAULT 0,
              mode TEXT NOT NULL DEFAULT 'interval',reset_day INTEGER NOT NULL DEFAULT 0);
""")
# Existing installations need additive migration before scheduler reads columns.
once('backend/manager.py',
"""            CREATE INDEX IF NOT EXISTS client_groups_owner ON client_groups(owner,name);
            ''')
""",
"""            CREATE INDEX IF NOT EXISTS client_groups_owner ON client_groups(owner,name);
            ''')
            cycle_cols={r[1] for r in store.db.execute('PRAGMA table_info(client_cycles)')}
            if 'mode' not in cycle_cols:store.db.execute("ALTER TABLE client_cycles ADD COLUMN mode TEXT NOT NULL DEFAULT 'interval'")
            if 'reset_day' not in cycle_cols:store.db.execute("ALTER TABLE client_cycles ADD COLUMN reset_day INTEGER NOT NULL DEFAULT 0")
""")

old="""        if 'reset' in out: integer(out['reset'],0,3650)
        if 'resetCount' in out: integer(out['resetCount'],0,100000)
        for key in ('resetDay','resetTraffic','resetTrafficDay'):
            if out.get(key): raise PolicyError('Only fixed-day quota reset cycles are supported in this release')
"""
new="""        if 'reset' in out: integer(out['reset'],0,3650)
        if 'resetCount' in out: integer(out['resetCount'],0,100000)
        if out.get('resetDay'):raise PolicyError('Legacy resetDay is not used by DARK; use resetTrafficDay for monthly traffic reset')
        if 'resetTraffic' in out:
            if not isinstance(out['resetTraffic'],str) or out['resetTraffic'] not in ('','never','hourly','daily','weekly','monthly'):
                raise PolicyError('resetTraffic must be never, hourly, daily, weekly or monthly')
            out['resetTraffic']=out['resetTraffic'] or 'never'
        if 'resetTrafficDay' in out:integer(out['resetTrafficDay'],0,31)
        mode=out.get('resetTraffic','never');days=int(out.get('reset',0) or 0);day=int(out.get('resetTrafficDay',0) or 0)
        if mode!='never' and days:raise PolicyError('Choose either a calendar traffic reset or a custom day interval, not both')
        if mode=='monthly' and not 1<=day<=31:raise PolicyError('Monthly traffic reset requires resetTrafficDay from 1 to 31')
        if mode!='monthly' and day:raise PolicyError('resetTrafficDay is only valid for monthly traffic reset')
"""
once('backend/manager.py',old,new)

# Defaults on create so older consumers always see a stable shape.
once('backend/manager.py',
"""            for k,v in {'flow':'','security':'auto','limitIp':1,'limitHwid':0,'totalGB':0,
                        'expiryTime':0,'enable':True,'tgId':0,'group':'','comment':'','reset':0}.items():data.setdefault(k,v)
""",
"""            for k,v in {'flow':'','security':'auto','limitIp':1,'limitHwid':0,'totalGB':0,
                        'expiryTime':0,'enable':True,'tgId':0,'group':'','comment':'','reset':0,
                        'resetTraffic':'never','resetTrafficDay':0,'resetCount':0}.items():data.setdefault(k,v)
""")

old_start="""    def _schedule_cycles(self):
        \"\"\"Schedule at most one reset for missed periods; persist the next deadline.

        Expiry and manual/owner blocks remain independent. Completion advances
        the schedule only after a successful reset; crash-uncertain resets are
        deliberately not replayed.
        \"\"\"
        now=time.time()
        with self.store.transaction() as db:
            metas=db.execute(\"SELECT * FROM managed_clients WHERE state!='deleted'\").fetchall()
            for meta in metas:
                desired=json.loads(meta['desired']);days=desired.get('reset',0)
                if not days:
                    db.execute('DELETE FROM client_cycles WHERE email=?',(meta['email'],));continue
                row=db.execute('SELECT * FROM client_cycles WHERE email=?',(meta['email'],)).fetchone()
                cap=desired.get('resetCount',0)
                if not row or row['days']!=days:
                    db.execute('INSERT INTO client_cycles(email,days,next_at,completed,max_resets) VALUES(?,?,?,0,?) ON CONFLICT(email) DO UPDATE SET days=excluded.days,next_at=excluded.next_at,completed=0,max_resets=excluded.max_resets',
                               (meta['email'],days,now+days*86400,cap));continue
                db.execute('UPDATE client_cycles SET max_resets=? WHERE email=?',(cap,meta['email']))
                if cap and row['completed']>=cap:continue
                if row['next_at']<=now and meta['op']=='none' and meta['state']=='applied':
                    db.execute(\"UPDATE managed_clients SET op='reset',state='pending',retry_at=0,attempts=0,error='' WHERE email=?\",(meta['email'],))

    def _complete_cycle(self,email):
        with self.store.transaction() as db:
            row=db.execute('SELECT * FROM client_cycles WHERE email=?',(email,)).fetchone()
            now=time.time()
            if row and row['next_at']<=now:
                period=row['days']*86400
                next_at=row['next_at']+(int((now-row['next_at'])//period)+1)*period
                db.execute('UPDATE client_cycles SET next_at=?,completed=completed+1 WHERE email=?',(next_at,email))
"""
new_start="""    @staticmethod
    def _schedule_spec(desired:dict)->tuple[str,int,int]|None:
        mode=str(desired.get('resetTraffic','never') or 'never')
        days=int(desired.get('reset',0) or 0);day=int(desired.get('resetTrafficDay',0) or 0)
        if mode=='never':return ('interval',days,0) if days else None
        return mode,0,day

    def _monthly_next_at(self,day:int,now:float)->float:
        try:zone=ZoneInfo(str(self.engine.section('panel').get('timezone','UTC')))
        except Exception:zone=ZoneInfo('UTC')
        current=datetime.datetime.fromtimestamp(now,zone)
        year,month=current.year,current.month
        for _ in range(14):
            last=calendar.monthrange(year,month)[1];target=min(max(1,day),last)
            candidate=datetime.datetime(year,month,target,0,0,0,tzinfo=zone)
            if candidate.timestamp()>now:return candidate.timestamp()
            month+=1
            if month>12:month=1;year+=1
        raise PolicyError('Could not calculate next monthly traffic reset')

    @staticmethod
    def _period_seconds(mode:str,days:int)->int:
        return {'hourly':3600,'daily':86400,'weekly':604800,'interval':days*86400}.get(mode,0)

    def _initial_cycle_at(self,mode:str,days:int,reset_day:int,now:float)->float:
        if mode=='monthly':return self._monthly_next_at(reset_day,now)
        period=self._period_seconds(mode,days)
        if period<=0:raise PolicyError('Invalid traffic reset schedule')
        return now+period

    def _schedule_cycles(self):
        \"\"\"Schedule one destructive reset at a time with durable deadlines.

        Hourly/daily/weekly/custom intervals are deadline based. Monthly uses the
        panel IANA timezone and clamps day 29-31 to that month's last day. Missed
        periods collapse into one reset; crash-uncertain resets are never replayed.
        \"\"\"
        now=time.time()
        with self.store.transaction() as db:
            metas=db.execute(\"SELECT * FROM managed_clients WHERE state!='deleted'\").fetchall()
            for meta in metas:
                desired=json.loads(meta['desired']);spec=self._schedule_spec(desired)
                if not spec:
                    db.execute('DELETE FROM client_cycles WHERE email=?',(meta['email'],));continue
                mode,days,reset_day=spec;cap=int(desired.get('resetCount',0) or 0)
                row=db.execute('SELECT * FROM client_cycles WHERE email=?',(meta['email'],)).fetchone()
                changed=not row or row['mode']!=mode or row['days']!=days or row['reset_day']!=reset_day
                if changed:
                    next_at=self._initial_cycle_at(mode,days,reset_day,now)
                    db.execute('''INSERT INTO client_cycles(email,days,next_at,completed,max_resets,mode,reset_day)
                        VALUES(?,?,?,0,?,?,?) ON CONFLICT(email) DO UPDATE SET days=excluded.days,
                        next_at=excluded.next_at,completed=0,max_resets=excluded.max_resets,
                        mode=excluded.mode,reset_day=excluded.reset_day''',
                        (meta['email'],days,next_at,cap,mode,reset_day));continue
                db.execute('UPDATE client_cycles SET max_resets=? WHERE email=?',(cap,meta['email']))
                if cap and row['completed']>=cap:continue
                if row['next_at']<=now and meta['op']=='none' and meta['state']=='applied':
                    db.execute(\"UPDATE managed_clients SET op='reset',state='pending',retry_at=0,attempts=0,error='' WHERE email=?\",(meta['email'],))

    def _complete_cycle(self,email):
        with self.store.transaction() as db:
            row=db.execute('SELECT * FROM client_cycles WHERE email=?',(email,)).fetchone();now=time.time()
            if row and row['next_at']<=now:
                if row['mode']=='monthly':next_at=self._monthly_next_at(row['reset_day'],now)
                else:
                    period=self._period_seconds(row['mode'],row['days'])
                    if period<=0:raise PolicyError('Invalid persisted traffic reset schedule')
                    next_at=row['next_at']+(int((now-row['next_at'])//period)+1)*period
                db.execute('UPDATE client_cycles SET next_at=?,completed=completed+1 WHERE email=?',(next_at,email))
"""
once('backend/manager.py',old_start,new_start)

# ---------------------------------------------------------------------------
# UI: one schedule selector; retain custom interval for backward compatibility.
# ---------------------------------------------------------------------------
old_form="""${field('شناسه تلگرام','tgId',c.tgId||0,'number','min=\"0\" step=\"1\"')}${field('ریست دوره‌ای حجم (روز) · صفر خاموش','reset',c.reset||0,'number','min=\"0\" max=\"3650\"')}${field('گروه','group',c.group||'')}"""
new_form="""${field('شناسه تلگرام','tgId',c.tgId||0,'number','min=\"0\" step=\"1\"')}${select('Traffic reset schedule','resetMode',[['never','Never'],['hourly','Hourly'],['daily','Daily'],['weekly','Weekly'],['monthly','Monthly'],['interval','Custom interval']],(c.resetTraffic&&c.resetTraffic!=='never')?c.resetTraffic:(c.reset>0?'interval':'never'))}${field('Custom reset interval (days)','resetDays',c.reset||0,'number','min=\"0\" max=\"3650\"')}${field('Monthly reset day (1-31)','resetTrafficDay',c.resetTrafficDay||0,'number','min=\"0\" max=\"31\"')}${field('Maximum resets · 0 unlimited','resetCount',c.resetCount||0,'number','min=\"0\" max=\"100000\"')}${field('گروه','group',c.group||'')}"""
once('web/live.js',old_form,new_form)
old_payload="""let payload={limitIp:num(f,'limitIp'),limitHwid:num(f,'limitHwid'),totalGB:Math.round(num(f,'totalGB')*gb),enable:f.get('enable')==='true',flow:f.get('flow'),tgId:num(f,'tgId'),reset:num(f,'reset'),group:f.get('group'),comment:f.get('comment')};"""
new_payload="""let resetMode=f.get('resetMode'),resetDays=num(f,'resetDays'),resetDay=num(f,'resetTrafficDay');if(resetMode==='interval'&&resetDays<1)throw Error('Custom reset interval requires at least 1 day.');if(resetMode==='monthly'&&(resetDay<1||resetDay>31))throw Error('Monthly reset requires a day from 1 to 31.');let payload={limitIp:num(f,'limitIp'),limitHwid:num(f,'limitHwid'),totalGB:Math.round(num(f,'totalGB')*gb),enable:f.get('enable')==='true',flow:f.get('flow'),tgId:num(f,'tgId'),reset:resetMode==='interval'?resetDays:0,resetTraffic:['hourly','daily','weekly','monthly'].includes(resetMode)?resetMode:'never',resetTrafficDay:resetMode==='monthly'?resetDay:0,resetCount:num(f,'resetCount'),group:f.get('group'),comment:f.get('comment')};"""
once('web/live.js',old_payload,new_payload)

# English-first dictionary for legacy live.js labels.
p=Path('web/i18n-en.js');s=p.read_text();anchor=""" 'ریست دوره‌ای حجم (روز) · صفر خاموش':'Periodic traffic reset (days) · 0 = off',
"""
if anchor in s:
    s=s.replace(anchor,anchor+" 'Traffic reset schedule':'Traffic reset schedule',\n 'Custom reset interval (days)':'Custom reset interval (days)',\n 'Monthly reset day (1-31)':'Monthly reset day (1-31)',\n 'Maximum resets · 0 unlimited':'Maximum resets · 0 unlimited',\n",1)
p.write_text(s)

# ---------------------------------------------------------------------------
# Tests.
# ---------------------------------------------------------------------------
Path('tests/test_reset_scheduler_v2.py').write_text(r'''import calendar,datetime,time
from zoneinfo import ZoneInfo
import pytest
from test_standalone import env,create
from dark_policy import PolicyError


def test_calendar_schedule_validation_rejects_ambiguous_or_bad_monthly(env):
    store,engine,m,auth,c=env
    r=c.post('/api/clients',json={'owner':'dark','client':{'email':'bad','reset':2,'resetTraffic':'daily'},'inboundIds':[1]})
    assert r.status_code==400 and 'either a calendar traffic reset or a custom day interval' in r.text
    r=c.post('/api/clients',json={'owner':'dark','client':{'email':'bad2','resetTraffic':'monthly','resetTrafficDay':0},'inboundIds':[1]})
    assert r.status_code==400 and 'Monthly traffic reset requires' in r.text


def test_hourly_schedule_persists_mode_and_advances_once(env):
    store,engine,m,auth,c=env
    create(c,extra={'resetTraffic':'hourly','resetTrafficDay':0,'resetCount':2})
    with store.lock:r=store.db.execute('SELECT * FROM client_cycles WHERE email=?',('dark-test',)).fetchone()
    assert r['mode']=='hourly' and r['completed']==0 and r['next_at']>time.time()
    with store.transaction() as db:db.execute('UPDATE client_cycles SET next_at=? WHERE email=?',(time.time()-3700,'dark-test'))
    m.tick(suppress=False)
    with store.lock:r=store.db.execute('SELECT * FROM client_cycles WHERE email=?',('dark-test',)).fetchone()
    assert r['completed']==1 and r['next_at']>time.time()


def test_monthly_day_31_clamps_to_last_day_in_panel_timezone(env):
    store,engine,m,auth,c=env
    panel=engine.section('panel');panel['timezone']='UTC';engine.save_section('panel',panel)
    now=datetime.datetime(2027,2,1,12,0,tzinfo=ZoneInfo('UTC')).timestamp()
    nxt=m._monthly_next_at(31,now);dt=datetime.datetime.fromtimestamp(nxt,ZoneInfo('UTC'))
    assert (dt.year,dt.month,dt.day,dt.hour)==(2027,2,28,0)


def test_legacy_interval_schedule_remains_supported(env):
    store,engine,m,auth,c=env
    create(c,extra={'reset':2,'resetTraffic':'never'})
    with store.lock:r=store.db.execute('SELECT * FROM client_cycles WHERE email=?',('dark-test',)).fetchone()
    assert r['mode']=='interval' and r['days']==2


def test_manual_disable_survives_scheduled_reset(env):
    store,engine,m,auth,c=env
    create(c,extra={'resetTraffic':'hourly','enable':False})
    with store.transaction() as db:db.execute('UPDATE client_cycles SET next_at=? WHERE email=?',(time.time()-10,'dark-test'))
    m.tick(suppress=False)
    d=c.get('/api/clients/dark-test').json()
    assert 'client_manual' in d['block_reasons'] and d['client']['enable'] is False
''')

once('tests/run-tests.sh',
"""python -m pytest tests/test_policy_consistency.py -q --junitxml=qa/junit/policy-consistency.xml
""",
"""python -m pytest tests/test_policy_consistency.py -q --junitxml=qa/junit/policy-consistency.xml
python -m pytest tests/test_reset_scheduler_v2.py -q --junitxml=qa/junit/reset-scheduler-v2.xml
""")
Path('VERSION').write_text('0.8.0-standalone-lab\n')
print('0.8.0 reset scheduler patch applied')
