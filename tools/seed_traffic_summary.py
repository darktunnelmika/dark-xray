#!/usr/bin/env python3
"""Build accounting summaries once, then install with a short compare-and-swap.

The ledger is append-only. All expensive reads use a separate read-only WAL
snapshot; concurrent writes are caught up before a bounded final transaction.
No client, credit, usage or ledger record is changed.
"""
import argparse
import json
import os
from pathlib import Path
import sqlite3
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
import traffic_summary as summary


def fold(source, target, after=0):
    totals={};activity={};watermarks={};external=[];highwater=after;count=0
    source.execute('BEGIN')
    try:
        rows=source.execute('SELECT rowid,* FROM traffic_ledger WHERE rowid>? ORDER BY rowid',(after,))
        while batch:=rows.fetchmany(10000):
            for rowid,event,owner,client,period,up,down,seen in batch:
                highwater=rowid;count+=1
                key=(owner,period)
                value=totals.setdefault(key,[0,0,0,seen])
                value[0]+=up;value[1]+=down;value[2]+=1;value[3]=max(value[3],seen)
                activity[client]=max(activity.get(client,seen),seen)
                internal=summary.internal_key(event)
                if internal:
                    stream,seq=internal;watermarks[stream]=max(watermarks.get(stream,0),seq)
                else:external.append((event,owner,client,period,up,down,seen))
            if count and count%1000000==0:print(json.dumps({'scanned':count,'highwater':highwater}),flush=True)
            if external:
                target.executemany('INSERT INTO traffic_external_events VALUES(?,?,?,?,?,?,?)',external);external.clear()
        target.executemany('''INSERT INTO traffic_totals VALUES(?,?,?,?,?,?) ON CONFLICT(owner,period) DO UPDATE SET
        up_bytes=up_bytes+excluded.up_bytes,down_bytes=down_bytes+excluded.down_bytes,
        events=events+excluded.events,last_seen=MAX(last_seen,excluded.last_seen)''',
        [(owner,period,*values) for (owner,period),values in totals.items()])
        target.executemany('INSERT INTO traffic_activity VALUES(?,?) ON CONFLICT(client_id) DO UPDATE SET last_seen=MAX(last_seen,excluded.last_seen)',activity.items())
        target.executemany('INSERT INTO traffic_watermarks VALUES(?,?) ON CONFLICT(stream) DO UPDATE SET seq=MAX(seq,excluded.seq)',watermarks.items())
        target.execute('UPDATE traffic_summary_state SET highwater=? WHERE id=1',(highwater,));target.commit()
    finally:source.rollback()
    return highwater,count


def seed(database,stage,*,install=False):
    database,stage=Path(database),Path(stage)
    if database.is_symlink() or stage.exists() or stage.is_symlink():raise ValueError('Unsafe seed paths')
    fd=os.open(stage,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600);os.close(fd)
    src=sqlite3.connect(database.resolve().as_uri()+'?mode=ro',uri=True,timeout=2)
    dst=sqlite3.connect(stage)
    try:
        if summary.ready(src):raise ValueError('Summary is already initialized')
        summary.schema(dst);dst.commit()
        start=time.monotonic();highwater,count=fold(src,dst)
        print(json.dumps({'seed_rows':count,'highwater':highwater,'seconds':round(time.monotonic()-start,2)}),flush=True)
        if not install:return {'highwater':highwater,'rows':count,'installed':False}
        live=sqlite3.connect(database,timeout=1,isolation_level=None)
        try:
            live.execute('ATTACH DATABASE ? AS seed',(str(stage),))
            for attempt in range(100):
                highwater,added=fold(src,dst,highwater);count+=added
                try:live.execute('BEGIN IMMEDIATE')
                except sqlite3.OperationalError:
                    time.sleep(.05);continue
                locked=time.monotonic()
                live.set_progress_handler(lambda: int(time.monotonic()-locked>2),1000)
                try:
                    if summary.ready(live):raise ValueError('Another initializer already installed summary')
                    tip=live.execute('SELECT COALESCE(MAX(rowid),0) FROM traffic_ledger').fetchone()[0]
                    if tip!=highwater:
                        live.rollback();live.set_progress_handler(None,0);continue
                    summary.schema(live)
                    for table in summary.TABLES:
                        live.execute('DELETE FROM '+table)
                        live.execute('INSERT INTO '+table+' SELECT * FROM seed.'+table)
                    live.execute('UPDATE traffic_summary_state SET ready=1,highwater=? WHERE id=1',(highwater,))
                    summary.triggers(live)
                    live.commit()
                    result={'installed':True,'rows':count,'highwater':highwater,'write_lock_seconds':round(time.monotonic()-locked,3),'seconds':round(time.monotonic()-start,2)}
                    print(json.dumps(result),flush=True);return result
                except BaseException:live.rollback();raise
            raise RuntimeError('Busy ledger; no changes installed')
        finally:live.close()
    finally:src.close();dst.close()

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('database',type=Path);p.add_argument('stage',type=Path)
    p.add_argument('--install',action='store_true')
    args=p.parse_args();seed(args.database,args.stage,install=args.install)
