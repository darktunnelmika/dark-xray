# Node runtime log growth: 15-minute bounded rotation

## Operational finding
On the Netherlands production Node on 2026-10-11, real customer tunnel traffic
grew Xray `runtime/access.log` to approximately 390 MiB while Linux's generic
logrotate timer only ran daily. A 50-MiB threshold checked once per day does not
bound the file to 50 MiB. High network/tunnel load by itself is **not** an
Agent failure or grounds to disable a Node.

## Scoped fix
Install an **independent**, root-owned systemd oneshot logrotate service and
15-minute timer for the existing Node policy:
`/etc/logrotate.d/dark-xray-node-runtime`. The same policy keeps five
rotations, compression, copytruncate and a 50-MiB size trigger. Do not stop,
restart or reconfigure Node Agent, owned Xray, or the independent dark_core
tunnels when rotating logs.

The timer is provisioned on fresh Nodes. Exact-SHA Node updater installs the
new units and enables the timer on update. Existing fleet installations may
need an explicit one-time root-owned bootstrap because older updater binaries
copied only three deployment service files.

## Canary evidence
On Netherlands only: independently verified config/source + SQLite backups;
`logrotate -d` correctly identified the large active log. A manually started
oneshot rotated the log into `access.log.1` and left the Agent's MainPID,
NRestarts=0, Xray and four independent dark_core processes unchanged.
Other production Nodes are **not** implicitly modified by this PR.

## Safety and tests
Systemd timer's ExecStart only invokes `logrotate`; there is no Core/Agent
restart and no database operation. Focused root-free tests assert install,
unit contents, cadence and isolation. Source checksums are strict. Existing
Node watchdog/Fail-Closed/Lease logic and client assignments are untouched.

Do not deploy an unreviewed Node code update just to obtain this timer.
