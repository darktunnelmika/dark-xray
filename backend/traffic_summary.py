"""Small transactional accounting read model and operational backup snapshots.

Detailed traffic stays in the source ledger. A compact recovery carries totals,
activity and replay watermarks, plus every non-internal idempotency event.
"""
from __future__ import annotations
import re
import sqlite3
import os
import time
from pathlib import Path

INTERNAL=re.compile(r'^(engine:[0-9a-f]{20}|node:[0-9a-f]{24}):([1-9][0-9]{0,18})$')
MAX_INT=(1<<63)-1
DDL=[
'''CREATE TABLE IF NOT EXISTS traffic_summary_state(id INTEGER PRIMARY KEY CHECK(id=1),ready INTEGER NOT NULL DEFAULT 0,highwater INTEGER NOT NULL DEFAULT 0,archived INTEGER NOT NULL DEFAULT 0)''',
'''CREATE TABLE IF NOT EXISTS traffic_totals(owner TEXT NOT NULL,period INTEGER NOT NULL,up_bytes INTEGER NOT NULL CHECK(typeof(up_bytes)='integer' AND up_bytes>=0),down_bytes INTEGER NOT NULL CHECK(typeof(down_bytes)='integer' AND down_bytes>=0),events INTEGER NOT NULL,last_seen REAL NOT NULL,PRIMARY KEY(owner,period))''',
'''CREATE TABLE IF NOT EXISTS traffic_activity(client_id TEXT PRIMARY KEY,last_seen REAL NOT NULL)''',
'''CREATE TABLE IF NOT EXISTS traffic_watermarks(stream TEXT PRIMARY KEY,seq INTEGER NOT NULL)''',
'''CREATE TABLE IF NOT EXISTS traffic_external_events(event_id TEXT PRIMARY KEY,owner TEXT NOT NULL,client_id TEXT NOT NULL,period INTEGER NOT NULL,up_bytes INTEGER NOT NULL,down_bytes INTEGER NOT NULL,observed_at REAL NOT NULL)'''
]
TABLES=['traffic_totals','traffic_activity','traffic_watermarks','traffic_external_events']

def internal_key(event):
    m=INTERNAL.fullmatch(event)
    if m and int(m[2])<=MAX_INT:return m[1],int(m[2])
    return None


def schema(db):
    for sql in DDL:db.execute(sql)
    db.execute('INSERT OR IGNORE INTO traffic_summary_state(id) VALUES(1)')


def ready(db):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='traffic_summary_state' AND type='table'").fetchone():return False
    row=db.execute('SELECT ready FROM traffic_summary_state WHERE id=1').fetchone()
    return bool(row and row[0])


def triggers(db):
    # Match Python's canonical internal IDs, never a loose prefix heuristic.
    seq="CASE WHEN substr(NEW.event_id,1,7)='engine:' THEN substr(NEW.event_id,29) ELSE substr(NEW.event_id,31) END"
    stream="CASE WHEN substr(NEW.event_id,1,7)='engine:' THEN substr(NEW.event_id,1,27) ELSE substr(NEW.event_id,1,29) END"
    shape="((substr(NEW.event_id,1,7)='engine:' AND substr(NEW.event_id,28,1)=':' AND substr(NEW.event_id,8,20) NOT GLOB '*[^0-9a-f]*') OR (substr(NEW.event_id,1,5)='node:' AND substr(NEW.event_id,30,1)=':' AND substr(NEW.event_id,6,24) NOT GLOB '*[^0-9a-f]*'))"
    valid=f"({shape} AND CAST({seq} AS INTEGER)>0 AND CAST(CAST({seq} AS INTEGER) AS TEXT)={seq})"
    db.execute(f'''CREATE TRIGGER IF NOT EXISTS traffic_summary_replay BEFORE INSERT ON traffic_ledger
    WHEN (SELECT ready FROM traffic_summary_state WHERE id=1)=1 AND {valid}
      AND CAST({seq} AS INTEGER)<=COALESCE((SELECT seq FROM traffic_watermarks WHERE stream={stream}),0)
    BEGIN SELECT RAISE(ABORT,'Archived traffic sequence replay'); END''')
    db.execute(f'''CREATE TRIGGER IF NOT EXISTS traffic_summary_insert AFTER INSERT ON traffic_ledger
    WHEN (SELECT ready FROM traffic_summary_state WHERE id=1)=1
    BEGIN
      INSERT INTO traffic_totals VALUES(NEW.owner,NEW.period,NEW.up_bytes,NEW.down_bytes,1,NEW.observed_at)
      ON CONFLICT(owner,period) DO UPDATE SET up_bytes=up_bytes+NEW.up_bytes,down_bytes=down_bytes+NEW.down_bytes,events=events+1,last_seen=MAX(last_seen,NEW.observed_at);
      INSERT INTO traffic_activity VALUES(NEW.client_id,NEW.observed_at)
      ON CONFLICT(client_id) DO UPDATE SET last_seen=MAX(last_seen,NEW.observed_at);
      INSERT INTO traffic_watermarks SELECT {stream},CAST({seq} AS INTEGER) WHERE {valid}
      ON CONFLICT(stream) DO UPDATE SET seq=MAX(seq,excluded.seq);
      INSERT INTO traffic_external_events SELECT NEW.event_id,NEW.owner,NEW.client_id,NEW.period,NEW.up_bytes,NEW.down_bytes,NEW.observed_at WHERE NOT {valid};
      UPDATE traffic_summary_state SET highwater=MAX(highwater,NEW.rowid) WHERE id=1;
    END''')


def ensure(db):
    schema(db)
    if not ready(db) and not db.execute('SELECT 1 FROM traffic_ledger LIMIT 1').fetchone():
        db.execute('UPDATE traffic_summary_state SET ready=1 WHERE id=1')
    triggers(db)


def activity(db,email):
    if ready(db):
        row=db.execute('SELECT last_seen FROM traffic_activity WHERE client_id=?',(email,)).fetchone()
    else:row=db.execute('SELECT MAX(observed_at) FROM traffic_ledger WHERE client_id=?',(email,)).fetchone()
    return row[0] if row else None


def previous_event(db,event_id):
    if not ready(db):return None
    row=db.execute('SELECT * FROM traffic_external_events WHERE event_id=?',(event_id,)).fetchone()
    if row:return row
    key=internal_key(event_id)
    if key:
        row=db.execute('SELECT seq FROM traffic_watermarks WHERE stream=?',(key[0],)).fetchone()
        if row and key[1]<=row[0]:
            from dark_policy import PolicyError
            raise PolicyError('Archived internal usage event cannot be replayed')
    return None


def operational_snapshot(source_path,destination):
    """Copy operational tables under one short WAL snapshot; never scan history."""
    from dark_policy import PolicyError
    source_path,destination=Path(source_path),Path(destination)
    if source_path.is_symlink() or destination.exists() or destination.is_symlink():raise PolicyError('Unsafe snapshot paths')
    src=sqlite3.connect(source_path.resolve().as_uri()+'?mode=ro',uri=True,timeout=10)
    dst=None
    deadline=time.monotonic()+60
    src.set_progress_handler(lambda: int(time.monotonic()>deadline),1000)
    try:
        src.execute('BEGIN')
        definitions=src.execute("SELECT type,name,sql FROM sqlite_master WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%' ORDER BY rowid").fetchall()
        has_ledger=any(n=='traffic_ledger' for _,n,_ in definitions)
        summarized=ready(src)
        if has_ledger and not summarized:raise PolicyError('Accounting summary must be initialized before operational backup')
        fd=os.open(destination,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600);os.close(fd)
        dst=sqlite3.connect(destination)
        dst.set_progress_handler(lambda: int(time.monotonic()>deadline),1000)
        dst.execute('PRAGMA journal_mode=OFF');dst.execute('PRAGMA synchronous=OFF')
        for kind,name,sql in definitions:
            if kind=='table':dst.execute(sql)
        total_rows=0
        for kind,name,sql in definitions:
            if kind!='table':continue
            quoted='"'+name.replace('"','""')+'"'
            if name=='traffic_ledger':
                columns=[r[1] for r in src.execute('PRAGMA table_info(traffic_ledger)')]
                query='SELECT rowid,* FROM traffic_ledger ORDER BY rowid DESC LIMIT 250'
                targets='(rowid,'+','.join('"'+x.replace('"','""')+'"' for x in columns)+')'
            else:query='SELECT * FROM '+quoted;targets=''
            rows=src.execute(query)
            while batch:=rows.fetchmany(500):
                dst.executemany('INSERT INTO '+quoted+targets+' VALUES('+','.join('?' for _ in batch[0])+')',batch)
                total_rows+=len(batch)
                if time.monotonic()>deadline:raise PolicyError('Operational backup exceeded time budget')
        if src.execute("SELECT 1 FROM sqlite_master WHERE name='sqlite_sequence'").fetchone():
            dst.execute('DELETE FROM sqlite_sequence')
            dst.executemany('INSERT INTO sqlite_sequence VALUES(?,?)',src.execute('SELECT * FROM sqlite_sequence'))
        for kind,name,sql in definitions:
            if kind in ('index','view','trigger'):dst.execute(sql)
        if summarized:
            dst.execute('UPDATE traffic_summary_state SET archived=1 WHERE id=1')
            dst.execute('PRAGMA user_version=5')
        else:dst.execute('PRAGMA user_version='+str(src.execute('PRAGMA user_version').fetchone()[0]))
        dst.commit()
        result={'profile':'operational','history_included':False,'recent_traffic_events':min(250,src.execute('SELECT COUNT(*) FROM (SELECT 1 FROM traffic_ledger LIMIT 250)').fetchone()[0]) if has_ledger else 0,
                'accounting_totals_preserved':summarized,'copied_rows':total_rows,'requires_summary_aware_restore':summarized}
        return result
    finally:
        if dst:dst.close()
        src.close()
