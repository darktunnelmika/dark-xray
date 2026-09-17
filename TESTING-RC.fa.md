# تست DARK XRAY 0.9.0-rc2

این نسخه برای تست روی VPS واقعی آماده شده و هنوز Stable/Production Ready اعلام نشده است.

## نصب دقیق همین RC

```bash
curl -fL --retry 3 https://raw.githubusercontent.com/darktunnelmika/dark-xray/v0.9.0-rc2/install-online.sh -o /tmp/dark-xray-install.sh
sudo DARK_XRAY_REF=v0.9.0-rc2 bash /tmp/dark-xray-install.sh
```

بعد از نصب:

```bash
sudo darkxray vps-verify
sudo darkxray production-gate
```

## تست reboot واقعی

```bash
sudo reboot
```

بعد از اتصال مجدد:

```bash
systemctl status dark-xray.service --no-pager -l
sudo darkxray vps-verify
```

## چیزهایی که نتیجه‌شان را ثبت کن

- نصب Fresh روی Ubuntu واقعی
- ورود پنل و ساخت Inbound/Client
- اتصال واقعی Client
- reboot و بازگشت Panel/Xray
- Domain/TLS و renewal
- IP Guard روی topology واقعی
- Node دوم روی VPS مستقل
- Update/Rollback همین RC

اگر یکی از این مراحل fail شد، log همان بخش را نگه دار؛ credential/private key را ارسال نکن.
