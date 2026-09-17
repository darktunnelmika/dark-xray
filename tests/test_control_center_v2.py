import importlib.util,sqlite3,json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
SPEC=importlib.util.spec_from_file_location("dark_menu_control_v2",ROOT/"tools/menu.py")
assert SPEC and SPEC.loader
MENU=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(MENU)

def test_protected_ports_uses_configured_ssh_and_core_ports():
    assert MENU.protected_ports({"xray_api_port":10085,"protected_ports":[22,2222,2087]})=={22,2222,2087,10085}

def test_runtime_stage_state_reports_pending(tmp_path,monkeypatch):
    monkeypatch.setattr(MENU,"DATA",tmp_path)
    db=tmp_path/"dark.sqlite3"
    with sqlite3.connect(db) as con:
        con.execute("CREATE TABLE core_sections(name TEXT PRIMARY KEY,body TEXT NOT NULL)")
        con.execute("INSERT INTO core_sections VALUES(?,?)",("runtime",json.dumps({"bind_port":9090,"panel_path":"/"})))
    c={"public_origin":"http://127.0.0.1:2087","bind_port":2087,"panel_path":"/","public_address":"127.0.0.1","xray_api_port":10085}
    assert MENU.runtime_stage_state(c)=="1 pending"

def test_bbr_refuses_unsupported_kernel_before_file_write(monkeypatch):
    monkeypatch.setattr(MENU,"need_root",lambda:True);monkeypatch.setattr(MENU,"exists",lambda name:True)
    class CP:
        returncode=0;stdout="reno cubic\n"
    monkeypatch.setattr(MENU,"run",lambda *a,**k:CP())
    assert MENU.safe_enable_bbr() is False

def test_control_center_routes_real_safety_commands():
    src=(ROOT/"tools/menu.py").read_text()
    assert "'nft','list','table','inet','dark_xray_ip'" in src
    assert "'guard-control','clear'" in src
    assert "'backup-verify'" in src and "'restore'" in src
    assert "'production-gate'" in src and "'vps-verify'" in src
    assert "'update','--ref',ref" in src
    assert "CPU {cpu}" not in src and "RAM {ram}" not in src
