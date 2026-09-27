# DARK VPN · GRE Direct

مدیر مستقل **GRE Direct** با همان روال اپراتوری DARK Backhaul.

> وضعیت: `v0.2.0-rc2` — Draft / Test

## منوی اصلی

```text
SETUP
[1] Core                 GRE kernel / dependencies
[2] New tunnel - IRAN    makes Pair Code
[3] New tunnel - KHAREJ  takes Pair Code

OPERATE
[4] Manage tunnels       ports, profile, endpoint
[5] Dashboard
[6] Diagnostics

MAINTENANCE
[7] Update
[8] Uninstall
[0] Exit
```

## Pair Code

فلو مثل DARK Backhaul است:

1. روی **IRAN** تانل ساخته می‌شود.
2. IP ایران/خارج، subnet داخلی، Profile و تنظیمات GRE ثبت می‌شوند.
3. یک Pair Code با پیشوند `DGR1-` ساخته و در `pair.code` ذخیره می‌شود.
4. روی **KHAREJ** فقط Pair Code Paste می‌شود.
5. Pair Code روی IRAN همیشه از مسیر زیر دوباره قابل مشاهده است:

```text
Manage tunnels
  -> TUNNEL
     -> PAIRING
        -> Pair code
```

## مدیریت تانل

```text
CONTROL
  Start / Stop / Restart

PAIRING (IRAN)
  Pair code

CONFIGURE
  Ports
  Tuning
  Endpoint

INSPECT
  Ping inner peer
  Logs + interface

ADVANCED
  Delete tunnel
```

پورت‌ها روی IRAN تعریف می‌شوند. حالت پیش‌فرض Same Port است؛ مثلاً `1185 -> 1185`. TCP پیش‌فرض است و در صورت نیاز UDP نیز قابل فعال‌سازی است.

## نصب RC2

```bash
curl -fsSL https://raw.githubusercontent.com/darktunnelmika/dark-xray/feature/dark-gre-direct-v1/standalone/dark-gre/install.sh | bash
```

بعداً با این دستور دوباره Manager باز می‌شود:

```bash
darkgre
```

Installer مانند DARK Backhaul قبل از نصب، فایل Manager را دانلود، marker را بررسی و `bash -n` اجرا می‌کند و نسخه قبلی `darkgre` را به عنوان backup نگه می‌دارد.

## Data Plane

```text
USER
  |
  v
IRAN Public IP : PORT
  |
  | DNAT / MASQUERADE
  v
IRAN GRE IP  ===== GRE protocol 47 =====  KHAREJ GRE IP
                                           |
                                           v
                                     Xray : PORT
```

GRE از **IP protocol 47** استفاده می‌کند؛ بنابراین هر دو دیتاسنتر باید GRE را عبور دهند.

## فایل‌ها

```text
/etc/dark-gre/
├── update.url
└── tunnels/
    └── <name>/
        ├── meta.conf
        ├── ports.list
        └── pair.code   # IRAN

/usr/local/bin/darkgre
/usr/local/libexec/darkgre-runner
/etc/systemd/system/darkgre@.service
/etc/sysctl.d/99-dark-gre.conf
```

## نکته امنیتی

GRE به تنهایی Encryption یا Authentication ندارد. امنیت Sessionهای Xray همچنان توسط پروتکل Xray تأمین می‌شود؛ GRE فقط مسیر مستقیم L3 را ایجاد می‌کند.

این RC تا قبل از تست کامل **Public Port -> GRE -> Xray** در Draft باقی می‌ماند و به `main` Merge نمی‌شود.
