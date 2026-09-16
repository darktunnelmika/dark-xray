# وضعیت DARK XRAY 0.8.2

تاریخ بازبینی: **16 سپتامبر 2026**  
برچسب فعلی: **0.8.2-standalone-lab**

> این فایل وضعیت واقعی پروژه را توصیف می‌کند. «پیاده‌سازی‌شده» به معنی وجود کد، کنترل‌های سمت سرور و تست‌های خودکار مربوط است؛ تا زمانی که گیت‌های VPS واقعی پایین تکمیل نشوند، DARK XRAY به‌عنوان Production Ready اعلام نمی‌شود.

## وضعیت کلی

DARK XRAY اکنون یک پنل مستقل با دیتابیس، API، رابط وب و مدیریت مستقیم Xray-core است و برای اجرای اصلی به Sanayi/3x-ui وابسته نیست. تمرکز نسخه فعلی از «ساخت قابلیت‌ها» به **Hardening، قابلیت بازیابی، جداسازی نماینده‌ها، امنیت و UX پایدار** منتقل شده است.

| بخش | وضعیت فعلی |
|---|---|
| استقلال پنل | مستقل؛ DB/API/UI و runtime متعلق به DARK |
| Inbounds / REALITY | فرم‌های بومی، اعتبارسنجی و workflow اختصاصی DARK |
| Clients V2 | فیلتر، Group، Bulk operations و جداسازی مالک/نماینده |
| Groups | owner-scoped؛ گروه هم‌نام دو نماینده با هم قاطی نمی‌شود |
| Reseller / RBAC | سقف نقش، scope سمت سرور، حذف grantهای legacy نامعتبر |
| Session / TOTP | نشست‌های قابل ابطال، TOTP و جلوگیری از replay counter |
| Robot API Keys | scope محدود؛ key lifecycle و mutation مالی به Robot Key واگذار نمی‌شود |
| Settings V2 | تنظیمات ساختاریافته، stage/apply برای تغییرات privileged و rollback |
| Panel URI path | مسیر سفارشی با جلوگیری از تداخل API/Assets/Subscription |
| Domain / TLS | Certbot workflow و rollback-safe activation در کد |
| Installer / Update | preflight، health check زنده، snapshot DB/source و rollback |
| Finance / Ledger | event-id idempotency، Ledger ماندگار و Credit فقط توسط Owner تعاملی |
| Finance V2 | دفتر مالی/مصرف با Owner filter، Event ID و lifetime totals معتبر |
| Traffic accounting | مصرف دوره جاری از lifetime ledger جداست؛ reset تاریخچه را پاک نمی‌کند |
| IP / HWID | policy و محدودیت دستگاه/IP در runtime مستقل DARK |
| IP Guard | worker مجزا و مرز root برای اعمال firewall؛ تست packet واقعی هنوز گیت انتشار است |
| Nodes V2 | token رمز‌شده، HTTPS اجباری، probe/deploy/core actions |
| Node egress security | IP عمومی اجباری، DNS pinning، TLS hostname verification، redirect/proxy bypass بسته |
| Node health | monitor پس‌زمینه برای نودهای فعال و ثبت latency/error |
| Backup / Restore | DB و backup workflow با کنترل‌های ایمنی و تست‌های rollback |
| UI stability | refresh هنگام تایپ DOM را خراب نمی‌کند؛ focus/caret/scroll همان صفحه حفظ می‌شود |
| CI | Source/installer/workspace + تست‌های Python 3.12/3.13 و JavaScript |

## Hardeningهای اخیر

### Runtime و نصب

- `darkxray check` از سرویس زنده مستقل شده و با lock پروسه اصلی تداخل ندارد.
- تغییرات privileged پورت/دامنه/Path ابتدا Stage می‌شوند و بعد از SSH اعمال می‌شوند.
- تداخل Panel Path و Subscription Path هم در Web و هم CLI بسته شده است.
- updater قبل از activation، source و SQLite snapshot می‌گیرد و بعد از restart فقط به وضعیت systemd اکتفا نمی‌کند؛ Doctor باید route و asset واقعی پنل را سالم ببیند.

### Clients / Resellers

- Group filter با ترکیب Owner + Group کار می‌کند.
- تغییر Search/Filter/Owner انتخاب‌های Bulk مخفی را invalidate می‌کند.
- Backend برای عملیات Bulk دوباره ownership، permission و inbound assignment را بررسی می‌کند.
- Runtime permission قدیمی `ip.read` از RBAC پنل حذف شده؛ سطح IP/Device پنل از `clients.ip` استفاده می‌کند.
- `/api/sync` برای non-owner فقط وضعیت عمومی runtime را می‌دهد و diagnosticهای داخلی Owner را افشا نمی‌کند.

### امنیت حساب و دسترسی

- Role ceiling در زمان ذخیره و احراز هویت enforce می‌شود؛ رکورد legacy دستکاری‌شده privilege جدید نمی‌سازد.
- `finance.credit` و `finance.refund` قابل delegation به reseller/readonly نیستند.
- Robot Key نمی‌تواند `api.manage`، credit یا refund دریافت کند؛ grantهای legacy نیز هنگام auth حذف می‌شوند.
- TOTP counter واقعیِ کدی که در پنجره ±1 match شده ذخیره می‌شود تا replay window ایجاد نشود.

### Finance / Accounting

- Credit در Runtime API فقط Interactive Owner است.
- Retry همان `event_id` دوباره balance یا audit ایجاد نمی‌کند.
- Event ID در Audit اولین ثبت واقعی قابل ردیابی است.
- Reset دوره نماینده فقط meter دوره جاری را صفر می‌کند؛ lifetime traffic ledger حفظ می‌شود.
- Finance V2 زمان Traffic را از `observed_at` واقعی می‌خواند و lifetime summary را از `owner.lifetime_used_bytes` معتبر می‌گیرد، نه از پنجره محدود آخرین رکوردهای Ledger.

### Nodes

- Node Origin باید HTTPS بدون credential/path/query باشد.
- DNS باید فقط به IPهای globally routable resolve شود.
- اتصال TCP مستقیماً به IP تأییدشده pin می‌شود و TLS همچنان hostname اصلی را verify می‌کند.
- HTTP redirect دنبال نمی‌شود و proxy محیط سیستم در مسیر Node استفاده نمی‌شود.
- request/response size limit و total request deadline وجود دارد.
- تغییر Origin/Token/Enabled وضعیت Probe قدیمی را invalidate می‌کند.
- monitor داخلی، نودهای فعال را دوره‌ای probe و error/latency را persist می‌کند.

## وضعیت تست

CI اصلی روی **Python 3.12 و 3.13** اجرا می‌شود و شامل این دسته‌هاست:

- Policy / RBAC / API key / Session / TOTP
- Accounting / Finance hardening
- Clients / Groups / Subscription
- Settings / Runtime apply / Domain
- Update / Backup / destructive recovery
- Nodes / Node monitor / SSRF boundaries
- Web contract و JavaScript model tests
- Finance V2 و UI stability browserless regressions

تست‌های شبیه‌ساز Xray، HTTP محلی و runnerهای ایزوله به‌صراحت از تست Xray/firewall واقعی تفکیک شده‌اند. نتیجه موفق CI به‌تنهایی جای تست شبکه روی VPS واقعی را نمی‌گیرد.

## گیت‌های اجباری قبل از Production Ready

1. **Fresh install روی VPS تمیز** با systemd و مسیر نصب واقعی.
2. **Xray-core واقعی**: ساخت inbound، create/update/delete client، restart و recovery با ترافیک واقعی.
3. **Domain/TLS واقعی**: صدور Let’s Encrypt، تمدید Certbot، reboot و بررسی Secure Cookie/HSTS.
4. **IP Guard واقعی**: nftables روی کرنل، دو IP واقعی، ban/unban و اطمینان از عدم آسیب به SSH/Panel ports.
5. **Remote Node واقعی**: دو VPS با HTTPS معتبر، probe، deploy inbound، core validate/restart و قطع/وصل شبکه.
6. **Reboot/Crash recovery**: قطع سرویس وسط update/reset و بررسی rollback و ledger integrity.
7. **Load/Scale**: تعداد بالای client/inbound، polling، Bulk actions و SQLite contention با سناریوی اندازه‌گیری‌شده.
8. **Browser QA**: دسکتاپ/موبایل، EN/FA، فرم‌ها، modalها، focus/refresh و عملیات طولانی.
9. **Release hygiene**: VERSION/CHANGELOG/README/SHA256SUMS و بسته Release نهایی باید با همان commit تأییدشده هماهنگ شوند.

## مواردی که نباید بیش از واقعیت ادعا شوند

- IP limit شمارش قطعی «آدم/دستگاه هم‌زمان» نیست؛ مدل آن به منبع مشاهده و topology شبکه وابسته است.
- وجود نام یک protocol در Xray به معنی برابری کامل فرم DARK با تمام قابلیت‌های آن protocol نیست.
- Node monitor جای مانیتورینگ بیرونی دیتاسنتر یا آزمون packet-level را نمی‌گیرد.
- Ledger مالی پایه، فروشگاه/درگاه پرداخت/تسویه جامع محسوب نمی‌شود.
- تا تکمیل گیت‌های VPS واقعی بالا، برچسب پروژه **standalone-lab** باقی می‌ماند.

## مسیر بعدی

پس از سبزشدن CI آخرین `main`، مرحله بعد **Real-VPS Validation + Browser QA** است. هر باگی که در آن مرحله پیدا شود باید به regression test تبدیل شود تا دوباره برنگردد.
