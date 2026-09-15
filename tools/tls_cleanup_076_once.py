#!/usr/bin/env python3
from pathlib import Path


def once(path,old,new):
    p=Path(path);s=p.read_text()
    if old not in s:raise SystemExit(f'anchor missing in {path}: {old[:150]!r}')
    p.write_text(s.replace(old,new,1))

# settings_apply: retiring public TLS must retire the executable Certbot hook.
once('tools/settings_apply.py',
"""GUARD_PATH = Path('/etc/dark-xray/guard.json')
""",
"""GUARD_PATH = Path('/etc/dark-xray/guard.json')
TLS_SOURCE_PATH = Path('/etc/dark-xray/tls-source.json')
TLS_RENEW_HOOK = Path('/etc/letsencrypt/renewal-hooks/deploy/dark-xray-panel')
""")
once('tools/settings_apply.py',
"""def _service_active(name:str)->bool:
""",
"""def _validate_tls_cleanup_targets(source:Path=TLS_SOURCE_PATH,hook:Path=TLS_RENEW_HOOK)->None:
    for path in (source,hook):
        if path.exists() and path.is_dir() and not path.is_symlink():
            raise SystemExit('Refusing TLS cleanup: expected file path is a directory: '+str(path))


def _deactivate_tls_renewal(source:Path=TLS_SOURCE_PATH,hook:Path=TLS_RENEW_HOOK)->None:
    _validate_tls_cleanup_targets(source,hook)
    for path in (hook,source):
        if path.exists() or path.is_symlink():path.unlink()


def _service_active(name:str)->bool:
""")
once('tools/settings_apply.py',
"""    if desired['access_mode']=='ssh' or plan['tls_ready']:
        candidate,guard_candidate=_candidate_from_desired(current,desired,plan)
        _activate_candidate(config_path,current,candidate,guard_candidate);return
""",
"""    if desired['access_mode']=='ssh' or plan['tls_ready']:
        if desired['access_mode']=='ssh':_validate_tls_cleanup_targets()
        candidate,guard_candidate=_candidate_from_desired(current,desired,plan)
        _activate_candidate(config_path,current,candidate,guard_candidate)
        if desired['access_mode']=='ssh':_deactivate_tls_renewal()
        return
""")

# domain renewal: stale SOURCE is not authorization to restart a panel that no
# longer uses that TLS domain.
once('tools/domain.py',
"""from pathlib import Path
""",
"""from pathlib import Path
from urllib.parse import urlsplit
""")
once('tools/domain.py',
"""def _renew():
    state=json.loads(SOURCE.read_text());lineage=Path(state['lineage']);snapshot=_pair_snapshot()
""",
"""def _renewal_source_active(state:dict,config:dict)->bool:
    try:origin=urlsplit(str(config.get('public_origin','')))
    except Exception:return False
    return bool(origin.scheme=='https' and origin.hostname==state.get('domain') and
                str(config.get('tls_certificate',''))==str(TLS_DIR/'cert.pem') and
                str(config.get('tls_private_key',''))==str(TLS_DIR/'key.pem'))


def _renew():
    if not SOURCE.is_file() or SOURCE.is_symlink():raise SystemExit('No trusted active DARK TLS renewal source is configured')
    state=json.loads(SOURCE.read_text());config=json.loads((CONF/'config.json').read_text())
    if not _renewal_source_active(state,config):raise SystemExit('Stored TLS renewal source is not the active DARK panel TLS domain')
    lineage=Path(state['lineage']);snapshot=_pair_snapshot()
""")

# Uninstall application removes only the executable renewal hook. /etc config,
# cert files and data remain deliberately preserved.
once('tools/menu.py',
"""            if confirm('Remove application/services but KEEP /etc/dark-xray and /var/lib/dark-xray?','UNINSTALL'):
                run(['systemctl','disable','--now','dark-xray.service']);run(['systemctl','disable','--now','dark-xray-guard.service'])
                for p in ('/etc/systemd/system/dark-xray.service','/etc/systemd/system/dark-xray-guard.service','/usr/local/bin/darkxray'):Path(p).unlink(missing_ok=True)
                shutil.rmtree('/opt/dark-xray',ignore_errors=True);run(['systemctl','daemon-reload']);print('Application removed; config/data preserved.');raise SystemExit(0)
""",
"""            if confirm('Remove application/services but KEEP /etc/dark-xray and /var/lib/dark-xray?','UNINSTALL'):
                run(['systemctl','disable','--now','dark-xray.service']);run(['systemctl','disable','--now','dark-xray-guard.service'])
                for p in ('/etc/systemd/system/dark-xray.service','/etc/systemd/system/dark-xray-guard.service','/usr/local/bin/darkxray'):
                    Path(p).unlink(missing_ok=True)
                hook=Path('/etc/letsencrypt/renewal-hooks/deploy/dark-xray-panel')
                if hook.exists() or hook.is_symlink():hook.unlink()
                shutil.rmtree('/opt/dark-xray',ignore_errors=True);run(['systemctl','daemon-reload']);print('Application removed; config/data/certificates preserved; DARK renewal hook removed.');raise SystemExit(0)
""")

# Tests for safe TLS lifecycle helpers.
p=Path('tests/test_domain_tool.py');s=p.read_text();s += r'''


def test_renewal_source_must_match_active_https_config(tmp_path):
    mod=load_module();old=mod.TLS_DIR;mod.TLS_DIR=tmp_path/'tls'
    try:
        state={'domain':'panel.example.com','lineage':'/x'}
        good={'public_origin':'https://panel.example.com:2087','tls_certificate':str(mod.TLS_DIR/'cert.pem'),'tls_private_key':str(mod.TLS_DIR/'key.pem')}
        assert mod._renewal_source_active(state,good) is True
        assert mod._renewal_source_active(state,good|{'public_origin':'http://127.0.0.1:2087'}) is False
        assert mod._renewal_source_active(state,good|{'public_origin':'https://old.example.com:2087'}) is False
    finally:mod.TLS_DIR=old
''';p.write_text(s)

p=Path('tests/test_runtime_apply.py');s=p.read_text();s += r'''


def test_tls_renewal_metadata_cleanup_removes_only_files(tmp_path):
    mod=load_module();source=tmp_path/'tls-source.json';hook=tmp_path/'dark-xray-panel'
    source.write_text('{}');hook.write_text('#!/bin/sh\n')
    mod._deactivate_tls_renewal(source,hook)
    assert not source.exists() and not hook.exists()


def test_tls_renewal_cleanup_refuses_directory_shape(tmp_path):
    mod=load_module();source=tmp_path/'source-dir';hook=tmp_path/'hook';source.mkdir()
    try:mod._validate_tls_cleanup_targets(source,hook);assert False
    except SystemExit as exc:assert 'expected file path is a directory' in str(exc)
''';p.write_text(s)

Path('VERSION').write_text('0.7.6-standalone-lab\n')
print('0.7.6 TLS lifecycle patch applied')
