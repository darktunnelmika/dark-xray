"""Shared policy must be available in minimal/restricted Node installations."""
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]

def test_minimal_node_package_contains_and_loads_credit_dependency(tmp_path):
    update=runpy.run_path(str(ROOT/'tools/update_node.py'))
    backend=tmp_path/'backend';backend.mkdir()
    assert 'unlimited_credit.py' in update['BACKEND']
    for name in update['BACKEND']:
        shutil.copy2(ROOT/'backend'/name,backend/name)
    result=subprocess.run([sys.executable,'-c',
        "from dark_policy import Store; s=Store(':memory:'); assert s.db.execute('PRAGMA user_version').fetchone()[0]==4; s.close()"],
        cwd=tmp_path,env={**os.environ,'PYTHONPATH':str(backend)},capture_output=True,text=True,timeout=20)
    assert result.returncode==0,result.stderr
    assert "'unlimited_credit.py'" in (ROOT/'tools/provision_node.py').read_text()

def test_policy_preloads_credit_before_source_path_becomes_unavailable(tmp_path):
    code="import sys; from dark_policy import Store; assert 'unlimited_credit' in sys.modules; sys.path[:]=[p for p in sys.path if not p.endswith('/backend')]; s=Store(':memory:'); s.close()"
    result=subprocess.run([sys.executable,'-c',code],cwd=tmp_path,
        env={**os.environ,'PYTHONPATH':str(ROOT/'backend')},capture_output=True,text=True,timeout=20)
    assert result.returncode==0,result.stderr
