# Security / امنیت

DARK XRAY 0.6 is experimental. No independent security audit or real-network certification is claimed.

Do not expose plain HTTP publicly. Keep initial access on loopback with SSH forwarding until TLS is configured. Do not run the whole panel as root. Confirm management ports before enabling the separate firewall worker.

Never paste passwords, API tokens, subscription credentials, private keys, live databases or unredacted logs into public issues. Report suspected vulnerabilities privately to the repository owner through an available private channel. A public issue should contain only a non-sensitive description, not exploit data or live access details. No security-response SLA is promised.

The repository checker is a small defense-in-depth scan, not a complete secret scanner or security audit. `.gitignore` does not protect files uploaded directly through a browser. Review the actual upload selection.

رمز، کلید، لینک اشتراک مشتری، دیتابیس و لاگ واقعی را منتشر نکن. محدودیت IP بر IP و پورت اثر دارد و جای تشخیص قطعی دستگاه نیست. قبل از فعال‌سازی فایروال، مسیر مستقیم، IP واقعی و حفاظت SSH را روی سرور آزمایشی بررسی کن.
