import hashlib
import importlib.util
import json
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

HERE=Path(__file__).resolve().parents[1]
SPEC=importlib.util.spec_from_file_location("release_snapshot",HERE/"tools/release_snapshot.py")
release=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(release)


def run(repo,*args):
    return subprocess.run(["git",*args],cwd=repo,check=True,stdout=subprocess.PIPE,text=True).stdout.strip()


def commit(repo,msg):
    run(repo,"add","-A");run(repo,"commit","-m",msg)
    return run(repo,"rev-parse","HEAD")


def repo(tmp_path,monkeypatch):
    root=tmp_path/"repo";root.mkdir()
    run(root,"init","-b","main");run(root,"config","user.name","Stage5 Test");run(root,"config","user.email","stage5@example.test")
    (root/"backend").mkdir();(root/"docs").mkdir();(root/"tools").mkdir()
    (root/"VERSION").write_text("0.9.0-rc7\n")
    (root/"README.md").write_text("stage4\n")
    (root/"backend/core.py").write_text("RUNTIME=1\n")
    (root/"tools/release_snapshot.py").write_text("# release helper\n")
    for i in range(24):
        (root/"docs"/f"fixture-{i:02d}.md").write_text(f"fixture {i}\n")
    (root/"SHA256SUMS").write_text("")
    stage4=commit(root,"stage4")
    monkeypatch.setattr(release,"ROOT",root)
    monkeypatch.setattr(release,"repo_check",lambda:None)
    return root,stage4


def finish_release(root):
    (root/"README.md").write_text("stage5 docs\n")
    commit(root,"release docs")
    release.refresh_source_sums()
    return commit(root,"source checksums")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_release_artifacts_are_reproducible_and_self_verifying(tmp_path,monkeypatch):
    root,stage4=repo(tmp_path,monkeypatch);candidate=finish_release(root)
    one=tmp_path/"one";two=tmp_path/"two"
    first=release.build(candidate,stage4,one);second=release.build(candidate,stage4,two)
    assert first["release_commit"]==candidate and first["stage4_runtime_equivalent"] is True
    for name in first["artifacts"]:
        assert digest(one/name)==digest(two/name)
    assert digest(Path(first["manifest"]))==digest(Path(second["manifest"]))
    assert digest(Path(first["release_sha256sums"]))==digest(Path(second["release_sha256sums"]))

    tar_name=next(name for name in first["artifacts"] if name.endswith(".tar.gz"))
    zip_name=next(name for name in first["artifacts"] if name.endswith(".zip"))
    prefix="dark-xray-0.9.0-rc7/"
    with tarfile.open(one/tar_name,"r:gz") as tf:
        names=tf.getnames()
        assert prefix+"DARK-RELEASE.json" in names
        assert prefix+"release-install.py" in names
        tf.extractall(tmp_path/"unpack",filter="data")
    with zipfile.ZipFile(one/zip_name) as zf:
        assert prefix+"DARK-RELEASE.json" in zf.namelist()
        assert prefix+"release-install.py" in zf.namelist()

    extracted=tmp_path/"unpack"/prefix.rstrip("/")
    meta=json.loads((extracted/"DARK-RELEASE.json").read_text())
    assert meta["release_commit"]==candidate
    assert meta["stage4_accepted_commit"]==stage4
    cp=subprocess.run([sys.executable,str(extracted/"release-install.py"),"verify"],
                      cwd=extracted,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    assert cp.returncode==0,cp.stderr
    verified=json.loads(cp.stdout)
    assert verified["passed"] is True and verified["release_commit"]==candidate
    assert verified["verified_source_files"]>=20


def test_dirty_tree_is_refused(tmp_path,monkeypatch):
    root,stage4=repo(tmp_path,monkeypatch);candidate=finish_release(root)
    (root/"README.md").write_text("dirty\n")
    with pytest.raises(release.ReleaseError,match="not clean"):
        release.build(candidate,stage4,tmp_path/"dist")


def test_runtime_drift_is_refused(tmp_path,monkeypatch):
    root,stage4=repo(tmp_path,monkeypatch)
    (root/"backend/core.py").write_text("RUNTIME=2\n");commit(root,"runtime changed")
    release.refresh_source_sums();candidate=commit(root,"checksums")
    assert release.runtime_drift(stage4,candidate)==["backend/core.py"]
    with pytest.raises(release.ReleaseError,match="runtime drift"):
        release.build(candidate,stage4,tmp_path/"dist")


def test_stale_source_sums_are_refused(tmp_path,monkeypatch):
    root,stage4=repo(tmp_path,monkeypatch);candidate=finish_release(root)
    (root/"README.md").write_text("changed after checksums\n");stale=commit(root,"stale sums")
    with pytest.raises(release.ReleaseError,match="stale"):
        release.verify_source_sums(stale)


def test_release_only_policy_is_narrow():
    assert release.release_only("README.md")
    assert release.release_only("docs/stage5-release-preparation.md")
    assert release.release_only("tests/test_release_snapshot.py")
    assert release.release_only("tools/release_snapshot.py")
    assert not release.release_only("backend/core.py")
    assert not release.release_only("tools/update.py")
    assert not release.release_only("web/index.html")
