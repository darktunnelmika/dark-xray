"""Encrypted, consistent standalone backups. No restore into nonempty targets.

The bundle contains the SQLite database, MFA encryption key, panel configuration
and optional panel TLS pair. TLS certificate/key files referenced by managed
inbounds are copied into the encrypted archive and their database paths are
rewritten on restore. Xray binary, systemd units and the root guard allowlist
remain rebuildable host artifacts.
"""
from __future__ import annotations
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import time
import zipfile
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from dark_policy import PolicyError

MAGIC = b'DARK-XRAY-BACKUP-1\x00'
LIMIT = 256 * 1024 * 1024
ALLOWED_BASE = {'manifest.json', 'data/dark.sqlite3', 'data/secret.key', 'config.json', 'tls/cert.pem', 'tls/key.pem'}
ROOT = Path(__file__).resolve().parents[1]

def project_version() -> str:
    path=ROOT/'VERSION'
    try:value=path.read_text(encoding='utf-8').strip()
    except OSError:return 'unknown'
    return value if value and len(value)<=128 else 'unknown'

def derive(password: str, salt: bytes) -> bytes:
    if not isinstance(password, str) or len(password) < 12:
        raise PolicyError('Backup passphrase must contain at least 12 characters')
    return hashlib.scrypt(password.encode(), salt=salt, n=32768, r=8, p=1, dklen=32, maxmem=64*1024*1024)

def private_write(path: Path, raw: bytes):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'wb') as f:
        f.write(raw); f.flush(); os.fsync(f.fileno())

def safe_member(name: str) -> bool:
    if name in ALLOWED_BASE:return True
    parts=name.split('/')
    return (len(parts)==3 and parts[0]=='inbound-tls' and len(parts[1])==64
            and all(c in '0123456789abcdef' for c in parts[1])
            and parts[2] in {'certificate.pem','private-key.pem'})

def inbound_tls_files(snapshot: Path) -> tuple[dict[str,bytes],dict[str,str]]:
    """Collect only TLS files actually referenced by the consistent DB snapshot."""
    extra={};mapping={}
    try:db=sqlite3.connect(snapshot.resolve().as_uri()+'?mode=ro',uri=True,timeout=10)
    except sqlite3.Error as ex:raise PolicyError('Cannot inspect inbound TLS references') from ex
    try:
        exists=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='core_inbounds'").fetchone()
        rows=db.execute('SELECT body FROM core_inbounds').fetchall() if exists else []
        refs=[]
        for (raw,) in rows:
            try:doc=json.loads(raw)
            except Exception as ex:raise PolicyError('Inbound record is invalid while building backup') from ex
            st=doc.get('streamSettings',{}) if isinstance(doc,dict) else {}
            tls=st.get('tlsSettings',{}) if isinstance(st,dict) else {}
            certs=tls.get('certificates',[]) if isinstance(tls,dict) else []
            if not isinstance(certs,list):continue
            for cert in certs:
                if not isinstance(cert,dict):continue
                for key,leaf in (('certificateFile','certificate.pem'),('keyFile','private-key.pem')):
                    value=cert.get(key)
                    if isinstance(value,str) and value.strip():refs.append((value.strip(),leaf))
        for original,leaf in refs:
            field='certificateFile' if leaf=='certificate.pem' else 'keyFile'
            map_key=field+'\n'+original
            if map_key in mapping:continue
            path=Path(original)
            try:resolved=path.resolve(strict=True)
            except OSError as ex:raise PolicyError('Referenced inbound TLS file is missing: '+original) from ex
            if not resolved.is_file() or resolved.stat().st_size>1024*1024:
                raise PolicyError('Referenced inbound TLS file is unsafe or too large: '+original)
            digest=hashlib.sha256((field+'\0'+original).encode()).hexdigest()
            member='inbound-tls/'+digest+'/'+leaf
            extra[member]=resolved.read_bytes();mapping[map_key]=member
        return extra,mapping
    finally:db.close()

def rewrite_inbound_tls_paths(dbpath: Path, mapping: dict[str,str], destination: Path):
    if not mapping:return
    db=sqlite3.connect(dbpath)
    try:
        exists=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='core_inbounds'").fetchone()
        if not exists:return
        for rowid,raw in db.execute('SELECT id,body FROM core_inbounds').fetchall():
            doc=json.loads(raw);changed=False
            st=doc.get('streamSettings',{}) if isinstance(doc,dict) else {}
            tls=st.get('tlsSettings',{}) if isinstance(st,dict) else {}
            certs=tls.get('certificates',[]) if isinstance(tls,dict) else []
            if not isinstance(certs,list):continue
            for cert in certs:
                if not isinstance(cert,dict):continue
                for key in ('certificateFile','keyFile'):
                    original=cert.get(key)
                    map_key=key+'\n'+str(original)
                    if original and map_key in mapping:
                        cert[key]=str(destination/mapping[map_key]);changed=True
            if changed:db.execute('UPDATE core_inbounds SET body=? WHERE id=?',(json.dumps(doc),rowid))
        db.commit()
    except (sqlite3.Error,ValueError,TypeError) as ex:
        db.rollback();raise PolicyError('Cannot rewrite restored inbound TLS paths') from ex
    finally:db.close()

def prepare_telegram_disaster_recovery(db: sqlite3.Connection) -> dict:
    """Preserve Telegram business state but require a fresh bot identity after restore."""
    tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    result={'state_present':False,'bot_rows_reset':0,'forum_rows_preserved':0,
            'topic_rows_preserved':0,'commerce_tables_preserved':[],'token_required':False,
            'forum_rebind_required':False}
    if 'telegram_bots' in tables:
        cols={r[1] for r in db.execute('PRAGMA table_info(telegram_bots)')}
        assigns=[]
        for name,value in (
            ('enabled','0'),('token_enc',"''"),('update_offset','0'),('bot_username',"''"),
            ('last_error',"''"),('last_seen','0'),('forum_prompted_at','0')):
            if name in cols:assigns.append(name+'='+value)
        if 'updated_at' in cols:assigns.append('updated_at='+str(float(time.time())))
        count=int(db.execute('SELECT COUNT(*) FROM telegram_bots').fetchone()[0])
        if assigns and count:db.execute('UPDATE telegram_bots SET '+','.join(assigns))
        result.update(state_present=bool(count),bot_rows_reset=count,token_required=bool(count))
    if 'telegram_forums' in tables:
        cols={r[1] for r in db.execute('PRAGMA table_info(telegram_forums)')}
        if 'rebind_required' not in cols:
            db.execute("ALTER TABLE telegram_forums ADD COLUMN rebind_required INTEGER NOT NULL DEFAULT 0")
        if 'rebind_reason' not in cols:
            db.execute("ALTER TABLE telegram_forums ADD COLUMN rebind_reason TEXT NOT NULL DEFAULT ''")
        count=int(db.execute('SELECT COUNT(*) FROM telegram_forums').fetchone()[0])
        if count:
            db.execute("UPDATE telegram_forums SET rebind_required=1,rebind_reason='restored-backup',updated_at=?",
                       (time.time(),))
        result.update(state_present=result['state_present'] or bool(count),
                      forum_rows_preserved=count,forum_rebind_required=bool(count))
    if 'telegram_forum_topics' in tables:
        result['topic_rows_preserved']=int(db.execute('SELECT COUNT(*) FROM telegram_forum_topics').fetchone()[0])
    for table in ('commerce_products','commerce_prices','commerce_gateways','commerce_orders','commerce_payments'):
        if table in tables:result['commerce_tables_preserved'].append(table)
    return result

def create_backup(data: Path, config: Path, output: Path, password: str) -> dict:
    data,config,output=Path(data),Path(config),Path(output)
    # These are privileged runtime inputs. Refuse indirection before resolve() so
    # a replaced config/database symlink cannot smuggle unrelated host files into
    # a backup archive.
    if data.is_symlink() or config.is_symlink():raise PolicyError('DARK data/config symlinks are not accepted for backup')
    data,config,output=data.resolve(),config.resolve(),output.absolute()
    if not data.is_dir() or not config.is_file() or config.stat().st_size>1024*1024:
        raise PolicyError('DARK data/config paths are missing or unsafe')
    if output.exists() or output.is_symlink():
        raise PolicyError('Backup target already exists')
    dbfile, secret = data/'dark.sqlite3', data/'secret.key'
    if dbfile.is_symlink() or secret.is_symlink() or not dbfile.is_file() or not secret.is_file():
        raise PolicyError('An initialized non-symlink DARK database and original secret.key are required')
    try:cfg=json.loads(config.read_text(encoding='utf-8'))
    except (OSError,UnicodeError,json.JSONDecodeError) as ex:raise PolicyError('Panel configuration is not valid JSON') from ex
    if not isinstance(cfg,dict):raise PolicyError('Panel configuration must be a JSON object')
    files = {'config.json': config.read_bytes(), 'data/secret.key': secret.read_bytes()}
    with tempfile.TemporaryDirectory() as temp:
        snapshot = Path(temp)/'snapshot.sqlite3'
        source = sqlite3.connect(dbfile.resolve().as_uri()+'?mode=ro', uri=True, timeout=30)
        dest = sqlite3.connect(snapshot)
        try:
            source.backup(dest)
            if dest.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                raise PolicyError('Database consistency check failed')
        except sqlite3.Error as ex:
            raise PolicyError('Database snapshot could not be created safely') from ex
        finally:
            dest.close(); source.close()
        if snapshot.stat().st_size > LIMIT:
            raise PolicyError('Database exceeds this backup utility limit of 256 MiB')
        external_files,external_map=inbound_tls_files(snapshot)
        files['data/dark.sqlite3'] = snapshot.read_bytes()
        files.update(external_files)
    for key, name in [('tls_certificate','tls/cert.pem'),('tls_private_key','tls/key.pem')]:
        if cfg.get(key):
            path = Path(cfg[key])
            if not path.is_file() or path.stat().st_size > 1024*1024:
                raise PolicyError('Configured panel TLS file missing or too large')
            files[name] = path.read_bytes()
    manifest = {'schema': 2, 'project': 'DARK XRAY', 'version': project_version(),
                'files': {name: hashlib.sha256(raw).hexdigest() for name,raw in files.items()},
                'external_files': external_map,
                'telegram_disaster_recovery':{
                    'business_state_in_database':True,
                    'bot_token_reset_on_restore':True,
                    'forum_and_topics_preserved':True,
                    'forum_rebind_required_after_restore':True},
                'excluded': ['Xray binary and geo assets','root firewall allowlist','systemd service definitions',
                             'Node Agent host TLS (reissued when a disposable node is reinstalled)'],
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
    archive,destination=Path(archive),Path(destination)
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
            if len(names) != len(set(names)) or not all(safe_member(n) for n in names):
                raise PolicyError('Unsafe or duplicate backup members')
            if sum(i.file_size for i in z.infolist()) > LIMIT:
                raise PolicyError('Decompressed backup exceeds 256 MiB')
            files = {n:z.read(n) for n in names}
    except (zipfile.BadZipFile,KeyError) as exc:
        raise PolicyError('Invalid backup contents') from exc
    try:
        manifest = json.loads(files['manifest.json'])
        if not isinstance(manifest,dict) or manifest.get('project') != 'DARK XRAY' or manifest.get('schema') not in (1,2):
            raise PolicyError('Unknown backup schema')
        manifest_files=manifest.get('files')
        excluded=manifest.get('excluded')
        external_map=manifest.get('external_files',{}) if manifest.get('schema')==2 else {}
        if not isinstance(manifest_files,dict) or not isinstance(excluded,list) or not all(isinstance(x,str) for x in excluded):
            raise PolicyError('Invalid backup manifest')
        if not isinstance(external_map,dict) or any(not isinstance(k,str) or not isinstance(v,str) or not safe_member(v) for k,v in external_map.items()):
            raise PolicyError('Invalid inbound TLS backup mapping')
        if any(v not in manifest_files for v in external_map.values()):raise PolicyError('Inbound TLS mapping references a missing member')
        if set(manifest_files) != set(files)-{'manifest.json'}:
            raise PolicyError('Incomplete backup manifest')
        for name, expected in manifest_files.items():
            if not isinstance(expected,str) or hashlib.sha256(files[name]).hexdigest() != expected:
                raise PolicyError('Backup hash mismatch')
        for name in ('data/dark.sqlite3','data/secret.key','config.json'):
            if not files[name]:raise PolicyError('Required backup member empty')
        cfg = json.loads(files['config.json'])
        if not isinstance(cfg,dict):raise PolicyError('Panel configuration must be a JSON object')
    except (KeyError,ValueError,TypeError,AttributeError) as exc:
        raise PolicyError('Invalid required backup members') from exc
    dest.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.TemporaryDirectory(prefix='.dark-restore-',dir=dest.parent) as tmp:
        staging = Path(tmp)/'staging';staging.mkdir(mode=0o700)
        for name, content in files.items():
            out = staging/name;out.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
            private_write(out, content)
        try:db = sqlite3.connect(staging/'data/dark.sqlite3')
        except sqlite3.Error as ex:raise PolicyError('Restored database cannot be opened') from ex
        try:
            if db.execute('PRAGMA quick_check').fetchone()[0] != 'ok':raise PolicyError('Restored database is invalid')
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not {'clients','owners','api_admins','core_clients'} <= tables:raise PolicyError('Not a standalone DARK database')
            # Stolen old session cookies must not survive recovery. Older lab
            # snapshots without live_sessions remain restorable.
            if 'live_sessions' in tables:db.execute('DELETE FROM live_sessions')
            telegram_recovery=prepare_telegram_disaster_recovery(db)
            db.commit()
        except sqlite3.Error as ex:raise PolicyError('Restored database validation failed') from ex
        finally:db.close()
        rewrite_inbound_tls_paths(staging/'data/dark.sqlite3',external_map,dest)
        for key,name in [('tls_certificate','tls/cert.pem'),('tls_private_key','tls/key.pem')]:
            if name in files:cfg[key]=str(dest/name)
        cfg['core_autostart'] = False
        (staging/'config.json').write_text(json.dumps(cfg,indent=2),encoding='utf-8')
        os.rename(staging,dest)
    return {'restored':True,'destination':str(dest),'core_autostart':False,
            'sessions_revoked':True,'mfa_key_restored':True,'telegram_recovery':telegram_recovery,
            'new_bot_token_required':bool(telegram_recovery.get('token_required')),
            'forum_rebind_required':bool(telegram_recovery.get('forum_rebind_required')),
            'excluded':excluded,'backup_version':manifest.get('version','unknown')}