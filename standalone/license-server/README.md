# DARK License Authority V1

Deploy this on a server separate from customer HUBs. Put an HTTPS reverse proxy/domain in front of `127.0.0.1:8099`.
The Ed25519 private signing key and `DARK_LICENSE_ADMIN_TOKEN` stay only on this authority server.

1. Create `/etc/dark-license`, `/var/lib/dark-license`, `/opt/dark-license` owned by a dedicated `dark-license` user.
2. Run `generate-key.py /etc/dark-license/signing.key`; copy only the printed public key to DARK HUBs.
3. Put `DARK_LICENSE_ADMIN_TOKEN=<long random secret>` in `/etc/dark-license/env` mode `0600`.
4. Serve the authority through a stable HTTPS domain. HUB configuration rejects plain HTTP.
5. `POST /admin/licenses` with the admin header creates a Telegram-bound license. `POST /admin/licenses/{id}/replace` revokes the old key and issues a fresh unbound key for migrations. `POST /admin/licenses/{id}/revoke` revokes it.

HUBs check at most every five days after a successful check. A two-day offline grace follows the signed lease. Once enforcement is enabled, expired/revoked/offline-expired licenses make panel mutations read-only; V1 does not terminate existing proxy traffic.

## Telegram operator bot
Set `DARK_LICENSE_TELEGRAM_BOT_TOKEN` and a comma-separated `DARK_LICENSE_TELEGRAM_ADMINS` in the authority env file, then enable `dark-license-bot.service`. Only those Telegram IDs may run `/issue`, `/revoke`, or `/replace`. The bot calls the authority admin API locally; it never receives the Ed25519 private key. Payment confirmation must happen before `/issue` (or a future payment service calls the same admin API).