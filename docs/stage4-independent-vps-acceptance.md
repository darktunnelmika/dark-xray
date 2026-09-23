# Stage 4 — independent VPS acceptance / پذیرش VPS مستقل

این سند **راهنمای پذیرش** است، نه اعلام آمادگی انتشار. Stage 4 فقط وقتی بسته می‌شود
که شواهد روی VPS مستقل و همان commit نامزد ثبت شوند. موفقیت CI جای DNS/WAN،
reboot واقعی، ACME عمومی یا بار روی VPS هدف را نمی‌گیرد.

## چیزهایی که گیت جدید ثابت می‌کند

- نصب دقیق همان commit با `installed-source.json`.
- Production Gate و سلامت HTTPS/HSTS محلی.
- حداقل دو Node واقعی از مسیر HTTPS مدیریت.
- rehearsal قطع و برگشت Node انتخابی؛ خود گیت شبکه را قطع نمی‌کند.
- مشاهدهٔ تازهٔ IP مبدأ برای یک کلاینت مشخص روی Node، با `sourceVerified=true`.
- Let's Encrypt **public staging** با HTTP-01 واقعی و `certbot renew --dry-run`.
- اجرای deploy hook واقعی و مشاهدهٔ restart سرویس DARK.
- سه اجرای مستقل سناریوی 1000 client / 12 worker با گزارش جدا و حفظ‌شده.
- reboot واقعی با boot-id جدید، در حالی که source commit و config ثابت می‌مانند.

Renewal عمومی با staging، گواهی production را جایگزین نمی‌کند؛ اما deploy hook را
با `--run-deploy-hooks` اجرا می‌کند و بنابراین DARK restart می‌شود. این بخش فقط
روی VPS دورریختنی/آزمایشی اجرا شود.

## مرحله 1 — baseline پیش از reboot

روی VPS مستقلِ نصب‌شده از commit نامزد:

```bash
sudo darkxray target-vps-gate \
  --phase pre-reboot \
  --expect-source-commit <40_CHAR_COMMIT_SHA> \
  --require-domain-tls \
  --min-nodes 2
```

اگر PASS شد:

```bash
sudo reboot
```

## مرحله 2 — پذیرش کامل Stage 4 بعد از reboot

پیش از فرمان زیر یک کلاینت آزمایشی واقعی را از IP معلوم به Node موردنظر وصل کنید
تا observation تازه ایجاد شود. در مدت watch نیز برای `--expect-outage` مسیر
مدیریت همان Node را واقعاً قطع و سپس برگردانید.

```bash
sudo darkxray target-vps-gate \
  --phase post-reboot \
  --stage4 \
  --expect-source-commit <40_CHAR_COMMIT_SHA> \
  --require-domain-tls \
  --min-nodes 2 \
  --node-watch-seconds 120 \
  --expect-outage <NODE_ID> \
  --expect-source-ip '<NODE_ID>,<CLIENT_EMAIL>,<EXPECTED_PUBLIC_IP>' \
  --source-ip-max-age 180 \
  --rehearse-public-renewal \
  --allow-service-restart \
  --run-load-acceptance
```

`--stage4` عمداً ناقص اجرا نمی‌شود. برای این profile وجود commit دقیق، TLS دامنه،
دو Node یا بیشتر، outage/recovery، source-IP تازه، renewal عمومی و سه اجرای بار
الزامی است.

## Public ACME rehearsal

گیت از endpoint رسمی Let's Encrypt staging استفاده می‌کند:

`https://acme-staging-v02.api.letsencrypt.org/directory`

فرمان renewal محدود به lineage فعال DARK است و با `--dry-run --run-deploy-hooks`
اجرا می‌شود. PASS یعنی staging ACME از اینترنت HTTP-01 را اعتبارسنجی کرده، deploy
hook واقعی اجرا شده، PID سرویس DARK عوض شده و HTTPS پنل قبل و بعد سالم بوده است.

این نتیجه به معنی صدور دوبارهٔ certificate production یا تست revocation نیست.

## سه اجرای بار

`darkxray load-gate` اکنون روی نصب واقعی موجود است. Stage 4 آن را دقیقاً سه بار با
پارامترهای ثابت زیر اجرا می‌کند:

- clients: 1000
- workers/concurrency: 12
- PATCH timeout داخل سناریو: همان 45 ثانیهٔ قبلی
- دیتابیس: SQLite موقت و جدا از دیتابیس مشتری
- API: HTTP واقعی برنامه، با test-engine و بدون دست‌زدن به Xray مشتری

هر run باید 100 PATCH از 100 را بپذیرد، integrity check و resource-credit checks را
پاس کند و بدون timeout تمام شود. گزارش‌ها overwrite نمی‌شوند و زیر مسیر زیر
نگه داشته می‌شوند:

`/var/lib/dark-xray/qa/stage4-load/<batch>/`

یک failure در batch باعث FAIL کل batch است؛ retry بعدی نباید برای پاک‌کردن شواهد
شکست قبلی استفاده شود. سه PASS معیار پذیرش این workload است، نه SLA و نه اثبات
علت یگانهٔ ReadTimeout تاریخی.

## Source-IP acceptance

فرمت:

```text
--expect-source-ip NODE_ID,CLIENT_EMAIL,EXPECTED_IP
```

IPv4 و IPv6 پشتیبانی می‌شوند. گیت فقط زمانی این evidence را قبول می‌کند که:

1. Agent منبع را verified گزارش کند؛
2. همان client mirror، همان IP دقیق را داشته باشد؛
3. `lastSeen` از پنجرهٔ freshness تعیین‌شده قدیمی‌تر نباشد.

گیت خودش اتصال مشتری تولید نمی‌کند و IP را جعل یا trusted نمی‌کند. بنابراین ترافیک
آزمایشی باید واقعاً از مسیر عرضه‌شده عبور کند.

## چیزهایی که Stage 4 خودش تزریق نمی‌کند

- reboot ماشین؛
- قطع شبکهٔ Node؛
- traffic مشتری؛
- صدور certificate production؛
- تغییر firewall برای ساختن نتیجهٔ مصنوعی.

اگر هرکدام از موارد لازم در عمل قابل ایجاد یا مشاهده نباشند، Stage 4 بسته نیست.

## خروجی مورد انتظار

PASS نهایی باید هم‌زمان این‌ها را داشته باشد:

- `target_vps_gate_passed=true`
- `reboot.ok=true`
- `base.production.production_gate_passed=true`
- `base.source.ok=true`
- `base.tls.passed=true`
- `base.nodes.passed=true`
- `base.nodes.source_observation_verified=true`
- `public_renewal.renewal_rehearsed=true`
- `load_acceptance.passed=true`
- سه report مستقل load با `passed=true`

تا زمانی که این اجرای واقعی روی VPS مستقل انجام نشده، Stage 4 فقط **آمادهٔ اجرا**
است و نباید به‌عنوان production-ready یا release-approved توصیف شود.
