[فارسی](README.md) | [English](README.en.md)

```text
╔══════════════════════════════════════════╗
║               DARK XRAY                  ║
║       STANDALONE CONTROL PANEL           ║
╚══════════════════════════════════════════╝
```

# DARK XRAY 🖤

پنل مستقل مدیریت Xray با رابط Cyber/Dark، مالکیت مشتری، مدیریت نمایندگان و کنترل سطح دسترسی. زبان پیش‌فرض رابط وب **English / LTR** است و از داخل خود پنل می‌توان بین English و فارسی جابه‌جا شد.

نسخهٔ مبنا: `0.6.0-standalone-lab`

> [!CAUTION]
> **سورس نسخهٔ ۰.۶ کامل است، اما پروژه هنوز Lab/Experimental است و Production Ready اعلام نشده.**
> CI مخزن موفق است، ولی اتصال واقعی کلاینت، اعمال واقعی nftables، صدور گواهی روی همهٔ دیتاسنترها، بازیابی پس از ریبوت و ظرفیت زیر بار باید روی VPS واقعی جداگانه تأیید شوند.

## نصب آنلاین

روی Ubuntu/Debian دارای systemd:

```bash
curl -fL --retry 3 https://raw.githubusercontent.com/darktunnelmika/dark-xray/main/install-online.sh -o /tmp/dark-xray-install.sh
sudo bash /tmp/dark-xray-install.sh
```

Installer دارای روند ۱→۱۰۰ است و سه حالت را تشخیص می‌دهد:

- **Clean** — نصب تازه
- **Partial / Failed** — نگهداری بقایای قبلی در recovery و Repair امن
- **Installed** — Safe Update یا ورود مستقیم به Manager

پروفایل پیشنهادی `Domain + HTTPS/TLS` دامنه، پورت، Owner، Xray-core و Certbot را در همان Wizard مدیریت می‌کند. شکست DNS/TLS دیگر نصب سالم پنل را خراب نمی‌کند و TLS را می‌توان بعداً از `darkxray` دوباره اجرا کرد.

پس از نصب:

```bash
darkxray
```

## DARK XRAY Cyber Control Center

منوی ترمینال، عملیات مدیریتی اصلی را یکجا نگه می‌دارد:

- Live Status برای Panel، IP Guard، CPU، RAM، Disk، Endpoint و Xray
- Start / Stop / Restart و Autostart
- Log Center و Doctor / Diagnostics
- Backup رمزدار و Restore ایزوله
- Reset رمز Owner
- Domain / TLS / Let's Encrypt / Certbot
- IP Guard و پورت‌های دادهٔ تأییدشده
- BBR، پورت‌های Listening و وضعیت nftables
- Safe Update با snapshot برگشت

رمز، API key و private key ذخیره‌شده در منو چاپ نمی‌شوند و عملیات حساس نیاز به تأیید صریح دارند.

## سیاست رمز حساب‌ها

حداقل رمز **حساب‌های DARK XRAY برابر ۸ کاراکتر** و حداکثر ۵۱۲ کاراکتر است. این قانون برای Owner، Admin/Reseller و تغییر رمز یکسان شده است. Passphrase بکاپ رمزدار یک سیاست جداگانه دارد و همچنان حداقل ۱۲ کاراکتر می‌خواهد.

## رابط Cyber/Dark

رابط وب اکنون به‌صورت پیش‌فرض English/LTR است و لایهٔ Cyber اختصاصی DARK دارد: پس‌زمینهٔ grid/scanline، پنل‌های شیشه‌ای تیره، glow سبز/فیروزه‌ای، سایدبار LTR، وضعیت‌های واضح Online/Warning و سوییچ EN/FA. این لایه فقط presentation است و منطق API/مالکیت/Xray را تغییر نمی‌دهد.

## معماری مستقل

```text
DARK UI → DARK API / Access Control → DARK Database → Xray-core
                                              └→ IP guard (nftables)
```

سنایی/3x-ui یا پنل دیگری پیش‌نیاز Runtime نیست. برای تجربهٔ Installer/Manager از الگوهای خوب پنل‌های成熟 مثل نصب مرحله‌ای، مدیریت SSL، Update و Service Control الهام گرفته شده، اما دیتابیس، UI، API و سرویس‌های DARK مستقل‌اند.

## امکانات مبنای ۰.۶

- مدیریت Inbound، Client و مالکیت روی Inboundهای مشترک
- مدیریت نماینده، سقف مشتری، سهمیه و ledger مستقل مصرف
- Session، TOTP و API key
- محدودیت IP با worker مستقل و دسترسی محدود nftables
- Host metrics و Dashboard
- Host، Outbound و Routing با فرم بومی و Advanced JSON
- Backup/Restore
- systemd installation
- تست‌های Python/JavaScript و GitHub Actions روی Python 3.12 و 3.13

## وضعیت اعتبارسنجی

CI شامل Repository Hygiene، Runtime Self-Test نصب‌کننده، Smoke منوی مدیریتی، Smoke رابط English/Cyber، بررسی JavaScript و مجموعهٔ تست‌های ایزوله است. این تست‌ها از Xray test-double و firewall شبیه‌سازی‌شده استفاده می‌کنند؛ بنابراین سبز بودن CI به معنی تأیید نهایی ترافیک واقعی VPN روی هر VPS نیست.

قبل از استفادهٔ production هنوز باید روی VPS واقعی بررسی شوند: اتصال کلاینت Xray، IP Guard واقعی، سهمیه، certificate renewal، reboot recovery و load/concurrency. Multi-node و global IP limit چندنودی نیز هنوز کامل نیستند.

## راهنماها

- [راهنمای فارسی توسعه و نصب آزمایشی](README.fa.md)
- [وضعیت امکانات نسخهٔ ۰.۶](STATUS.fa.md)
- [امنیت](SECURITY.md)
- [اجزای ثالث و مجوزها](THIRD-PARTY-NOTICES.md)
- [وضعیت انتشار](PUBLISH-STATUS.json)

`SHA256SUMS` مربوط به snapshot انتشار قبلی است و پس از تثبیت release/tag بعدی باید دوباره تولید شود؛ برای وضعیت جاری شاخهٔ `main` به CI و commit SHA تکیه کنید.

**Credential واقعی، private key، certificate، database یا log بدون سانسور را در مخزن و Issue منتشر نکنید.**
