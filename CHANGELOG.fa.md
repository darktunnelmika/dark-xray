# تغییرات DARK XRAY

## Unreleased — بعد از 0.9.0-rc7

- فعلاً موردی ثبت نشده است.

## 0.9.0-rc7 — Stage-4 validated Release Candidate

- **Traffic Engine V4** برای Outbound / Routing / Balancer / Observatory با UX Guided/Basic-first و preview تصمیم Routing اضافه شد.
- **DNS Guided V3**، **Public Endpoints V3**، **Clients V5 Delivery Center**، **Nodes V4 Orchestrator**، **Security Center V4** و **Sync Runtime V4** وارد Browser QA واقعی شده‌اند.
- refreshهای هم‌پوشان UI coalesce می‌شوند و stale async render دیگر صفحهٔ جدیدتر را overwrite نمی‌کند؛ raceهای کشف‌شده به regression Browser تبدیل شدند.
- Gate دائمی **Fresh Install E2E** اضافه شد: Installer تعاملی واقعی روی Ubuntu 24.04، Xray رسمی، systemd، Update Broker، readiness، `vps-verify` و `production-gate`.
- همین Gate یک startup race واقعی را پیدا کرد: `dark-xray.service` می‌توانست قبل از آماده‌شدن HTTP listener Active شود و Final Doctor `ConnectionRefusedError` بگیرد. Installer اکنون تا endpoint محلی `/health` به‌صورت fail-closed صبر می‌کند.
- Snapshot `88e9e5967c3577378f0cca03305b6d127f238176` روی `main` در Run 761 هر **۸ Gate** را پاس کرده است.

- **Stage 4 independent VPS acceptance** روی exact head `94ae50548f105bea7e79b40a28f7f5ae3704056d` PASS شد؛ merge commit `37a640817fe64e8e5c066df7f691b624c6ed5707` همان tree دقیق را دارد.
- reboot واقعی، HTTPS/HSTS، دو Node واقعی، source-IP verified و Ready → Down → Recovered روی Node انتخابی ثبت شد.
- Let's Encrypt public staging HTTP-01 renewal rehearsal با deploy-hook restart PASS شد و certificate production جایگزین نشد.
- Load acceptance سه run مستقل 1000 Client / concurrency 12 را PASS کرد؛ هر سه 100/100 PATCH، SQLite `quick_check=ok` و بدون 5xx/timeout بودند.
- باگ write amplification در `bulk_adjust_500` با batch policy/managed/core writes بسته شد؛ زمان سه run نهایی 1.358s، 1.422s و 1.534s بود.
- RC7 هنوز Stable/Production Ready نیست؛ fresh exact-artifact install، production certificate issue/renewal، final artifact rollback rehearsal و مرزهای کامل global multi-node enforcement باز هستند.

- Update Center داخل خود پنل اضافه شد و فقط برای Interactive Owner قابل دسترسی است؛ Reseller و API Key نمی‌توانند Check/Start آپدیت انجام دهند.
- یک `dark-xray-update.service` مستقل و root-owned با Unix socket محدود اضافه شد؛ Web process همچنان non-root باقی می‌ماند و هیچ shell دلخواهی از API قابل اجرا نیست.
- کانال‌های Latest Verified / Stable / Release Candidate / Exact Ref اضافه شدند؛ هر Ref قبل از نصب به Commit ثابت ۴۰ کاراکتری resolve می‌شود.
- Latest Verified فقط وقتی قابل نصب است که Workflow اصلی GitHub Actions برای همان Commit دقیق، completed + success باشد.
- Web downgrade به نسخه قدیمی‌تر حتی با CI سبز رد می‌شود؛ بازیابی نسخه فقط از rollback snapshot مسیر امن است.
- Preflight داخل پنل DB quick_check، سرویس، فضای rollback، Git و CI را نشان می‌دهد؛ Candidate نامعتبر یا CI قرمز/نامشخص fail-closed قفل می‌شود.
- Safe Update وضعیت مرحله‌به‌مرحله می‌نویسد: health → resolve → source validation → rollback preflight → dependencies → source snapshot → DB snapshot → apply → restart/health → success یا rollback.
- هنگام ری‌استارت پنل، Job داخل Broker مستقل ادامه پیدا می‌کند و UI بعد از برگشت سرویس خودکار reconnect و نتیجه را نمایش می‌دهد.
- در خطای activation، سورس و SQLite قبلی مثل قبل خودکار restore می‌شوند؛ bootstrap اولیه از نسخه‌های pre-broker هم rollback-compatible باقی مانده است.
- Update Center Changelog، Commit، CI status، progress timeline و log tail را داخل خود پنل نشان می‌دهد.
- Fresh install / Safe Update آنلاین Broker را نصب و فعال می‌کنند؛ `vps-verify` از این نسخه وجود و سلامت Update Broker را Hard Gate می‌داند.
- سرورهای RC6 برای فعال شدن Broker فقط یک بار باید با installer آنلاین RC7 bootstrap شوند؛ بعد از آن آپدیت‌های آینده از خود پنل انجام می‌شوند.
- **Nodes V3 Assignment**: هنگام Add/Edit Node می‌توان Inboundهای همان نود را انتخاب کرد؛ فقط همان Inboundها روی Remote Mirror می‌شوند.
- Credential کلاینت‌های متصل به Inbound انتخاب‌شده با شناسه Mirror جدا روی Node همگام می‌شود؛ Inboundهای انتخاب‌نشده و Clientهای آن‌ها به آن Node ارسال نمی‌شوند.
- Clone دستی Inbound از مسیر اصلی حذف و با `Sync assigned` جایگزین شد؛ Save اولیه Sync فوری می‌زند و monitor پس‌زمینه نیز assignmentها را دوره‌ای reconcile می‌کند.
- حذف assignment باعث حذف Mirror مربوط به همان Agent Token می‌شود. Traffic accounting نود هنوز محلی است و در Central تجمیع نمی‌شود.

## 0.9.0-rc6 — Clients V4 cleanup pass

- IP/HWID action و نمایش IP/HWID از صفحه و Detail کاربران حذف شد؛ policy هسته همچنان دست‌نخورده است.
- More Actions و دکمه سه‌نقطه حذف شدند؛ هر ردیف فقط OPEN و QR دارد و Detail فقط Edit و QR را نگه می‌دارد.
- فونت‌های صفحه Clients، فیلترها، وضعیت، سرویس، مصرف، انقضا، فرم و Detail خواناتر شدند.
- فلش Online دیگر glyph وابسته به bidi/font نیست و با CSS triangle دقیقاً روی محور Signal line قرار می‌گیرد.
- HWID از فرم Advanced پنهان شد و مقدار موجود هنگام Edit حفظ می‌شود.

## 0.9.0-rc5 — Clients V4 cyber command deck

- صفحه Clients از نو با Command Deck دارک/سایبری ساخته شد؛ Sidebar دائمی حذف و Search/Quick Presence/Filter/Sort در یک نوار کنترل جمع شدند.
- اکشن‌های هر ردیف به سه مسیر واضح `OPEN / QR / More` کاهش یافتند؛ Edit، IP/HWID، Enable/Disable، Reset و Delete داخل منوی More قرار گرفتند.
- Create/Edit Client به Basic-first تبدیل شد: Identity، Inbound، Plan، Expiry و IP limit در مسیر اصلی؛ HWID، reset schedule، comment و XTLS Flow داخل Advanced بسته هستند.
- Telegram ID و Custom UUID از فرم روزمره حذف شدند و credentialهای جدید به تولید امن سمت سرور سپرده می‌شوند.
- Bulk Create هم به فرم ساده سه‌بخشی Batch / Service / Plan منتقل شد.
- Group filter همچنان owner-scoped است و Login Owner دیگر به‌اشتباه به‌عنوان Owner Profile پیش‌فرض فرض نمی‌شود.
- Detail و Action menu ظاهر سایبری یکدست، Responsive و Reduced-motion-safe دارند.

## 0.9.0-rc4 — Clients V3 + REALITY Guard

- Clients V3 صفحه کاربران را به workspace سازمان‌یافته با Summary، Owner/Inbound/Status/Presence filters، Sort، Group sidebar و Detail view ارتقا می‌دهد.
- وضعیت Online/Idle/Offline از activity واقعی Traffic Ledger / access observation / device activity گرفته می‌شود؛ Enabled بودن اکانت به‌تنهایی Online محسوب نمی‌شود.
- indicator سایبری Online شامل نقطه Pulse، خط Signal و فلش متحرک است و Reduced Motion را رعایت می‌کند.
- QR/Share کامل شد: Subscription و هر Config تولیدشده QR مستقل دارند، Copy و Download SVG هم اضافه شد.
- REALITY target guard برای Xray pin‌شده v26.3.27 اضافه شد؛ `www.microsoft.com` به‌دلیل مشکل شناخته‌شده TLS Certificate record بزرگ‌تر از parser limit fail-closed رد می‌شود و `www.bing.com:443` پیش‌فرض پیشنهادی است.
- Scanner و Inbounds V3 compatibility/advisory را نمایش می‌دهند و target مسدود قابل انتخاب/ذخیره نیست.
- XTLS Vision روی VLESS gRPC/XHTTP دیگر وارد config/share-link نمی‌شود و backend اتصال Client با Flow ناسازگار را رد می‌کند.

## 0.9.0-rc3 — Control Center V2 audit

- منوی ترمینال از نظر Backup/Restore، Guard، Update/Ref، Validation gates و عملیات systemd audit و بازطراحی شد.
- Backup/Restore CLI واقعی با passphrase تعاملی، verify و isolated restore اضافه شد.
- Guard status/clear فقط از Broker احرازشده انجام می‌شود؛ نام جدول nft صحیح `dark_xray_ip` است و Stop دیگر به‌اشتباه ادعای clear ban ندارد.
- Update منویی از Tag/Commit/Ref دقیق پشتیبانی می‌کند و `check`، `vps-verify` و `production-gate` داخل Control Center در دسترس‌اند.
- BBR قبل از persistence، پشتیبانی kernel را بررسی می‌کند و در خطای activation فایل/runtime قبلی را restore می‌کند.
- Main Control Matrix از CPU/RAM شلوغ پاک شد؛ منابع سیستم در Diagnostics باقی مانده‌اند.

## 0.9.0-rc2 — Safe Update compatibility hotfix

- RC1 روی نصب‌های قدیمی که Doctor آن‌ها هنوز `panel_route` نداشت، ممکن بود با `panel={}` به‌اشتباه Update را unhealthy تشخیص دهد.
- updater در این حالت فقط به یک probe محلی سخت‌گیرانه fallback می‌کند: UI و `assets/style.css` هر دو باید HTTP 200 بدهند.
- اگر Doctor جدید `panel_route` دارد ولی آن را fail اعلام کند، هیچ fallbackی انجام نمی‌شود و Update همچنان fail-closed می‌ماند.
- regression برای شکل دقیق legacy Doctor اضافه شد.
- Account & Access قبل از تغییر رمز/نام، revoke session/API key یا TOTP reset همیشه فهرست شماره‌دار Login Ownerها را نشان می‌دهد؛ حتی اگر فقط یک Login Owner وجود داشته باشد. Profileهای بدون Login/Password جدا و غیرقابل انتخاب نمایش داده می‌شوند.

## 0.9.0-rc1 — Release Candidate برای تست VPS

- همه Gateهای CI روی Python 3.12/3.13، Chromium، Xray رسمی، nftables packet-level و systemd recovery سبز هستند.
- Load/Scale smoke با 1000 Client و SQLite contention به Gate اصلی اضافه و سبز شده است.
- CLI در برابر `PYTHONHOME/PYTHONPATH` خارجی harden شده تا virtualenv نصب‌شده منحرف نشود.
- `kernel-firewall` شامل TCP/UDP drop، management-port preservation، nft timeout، unban و foreign-table ownership refusal است.
- `systemd-recovery` شامل `vps-verify`، SIGKILL→Restart=on-failure، stop/start و تک‌بودن Xray child است.
- مستندات، Validation path، VERSION و checksum snapshot برای RC یکدست شده‌اند.
- این نسخه Production Ready اعلام نمی‌شود تا VPS هدف، reboot واقعی، TLS renewal و دو VPS Node واقعی پاس شوند.

## 0.8.2-standalone-lab — Hardening و Validation

### Runtime / Installer / Recovery

- `darkxray check` به health-check خواندنی تبدیل شد و دیگر با instance lock سرویس زنده رقابت نمی‌کند.
- Panel URI Path و Subscription Path در Web و CLI collision-safe شدند.
- Settings privileged همچنان Stage → Apply می‌شوند و rollback مسیر Runtime حفظ شده است.
- Safe Update با preflight dependency، SQLite `quick_check`، snapshot مستقل Source/DB و Doctor پس از activation سخت‌گیرانه‌تر شد.
- `darkxray vps-verify` برای readiness نصب واقعی اضافه شد.
- `darkxray production-gate` readiness نصب را با lab ایزوله روی همان Xray binary نصب‌شده ترکیب می‌کند.

### Inbounds / Clients / UX

- Inbounds V3 با Browser QA واقعی وارد Gate اصلی شد.
- crash ذخیره Inboundهای غیر-XHTTP ناشی از دسترسی اشتباه به `xhttpSettings.xPaddingBytes` اصلاح و regression دائمی اضافه شد.
- Save error اینباند دیگر silent نمی‌ماند و پیام backend را نمایش می‌دهد.
- Language Switch دیگر روی drawer/modal کنترل‌های Save را نمی‌پوشاند.
- Group filter با Owner + Group کار می‌کند تا Groupهای هم‌نام نماینده‌ها مخلوط نشوند.
- تغییر Search/Filter/Owner، Bulk selection مخفی قبلی را invalidate می‌کند.
- Auto-refresh هنگام کار روی فرم، focus/caret/scroll همان صفحه را حفظ می‌کند و page navigation را قفل نمی‌کند.

### Security / RBAC

- Role ceiling هنگام ذخیره و احراز هویت enforce می‌شود و grantهای legacy خارج از Role حذف می‌شوند.
- Robot API Key نمی‌تواند `api.manage` یا mutationهای مالی حساس را نگه دارد.
- Credit در Runtime API فقط Interactive Owner است.
- TOTP counter واقعی window match ذخیره می‌شود تا replay window بسته شود.
- Node egress با public HTTPS، DNS pinning، TLS hostname verification، no-redirect و no-environment-proxy سخت‌گیرانه‌تر شد.

### Finance / Accounting

- Event IDهای مالی idempotent شدند؛ retry یک event دوباره Balance یا Audit نمی‌سازد.
- Reset دوره، lifetime traffic ledger را پاک نمی‌کند.
- Finance V2 زمان مصرف را از `observed_at` و lifetime را از منبع authoritative Owner می‌گیرد.
- Summary محدود آخرین Ledger rows دیگر به‌عنوان Lifetime معرفی نمی‌شود.

### Nodes

- Node connection به IP validate‌شده pin می‌شود و TLS همچنان hostname اصلی را verify می‌کند.
- HTTP redirect و proxy environment برای Node request دنبال نمی‌شوند.
- monitor داخلی، نودهای فعال را دوره‌ای probe و latency/error را persist می‌کند.

### Validation Evidence

روی `main` Gateهای زیر اجرا می‌شوند:

- Python 3.12 و 3.13؛
- Chromium Browser QA؛
- Xray رسمی `v26.3.27` و data-plane واقعی `SOCKS → VLESS → HTTP`؛
- nftables واقعی در Linux network namespace با TCP/UDP drop، timeout و unban؛
- systemd واقعی با service user محدود، SIGKILL restart و stop/start recovery.

این evidence جای Target-VPS Validation را نمی‌گیرد. Reboot واقعی ماشین، TLS issue/renewal provider هدف، topology واقعی IP Guard، دو VPS Node و Load/Scale هنوز گیت Release هستند.

## 0.6 baseline

- استقلال اجرایی از پنل‌های دیگر.
- نصب مستقل systemd و service account غیر-root.
- IP Guard worker جداگانه با Unix socket و nftables.
- سیاست IP پویا، Host/Outbound/Routing forms و Advanced JSON.
- Ledger مصرف، Reset دوره‌ای، Backup رمزدار و Restore ایزوله.
- Domain/Certbot tooling و Manager اولیه.

برای وضعیت دقیق جاری به `STATUS.fa.md` و `docs/VALIDATION.md` مراجعه کنید.
