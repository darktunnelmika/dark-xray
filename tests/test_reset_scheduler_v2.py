import calendar,datetime,time
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
