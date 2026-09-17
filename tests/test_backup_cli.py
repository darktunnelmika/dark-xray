import importlib.util,sqlite3,sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
SPEC=importlib.util.spec_from_file_location("dark_backup_cli",ROOT/"tools/backup_cli.py")
assert SPEC and SPEC.loader
CLI=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(CLI)

def make_install(tmp_path):
    data=tmp_path/"data";data.mkdir();(data/"secret.key").write_bytes(b"x"*32)
    db=data/"dark.sqlite3"
    with sqlite3.connect(db) as con:
        for table in ("clients","owners","api_admins","core_clients"):con.execute(f"CREATE TABLE {table}(id TEXT)")
    config=tmp_path/"config.json";config.write_text("{}")
    return data,config

def test_backup_verify_restore_cli_roundtrip(tmp_path,monkeypatch):
    data,config=make_install(tmp_path);archive=tmp_path/"test.darkbackup";password="correct horse battery staple"
    monkeypatch.setattr(CLI,"new_secret",lambda:password)
    monkeypatch.setattr(sys,"argv",["backup_cli.py","backup","--config",str(config),"--data",str(data),"--output",str(archive)])
    assert CLI.main()==0 and archive.is_file()
    monkeypatch.setattr(CLI,"secret",lambda prompt:password)
    monkeypatch.setattr(sys,"argv",["backup_cli.py","verify","--archive",str(archive)])
    assert CLI.main()==0
    dest=tmp_path/"restore"
    monkeypatch.setattr(sys,"argv",["backup_cli.py","restore","--archive",str(archive),"--destination",str(dest)])
    assert CLI.main()==0 and (dest/"data/dark.sqlite3").is_file()
