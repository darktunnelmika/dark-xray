# DARK VPN · GRE Direct

`DARK GRE` یک مدیر مستقل برای ساخت تونل **GRE مستقیم** بین سرور ایران و سرور خارج است. طراحی رابط، Pair Code، مدیریت چند تونل، Dashboard و Update از الگوی Dark Backhaul گرفته شده، ولی Data Plane کاملاً GRE و بدون Core ثالث است.

> وضعیت: `v0.1.0-rc1` — توسعه / تست

## معماری

```text
User
  |
  |  TCP / UDP : PORT
  v
IRAN Public IP
  |
  |  DNAT + MASQUERADE
  v
10.77.x.1  ===== GRE protocol 47 =====  10.77.x.2
 IRAN                                      KHAREJ
                                             |
                                             v
                                   Xray inbound : PORT
```

ساخت از سمت **IRAN** شروع می‌شود. مدیر IP عمومی ایران و خارج، subnet خصوصی `/30`، MTU/Profile و پورت‌های Forward را ذخیره می‌کند و یک Pair Code می‌دهد. همان Pair Code روی KHAREJ Paste می‌شود و سمت دوم با مقادیر معکوس ساخته می‌شود.

## ویژگی‌های RC1

- GRE واقعی Linux با `ip tunnel`
- نقش‌های `IRAN` و `KHAREJ`
- Pair Code دارای checksum
- چند تونل همزمان با interface مستقل
- subnet خودکار از محدوده `10.77.0.0/16`
- پروفایل‌های `Balanced / Stable / Low Ping / Turbo`
- Port Forward برای TCP و UDP از ایران به خارج
- `1185 -> 1185` و Mapping پورت متفاوت
- systemd و Auto Start پس از reboot
- Dashboard و Ping داخلی تونل
- Start / Stop / Restart / Delete
- Add Port / Remove Port بدون ساخت مجدد تونل
- Live Log با journalctl
- Update داخلی
- نصب مستقل از DARK XRAY

## پروفایل‌ها

| Profile | MTU | TX Queue | کاربرد |
|---|---:|---:|---|
| Balanced | 1436 | 1000 | پیش‌فرض |
| Stable | 1380 | 1000 | مسیرهای حساس به Fragment |
| Low Ping | 1400 | 500 | ترافیک تعاملی |
| Turbo | 1476 | 2000 | مسیر با MTU کامل 1500 |

GRE خودش Encryption یا Authentication ندارد. برای DARK XRAY، ترافیک Xray همچنان امنیت پروتکل خودش را دارد؛ GRE فقط مسیر L3 مستقیم را می‌سازد.

## نصب نسخه توسعه

```bash
curl -fsSL https://raw.githubusercontent.com/darktunnelmika/dark-xray/feature/dark-gre-direct-v1/standalone/dark-gre/install.sh | bash
```

بعد:

```bash
darkgre
```

## راه‌اندازی

### 1. IRAN

از منو:

```text
[2] New tunnel - IRAN
```

مقادیر:
- Tunnel name
- IRAN public IP
- KHAREJ public IP
- Profile
- Forward ports

مثال:

```text
Listen port on IRAN: 1185
Protocol: tcp
Target port on KHAREJ: 1185
```

در پایان Pair Code نمایش داده می‌شود.

### 2. KHAREJ

روی سرور خارج:

```text
[3] New tunnel - KHAREJ
```

Pair Code را Paste کنید. Manager آدرس‌های Public و Private را معکوس می‌کند، interface GRE را می‌سازد و Ping سمت ایران را تست می‌کند.

## مسیر فایل‌ها

```text
/etc/dark-gre/
├── update.url
└── tunnels/
    └── <name>/
        ├── meta.conf
        └── ports.list

/usr/local/bin/darkgre
/usr/local/libexec/darkgre-runner
/etc/systemd/system/darkgre@.service
/etc/sysctl.d/99-dark-gre.conf
```

## مدیریت پورت

روی IRAN:

```text
Manage tunnels
  -> Add port
  -> Remove port
```

فرمت داخلی `ports.list`:

```text
tcp:1185:1185
udp:2053:2053
tcp:443:8443
```

## تست سلامت

```bash
systemctl status darkgre@NAME
ip -d link show dgrXXXXXXXX
ip -s link show dgrXXXXXXXX
ping REMOTE_TUN_IP
journalctl -u darkgre@NAME -n 100 --no-pager
```

## نکته شبکه

GRE از **IP protocol 47** استفاده می‌کند، نه TCP/UDP port. هر دو دیتاسنتر باید GRE را عبور دهند. در تست واقعی توسعه، مسیر `213.142.132.210 <-> 92.223.2.152` با GRE و subnet موقت `/30`، چهار پکت از چهار پکت و بدون packet loss پاسخ داده است.

## مرحله بعد

بعد از تأیید RC1:
1. انتقال مدیر مستقل به ریپوی `dark-gre`
2. Release و SHA256SUMS
3. اضافه شدن Plugin `Dark GRE Direct` به DARK XRAY
4. انتخاب IRAN Node و KHAREJ Node از پنل
5. Create / Delete / Status / Logs / Add Port از UI پنل
