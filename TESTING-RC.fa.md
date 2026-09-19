# تست Snapshot فعلی DARK XRAY

نسخهٔ سورس فعلی: **`0.9.0-rc7`**  
Snapshot سبز برای تست VPS: **`88e9e5967c3577378f0cca03305b6d127f238176`**  
CI روی `main`: **Run 761 — 8/8 Gate PASS**

> هنوز Tag به نام `v0.9.0-rc7` منتشر نشده است. برای اینکه تست VPS دقیقاً روی همان سورسی انجام شود که همهٔ Gateها را پاس کرده، فعلاً از Commit بالا استفاده کن. این Snapshot هنوز Stable/Production Ready اعلام نشده است.

## نصب دقیق Snapshot تست‌شده

```bash
curl -fL --retry 3 https://raw.githubusercontent.com/darktunnelmika/dark-xray/88e9e5967c3577378f0cca03305b6d127f238176/install-online.sh -o /tmp/dark-xray-install.sh
sudo DARK_XRAY_REF=88e9e5967c3577378f0cca03305b6d127f238176 bash /tmp/dark-xray-install.sh
```

بعد از نصب:

```bash
sudo darkxray check
sudo darkxray vps-verify
sudo darkxray production-gate
```

Gate خودکار Fresh Install همین مسیر را روی Ubuntu 24.04 یک‌بارمصرف اجرا می‌کند و نصب، Xray رسمی، systemd، Update Broker، readiness محلی، `vps-verify` و `production-gate` را پوشش می‌دهد. این evidence جای VPS/Provider واقعی مقصد را نمی‌گیرد.

## تست reboot واقعی

```bash
sudo reboot
```

بعد از اتصال مجدد:

```bash
systemctl status dark-xray.service dark-xray-update.service --no-pager -l
sudo darkxray vps-verify
sudo darkxray production-gate
```

## چیزهایی که روی VPS هدف باید ثبت شوند

- Fresh Install روی image/provider نهایی؛
- ورود پنل، ساخت Inbound و Client و اتصال واقعی؛
- Traffic Engine / Outbound / Routing با سناریوی واقعی همان سرور؛
- reboot یا power-cycle و بازگشت Panel/Xray/SQLite؛
- Domain/TLS واقعی، Secure Cookie/HSTS و renewal؛
- IP Guard روی topology واقعی همان دیتاسنتر/تونل؛
- Node دوم روی VPS مستقل، HTTPS معتبر، Traffic/Security Sync و Down → Recovery؛
- Load/Capacity روی سخت‌افزار مقصد؛
- Update/Rollback با exact artifact کاندید نهایی.

اگر مرحله‌ای fail شد، log همان بخش را نگه دار؛ credential، password، token، private key و secret را ارسال نکن.
