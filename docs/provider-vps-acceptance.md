# Stage 4 — independent provider VPS acceptance

This stage is intentionally separate from CI. A green pull request does not prove
provider DNS, WAN reachability, a public CA, machine reboot, customer-facing Node
paths or target-hardware capacity.

## 4A — target VPS local/provider gate

On a disposable or explicitly approved target VPS running the exact candidate:

```bash
sudo darkxray target-vps-gate --phase pre-reboot \
  --expect-source-commit <40-char-candidate-sha> \
  --require-domain-tls --require-letsencrypt --rehearse-renewal
sudo reboot
# after the machine returns
sudo darkxray target-vps-gate --phase post-reboot \
  --expect-source-commit <40-char-candidate-sha> \
  --require-domain-tls --require-letsencrypt
```

The renewal rehearsal is opt-in because it contacts a public ACME staging
service and needs real external HTTP-01 reachability. It must leave the active
certificate/key byte-identical. The gate itself never reboots the host.

## 4B — external public DNS/HTTPS vantage

From an independent machine outside the provider:

```bash
python tools/public-panel-gate.py \
  --domain panel.example.com --port 2087 \
  --expected-ip 203.0.113.10 --require-exact-dns
```

For release acceptance, the expected IP must be the actual target public IP and
must be globally routable. The manual GitHub workflow
`External public panel acceptance` additionally requires both Cloudflare
`1.1.1.1` and Google `8.8.8.8` to return exactly that IPv4 before running
the TLS probe from a GitHub-hosted runner.

PASS requires public CA trust with normal hostname verification, a direct
connection to every expected address, HTTP 200 from `/health`, DARK
XRAY/standalone identity and positive HSTS. No insecure TLS mode, custom test CA,
redirect or proxy is accepted.

## Remaining Stage 4 gates

- **4C — real two-Node WAN:** at least two independently hosted Nodes, Central
  `node-wan-gate`, and one-at-a-time real network loss/recovery rehearsal.
  A CI namespace outage is not a substitute.
- **4D — real traffic/IP topology:** verify the source IP observed by Xray and
  host packet path on every topology that will be sold (direct/tunnel/proxy).
  IP Limit is not accepted from a guessed or manually trusted source.
- **4E — target hardware capacity:** run the fixed 1000-client / 12-worker
  contention workload three independent times on the target class of VPS and
  retain all outcomes. A timeout is a failure and is not erased by a later pass.
- **4F — provider/public ACME:** 4A renewal rehearsal and 4B public endpoint
  must both pass. The isolated Pebble acceptance from Stage 3 remains evidence
  of product behavior, not provider reachability.

Stage 4 closes only when 4A–4F are recorded against the same candidate commit
and target topology. None of these gates authorizes a production customer
migration by itself.
