# وضعیت DARK XRAY 0.9.0 RC7

تاریخ بازبینی: **18 سپتامبر 2026**  
برچسب فعلی: **`0.9.0-rc7`**

> «سبز بودن CI» در این فایل فقط برای سناریوی مشخص همان Gate معنا دارد. DARK XRAY هنوز Production Ready اعلام نشده، چون بخشی از گیت‌ها باید روی VPS/Provider هدف انجام شوند.

## وضعیت کلی

DARK XRAY اکنون پنل مستقل با DB/API/UI/RBAC و کنترل مستقیم Xray-core است. تمرکز شاخه فعلی از ساخت اولیه قابلیت‌ها به **Hardening، evidence واقعی Runtime، بازیابی، امنیت نماینده‌ها و UX پایدار** منتقل شده است.

| بخش | وضعیت فعلی |
|---|---|
| استقلال Runtime | مستقل از Sanayi/3x-ui |
| Inbounds / REALITY | Inbounds V3 + REALITY target compatibility guard + regression ذخیره واقعی |
| Clients / Groups | V4 Command Deck، Basic-first editor، real-activity presence، QR per-config، owner-scoped و Bulk-safe |
| Reseller / RBAC | role ceiling، legacy sanitization و server-side scope |
| Session / TOTP | revoke + replay-counter hardening |
| Robot API keys | بدون API-key lifecycle و finance mutation حساس |
| Settings V2 | stage/apply privileged + rollback-aware activation |
| Panel/Subscription paths | collision protection در Web و CLI |
| Domain / TLS | workflow موجود؛ provider/live renewal هنوز گیت VPS است |
| Finance / Ledger | event-id idempotency، lifetime/current separation، Owner-only credit |
| Nodes V3 | HTTPS-only + DNS pinning + TLS hostname verification + inbound assignment + client credential mirror + health/sync monitor |
| Safe Update / Web Update | root-owned broker + exact-commit CI gate + source/SQLite snapshot + dependency preflight + health-gated rollback |
| Browser | Chromium واقعی، EN/FA، mobile و Inbounds V3 save |
| Real Xray | official v26.3.27 data-plane در CI |
| Kernel nftables | packet-level TCP/UDP enforcement در namespace واقعی Linux |
| systemd recovery | SIGKILL restart + stop/start + single Xray child در CI |
| Python | 3.12 و 3.13 کامل |
| Load / Scale CI | 1000 Client + SQLite contention smoke سبز |

## Gateهای واقعی که الان در `main` اجرا می‌شوند

### 1. Real Xray data-plane ✅

CI باینری رسمی `Xray v26.3.27` را با helper داخلی و SHA-256 رسمی Release دریافت می‌کند و `tools/smoke-real.py` را اجرا می‌کند. موارد اثبات‌شده:

- SOCKS → VLESS → HTTP واقعی؛
- دو Client واقعی روی Inbound مشترک؛
- Subscription تولیدشده توسط DARK؛
- Traffic metering و ledger؛
- quota isolation نماینده؛
- top-up recovery و حفظ manual disable؛
- عدم refund ترافیک تاریخی با reset/delete؛
- stop تمیز Core.

### 2. Kernel nftables packet gate ✅

`tests/kernel-firewall-smoke.py` در network namespaceهای disposable از nftables و packet واقعی استفاده می‌کند:

- TCP drop بعد Ban؛
- UDP drop بعد Ban؛
- سالم‌ماندن management port؛
- timeout واقعی nft؛
- explicit unban؛
- جلوگیری از overwrite جدول same-name با marker بیگانه؛
- بدون تغییر ruleset namespace اصلی runner.

این evidence رفتار Ruleهای DARK را ثابت می‌کند، نه topology خاص یک دیتاسنتر/تونل/CDN را.

### 3. systemd recovery gate ✅

`tests/systemd-recovery-smoke.sh` مسیرهای production-like و user واقعی `darkxray` را می‌سازد و بررسی می‌کند:

- service enable/start؛
- `vps-verify` بعد نصب؛
- SIGKILL Main PID و Restart=on-failure با PID جدید؛
- stop/start سالم؛
- حفظ autostart؛
- باقی‌ماندن دقیقاً یک Xray child بعد recovery.

`real_machine_reboot_tested` عمداً **false** است؛ این Gate reboot/power-cycle واقعی host نیست.

### 4. Browser QA ✅

Chromium واقعی:

- Login/Cookie session؛
- صفحات اصلی Owner؛
- ساخت و ذخیره Inbound V3؛
- English/LTR ↔ فارسی/RTL؛
- focus/scroll stability در refresh؛
- viewport موبایل 390px و overflow checks.

### 5. Python 3.12 / 3.13 ✅

مجموعه تست‌های application شامل:

- Policy / RBAC / Sessions / API key / TOTP؛
- Accounting / Finance / destructive recovery؛
- Settings / Runtime / Domain / Subscription؛
- Clients / Groups / Hosts / Operations؛
- Nodes / SSRF / Node monitor؛
- Backup / Update transaction و permission hardening؛
- Web contracts و JavaScript regressions.

## ابزارهای Validation نصب‌شده

Readiness فقط خواندنی:

```bash
sudo darkxray vps-verify
```

Readiness + data-plane lab ایزوله با همان Xray binary نصب:

```bash
sudo darkxray production-gate
```

برای JSON:

```bash
sudo darkxray production-gate --json-only
```

`production-gate` برای lab خودش temporary DB/ports ایجاد می‌کند و customer DB یا firewall نصب‌شده را تغییر نمی‌دهد.

## Hardeningهای مهم RC7

- **Nodes V3 Assignment** در Add/Edit Node اجازه می‌دهد فقط Inboundهای انتخاب‌شده به آن Node تعلق بگیرند؛ Clone دستی از مسیر اصلی حذف شده است.
- **Node credential mirror** فقط Clientهای متصل به همان Inboundهای انتخاب‌شده را با شناسه Mirror جدا روی Remote Xray sync می‌کند؛ Traffic accounting بین Node و Central هنوز تجمیع نمی‌شود.

- **Web Update Center** فقط برای Interactive Owner است؛ Reseller/API Key اجازه Check/Start ندارند.
- **Update Broker** با root و Unix peer credential اجرا می‌شود؛ Web process non-root باقی می‌ماند و arbitrary shell عبور نمی‌کند.
- Candidate قبل از Start به SHA ثابت resolve و CI همان Commit دقیق بررسی می‌شود؛ CI غیرسبز/نامشخص Web Update را قفل می‌کند.
- Progress و reconnect مرحله‌های snapshot/apply/restart/rollback را حتی هنگام restart پنل قابل بازیابی می‌کنند.
- `vps-verify` فعال و enabled بودن Broker و پاسخ status آن را Hard Gate می‌داند.
- یک بار bootstrap از RC6 با installer آنلاین لازم است؛ بعد از آن مسیر اصلی آپدیت داخل پنل است.

## Hardeningهای مهم RC5

- **Clients V4 Command Deck** Sidebar دائمی را حذف و کنترل‌ها را به Search/Presence/Filter/Sort جمع می‌کند؛ ردیف‌ها فقط OPEN / QR دارند و IP/HWID و More Actions حذف شده‌اند.
- **Create/Edit Basic-first** فقط Identity/Inbound/Plan/Expiry/IP را در مسیر اصلی نشان می‌دهد و گزینه‌های کم‌مصرف را داخل Advanced نگه می‌دارد.
- **Clients V3 Online** از آخرین activity واقعی Client محاسبه می‌شود: تا 60 ثانیه Online، تا 5 دقیقه Idle و بعد Offline؛ این status ادعای socket دائماً باز نیست.
- QR هر Config/Subscription exact payload خودش را دارد و لینک انتخابی مستقیماً encode می‌شود.
- REALITY target policy در backend fail-closed است؛ Microsoft target روی Xray v26.3.27 مسدود و Bing در لیست پیش‌فرض اول است.
- Flow سازگار با Transport کنترل می‌شود و Vision روی gRPC/XHTTP رد می‌شود.

## Hardeningهای مهم 0.8.2

- `darkxray check` بدون گرفتن instance lock سرویس زنده اجرا می‌شود.
- Panel Path و Subscription Path در Web/CLI نمی‌توانند هم‌پوشانی ناامن داشته باشند.
- Clients V2 هنگام Search/Filter/Owner change، Bulk selection مخفی را invalidate می‌کند.
- Group filter با Owner + Group کار می‌کند.
- Role ceiling هم هنگام ذخیره و هم auth enforce می‌شود.
- Robot Key grantهای legacy حساس را هنگام auth از دست می‌دهد.
- TOTP counter واقعی window match ذخیره می‌شود.
- Finance event retry دوباره Balance/Audit ایجاد نمی‌کند.
- Node request به IP validate‌شده pin می‌شود ولی TLS hostname اصلی verify می‌شود؛ redirect/proxy-env بسته است.
- Node monitor health/error/latency را دوره‌ای persist می‌کند.
- Auto-refresh هنگام کار با فرم focus/caret/scroll را بی‌دلیل خراب نمی‌کند.
- Inbounds V3 crash مربوط به XHTTP padding روی transportهای غیر-XHTTP بسته و regression دائمی اضافه شده است.

## گیت‌های باقی‌مانده قبل از Production Ready

1. **Fresh install روی VPS هدف واقعی** با image/provider نهایی و exact commit/release.
2. **Reboot/Power-cycle واقعی ماشین** و بررسی Panel/Xray/SQLite بعد boot.
3. **Domain/TLS provider gate**: issue و renewal واقعی Let's Encrypt، Secure Cookie و HSTS.
4. **IP Guard روی topology واقعی**: تأیید اینکه source مشاهده‌شده در Xray همان packet source قابل enforce است.
5. **دو VPS واقعی Node** با HTTPS معتبر، deploy/probe/core action و network loss/recovery.
6. **Capacity روی VPS هدف**؛ smoke هزار Client/SQLite contention در CI سبز است ولی ظرفیت provider/hardware باید روی مقصد اندازه‌گیری شود.
7. **Update/Rollback rehearsal** روی VPS disposable با exact release artifact نهایی.
8. **Stable promotion** بعد از پاس‌شدن گیت‌های VPS واقعی همین RC.

## مرزهایی که نباید بیش از واقعیت ادعا شوند

- IP limit شمارش قطعی انسان/دستگاه فیزیکی نیست؛ NAT، dual-stack و topology روی مشاهده اثر دارند.
- REALITY/Transport formهای DARK الزاماً همه optionهای هر نسخه Xray را UI نمی‌کنند؛ Advanced JSON برای این مرز باقی است.
- Kernel CI رفتار nftables DARK را ثابت می‌کند، نه routing/provider خاص VPS مشتری.
- systemd recovery CI reboot واقعی ماشین نیست.
- Ledger فعلی سیستم حسابداری عملیاتی پنل است، نه فروشگاه/درگاه/تسویه جامع.
- Multi-node هنوز به معنی global distributed IP/accounting convergence کامل نیست.

## مسیر بعدی

مرحله بعد، تست **`0.9.0-rc7`** روی VPS هدف، TLS/Node/Reboot واقعی و سپس promotion همان کاندید به Stable است. هر failure جدید باید قبل از Stable به regression test تبدیل شود.

جزئیات ماتریس evidence: [`docs/VALIDATION.md`](docs/VALIDATION.md)
