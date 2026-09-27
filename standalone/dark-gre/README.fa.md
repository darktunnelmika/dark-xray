# DARK VPN · GRE Direct

مدیر مستقل **GRE Direct** با همان روال اپراتوری DARK Backhaul.

> وضعیت: `v0.11.0-rc11` — Draft / Test

## تغییرات RC11

- Installer بعد از هر نصب/آپدیت **runtime repair** اجرا می‌کند تا unit/runner قدیمی باقی نماند
- سرویس اصلی در Secure Mode در حالت **armed/active** می‌ماند و منتظر Peer fail نمی‌شود
- Watcher جداگانه هر ۵ ثانیه IPsec/GRE را reconcile می‌کند
- اگر IPsec قطع شود GRE به‌صورت fail-closed پایین می‌آید و plaintext عبور نمی‌کند
- **DGR2 Pair Code** شامل GRE Key، MTU، Security و Scheduled Restart
- **Auto PMTU Scan** با DF probe و محاسبه MTU مناسب برای مسیر
- **TCP MSS Clamp** خودکار برای جلوگیری از مشکل fragmentation/black-hole
- **GRE Key** برای جداسازی Pairها
- **GRE + IPsec** اختیاری با strongSwan، IKEv2 و AES-256-GCM
- سازگاری با تونل‌ها و Pair Codeهای RC2 / DGR1
- Re-pair روی KHAREJ بدون Delete کردن تانل
- Scheduled Restart
- Traffic counters
- Live Connections
- Speed Test: Latency / Passive / Active iperf3
- Config Fingerprint
- Health Check، PMTU diagnostics و XFRM/IPsec status
- Show Config و Edit Metadata برای Advanced

## منوی اصلی

```text
SETUP
[1] Core                 GRE + security cores
[2] New tunnel - IRAN    makes Pair Code
[3] New tunnel - KHAREJ  takes Pair Code

OPERATE
[4] Manage tunnels       ports, security, MTU, endpoint
[5] Dashboard
[6] Diagnostics          logs, tests, fingerprint

MAINTENANCE
[7] Update
[8] Uninstall
[0] Exit
```

## ساخت تانل

### IRAN

IRAN صاحب Pair است. هنگام ساخت:

1. IP ایران و خارج ثبت می‌شود.
2. GRE Key ساخته می‌شود.
3. Security انتخاب می‌شود:
   - `GRE + IPsec` — حالت پیشنهادی
   - `Plain GRE`
4. Profile انتخاب می‌شود.
5. MTU به صورت Auto Scan / Safe / Maximum / Custom تنظیم می‌شود.
6. پورت‌ها و Scheduled Restart تنظیم می‌شوند.
7. Pair Code با پیشوند `DGR2-` ساخته می‌شود.

### KHAREJ

روی KHAREJ فقط Pair Code Paste می‌شود و GRE Key، MTU، Security، IPsec PSK و Restart از کد دریافت می‌شوند.

اگر بعداً MTU، Endpoint یا Security روی IRAN تغییر کرد:

```text
Manage tunnels
 -> PAIRING
 -> Apply Pair Code
```

روی KHAREJ کد جدید را اعمال می‌کند و نیازی به حذف تانل نیست.

## Security

RC8 برای مسیرهای محدودکننده، ESP را با `forceencaps=yes` همیشه داخل **UDP/4500** می‌فرستد. IRAN تنها Initiator است و KHAREJ فقط Responder می‌ماند تا IKE collision و SA churn کمتر شود.



RC7 از مدل **policy-gated Secure GRE** استفاده می‌کند: رابط GRE ثابت می‌ماند، اما برای Peer امن فقط GRE دارای policy واقعی IPsec اجازه عبور دارد و GRE بدون رمزنگاری Drop می‌شود. بنابراین نوسان کوتاه IKE/CHILD_SA باعث حذف و ساخت دوباره Interface نمی‌شود.

Secure Auto MTU در RC7 حداکثر **1400** است.



RC6 فایل‌های strongSwan را داخل مسیرهای AppArmor-safe نگه می‌دارد:

- `/etc/ipsec.d/dark-gre/*.conf`
- `/etc/ipsec.dark-gre.secrets`

Watcher دیگر `ipsec up` را تکرار نمی‌کند؛ retry خود IKE به strongSwan سپرده شده است.



### Plain GRE

GRE خالص رمزنگاری ندارد.

### GRE + IPsec

در Secure Mode، strongSwan یک IPsec Transport Mode بین IPهای عمومی Pair می‌سازد و GRE داخل آن محافظت می‌شود.

```text
IRAN
  |
  +-- Public Port
  |
  +-- GRE interface
  |
  +== IPsec / AES-256-GCM ==+
                            |
                         KHAREJ
                            |
                         Xray Port
```

Runner قبل از بالا آوردن GRE در Secure Mode، وجود IPsec/XFRM policy را بررسی می‌کند؛ اگر Security آماده نباشد GRE بالا نمی‌آید.

## MTU

MTU دیگر از Profile تعیین نمی‌شود.

```text
[1] Auto Scan   recommended
[2] Safe
[3] Maximum
[4] Custom
```

Auto Scan با DF probe Path MTU را پیدا می‌کند و سربار GRE Key و در Secure Mode سربار IPsec را کم می‌کند.

همچنین روی مسیر TCP، `TCPMSS --clamp-mss-to-pmtu` به صورت خودکار اعمال می‌شود.

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
  Security
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
- Speed responder
- IPsec / XFRM status

## نصب RC11

```bash
curl -fsSL https://raw.githubusercontent.com/darktunnelmika/dark-xray/feature/dark-gre-direct-v1/standalone/dark-gre/install.sh | bash
```

اجرای مجدد:

```bash
darkgre
```

## مسیر فایل‌ها

```text
/etc/dark-gre/
├── update.url
├── security/
│   ├── ipsec.secrets
│   └── ipsec.d/
└── tunnels/
    └── <name>/
        ├── meta.conf
        ├── ports.list
        └── pair.code

/usr/local/bin/darkgre
/usr/local/libexec/darkgre-runner
/etc/systemd/system/darkgre@.service
/etc/systemd/system/darkgre-restart@.service
/etc/systemd/system/darkgre-restart@.timer
/etc/sysctl.d/99-dark-gre.conf
```

RC11 تا زمان تست واقعی **Public Port -> GRE -> Xray** و تست Secure Mode روی دو VPS در Draft می‌ماند.
