[فارسی](README.md) | [English](README.en.md)

```text
╔════════════════════════════════════════════╗
║                 DARK XRAY                  ║
║          STANDALONE CONTROL PANEL          ║
╚════════════════════════════════════════════╝
```

# DARK XRAY 🖤

پنل مستقل مدیریت Xray با رابط Cyber/Dark، مدیریت Inbound و Client، مالک اصلی واحد، نمایندگان، Ledger، Subscription، Nodes و کنترل مستقیم Xray-core.

**نسخه آزمایشی برای تست VPS:** `0.9.0-rc7`

> [!CAUTION]
> DARK XRAY در وضعیت **Release Candidate** است و هنوز Production Ready اعلام نشده. Browser، Xray رسمی، nftables packet-level، systemd recovery و Load/Scale هزار کلاینت در CI سبزند؛ اما نصب روی VPS هدف، reboot واقعی ماشین، TLS/renewal دیتاسنتر موردنظر و دو VPS واقعی Node هنوز گیت نهایی هستند.

## وضعیت فعلی

DARK XRAY برای Runtime به Sanayi/3x-ui وابسته نیست:

```text
DARK UI → DARK API / Owner + Representative Scope → DARK Database → Xray-core
                                  ├→ Ledger / Policy
                                  ├→ Node control
                                  └→ IP Guard → nftables
```

قابلیت‌های اصلی فعلی:

- **Inbounds V3** با VLESS/VMess/Trojan/Shadowsocks، Transportهای اصلی، TLS/REALITY، Sniffing، Fallback و Advanced JSON.
- **Clients + Groups V4** با Command Deck دارک/سایبری، Search/Quick Presence/Filter/Sort، Groupهای owner-scoped و Online/Idle/Offline مبتنی بر activity واقعی.
- **Create/Edit Client V4** با مسیر Basic-first؛ Identity، Inbound، Plan، Expiry و IP limit در حالت اصلی و HWID/reset/comment/XTLS Flow داخل Advanced.
- **Reduced row actions** با دو مسیر واضح `OPEN / QR`؛ IP/HWID و More Actions از UI اصلی حذف شده‌اند.
- **QR / Share V3** با QR مستقل برای Subscription و هر Config، Copy و Download SVG.
- **REALITY Guard** برای Xray pin‌شده: تارگت‌های شناخته‌شده ناسازگار مثل `www.microsoft.com` fail-closed رد می‌شوند و Target Search مسیر پیشنهادی سالم می‌دهد.
- **Representatives V2** با یک Primary Owner، فرم یکپارچه Login/Profile، Inboundهای مجاز، سهمیه، سقف Client، Prefix اجباری اختیاری، Max IP/HWID و scope ثابت سمت سرور؛ صفحه Access Levels/Permission Matrix از UI حذف شده است.
- **Account Security** شامل Session، TOTP ضد replay و API Key با محدودیت Robot scope.
- **Settings V2** با General/Security/Network/Domain-TLS/Subscription/IP Guard/Appearance/System و مرز stage/apply برای تنظیمات privileged.
- **Finance / Ledger V2** با event-id idempotency، مصرف دوره جاری و lifetime، Credit تعاملی Owner و Audit قابل ردیابی.
- **Xray Control V2** برای DNS، Outbound، Routing، Balancer و Observatory.
- **Nodes V5 / Lightweight Agent** با نصب مستقل و بدون Web Panel دوم، Pair Code یک‌بارمصرف، Desired State نسخه‌دار، Deployment Target داخل Inbound، Runtime داخل Public Endpoint، Traffic/Security/IP-HWID مرکزی، Guard محلی، Xray/Logs/Update از Hub و recovery خودکار Node آفلاین.
- **Dashboard Control Center** برای Owner؛ Update Center با Latest Verified / Stable / RC / Exact Ref، CI gate روی Commit دقیق، Preflight، Changelog، Progress، Log و Rollback خودکار مستقیماً روی صفحه اول قرار دارد.
- **Root Update Broker** مستقل از Web process؛ پنل non-root می‌ماند و Broker فقط status/check/start محدود را از Unix socket احرازشده می‌پذیرد.
- **Backup / Restore / Doctor** روی Hub متمرکز است؛ Full Encrypted Backup از خود پنل شامل SQLite، `secret.key`، TLS پنل، TLS اینباندها و Node/Desired State قابل دریافت است. Nodeها بکاپ مدیریتی مستقل ندارند و از Hub بازسازی می‌شوند.
- **Cyber UI** با English/LTR پیش‌فرض، فارسی/RTL، responsive layout و refresh/focus stability.

## نصب آنلاین

### سرور اصلی / Hub

روی Ubuntu/Debian دارای systemd:

```bash
curl -fL --retry 3 https://raw.githubusercontent.com/darktunnelmika/dark-xray/main/install-online.sh -o /tmp/dark-xray-install.sh
sudo bash /tmp/dark-xray-install.sh
```

Installer پنل اصلی حالت‌های `Clean`، `Partial/Failed` و `Installed` را تشخیص می‌دهد و برای نصب/Repair/Update مسیر جدا دارد. بعد از نصب:

```bash
darkxray
```

### سرور Node سبک

روی VPS جداگانه‌ای که پنل اصلی روی آن نصب نیست:

```bash
curl -fL --retry 3 https://raw.githubusercontent.com/darktunnelmika/dark-xray/main/install-node.sh -o /tmp/dark-xray-node.sh
sudo bash /tmp/dark-xray-node.sh
```

Node فقط **DARK Node Agent + Xray-core + Guard + Updater + TLS runtime** را نصب می‌کند؛ Web UI، Owner، Finance و Reseller روی Node نصب نمی‌شوند. Installer یک `DXN1...` Pair Code می‌دهد؛ آن را در **Main Panel → Nodes → Add Node** وارد کن. پس از Pair موفق، credential bootstrap خودکار Rotate می‌شود و Pair Code مصرف‌شده دیگر credential دائمی Node نیست.

Xray رسمی در هر دو مسیر به نسخه pin‌شده دریافت می‌شود و SHA-256 رسمی Release بررسی می‌شود.

## گیت‌های سلامت روی سرور

Readiness بدون تغییر سرویس/DB/firewall:

```bash
sudo darkxray vps-verify
```

گیت کامل‌تر که readiness نصب را با یک lab ایزوله روی **همان Xray binary نصب‌شده** ترکیب می‌کند:

```bash
sudo darkxray production-gate
```

خروجی ماشینی:

```bash
sudo darkxray production-gate --json-only
```

`production-gate` دیتابیس مشتری‌ها یا firewall نصب‌شده را تغییر نمی‌دهد؛ data-plane را در دیتابیس/پورت‌های موقت loopback تست می‌کند.

برای بررسی واقعی Nodeهای ثبت‌شده از Central VPS:

```bash
sudo darkxray node-wan-gate --json-only
```

برای rehearsal قطع/وصل واقعی یک Node، Gate را در حالت Watch اجرا کن و شبکه همان Node را خارج از Gate قطع و دوباره وصل کن:

```bash
sudo darkxray node-wan-gate --watch-seconds 180 --expect-outage NODE_ID
```

این ابزار از همان HTTPS/TLS pinning مسیر Production استفاده می‌کند و Health، Traffic، Security و Failover readiness را از WAN می‌سنجد؛ **خودش outage ایجاد نمی‌کند** و بدون مشاهده واقعی Down → Recovery ادعای network-loss recovery نمی‌کند.

## چه چیزهایی واقعاً در CI تست شده‌اند؟

روی `main` این Gateها مستقل اجرا می‌شوند:

- **Python 3.12 و 3.13:** API، Owner/Representative scope، hardening legacy roleها، TOTP، Finance، Settings، Backup/Update rollback، Nodes و regressionها.
- **Browser QA:** Chromium واقعی، Login، صفحات Owner، ذخیره Inbound V3، EN/LTR ↔ FA/RTL، refresh/focus و viewport موبایل 390px.
- **Real Xray:** Xray رسمی `v26.3.27` با مسیر واقعی `SOCKS → VLESS → HTTP`، Subscription، Traffic Metering، quota isolation و recovery semantics.
- **Kernel firewall:** network namespace و nftables واقعی؛ TCP/UDP drop، سالم‌ماندن management port، timeout، unban و foreign-table protection.
- **systemd recovery:** مسیرهای production-like، user محدود `darkxray`، enable/start، `vps-verify`، SIGKILL restart، stop/start و جلوگیری از Xray child تکراری.

جزئیات و مرز ادعاها: [docs/VALIDATION.md](docs/VALIDATION.md)

## امنیت و مرزهای دسترسی

- Password حساب‌های DARK: حداقل ۸ و حداکثر ۵۱۲ کاراکتر.
- Passphrase بکاپ رمزدار: سیاست جدا و حداقل ۱۲ کاراکتر.
- Robot Key نمی‌تواند lifecycle کلیدها یا mutation مالی حساس را در اختیار بگیرد.
- `finance.credit` و `finance.refund` در سقف Roleهای پایین‌تر قرار ندارند.
- Node URL باید HTTPS باشد و به IP عمومی resolve شود؛ redirect و proxy environment در مسیر Node دنبال نمی‌شود.
- IP Guard فقط وقتی باید enforce شود که تطابق IP مشاهده‌شده در Xray و source packet روی همان host تأیید شده باشد.
- **Global Multi-node Guard** فقط IPهایی را که Node با source مستقیمِ تأییدشده دیده تجمیع می‌کند؛ HWID خام بین Nodeها جابه‌جا نمی‌شود و فقط SHA-256 آن Sync می‌شود. عبور از limit باعث Block credential در Central و Mirrorها می‌شود؛ nftables همچنان host-local است.

## هنوز چه چیزهایی برای Production باقی است؟

- Fresh install روی **VPS هدف واقعی** با image/provider نهایی.
- Reboot/Power-cycle واقعی ماشین؛ CI فعلاً crash و stop/start systemd را اثبات می‌کند، نه reboot میزبان.
- صدور و renewal واقعی Let's Encrypt در DNS/provider هدف و بررسی Secure Cookie/HSTS.
- IP Guard روی topology واقعی ترافیک همان VPS/تونل/CDN.
- دو VPS واقعی Node با HTTPS، اجرای `sudo darkxray node-wan-gate`، قطع/وصل واقعی شبکه و مشاهده‌ی Down → Recovery روی WAN واقعی. خود Gate قطعی شبکه ایجاد نمی‌کند.
- ظرفیت‌سنجی روی سخت‌افزار/پلن VPS هدف؛ CI فعلی smoke هزار Client و SQLite contention را پاس کرده است.
- rehearsal نهایی Update/Rollback با همان release artifact که قرار است deploy شود.

بنابراین این build با برچسب **`0.9.0-rc7`** برای تست VPS است؛ Production Ready بعد از گیت‌های واقعی هدف اعلام خواهد شد.

### یک‌بار Bootstrap برای نصب‌های RC6 و قدیمی‌تر

برای سروری که هنوز Update Broker ندارد، یک بار Safe Update با installer آنلاین همین RC لازم است. این مسیر **updater خود Candidate** را اجرا می‌کند و `dark-xray-update.service` را نصب می‌کند. از آن به بعد Update Center روی صفحه اول پنل مسیر اصلی آپدیت است.

## مستندات

- [راهنمای فارسی](README.fa.md)
- [وضعیت دقیق قابلیت‌ها و گیت‌ها](STATUS.fa.md)
- [Validation Matrix](docs/VALIDATION.md)
- [Security](SECURITY.md)
- [Third-party notices](THIRD-PARTY-NOTICES.md)
- [Publication status](PUBLISH-STATUS.json)

`SHA256SUMS` و archive checksum فقط هنگام snapshot/tag نهایی RC بازتولید می‌شوند؛ فایل checksum قدیمی به‌عنوان evidence RC7 معرفی نمی‌شود.

**Credential، private key، certificate، database یا log بدون سانسور را در Repository/Issue منتشر نکنید.**
