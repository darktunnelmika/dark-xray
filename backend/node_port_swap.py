"""Opaque TCP access to a destination inbound through an independent relay port.

No customer is created on the relay and no ordinary relay inbound is rerouted.
The destination shadow listener shares its inbound's authentication/accounting.
"""
import copy
import threading
import time
from urllib.parse import urlsplit

from dark_policy import PolicyError


class NodePortSwap:
    def __init__(self, store, nodes, engine):
        self.store, self.nodes, self.engine = store, nodes, engine
        self.lock = threading.RLock()
        with store.transaction() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS node_port_swaps (
                id INTEGER PRIMARY KEY AUTOINCREMENT, source_node TEXT NOT NULL,
                source_port INTEGER NOT NULL, exit_node TEXT NOT NULL,
                inbound_id INTEGER NOT NULL, exit_port INTEGER NOT NULL,
                entry_address TEXT NOT NULL, entry_port INTEGER NOT NULL,
                name TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 0,
                phase TEXT NOT NULL DEFAULT 'disabled', updated_at REAL NOT NULL,
                UNIQUE(source_node,source_port), UNIQUE(exit_node,inbound_id))''')

    @staticmethod
    def source_id(route_id):
        return 1000000000 + route_id

    def assignment_sources(self, node_id):
        # Only currently enabled, node-scoped routes may acknowledge generated
        # listeners. An ID range or tag prefix alone is not authorization.
        return {self.source_id(r['id']) for r in self.rows()
                if r['source_node'] == node_id and r['enabled'] and r['phase'] != 'deleting'}

    def rows(self):
        with self.store.lock:
            return [dict(r) for r in self.store.db.execute('SELECT * FROM node_port_swaps ORDER BY id')]

    def get(self, rid):
        row = next((r for r in self.rows() if r['id'] == rid), None)
        if not row: raise PolicyError('DARK SWAP route not found')
        return row

    @staticmethod
    def public(r):
        return {'id': r['id'], 'sourceNodeId': r['source_node'], 'sourcePort': r['source_port'],
                'exitNodeId': r['exit_node'], 'inboundId': r['inbound_id'], 'exitPort': r['exit_port'],
                'entryAddress': r['entry_address'], 'entryPort': r['entry_port'], 'name': r['name'],
                'enabled': bool(r['enabled']), 'phase': r['phase'], 'updatedAt': r['updated_at'],
                'accountingMode': 'destination-customer'}

    def _ports(self, node_id):
        node = self.nodes.get(node_id)
        ports = set(self.engine.config.protected_ports)
        ports.add(urlsplit(node['origin']).port or 443)
        for i in node['inboundIds']:
            ib = self.engine.inbound(i)
            ports.add(ib['port'])
            p = ib.get('panelMeta', {}).get('tunnelPorts', {}).get('node:' + node_id)
            if p: ports.add(p)
        return ports

    def validate(self, row):
        src, dst = self.nodes.get(row['source_node']), self.nodes.get(row['exit_node'])
        if src['id'] == dst['id']: raise PolicyError('Relay and destination must be different nodes')
        if not src['enabled'] or not dst['enabled']: raise PolicyError('Both nodes must be enabled')
        if row['inbound_id'] not in dst['inboundIds']: raise PolicyError('Assign this inbound to the destination node first')
        ib = self.engine.inbound(row['inbound_id'])
        if ib.get('listen') not in ('0.0.0.0','::',''):
            raise PolicyError('Destination inbound must listen on a public interface')
        if not ib.get('enable', True) or ib['protocol'] not in ('vless', 'vmess', 'trojan', 'shadowsocks'):
            raise PolicyError('Choose an enabled customer inbound')
        if ib.get('streamSettings', {}).get('network', 'tcp') not in ('tcp', 'raw', 'grpc', 'ws', 'httpupgrade', 'xhttp'):
            raise PolicyError('DARK SWAP supports TCP transports; UDP/mKCP cannot use this path')
        if not dst.get('data_address'): raise PolicyError('Destination node needs a data address')
        for key in ('source_port', 'exit_port', 'entry_port'):
            if type(row[key]) is not int or not 1 <= row[key] <= 65535: raise PolicyError('Ports must be between 1 and 65535')
        if row['source_port'] in self._ports(src['id']):
            raise PolicyError('Relay port collides with an existing direct/tunnel/management port')
        existing = ib.get('panelMeta', {}).get('tunnelPorts', {}).get('node:' + dst['id'])
        if existing and existing != row['exit_port']:
            raise PolicyError('Use the destination existing tunnel listener port; ordinary tunnel settings cannot be replaced')
        if not existing and row['exit_port'] in self._ports(dst['id']):
            raise PolicyError('Destination shadow port collides with an existing listener')
        for r in self.rows():
            if r['id'] == row.get('id'): continue
            if r['source_node'] == src['id'] and r['source_port'] == row['source_port']:
                raise PolicyError('Relay port is already reserved by another SWAP route')
            if r['exit_node'] == dst['id'] and r['inbound_id'] == row['inbound_id']:
                raise PolicyError('This destination inbound already has a SWAP route')
            if r['source_node'] == dst['id'] and r['source_port'] == row['exit_port']:
                raise PolicyError('Destination port is a relay port, not the customer inbound')
            if r['exit_node'] == src['id'] and r['exit_port'] == row['source_port']:
                raise PolicyError('Relay port collides with a destination shadow listener')
            if r['exit_node'] == dst['id'] and r['exit_port'] == row['exit_port']:
                raise PolicyError('Destination port belongs to another SWAP inbound')
        if any(o.get('tag', '').startswith('dark-swap-') for o in self.engine.runtime_outbounds('node:' + src['id'])):
            raise PolicyError('The dark-swap- outbound namespace is reserved')

    def configure(self, body, rid=None, sync=None):
        with self.lock:
            if rid:
                old = self.get(rid)
                if old['enabled'] or old['phase'] != 'disabled': raise PolicyError('Disable and confirm synchronization before editing')
            row = {'id': rid, 'source_node': body['sourceNodeId'], 'source_port': body['sourcePort'],
                   'exit_node': body['exitNodeId'], 'inbound_id': body['inboundId'], 'exit_port': body['exitPort'],
                   'entry_address': body['entryAddress'].strip(), 'entry_port': body['entryPort'], 'name': body['name'].strip()}
            address = row['entry_address']
            if not address or len(address) > 253 or any(c in address for c in '/?#@ \r\n'):
                raise PolicyError('Entry address must be a plain IP or domain')
            if not row['name'] or len(row['name']) > 160: raise PolicyError('Choose a route name up to 160 characters')
            self.validate(row)
            if rid and any(row[k] != old[k] for k in ('exit_node', 'inbound_id', 'exit_port')):
                if sync is None: raise PolicyError('Destination changes require confirmed shadow cleanup')
                self._state(rid, False, 'deleting')
                ack = sync(old['exit_node'])
                if not ack.get('desired_state_applied') or ack.get('queued'):
                    raise PolicyError('Old destination cleanup pending; retry delete before recreating')
            with self.store.transaction() as db:
                fields = ('source_node', 'source_port', 'exit_node', 'inbound_id', 'exit_port', 'entry_address', 'entry_port', 'name')
                if rid:
                    db.execute("UPDATE node_port_swaps SET "+','.join(k+'=?' for k in fields)+",phase='disabled',updated_at=? WHERE id=?",
                               tuple(row[k] for k in fields)+(time.time(), rid))
                else:
                    rid = db.execute('INSERT INTO node_port_swaps ('+','.join(fields)+',updated_at) VALUES (?,?,?,?,?,?,?,?,?)',
                                     tuple(row[k] for k in fields)+(time.time(),)).lastrowid
            return self.public(self.get(rid))

    def rename(self, rid, name):
        """Rename a route without interrupting an active data path.

        The name is presentation metadata plus an Xray remark. Persisting it does
        not synchronize/restart the relay; the runtime remark catches up on the
        next ordinary desired-state apply, while subscriptions/UI update now.
        """
        with self.lock:
            row = self.get(rid)
            if row['phase'] == 'deleting':
                raise PolicyError('Complete deletion before renaming DARK SWAP')
            value = str(name or '').strip()
            if not value or len(value) > 160:
                raise PolicyError('Choose a route name up to 160 characters')
            if value == row['name']:
                return self.public(row)
            with self.store.transaction() as db:
                db.execute('UPDATE node_port_swaps SET name=?,updated_at=? WHERE id=?',
                           (value, time.time(), rid))
            return self.public(self.get(rid))

    def _state(self, rid, enabled, phase):
        with self.store.transaction() as db:
            db.execute('UPDATE node_port_swaps SET enabled=?,phase=?,updated_at=? WHERE id=?', (int(enabled), phase, time.time(), rid))

    def toggle(self, rid, enabled, sync):
        with self.lock:
            row = self.get(rid)
            if row['phase'] == 'deleting': raise PolicyError('Complete deletion first')
            if enabled:
                if row['phase'] == 'disabling': raise PolicyError('Confirm disable before enabling')
                self.validate(row)
                ack = sync(row['exit_node'])
                if not ack.get('desired_state_applied') or ack.get('queued'):
                    raise PolicyError('Destination has not acknowledged its shadow listener')
            self._state(rid, enabled, 'enabling' if enabled else 'disabling')
            ack = sync(row['source_node'])
            if not ack.get('desired_state_applied') or ack.get('queued'):
                raise PolicyError('Relay synchronization pending; retry to confirm')
            self._state(rid, enabled, 'enabled' if enabled else 'disabled')
            return self.public(self.get(rid))

    def delete(self, rid, sync):
        with self.lock:
            row = self.get(rid)
            if row['enabled'] or row['phase'] not in ('disabled', 'deleting'):
                raise PolicyError('Disable and confirm the relay before deleting')
            self._state(rid, False, 'deleting')
            ack = sync(row['exit_node'])
            if not ack.get('desired_state_applied') or ack.get('queued'):
                raise PolicyError('Destination cleanup pending; retry delete')
            with self.store.transaction() as db: db.execute('DELETE FROM node_port_swaps WHERE id=?', (rid,))
            return {'deleted': True}

    def check_node_change(self, node_id, *, enabled=True, inbound_ids=None, deleting=False, origin=None):
        for r in self.rows():
            if inbound_ids is not None:
                for iid in inbound_ids:
                    ib = self.engine.inbound(iid)
                    tunnel = ib.get('panelMeta', {}).get('tunnelPorts', {}).get('node:'+node_id)
                    if node_id == r['source_node'] and r['source_port'] in (ib['port'], tunnel):
                        raise PolicyError('Node assignment collides with a reserved DARK SWAP relay port')
                    if node_id == r['exit_node'] and (ib['port'] == r['exit_port'] or (tunnel == r['exit_port'] and iid != r['inbound_id'])):
                        raise PolicyError('Node assignment collides with a reserved DARK SWAP shadow port')
            if deleting and node_id in (r['source_node'], r['exit_node']):
                raise PolicyError('Delete referenced DARK SWAP routes before deleting the node')
            if not r['enabled'] and r['phase'] == 'disabled': continue
            if node_id not in (r['source_node'], r['exit_node']): continue
            if (deleting or not enabled or (origin and self.nodes.get(node_id)['origin'] != origin.rstrip('/'))
                or (node_id == r['exit_node'] and inbound_ids is not None and r['inbound_id'] not in inbound_ids)):
                raise PolicyError('Disable and synchronize DARK SWAP before changing its nodes or assignment')

    def check_inbound(self, body, inbound_id=None, deleting=False):
        for r in self.rows():
            same = inbound_id == r['inbound_id']
            if same and deleting:
                raise PolicyError('Delete referenced DARK SWAP routes before deleting this inbound')
            if same and (r['enabled'] or r['phase'] != 'disabled'):
                old = self.engine.inbound(inbound_id)
                for key in ('port', 'protocol', 'enable', 'listen', 'streamSettings'):
                    if body.get(key, old.get(key)) != old.get(key):
                        raise PolicyError('Disable and synchronize DARK SWAP before changing its destination inbound')
            meta = body.get('panelMeta', {})
            targets = meta.get('deploymentTargets', [])
            for node_id, reserved in ((r['source_node'], r['source_port']), (r['exit_node'], r['exit_port'])):
                assigned = inbound_id in self.nodes.get(node_id)['inboundIds'] if inbound_id else False
                if (assigned or node_id in targets or 'node:'+node_id in targets) and body.get('port') == reserved:
                    raise PolicyError('Inbound port is reserved by DARK SWAP')
                tunnel = meta.get('tunnelPorts', {}).get('node:'+node_id)
                if tunnel == reserved and not (same and node_id == r['exit_node']):
                    raise PolicyError('Tunnel port is reserved by DARK SWAP')
                if same and node_id == r['exit_node'] and tunnel and tunnel != reserved:
                    raise PolicyError('Destination Tunnel Port must match its DARK SWAP shadow port')

    def enrich(self, node_id, bundles, sections):
        relevant = [r for r in self.rows() if r['phase'] != 'deleting' and
                    (r['exit_node'] == node_id or (r['source_node'] == node_id and r['enabled']))]
        if not relevant: return bundles, sections
        bundles, sections = copy.deepcopy(bundles), copy.deepcopy(sections)
        for r in relevant:
            if r['exit_node'] == node_id:
                try: self.validate(r)
                except PolicyError:
                    if not r['enabled'] and r['phase'] == 'disabled': continue
                    raise
                bundle = next((b for b in bundles if b['sourceInboundId'] == r['inbound_id']), None)
                if bundle:
                    meta = bundle['inbound'].setdefault('panelMeta', {})
                    meta.setdefault('tunnelPorts', {})['local'] = r['exit_port']
            if r['source_node'] == node_id and r['enabled']:
                # Invalid active intent must fail closed, not silently omit its
                # relay listener and accidentally reuse a customer's port.
                self.validate(r)
                synthetic = self.source_id(r['id'])
                tag = 'dark-swap-' + str(r['id'])
                if any(b['sourceInboundId'] == synthetic for b in bundles): raise PolicyError('SWAP synthetic inbound collision')
                bundles.append({'sourceInboundId': synthetic, 'clients': [], 'inbound': {
                    'tag': tag, 'remark': 'DARK SWAP TCP · ' + r['name'], 'enable': True,
                    'listen': '0.0.0.0', 'port': r['source_port'], 'protocol': 'dokodemo-door',
                    'settings': {'address': self.nodes.get(r['exit_node'])['data_address'], 'port': r['exit_port'], 'network': 'tcp', 'followRedirect': False},
                    'streamSettings': {'network': 'tcp', 'security': 'none'}, 'sniffing': {'enabled': False}}})
                if any(o.get('tag') == tag for o in sections['outbounds']): raise PolicyError('Reserved SWAP outbound collision')
                sections['outbounds'].append({'tag': tag, 'protocol': 'freedom', 'settings': {}})
                sections['routing'].setdefault('rules', []).insert(0, {'type': 'field', 'inboundTag': [tag], 'outboundTag': tag, 'ruleTag': tag})
        return bundles, sections

    def hosts(self):
        nodes = {n['id']: n for n in self.nodes.list()}
        hosts = []
        for r in self.rows():
            if not r['enabled'] or r['phase'] != 'enabled': continue
            src, dst = nodes.get(r['source_node'], {}), nodes.get(r['exit_node'], {})
            if any(not n.get('enabled') or not n.get('online') or n.get('maintenance') or n.get('last_error') or n.get('desired_state', {}).get('pending') or self.nodes._runtime_block_reason(n) for n in (src, dst)):
                continue
            hosts.append({'inboundId': r['inbound_id'], 'runtime': 'node:' + r['exit_node'],
                          'address': r['entry_address'], 'port': r['entry_port'],
                          'remark': r['name'], 'endpointType': 'swap', 'swapId': r['id']})
        return hosts
