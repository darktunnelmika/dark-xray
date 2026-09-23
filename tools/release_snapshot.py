#!/usr/bin/env python3
"""Build and verify a reproducible DARK XRAY release snapshot.

Stage 5 is fail-closed: the candidate must be an exact clean commit, source
checksums must match that commit, and runtime files may not drift from the
Stage 4 accepted runtime without rerunning that acceptance.

Release archives carry two generated, release-only root files:
- DARK-RELEASE.json: immutable release identity/provenance
- release-install.py: verifies the extracted source, then delegates to the
  already Stage-4-accepted provision/update code and records exact artifact
  identity after success.

Neither generated file is copied into /opt/dark-xray by provision.py, so the
accepted runtime tree is unchanged.
"""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import hashlib
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
SHA_RE=re.compile(r"^[0-9a-f]{40}$")
DEFAULT_STAGE4="94ae50548f105bea7e79b40a28f7f5ae3704056d"

RELEASE_ONLY_EXACT={
    ".gitattributes",".gitignore","CHANGELOG.fa.md","CONTRIBUTING.md","LICENSE",
    "PUBLISH-STATUS.json","README.md","README.en.md","README.fa.md","SECURITY.md",
    "SHA256SUMS","STATUS.fa.md","TESTING-RC.fa.md","THIRD-PARTY-NOTICES.md",
    "tools/release_snapshot.py",
}
RELEASE_ONLY_PREFIXES=(".github/","docs/","qa/","tests/")

RELEASE_INSTALLER=r'''#!/usr/bin/env python3
"""Verify and install/update an extracted DARK XRAY release artifact."""
from __future__ import annotations

import hashlib
import json
import os
import pwd
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parent
META=ROOT/"DARK-RELEASE.json"
SUMS=ROOT/"SHA256SUMS"
SHA40=re.compile(r"^[0-9a-f]{40}$")
SHA256=re.compile(r"^[0-9a-f]{64}$")


class ReleaseInstallError(RuntimeError):
    pass


def digest(path:Path)->str:
    h=hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda:f.read(1024*1024),b""):
            h.update(chunk)
    return h.hexdigest()


def metadata()->dict:
    if not META.is_file() or META.is_symlink():
        raise ReleaseInstallError("DARK-RELEASE.json is missing or unsafe")
    try:
        doc=json.loads(META.read_text(encoding="utf-8"))
    except Exception as ex:
        raise ReleaseInstallError("DARK-RELEASE.json is invalid") from ex
    required={"schema","project","version","release_commit","release_tree",
              "stage4_accepted_commit","source_sha256sums_sha256"}
    if set(doc)!=required or doc.get("schema")!=1 or doc.get("project")!="DARK XRAY":
        raise ReleaseInstallError("release metadata schema is invalid")
    if not SHA40.fullmatch(str(doc["release_commit"])) or not SHA40.fullmatch(str(doc["stage4_accepted_commit"])):
        raise ReleaseInstallError("release metadata commit identity is invalid")
    if not SHA40.fullmatch(str(doc["release_tree"])):
        raise ReleaseInstallError("release metadata tree identity is invalid")
    if not SHA256.fullmatch(str(doc["source_sha256sums_sha256"])):
        raise ReleaseInstallError("release metadata checksum identity is invalid")
    version=(ROOT/"VERSION").read_text(encoding="utf-8").strip() if (ROOT/"VERSION").is_file() else ""
    if version!=doc["version"]:
        raise ReleaseInstallError("release metadata VERSION mismatch")
    return doc


def verify_source(doc:dict)->int:
    if not SUMS.is_file() or SUMS.is_symlink():
        raise ReleaseInstallError("SHA256SUMS is missing or unsafe")
    if digest(SUMS)!=doc["source_sha256sums_sha256"]:
        raise ReleaseInstallError("SHA256SUMS digest does not match release metadata")
    seen=set();count=0
    for raw in SUMS.read_text(encoding="utf-8").splitlines():
        match=re.fullmatch(r"([0-9a-f]{64})  (.+)",raw)
        if not match:
            raise ReleaseInstallError("malformed SHA256SUMS line")
        expected,rel=match.groups()
        if rel in seen:
            raise ReleaseInstallError("duplicate SHA256SUMS path")
        seen.add(rel)
        candidate=ROOT/rel
        if candidate.is_symlink() or not candidate.is_file():
            raise ReleaseInstallError("release source file is missing or unsafe: "+rel)
        try:
            candidate.resolve().relative_to(ROOT.resolve())
        except ValueError:
            raise ReleaseInstallError("release source path escapes artifact root: "+rel)
        if digest(candidate)!=expected:
            raise ReleaseInstallError("release source checksum mismatch: "+rel)
        count+=1
    if count<20:
        raise ReleaseInstallError("release source checksum set is unexpectedly small")
    return count


def atomic_identity(doc:dict,ref:str)->None:
    data=Path("/var/lib/dark-xray")
    if data.is_symlink() or not data.is_dir():
        raise ReleaseInstallError("installed DARK data directory is missing or unsafe")
    try:
        account=pwd.getpwnam("darkxray")
    except KeyError as ex:
        raise ReleaseInstallError("darkxray service account is missing after installation") from ex
    target=data/"installed-source.json"
    if target.exists() and target.is_symlink():
        raise ReleaseInstallError("installed source identity path is unsafe")
    payload={
        "commit":doc["release_commit"],
        "version":doc["version"],
        "ref":"release-artifact:"+ref,
        "installed_at":time.time(),
        "artifact":{
            "tree":doc["release_tree"],
            "stage4_accepted_commit":doc["stage4_accepted_commit"],
            "source_sha256sums_sha256":doc["source_sha256sums_sha256"],
        },
    }
    fd,name=tempfile.mkstemp(prefix=".installed-source-",dir=data)
    tmp=Path(name)
    try:
        with os.fdopen(fd,"w",encoding="utf-8") as f:
            json.dump(payload,f,indent=2,sort_keys=True)
            f.write("\n");f.flush();os.fsync(f.fileno())
        os.chmod(tmp,0o640);os.chown(tmp,0,account.pw_gid)
        os.replace(tmp,target)
        dfd=os.open(data,os.O_RDONLY|os.O_DIRECTORY)
        try:os.fsync(dfd)
        finally:os.close(dfd)
    finally:
        try:tmp.unlink()
        except FileNotFoundError:pass


def run(action:str,args:list[str])->int:
    doc=metadata();count=verify_source(doc)
    if action=="verify":
        print(json.dumps({"passed":True,"release_commit":doc["release_commit"],
                          "release_tree":doc["release_tree"],"version":doc["version"],
                          "verified_source_files":count},sort_keys=True))
        return 0
    if os.geteuid()!=0:
        raise ReleaseInstallError("root is required for release fresh/update actions")
    if action=="fresh":
        cmd=[sys.executable,str(ROOT/"tools/provision.py"),*args]
        cp=subprocess.run(cmd)
        if cp.returncode:return cp.returncode
        atomic_identity(doc,"fresh")
        return 0
    if action=="update":
        if args:
            raise ReleaseInstallError("update action takes no extra arguments")
        runtime=Path("/opt/dark-xray/.venv/bin/python")
        updater=ROOT/"tools/update.py"
        if not runtime.is_file() or runtime.is_symlink():
            raise ReleaseInstallError("installed DARK runtime Python is missing or unsafe")
        cp=subprocess.run([str(runtime),str(updater),"--source",str(ROOT),"--non-interactive"])
        if cp.returncode:return cp.returncode
        atomic_identity(doc,"update")
        return 0
    raise ReleaseInstallError("action must be verify, fresh or update")


def main()->int:
    if len(sys.argv)<2:
        print("Usage: python3 release-install.py {verify|fresh|update} [fresh provision args...]",file=sys.stderr)
        return 2
    try:return run(sys.argv[1],sys.argv[2:])
    except ReleaseInstallError as ex:
        print("RELEASE INSTALL FAIL:",ex,file=sys.stderr)
        return 1


if __name__=="__main__":
    raise SystemExit(main())
'''


class ReleaseError(RuntimeError):
    pass


def git(*args:str, binary:bool=False, cwd:Path|None=None):
    cp=subprocess.run(["git",*args],cwd=cwd or ROOT,check=False,
                      stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                      text=not binary)
    if cp.returncode:
        err=cp.stderr.decode("utf-8","replace") if binary else cp.stderr
        raise ReleaseError("git "+" ".join(args)+" failed: "+err.strip()[:500])
    return cp.stdout


def exact_commit(value:str)->str:
    value=(value or "").strip().lower()
    if not SHA_RE.fullmatch(value):
        raise ReleaseError("release commit must be an immutable 40-character SHA")
    resolved=git("rev-parse","--verify",value+"^{commit}").strip().lower()
    if resolved!=value:
        raise ReleaseError("release commit did not resolve exactly")
    return resolved


def head_commit()->str:
    return git("rev-parse","HEAD").strip().lower()


def tree_sha(commit:str)->str:
    return git("rev-parse",commit+"^{tree}").strip().lower()


def clean_worktree()->None:
    dirty=git("status","--porcelain=v1","--untracked-files=all")
    if dirty.strip():
        names=[line[3:] if len(line)>3 else line for line in dirty.splitlines()]
        raise ReleaseError("working tree is not clean: "+", ".join(names[:12]))


def release_only(path:str)->bool:
    return path in RELEASE_ONLY_EXACT or any(path.startswith(p) for p in RELEASE_ONLY_PREFIXES)


def runtime_drift(stage4:str,candidate:str)->list[str]:
    changed=[x for x in git("diff","--name-only",stage4+".."+candidate).splitlines() if x]
    return [p for p in changed if not release_only(p)]


def ls_tree(commit:str)->list[tuple[str,str]]:
    raw=git("ls-tree","-r","-z",commit,binary=True)
    out=[]
    for item in raw.split(b"\0"):
        if not item:continue
        meta,path=item.split(b"\t",1)
        mode,kind,_sha=meta.decode().split()
        name=path.decode("utf-8","strict")
        if kind!="blob":continue
        if mode=="120000":
            raise ReleaseError("release source contains a symlink: "+name)
        out.append((mode,name))
    return out


def blob(commit:str,path:str)->bytes:
    return git("show",f"{commit}:{path}",binary=True)


def sums_bytes(items:list[tuple[str,bytes]])->bytes:
    return "".join(f"{hashlib.sha256(data).hexdigest()}  {name}\n"
                   for name,data in sorted(items)).encode()


def commit_source_sums(commit:str)->bytes:
    return sums_bytes([(path,blob(commit,path)) for _mode,path in ls_tree(commit)
                       if path!="SHA256SUMS"])


def verify_source_sums(commit:str)->None:
    try:actual=blob(commit,"SHA256SUMS")
    except ReleaseError as ex:raise ReleaseError("candidate has no SHA256SUMS") from ex
    if actual!=commit_source_sums(commit):
        raise ReleaseError("SHA256SUMS is stale for the exact release commit")


def refresh_source_sums()->Path:
    dirty=git("status","--porcelain=v1","--untracked-files=all")
    offenders=[]
    for line in dirty.splitlines():
        path=line[3:] if len(line)>3 else line
        if path!="SHA256SUMS":offenders.append(path)
    if offenders:
        raise ReleaseError("commit all release content before refreshing SHA256SUMS: "+", ".join(offenders[:12]))
    items=[]
    for path in git("ls-files","-z",binary=True).split(b"\0"):
        if not path:continue
        name=path.decode("utf-8","strict")
        if name=="SHA256SUMS":continue
        file=ROOT/name
        if file.is_symlink():raise ReleaseError("tracked symlink refused: "+name)
        if not file.is_file():raise ReleaseError("tracked file missing: "+name)
        items.append((name,file.read_bytes()))
    target=ROOT/"SHA256SUMS";data=sums_bytes(items)
    fd,tmp=tempfile.mkstemp(prefix=".SHA256SUMS.",dir=ROOT)
    try:
        with os.fdopen(fd,"wb") as f:
            f.write(data);f.flush();os.fsync(f.fileno())
        os.chmod(tmp,0o644);os.replace(tmp,target)
    finally:
        try:os.unlink(tmp)
        except FileNotFoundError:pass
    return target


def release_metadata(commit:str,stage4:str,version:str)->dict:
    return {
        "schema":1,
        "project":"DARK XRAY",
        "version":version,
        "release_commit":commit,
        "release_tree":tree_sha(commit),
        "stage4_accepted_commit":stage4,
        "source_sha256sums_sha256":hashlib.sha256(blob(commit,"SHA256SUMS")).hexdigest(),
    }


def archive(commit:str,prefix:str,fmt:str,extras:dict[str,str])->bytes:
    args=["archive","--format="+fmt,"--prefix="+prefix]
    for rel,content in sorted(extras.items()):
        args.append("--add-virtual-file="+prefix+rel+":"+content)
    args.append(commit)
    return git(*args,binary=True)


def atomic_write(path:Path,data:bytes,mode:int=0o644)->None:
    path.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(prefix="."+path.name+".",dir=path.parent)
    try:
        with os.fdopen(fd,"wb") as f:
            f.write(data);f.flush();os.fsync(f.fileno())
        os.chmod(tmp,mode);os.replace(tmp,path)
    finally:
        try:os.unlink(tmp)
        except FileNotFoundError:pass


def sha(path:Path)->str:
    h=hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda:f.read(1024*1024),b""):h.update(chunk)
    return h.hexdigest()


def repo_check()->None:
    tool=ROOT/"tools/repo-check.py"
    cp=subprocess.run([os.environ.get("PYTHON","python3"),str(tool)],cwd=ROOT,
                      stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
    if cp.returncode:
        raise ReleaseError("repository pre-publication check failed: "+cp.stdout[-1200:])


def build(commit:str,stage4:str,output:Path)->dict:
    commit=exact_commit(commit);stage4=exact_commit(stage4)
    if head_commit()!=commit:raise ReleaseError("HEAD must equal the exact release commit")
    clean_worktree();repo_check()
    drift=runtime_drift(stage4,commit)
    if drift:
        raise ReleaseError("runtime drift from Stage 4 requires acceptance rerun: "+", ".join(drift[:20]))
    verify_source_sums(commit);ls_tree(commit)

    version=blob(commit,"VERSION").decode("utf-8","strict").strip()
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?",version):
        raise ReleaseError("invalid VERSION in release commit")
    short=commit[:12];prefix=f"dark-xray-{version}/";stem=f"dark-xray-{version}-{short}"
    output=output.resolve()
    try:output.relative_to(ROOT.resolve())
    except ValueError:pass
    else:raise ReleaseError("release output directory must be outside the repository")

    identity=release_metadata(commit,stage4,version)
    identity_text=json.dumps(identity,indent=2,sort_keys=True)+"\n"
    extras={"DARK-RELEASE.json":identity_text,"release-install.py":RELEASE_INSTALLER}

    tar_path=output/(stem+".tar.gz");zip_path=output/(stem+".zip")
    tar_raw=archive(commit,prefix,"tar",extras)
    epoch=int(git("show","-s","--format=%ct",commit).strip())
    buf=tempfile.SpooledTemporaryFile(max_size=64*1024*1024)
    with gzip.GzipFile(fileobj=buf,mode="wb",compresslevel=9,mtime=epoch,filename="") as gz:gz.write(tar_raw)
    buf.seek(0);atomic_write(tar_path,buf.read());buf.close()
    atomic_write(zip_path,archive(commit,prefix,"zip",extras))

    artifacts={
        tar_path.name:{"sha256":sha(tar_path),"bytes":tar_path.stat().st_size},
        zip_path.name:{"sha256":sha(zip_path),"bytes":zip_path.stat().st_size},
    }
    changed=[x for x in git("diff","--name-only",stage4+".."+commit).splitlines() if x]
    when=dt.datetime.fromtimestamp(epoch,dt.timezone.utc).isoformat().replace("+00:00","Z")
    manifest={
        "schema":1,"project":"DARK XRAY","version":version,"release_commit":commit,
        "release_tree":tree_sha(commit),"stage4_accepted_commit":stage4,
        "stage4_runtime_equivalent":True,"release_only_changes":changed,
        "commit_time_utc":when,
        "source_sha256sums_sha256":identity["source_sha256sums_sha256"],
        "embedded_release_metadata_sha256":hashlib.sha256(identity_text.encode()).hexdigest(),
        "embedded_release_installer_sha256":hashlib.sha256(RELEASE_INSTALLER.encode()).hexdigest(),
        "artifacts":artifacts,
        "claim":"release snapshot artifact; Stage 5 install/update/rollback rehearsal is separate evidence",
    }
    manifest_path=output/(stem+".manifest.json");release_sums=output/"SHA256SUMS.release"
    atomic_write(manifest_path,(json.dumps(manifest,indent=2,sort_keys=True)+"\n").encode())
    atomic_write(release_sums,sums_bytes([
        (tar_path.name,tar_path.read_bytes()),(zip_path.name,zip_path.read_bytes()),
        (manifest_path.name,manifest_path.read_bytes())]))
    return manifest|{"manifest":str(manifest_path),"release_sha256sums":str(release_sums)}


def main()->int:
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--commit",help="exact 40-character release commit")
    ap.add_argument("--stage4-commit",default=DEFAULT_STAGE4)
    ap.add_argument("--output",type=Path,default=Path("/tmp/dark-xray-release"))
    ap.add_argument("--refresh-source-sums",action="store_true")
    ap.add_argument("--json-only",action="store_true")
    a=ap.parse_args()
    try:
        if a.refresh_source_sums:
            result={"refreshed":True,"path":str(refresh_source_sums())}
        else:
            if not a.commit:raise ReleaseError("--commit is required unless --refresh-source-sums is used")
            result=build(a.commit,a.stage4_commit,a.output)
    except ReleaseError as ex:
        if a.json_only:print(json.dumps({"passed":False,"error":str(ex)}))
        else:print("RELEASE SNAPSHOT FAIL:",ex)
        return 1
    if a.json_only:print(json.dumps({"passed":True,**result},indent=2))
    else:
        print("RELEASE SNAPSHOT PASS")
        print(json.dumps(result,indent=2))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
