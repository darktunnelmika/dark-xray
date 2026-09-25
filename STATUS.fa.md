# وضعیت DARK XRAY — main بعد از 0.9.0 RC7

تاریخ بازبینی: **19 سپتامبر 2026**  
نسخهٔ سورس فعلی: **`0.9.0-rc7`**  
Snapshot سبز `main`: **`88e9e5967c3577378f0cca03305b6d127f238176` — Run 761 — 8/8 Gate PASS**  
Tag هدف Release Preparation: **`v0.9.0-rc7`** — هنوز در این branch منتشر نشده

> «سبز بودن CI» در این فایل فقط برای سناریوی مشخص همان Gate معنا دارد. Stage 4 واقعی روی VPS مستقل PASS شده است، اما DARK XRAY هنوز Stable/Production Ready اعلام نشده چون fresh exact-artifact install، production certificate issue/renewal، final artifact rollback rehearsal و مرزهای کامل enforcement چندنودی باز هستند.

## وضعیت کلی

DARK XRAY اکنون پنل مستقل با DB/API/UI، یک Primary Owner، Representatives V2 و کنترل مستقیم Xray-core است. تمرکز شاخه فعلی از ساخت اولیه قابلیت‌ها به **Hardening، evidence واقعی Runtime، بازیابی، امنیت نماینده‌ها و UX پایدار** منتقل شده است.

| بخش | وضعیت فعلی |
|---|---|
| استقلال Runtime | مستقل از Sanayi/3x-ui |
| Inbounds / REALITY | Inbounds V3 + REALITY target compatibility guard + regression ذخیره واقعی |
| Clients / Groups | V4 Command Deck، Basic-first editor، real-activity presence، QR per-config، owner-scoped و Bulk-safe |
| Owner / Representatives V2 | یک Primary Owner + Login/Profile یکپارچه + quota/client cap/prefix/IP/HWID + fixed server-side scope؛ Access Levels UI حذف شده |
| Session / TOTP | revoke + replay-counter hardening |
| Robot API keys | بدون API-key lifecycle و finance mutation حساس |
| Settings V2 | stage/apply privileged + rollback-aware activation |
| Traffic Engine | V4 guided Outbound / Routing / Balancer / Observatory + DNS Guided V3 |
| Public Endpoints | V3 با تفکیک Xray listener از آدرس تحویل به مشتری |
| Security / Sync | Security Center V4 + Sync Runtime V4 |
| Panel/Subscription paths | collision protection در Web و CLI |
| Domain / TLS | HTTPS/HSTS + public staging HTTP-01 rehearsal PASS؛ issue/renewal production certificate روی provider نهایی هنوز باز است |
| Finance / Ledger | event-id idempotency، lifetime/current separation، Owner-only credit |
| Nodes V4 | HTTPS-only + DNS/TLS pinning + orchestrator + inbound/credential mirror + Central traffic + Global IP/device state + subscription failover + reconnect/reset recovery |
| Safe Update / Web Update | root-owned broker + exact-commit CI gate + source/SQLite snapshot + dependency preflight + health-gated rollback |
| Browser | Chromium واقعی، EN/FA، mobile و Inbounds V3 save |
| Real Xray | official v26.3.27 data-plane در CI |
| Kernel nftables | packet-level TCP/UDP enforcement در namespace واقعی Linux |
| systemd recovery | SIGKILL restart + stop/start + single Xray child در CI |
| Python | 3.12 و 3.13 کامل |
| Fresh Install CI | Installer واقعی روی Ubuntu 24.04 + systemd + Xray + vps-verify + production-gate سبز |
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

### 6. Fresh Install end-to-end ✅

Gate جدید `fresh-install` روی Ubuntu 24.04 یک‌بارمصرف، **خود `install-online.sh` تعاملی واقعی** را اجرا می‌کند و سپس موارد زیر را چک می‌کند:

- ساخت service account و SQLite از صفر؛
- دریافت و verify کردن Xray رسمی `v26.3.27`؛
- enabled/active بودن `dark-xray.service` و `dark-xray-update.service`؛
- readiness واقعی endpoint محلی `/health`؛
- `darkxray check`؛
- `darkxray vps-verify`؛
- `darkxray production-gate --json-only`.

این Gate یک race واقعی Installer را پیدا کرد: systemd ممکن بود سرویس را Active گزارش کند ولی HTTP listener هنوز آماده نباشد و Final Doctor فوراً `ConnectionRefusedError` بگیرد. Installer اکنون fail-closed تا آماده‌شدن `/health` صبر می‌کند و اگر سرویس در این فاصله بمیرد نصب را fail می‌کند.

### 7. Load / Scale ✅

`tests/load-scale-smoke.py` با 1000 Client و SQLite contention در Gate اصلی `main` اجرا می‌شود. این تست برای regression و contention مفید است، اما benchmark سخت‌افزار/provider مقصد نیست.

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

## Hardeningهای main بعد از RC7

- **Traffic Engine V4** Outbound، Routing، Balancer و Observatory را به مسیر Guided/Basic-first تبدیل کرده و تصمیم Routing و selector expansion را قبل از ذخیره قابل مشاهده می‌کند.
- **DNS Guided V3** کنترل ساخت‌یافتهٔ DNS را بدون اجبار کاربر به Raw JSON ارائه می‌دهد.
- **Public Endpoints V3** پورت/آدرس listener را از endpoint تحویل‌شده به مشتری جدا و اثر Delivery را preview می‌کند.
- **Clients V5 Delivery Center** خروجی Subscription و readiness مربوط به endpoint/failover را با مسیر Guided هماهنگ می‌کند.
- **Nodes V4 Orchestrator** semantics مربوط به Public Endpoint و source-inbound failover port را واضح‌تر کرده است.
- **Security Center V4** state مربوط به Local + Node IP/HWID را کنار مرز enforcement واقعی nftables نمایش می‌دهد.
- **Sync Runtime V4** external-disable و missing-runtime را از هم جدا می‌کند و recovery صریح برای credential گمشده دارد.
- Refreshهای هم‌پوشان UI coalesce می‌شوند و stale async render دیگر صفحهٔ جدیدتر را overwrite نمی‌کند؛ regression Browser برای این raceها سبز است.
- Gate دائمی **Fresh Install E2E** اضافه شد و race readiness بعد از systemd start در Installer رفع شد.

## Hardeningهای مهم RC7

- **Nodes V3 Assignment** در Add/Edit Node اجازه می‌دهد فقط Inboundهای انتخاب‌شده به آن Node تعلق بگیرند؛ Clone دستی از مسیر اصلی حذف شده است.
- **Node credential mirror** فقط Clientهای متصل به Inboundهای انتخاب‌شده را با شناسه Mirror جدا روی Remote Xray sync می‌کند.
- **Node Traffic Sync** از counterهای Remote snapshot می‌گیرد؛ اولین snapshot baseline است و deltaهای بعدی با event ID پایدار وارد Ledger مرکزی می‌شوند، بنابراین retry/reconnect مصرف را دوباره حساب نمی‌کند.
- **Global current usage** برای Client از Local + همه Nodeهای accounting جمع می‌شود و quota Client/Owner از مصرف Nodeها هم اثر می‌گیرد.
- **Traffic reset recovery** با reset ID پایدار و cache نتیجه روی Agent انجام می‌شود؛ retry بعد از خطا همان final counter قبلی را بازیابی می‌کند و reset دوباره اجرا نمی‌شود.
- **Node recovery state** تعداد failure/recovery، زمان آخرین outage/recovery و آخرین Traffic Sync را نگه می‌دارد.
- **Global IP Guard state** فقط Observationهایی را که Local/Node با source مستقیمِ تأییدشده دارند merge و deduplicate می‌کند؛ telemetry باید برای همه Nodeهای Deployشده fresh باشد تا بلاک جدید ساخته شود. اگر telemetry بعداً stale شود، بلاک موجود کورکورانه آزاد نمی‌شود.
- **Global Device state** فقط SHA-256 HWID را بین Node و Central جابه‌جا می‌کند؛ HWID خام ارسال نمی‌شود. عبور مجموع digestها از `limitHwid` یک blocker مستقل `global_device_quota` ایجاد می‌کند.
- **Global enforcement boundary** با disable شدن Credential در Central و Mirrorها اعمال می‌شود. nftables همچنان روی هر Host مستقل است؛ این قابلیت distributed firewall نیست.
- **Node Failover** برای هر Node `Data Address`، `Priority` و Enable/Disable دارد. فقط Nodeهای Online، Deployشده و بدون خطا وارد Subscription می‌شوند؛ Clash/Mihomo گروه `DARK FAILOVER` از نوع fallback و فرمت‌های Raw/Base64/JSON endpointهای سالم اضافی می‌گیرند.
- **WAN validation gate** با `darkxray node-wan-gate` همان HTTPS/TLS pinning Production را از Central به Nodeها تست می‌کند. حالت `--expect-outage` فقط وقتی PASS می‌شود که قطع و Recovery واقعی خارج از خود Gate مشاهده شود.

- **Web Update Center** فقط برای Interactive Owner است؛ Reseller/API Key اجازه Check/Start ندارند.
- **Update Broker** با root و Unix peer credential اجرا می‌شود؛ Web process non-root باقی می‌ماند و arbitrary shell عبور نمی‌کند.
- Candidate قبل از Start به SHA ثابت resolve و CI همان Commit دقیق بررسی می‌شود؛ CI غیرسبز/نامشخص Web Update را قفل می‌کند.
- Progress و reconnect مرحله‌های snapshot/apply/restart/rollback را حتی هنگام restart پنل قابل بازیابی می‌کنند.
- `vps-verify` فعال و enabled بودن Broker و پاسخ status آن را Hard Gate می‌داند.
- یک بار bootstrap از RC6 با installer آنلاین لازم است؛ بعد از آن مسیر اصلی آپدیت داخل پنل است.

## Hardeningهای مهم RC5

- **Representatives V2** صفحه Access Levels/Role Matrix را حذف کرده و مدیریت نماینده را یکپارچه کرده است: Login، فعال/غیرفعال، Inbound، quota، max clients، Prefix، Max IP و Max HWID در یک فرم Owner-only.
- **Single Owner boundary** ساخت Owner دوم/Readonly جدید از API اصلی رد می‌شود و CLI recovery نیز فقط در نبود Owner اجازه bootstrap یک Primary Owner می‌دهد؛ رکوردهای legacy به‌صورت غیرمخرب برای migration باقی می‌مانند.
- **Representative client policy** Prefix و سقف IP/HWID در Backend روی Create/Edit enforce می‌شوند و نماینده scope دلخواه یا cross-owner قابل انتخاب در UI ندارد.
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

Stage 4 روی exact head `94ae50548f105bea7e79b40a28f7f5ae3704056d` و tree یکسان merge commit `37a640817fe64e8e5c066df7f691b624c6ed5707` PASS شده است. reboot واقعی، HTTPS/HSTS، دو Node واقعی، verified source-IP، Down → Recovery، ACME staging rehearsal و سه workload هزارکلاینتی دیگر گیت باز Stage 4 نیستند.

1. **Fresh exact-artifact install** روی image/provider نهایی مقصد.
2. **Production certificate provider gate**: issue و renewal واقعی گواهی production؛ Stage 4 فقط public staging HTTP-01 rehearsal را ثابت کرد.
3. **Global multi-node enforcement نهایی**: semantics نود آفلاین، IP/HWID policy و packet-level enforcement که همچنان host-local است.
4. **Provider capacity/SLA sizing** فراتر از workload پذیرش 3×1000 Client / concurrency 12.
5. **Stable promotion / publication** فقط بعد از fresh provider install، production certificate و enforcement boundaryهای باز؛ tag نهایی باید همین snapshot ثابت را نشان دهد.

## مرزهایی که نباید بیش از واقعیت ادعا شوند

- IP limit شمارش قطعی انسان/دستگاه فیزیکی نیست؛ NAT، dual-stack و topology روی مشاهده اثر دارند.
- REALITY/Transport formهای DARK الزاماً همه optionهای هر نسخه Xray را UI نمی‌کنند؛ Advanced JSON برای این مرز باقی است.
- Kernel CI رفتار nftables DARK را ثابت می‌کند، نه routing/provider خاص VPS مشتری.
- systemd recovery CI reboot واقعی ماشین نیست.
- Ledger فعلی سیستم حسابداری عملیاتی پنل است، نه فروشگاه/درگاه/تسویه جامع.
- Multi-node اکنون Traffic accounting، verified global IP/device blockers و client-side subscription failover را در Central همگرا می‌کند؛ packet-level nftables همچنان Host-local است و transparent server-side routing/failover برای Clientهای generic URI ادعا نمی‌شود. WAN outage/recovery روی دو VPS واقعی در Stage 4 PASS شده، اما این نتیجه packet-level enforcement سراسری روی نود آفلاین را ثابت نمی‌کند.

## مسیر بعدی

Stage 5 exact-artifact rehearsal روی commit `c196207881bdb38e5a40e5f2d0e265061c24dce7` PASS شده است: source/release checksum، build reproducible، fresh install، successful update و automatic rollback. مرحله بعد بستن provider fresh install، production certificate، global enforcement و انتشار tag/GitHub Release برای همین snapshot ثابت است.

جزئیات Stage 5: [`docs/stage5-release-preparation.md`](docs/stage5-release-preparation.md)

جزئیات ماتریس evidence: [`docs/VALIDATION.md`](docs/VALIDATION.md)
