# Contributing

Keep DARK standalone. Do not add a dependency on the Sanayi panel, its API, login or pages.

Preserve the familiar inbound/client workflow. Ownership and permissions must be enforced server-side. Shared inbounds must not be shut down just because one reseller is limited. Historical traffic must survive client resets/deletion. Quota recovery must not undo manual disable reasons.

Create a branch for a focused change, add tests and run:

```bash
python tools/repo-check.py
bash tests/run-tests.sh
```

Document live tests separately from test doubles. Do not label simulated firewall success or mocked Xray lifecycle tests as live connectivity validation. Do not include real credentials in tests, reports or screenshots.

The project uses Python 3.11+ and Node.js for pure JavaScript mapping tests. Installer, firewall and certificate changes require separate isolated-VPS testing. Never run those tests against production customer servers.
