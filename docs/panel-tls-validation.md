# Panel TLS: preflight, local HTTPS and rollback / اعتبارسنجی TLS پنل

## محدودهٔ این اصلاح

این سند محدودهٔ مرحلهٔ سوم را ثبت می‌کند. پذیرش ایزولهٔ گواهی با کامیت‌های
`ae5bde4` تا `a670729` کامل شده است؛ این به معنی مجوز انتشار یا پذیرش شبکهٔ
ارائه‌دهنده نیست. مبنای اولیهٔ پچ `f051624d7bf324d5a7c64a4cdf7f61ddaf60d2d6` بود. در این گام
کد `tools/domain.py` و آزمون‌هایش تغییر می‌کنند؛ Agent، Xray، DNS عمومی،
قواعد محدودیت مشتری، نصب‌کننده و شمارهٔ نسخه تغییر نمی‌کنند.

پیش از جایگزینی فایل فعال، گواهی و کلید یک‌بار از فایل معمولی خوانده و
همان بایت‌ها در دو موتور محلی OpenSSL از طریق MemoryBIO آزمایش می‌شوند.
اعتبار زنجیره با trust store سیستم، زمان اعتبار، SAN دامنه، کاربرد TLS سرور
و تطابق کلید کنترل می‌شود. هیچ اتصال اینترنتی در این پیش‌بررسی لازم نیست،
ولی اعتماد به CA همچنان لازم است. Common Name به‌جای SAN پذیرفته نمی‌شود.
گواهی خودش به trust store افزوده نمی‌شود و گزینهٔ خاموش‌کردن تأیید TLS نداریم.
فایل‌های symlink معمول Certbot پشتیبانی می‌شوند؛ FIFO و فایل غیرمعمول رد می‌شوند.

پس از restart، اتصال به listener محلی با SNI و Host عمومی انجام می‌شود.
گواهی ارائه‌شده باید دقیقاً leaf گواهی نامزد باشد؛ پاسخ موفق `/health` باید
DARK XRAY/standalone باشد و برای تنظیم HTTPS امن، HSTS مثبت لازم است.
مهلت سوکت و اندازهٔ پاسخ محدودند؛ redirect، proxy یا تغییر DNS استفاده نمی‌شود.
این بررسی، سالم‌بودن همهٔ صفحه‌ها، Secure Cookie ورود یا اینترنت عمومی را اثبات نمی‌کند.

در خطای کپی/فعال‌سازی، تنظیمات و فایل‌های قبلی برگردانده و listener قبلی
واقعاً بررسی می‌شود. در renewal، گواهی نامعتبر پیش از دست‌زدن به فایل فعال
یا restart رد می‌شود. در خطای پس از جایگزینی، زوج قبلی برمی‌گردد و HTTPS
آن بررسی می‌شود. اگر برگشت نیز ناموفق باشد، نتیجه CRITICAL است، نه موفقیت.
پنل قدیمی پشت reverse proxy با listener داخلی HTTP هم برای rollback پشتیبانی
می‌شود؛ این پذیرشِ listener محلی است، نه گواهی آن reverse proxy.

## مرز آزمون

آزمون واحد `tests/test_domain_tls_validation.py` در runner موجود اجرا می‌شود.
CA خصوصی فقط به context همان آزمون افزوده می‌شود؛ تأیید نام و زنجیره خاموش نیست.
آزمون ادغام، خودِ برنامهٔ DARK/FastAPI/Uvicorn و SQLite موقت را روی loopback
اجرا می‌کند. در این لایه restart سرویس شبیه‌سازی می‌شود، اما فایل‌نویسی،
TLS و HTTP واقعی‌اند.

پذیرش جداگانهٔ `tests/panel-tls-acme-systemd-smoke.sh` روی Ubuntu دورریختنی
**Certbot بستهٔ سیستم و systemd واقعی** را اجرا می‌کند. DARK از مسیر واقعی
`setup.sh` نصب می‌شود؛ Certbot از یک Pebble v2.10.1 پین‌شده به‌عنوان CA
آزمایشی گواهی می‌گیرد و `certbot renew --force-renewal` واقعاً اجرا می‌شود.
deploy hook با تغییر PID سرویس مشاهده می‌شود و پس از صدور و renewal، HTTPS،
گواهی جدید، HSTS و ویژگی‌های `Secure`، `HttpOnly`، `SameSite=Strict`
و Path کوکی ورود بررسی می‌شوند. این runner پس از آزمون پاک می‌شود و از دادهٔ
مشتری استفاده نمی‌کند.

Pebble عمداً با حالت `always-valid` اجرا می‌شود؛ بنابراین این پذیرش **اثبات
Let’s Encrypt عمومی، DNS عمومی، دسترسی WAN یا اعتبارسنجی خارجی HTTP-01 نیست**.
این موارد در مرحلهٔ پذیرش VPS مستقل باقی می‌مانند و با موفقیت CI جایگزین
نمی‌شوند.

ثبت منبع تمدید `tls-source.json` و deploy hook اکنون به‌صورت یک واحد rollback-aware انجام می‌شود. پیش از نوشتن، فایل‌های قبلی snapshot می‌شوند؛ symlink یا فایل غیرعادی برای metadata پذیرفته نمی‌شود. اگر نوشتن source یا hook شکست بخورد، هر دو به وضعیت قبلی برمی‌گردند. اگر این شکست پس از فعال‌شدن TLS رخ دهد، runtime، Guard و زوج گواهی قبلی نیز بازیابی و listener قبلی بررسی می‌شود؛ شکست بازیابی CRITICAL است. وضعیت Guard مورد استفاده برای rollback از قبلِ فعال‌سازی گرفته می‌شود، نه بعد از آن.

**وضعیت مرحلهٔ سوم:** در محدودهٔ ایزوله بسته است؛ صدور، renewal، deploy hook،
systemd، HTTPS و Secure Cookie آزمایش شده‌اند. کار باقی‌ماندهٔ گواهی در سطح
ارائه‌دهنده شامل DNS عمومی، CA عمومی/Let’s Encrypt و HTTP-01 از بیرون است و
به مرحلهٔ VPS مستقل منتقل می‌شود. پذیرش بستهٔ نهایی نیز مرحلهٔ انتشار است.

این پذیرش atomicity در قطع برق/قتل فرایند، هماهنگی چند نویسندهٔ مستقل فایل TLS،
وضعیت ابطال OCSP/CRL یا احیای دسترسی با گواهی قبلیِ از قبل منقضی را تضمین
نمی‌کند.

## English implementation boundary

The candidate validates a bounded, single-read PEM/key snapshot through an
in-memory TLS client/server handshake using Python/OpenSSL system trust and SAN
hostname verification. No supplied leaf is promoted to a trust anchor. Local
activation checks the exact leaf served by the panel and its bounded `/health`
response. A service-active flag alone is insufficient. Invalid renewal material
is rejected before changing active files or restarting. Error rollback verifies
the prior local listener rather than merely reporting that files were restored.

The local unit harness remains separate from host acceptance. A disposable
Ubuntu workflow additionally runs packaged Certbot and real systemd against a
pinned Pebble test CA, verifies issuance, forced renewal, deploy-hook restart,
HTTPS/HSTS and secure login-cookie attributes, then removes its disposable
state. Pebble challenge validation is intentionally bypassed in test mode, so
public ACME/Let's Encrypt, external DNS/WAN and external HTTP-01 remain provider
VPS acceptance items. Default OpenSSL trust validation is not an OCSP/CRL
revocation-status guarantee. The workflow temporarily adds only its disposable
test root to the disposable runner trust store; product code does not modify OS
trust. No customer database or production key/certificate is evidence.

Run selected tests from the repository root:

```sh
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest \
  tests/test_domain_tool.py tests/test_domain_tls_validation.py -q
```

Primary references for the APIs and existing hook contract:
- https://docs.python.org/3.13/library/ssl.html#ssl.SSLContext.wrap_bio
- https://docs.python.org/3.13/library/ssl.html#ssl.create_default_context
- https://docs.python.org/3.13/library/ssl.html#ssl.SSLContext.hostname_checks_common_name
- https://eff-certbot.readthedocs.io/en/stable/using.html#renewing-certificates
