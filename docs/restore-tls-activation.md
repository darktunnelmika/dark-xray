# Restore subscription frontend

Grouping and importing do not create a network listener. A legacy link such as
`https://old.example:2096/sub/token` needs the original hostname, port, path and
query to continue working; changing only DNS is insufficient.

On the HUB, after importing and pointing every A/AAAA record to the HUB:

```bash
sudo darkxray restore-tls old.example --dry-run
sudo darkxray restore-tls old.example
```

Prerequisites: the `nginx` binary and `certbot`. This command does not install
packages or stop unrelated services. Certificate issuance uses standalone
HTTP-01 and requires free public port 80. It refuses legacy port 80 deployments,
which require an operator-managed webroot or DNS-01 renewal arrangement instead.
It does not assume port 443 and refuses panel-port conflicts.

The dedicated `dark-xray-restore.service` reads
`/etc/dark-xray-restore/nginx.conf`, with one site per imported domain. It does not
load or change global nginx sites. The panel origin, panel TLS and Xray listeners
remain untouched. Requests retain the original Host, URI and query. Upstream
HTTPS validates the panel certificate and uses its real SNI hostname; disabling
TLS verification is not a fallback. Nonmatching Host headers and write methods
are denied; cookies and authorization headers are not forwarded. Access logging
is disabled to keep customer subscription tokens out of proxy access logs.

Certbot's deploy hook reloads only this frontend. Port 80 remains free between
renewals so existing standalone panel renewal continues to work. No production
reboot is required. On a replacement HUB, reapply `restore-tls` after restoring
the database and repointing DNS; proxy configuration is reconstructed from the
saved imports, and certificates can be reissued.

The domain check verifies a token-free readiness endpoint on every imported
port, including TLS hostname/chain validation. A certificate file alone is not
reported as a working subscription frontend.

HEAD requests to imported subscription URLs validate the real routing and
subscription generation but do not update `first_seen`, `last_seen` or migration
events. GET still records a subscription update. Expired subscriptions remain
expired; activation never resets quota, credentials, group membership or expiry.

References: nginx HTTP proxy module documentation (Host and URI preservation,
`proxy_ssl_verify`); Certbot `certonly --standalone` and renewal deploy hooks.
