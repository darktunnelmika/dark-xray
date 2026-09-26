# تست Release Candidate فعلی DARK XRAY

نسخهٔ کاندید یکپارچه: **`0.9.1-rc1`**

> Stage 8 روی integration branch انجام می‌شود. RC باید هم `main` فعلی و هم Stage 7 Smart Routing را در ancestry داشته باشد. هیچ نصب/آپدیت Production بخشی از Gate نیست؛ fresh/update تست‌ها فقط روی runner/disposable target اجرا می‌شوند.

# تست Snapshot فعلی DARK XRAY

نسخهٔ سورس فعلی: **`0.9.0-rc7`**
Stage 4 runtime head: **`94ae50548f105bea7e79b40a28f7f5ae3704056d`**
Merged main baseline: **`37a640817fe64e8e5c066df7f691b624c6ed5707`**
Tree هر دو: **`539d93abc8a6ce833f20d382797730bf3f09e47d`**
Exact-head CI: **14/14 workflow PASS**

> Stage 5 exact-artifact rehearsal روی commit `c196207881bdb38e5a40e5f2d0e265061c24dce7` PASS شده است: source/release checksum، build reproducible، disposable fresh install، successful update و automatic rollback. نسخه هنوز Stable نیست؛ provider fresh install، production certificate، enforcement boundaryهای باز و tag/GitHub Release نهایی باقی مانده‌اند.

## نصب دقیق baseline ادغام‌شده

```bash
curl -fL --retry 3 https://raw.githubusercontent.com/darktunnelmika/dark-xray/37a640817fe64e8e5c066df7f691b624c6ed5707/install-online.sh -o /tmp/dark-xray-install.sh
sudo DARK_XRAY_REF=37a640817fe64e8e5c066df7f691b624c6ed5707 bash /tmp/dark-xray-install.sh
```

بعد از نصب:

```bash
sudo darkxray check
sudo darkxray vps-verify
sudo darkxray production-gate
```

## Evidence واقعی Stage 4

- `target_vps_gate_passed=true` بعد از reboot واقعی و boot-id جدید؛
- source commit و config بعد reboot ثابت ماندند؛
- HTTPS/HSTS و certificate verification PASS؛
- دو Node واقعی healthy + failover-ready؛
- Node انتخابی در همان run به‌ترتیب Ready → Down → Recovered مشاهده شد؛
- source-IP واقعی و verified برای client آزمایشی روی Node ثبت شد؛
- Let's Encrypt public staging HTTP-01 renewal rehearsal و deploy-hook restart PASS شد؛ certificate production جایگزین نشد؛
- سه run مستقل 1000 Client / concurrency 12 همگی PASS؛ هر سه 100/100 PATCH داشتند؛
- `bulk_adjust_500` در سه run به‌ترتیب 1.358s، 1.422s و 1.534s بود؛
- SQLite در هر سه run `quick_check=ok` و WAL باقی ماند؛
- failureهای تاریخی حذف یا با retry overwrite نشدند.

## گیت‌های باز قبل از Stable

- Fresh install از exact ZIP/tar.gz/tag نهایی روی image/provider مقصد؛
- issue و renewal واقعی production certificate روی DNS/provider نهایی؛
- global multi-node enforcement کامل، مخصوصاً semantics نود آفلاین و packet-level enforcement host-local؛
- capacity/SLA sizing فراتر از workload پذیرش Stage 4؛
- انتشار tag/GitHub Release نهایی برای exact snapshot تأییدشده؛ هر source change نیازمند rehearsal مجدد است.

اگر مرحله‌ای fail شد، log همان بخش را نگه دار؛ credential، password، token، private key و secret را منتشر نکن.
