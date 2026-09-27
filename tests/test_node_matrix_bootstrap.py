import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
BACKEND=ROOT/"backend"

def _legacy_node_tree(tmp_path:Path)->Path:
    root=tmp_path/"node";backend=root/"backend";backend.mkdir(parents=True)
    for src in BACKEND.glob("*.py"):
        if src.name in {"traffic_matrix.py","outbound_probe.py","warp_paths.py","warp_cloudflare.py"}:
            continue
        shutil.copy2(src,backend/src.name)
    shutil.copy2(ROOT/"VERSION",root/"VERSION")
    return root

def test_rc8_node_agent_bootstraps_when_legacy_updater_omits_new_modules(tmp_path):
    root=_legacy_node_tree(tmp_path)
    env=os.environ.copy();env["PYTHONPATH"]=str(root/"backend")
    cp=subprocess.run([sys.executable,str(root/"backend/node_agent.py"),"--help"],
                      env=env,cwd=root,capture_output=True,text=True,timeout=20)
    assert cp.returncode==0,(cp.stdout+cp.stderr)[-2000:]
    assert "ModuleNotFoundError" not in cp.stderr

def test_rc8_core_fallback_compiles_direct_and_tunnel_matrix_without_helper_module(tmp_path):
    root=_legacy_node_tree(tmp_path)
    code="""import core
r=core.compile_matrix_rules(scope='node:n1',inbound_id=7,access_path='tunnel',
 policy='warp_ai_adblock',inbound_tag='ib',tunnel_port=1185)
assert [x['outboundTag'] for x in r]==['block','warp','direct']
assert all(x['inboundTag']==['dark-tunnel-7-1185'] for x in r)
print('ok')
"""
    env=os.environ.copy();env["PYTHONPATH"]=str(root/"backend")
    cp=subprocess.run([sys.executable,"-c",code],env=env,cwd=root,capture_output=True,text=True,timeout=20)
    assert cp.returncode==0,(cp.stdout+cp.stderr)[-2000:]
    assert cp.stdout.strip()=="ok"

def test_node_manager_same_sha_can_materialize_missing_rc8_modules():
    src=(ROOT/"tools/node_manager.py").read_text()
    assert "Materializing missing rc8 Node modules at current commit" in src
    assert "traffic_matrix.py" in src and "outbound_probe.py" in src and "warp_paths.py" in src
