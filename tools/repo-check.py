#!/usr/bin/env python3
"""Best-effort pre-publication checks. Not a complete secret scanner or audit."""
from pathlib import Path
import hashlib
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
SKIP = {".git", ".venv", "venv", "__pycache__", ".pytest_cache", "node_modules"}

# Public OFL assets are permitted ONLY at these exact paths and cryptographic
# digests. Unreviewed font binaries remain prohibited, as do runtime/secrets.
# License files are mandatory. Do not relax the global binary extension ban.
APPROVED_PUBLIC_FONTS = {
    "web/fonts/vazirmatn-arabic-variable.woff2": (
        "84a382e46c30fb4f73d0e3800c16d0af15888e2731e57fa5f93e2c29a2c6a957",
        "web/fonts/licenses/vazirmatn-OFL.txt",
    ),
    "web/fonts/manrope-latin-variable.woff2": (
        "a30ddcd349703aff7464c34bef3fffdff405ee50c113440d7c8693c02d210972",
        "web/fonts/licenses/manrope-OFL.txt",
    ),
    "web/fonts/jetbrains-mono-latin-variable.woff2": (
        "18be452724bfdc236c074ca94a249a7f41a86752c7d04ab258ce9ed5651f6a7e",
        "web/fonts/licenses/jetbrains-mono-OFL.txt",
    ),
}

PATTERNS = {
    "private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |ENCRYPTED )?PRIVATE KEY-----"),
    "GitHub token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b|\bgithub_pat_[A-Za-z0-9_]{60,}"),
    "AWS access key": re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    "Telegram bot token": re.compile(r"\b[0-9]{8,12}:[A-Za-z0-9_-]{35}\b"),
}
REQUIRED = ["README.md", "README.en.md", "README.fa.md", "LICENSE", "THIRD-PARTY-NOTICES.md",
            "VERSION", "setup.sh", "config.example.json", ".gitignore", ".github/workflows/ci.yml",
            "backend/updated.py", "backend/update_bridge.py", "deploy/dark-xray-update.service",
            "web/update-center.js", "web/update-center.css"]

def main() -> int:
    errors = []
    count = 0
    for name in REQUIRED:
        if not (ROOT / name).is_file():
            errors.append(f"missing required file: {name}")
    for path in sorted(ROOT.rglob("*")):
        rel = path.relative_to(ROOT)
        if any(part in SKIP for part in rel.parts):
            continue
        if path.is_symlink():
            errors.append(f"symlink requires manual review: {rel}")
            continue
        if not path.is_file():
            continue
        count += 1
        if path.suffix in {".ttf", ".otf", ".woff", ".woff2"}:
            approved = APPROVED_PUBLIC_FONTS.get(rel.as_posix())
            if (
                approved is None
                or not (ROOT / approved[1]).is_file()
                or path.stat().st_size > 200_000
                or hashlib.sha256(path.read_bytes()).hexdigest() != approved[0]
            ):
                errors.append(f"unapproved public font or missing OFL license: {rel}")
            # Approved files are verified by digest, not a UTF-8 regex scan.
            continue
        if path.name in {"config.json", ".env", "credentials.json", "auth.json"} or (
            path.name.startswith(".env.") and path.name != ".env.example"
        ) or path.suffix in {".key", ".pem", ".p12", ".pfx", ".db", ".sqlite", ".sqlite3"}:
            errors.append(f"private/runtime/font file must not be published: {rel}")
        if any(part in {"data", "runtime", "backups"} for part in rel.parts[:-1]):
            errors.append(f"runtime file must not be published: {rel}")
        if path.stat().st_size > 25 * 1024 * 1024:
            errors.append(f"file exceeds browser upload limit: {rel}")
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for name, pattern in PATTERNS.items():
            if pattern.search(text):
                errors.append(f"possible {name} in {rel} (value suppressed)")
    if errors:
        print("Repository check failed:", file=sys.stderr)
        print("\n".join(errors), file=sys.stderr)
        return 1
    print(f"Repository checks passed for {count} files. Best-effort scan only.")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
