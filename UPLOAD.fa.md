# بارگذاری DARK XRAY روی GitHub

این بسته فقط آمادهٔ بارگذاری است؛ تهیهٔ این فایل به معنی ساخته‌شدن مخزن آنلاین نیست.

## روش مرورگر

۱. وارد حساب `darktunnelmika` شو و از `New repository` مخزنی با نام `dark-xray` بساز. برای این نسخهٔ آزمایشی Private پیشنهاد می‌شود؛ Public انتخابی آگاهانه برای انتشار عمومی سورس است. افزودن خودکار README، gitignore و License را خاموش بگذار؛ هر سه در بسته وجود دارند.

۲. ZIP را استخراج کن. **محتویات داخل پوشهٔ `dark-xray`** را در صفحهٔ `uploading an existing file` یا `Add file → Upload files` بکش؛ خود ZIP و خود پوشهٔ بیرونی را آپلود نکن. پوشه‌های `backend`، `web`، `tools`، `tests`، `deploy`، `docs`، `qa` و `.github` باید در ریشه قرار بگیرند. فایل‌های نقطه‌دار `.gitignore` و `.gitattributes` را جا نگذار.

۳. پیام commit پیشنهادی:

```text
Initial import: DARK XRAY v0.6 standalone lab
```

سپس تغییرات را در `main` ثبت کن. تعداد فایل‌های بسته کمتر از ۱۰۰ است تا در یک بارگذاری مرورگر جا شود. مستندات GitHub می‌گوید هر فایل مرورگر تا ۲۵ MiB و هر بار تا ۱۰۰ فایل مجاز است.

از داخل مرورگر هیچ `config.json`، `.env`، دیتابیس یا پوشهٔ runtime را بعداً آپلود نکن؛ `.gitignore` در آپلود مستقیم مرورگر مانع انتخاب آن‌ها نمی‌شود.

## روش خط فرمان، بدون دادن توکن در گفتگو

روی رایانهٔ خودت با Git و GitHub CLI نصب‌شده، وارد پوشهٔ پروژه شو:

```bash
gh auth login --hostname github.com --web
bash tools/publish-github.sh
```

اسکریپت به‌صورت پیش‌فرض مخزن **Private** با نام `dark-xray` در حساب `darktunnelmika` می‌سازد و فایل‌ها را commit و push می‌کند. اگر ورود CLI مربوط به حساب دیگری باشد متوقف می‌شود. مخزن قبلی را بازنویسی و force-push نمی‌کند.

فقط برای انتشار عمومی آگاهانه:

```bash
bash tools/publish-github.sh --public
```

اجرای این اسکریپت در محیط تهیهٔ بسته انجام نشده است. GitHub CLI ممکن است برای ایجاد مخزن یا ارسال workflow مجوز لازم را در ورود خودش درخواست کند؛ توکن یا رمز را اینجا ارسال نکن.

## بعد از بارگذاری

صفحهٔ اول باید README فارسی را با انتخاب English نشان دهد. در Actions، نتیجهٔ واقعی workflow را بررسی کن؛ وجود فایل workflow به معنی اجرای موفق آن نیست. راهنمای نصب کامل در `README.fa.md` است.

## منابع رسمی

- https://cli.github.com/manual/gh_repo_create
- https://cli.github.com/manual/gh_auth_login
- https://docs.github.com/en/repositories/working-with-files/managing-files/adding-a-file-to-a-repository
- https://docs.github.com/en/actions/tutorials/build-and-test-code/python
