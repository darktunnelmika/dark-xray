# Panel TLS: preflight, local HTTPS and rollback / اعتبارسنجی TLS پنل

## محدودهٔ این اصلاح

این تغییر بخشی از مرحلهٔ سوم است، نه بسته‌شدن آن یا مجوز انتشار.
مبنای پچ `f051624d7bf324d5a7c64a4cdf7f61ddaf60d2d6` است. در این گام
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

آزمون جدید `tests/test_domain_tls_validation.py` در runner موجود اضافه شده است.
CA خصوصی فقط به context همان آزمون افزوده می‌شود؛ تأیید نام و زنجیره خاموش نیست.
آزمون ادغام، خودِ برنامهٔ DARK/FastAPI/Uvicorn و SQLite موقت را روی loopback
اجرا می‌کند. restart سرویس توسط یک شبیه‌ساز کنترل‌شده به stop/start همان
برنامهٔ محلی تبدیل می‌شود. chown در پوشهٔ موقت شبیه‌سازی شده؛ فایل‌نویسی،
TLS و HTTP واقعی‌اند. Xray واقعی، systemd و Certbot فراخوانی نشده‌اند.
آزمون‌های failure اولیه و نهایی جای پذیرش CI کامیت منتشرشده را نمی‌گیرند.

موارد باقی‌مانده: ثبت کامیت و CI این پچ؛ ثبت امن source/hook تمدید در خطای
نوشتن metadata؛ صدور/تمدید واقعی Certbot و systemd روی محیط مجاز؛ بررسی
Secure Cookie/مسیر UI؛ DNS و شبکهٔ VPS؛ پذیرش بستهٔ نهایی. این گام atomicity
در قطع برق/قتل فرایند، هماهنگی چند نویسندهٔ مستقل فایل TLS، وضعیت ابطال OCSP/CRL
یا احیای دسترسی با گواهی قبلیِ از قبل منقضی را تضمین نمی‌کند.

## English implementation boundary

The candidate validates a bounded, single-read PEM/key snapshot through an
in-memory TLS client/server handshake using Python/OpenSSL system trust and SAN
hostname verification. No supplied leaf is promoted to a trust anchor. Local
activation checks the exact leaf served by the panel and its bounded `/health`
response. A service-active flag alone is insufficient. Invalid renewal material
is rejected before changing active files or restarting. Error rollback verifies
the prior local listener rather than merely reporting that files were restored.

Existing issuance and Certbot deploy-hook behavior remains; public ACME issuance,
external DNS, real systemd and renewal metadata transactionality are not claimed
validated by the local test harness. Default OpenSSL trust validation is not an
OCSP/CRL revocation-status guarantee. Trust files belonging to the OS are not
modified. No real key, certificate or customer database is included in evidence.

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
