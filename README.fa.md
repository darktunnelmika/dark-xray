# راهنمای DARK XRAY 0.9.0 RC1

برچسب فعلی: **`0.9.0-rc2`**

DARK XRAY یک پنل مستقل مدیریت Xray است. دیتابیس، API، احراز هویت، نماینده‌ها، Ledger، UI، Subscription، Node management و کنترل Xray متعلق به خود DARK هستند و برای Runtime به Sanayi/3x-ui وابسته نیستند.

> [!CAUTION]
> این نسخه **Release Candidate** برای تست VPS است. CI با Xray رسمی، nftables واقعی، systemd واقعی، Chromium واقعی و Load/Scale هزار Client سبز است؛ Production Ready فقط بعد از VPS هدف، reboot واقعی، TLS renewal واقعی و Nodeهای چندسروری اعلام می‌شود.

## نصب آنلاین

روی Ubuntu/Debian دارای systemd:

```bash
curl -fL --retry 3 https://raw.githubusercontent.com/darktunnelmika/dark-xray/main/install-online.sh -o /tmp/dark-xray-install.sh
sudo bash /tmp/dark-xray-install.sh
```

Installer سه وضعیت را تشخیص می‌دهد:

- **Clean** — نصب تازه.
- **Partial / Failed** — نگهداری بقایای نصب ناقص و Repair امن.
- **Installed** — Safe Update یا ورود به Manager.

مسیرهای اصلی نصب:

| مورد | مسیر |
|---|---|
| سورس و virtualenv | `/opt/dark-xray` |
| تنظیمات | `/etc/dark-xray/config.json` |
| دیتابیس، secret و runtime | `/var/lib/dark-xray` |
| Xray رسمی | `/usr/local/lib/dark-xray/<version>` |
| سرویس اصلی | `dark-xray.service` |
| IP Guard worker | `dark-xray-guard.service` |
| فرمان مدیریتی | `/usr/local/bin/darkxray` |

پنل اصلی با user محدود `darkxray` اجرا می‌شود. دسترسی root برای تغییرات privileged مثل TLS/runtime apply و firewall worker جدا نگه داشته شده است.

## Xray-core

مسیر آنلاین، Xray را از Release رسمی pin‌شده دریافت می‌کند. `tools/fetch-core.py` وجود SHA-256 رسمی asset را الزامی می‌داند و در خطای validation به mirror یا دانلود insecure fallback نمی‌کند.

نسخه‌ای که در Gate واقعی CI فعلی اجرا می‌شود:

```text
Xray v26.3.27
```

برای آرشیو محلی نیز مسیر import فقط با SHA-256 مورداعتماد پذیرفته می‌شود.

## دسترسی اولیه

حالت SSH/loopback نمونه:

```bash
ssh -p YOUR_SSH_PORT -L 2087:127.0.0.1:2087 root@YOUR_SERVER_IP
```

سپس آدرس loopback پنل را باز کن. برای public access از Settings/Domain-TLS و workflow گواهی استفاده کن. تغییرات حساس Network/Port/Path اول stage می‌شوند و بعد از مرز root اعمال می‌شوند تا احتمال lockout کمتر شود.

## فرمان مدیریتی

```bash
darkxray
```

فرمان‌های مهم:

```bash
sudo darkxray vps-verify
sudo darkxray production-gate
sudo darkxray settings-apply
sudo darkxray doctor
```

`vps-verify` read-only است و Configuration، SQLite، Xray binary، route پنل، systemd، TLS state، IP Guard و Node readiness را بررسی می‌کند.

`production-gate` علاوه بر readiness، یک محیط موقت مستقل می‌سازد و با **همان Xray binary تعریف‌شده در config نصب** data-plane را تست می‌کند. این lab دیتابیس مشتری واقعی یا firewall نصب‌شده را تغییر نمی‌دهد.

## Inbounds V3

Inbound workflow برای کار روزمره شبیه پنل‌های ساده‌تر طراحی شده ولی backend مستقل است. امکانات اصلی:

- VLESS، VMess، Trojan، Shadowsocks و protocolهای فرم‌شدهٔ پنل؛
- TCP/RAW، WebSocket، gRPC، HTTP Upgrade، XHTTP و mKCP در محدوده قابلیت‌های فرم فعلی؛
- TLS و REALITY؛
- Sniffing؛
- Fallback برای ترکیب‌های پشتیبانی‌شده؛
- Host/endpoint override؛
- Advanced JSON برای تنظیمات خارج از فرم ساده؛
- Validate و state مستقل Core.

وجود یک قابلیت در Xray به معنی parity کامل فرم DARK با همه optionهای ممکن Xray نیست؛ Advanced JSON برای این مرز حفظ شده است.

## Clients / Groups / Resellers

- Clientها به Owner/Reseller مشخص متصل‌اند.
- Groupها owner-scoped هستند؛ دو نماینده می‌توانند Group هم‌نام داشته باشند بدون قاطی‌شدن فیلتر.
- Bulk selection با تغییر Search/Filter/Owner invalidate می‌شود تا عملیات روی ردیف مخفی اجرا نشود.
- Backend ownership و permission را دوباره بررسی می‌کند؛ UI مرز امنیتی محسوب نمی‌شود.
- محدودیت تعداد Client، quota ترافیک و Inbound assignment در سمت سرور اعمال می‌شوند.

## RBAC و امنیت حساب

- Role ceiling هنگام ذخیره و احراز هویت enforce می‌شود.
- Permission legacy دستکاری‌شده نمی‌تواند سطح بالاتر ایجاد کند.
- `finance.credit` و `finance.refund` به Reseller/Readonly delegate نمی‌شوند.
- Robot API Key نمی‌تواند lifecycle کلیدها یا mutation مالی حساس را مدیریت کند.
- Sessionها قابل revoke هستند.
- TOTP با counter واقعی window match ذخیره می‌شود تا replay window کاهش یابد.

سیاست Password حساب‌ها: ۸ تا ۵۱۲ کاراکتر. Passphrase بکاپ رمزدار حداقل ۱۲ کاراکتر دارد.

## Finance / Ledger

Ledger مالی/مصرفی پایه برای حساب‌وکتاب داخلی پنل است، نه درگاه پرداخت کامل.

- event-id برای عملیات مالی idempotent است؛
- retry یک event موجود دوباره balance/Audit ایجاد نمی‌کند؛
- Credit فقط Interactive Owner است؛
- Traffic ledger تاریخی با Reset دوره پاک نمی‌شود؛
- Current-period usage از lifetime usage جدا نگه داشته می‌شود؛
- Finance V2 lifetime را از منبع authoritative Owner می‌گیرد، نه صرفاً آخرین صفحه Ledger.

## Subscription

Subscription از مسیر مستقل DARK تولید می‌شود و فرمت‌های فعلی شامل Raw/Base64/DARK JSON و Clash/Mihomo هستند. Path Subscription با Panel Path تداخل‌سنجی می‌شود تا route پنل یا subscription روی هم نیفتند.

## Nodes V2

Node control برای Origin عمومی HTTPS طراحی شده است:

- URL بدون credential/path/query ناخواسته؛
- DNS فقط به IPهای globally routable؛
- TCP connection به IP validate‌شده pin می‌شود؛
- TLS همچنان hostname اصلی را verify می‌کند؛
- HTTP redirect دنبال نمی‌شود؛
- proxy environment برای Node request استفاده نمی‌شود؛
- response/request limit و deadline وجود دارد؛
- health monitor نودهای فعال را دوره‌ای probe می‌کند.

این کنترل‌ها SSRF risk را کم می‌کنند ولی جای network ACL بیرونی را نمی‌گیرند.

## IP Guard

IP Guard از worker root جداگانه و nftables استفاده می‌کند. حالت `observe` باید قبل از `enforce` برای topology واقعی بررسی شود.

محدودیت IP به معنای شمارش قطعی «آدم» یا «دستگاه فیزیکی» نیست. NAT، IPv4/IPv6 دوگانه، tunnel، CDN/proxy و sessionهای طولانی روی نتیجه اثر دارند.

قبل از enforce باید مطمئن شوی IP مبدأیی که Xray برای Client مشاهده می‌کند همان source packet قابل enforce روی nftables همان host است.

### چیزی که در CI packet-level ثابت شده

تست `kernel-firewall` با network namespace و nftables واقعی انجام می‌شود و موارد زیر را اثبات می‌کند:

- TCP data port بعد Ban drop می‌شود؛
- UDP data port بعد Ban drop می‌شود؛
- management port محافظت‌شده سالم می‌ماند؛
- nft timeout واقعی دسترسی را برمی‌گرداند؛
- explicit unban کار می‌کند؛
- جدول same-name با owner/comment بیگانه overwrite نمی‌شود.

این تست روی kernel واقعی runner است، اما هنوز topology دیتاسنتر/VPS هدف را ثابت نمی‌کند.

## Safe Update / Recovery

Updater قبل از activation:

- source candidate را validate می‌کند؛
- dependency preflight ایزوله می‌سازد؛
- SQLite `quick_check` می‌زند؛
- snapshot مستقل سورس و DB می‌گیرد؛
- بعد از restart فقط `systemctl active` را کافی نمی‌داند و Doctor باید route/asset پنل را سالم ببیند؛
- در failure، source و DB قبلی را restore می‌کند.

### systemd recovery که واقعاً در CI اجرا می‌شود

Gate `systemd-recovery` روی Ubuntu runner مسیرهای production-like را می‌سازد، سرویس را با user `darkxray` بالا می‌آورد، `vps-verify` را پاس می‌کند، Main PID را با SIGKILL می‌زند و نیاز دارد systemd با PID جدید سرویس را برگرداند. سپس stop/start انجام می‌شود و باید تنها یک Xray child متعلق به سرویس باقی بماند.

**این تست reboot واقعی ماشین نیست.** تا reboot/power-cycle واقعی روی VPS انجام نشود، این claim باز می‌ماند.

## Browser QA

Browser Gate با Chromium واقعی اجرا می‌شود و شامل Login/Session، navigation صفحات Owner، ساخت و ذخیره Inbound V3، تغییر English/LTR به فارسی/RTL، حفظ focus/scroll در refresh و viewport موبایل 390px است.

## Real Xray data-plane QA

Gate `real-core` باینری رسمی checksum-verified را اجرا می‌کند و مسیر واقعی زیر را می‌سنجد:

```text
SOCKS client → VLESS → DARK-managed Xray → local HTTP target
```

همراه با دو Client روی Inbound مشترک، Subscription، Traffic Metering، reseller quota isolation، top-up، manual disable، reset/delete accounting و stop تمیز Core.

## چه چیزهایی هنوز باید روی VPS هدف انجام شوند؟

1. Fresh install با online installer روی image/provider واقعی.
2. Reboot/Power-cycle واقعی ماشین و بررسی Panel/Xray/DB بعد boot.
3. Domain/TLS واقعی: issue و renewal گواهی، Secure Cookie و HSTS.
4. IP Guard در topology واقعی سرور/تونل/CDN.
5. دو VPS واقعی Node با HTTPS معتبر و network-loss recovery.
6. ظرفیت‌سنجی روی پلن واقعی VPS؛ smoke هزار Client و SQLite contention در CI قبلاً سبز شده است.
7. Update/Rollback rehearsal با exact release artifact نهایی.
8. تولید دوباره `SHA256SUMS` فقط برای همان release/tag ثابت.

تا تکمیل این موارد، نام نسخه **`0.8.2-standalone-lab`** حفظ می‌شود.

## منابع وضعیت

- `README.md` — معرفی سریع فارسی
- `README.en.md` — معرفی انگلیسی
- `STATUS.fa.md` — وضعیت مهندسی فعلی
- `docs/VALIDATION.md` — تفکیک evidence و claim
- `PUBLISH-STATUS.json` — وضعیت machine-readable انتشار
- `SECURITY.md` — گزارش امنیتی و disclosure

Credential واقعی، private key، certificate، database، secret.key یا log بدون سانسور را داخل مخزن یا Issue منتشر نکن.
