#!/usr/bin/env python3
from pathlib import Path
import hashlib,json,subprocess

ROOT=Path(__file__).resolve().parents[1]
NEW='0.9.0-rc1'
OLD='0.8.2-standalone-lab'

def replace(path:str, old:str, new:str, required=True):
    p=ROOT/path
    s=p.read_text(encoding='utf-8')
    if required and old not in s:
        raise SystemExit(f'anchor missing in {path}: {old[:80]}')
    p.write_text(s.replace(old,new),encoding='utf-8')

# Version + validation doc path
(ROOT/'VERSION').write_text(NEW+'\n',encoding='utf-8')
oldv=ROOT/'docs/VALIDATION-GATES.md'; newv=ROOT/'docs/VALIDATION.md'
if oldv.exists() and not newv.exists(): oldv.rename(newv)
if not newv.exists(): raise SystemExit('validation document missing')

# README Persian
replace('README.md', f'**نسخه فعلی:** `{OLD}`', f'**نسخه آزمایشی برای تست VPS:** `{NEW}`')
replace('README.md', 'DARK XRAY هنوز **Production Ready اعلام نشده**. بخش بزرگی از رفتار Runtime در CI با مرورگر واقعی، Xray رسمی، nftables واقعی و systemd واقعی تست می‌شود؛ اما تست روی VPS هدف، reboot واقعی ماشین، TLS/renewal دیتاسنتر موردنظر، دو VPS واقعی Node و Load/Scale هنوز گیت انتشار هستند.',
        'DARK XRAY در وضعیت **Release Candidate** است و هنوز Production Ready اعلام نشده. Browser، Xray رسمی، nftables packet-level، systemd recovery و Load/Scale هزار کلاینت در CI سبزند؛ اما نصب روی VPS هدف، reboot واقعی ماشین، TLS/renewal دیتاسنتر موردنظر و دو VPS واقعی Node هنوز گیت نهایی هستند.')
replace('README.md', '- Load/Scale اندازه‌گیری‌شده برای تعداد Client/Inbound موردنظر و contention دیتابیس.', '- ظرفیت‌سنجی روی سخت‌افزار/پلن VPS هدف؛ CI فعلی smoke هزار Client و SQLite contention را پاس کرده است.')
replace('README.md', 'بنابراین برچسب پروژه فعلاً **`standalone-lab`** می‌ماند.', f'بنابراین این build با برچسب **`{NEW}`** برای تست VPS منتشر می‌شود؛ Production Ready بعد از گیت‌های واقعی هدف اعلام خواهد شد.')
replace('README.md', '`SHA256SUMS` فقط باید برای release/tag نهایی و ثابت دوباره تولید شود. برای `main` متحرک، CI و commit SHA منبع وضعیت هستند.', '`SHA256SUMS` این شاخه برای همین RC بازتولید شده است؛ checksum آرشیوهای Release نیز کنار assetهای همان tag منتشر می‌شود.')

# README English
replace('README.en.md', f'**Current line:** `{OLD}`', f'**VPS test candidate:** `{NEW}`')
replace('README.en.md', 'DARK XRAY is **not declared production-ready yet**. The repository now exercises substantial runtime behavior with a real browser, official Xray, real Linux nftables packet paths and real systemd recovery. Target-VPS installation, an actual machine reboot, provider-specific TLS renewal, two-VPS node validation and measured production load still remain release gates.',
        'DARK XRAY is now a **Release Candidate**, not a production-ready declaration. Real Chromium, official Xray, packet-level nftables, systemd crash recovery and a 1000-client/SQLite contention smoke are green in CI; target-VPS install, real machine reboot, provider TLS renewal and two-VPS node validation remain final gates.')
replace('README.en.md', '- measured load/scale testing for the intended client/inbound counts and SQLite concurrency;', '- capacity validation on the intended VPS plan; CI already passes a 1000-client and SQLite-contention smoke, but that is not a provider capacity guarantee;')
replace('README.en.md', 'The project intentionally remains `standalone-lab` until the intended deployment environment passes:', f'This `{NEW}` candidate remains pre-production until the intended deployment environment passes:')
replace('README.en.md', '`SHA256SUMS` must be regenerated for the exact stabilized release/tag. For a moving `main`, use CI and the exact commit SHA as the source of truth.', '`SHA256SUMS` is regenerated for this RC snapshot; release-archive checksums are published with the matching prerelease assets.')

# Persian detailed guide
replace('README.fa.md', '# راهنمای DARK XRAY 0.8.2', '# راهنمای DARK XRAY 0.9.0 RC1')
replace('README.fa.md', f'برچسب فعلی: **`{OLD}`**', f'برچسب فعلی: **`{NEW}`**')
replace('README.fa.md', 'این شاخه هنوز **Lab** است. CI اکنون با Xray رسمی، nftables واقعی، systemd واقعی و Chromium واقعی تست دارد، اما Production Ready فقط زمانی اعلام می‌شود که VPS هدف، reboot واقعی ماشین، TLS renewal واقعی، Nodeهای چندسروری و Load/Scale هم تأیید شوند.',
        'این نسخه **Release Candidate** برای تست VPS است. CI با Xray رسمی، nftables واقعی، systemd واقعی، Chromium واقعی و Load/Scale هزار Client سبز است؛ Production Ready فقط بعد از VPS هدف، reboot واقعی، TLS renewal واقعی و Nodeهای چندسروری اعلام می‌شود.')
replace('README.fa.md', '6. Load/Scale متناسب با تعداد Client/Inbound موردنظر و SQLite concurrency.', '6. ظرفیت‌سنجی روی پلن واقعی VPS؛ smoke هزار Client و SQLite contention در CI قبلاً سبز شده است.')

# STATUS
replace('STATUS.fa.md', '# وضعیت DARK XRAY 0.8.2', '# وضعیت DARK XRAY 0.9.0 RC1')
replace('STATUS.fa.md', f'برچسب فعلی: **`{OLD}`**', f'برچسب فعلی: **`{NEW}`**')
replace('STATUS.fa.md', '| Python | 3.12 و 3.13 کامل |', '| Python | 3.12 و 3.13 کامل |\n| Load / Scale CI | 1000 Client + SQLite contention smoke سبز |')
replace('STATUS.fa.md', '6. **Load/Scale** با تعداد Client/Inbound هدف، polling، Bulk و SQLite contention اندازه‌گیری‌شده.', '6. **Capacity روی VPS هدف**؛ smoke هزار Client/SQLite contention در CI سبز است ولی ظرفیت provider/hardware باید روی مقصد اندازه‌گیری شود.')
replace('STATUS.fa.md', '8. **Release finalization**: VERSION/CHANGELOG/README و تولید مجدد `SHA256SUMS` برای همان tag ثابت.', '8. **Stable promotion** بعد از پاس‌شدن گیت‌های VPS واقعی همین RC.')
replace('STATUS.fa.md', 'مرحله بعد از این hardening، **Target VPS Validation + TLS/Node/Load evidence** و سپس Release Candidate ثابت است. هر failure جدید باید قبل از Release به regression test تبدیل شود.', f'مرحله بعد، تست **`{NEW}`** روی VPS هدف، TLS/Node/Reboot واقعی و سپس promotion همان کاندید به Stable است. هر failure جدید باید قبل از Stable به regression test تبدیل شود.')

# Changelog: prepend RC entry
p=ROOT/'CHANGELOG.fa.md'; s=p.read_text(encoding='utf-8')
entry=f'''# تغییرات DARK XRAY\n\n## {NEW} — Release Candidate برای تست VPS\n\n- همه Gateهای CI روی Python 3.12/3.13، Chromium، Xray رسمی، nftables packet-level و systemd recovery سبز هستند.\n- Load/Scale smoke با 1000 Client و SQLite contention به Gate اصلی اضافه و سبز شده است.\n- CLI در برابر `PYTHONHOME/PYTHONPATH` خارجی harden شده تا virtualenv نصب‌شده منحرف نشود.\n- `kernel-firewall` شامل TCP/UDP drop، management-port preservation، nft timeout، unban و foreign-table ownership refusal است.\n- `systemd-recovery` شامل `vps-verify`، SIGKILL→Restart=on-failure، stop/start و تک‌بودن Xray child است.\n- مستندات، Validation path، VERSION و checksum snapshot برای RC یکدست شده‌اند.\n- این نسخه Production Ready اعلام نمی‌شود تا VPS هدف، reboot واقعی، TLS renewal و دو VPS Node واقعی پاس شوند.\n\n'''
if s.startswith('# تغییرات DARK XRAY\n\n'):
    s=entry+s[len('# تغییرات DARK XRAY\n\n'):]
else: raise SystemExit('changelog header mismatch')
p.write_text(s.replace('`docs/VALIDATION.md`','`docs/VALIDATION.md`'),encoding='utf-8')

# PUBLISH-STATUS structured update
p=ROOT/'PUBLISH-STATUS.json'; data=json.loads(p.read_text(encoding='utf-8'))
data['status']='release_candidate_target_vps_validation'
data['current_version']=NEW
data['release_candidate']=True
data['production_ready']=False
data['ci']['gates']['load_scale_1000_clients_sqlite_contention']=True
data['target_infrastructure_validation']['production_load_and_sqlite_contention']=False
data['target_infrastructure_validation']['ci_load_scale_1000_clients_complete']=True
data['release_hygiene']={
    'version_file_current':True,
    'readmes_current_for_rc':True,
    'status_current_for_rc':True,
    'validation_doc_path_current':True,
    'source_sha256sums_regenerated_for_rc':True,
    'release_archive_sha256sums_generated_with_prerelease':False
}
data['checksum_note']='Repository SHA256SUMS is regenerated for the RC source snapshot. Release archive checksums are generated when the v0.9.0-rc1 prerelease assets are built.'
p.write_text(json.dumps(data,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')

# Validation wording
p=newv; s=p.read_text(encoding='utf-8')
s=s.replace('before removing the `standalone-lab` label.', f'before promoting `{NEW}` to a stable production release.')
p.write_text(s,encoding='utf-8')

# Retire obsolete initial-upload guide; create RC testing guide.
old_upload=ROOT/'UPLOAD.fa.md'
if old_upload.exists(): old_upload.unlink()
(ROOT/'TESTING-RC.fa.md').write_text(f'''# تست DARK XRAY {NEW}\n\nاین نسخه برای تست روی VPS واقعی آماده شده و هنوز Stable/Production Ready اعلام نشده است.\n\n## نصب دقیق همین RC\n\n```bash\ncurl -fL --retry 3 https://raw.githubusercontent.com/darktunnelmika/dark-xray/v{NEW}/install-online.sh -o /tmp/dark-xray-install.sh\nsudo DARK_XRAY_REF=v{NEW} bash /tmp/dark-xray-install.sh\n```\n\nبعد از نصب:\n\n```bash\nsudo darkxray vps-verify\nsudo darkxray production-gate\n```\n\n## تست reboot واقعی\n\n```bash\nsudo reboot\n```\n\nبعد از اتصال مجدد:\n\n```bash\nsystemctl status dark-xray.service --no-pager -l\nsudo darkxray vps-verify\n```\n\n## چیزهایی که نتیجه‌شان را ثبت کن\n\n- نصب Fresh روی Ubuntu واقعی\n- ورود پنل و ساخت Inbound/Client\n- اتصال واقعی Client\n- reboot و بازگشت Panel/Xray\n- Domain/TLS و renewal\n- IP Guard روی topology واقعی\n- Node دوم روی VPS مستقل\n- Update/Rollback همین RC\n\nاگر یکی از این مراحل fail شد، log همان بخش را نگه دار؛ credential/private key را ارسال نکن.\n''',encoding='utf-8')

# Stage renames/deletions before enumerating tracked files.
subprocess.run(['git','add','-A'],cwd=ROOT,check=True)
exclude={'SHA256SUMS','.github/workflows/release-prep-once.yml','tools/release_prep_once.py'}
raw=subprocess.check_output(['git','ls-files','-z'],cwd=ROOT)
files=[x.decode() for x in raw.split(b'\0') if x and x.decode() not in exclude]
lines=[]
for name in sorted(files):
    fp=ROOT/name
    if not fp.is_file() or fp.is_symlink(): continue
    h=hashlib.sha256(fp.read_bytes()).hexdigest()
    lines.append(f'{h}  {name}')
(ROOT/'SHA256SUMS').write_text('\n'.join(lines)+'\n',encoding='utf-8')

# Final sanity
assert (ROOT/'VERSION').read_text().strip()==NEW
assert (ROOT/'docs/VALIDATION.md').is_file()
assert not (ROOT/'docs/VALIDATION-GATES.md').exists()
assert '1000 Client' in (ROOT/'STATUS.fa.md').read_text(encoding='utf-8')
print('RC prep complete:',NEW,'checksummed files:',len(lines))
