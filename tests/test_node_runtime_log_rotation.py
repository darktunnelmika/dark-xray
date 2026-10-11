"""Root-free timer wiring regression: no runtime/database mutations."""
from pathlib import Path
import importlib.util

ROOT=Path(__file__).resolve().parents[1]
SERVICE=ROOT/'deploy/dark-xray-node-runtime-rotate.service'
TIMER=ROOT/'deploy/dark-xray-node-runtime-rotate.timer'


def test_timer_runs_quarter_hour_and_never_controls_agent_or_tunnels():
    svc=SERVICE.read_text()
    timer=TIMER.read_text()
    assert 'ExecStart=/usr/sbin/logrotate /etc/logrotate.d/dark-xray-node-runtime' in svc
    assert 'Type=oneshot' in svc and 'NoNewPrivileges=true' in svc
    assert 'OnCalendar=*-*-* *:0/15:00' in timer
    assert 'Unit=dark-xray-node-runtime-rotate.service' in timer
    assert 'Persistent=true' in timer
    for content in (svc,timer):
        assert 'ExecStop' not in content and 'dark-xray-node.service' not in content
        assert 'dark_core' not in content and 'systemctl restart' not in content


def test_node_updater_snapshots_and_installs_both_new_timer_units():
    spec=importlib.util.spec_from_file_location('dark_node_updater_rotation',ROOT/'tools/update_node.py')
    updater=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(updater)
    assert 'dark-xray-node-runtime-rotate.service' in updater.DEPLOY
    assert 'dark-xray-node-runtime-rotate.timer' in updater.DEPLOY
    assert 'deploy/dark-xray-node-runtime-rotate.timer' in updater.SOURCE_FILES
    assert 'deploy/dark-xray-node-runtime-rotate.service' in updater.SOURCE_FILES
    text=(ROOT/'tools/update_node.py').read_text()
    assert "run(['systemctl','enable','--now','dark-xray-node-runtime-rotate.timer']" in text


def test_fresh_node_installer_installs_and_enables_timer():
    text=(ROOT/'tools/provision_node.py').read_text()
    assert 'dark-xray-node-runtime-rotate.service' in text
    assert 'dark-xray-node-runtime-rotate.timer' in text
    assert "run(['systemctl','enable','--now','dark-xray-node-runtime-rotate.timer'])" in text
    assert text.index('install_logrotate()')<text.index("run(['systemctl','enable','--now','dark-xray-node-runtime-rotate.timer'])")
