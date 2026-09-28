"""Opt-in Restore enforcement with real Xray, two TLS Agents and a local Hub.

All DBs, identities, ports, source metadata and HTTP destinations are disposable.
Traffic counters and expiry clocks are real. Reconciliation is explicit, so these
checks do not claim an offline lease or byte-exact distributed quota cutoff.
"""
import contextlib
import json
import time
from types import SimpleNamespace
from urllib.parse import urlsplit
import pytest
from test_node_real_data_plane import real_binary, real_fleet, tls_material, xray_client, transfer
from test_node_hub_recovery import inbound, free_port


def sync(f):
    if f.engine.running:
        f.engine.collect_stats(force=True, strict=True)
    for node in f.agents:
        node.engine.collect_stats(force=True, strict=True)
        f.reg.sync_traffic(node.id)
    f.api('/api/sync', {})
    for node in f.agents:
        out=f.api('/api/nodes/'+node.id+'/sync', {})
        assert out['desired_state_applied']
        f.reg.probe(node.id)


@contextlib.contextmanager
def restored(f, monkeypatch):
    restore=f.http.app.state.dark_restore
    source={'status':'verified','error':'','upload':50000,'download':0,'total':300000,'expire':0}
    monkeypatch.setattr(restore,'_scan',lambda url:dict(source))
    ids=[i['id'] for i in f.engine.inbounds()]
    local=inbound(free_port());local.update(tag='restore-local',remark='Restore local')
    local['panelMeta']={'deployLocal':True}
    local_id=f.api('/api/inbounds',local)['id'];ids.append(local_id)
    f.engine.config.public_address='127.0.0.1'
    shadow=free_port(); first=f.agents[0]
    # Configure a real separate listener in the disposable first Node's inbound.
    with f.store.transaction() as db:
        r=db.execute('SELECT body FROM core_inbounds WHERE id=?',(ids[0],)).fetchone()
        body=json.loads(r['body']);body.setdefault('panelMeta',{})['tunnelPorts']={'node:'+first.id:shadow}
        db.execute('UPDATE core_inbounds SET body=? WHERE id=?',(json.dumps(body),ids[0]))
    hosts=f.engine.section('hosts')
    hosts.append({'inboundId':ids[0],'address':'127.0.0.1','port':shadow,'remark':'RESTORE TUNNEL',
                  'runtime':'node:'+first.id,'endpointType':'tunnel','enable':True,'security':'same'})
    f.api('/api/settings/hosts',{'value':hosts},'PUT')
    result=f.api('/api/dark-restore/import',{'urls':['https://old.example/sub/restored-user'],
        'inboundIds':ids,'nodeMode':'all','includeLocal':True,'groupName':'Restore real test','scan':True})
    item=restore.rows()[0]; rid=item['id']; token=item['public_token']
    identity=f.engine.client_detail(item['core_email'])['client'].copy()
    f.api('/api/core/start',{});sync(f)
    response=f.http.get('/restore/sub/'+token+'?format=json')
    assert response.status_code==200,response.text
    links=response.json()['links']
    assert len(links)==4
    assert sum(x['endpointType']=='direct' for x in links)==3
    assert sum(x['endpointType']=='tunnel' for x in links)==1
    with contextlib.ExitStack() as stack:
        clients=[stack.enter_context(xray_client(f.root/('restore-client-'+str(i)),f.binary.path,
                    x['uri'],expected_uuid=identity['id'])) for i,x in enumerate(links)]
        yield SimpleNamespace(restore=restore,item=item,rid=rid,token=token,identity=identity,clients=clients,links=links)


def assert_saved_identity(f,r):
    fresh=f.engine.client_detail(r.item['core_email'])['client']
    for key in ('id','password','email'):assert fresh[key]==r.identity[key]
    item=next(x for x in r.restore.rows() if x['id']==r.rid)
    assert item['public_token']==r.token and item['group_id']==r.item['group_id']
    return item


def assert_only_restore_blocked(f,r):
    # Reuse the SAME previously downloaded URI and Xray client process.
    for client in r.clients:transfer(client.port,f.target,allowed=False)
    for client in f.clients:transfer(client.port,f.target,allowed=True)
    assert f.engine.running and all(n.engine.running for n in f.agents)
    assert f.http.get('/restore/sub/'+r.token).status_code==403


def test_restore_combined_quota_blocks_hub_nodes_and_shadow_not_native(real_fleet,monkeypatch):
    f=real_fleet
    with restored(f,monkeypatch) as r:
        received=sum(transfer(client.port,f.target) for client in r.clients)
        sync(f)
        item=assert_saved_identity(f,r)
        assert item['dark_used']>=received and item['local_used']>0 and item['node_used']>0
        assert item['dark_used']<item['legacy_total']
        assert item['effective_used']>=item['legacy_total']
        assert item['service_status']=='exhausted'
        assert_only_restore_blocked(f,r)
        status=r.restore.safety_status()
        assert status['hub_state']=='synced',status
        assert all(n['state']=='synced' for n in status['nodes']),status
        assert not status['offline_enforcement_guaranteed']
        f.evidence.update(restore_quota=True,restore_hub_direct=True,restore_two_nodes=True,
                          restore_shadow_listener=True,previously_delivered_uris_reused=True,
                          native_control_connections_passed=True,observed_dark_bytes=item['dark_used'])


def test_restore_natural_expiry_blocks_previously_received_configs(real_fleet,monkeypatch):
    f=real_fleet
    with restored(f,monkeypatch) as r:
        item=assert_saved_identity(f,r); expires=int(time.time())+12
        review={k:item['legacy_'+k] for k in ('upload','download','total','expire')}
        review.update(total=10*1024*1024,expire=expires,note='Explicit temporary fixture source expiry',
                      confirmed=True,expectedRevision=item['metadata_revision'])
        f.api('/api/dark-restore/'+r.rid+'/metadata',review,'PUT');sync(f)
        for client in r.clients:transfer(client.port,f.target)
        time.sleep(max(0,expires-time.time()+.2))
        sync(f)
        assert assert_saved_identity(f,r)['service_status']=='expired'
        assert_only_restore_blocked(f,r)
        f.evidence.update(restore_expiry=True,natural_clock=True,previously_delivered_uris_reused=True,
                          native_control_connections_passed=True)
