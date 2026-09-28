# DARK VPN · GRE Direct

مدیر مستقل **GRE Direct** با همان روال اپراتوری DARK Backhaul.

> وضعیت: `v0.12.0-rc12` — Draft / Test

## وضعیت RC12

RC12 بعد از تست واقعی ایران ↔ لهستان روی **Plain GRE** تثبیت شد:

- Boot Recovery سمت KHAREJ: PASS
- Boot Recovery سمت IRAN: PASS
- Inner GRE Ping: `10/10` و `0% packet loss`
- Xray روی پورت `1190`: PASS
- اتصال TCP از ایران به `10.77.10.2:1190`: PASS
- IPsec / strongSwan از مسیر فعال DARK GRE حذف شده است

## قابلیت‌ها

- Pair Code با پیشوند `DGR2-`
- GRE Key برای جداسازی Pairها
- Auto PMTU Scan
- MTU حالت‌های Auto / Safe / Maximum / Custom
- TCP MSS Clamp
- Port Forward روی سمت IRAN
- Scheduled Restart
- Traffic counters
- Live Connections
- Speed Test
- Config Fingerprint
- Health Check
- Pair Integrity
- Show Config / Edit Metadata
- Runtime Repair بعد از Update
- Watcher مستقل برای حفظ GRE runtime
- Re-pair روی KHAREJ بدون حذف Tunnel

## منوی اصلی

```text
SETUP
[1] Core                 GRE kernel + dependencies
[2] New tunnel - IRAN    makes Pair Code
[3] New tunnel - KHAREJ  takes Pair Code

OPERATE
[4] Manage tunnels       ports, MTU, endpoint
[5] Dashboard
[6] Diagnostics          logs, tests, fingerprint

MAINTENANCE
[7] Update
[8] Uninstall
[0] Exit
```

## ساخت Tunnel

### IRAN

IRAN صاحب Pair است:

1. IP ایران و KHAREJ ثبت می‌شود.
2. GRE Key ساخته می‌شود.
3. Profile انتخاب می‌شود.
4. MTU تنظیم می‌شود.
5. پورت‌ها و Scheduled Restart تنظیم می‌شوند.
6. Pair Code ساخته می‌شود.

### KHAREJ

روی KHAREJ فقط Pair Code Paste می‌شود.

اطلاعات زیر از Pair Code دریافت می‌شوند:

- Public IPهای دو سمت
- Inner GRE IPها
- GRE Key
- Profile
- MTU / Path MTU
- TX Queue
- Scheduled Restart

Pair Codeهای قدیمی DGR2 که فیلد Security داشته‌اند همچنان Decode می‌شوند، اما RC12 آن‌ها را فقط به **Plain GRE** تبدیل می‌کند و IPsec دوباره فعال نمی‌شود.

## MTU

MTU مستقل از Profile است.

```text
[1] Auto Scan
[2] Safe
[3] Maximum
[4] Custom
```

Auto Scan با DF probe، Path MTU را پیدا می‌کند و سربار GRE را کم می‌کند.

برای Path MTU برابر 1500، حداکثر GRE MTU فعلی:

```text
1472
```

برای TCP نیز `TCPMSS --clamp-mss-to-pmtu` اعمال می‌شود.

## Manage

```text
CONTROL
  Start
  Stop
  Restart

PAIRING
  IRAN   -> Pair code
  KHAREJ -> Apply Pair Code

CONFIGURE
  Ports
  Tuning
  Endpoint
  Scheduled restart

INSPECT
  Speed test
  Live connections
  Logs + interface
  Config fingerprint
  Show config

ADVANCED
  Edit metadata
  Delete tunnel
```

## Diagnostics

- Live log
- Last 60 lines
- Health check
- Link test
- Config fingerprint
- Path MTU scan
- Pair Integrity
- Speed responder

## نصب RC12

```bash
curl -fsSL https://raw.githubusercontent.com/darktunnelmika/dark-xray/feature/dark-gre-direct-v1/standalone/dark-gre/install.sh | bash
```

اجرای مجدد:

```bash
darkgre
```

## انتشار مستقل DARK GRE

نسخه‌بندی DARK GRE از نسخهٔ اصلی DARK XRAY جداست.

Tag پیشنهادی:

```text
dark-gre-v0.12.0-rc12
```

Workflow زیر بستهٔ مستقل می‌سازد:

```text
.github/workflows/dark-gre-release.yml
```

خروجی‌ها:

- `dark-gre-<version>.tar.gz`
- `dark-gre-<version>.zip`
- `SHA256SUMS`

Workflow هم با Tagهای `dark-gre-v*` و هم به‌صورت دستی قابل اجراست. در اجرای دستی می‌توان فقط Artifact ساخت یا GitHub Release مستقل DARK GRE را منتشر کرد.

## مسیر فایل‌ها

```text
/etc/dark-gre/
├── update.url
└── tunnels/
    └── <name>/
        ├── meta.conf
        ├── ports.list
        └── pair.code

/usr/local/bin/darkgre
/usr/local/libexec/darkgre-runner
/etc/systemd/system/darkgre@.service
/etc/systemd/system/darkgre-watch@.service
/etc/systemd/system/darkgre-restart@.service
/etc/systemd/system/darkgre-restart@.timer
/etc/sysctl.d/99-dark-gre.conf
```

## تست واقعی فعلی

Pair تست‌شده:

```text
IRAN    5.202.75.78   10.77.10.1/30
KHAREJ  82.47.63.165  10.77.10.2/30
Port    1190/TCP
Mode    Plain GRE
```

هر دو سمت بعد از Reboot بدون Start دستی Tunnel را بازیابی کردند و Inner Ping و پورت Xray سالم ماندند.

PR تا زمان آخرین Smoke/CI و تثبیت نهایی همچنان Draft می‌ماند.
