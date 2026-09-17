# تغییرات DARK XRAY

## 0.9.0-rc2 — Safe Update compatibility hotfix

- RC1 روی نصب‌های قدیمی که Doctor آن‌ها هنوز `panel_route` نداشت، ممکن بود با `panel={}` به‌اشتباه Update را unhealthy تشخیص دهد.
- updater در این حالت فقط به یک probe محلی سخت‌گیرانه fallback می‌کند: UI و `assets/style.css` هر دو باید HTTP 200 بدهند.
- اگر Doctor جدید `panel_route` دارد ولی آن را fail اعلام کند، هیچ fallbackی انجام نمی‌شود و Update همچنان fail-closed می‌ماند.
- regression برای شکل دقیق legacy Doctor اضافه شد.

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
