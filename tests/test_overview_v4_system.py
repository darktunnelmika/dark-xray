from core import Config,CoreEngine
from dark_policy import Store


def test_overview_v4_system_telemetry_shape(tmp_path):
    store=Store(tmp_path/'dark.sqlite3')
    engine=CoreEngine(Config(public_origin='http://127.0.0.1:2087',
                             xray_binary=str(tmp_path/'missing-xray'),
                             xray_assets=str(tmp_path),test_engine=True),
                      store,tmp_path/'runtime')
    try:
        doc=engine.system()
        assert {'cpu','cpuInfo','mem','disk','swap','uptime','netTraffic','netIO','connections','addresses','panel','xray','runtime'}<=set(doc)
        assert {'physical','logical','mhz'}<=set(doc['cpuInfo'])
        assert {'open','tcp','udp','available'}<=set(doc['connections'])
        assert {'mem','threads','pid'}<=set(doc['panel'])
        assert {'state','version','mem','uptime'}<=set(doc['xray'])
        assert doc['panel']['pid']>0
        assert doc['panel']['threads']>=1
        assert doc['connections']['open']==doc['connections']['tcp']+doc['connections']['udp']
        assert isinstance(doc['addresses'],list)
        for row in doc['addresses']:
            assert set(row)=={'interface','address','family'}
            assert row['family'] in (4,6)
    finally:
        engine.close();store.close()
