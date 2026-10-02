# Backup / recovery closeout (rc33)

## Audit findings

The encrypted backup implementation already preserves the SQLite snapshot,
original encryption key, panel configuration, panel TLS and managed inbound TLS.
The full-backup browser endpoint and passphrase dialog also already existed;
legacy backup navigation incorrectly described them as terminal-only. The
active dashboard and Backup page now route to that same existing workflow.
Persian browser acceptance also caught automatic translation corrupting literal
`backup-verify` and `--destination` shell commands. CLI snippets are now explicitly
non-translatable and left-to-right; both languages assert the same exact commands.
A database-only ZIP is explicitly labelled unencrypted and insufficient by itself
for full disaster recovery. No new backup cryptography or payment flow is introduced.

A recovered `restore_domains` table used to retain old-host `ok` / `ready`
observations and certificate paths even though the dedicated Restore nginx/ACME
frontend is a separate host artifact. This release clears those observations
only in the isolated recovery database, preserves domain identity and ACME email,
and reports which domains require rechecking/rebuilding. Customer subscription
URLs/tokens, groups, usage offsets, quotas, expiry, mappings and promotion state
are not reset or reimported. Backup schema 1/2 readers remain supported.

## Existing workflows

Create a full encrypted backup from the Owner dashboard or Backup page and keep
the passphrase separately. The browser creation passphrase travels to the HUB
over the panel connection; use the HTTPS panel. The archive is encrypted by the
existing server-side backup implementation. It may also be delivered to the
configured Telegram backup destination by the existing workflow. Neither the
passphrase nor a decryption key is sent to Telegram.

Terminal alternative (passphrase is requested interactively, never via argv):

```sh
sudo darkxray backup --output /root/dark-full.darkbackup
sudo darkxray backup-verify --archive /root/dark-full.darkbackup
sudo darkxray restore --archive /root/dark-full.darkbackup --destination /root/dark-restore
```

Verify rehearses decryption and recovery into a temporary directory, then deletes
it. Both Verify and Restore report required follow-up actions. Restore refuses
an existing destination, revokes old sessions, leaves core_autostart false, and
requires new bot tokens/forum rebind while preserving business records. Neither
command promotes the recovered copy into the live installation or starts it.

After deliberate installation activation on the replacement host, check the
new host's network/domain settings. Rebuild each Dark Restore domain frontend
using the existing `darkxray restore-tls DOMAIN` flow (the actual imported domain),
then check DNS/TLS readiness in Dark Restore. Source binaries, service definitions,
firewall host allowlists, ACME account/renewal state and the dedicated Restore TLS
frontend are not claimed to be included in the encrypted data archive. The
manifest now makes the frontend exclusion explicit.

Licensing remains frozen. This change neither configures nor enables it. Node,
Tunnel, routing, WARP selection, accounting enforcement and Guard implementation
are unchanged. Tests and recovery rehearsal must not activate a clone against
live customer Nodes or overwrite the production installation.
