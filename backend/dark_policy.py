#!/usr/bin/env python3
"""DARK XRAY policy/ledger development module (Python 3.10+, stdlib only).

This is NOT a complete Xray panel backend. It can consume real local Xray access
logs and, with explicit enablement, use dedicated Fail2ban jails. It never touches
x-ui.service, x-ui.db, the existing 3x-ipl jail, SSH rules or the Xray binary.

An IP quota means distinct recently observed source IPs, NOT exact concurrent
sessions or people. Address bans can affect other clients behind the same NAT.
Forwarded original IPs are not necessarily the packet source on this host.
Enforcement is therefore refused for opaque/proxy/tunnel sources in this module.
"""
from __future__ import annotations

import argparse
import contextlib
import dataclasses
import hashlib
import ipaddress
import json
import os
import re
import shutil
import sqlite3
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Iterator

SCHEMA_VERSION = 2
MAX_INT = (1 << 63) - 1
NAME_RE = re.compile(r"^[A-Za-z0-9_.@+\-]{1,128}$")

class PolicyError(ValueError):
    """Invalid or unsafe policy; no system changes may follow this error."""

class PermissionDenied(PolicyError):
    pass

@dataclasses.dataclass(frozen=True)
class Actor:
    id: str
    role: str = "reseller"
    permissions: dict[str, str] = dataclasses.field(default_factory=dict)

    def can(self, resource: str, action: str, owner: str | None = None) -> bool:
        if self.role == "owner":
            return True
        scope = self.permissions.get(f"{resource}.{action}", "none")
        return scope == "all" or (scope == "own" and owner is not None and owner == self.id)

    def require(self, resource: str, action: str, owner: str | None = None) -> None:
        if not self.can(resource, action, owner):
            raise PermissionDenied("This actor is not authorized for that resource and owner")


def integer(value: Any, minimum: int = 0, maximum: int = MAX_INT) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise PolicyError(f"Expected an integer in [{minimum}, {maximum}]")
    return value


def normalize_ip(raw: str) -> str:
    """Canonical IPv4/IPv6; collapse IPv4-mapped IPv6; reject zones/ports."""
    if not isinstance(raw, str) or "%" in raw:
        raise PolicyError("Address must be a plain IPv4/IPv6 literal without a zone")
    text = raw.strip()
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1]
    try:
        value = ipaddress.ip_address(text)
    except ValueError as exc:
        raise PolicyError("Invalid IP literal") from exc
    if isinstance(value, ipaddress.IPv6Address) and value.ipv4_mapped:
        value = value.ipv4_mapped
    return value.compressed


def _networks(values: list[str]) -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
    result = []
    for item in values:
        try:
            result.append(ipaddress.ip_network(item, strict=False))
        except ValueError as exc:
            raise PolicyError("Invalid exempt IP/CIDR") from exc
    return tuple(result)


def normalize_ports(values: Any) -> tuple[int, ...]:
    if not isinstance(values, (list, tuple)) or len(values) > 512:
        raise PolicyError("Ports must be a list of at most 512 explicit integers")
    return tuple(sorted(set(integer(v, 1, 65535) for v in values)))


def jail_for(ports: tuple[int, ...]) -> str:
    digest = hashlib.sha256(",".join(map(str, ports)).encode()).hexdigest()[:12]
    return "dark-xray-" + digest


@dataclasses.dataclass(frozen=True)
class ClientPolicy:
    email: str
    owner: str
    limit_ip: int
    ports: tuple[int, ...]

@dataclasses.dataclass(frozen=True)
class Policy:
    clients: dict[str, ClientPolicy]
    enforce: bool = False
    window_seconds: int = 120
    ban_seconds: int = 1800
    source_mode: str = "opaque"
    original_ip_verified: bool = False
    protected_ports: tuple[int, ...] = (22, 2053, 2096)
    trusted_peers: tuple[str, ...] = ()
    exempt_ips: tuple[str, ...] = ()
    allow_private_sources: bool = False
    node_id: str = "local"

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Policy":
        if not isinstance(raw, dict) or raw.get("schema") != 1:
            raise PolicyError("Policy schema must be 1")
        protected = normalize_ports(raw.get("protected_ports", [22, 2053, 2096]))
        if 22 not in protected:
            raise PolicyError("The protected port set must include 22 plus your actual SSH/panel ports")
        clients: dict[str, ClientPolicy] = {}
        source = raw.get("clients", {})
        if not isinstance(source, dict) or len(source) > 100000:
            raise PolicyError("Invalid client map")
        for email, row in source.items():
            if not isinstance(email, str) or not NAME_RE.fullmatch(email) or not isinstance(row, dict):
                raise PolicyError("Invalid client email")
            owner = row.get("owner", "")
            if not isinstance(owner, str) or not NAME_RE.fullmatch(owner):
                raise PolicyError("Invalid owner")
            ports = normalize_ports(row.get("ports", []))
            if not ports:
                raise PolicyError(f"No data ports configured for {email}")
            if set(ports).intersection(protected):
                raise PolicyError("A data port overlaps a protected management port; refusing policy")
            clients[email] = ClientPolicy(email, owner, integer(row.get("limit_ip", 0), 0, 1000), ports)
        mode = raw.get("source_mode", "opaque")
        if mode not in {"direct", "trusted-proxy", "opaque"}:
            raise PolicyError("Unknown source mode")
        for flag in ("enforce", "original_ip_verified", "allow_private_sources"):
            if flag in raw and not isinstance(raw[flag], bool):
                raise PolicyError(f"{flag} must be boolean")
        peers = tuple(normalize_ip(v) for v in raw.get("trusted_peers", []))
        exempt = raw.get("exempt_ips", [])
        if not isinstance(exempt, list):
            raise PolicyError("Exempt IPs must be a list")
        _networks(exempt)
        node = raw.get("node_id", "local")
        if not isinstance(node, str) or not NAME_RE.fullmatch(node):
            raise PolicyError("Invalid node ID")
        return cls(clients, raw.get("enforce", False),
                   integer(raw.get("window_seconds", 120), 10, 3600),
                   integer(raw.get("ban_seconds", 1800), 10, 86400), mode,
                   raw.get("original_ip_verified", False), protected, peers, tuple(exempt),
                   raw.get("allow_private_sources", False), node)

    def exemption(self, ip: str) -> str | None:
        address = ipaddress.ip_address(ip)
        if ip in self.trusted_peers:
            return "trusted_peer"
        if address.is_loopback or address.is_unspecified or address.is_multicast or address.is_link_local:
            return "special_address"
        if not self.allow_private_sources and address.is_private:
            return "private_or_reserved_address"
        if any(address.version == n.version and address in n for n in _networks(list(self.exempt_ips))):
            return "explicit_exemption"
        return None

    def assert_enforcement_safe(self) -> None:
        if not self.enforce:
            raise PolicyError("Policy.enforce is false; monitor mode is the default")
        if self.source_mode != "direct" or not self.original_ip_verified:
            raise PolicyError("Enforcement requires verified DIRECT packet-source IP on this host; forwarded/tunnel IP is not enough")


def load_policy(path: Path, apply: bool = False) -> Policy:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 16 * 1024 * 1024:
        raise PolicyError("Policy must be a regular non-symlink file under 16 MiB")
    if apply:
        info = path.stat()
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o022:
            raise PolicyError("Enforcement policy must be owned by the running user and not group/world writable")
    value = Policy.from_dict(json.loads(path.read_text(encoding="utf-8")))
    if apply:
        value.assert_enforcement_safe()
    return value


class Store:
    """Transactional SQLite policy and durable ledgers; no foreign-key cascade."""
    def __init__(self, path: str | Path):
        self.path = str(path)
        if self.path != ":memory:":
            parent = Path(self.path).resolve().parent
            parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            if Path(self.path).is_symlink():
                raise PolicyError("Database symlinks are not accepted")
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.path, timeout=30, isolation_level=None, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("PRAGMA busy_timeout=30000")
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1, SCHEMA_VERSION):
            raise PolicyError("Unrecognized database schema; refusing automatic downgrade")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS owners(
          id TEXT PRIMARY KEY, quota_bytes INTEGER NOT NULL DEFAULT 0,
          max_clients INTEGER NOT NULL DEFAULT 0, manual INTEGER NOT NULL DEFAULT 0,
          account_disabled INTEGER NOT NULL DEFAULT 0,
          period INTEGER NOT NULL DEFAULT 0, credit INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS clients(
          id TEXT PRIMARY KEY, owner TEXT NOT NULL, limit_ip INTEGER NOT NULL DEFAULT 0,
          quota_bytes INTEGER NOT NULL DEFAULT 0, used_bytes INTEGER NOT NULL DEFAULT 0,
          manual INTEGER NOT NULL DEFAULT 0, expires_at INTEGER NOT NULL DEFAULT 0);
        CREATE INDEX IF NOT EXISTS clients_owner ON clients(owner);
        CREATE TABLE IF NOT EXISTS traffic_ledger(
          event_id TEXT PRIMARY KEY, owner TEXT NOT NULL, client_id TEXT NOT NULL,
          period INTEGER NOT NULL, up_bytes INTEGER NOT NULL, down_bytes INTEGER NOT NULL,
          observed_at REAL NOT NULL);
        CREATE INDEX IF NOT EXISTS ledger_owner_period ON traffic_ledger(owner,period);
        CREATE TABLE IF NOT EXISTS money_ledger(
          event_id TEXT PRIMARY KEY, owner TEXT NOT NULL, amount INTEGER NOT NULL,
          kind TEXT NOT NULL, reference TEXT NOT NULL DEFAULT '', at REAL NOT NULL);
        CREATE UNIQUE INDEX IF NOT EXISTS one_refund ON money_ledger(reference)
          WHERE kind='refund';
        CREATE TABLE IF NOT EXISTS observations(
          client_id TEXT NOT NULL, ip TEXT NOT NULL, node TEXT NOT NULL,
          first_seen REAL NOT NULL, last_seen REAL NOT NULL, granted INTEGER NOT NULL,
          PRIMARY KEY(client_id,ip,node));
        CREATE INDEX IF NOT EXISTS observation_window ON observations(client_id,last_seen);
        CREATE TABLE IF NOT EXISTS bans(
          jail TEXT NOT NULL, ip TEXT NOT NULL, client_id TEXT NOT NULL,
          node TEXT NOT NULL, expires_at REAL NOT NULL, state TEXT NOT NULL,
          PRIMARY KEY(jail,ip,node));
        CREATE TABLE IF NOT EXISTS events(
          id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL,
          owner TEXT NOT NULL, client_id TEXT NOT NULL, ip TEXT NOT NULL,
          node TEXT NOT NULL, detail TEXT NOT NULL, at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS api_admins(
          id TEXT PRIMARY KEY, role TEXT NOT NULL, password_hash TEXT NOT NULL,
          permissions TEXT NOT NULL, disabled INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS sessions(
          digest TEXT PRIMARY KEY, admin_id TEXT NOT NULL, expires_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS login_attempts(
          source TEXT PRIMARY KEY, attempts INTEGER NOT NULL DEFAULT 0, start_at REAL NOT NULL);
        
        """)
        columns={row[1] for row in self.db.execute("PRAGMA table_info(owners)")}
        if "account_disabled" not in columns:
            self.db.execute("ALTER TABLE owners ADD COLUMN account_disabled INTEGER NOT NULL DEFAULT 0")
        self.db.execute("PRAGMA user_version=2")
        if self.path != ":memory:":
            os.chmod(self.path, 0o600)

    @contextlib.contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                yield self.db
                self.db.execute("COMMIT")
            except BaseException:
                self.db.execute("ROLLBACK")
                raise

    def close(self) -> None:
        with self.lock:
            self.db.close()

    def register_owner(self, actor: Actor, owner: str, quota_bytes: int = 0,
                       max_clients: int = 0, manual: bool | None = None) -> None:
        actor.require("owners", "edit", owner)
        if not NAME_RE.fullmatch(owner):
            raise PolicyError("Invalid owner name")
        integer(quota_bytes); integer(max_clients, 0, 1000000)
        if manual is not None and not isinstance(manual, bool):
            raise PolicyError("manual must be boolean")
        with self.transaction() as db:
            previous = db.execute("SELECT manual FROM owners WHERE id=?", (owner,)).fetchone()
            preserve_manual = previous["manual"] if previous and manual is None else int(bool(manual))
            count = db.execute("SELECT COUNT(*) FROM clients WHERE owner=?", (owner,)).fetchone()[0]
            if max_clients and count > max_clients:
                raise PolicyError("The requested client quota is below the current number of records")
            db.execute("""INSERT INTO owners(id,quota_bytes,max_clients,manual) VALUES(?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET quota_bytes=excluded.quota_bytes,
              max_clients=excluded.max_clients,manual=excluded.manual""",
                       (owner, quota_bytes, max_clients, preserve_manual))

    @staticmethod
    def _usage(db: sqlite3.Connection, owner: str, period: int | None = None) -> int:
        sql = "SELECT COALESCE(SUM(up_bytes+down_bytes),0) FROM traffic_ledger WHERE owner=?"
        args: tuple = (owner,)
        if period is not None:
            sql += " AND period=?"; args = (owner, period)
        return int(db.execute(sql, args).fetchone()[0])

    def register_client(self, actor: Actor, client_id: str, owner: str,
                        limit_ip: int = 1, quota_bytes: int = 0, price: int = 0,
                        order_id: str | None = None) -> bool:
        actor.require("clients", "create", owner)
        if not NAME_RE.fullmatch(client_id):
            raise PolicyError("Invalid client ID")
        integer(limit_ip, 0, 1000); integer(quota_bytes); integer(price)
        if order_id is not None and (not isinstance(order_id, str) or not 1 <= len(order_id) <= 256):
            raise PolicyError("Order ID must be a nonempty string under 257 characters")
        if price and not order_id:
            raise PolicyError("A paid creation requires a unique order ID")
        with self.transaction() as db:
            if order_id:
                previous = db.execute("SELECT * FROM money_ledger WHERE event_id=?", (order_id,)).fetchone()
                if previous:
                    if (previous["kind"], previous["owner"], previous["amount"], previous["reference"]) != ("sale", owner, -price, client_id):
                        raise PolicyError("Idempotency key was reused with different order data")
                    return False
            r = db.execute("SELECT * FROM owners WHERE id=?", (owner,)).fetchone()
            if not r:
                raise PolicyError("Owner is not registered")
            if r["manual"] or r["account_disabled"] or r["quota_bytes"] and self._usage(db, owner, r["period"]) >= r["quota_bytes"]:
                raise PolicyError("Owner is blocked or over traffic quota")
            count = db.execute("SELECT COUNT(*) FROM clients WHERE owner=?", (owner,)).fetchone()[0]
            if r["max_clients"] and count >= r["max_clients"]:
                raise PolicyError("Owner client quota is full")
            if price > r["credit"]:
                raise PolicyError("Insufficient credit")
            db.execute("INSERT INTO clients(id,owner,limit_ip,quota_bytes) VALUES(?,?,?,?)", (client_id, owner, limit_ip, quota_bytes))
            if order_id:
                db.execute("INSERT INTO money_ledger VALUES(?,?,?,?,?,?)",
                           (order_id, owner, -price, "sale", client_id, time.time()))
            if price:
                db.execute("UPDATE owners SET credit=credit-? WHERE id=?", (price, owner))
            return True

    def edit_client(self, actor: Actor, client_id: str, *, limit_ip: int | None = None,
                    quota_bytes: int | None = None, manual: bool | None = None,
                    expires_at: int | None = None) -> None:
        with self.transaction() as db:
            u = db.execute("SELECT * FROM clients WHERE id=?", (client_id,)).fetchone()
            if not u: raise PolicyError("Client does not exist")
            actor.require("clients", "edit", u["owner"])
            changes: dict[str, int] = {}
            if limit_ip is not None: changes["limit_ip"] = integer(limit_ip, 0, 1000)
            if quota_bytes is not None: changes["quota_bytes"] = integer(quota_bytes)
            if expires_at is not None: changes["expires_at"] = integer(expires_at)
            if manual is not None:
                if not isinstance(manual, bool): raise PolicyError("manual must be boolean")
                changes["manual"] = int(manual)
            if changes:
                db.execute("UPDATE clients SET "+",".join(k+"=?" for k in changes)+" WHERE id=?", (*changes.values(), client_id))

    def delete_client(self, actor: Actor, client_id: str) -> None:
        with self.transaction() as db:
            u = db.execute("SELECT owner FROM clients WHERE id=?", (client_id,)).fetchone()
            if not u: raise PolicyError("Client does not exist")
            actor.require("clients", "delete", u["owner"])
            db.execute("DELETE FROM clients WHERE id=?", (client_id,))
            # Historical traffic/money remain attached to the original owner.
            db.execute("DELETE FROM observations WHERE client_id=?", (client_id,))

    def record_usage(self, event_id: str, client_id: str, up: int, down: int,
                     *, now: float | None = None) -> bool:
        if not isinstance(event_id, str) or not 1 <= len(event_id) <= 256:
            raise PolicyError("A stable unique event ID is required")
        integer(up); integer(down)
        if up + down > MAX_INT: raise PolicyError("Counter overflow")
        with self.transaction() as db:
            previous = db.execute("SELECT * FROM traffic_ledger WHERE event_id=?", (event_id,)).fetchone()
            if previous:
                if (previous["client_id"], previous["up_bytes"], previous["down_bytes"]) != (client_id, up, down):
                    raise PolicyError("Idempotency key reused for different traffic data")
                return False
            u = db.execute("SELECT * FROM clients WHERE id=?", (client_id,)).fetchone()
            if not u: raise PolicyError("Client does not exist")
            r = db.execute("SELECT * FROM owners WHERE id=?", (u["owner"],)).fetchone()
            if u["used_bytes"] + up + down > MAX_INT or self._usage(db, u["owner"]) + up + down > MAX_INT:
                raise PolicyError("Ledger counter overflow")
            db.execute("INSERT INTO traffic_ledger VALUES(?,?,?,?,?,?,?)",
                       (event_id, u["owner"], client_id, r["period"], up, down, time.time() if now is None else now))
            db.execute("UPDATE clients SET used_bytes=used_bytes+? WHERE id=?", (up+down, client_id))
            return True

    def reset_client_usage(self, actor: Actor, client_id: str) -> None:
        with self.transaction() as db:
            u = db.execute("SELECT owner FROM clients WHERE id=?", (client_id,)).fetchone()
            if not u: raise PolicyError("Client does not exist")
            actor.require("clients", "reset", u["owner"])
            db.execute("UPDATE clients SET used_bytes=0 WHERE id=?", (client_id,))

    def reset_owner_period(self, actor: Actor, owner: str) -> None:
        actor.require("owners", "reset", owner)
        with self.transaction() as db:
            if not db.execute("SELECT id FROM owners WHERE id=?", (owner,)).fetchone(): raise PolicyError("Owner does not exist")
            db.execute("UPDATE owners SET period=period+1 WHERE id=?", (owner,))
            # Does not alter manual flags, expiration, credit or any ledger row.

    def owner_stats(self, actor: Actor, owner: str) -> dict[str, Any]:
        actor.require("owners", "read", owner)
        with self.lock:
            r = self.db.execute("SELECT * FROM owners WHERE id=?", (owner,)).fetchone()
            if not r: raise PolicyError("Owner does not exist")
            return {**dict(r), "used_bytes": self._usage(self.db, owner, r["period"]),
                    "lifetime_used_bytes": self._usage(self.db, owner),
                    "client_count": self.db.execute("SELECT COUNT(*) FROM clients WHERE owner=?", (owner,)).fetchone()[0]}

    def list_clients(self, actor: Actor) -> list[dict[str, Any]]:
        with self.lock:
            return [dict(r) for r in self.db.execute("SELECT * FROM clients ORDER BY id")
                    if actor.can("clients", "read", r["owner"])]

    def client_reasons(self, client_id: str, now: float | None = None) -> list[str]:
        with self.lock:
            u = self.db.execute("SELECT * FROM clients WHERE id=?", (client_id,)).fetchone()
            if not u: return ["missing_client"]
            r = self.db.execute("SELECT * FROM owners WHERE id=?", (u["owner"],)).fetchone()
            reasons = []
            if u["manual"]: reasons.append("client_manual")
            if u["expires_at"] and u["expires_at"] <= (time.time() if now is None else now): reasons.append("expired")
            if u["quota_bytes"] and u["used_bytes"] >= u["quota_bytes"]: reasons.append("client_quota")
            if not r: reasons.append("missing_owner")
            else:
                if r["manual"]: reasons.append("owner_manual")
                if r["account_disabled"]: reasons.append("owner_account_disabled")
                if r["quota_bytes"] and self._usage(self.db, r["id"], r["period"]) >= r["quota_bytes"]: reasons.append("owner_quota")
            return reasons

    def credit(self, actor: Actor, owner: str, amount: int, event_id: str) -> bool:
        actor.require("finance", "credit", owner); integer(amount, 1)
        if not event_id or len(event_id) > 256: raise PolicyError("Unique event ID required")
        with self.transaction() as db:
            old = db.execute("SELECT * FROM money_ledger WHERE event_id=?", (event_id,)).fetchone()
            if old:
                if (old["owner"],old["amount"],old["kind"]) != (owner,amount,"credit"): raise PolicyError("Idempotency collision")
                return False
            r=db.execute("SELECT * FROM owners WHERE id=?",(owner,)).fetchone()
            if not r: raise PolicyError("Owner does not exist")
            if r["credit"]+amount>MAX_INT: raise PolicyError("Credit overflow")
            db.execute("INSERT INTO money_ledger VALUES(?,?,?,?,?,?)",(event_id,owner,amount,"credit","",time.time()))
            db.execute("UPDATE owners SET credit=credit+? WHERE id=?",(amount,owner))
            return True

    def refund(self, actor: Actor, order_id: str, event_id: str) -> bool:
        if not isinstance(event_id, str) or not 1 <= len(event_id) <= 256:
            raise PolicyError("A unique refund ID is required")
        with self.transaction() as db:
            order=db.execute("SELECT * FROM money_ledger WHERE event_id=? AND kind='sale'",(order_id,)).fetchone()
            if not order: raise PolicyError("Sale does not exist")
            actor.require("finance","refund",order["owner"])
            previous=db.execute("SELECT * FROM money_ledger WHERE event_id=?",(event_id,)).fetchone()
            if previous:
                if previous["kind"] != "refund" or previous["reference"] != order_id: raise PolicyError("Idempotency collision")
                return False
            if db.execute("SELECT event_id FROM money_ledger WHERE kind='refund' AND reference=?",(order_id,)).fetchone(): raise PolicyError("Sale already refunded")
            amount=-order["amount"]
            owner=db.execute("SELECT credit FROM owners WHERE id=?",(order["owner"],)).fetchone()
            if not owner or owner["credit"]+amount>MAX_INT:
                raise PolicyError("Owner missing or credit overflow")
            db.execute("INSERT INTO money_ledger VALUES(?,?,?,?,?,?)",(event_id,order["owner"],amount,"refund",order_id,time.time()))
            db.execute("UPDATE owners SET credit=credit+? WHERE id=?",(amount,order["owner"]))
            return True

    def backup(self, destination: Path) -> None:
        if destination.exists(): raise PolicyError("Backup target already exists")
        destination.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        with self.lock:
            other=sqlite3.connect(destination)
            try:self.db.backup(other)
            finally:other.close()
        os.chmod(destination,0o600)


@dataclasses.dataclass(frozen=True)
class Observation:
    email: str
    ip: str
    timestamp: float


def parse_access_line(line: str, observed_at: float | None = None) -> Observation | None:
    """Parse only accepted source IP and email, never destinations/forwarded headers.

    Rejected/malformed lines are ignored. Log timestamps are deliberately NOT
    trusted to renew leases; the tail reader calls this only for newly read lines.
    Replaying an old log is safe only in --once monitor mode, never --apply.
    """
    if not isinstance(line,str) or len(line)>16384 or " accepted " not in line:
        return None
    match=re.search(r"(?:^|\s)(?:from\s+)?((?:(?:tcp|udp):)?(?:\[[0-9a-fA-F:.]+\]|[0-9a-fA-F:.]+):\d+)\s+accepted\s",line)
    user=re.search(r"\bemail:\s*([A-Za-z0-9_.@+\-]{1,128})(?=\s|\]|$)",line)
    if not match or not user:return None
    source=re.sub(r"^(?:tcp|udp):","",match.group(1))
    try:
        host,port=source.rsplit(":",1);integer(int(port),1,65535);ip=normalize_ip(host)
    except (ValueError,PolicyError):return None
    return Observation(user.group(1),ip,time.time() if observed_at is None else observed_at)


class Fail2BanExecutor:
    """Only dedicated, port-scoped DARK jails; never arbitrary shell commands."""
    def __init__(self, policy: Policy, runner: Callable[...,Any] = subprocess.run,
                 executable: str | None = None):
        policy.assert_enforcement_safe()
        self.policy=policy;self.runner=runner
        self.executable=executable or shutil.which("fail2ban-client") or "/usr/bin/fail2ban-client"
        self.jails={jail_for(c.ports) for c in policy.clients.values()}

    def _run(self,*args: str) -> str:
        result=self.runner([self.executable,*args],capture_output=True,text=True,timeout=8,check=False)
        if result.returncode!=0:raise PolicyError("Fail2ban command failed; enforcement was NOT confirmed: "+result.stderr.strip()[:300])
        return result.stdout.strip()

    def check(self) -> None:
        self._run("ping")
        for jail in sorted(self.jails):
            self._run("status",jail)
            value=self._run("get",jail,"bantime")
            if value!=str(self.policy.ban_seconds):raise PolicyError("Jail bantime does not match the reviewed policy")

    def ban(self,jail: str,ip: str) -> None:
        if jail not in self.jails:raise PolicyError("Unmanaged jail")
        self._run("set",jail,"banip",normalize_ip(ip))

    def unban(self,jail: str,ip: str) -> None:
        if jail not in self.jails:raise PolicyError("Unmanaged jail")
        self._run("set",jail,"unbanip",normalize_ip(ip))


class Guard:
    def __init__(self,policy: Policy,store: Store,executor: Fail2BanExecutor | None = None):
        self.policy=policy;self.store=store;self.executor=executor
        if executor:policy.assert_enforcement_safe()

    def observe(self,email: str,raw_ip: str,now: float | None = None) -> dict[str,Any]:
        now=time.time() if now is None else float(now)
        if not (0<=now<1e15):raise PolicyError("Invalid observation time")
        ip=normalize_ip(raw_ip);p=self.policy;c=p.clients.get(email)
        if not c:return {"decision":"ignored","reason":"unknown_client","applied":False}
        reason=p.exemption(ip)
        if reason:return {"decision":"exempt","reason":reason,"applied":False}
        if p.source_mode!="direct" or not p.original_ip_verified:
            with self.store.transaction() as db:
                previous=db.execute("SELECT id FROM events WHERE client_id=? AND ip=? AND node=? AND kind='source_ambiguous' AND at>?",(email,ip,p.node_id,now-p.window_seconds)).fetchone()
                if not previous:db.execute("INSERT INTO events(kind,owner,client_id,ip,node,detail,at) VALUES(?,?,?,?,?,?,?)",("source_ambiguous",c.owner,email,ip,p.node_id,"No IP ban: original packet source is not verified",now))
            return {"decision":"observe_only","reason":"unverified_original_packet_source","applied":False}
        jail=jail_for(c.ports);violation=False
        with self.store.transaction() as db:
            existing=db.execute("SELECT * FROM bans WHERE jail=? AND ip=? AND node=? AND expires_at>? AND state='applied'",(jail,ip,p.node_id,now)).fetchone()
            if existing:return {"decision":"banned","reason":"active_ip_ban","applied":True,"until":existing["expires_at"]}
            leases=db.execute("SELECT ip,MIN(first_seen) AS first FROM observations WHERE client_id=? AND granted=1 AND last_seen>? GROUP BY ip ORDER BY first,ip",(email,now-p.window_seconds)).fetchall()
            active={r[0] for r in (leases[:c.limit_ip] if c.limit_ip else leases)}
            # Lowering a limit takes effect at the next observation. Existing grants
            # above the new limit are rejected on their next logged connection event.
            granted=c.limit_ip==0 or ip in active or len(active)<c.limit_ip
            violation=not granted
            db.execute("""INSERT INTO observations VALUES(?,?,?,?,?,?)
            ON CONFLICT(client_id,ip,node) DO UPDATE SET last_seen=excluded.last_seen,
                granted=excluded.granted""",(email,ip,p.node_id,now,now,int(granted)))
            if violation:
                # Retain one diagnostic per client/source/window rather than log every packet.
                duplicate=db.execute("SELECT id FROM events WHERE client_id=? AND ip=? AND kind='violation' AND at>?",(email,ip,now-p.window_seconds)).fetchone()
                if not duplicate:db.execute("INSERT INTO events(kind,owner,client_id,ip,node,detail,at) VALUES(?,?,?,?,?,?,?)",("violation",c.owner,email,ip,p.node_id,"distinct recent source IP quota exceeded",now))
            # Bound recent IP memory, but NEVER prune the usage or money ledgers.
            db.execute("DELETE FROM observations WHERE last_seen<?",(now-max(86400,p.window_seconds*2),))
        if not violation:return {"decision":"allow","applied":False,"active_ips":len(active|{ip}),"limit_ip":c.limit_ip}
        if not self.executor:return {"decision":"violation","applied":False,"reason":"monitor_mode","jail":jail}
        try:self.executor.ban(jail,ip)
        except (PolicyError,subprocess.SubprocessError,OSError) as exc:
            with self.store.transaction() as db:db.execute("INSERT INTO events(kind,owner,client_id,ip,node,detail,at) VALUES(?,?,?,?,?,?,?)",("enforcement_failed",c.owner,email,ip,p.node_id,str(exc)[:500],now))
            return {"decision":"enforcement_failed","applied":False,"reason":str(exc)}
        until=now+p.ban_seconds
        with self.store.transaction() as db:
            db.execute("""INSERT INTO bans VALUES(?,?,?,?,?,?) ON CONFLICT(jail,ip,node)
            DO UPDATE SET expires_at=excluded.expires_at,state=excluded.state,client_id=excluded.client_id""",(jail,ip,email,p.node_id,until,"applied"))
        return {"decision":"banned","applied":True,"confirmation":getattr(self.executor,"confirmation","fail2ban_command_accepted"),"packet_block_verified":False,"until":until,"jail":jail,"scope":"source-IP and configured data ports; other clients on this IP may also be affected"}

    def unban(self,actor: Actor,jail: str,ip: str) -> None:
        # A source-address ban is not safely owned by one reseller behind shared NAT.
        if actor.role!="owner":raise PermissionDenied("Global IP unban is owner-only")
        if not self.executor:raise PolicyError("No enforcement adapter is connected; no unban was executed")
        ip=normalize_ip(ip);self.executor.unban(jail,ip)
        with self.store.transaction() as db:
            db.execute("UPDATE bans SET state='released',expires_at=? WHERE jail=? AND ip=? AND node=?",(time.time(),jail,ip,self.policy.node_id))
            db.execute("DELETE FROM observations WHERE ip=? AND node=? AND granted=0",(ip,self.policy.node_id))


def render_fail2ban(policy: Policy) -> dict[str,str]:
    """Return reviewable config text; never write /etc or restart a daemon."""
    policy.assert_enforcement_safe()
    result={"filter.d/dark-xray-policy.conf":"# DARK XRAY generated filter; policy module invokes banip explicitly.\n[Definition]\nfailregex = ^DARK-XRAY VIOLATION <HOST>$\nignoreregex =\n"}
    ignore="127.0.0.1/8 ::1 "+" ".join((*policy.trusted_peers,*policy.exempt_ips))
    sections=[]
    for ports in sorted({c.ports for c in policy.clients.values()}):
        name=jail_for(ports)
        sections.append(f"""[{name}]
# Review protected ports and shared-NAT implications before enabling.
enabled = true
filter = dark-xray-policy
logpath = /var/log/dark-xray-policy/violations.log
backend = polling
maxretry = 1
findtime = {policy.window_seconds}
bantime = {policy.ban_seconds}
ignoreip = {ignore}
action = nftables[type=multiport, name={name}, port="{','.join(map(str,ports))}", protocol="tcp,udp"]
""")
    result["jail.d/dark-xray-policy.local"]="# DARK XRAY — dedicated jails, separate from 3x-ipl\n"+"\n".join(sections)
    return result


def follow_new_lines(path: Path,stop: threading.Event,interval: float=.4) -> Iterator[str]:
    """Tail NEW complete lines; handle rotation/truncation; bound partial buffers."""
    handle=None;identity=None;pending=""
    try:
        while not stop.is_set():
            try:info=path.stat()
            except FileNotFoundError:
                stop.wait(interval);continue
            current=(info.st_dev,info.st_ino)
            if handle is None or identity!=current:
                if handle:handle.close()
                handle=path.open("r",encoding="utf-8",errors="replace")
                # Do not replay old access records at process start/rotation.
                handle.seek(0,os.SEEK_END);identity=current;pending=""
            elif info.st_size<handle.tell():
                handle.seek(0);pending=""
            chunk=handle.read(8192)
            if not chunk:
                stop.wait(interval);continue
            pending+=chunk
            while "\n" in pending:
                line,pending=pending.split("\n",1)
                if len(line)<=16384:yield line
            if len(pending)>32768:pending=""
    finally:
        if handle:handle.close()


def main(argv: list[str] | None=None) -> int:
    parser=argparse.ArgumentParser(description=__doc__,formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--policy",type=Path,required=True)
    parser.add_argument("--database",type=Path,default=Path("./data/policy.sqlite3"))
    parser.add_argument("--access-log",type=Path)
    parser.add_argument("--once",action="store_true",help="Replay a file in MONITOR mode only")
    parser.add_argument("--apply",action="store_true",help="Explicitly enable Fail2ban effects after safeguards")
    parser.add_argument("--acknowledge-shared-nat",action="store_true")
    parser.add_argument("--render-fail2ban",type=Path,metavar="OUTPUT_DIR")
    args=parser.parse_args(argv)
    try:
        policy=load_policy(args.policy,args.apply)
        if args.render_fail2ban:
            files=render_fail2ban(policy)
            for relative,text in files.items():
                target=args.render_fail2ban/relative
                target.parent.mkdir(parents=True,exist_ok=True)
                target.write_text(text,encoding="utf8")
            print("Generated files for review only; nothing installed or restarted.")
            return 0
        if not args.access_log:raise PolicyError("--access-log is required")
        if args.apply and args.once:raise PolicyError("Never replay old logs while enforcement is enabled")
        if args.apply and not args.acknowledge_shared_nat:raise PolicyError("Explicit shared-NAT acknowledgement is required")
        if args.apply and os.geteuid()!=0:raise PolicyError("Fail2ban enforcement requires root; monitor mode does not")
        if args.access_log.is_symlink():raise PolicyError("Access-log symlinks are not accepted")
        executor=Fail2BanExecutor(policy) if args.apply else None
        if executor:executor.check()
        store=Store(args.database);guard=Guard(policy,store,executor)
        print(json.dumps({"service":"DARK XRAY IP guard","apply":bool(executor),"node":policy.node_id,"note":"recent IP window, not exact concurrent sessions"}))
        stop=threading.Event()
        log_handle=args.access_log.open("r",encoding="utf8",errors="replace") if args.once else None
        lines=log_handle if args.once else follow_new_lines(args.access_log,stop)
        try:
            for line in lines:
                observation=parse_access_line(line)
                if observation:
                    result=guard.observe(observation.email,observation.ip,observation.timestamp)
                    if result["decision"] not in {"allow","ignored","exempt"}:
                        print(json.dumps({"email":observation.email,"ip":observation.ip,**result}),flush=True)
        except KeyboardInterrupt:stop.set()
        finally:
            if log_handle:log_handle.close()
            store.close()
        return 0
    except (PolicyError,sqlite3.Error,OSError,json.JSONDecodeError) as exc:
        print("ERROR: "+str(exc),file=sys.stderr);return 2

if __name__=="__main__":
    raise SystemExit(main())
