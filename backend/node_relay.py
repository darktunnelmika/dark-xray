"""Owner-selected, encrypted node exits. No customer or billing rows at the exit.

The destination credential is retained on disable so a delayed source ACK cannot
strand an old runtime. Reconfiguration is forbidden until the source ACKs disable.
An invalid active destination compiles to blackhole, never to direct fallback.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import threading
import time
import uuid

from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from dark_policy import PolicyError
from core import CoreError


class NodeRelay:
    def __init__(self, store, cipher, nodes, engine):
        self.store, self.cipher, self.nodes, self.engine = store, cipher, nodes, engine
        self.lock = threading.RLock()
        with store.transaction() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS node_relays (
                source_node TEXT NOT NULL, source_inbound INTEGER NOT NULL,
                exit_node TEXT NOT NULL, exit_inbound INTEGER NOT NULL,
                identity TEXT NOT NULL UNIQUE, credential_enc TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 0, phase TEXT NOT NULL DEFAULT 'disabled',
                updated_at REAL NOT NULL,
                PRIMARY KEY(source_node,source_inbound))''')

    def rows(self):
        with self.store.lock:
            return [dict(r) for r in self.store.db.execute(
                'SELECT * FROM node_relays ORDER BY source_node,source_inbound')]

    @staticmethod
    def public(row):
        return {'sourceNodeId': row['source_node'], 'sourceInboundId': row['source_inbound'],
                'exitNodeId': row['exit_node'], 'exitInboundId': row['exit_inbound'],
                'enabled': bool(row['enabled']), 'phase': row['phase'],
                'updatedAt': row['updated_at']}

    def list(self, source):
        self.nodes.get(source)
        return [self.public(r) for r in self.rows() if r['source_node'] == source]

    def get(self, source, inbound):
        for row in self.rows():
            if row['source_node'] == source and row['source_inbound'] == inbound:
                return row
        raise PolicyError('Node exit route does not exist')

    def _inbound(self, node_id, inbound_id):
        node = self.nodes.get(node_id)
        if not node['enabled']: raise PolicyError('Node exit routes require enabled nodes')
        if inbound_id not in node['inboundIds']: raise PolicyError('Inbound is not assigned to this node')
        try: inbound = self.engine.inbound(inbound_id)
        except CoreError as ex: raise PolicyError('Node exit inbound no longer exists') from ex
        if not inbound.get('enable', True): raise PolicyError('Node exit routes require enabled inbounds')
        return node, inbound

    def destination(self, row):
        node, inbound = self._inbound(row['exit_node'], row['exit_inbound'])
        st = inbound.get('streamSettings', {})
        network = st.get('network', 'tcp')
        if (inbound['protocol'] != 'vless' or st.get('security') != 'reality'
                or network not in ('tcp', 'raw', 'grpc')
                or inbound.get('settings', {}).get('decryption', 'none') != 'none'):
            raise PolicyError('Choose a VLESS Reality TCP or gRPC exit inbound')
        if inbound.get('listen') not in ('0.0.0.0', '::', ''):
            raise PolicyError('Exit inbound must listen on a public interface')
        if not node.get('data_address'): raise PolicyError('Exit node needs a data address')
        if network in ('tcp', 'raw') and st.get('tcpSettings', st.get('rawSettings', {})).get('header', {}).get('type', 'none') != 'none':
            raise PolicyError('Exit relay requires a plain Reality transport')
        reality = st.get('realitySettings', {})
        try:
            key = base64.urlsafe_b64decode(reality['privateKey'] + '=' * (-len(reality['privateKey']) % 4))
            public = X25519PrivateKey.from_private_bytes(key).public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
            public = base64.urlsafe_b64encode(public).decode().rstrip('=')
            server = reality['serverNames'][0]
            short = reality['shortIds'][0]
            if not isinstance(server, str) or not server or not isinstance(short, str): raise ValueError()
        except (KeyError, IndexError, TypeError, ValueError) as ex:
            raise PolicyError('Exit Reality settings are incomplete') from ex
        stream = {'network': network, 'security': 'reality', 'realitySettings': {
            'fingerprint': 'chrome', 'serverName': server, 'publicKey': public, 'shortId': short}}
        if network == 'grpc':
            stream['grpcSettings'] = {k: copy.deepcopy(v) for k, v in st.get('grpcSettings', {}).items()
                                      if k in ('serviceName', 'authority', 'multiMode')}
        return node, inbound, stream

    def validate(self, row):
        if row['source_node'] == row['exit_node']: raise PolicyError('Source and exit must be different nodes')
        self._inbound(row['source_node'], row['source_inbound'])
        self.destination(row)
        for node in (row['source_node'], row['exit_node']):
            if any(str(o.get('tag', '')).startswith('dark-relay-') for o in self.engine.runtime_outbounds('node:' + node)):
                raise PolicyError('The dark-relay- outbound namespace is reserved; rename the existing outbound first')
        edges = {}
        for r in self.rows():
            if (r['source_node'], r['source_inbound']) == (row['source_node'], row['source_inbound']): continue
            if r['enabled'] or r['phase'] != 'disabled': edges.setdefault(r['source_node'], set()).add(r['exit_node'])
        edges.setdefault(row['source_node'], set()).add(row['exit_node'])
        def walk(node, path):
            if node in path: raise PolicyError('Node exit routes must not form a cycle')
            for child in edges.get(node, ()): walk(child, path | {node})
        walk(row['source_node'], set())

    def configure(self, source, inbound, exit_node, exit_inbound):
        with self.lock:
            old = next((r for r in self.rows() if r['source_node'] == source and r['source_inbound'] == inbound), None)
            if old and (old['enabled'] or old['phase'] != 'disabled'):
                raise PolicyError('Disable and synchronize this route before changing its exit')
            row = {'source_node': source, 'source_inbound': inbound, 'exit_node': exit_node, 'exit_inbound': exit_inbound}
            self.validate(row)
            identity = old['identity'] if old else '_dark_relay.' + uuid.uuid4().hex
            credential = old['credential_enc'] if old else self.cipher.encrypt(str(uuid.uuid4()).encode()).decode()
            with self.store.transaction() as db:
                db.execute('''INSERT INTO node_relays VALUES (?,?,?,?,?,?,0,'disabled',?)
                    ON CONFLICT(source_node,source_inbound) DO UPDATE SET
                    exit_node=excluded.exit_node,exit_inbound=excluded.exit_inbound,updated_at=excluded.updated_at''',
                    (source, inbound, exit_node, exit_inbound, identity, credential, time.time()))
            return self.public(self.get(source, inbound))

    def _state(self, source, inbound, enabled, phase):
        with self.store.transaction() as db:
            db.execute('UPDATE node_relays SET enabled=?,phase=?,updated_at=? WHERE source_node=? AND source_inbound=?',
                       (int(enabled), phase, time.time(), source, inbound))

    def toggle(self, source, inbound, enabled, synchronize):
        with self.lock:
            row = self.get(source, inbound)
            if enabled:
                if row['phase'] == 'disabling': raise PolicyError('Finish synchronizing disable before enabling')
                self.validate(row)
                # Confirm full-state support on ingress while it is still off.
                # A legacy mirror-only source must never see enabled intent.
                preflight = synchronize(source)
                if not preflight.get('desired_state_applied') or preflight.get('queued'):
                    raise PolicyError('Source has not acknowledged full configuration; route was not enabled')
                # Exit must ACK its credential and direct rule before any source
                # monitor or lease renewal can see an enabled route.
                result = synchronize(row['exit_node'])
                if not result.get('desired_state_applied') or result.get('queued'):
                    raise PolicyError('Exit has not acknowledged configuration; source was not enabled')
            self._state(source, inbound, enabled, 'enabling' if enabled else 'disabling')
            result = synchronize(source)
            if not result.get('desired_state_applied') or result.get('queued'):
                raise PolicyError('Source synchronization pending; retry this action to confirm its state')
            self._state(source, inbound, enabled, 'enabled' if enabled else 'disabled')
            return self.public(self.get(source, inbound))

    def check_node_change(self, node_id, *, enabled=True, inbound_ids=None, deleting=False, origin=None):
        for row in self.rows():
            if not row['enabled'] and row['phase'] == 'disabled': continue
            for key, ibkey in (('source_node', 'source_inbound'), ('exit_node', 'exit_inbound')):
                if row[key] != node_id: continue
                moving = origin is not None and self.nodes.get(node_id)['origin'] != origin.rstrip('/')
                if deleting or not enabled or moving or (inbound_ids is not None and row[ibkey] not in inbound_ids):
                    raise PolicyError('Disable and synchronize node exit routes before removing their node or inbound')

    def enrich_bundles(self, node_id, bundles):
        by_id = {b['sourceInboundId']: b for b in bundles}
        for row in self.rows():
            if row['exit_node'] != node_id or row['exit_inbound'] not in by_id: continue
            # Only the exit receives a synthetic credential, never a managed
            # customer, quota allocation, subscription or commerce order.
            try: _, _, stream = self.destination(row)
            except PolicyError: continue
            client = {'id': self.cipher.decrypt(row['credential_enc'].encode()).decode(), 'enable': True}
            if stream['network'] in ('tcp', 'raw'): client['flow'] = 'xtls-rprx-vision'
            by_id[row['exit_inbound']]['clients'].append({'sourceEmail': row['identity'], 'client': client})
        return bundles

    @staticmethod
    def _tag(row):
        return 'dark-relay-' + hashlib.sha256((row['source_node'] + '\0' + str(row['source_inbound'])).encode()).hexdigest()[:20]

    def compile(self, node_id, sections):
        rows = self.rows()
        relevant = [r for r in rows if r['exit_node'] == node_id or (r['source_node'] == node_id and r['enabled'])]
        if not relevant: return sections
        sections = copy.deepcopy(sections)
        outbounds = sections['outbounds']; rules = sections['routing'].get('rules', [])
        if any(str(o.get('tag', '')).startswith('dark-relay-') for o in outbounds):
            raise PolicyError('The dark-relay- outbound namespace is reserved')
        ingress_rules = []; exit_rules = []
        blocked = {o['tag'] for o in outbounds if o.get('protocol') == 'blackhole'}
        def scoped_policies(match, tag, selected, inbound=None):
            scoped = []
            for original in rules:
                rule = copy.deepcopy(original)
                for key, values in match.items():
                    common = [v for v in values if key not in rule or v in rule[key]]
                    if not common: break
                    rule[key] = common
                else:
                    # Matrix direct rules must not acquire a tunnel match when
                    # Core expands ordinary inbound tags into shadow listeners.
                    if (inbound is not None and str(original.get('ruleTag', '')).startswith('dark-matrix-')
                            and rule.get('inboundTag') == [inbound['tag']]):
                        rule['localPort'] = inbound['port']
                    if original.get('outboundTag') not in blocked:
                        rule.pop('balancerTag', None)
                        rule['outboundTag'] = selected
                    rule['ruleTag'] = tag + '-policy-' + str(len(scoped))
                    scoped.append(rule)
            return scoped
        for row in relevant:
            tag = self._tag(row)
            if row['exit_node'] == node_id:
                try: self.destination(row)
                except PolicyError: continue
                outbounds.append({'tag': tag + '-exit', 'protocol': 'freedom', 'settings': {}})
                email = 'nm_' + hashlib.sha256((node_id + '\0' + row['identity']).encode()).hexdigest()[:24]
                exit_rules.extend(scoped_policies({'user': [email]}, tag + '-exit', tag + '-exit'))
                exit_rules.append({'type': 'field', 'user': [email], 'outboundTag': tag + '-exit', 'ruleTag': tag + '-exit'})
            if row['source_node'] == node_id and row['enabled']:
                try: inbound = self.engine.inbound(row['source_inbound'])
                except CoreError: continue  # Deleted ingress has no traffic to route.
                try:
                    self._inbound(node_id, row['source_inbound'])
                    node, exit_ib, stream = self.destination(row)
                    user = {'id': self.cipher.decrypt(row['credential_enc'].encode()).decode(), 'encryption': 'none'}
                    if stream['network'] in ('tcp', 'raw'): user['flow'] = 'xtls-rprx-vision'
                    outbound = {'tag': tag, 'protocol': 'vless', 'settings': {'vnext': [{
                        'address': node['data_address'], 'port': exit_ib['port'], 'users': [user]}]}, 'streamSettings': stream}
                except PolicyError:
                    outbound = {'tag': tag, 'protocol': 'blackhole', 'settings': {}}
                outbounds.append(outbound)
                ports = inbound.get('panelMeta', {}).get('tunnelPorts', {})
                tags = [inbound['tag']]
                port = ports.get('node:' + node_id)
                if type(port) is int and 1 <= port <= 65535:
                    tags.append('dark-tunnel-' + str(row['source_inbound']) + '-' + str(port))
                ingress_rules.extend(scoped_policies({'inboundTag': tags}, tag, tag, inbound))
                ingress_rules.append({'type': 'field', 'inboundTag': [inbound['tag']], 'outboundTag': tag, 'ruleTag': tag})
        # Preserve allow/deny precedence, including exceptions before blocks.
        # Bridge users terminate at this exit even if its customer inbound also
        # has an independent route to a third node.
        sections['routing']['rules'] = exit_rules + ingress_rules + rules
        return sections
