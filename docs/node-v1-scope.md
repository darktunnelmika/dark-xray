# Node v1 scope and remaining release gates / محدودهٔ نسخهٔ نخست نودها

[فارسی](#persian) | [English](#english)

<a id="persian"></a>

## فارسی — تصمیم محدوده، نه مجوز انتشار

تصمیم پروژه در ۲۳ سپتامبر ۲۰۲۶: نسخهٔ نخست نودها رفتار موجود هنگام قطع مدیریت و HWID اعلامیِ اشتراک را با مرزهای زیر حفظ می‌کند. «نسخهٔ نخست» نام محدودهٔ محصول است؛ شمارهٔ نسخه یا برچسب انتشار جدید نیست. هیچ قابلیت قطع سخت آفلاین یا قفل سخت‌افزاری با این تصمیم اضافه نمی‌شود.

### قطع ارتباط مدیریت

اگر ارتباط Hub با Agent قطع شود ولی مسیر مشتری برقرار باشد، نود با آخرین تنظیمات اعمال‌شده ادامه می‌دهد. **ممکن است لینک مستقیم قبلی حتی پس از تمام‌شدن سهمیه یا تغییر محدودیت در Hub کار کند و مصرف اضافه ایجاد شود.** مسدودکردن دریافت اشتراک یا حذف لینک از اشتراک تازه، لینکِ قبلاً دریافت‌شده را باطل نمی‌کند.

پس از برگشت ارتباط، پردازش عادی تغییرات در انتظار را پیگیری و شمارنده‌های هنوز قابل‌خواندن را دریافت می‌کند. این بازیابی، مصرف یا اطلاعاتی را که دیگر روی هیچ‌طرف موجود نیست بازسازی نمی‌کند. درخواست در انتظار، اعمال‌شده گزارش نمی‌شود.

در محدودهٔ نسخهٔ نخست، قطع مدیریت به‌تنهایی فرمان خودکار برای خاموش‌کردن همهٔ مشتری‌ها نیست. مجوز زمان‌دار، قطع آنی توزیع‌شده و قطع سختِ آفلاین تعهد این نسخه نیستند. این انتخاب به معنی تضمین روشن‌ماندن نود در برابر سایر خرابی‌ها یا نادیده‌گرفتن Stop دستی و محدودیت‌های قبلاً اعمال‌شده نیست.

### HWID و محدودیت دستگاه

HWID در این محصول **شناسهٔ اعلامی برنامه هنگام دریافت اشتراک** است. سقف ثبت شناسه و سیاست مبتنی بر آن وجود دارد؛ اما دستگاه فیزیکی در هر اتصال VLESS احراز نمی‌شود و **جلوگیری قطعی از کپی کانفیگ تضمین نشده است**. یک برنامهٔ تازه می‌تواند از لینک کپی‌شده استفاده کند؛ تکرار یک شناسهٔ اعلامی هم اثبات یکسان‌بودن سخت‌افزار نیست.

نام دقیق قابلیت در راهنما و معرفی محصول «محدودیت شناسهٔ اشتراک (HWID اعلامی)» است، نه «ضدکپی قطعی»، «قفل سخت‌افزاری» یا «شمارش دقیق دستگاه‌های هم‌زمان». Agent سبک از هر اتصال VLESS شناسهٔ سخت‌افزار دریافت نمی‌کند. این تصمیم، قابلیت موجود را حذف یا سقف‌ها را غیرفعال نمی‌کند.

### محدودیت IP و اتصال‌های آزموده‌شده

IP Limit، آدرس‌های متمایزِ اخیراً مشاهده‌شده و معتبر را تجمیع می‌کند، نه تعداد قطعی افراد یا دستگاه‌ها. یک IP مشترک پشت NAT و تغییر IP یک کاربر می‌توانند بر شمارش اثر بگذارند. برای مسیر تانل، CDN یا پروکسی، تا مبدأ اصلی و اعتمادپذیری آن تأیید نشده است نباید منبع را صرفاً برای فعال‌کردن محدودیت، معتبر علامت زد. اعمال nftables همچنان محلی است و با حذف مجوز همان مشتری روی چند نود یکی نیست.

محدودهٔ آزمون اتصال، [۱۶ ترکیب ثبت‌شده](node-real-transports.md) است؛ XHTTP در آن `packet-up` و mKCP با گزینه‌های پیش‌فرض است. این سند ادعای آزمون همهٔ حالت‌های XHTTP، payloadهای UDP، mux، mKCP سفارشی، همهٔ برنامه‌های کلاینت یا همهٔ تانل‌ها ندارد. گزینه‌های موجود حذف نمی‌شوند؛ هر گزینهٔ اضافی که قرار است با تضمین عرضه شود به پذیرش مشخص خودش نیاز دارد.

### وضعیت مرحلهٔ دوم

مبنای شواهد کد **`5ac338cd372aab7946be979014992a01a9d43639`** است؛ ۱۳ مجموعهٔ CI آن در بررسی ۲۳ سپتامبر ۲۰۲۶ کامل و موفق بودند. آزمون‌ها شامل مصرف/سهمیه و انقضا، اعمال خودکار، IP معتبر و HWID اعلامی، اتصال‌های منتخب، Stop صریح و اصلاح نوشتن تکراری حسابداری‌اند. پیوند شواهد و مرزهای اجرا:

- [اجرای خودکار و قطع مدیریت](node-automatic-limits.md)
- [IP و HWID](node-real-security.md)
- [ماتریس اتصال](node-real-transports.md)
- [بررسی بار و حسابداری](load-contention.md)

با ثبت دو تصمیم بالا و این راهنما، **مرحلهٔ دوم در محدودهٔ آزمایشگاهیِ منتخب نسخهٔ نخست جمع‌بندی و بسته می‌شود**؛ هیچ نتیجهٔ جدیدی برای کدِ تغییرکرده از این مبنا به‌صورت خودکار استنتاج نمی‌شود. بسته‌شدن این مرحله، پذیرش VPS مستقل یا مجوز Merge، انتشار یا آپدیت عملیاتی نیست. خطای جدید نباید به‌دلیل بسته‌شدن این مرحله نادیده گرفته شود.

### کارهای باز، بدون اعلام رفع‌شدن

| مورد | وضعیت | معیار پذیرش باقی‌مانده |
|---|---|---|
| تایم‌اوت تاریخی بار | نوشتن تکراری اصلاح شده؛ علت یگانهٔ ReadTimeout قدیمی اثبات نشده است. | سه اجرای مستقلِ همان سناریوی ۱۰۰۰ مشتری/۱۲ worker روی محیط VPS آزمایشی، با همان مهلت‌ها و نگهداری همهٔ نتایج. تایم‌اوت، گم‌شدن ویرایش یا اختلاف اعتبار باید بررسی شود و مانع انتشار بماند؛ اجرای موفق بعدی شکست را پاک نمی‌کند. سه موفقیت هم اثبات علت تاریخی یا SLA نیست. |
| IP در مسیر واقعی | شبکهٔ جدا در آزمایش، جای تانل/دیتاسنتر نیست. | مبدأ واقعی و اعتمادپذیری آن برای مسیر عرضه‌شده تأیید شود؛ در نبود تأیید، ادعای enforce معتبر مجاز نیست. |
| ترکیب خارج از ماتریس | هنوز تحت پوشش این پذیرش نیست. | پیش از تضمین آن ترکیب، آزمون مسیر و برنامهٔ موردنظر انجام شود؛ محدودیت پوشش در معرفی محصول روشن بماند. |

ترتیب برنامه ثابت است: **۳. دامنه و گواهی؛ ۴. VPSهای مستقل؛ ۵. آماده‌سازی انتشار.** صدور/تمدید واقعی گواهی، نصب/بوت و شبکه و بار روی VPS و rehearsal نصب/آپدیت/بازگشتِ بستهٔ نهایی هنوز از این سند نتیجه نمی‌شوند. سه اجرای بار بالا هنوز انجام نشده‌اند. تضمین سخت آفلاین و ضدکپی، با تصمیم محدوده از تعهد نسخهٔ نخست خارج‌اند، نه اینکه پیاده‌سازی یا آزمون‌شده باشند.

این تغییر فقط راهنماست؛ کد محصول، UI، workerها، نصب‌کننده و نسخه تغییر نمی‌کنند. `main`، سرور عملیاتی، DNS، تانل و اطلاعات مشتری با آن تغییر نمی‌کنند. به‌روزرسانی و اعتبارسنجی متن‌های داخل UI در مرور نهایی انتشار نیز باید از همین مرزها پیروی کند.

<a id="english"></a>

## English — scope decision, not deployment approval

Project decision recorded on 23 September 2026: the first node release retains the existing management-outage behavior and self-declared subscription HWID policy. “First release” describes product scope, not a new version number or tag. It does not introduce an offline authorization lease or physical-device binding.

### Management disconnection

When the Hub cannot reach an Agent but its customer data path still works, the node continues with its last applied configuration. **A previously delivered direct URI may remain usable after quota exhaustion or a restriction change at the Hub, causing additional usage.** Denying subscription retrieval or omitting a link from a fresh subscription does not revoke an earlier copy.

On reconnection, normal workers reconcile pending changes and import counters that remain readable. They cannot recreate usage or data no longer retained anywhere. Pending deployment is not reported as completed.

Management loss alone does not automatically stop every customer's service in v1. Authorization leases, instantaneous distributed cutoff and hard offline cutoff are not promised. This is not a guarantee of node uptime under other failures and does not override an explicit Stop or a restriction already applied on the node.

### HWID and device limits

HWID is a **client-declared identifier supplied when requesting a subscription**. Registration ceilings and their associated policies exist, but VLESS connections do not authenticate a physical device on every connection. **Copied-configuration prevention is not guaranteed.** A fresh client can use a copied URI; reuse of a declared identifier does not attest to identical hardware.

Describe the feature as a “subscription-identifier limit (declared HWID),” not guaranteed anti-sharing, hardware locking or an exact simultaneous-device count. Lightweight Agents do not receive a hardware identifier from each VLESS connection. This decision neither removes the feature nor disables existing limits.

### Source IP and transport coverage

The IP limit aggregates distinct recently observed, trusted addresses, not exact people or physical devices. Shared NAT and changing addresses matter. Original-source preservation and trust must be verified for the actual tunnel, CDN or proxy; never mark an unknown source verified merely to enable enforcement. Host-local nftables enforcement is distinct from per-customer authorization removal across nodes.

The accepted transport coverage is the [explicit 16-case matrix](node-real-transports.md), with XHTTP `packet-up` and default mKCP options. It is not all XHTTP modes, UDP payloads, mux settings, custom mKCP options, client applications or provider tunnels. Existing options are not removed; any additional combination offered with a guarantee needs its own acceptance evidence.

### Stage 2 checkpoint and open risks

The code evidence baseline is **`5ac338cd372aab7946be979014992a01a9d43639`**; all 13 CI workflows completed successfully when reviewed on 23 September 2026. The linked [automatic policy](node-automatic-limits.md), [IP/HWID](node-real-security.md), [transport](node-real-transports.md) and [load](load-contention.md) reports define the actual observations and exclusions.

Recording these decisions and this guide closes **Stage 2's selected v1 laboratory acceptance scope only**. It does not infer results for later code changes, prove independent-provider operation, waive new failures or approve merging, releasing or production updates.

The historical load ReadTimeout remains unattributed: redundant accounting writes were fixed, but their removal does not prove the sole historical cause. Stage 4 retains three independent runs of the existing 1,000-client/12-worker scenario on a disposable target VPS with unchanged timeouts and every result retained. A timeout, lost edit or credit discrepancy blocks release pending investigation; a later successful retry does not erase it. Three successes are not an SLA or proof of historical causality. Those three VPS runs have not occurred yet.

Stage 4 also retains original-source validation on deployed tunnels. Combinations outside the matrix remain unverified unless separately accepted before being guaranteed. Hard offline cutoff and guaranteed anti-sharing are outside v1's agreed scope, not implemented fixes.

Next milestones stay **3. domain/certificates; 4. independent VPS acceptance; 5. release preparation**. Real issuance/renewal, installation/boot, WAN and load, and exact-package install/update/rollback still need evidence. This documentation-only change does not alter product code, UI, workers, installers, version, main, production servers, DNS, tunnels or customer data. Final release review must keep any in-product help consistent with these boundaries.
