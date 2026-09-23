# Stage 5 — release preparation / آماده‌سازی انتشار

Stage 5 برای DARK XRAY `0.9.0-rc7` روی exact candidate زیر اجرا شد:

- release commit: `c196207881bdb38e5a40e5f2d0e265061c24dce7`
- release tree: `fba10c929ce313e5c32cc35151a865f27455fbae`
- Stage 4 accepted runtime: `94ae50548f105bea7e79b40a28f7f5ae3704056d`
- source SHA256SUMS digest: `9fab3d472a181e5a5305bc6b9f6c2b2c7a213d6c656fe01d41d376eb020d4ce6`

این Stage فقط release preparation را می‌بندد و به‌تنهایی Stable/Production Ready اعلام نمی‌کند.

## Exact artifacts

دو build مستقل از همان commit ساخته شدند و `SHA256SUMS.release` آن‌ها دقیقاً یکسان بود.

- `dark-xray-0.9.0-rc7-c196207881bd.tar.gz`
  - SHA256: `e13c357a6de59ba64af05275d156baf2a5dec23a03caf3192ccb477943d8c8ba`
  - bytes: `884012`
- `dark-xray-0.9.0-rc7-c196207881bd.zip`
  - SHA256: `be5392b7121646f8718d3e7bed47d387eda8dde9a8196b59cb28fd8f0781e185`
  - bytes: `1088427`
- manifest SHA256: `a5b13665880dc7907ed786a1523c0eff3cf438e3c317b2a5137f79b1b7ee11ed`
- release checksum file SHA256: `5c9fde1d179b43fb86cd4a594997d51ce5e613fc615d780afd296c0da65705e2`

Artifact شامل `DARK-RELEASE.json` و `release-install.py` است. wrapper قبل از fresh/update، metadata و 307 فایل source را با checksum داخلی verify می‌کند و بعد از نصب identity دقیق release را در `installed-source.json` می‌نویسد.

## Disposable rehearsal

Rehearsal روی Ubuntu 24.04.3 LTS داخل systemd-nspawn انجام شد. container network namespace خصوصی داشت تا هیچ listener یا Xray API روی Node VPS میزبان درگیر نشود.

Dependencyهای Python از 20 wheel pinned و آفلاین نصب شدند. Xray رسمی `v26.3.27` از archive رسمی با SHA256 زیر import شد:

`23cd9af937744d97776ee35ecad4972cf4b2109d1e0fe6be9930467608f7c8ae`

نتایج:

1. artifact verification: PASS
2. fresh install از exact tar.gz: PASS
3. identity بعد fresh: exact `c196207...`
4. `dark-xray.service` و `dark-xray-update.service`: active + enabled
5. `darkxray check`: PASS
6. successful update با همان artifact: PASS
7. source + SQLite rollback snapshot: PASS
8. identity بعد update: exact release commit
9. activation failure کنترل‌شده: injected
10. automatic source/database rollback: PASS
11. marker از source قبلی بعد rollback: restored
12. identity بعد rollback: preserved
13. هر دو service بعد rollback: active
14. SQLite `PRAGMA quick_check`: `ok`

## Fault-injection note

اولین تلاش rollback به‌عنوان evidence پذیرفته نشد. injector اولیه stamp را در `/run` می‌نوشت، ولی service با user غیرروت `darkxray` اجرا می‌شود و به آن مسیر write access نداشت. بنابراین failure در هر restart تکرار شد و activation نهایی rollback را عمداً مسدود کرد.

در اجرای معتبر، stamp داخل `/var/lib/dark-xray` قرار گرفت؛ این مسیر مطابق unit در `ReadWritePaths` است. injector مستقل قبل از rehearsal تست شد و دقیقاً `FIRST=1` و `SECOND=0` داد. فقط اجرای دوم به‌عنوان PASS ثبت شده است.

Structured evidence:

`qa/stage5-release-evidence.json`
## چیزهایی که هنوز باز هستند

Stage 5 این موارد را اثبات نمی‌کند:

- fresh install روی image/provider نهایی production؛
- issue/renewal واقعی production certificate؛
- انتشار Git tag / GitHub Release نهایی؛
- capacity/SLA provider فراتر از Stage 4 workload؛
- semantics کامل global multi-node enforcement.

`production_ready` بنابراین همچنان false می‌ماند.

## Invalidation rule

این evidence فقط برای exact commit و hashهای بالا معتبر است. هر تغییر بعدی در source/runtime، artifact، installer، checksum یا release metadata باید حداقل snapshot/reproducibility و exact-artifact rehearsal را دوباره اجرا کند. هر runtime drift نسبت به Stage 4 نیز باید Stage 4 acceptance را دوباره معتبر کند.
