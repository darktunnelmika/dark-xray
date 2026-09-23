# Stage 6 — Global Multi-Node Enforcement

Stage 6 مرز بین **سیاست سراسری Client** و **اعمال packet-level محلی** را نهایی می‌کند.

## مدل اعمال

DARK دو لایهٔ مستقل دارد:

1. **Global authorization convergence**
   - Hub مشاهده‌های verified همه Nodeهای assigned را تجمیع می‌کند.
   - عبور از IP/HWID policy، `global_ip_block` / `global_device_block` را تغییر می‌دهد.
   - policy جدید فوراً در desired-state همه Nodeهای مرتبط persist می‌شود.
   - Node آنلاین بعد از acknowledgement به `converged` می‌رسد.
   - Node آفلاین `offline_pending` باقی می‌ماند؛ DARK آن را اعمال‌شده جا نمی‌زند.

2. **Host-local packet Guard**
   - هر Host فقط source IPهایی را که خودش مستقیم و verified می‌بیند enforce می‌کند.
   - root-owned `guardd` از Unix peer credentials، allowlisted Xray ports و nftables استفاده می‌کند.
   - Hub local mode و Node mode مستقل‌اند؛ Hub می‌تواند Observe باشد و Nodeهای direct-source روی Enforce باشند.
## چرا global block به remote nft ban تبدیل نمی‌شود؟

nftables در این مدل source-IP + data-port را می‌بندد و هویت DARK Client را داخل packet نمی‌داند.
در NAT یا IP مشترک، remote ban کور می‌تواند Client دیگری را هم قطع کند.

بنابراین:

- Global decision = credential/service authorization block روی DARK runtimeهای synchronized.
- nftables decision = فقط Host محلی و فقط source packet تأییدشده.
- Security Center هیچ remote firewall ban ساختگی گزارش نمی‌کند.
- اگر یک Node آفلاین باشد، Hub blocker را حفظ و desired policy جدید را pending نگه می‌دارد.

## Convergence observability

`GET /api/clients/{email}/security-global` اکنون برای هر Node گزارش می‌دهد:

- `converged`
- `pending`
- `offline_pending`
- `error`
- desired/applied revision
- اینکه policy desired با global blocker فعلی یکسان است یا نه
- Node Guard mode/state/readiness

Security Center تعداد Nodeهای Enforce/Guard-ready، policy-pending و offline را نمایش می‌دهد.
## Independent Hub / Node mode

تنظیم `ipguard` دو mode دارد:

- `mode`: Guard محلی Hub
- `node_mode`: policy ارسالی به Nodeها

برای configهای قدیمی که `node_mode` ندارند، مقدار قدیمی `mode` به‌صورت سازگار migrate می‌شود.
کلید `node_mode` هرگز به Agent فرستاده نمی‌شود؛ Hub آن را به `mode` عادی Agent ترجمه می‌کند.

## Real packet acceptance

گیت `tests/node_guard_packet_lab.py` در network + mount namespace جدا اجرا می‌شود.

مسیر واقعی تست:

`Node desired state → NodeRuntime → real Xray → real access.log → DARK Guard → fixed Unix broker → nftables → packet DROP`

گیت همچنین ثابت می‌کند:

- broker socket همان path ثابت production است؛
- Agent peer با UID غیرروت از `SO_PEERCRED` احراز می‌شود؛
- NodeRuntime پورت inbound را runtime به broker allowlist اضافه می‌کند؛
- source اول در limit پذیرفته می‌شود؛
- source دوم violation واقعی access-log ایجاد می‌کند؛
- اتصال بعدی فقط از source متخلف روی همان data port DROP می‌شود؛
- source مجاز همچنان عبور می‌کند؛
- explicit unban عبور source متخلف را برمی‌گرداند.
namespace آزمایشی هیچ interface خارجی، veth، NAT یا default route ندارد.
mount namespace جدا مسیر `/run/dark-xray-guard` host را با tmpfs خصوصی می‌پوشاند.
با خروج bootstrap، nftables table و socket آزمایشی از بین می‌روند.

## Evidence فعلی

- Stage 6 targeted regressions: PASS
- Node/Guard/Security/Settings/V06 regression set: **158 passed, 1 skipped**
- real source-IP/HWID suite با Xray رسمی: **12 passed**
- real Node Guard packet gate: **1 passed**
- Xray: official `v26.3.27`
- host firewall namespace modified: **false**

## مواردی که هنوز provider gate می‌خواهند

Lab acceptance به‌تنهایی این‌ها را ثابت نمی‌کند:

- direct-source verification روی topology واقعی هر Node/Tunnel؛
- packet DROP روی هر دو VPS واقعی بعد از فعال‌کردن Node Enforce؛
- offline → reconnect → desired-policy convergence روی WAN واقعی؛
- رفتار NAT/shared-source مخصوص مشتریان واقعی؛
- SLA یا زمان حداکثر convergence در دیتاسنتر.

Stage 6 فقط پس از اجرای این provider acceptance روی exact commit نهایی بسته می‌شود.
