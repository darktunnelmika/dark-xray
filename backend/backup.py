"""Encrypted, consistent standalone backups. No restore into nonempty targets.

The bundle contains the SQLite database, MFA encryption key, panel configuration
and optional panel TLS pair. Xray binary, external inbound certificates, systemd
units and the root guard allowlist are NOT silently claimed to be backed up.
"""
from __future__ import annotations
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import zipfile
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from dark_policy import PolicyError

MAGIC = b'DARK-XRAY-BACKUP-1\x00'
LIMIT = 256 * 1024 * 1024
ALLOWED = {'manifest.json', 'data/dark.sqlite3', 'data/secret.key', 'config.json', 'tls/cert.pem', 'tls/key.pem'}

def derive(password: str, salt: bytes) -> bytes:
    if not isinstance(password, str) or len(password) < 12:
        raise PolicyError('Backup passphrase must contain at least 12 characters')
    return hashlib.scrypt(password.encode(), salt=salt, n=32768, r=8, p=1, dklen=32, maxmem=64*1024*1024)

def private_write(path: Path, raw: bytes):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'wb') as f:
        f.write(raw); f.flush(); os.fsync(f.fileno())

def create_backup(data: Path, config: Path, output: Path, password: str) -> dict:
    data, config, output = data.resolve(), config.resolve(), output.absolute()
    if output.exists() or output.is_symlink():
        raise PolicyError('Backup target already exists')
    dbfile, secret = data/'dark.sqlite3', data/'secret.key'
    if not dbfile.is_file() or not secret.is_file() or secret.is_symlink():
        raise PolicyError('An initialized DARK database and original secret.key are required')
    cfg = json.loads(config.read_text())
    files = {'config.json': config.read_bytes(), 'data/secret.key': secret.read_bytes()}
    with tempfile.TemporaryDirectory() as temp:
        snapshot = Path(temp)/'snapshot.sqlite3'
        source = sqlite3.connect(dbfile.as_uri()+'?mode=ro', uri=True, timeout=30)
        dest = sqlite3.connect(snapshot)
        try:
            source.backup(dest)
            if dest.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                raise PolicyError('Database consistency check failed')
        finally:
            dest.close(); source.close()
        if snapshot.stat().st_size > LIMIT:
            raise PolicyError('Database exceeds this backup utility limit of 256 MiB')
        files['data/dark.sqlite3'] = snapshot.read_bytes()
    for key, name in [('tls_certificate','tls/cert.pem'),('tls_private_key','tls/key.pem')]:
        if cfg.get(key):
            path = Path(cfg[key])
            if not path.is_file() or path.stat().st_size > 1024*1024:
                raise PolicyError('Configured panel TLS file missing or too large')
            files[name] = path.read_bytes()
    manifest = {'schema': 1, 'project': 'DARK XRAY', 'version': '0.6.0',
                'files': {name: hashlib.sha256(raw).hexdigest() for name,raw in files.items()},
                'excluded': ['Xray binary and geo assets','external inbound certificates',
                             'root firewall allowlist','systemd service definitions'],
                'restore_into_empty_destination_only': True}
    files['manifest.json'] = json.dumps(manifest, ensure_ascii=False, indent=2).encode()
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as z:
        for name, raw in files.items():
            z.writestr(name, raw)
    if sum(len(raw) for raw in files.values())>LIMIT:raise PolicyError('Backup contents exceed the 256 MiB restore limit')
    salt, nonce = os.urandom(16), os.urandom(12)
    encrypted = AESGCM(derive(password, salt)).encrypt(nonce, stream.getvalue(), MAGIC)
    if len(MAGIC)+28+len(encrypted)>LIMIT:raise PolicyError('Encrypted archive exceeds the 256 MiB restore limit')
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    private_write(output, MAGIC + salt + nonce + encrypted)
    return manifest

def restore_backup(archive: Path, destination: Path, password: str) -> dict:
    if archive.is_symlink() or not archive.is_file() or archive.stat().st_size > LIMIT:
        raise PolicyError('Invalid backup archive')
    dest = destination.absolute()
    if dest.exists() or dest.is_symlink():
        raise PolicyError('Restore destination must not already exist; current installation is never overwritten')
    raw = archive.read_bytes()
    if not raw.startswith(MAGIC) or len(raw) < len(MAGIC)+44:
        raise PolicyError('Unknown encrypted backup format')
    offset = len(MAGIC)
    salt, nonce, payload = raw[offset:offset+16], raw[offset+16:offset+28], raw[offset+28:]
    try:
        plain = AESGCM(derive(password, salt)).decrypt(nonce, payload, MAGIC)
    except Exception as exc:
        raise PolicyError('Incorrect passphrase or corrupted encrypted backup') from exc
    try:
        with zipfile.ZipFile(io.BytesIO(plain)) as z:
            names = z.namelist()
            if len(names) != len(set(names)) or not set(names) <= ALLOWED:
                raise PolicyError('Unsafe or duplicate backup members')
            if sum(i.file_size for i in z.infolist()) > LIMIT:
                raise PolicyError('Decompressed backup exceeds 256 MiB')
            files = {n:z.read(n) for n in names}
    except (zipfile.BadZipFile,KeyError) as exc:
        raise PolicyError('Invalid backup contents') from exc
    try:
        manifest = json.loads(files['manifest.json'])
        if manifest.get('project') != 'DARK XRAY' or manifest.get('schema') != 1:
            raise PolicyError('Unknown backup schema')
        if set(manifest['files']) != set(files)-{'manifest.json'}:
            raise PolicyError('Incomplete backup manifest')
        for name, expected in manifest['files'].items():
            if hashlib.sha256(files[name]).hexdigest() != expected:
                raise PolicyError('Backup hash mismatch')
        for name in ('data/dark.sqlite3','data/secret.key','config.json'):
            if not files[name]:raise PolicyError('Required backup member empty')
        cfg = json.loads(files['config.json'])
    except (KeyError,ValueError,TypeError) as exc:
        raise PolicyError('Invalid required backup members') from exc
    dest.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.TemporaryDirectory(prefix='.dark-restore-',dir=dest.parent) as tmp:
        staging = Path(tmp)/'staging';staging.mkdir(mode=0o700)
        for name, content in files.items():
            out = staging/name;out.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
            private_write(out, content)
        db = sqlite3.connect(staging/'data/dark.sqlite3')
        try:
            if db.execute('PRAGMA quick_check').fetchone()[0] != 'ok':raise PolicyError('Restored database is invalid')
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not {'clients','owners','api_admins','core_clients'} <= tables:raise PolicyError('Not a standalone DARK database')
            # Stolen old session cookies must not survive recovery.
            db.execute('DELETE FROM live_sessions');db.commit()
        finally:db.close()
        for key,name in [('tls_certificate','tls/cert.pem'),('tls_private_key','tls/key.pem')]:
            if name in files:cfg[key]=str(dest/name)
        cfg['core_autostart'] = False
        (staging/'config.json').write_text(json.dumps(cfg,indent=2))
        os.rename(staging,dest)
    return {'restored':True,'destination':str(dest),'core_autostart':False,
            'sessions_revoked':True,'mfa_key_restored':True,'excluded':manifest['excluded']}
