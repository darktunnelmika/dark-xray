"""Restore target selection and group edits, independent of native client policy.

The render view reuses CoreEngine's protocol/format generators with a private
host list. It never changes CoreEngine, persisted Hosts, credentials or routing.
"""
from __future__ import annotations
import copy
import hashlib
import json
import os
import time
from typing import Literal

from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field
from core import CoreEngine
from dark_policy import PolicyError


class RestoreTargetsBody(BaseModel):
    inboundIds: list[int] = Field(min_length=1, max_length=256)
    nodeIds: list[str] = Field(default_factory=list, max_length=256)
    nodeMode: Literal['all', 'selected'] = 'all'
    includeLocal: bool = True


class RestoreGroupTargetsBody(RestoreTargetsBody):
    expectedRevision: str = Field(min_length=64, max_length=64, pattern=r'^[0-9a-f]{64}$')


class _RestoreRenderView:
    """Request-local host defaults; all URI and format logic stays in CoreEngine."""
    links = CoreEngine.links
    subscription = CoreEngine.subscription

    def __init__(self, engine, hosts):
        self._engine, self._hosts = engine, hosts

    def __getattr__(self, name):
        return getattr(self._engine, name)

    def section(self, name):
        return self._hosts if name == 'hosts' else self._engine.section(name)


class RestoreTargetsMixin:
    def _init_targets(self):
        # Do not silently widen old explicitly saved mappings during an upgrade.
        with self.store.transaction() as db:
            cols = {r[1] for r in db.execute('PRAGMA table_info(restore_subscriptions)')}
            if 'node_mode' not in cols:
                db.execute("ALTER TABLE restore_subscriptions ADD COLUMN node_mode TEXT NOT NULL DEFAULT 'selected'")
            if 'include_local' not in cols:
                db.execute('ALTER TABLE restore_subscriptions ADD COLUMN include_local INTEGER NOT NULL DEFAULT 1')
            db.execute('''CREATE TABLE IF NOT EXISTS restore_group_targets(
                group_id TEXT PRIMARY KEY,body TEXT NOT NULL,updated_at REAL NOT NULL,
                FOREIGN KEY(group_id) REFERENCES restore_groups(id) ON DELETE CASCADE)''')

    @staticmethod
    def target_selection(row):
        return {'inboundIds': sorted(json.loads(row['inbound_ids'])),
                'nodeIds': sorted(json.loads(row['node_ids'])),
                'nodeMode': row['node_mode'], 'includeLocal': bool(row['include_local'])}

    def validate_selection(self, value):
        ids, nodes = value['inboundIds'], value['nodeIds']
        mode, local = value['nodeMode'], value['includeLocal']
        self._validate_targets(ids, nodes)
        if mode not in ('all', 'selected') or type(local) is not bool:
            raise PolicyError('Invalid Restore runtime selection')
        if len(set(nodes)) != len(nodes):
            raise PolicyError('Duplicate Restore Node selection')
        if mode == 'all' and nodes:
            raise PolicyError('Whole inbound mode must not also specify a Node subset')
        if mode == 'selected' and not local and not nodes:
            raise PolicyError('Select the Hub or at least one Node')
        known = {str(n['id']): n for n in self.nodes.list()}
        for node_id in nodes:
            assigned = {int(a['local_inbound_id']) for a in known[node_id].get('assignments', [])}
            if not assigned.intersection(ids):
                raise PolicyError('Selected Node is not assigned to any selected Inbound: ' + node_id)
        return {'inboundIds': sorted(ids), 'nodeIds': sorted(nodes), 'nodeMode': mode, 'includeLocal': local}

    def _store_import_targets(self, db, rid, old, nodes, node_mode, include_local):
        mode = node_mode if node_mode is not None else old['node_mode'] if old else ('selected' if nodes else 'all')
        local = include_local if include_local is not None else bool(old['include_local']) if old else True
        db.execute('UPDATE restore_subscriptions SET node_mode=?,include_local=? WHERE id=?', (mode, int(local), rid))

    def target_catalog(self):
        inbounds = self.engine.inbounds()
        nodes = self.nodes.list()
        return {'inbounds': [{'id': int(i['id']), 'name': i.get('remark') or i.get('tag'),
                    'port': i['port'], 'enabled': bool(i.get('enable', True)),
                    'local': i.get('panelMeta', {}).get('deployLocal', True) is not False} for i in inbounds],
                'nodes': [{'id': n['id'], 'name': n['name'], 'address': n.get('data_address', ''),
                    'online': bool(n.get('online')), 'enabled': bool(n.get('enabled')),
                    'inboundIds': [int(a['local_inbound_id']) for a in n.get('assignments', [])],
                    'readyInboundIds': [int(a['local_inbound_id']) for a in n.get('assignments', [])
                        if n.get('online') and n.get('enabled') and not n.get('last_error') and a.get('deployed') and not a.get('last_error')]}
                    for n in nodes],
                'hub': {'name': os.environ.get('DARK_HUB_NAME', 'HUB'), 'address': self.engine.config.public_address}}

    def resolved_targets(self, selection, catalog=None):
        catalog = catalog or self.target_catalog(); rows = []
        ids = set(selection['inboundIds']); chosen = set(selection['nodeIds'])
        for inbound in catalog['inbounds']:
            iid = inbound['id']
            if iid not in ids:
                continue
            if selection['includeLocal'] and inbound['local']:
                rows.append({'inboundId': iid, 'runtime': 'local', 'name': catalog['hub']['name'],
                             'address': catalog['hub']['address'], 'ready': inbound['enabled']})
            for node in catalog['nodes']:
                if iid not in node['inboundIds'] or (selection['nodeMode'] == 'selected' and node['id'] not in chosen):
                    continue
                rows.append({'inboundId': iid, 'runtime': 'node:' + node['id'], 'name': node['name'],
                             'address': node['address'], 'ready': inbound['enabled'] and iid in node['readyInboundIds']})
        return rows

    def runtime_ready_selection(self, selection):
        targets = self.resolved_targets(selection)
        ready = {'local': set()}
        for t in targets:
            if t['ready'] and t['address']:
                ready.setdefault(t['runtime'], set()).add(t['inboundId'])
        return ready, targets

    def render_view(self, selection, fmt):
        ready, targets = self.runtime_ready_selection(selection)
        hosts = copy.deepcopy(self.engine.section('hosts'))
        host_format = 'raw' if fmt == 'base64' else fmt
        for target in targets:
            iid, runtime = target['inboundId'], target['runtime']
            if iid not in ready.get(runtime, set()):
                continue
            # Explicit eligible Direct satisfies this runtime exactly once. The
            # base generator deduplicates Direct per inbound/runtime, never by IP.
            explicit = any(h.get('inboundId') == iid and (h.get('runtime') or 'local') == runtime
                           and h.get('enable', True) and (h.get('endpointType') or 'direct') != 'tunnel'
                           and host_format not in h.get('excludeFromSubTypes', []) for h in hosts)
            if explicit:
                continue
            inbound = self.engine.inbound(iid)
            hosts.append({'inboundId': iid, 'runtime': runtime, 'endpointType': 'direct',
                          'address': target['address'], 'port': inbound['port'], 'enable': True,
                          'remark': str(inbound.get('remark') or inbound['tag']) + ' · ' + str(target['name']),
                          'security': 'same'})
        return _RestoreRenderView(self.engine, hosts), ready

    def _group_target_state(self, group_id, db):
        rows = list(db.execute('''SELECT r.id,r.core_email,r.inbound_ids,r.node_ids,r.node_mode,r.include_local,
            c.inbounds core_inbounds FROM restore_subscriptions r LEFT JOIN core_clients c ON c.email=r.core_email
            WHERE r.group_id=? AND r.promoted_at=0 AND r.deleted_at=0 ORDER BY r.id''', (group_id,)))
        default = db.execute('SELECT body FROM restore_group_targets WHERE group_id=?', (group_id,)).fetchone()
        revision = hashlib.sha256(json.dumps([group_id, [tuple(r) for r in rows], default[0] if default else None],
                                            separators=(',', ':')).encode()).hexdigest()
        return rows, default, revision

    def group_targets(self, group_id):
        group = self._require_group(group_id)
        with self.store.lock:
            rows, default, revision = self._group_target_state(group_id, self.store.db)
        variants = {}
        for row in rows:
            value = self.target_selection(row); key = json.dumps(value, sort_keys=True)
            variants.setdefault(key, {**value, 'clients': 0})['clients'] += 1
        values = list(variants.values())
        current = {k: v for k, v in values[0].items() if k != 'clients'} if len(values) == 1 else None
        return {'groupId': group_id, 'name': group['name'], 'clients': len(rows), 'revision': revision,
                'mixed': len(values) > 1, 'variants': values, 'mapping': current,
                'defaultMapping': json.loads(default[0]) if default else None,
                'targets': self.resolved_targets(current) if current else [], 'catalog': self.target_catalog()}

    def preview_group_targets(self, group_id, selection):
        value = self.validate_selection(selection); state = self.group_targets(group_id)
        return {'groupId': group_id, 'name': state['name'], 'clients': state['clients'], 'revision': state['revision'],
                'mapping': value, 'targets': self.resolved_targets(value), 'previousMixed': state['mixed']}

    def set_targets(self, selection, *, group_id=None, restore_id=None, expected_revision=None):
        value = self.validate_selection(selection)
        if bool(group_id) == bool(restore_id):
            raise PolicyError('Select one Restore group or user')
        if group_id:
            self._require_group(group_id)
        self.engine._write(); changed = 0; core_changed = False; now = time.time()
        with self._import_lock, self.engine.lock, self.store.transaction() as db:
            if group_id:
                rows, _, revision = self._group_target_state(group_id, db)
                if expected_revision != revision:
                    raise HTTPException(409, 'Group membership or mapping changed. Preview again before applying.')
            else:
                rows = list(db.execute('''SELECT r.*,c.inbounds core_inbounds FROM restore_subscriptions r
                    LEFT JOIN core_clients c ON c.email=r.core_email WHERE r.id=? AND r.deleted_at=0''', (restore_id,)))
                if not rows:
                    raise HTTPException(404, 'Restore user not found')
                if float(rows[0]['promoted_at'] or 0)>0:
                    raise HTTPException(409, 'Promoted Restore users are managed from native Clients')
            if any(r['core_inbounds'] is None for r in rows):
                raise HTTPException(409, 'Restore identity missing. Nothing was changed; automatic credential replacement refused.')
            for row in rows:
                same_core = sorted(json.loads(row['core_inbounds'])) == value['inboundIds']
                if self.target_selection(row) == value and same_core:
                    continue
                db.execute('''UPDATE restore_subscriptions SET inbound_ids=?,node_ids=?,node_mode=?,include_local=?,updated_at=? WHERE id=?''',
                    (json.dumps(value['inboundIds']), json.dumps(value['nodeIds']), value['nodeMode'], int(value['includeLocal']), now, row['id']))
                if not same_core:
                    db.execute('UPDATE core_clients SET inbounds=? WHERE email=?', (json.dumps(value['inboundIds']), row['core_email']))
                    core_changed = True
                db.execute('INSERT INTO restore_events(restore_id,event,detail,at) VALUES(?,?,?,?)',
                           (row['id'], 'mapping.changed', json.dumps(value), now)); changed += 1
            if group_id:
                db.execute('''INSERT INTO restore_group_targets VALUES(?,?,?) ON CONFLICT(group_id)
                    DO UPDATE SET body=excluded.body,updated_at=excluded.updated_at''', (group_id, json.dumps(value), now))
        applied = True; error = ''
        if core_changed:
            try:
                self.engine.apply(start=self.engine.running)
            except Exception as ex:
                applied = False; error = str(ex)[:500]
        return {'updated': changed, 'clients': len(rows), 'mapping': value, 'applied': applied, 'apply_error': error,
                'core_changed': core_changed, 'subscription_update_required': bool(changed),
                'targets': self.resolved_targets(value)}

    def install_target_routes(self, app, owner, writable, audit):
        @app.get('/api/dark-restore/targets')
        def catalog(p=Depends(owner)):
            return self.target_catalog()

        @app.get('/api/dark-restore/groups/{group_id}/mapping')
        def group_mapping(group_id: str, p=Depends(owner)):
            return self.group_targets(group_id)

        @app.post('/api/dark-restore/groups/{group_id}/mapping/preview')
        def preview(group_id: str, body: RestoreTargetsBody, p=Depends(owner)):
            return self.preview_group_targets(group_id, body.model_dump())

        @app.put('/api/dark-restore/groups/{group_id}/mapping')
        def save(group_id: str, body: RestoreGroupTargetsBody, p=Depends(owner)):
            writable()
            value = body.model_dump(exclude={'expectedRevision'})
            result = self.set_targets(value, group_id=group_id, expected_revision=body.expectedRevision)
            audit(p.actor, p.actor.id, 'dark_restore.group.mapping', group_id, str(result['updated']))
            return result
