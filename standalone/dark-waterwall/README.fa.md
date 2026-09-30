# DARK VPN · WaterWall Direct

مدیر مستقل **WaterWall Direct** برای ساخت مسیر TCP مستقیم از IRAN به KHAREJ با ظاهر و روال اپراتوری DARK.

> وضعیت: `v0.1.0-rc1` — Initial RC / CI Validation Required / Not Live-Validated Yet

این ابزار Reverse Tunnel نیست. در مسیر اصلی، اتصال Transport از سمت IRAN به Listener سرور KHAREJ ایجاد می‌شود.

## معماری

### Reality Direct

```text
User TCP
   ↓
IRAN TcpListener
   ↓
HeaderClient (target port)
   ↓
RealityClient
   ↓
TcpConnector ───────── TCP ────────► KHAREJ TcpListener
                                      ↓
                                 RealityServer
                                      ↓
                                  HeaderServer
                                      ↓
                                  TcpConnector
                                      ↓
                              127.0.0.1:<target>
```

هر Connection کاربر یک Connection انتقال TCP به KHAREJ می‌سازد.

### Reality + HalfDuplex

```text
User TCP
   ↓
IRAN TcpListener
   ↓
HeaderClient
   ↓
HalfDuplexClient
   ↓
RealityClient
   ╞════════ upload TCP ════════►
   ╘════════ download TCP ══════► KHAREJ RealityServer
                                      ↓
                                HalfDuplexServer
                                      ↓
                                  HeaderServer
                                      ↓
                                   Backend
```

در HalfDuplex هر Connection منطقی کاربر به دو Connection انتقال تبدیل می‌شود؛ بنابراین این Mode برای شرایطی است که رفتار HalfDuplex واقعاً لازم باشد، نه به‌عنوان پیش‌فرض همیشگی.

## قابلیت‌های RC1

- Direct initiation از IRAN به KHAREJ
- Reality v2 با `chacha20-poly1305`
- Reality + HalfDuplex اختیاری
- Pair Code با پیشوند `DWW1-`
- Pair checksum و fingerprint
- Secret تصادفی ۳۲ بایتی ASCII برای Reality
- Multi-port
- Port range
- Port remap برای پورت تکی
- HeaderClient/HeaderServer برای انتقال مقصد
- Whitelist شدن IP ایران روی Listener خارج
- Worker مستقل برای هر سمت
- systemd template مستقل برای هر Tunnel
- `LimitNOFILE=1048576`
- Restart خودکار سرویس
- Dashboard
- Diagnostics
- Live socket / FD count
- Update مستقل DARK Script
- Update مستقل WaterWall core
- SHA-256 verification وقتی digest رسمی Release API موجود باشد
- x86_64 و arm64

## فرمت پورت

```text
443
443,8443
8443:9443
20000-20100
443,8443:9443,20000-20100
```

معنی:

```text
443           IRAN:443       -> KHAREJ backend:443
8443:9443     IRAN:8443      -> KHAREJ backend:9443
20000-20100   same-port range
```

Range در RC1 به‌صورت same-port است.

## نصب

بعد از Merge شدن RC1 روی `main`:

```bash
curl -4 -fsSL --retry 3 https://cdn.jsdelivr.net/gh/darktunnelmika/dark-xray@main/standalone/dark-waterwall/install.sh | bash
```

اجرای مجدد:

```bash
darkwater
```

در زمان تست Feature Branch:

```bash
curl -4 -fsSL https://raw.githubusercontent.com/darktunnelmika/dark-xray/feature/dark-waterwall-direct-v1/standalone/dark-waterwall/install.sh | bash
```

## ساخت Tunnel

### 1. روی IRAN

از منو:

```text
[2] New Direct · IRAN
```

Wizard این موارد را می‌گیرد:

- نام Tunnel
- Public IP ایران
- Public IP خارج
- Transport TCP Port روی KHAREJ
- Mode: Reality یا Reality + HalfDuplex
- SNI / visitor domain
- Backend address روی KHAREJ
- Port mappings
- Worker count

سپس یک Pair Code ایجاد می‌کند.

Pair Code شامل Reality secret است و باید مانند Credential نگه‌داری شود.

### 2. روی KHAREJ

از منو:

```text
[3] New Direct · KHAREJ
```

فقط Pair Code را Paste کن. Script:

- IPها را بازیابی می‌کند.
- Transport Port را تنظیم می‌کند.
- Mode و SNI و Secret را بازیابی می‌کند.
- Backend را تنظیم می‌کند.
- Source IP ایران را روی `TcpListener.whitelist` محدود می‌کند.
- Config و systemd service را ایجاد می‌کند.

## منوی اصلی

```text
SETUP
[1] Install / Repair Core
[2] New Direct · IRAN
[3] New Direct · KHAREJ

OPERATE
[4] Manage tunnels
[5] Dashboard
[6] Diagnostics

MAINTENANCE
[7] Update DARK script
[8] Update WaterWall core
[9] Uninstall
[0] Exit
```

## WaterWall Core

Core در مسیر زیر نصب می‌شود:

```text
/opt/dark-waterwall/
```

نسخه Latest از Release API رسمی WaterWall پیدا می‌شود. Buildهای پیش‌فرض:

```text
x86_64  -> Waterwall-linux-gcc-x64.zip
arm64   -> Waterwall-linux-gcc-arm64.zip
```

برای CPU قدیمی:

```bash
DARK_WW_OLD_CPU=1 darkwater
```

برای Mirror اختصاصی:

```bash
DARK_WW_DOWNLOAD_URL='https://mirror.example/Waterwall.zip' \
DARK_WW_SHA256='<sha256>' \
darkwater
```

## مسیر فایل‌ها

```text
/etc/dark-waterwall/
├── update.url
└── tunnels/
    └── <name>/
        ├── meta.conf
        ├── ports.list
        ├── pair.code
        ├── core.json
        ├── config.json
        └── logs/

/opt/dark-waterwall/
├── Waterwall
├── version.txt
└── libs/

/usr/local/bin/darkwater
/etc/systemd/system/darkwater@.service
```

## نکات ظرفیت

RC1 عمداً MUX را وارد مسیر نکرده است تا رفتار Direct Reality و HalfDuplex مستقل و قابل اندازه‌گیری باشد.

- Reality Direct: تقریباً یک Transport TCP به ازای هر Connection کاربر.
- HalfDuplex: دو Transport TCP به ازای هر Connection منطقی.
- تعداد کاربران پنل برابر تعداد socket همزمان نیست؛ ظرفیت باید با concurrency و traffic واقعی سنجیده شود.
- `LimitNOFILE` بالا گذاشته شده، اما ظرفیت نهایی همچنان به CPU، RAM، NIC، kernel، RTT و تعداد Connectionهای واقعی وابسته است.

قبل از اعلام Production باید روی Pair واقعی ایران ↔ خارج، reboot recovery، load test و packet-loss/reconnect تست شود.

## Security Boundary

- Transport Listener خارج فقط IP ایران ثبت‌شده در Pair را می‌پذیرد.
- Reality payload با secret مشترک محافظت می‌شود.
- Pair Code شامل secret است؛ آن را در Issue، Log عمومی یا Screenshot عمومی منتشر نکن.
- Backend پیش‌فرض `127.0.0.1` است.
- Header مسیر مقصد را از Pair مورد اعتماد ایران می‌گیرد؛ این مسیر برای Pairهای تحت کنترل خود اپراتور طراحی شده است.

## Validation

Smoke CI باید موارد زیر را PASS کند:

- `bash -n`
- Pair encode/decode
- Port parser
- IRAN config generation
- KHAREJ config generation
- JSON parse
- وجود RealityClient/RealityServer
- وجود HalfDuplexClient/HalfDuplexServer در Mode مربوطه
- Startup واقعی هر دو Config با WaterWall pin‌شده در CI

RC1 تا قبل از تست Pair واقعی **Live-Validated** محسوب نمی‌شود.
