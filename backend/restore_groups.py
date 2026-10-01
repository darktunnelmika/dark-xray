"""Restore-only grouping and accounting; never charge native representative credit.

Legacy subscription counters are immutable migration metadata. DARK counters come
from locally persisted Xray deltas and authenticated Node mirror snapshots. Local
SQL triggers retain traffic across counter resets; the Node observer delegates all
native accounting to NodeRegistry unchanged, then records only Restore identities.
"""
from __future__ import annotations
import json
import secrets
import threading
import time
import unicodedata

from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field
from dark_policy import PolicyError

UNGROUPED = 'grp_ungrouped'
MAX_COUNTER = (1 << 63) - 1

class RestoreGroupBody(BaseModel):
    name: str = Field(min_length=1, max_length=80)

class RestoreGroupAssignment(BaseModel):
    groupId: str = Field(min_length=1, max_length=80)
    ids: list[str] = Field(min_length=1, max_length=2000)

class RestoreGroupsMixin:
    def _init_groups(self):
        self._import_lock = threading.RLock()
        now = time.time()
        with self.store.transaction() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS restore_groups(
                id TEXT PRIMARY KEY,name TEXT NOT NULL,name_key TEXT UNIQUE NOT NULL,
                created_at REAL NOT NULL,updated_at REAL NOT NULL)''')
            db.execute('INSERT OR IGNORE INTO restore_groups VALUES(?,?,?,?,?)',
                       (UNGROUPED, 'Ungrouped / previous imports', '__ungrouped__', now, now))
            cols = {r[1] for r in db.execute('PRAGMA table_info(restore_subscriptions)')}
            if 'group_id' not in cols:
                db.execute("ALTER TABLE restore_subscriptions ADD COLUMN group_id TEXT NOT NULL DEFAULT 'grp_ungrouped'")
            if 'promoted_owner' not in cols:
                db.execute("ALTER TABLE restore_subscriptions ADD COLUMN promoted_owner TEXT NOT NULL DEFAULT ''")
            if 'promoted_at' not in cols:
                db.execute("ALTER TABLE restore_subscriptions ADD COLUMN promoted_at REAL NOT NULL DEFAULT 0")
            db.execute('CREATE INDEX IF NOT EXISTS restore_by_group ON restore_subscriptions(group_id,created_at)')
            db.execute('''CREATE TABLE IF NOT EXISTS restore_usage(
                restore_id TEXT NOT NULL,scope TEXT NOT NULL,
                up INTEGER NOT NULL DEFAULT 0,down INTEGER NOT NULL DEFAULT 0,
                raw_up INTEGER NOT NULL DEFAULT 0,raw_down INTEGER NOT NULL DEFAULT 0,
                updated_at REAL NOT NULL DEFAULT 0,activity_at REAL NOT NULL DEFAULT 0,
                PRIMARY KEY(restore_id,scope),
                FOREIGN KEY(restore_id) REFERENCES restore_subscriptions(id) ON DELETE CASCADE)''')
            usage_cols={r[1] for r in db.execute('PRAGMA table_info(restore_usage)')}
            if 'activity_at' not in usage_cols:
                db.execute("ALTER TABLE restore_usage ADD COLUMN activity_at REAL NOT NULL DEFAULT 0")
                db.execute("UPDATE restore_usage SET activity_at=updated_at WHERE scope='local' AND up+down>0")
            # Existing Core counters were born in DARK, not copied from the old panel.
            # Seed once, retaining every existing ID/token/quota and every future total.
            db.execute('''INSERT OR IGNORE INTO restore_usage(restore_id,scope,up,down,raw_up,raw_down,updated_at)
                SELECT r.id,'local',c.up,c.down,c.up,c.down,? FROM restore_subscriptions r
                JOIN core_clients c ON c.email=r.core_email''', (now,))
            db.execute('DROP TRIGGER IF EXISTS restore_local_usage_v1')
            db.execute('''CREATE TRIGGER restore_local_usage_v1
                AFTER UPDATE OF up,down ON core_clients
                WHEN (NEW.up<>OLD.up OR NEW.down<>OLD.down) AND
                     EXISTS(SELECT 1 FROM restore_subscriptions WHERE core_email=NEW.email AND promoted_at=0)
                BEGIN
                    INSERT INTO restore_usage(restore_id,scope,up,down,raw_up,raw_down,updated_at,activity_at)
                    SELECT id,'local',
                        CASE WHEN NEW.up>=OLD.up THEN NEW.up-OLD.up ELSE NEW.up END,
                        CASE WHEN NEW.down>=OLD.down THEN NEW.down-OLD.down ELSE NEW.down END,
                        NEW.up,NEW.down,CAST(strftime('%s','now') AS REAL),CAST(strftime('%s','now') AS REAL)
                    FROM restore_subscriptions WHERE core_email=NEW.email AND promoted_at=0
                    ON CONFLICT(restore_id,scope) DO UPDATE SET
                        up=restore_usage.up+excluded.up,down=restore_usage.down+excluded.down,
                        raw_up=excluded.raw_up,raw_down=excluded.raw_down,updated_at=excluded.updated_at,
                        activity_at=excluded.activity_at;
                END''')
        self._observe_node_traffic()

    def _observe_node_traffic(self):
        # Feature-local observer: native permissions, baselines and ledger remain
        # owned by the original registry. Reinitialization never stacks wrappers.
        nodes = self.nodes
        if not hasattr(nodes, 'apply_traffic_snapshot'):
            return
        nodes._restore_traffic_observer = self
        if hasattr(nodes, '_restore_native_snapshot'):
            return
        nodes._restore_native_snapshot = nodes.apply_traffic_snapshot
        def observed(node_id, items, *, captured_at=None, initialize_absent=False):
            result = nodes._restore_native_snapshot(node_id, items, captured_at=captured_at,
                                                    initialize_absent=initialize_absent)
            result['restore_usage'] = nodes._restore_traffic_observer.record_node_usage(
                node_id, items, captured_at=captured_at)
            return result
        nodes.apply_traffic_snapshot = observed

    def record_node_usage(self, node_id, items, *, captured_at=None):
        now = time.time() if captured_at is None else float(captured_at)
        counted = 0
        with self.store.transaction() as db:
            assigned = {int(r[0]) for r in db.execute(
                'SELECT local_inbound_id FROM remote_node_inbounds WHERE node_id=?', (node_id,))}
            restored = {r['core_email']: r for r in db.execute(
                'SELECT id,core_email,inbound_ids FROM restore_subscriptions WHERE promoted_at=0')
                if assigned.intersection(json.loads(r['inbound_ids']))}
            seen = set()
            for item in items:
                email = item.get('sourceEmail')
                if email not in restored:
                    continue
                up, down = item.get('up'), item.get('down')
                if email in seen or any(type(x) is not int or x < 0 or x > MAX_COUNTER for x in (up, down)) or up + down > MAX_COUNTER:
                    raise PolicyError('Invalid Restore Node traffic snapshot')
                seen.add(email)
                rid = restored[email]['id']; scope = 'node:' + str(node_id)
                old = db.execute('SELECT * FROM restore_usage WHERE restore_id=? AND scope=?', (rid, scope)).fetchone()
                if old and now < float(old['updated_at']):
                    continue
                # Each restore core_email is new and exclusive to DARK. The first
                # authenticated cumulative sample already belongs to this migration.
                du = up if not old or up < old['raw_up'] else up - old['raw_up']
                dd = down if not old or down < old['raw_down'] else down - old['raw_down']
                total_up = (int(old['up']) if old else 0) + du
                total_down = (int(old['down']) if old else 0) + dd
                if total_up + total_down > MAX_COUNTER:
                    raise PolicyError('Restore traffic counter overflow')
                # Record the watermark even without new bytes, so a delayed older
                # sample cannot later be mistaken for a counter reset.
                activity=now if du+dd>0 else float(old['activity_at'] or 0) if old else 0
                db.execute('''INSERT INTO restore_usage(restore_id,scope,up,down,raw_up,raw_down,updated_at,activity_at)
                    VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(restore_id,scope) DO UPDATE SET up=excluded.up,down=excluded.down,
                    raw_up=excluded.raw_up,raw_down=excluded.raw_down,updated_at=excluded.updated_at,
                    activity_at=excluded.activity_at''',
                    (rid, scope, total_up, total_down, up, down, now, activity))
                counted += 1
        return {'clients': counted}

    @staticmethod
    def _group_name(value):
        name = ' '.join(unicodedata.normalize('NFKC', str(value)).split())
        if not name or len(name) > 80 or any(unicodedata.category(c).startswith('C') and c!='\u200c' for c in name):
            raise PolicyError('Group name must contain 1..80 visible characters')
        return name, name.casefold()

    def save_group(self, name, group_id=None):
        name, key = self._group_name(name); now = time.time()
        with self.store.transaction() as db:
            duplicate = db.execute('SELECT * FROM restore_groups WHERE name_key=?', (key,)).fetchone()
            if group_id:
                if group_id == UNGROUPED:
                    raise PolicyError('The previous-imports group cannot be renamed')
                if not db.execute('SELECT 1 FROM restore_groups WHERE id=?', (group_id,)).fetchone():
                    raise HTTPException(404, 'Restore group not found')
                if duplicate and duplicate['id'] != group_id:
                    raise PolicyError('Another Restore group already has this name', status=409)
                db.execute('UPDATE restore_groups SET name=?,name_key=?,updated_at=? WHERE id=?', (name, key, now, group_id))
            elif duplicate:
                return dict(duplicate)
            else:
                group_id = 'grp_' + secrets.token_hex(12)
                db.execute('INSERT INTO restore_groups VALUES(?,?,?,?,?)', (group_id, name, key, now, now))
            return dict(db.execute('SELECT * FROM restore_groups WHERE id=?', (group_id,)).fetchone())

    def _require_group(self, group_id):
        with self.store.lock:
            row = self.store.db.execute('SELECT * FROM restore_groups WHERE id=?', (group_id,)).fetchone()
        if not row:
            raise HTTPException(404, 'Restore group not found')
        return dict(row)

    def assign_group(self, ids, group_id):
        self._require_group(group_id)
        ids = list(dict.fromkeys(ids))
        if not ids or len(ids) > 2000:
            raise PolicyError('Select 1..2000 Restore users')
        marks = ','.join('?' for _ in ids); now = time.time()
        with self.store.transaction() as db:
            rows = list(db.execute('SELECT id,group_id FROM restore_subscriptions WHERE id IN (' + marks + ')', ids))
            if len(rows) != len(ids):
                raise HTTPException(404, 'A selected Restore user no longer exists')
            db.execute('UPDATE restore_subscriptions SET group_id=?,updated_at=? WHERE id IN (' + marks + ')', (group_id, now, *ids))
            db.executemany('INSERT INTO restore_events(restore_id,event,detail,at) VALUES(?,?,?,?)',
                           [(r['id'], 'group.changed', json.dumps({'from':r['group_id'],'to':group_id}), now) for r in rows if r['group_id'] != group_id])
        return {'updated': len(rows), 'group_id': group_id}

    def usage(self, restore_id):
        with self.store.lock:
            r = self.store.db.execute('SELECT COALESCE(SUM(up),0) up,COALESCE(SUM(down),0) down FROM restore_usage WHERE restore_id=?', (restore_id,)).fetchone()
        return dict(r)

    def rows(self, group_id=None):
        if group_id:
            self._require_group(group_id)
        where = ' WHERE r.group_id=?' if group_id else ''
        with self.store.lock:
            rows = [dict(r) for r in self.store.db.execute('''SELECT r.*,g.name group_name,
                COALESCE(u.dark_up,0) dark_up,COALESCE(u.dark_down,0) dark_down,
                COALESCE(u.local_used,0) local_used,COALESCE(u.node_used,0) node_used,
                COALESCE(u.activity_at,0) activity_at
                FROM restore_subscriptions r JOIN restore_groups g ON g.id=r.group_id
                LEFT JOIN (SELECT restore_id,SUM(up) dark_up,SUM(down) dark_down,
                    SUM(CASE WHEN scope='local' THEN up+down ELSE 0 END) local_used,
                    SUM(CASE WHEN scope<>'local' THEN up+down ELSE 0 END) node_used,
                    MAX(activity_at) activity_at
                    FROM restore_usage GROUP BY restore_id) u ON u.restore_id=r.id''' + where +
                ' ORDER BY r.created_at DESC,r.id', (group_id,) if group_id else ())]
        for r in rows:
            r['inbound_ids'] = json.loads(r['inbound_ids']); r['node_ids'] = json.loads(r['node_ids'])
            r['enabled'] = bool(r['enabled'])
            r['dark_used'] = int(r['dark_up']) + int(r['dark_down'])
            r['legacy_used'] = int(r['legacy_upload']) + int(r['legacy_download'])
            r['effective_used'] = r['legacy_used'] + r['dark_used']  # quota compatibility, not display usage
            r['remaining'] = max(0, int(r['legacy_total']) - r['effective_used']) if int(r['legacy_total']) else 0
            r['usage_since'] = float(r['first_seen'] or r['created_at'])
            activity=float(r.get('activity_at') or 0);age=max(0,int(time.time()-activity)) if activity else None
            r['presence_state']='online' if age is not None and age<=60 else 'idle' if age is not None and age<=300 else 'offline'
            r['presence_age_seconds']=age
            r['plan_type']='unlimited' if int(r['legacy_total'])==0 else 'limited'
            r['promoted']=float(r.get('promoted_at') or 0)>0
        return rows

    def groups(self):
        rows = self.rows()
        with self.store.lock:
            groups = [dict(r) for r in self.store.db.execute('SELECT id,name,created_at,updated_at FROM restore_groups ORDER BY created_at,id')]
        by_id = {g['id']: g for g in groups}
        for g in groups:
            g.update(clients=0, migrated=0, dark_up=0, dark_down=0, dark_used=0, local_used=0, node_used=0,
                     limited=0,unlimited=0,promoted=0,online=0,idle=0,offline=0)
            g['unassigned'] = g['id'] == UNGROUPED
        for r in rows:
            g = by_id[r['group_id']]; g['clients'] += 1; g['migrated'] += int(r['first_seen'] > 0)
            g[r['plan_type']] += 1;g['promoted'] += int(r['promoted']);g[r['presence_state']] += 1
            for key in ('dark_up','dark_down','dark_used','local_used','node_used'):
                g[key] += int(r[key])
        # Surface actual runtime target health on each group card. Mixed groups
        # report the union of their active mapping variants rather than hiding
        # destinations just because users differ.
        for g in groups:
            g.update(target_total=0,target_ready=0,target_pending=0,target_mixed=False)
            try:
                state=self.group_targets(g['id'])
                variants=state.get('variants') or []
                selections=[]
                if state.get('mapping'):selections=[state['mapping']]
                elif variants:selections=[{k:v for k,v in x.items() if k!='clients'} for x in variants]
                elif state.get('defaultMapping'):selections=[state['defaultMapping']]
                seen={}
                for value in selections:
                    for target in self.resolved_targets(value):
                        key=(target['runtime'],int(target['inboundId']))
                        current=seen.get(key)
                        seen[key]=bool(target.get('ready')) if current is None else bool(current or target.get('ready'))
                g['target_total']=len(seen);g['target_ready']=sum(1 for ok in seen.values() if ok)
                g['target_pending']=g['target_total']-g['target_ready'];g['target_mixed']=bool(state.get('mixed'))
            except Exception:
                # Group listing must remain available even if one target mapping
                # needs repair; the detailed mapping page will expose that error.
                g['target_pending']=g['target_total']
        return groups

    def import_urls(self, urls, inbounds, nodes, scan, *, group_id='', group_name='', node_mode=None, include_local=None):
        self._validate_targets(inbounds, nodes)
        self.validate_selection({'inboundIds': inbounds, 'nodeIds': nodes,
            'nodeMode': node_mode or ('selected' if nodes else 'all'),
            'includeLocal': True if include_local is None else include_local})
        if group_id and group_name.strip():
            raise PolicyError('Choose an existing group OR a new group name')
        # Validate the entire batch before creating a group or mutating a client.
        parsed = [self._safe_url(raw) for raw in urls]
        unique = {(host,path,query):(u,host,path,query) for u,host,path,query in parsed}
        with self._import_lock:
            group = self._require_group(group_id) if group_id else self.save_group(group_name) if group_name.strip() else None
            created = updated = 0; items = []; conflicts = []; now = time.time()
            for u,host,path,query in unique.values():
                legacy = u.geturl()
                with self.store.lock:
                    old = self.store.db.execute('SELECT * FROM restore_subscriptions WHERE legacy_host=? AND legacy_path=? AND legacy_query=?', (host,path,query)).fetchone()
                if old and float(old['promoted_at'] or 0)>0:
                    conflicts.append({'id':old['id'],'group_id':old['group_id'],'reason':'promoted_to_native'})
                    continue
                if old and group and old['group_id'] != group['id']:
                    conflicts.append({'id':old['id'],'group_id':old['group_id'],'reason':'already_in_another_group'})
                    continue
                # Re-scanning a taken-over URL would import our own usage again.
                # Preserve verified metadata and every migrated account's baseline.
                frozen = old and (old['scan_status'] == 'verified' or old['first_seen'] or sum(self.usage(old['id']).values()))
                probe = None if frozen or (old and not scan) else self._scan(legacy) if scan else {
                    'status':'pending','error':'','upload':0,'download':0,'total':0,'expire':0}
                refresh_metadata = probe is not None and (not old or probe['status'] == 'verified')
                if not old and group is None:
                    group = self.save_group('Migration ' + time.strftime('%Y-%m-%d %H:%M:%S',time.gmtime()) + ' ' + secrets.token_hex(2))
                gid = old['group_id'] if old else group['id']
                self.engine._write()
                with self.engine.lock, self.store.transaction() as db:
                    if old:
                        rid = old['id']; core_email = old['core_email']
                        core = db.execute('SELECT body FROM core_clients WHERE email=?', (core_email,)).fetchone()
                        if not core:
                            raise PolicyError('Restore core identity missing; automatic credential replacement refused', status=409)
                        body = json.loads(core['body'])
                        if refresh_metadata:
                            db.execute('''UPDATE restore_subscriptions SET legacy_upload=?,legacy_download=?,legacy_total=?,legacy_expire=?,scan_status=?,scan_error=? WHERE id=?''',
                                (probe['upload'],probe['download'],probe['total'],probe['expire'],probe['status'],probe['error'],rid))
                            body['totalGB'] = int(probe['total']); body['expiryTime'] = int(probe['expire'])*1000
                        db.execute('UPDATE restore_subscriptions SET legacy_url=?,inbound_ids=?,node_ids=?,updated_at=? WHERE id=?', (legacy,json.dumps(inbounds),json.dumps(nodes),now,rid))
                        db.execute('UPDATE core_clients SET body=?,inbounds=? WHERE email=?', (json.dumps(body),json.dumps(inbounds),core_email))
                        updated += 1
                    else:
                        rid='rst_'+secrets.token_hex(12); token=secrets.token_urlsafe(24); core_email='restore_'+secrets.token_hex(10)+'@dark.restore'
                        body=self._client_body(core_email,inbounds,probe['total'],probe['expire'])
                        db.execute('INSERT INTO core_clients(email,body,inbounds) VALUES(?,?,?)',(core_email,json.dumps(body),json.dumps(inbounds)))
                        db.execute('''INSERT INTO restore_subscriptions(id,public_token,legacy_url,legacy_host,legacy_path,legacy_query,core_email,
                            inbound_ids,node_ids,legacy_upload,legacy_download,legacy_total,legacy_expire,scan_status,scan_error,enabled,created_at,updated_at,group_id)
                            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                            (rid,token,legacy,host,path,query,core_email,json.dumps(inbounds),json.dumps(nodes),probe['upload'],probe['download'],probe['total'],probe['expire'],probe['status'],probe['error'],1,now,now,gid))
                        db.execute('INSERT INTO restore_usage(restore_id,scope,updated_at) VALUES(?,?,?)',(rid,'local',now))
                        created += 1
                    self._store_import_targets(db, rid, old, nodes, node_mode, include_local)
                self.ensure_domain(host)
                items.append({'id':rid,'group_id':gid,'scan_status':probe['status'] if refresh_metadata else old['scan_status']})
            applied = True; apply_error = ''
            if created or updated:
                try:self.engine.apply(start=self.engine.running)
                except Exception as ex:applied=False;apply_error=str(ex)[:500]
            return {'created':created,'updated':updated,'items':items,'group':group,
                    'conflicts':conflicts,'duplicates':len(parsed)-len(unique),'applied':applied,'apply_error':apply_error}

    def install_group_routes(self, app, owner, writable, audit):
        @app.post('/api/dark-restore/groups')
        def create_group(body: RestoreGroupBody, p=Depends(owner)):
            writable(); result=self.save_group(body.name)
            audit(p.actor,p.actor.id,'dark_restore.group.create',result['id'],result['name'])
            return result

        @app.put('/api/dark-restore/groups/{group_id}')
        def rename_group(group_id: str, body: RestoreGroupBody, p=Depends(owner)):
            writable(); result=self.save_group(body.name,group_id)
            audit(p.actor,p.actor.id,'dark_restore.group.rename',group_id,result['name'])
            return result

        @app.post('/api/dark-restore/groups/assign')
        def assign_group(body: RestoreGroupAssignment, p=Depends(owner)):
            writable(); result=self.assign_group(body.ids,body.groupId)
            audit(p.actor,p.actor.id,'dark_restore.group.assign',body.groupId,str(result['updated']))
            return result
