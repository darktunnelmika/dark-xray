#!/usr/bin/env bash
# Prepares DARK only; no other panel, remote shell script, firewall, or system service is installed.
set -Eeuo pipefail
umask 077
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
cd "$ROOT"
command -v python3 >/dev/null || { echo 'Python 3.11+ is required.' >&2; exit 1; }
python3 -c 'import sys; assert sys.version_info >= (3, 11), "Python 3.11+ is required"'
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
if [[ ! -e config.json ]]; then
  cp config.example.json config.json
  chmod 600 config.json
fi
mkdir -p data
chmod 700 data
chmod +x darkxray
printf '\nDARK XRAY standalone environment prepared.\n'
printf 'No other panel is required or installed.\n'
printf 'Next: review config.json, then ./darkxray init --username dark\n'
printf 'Then: ./darkxray serve\n'
printf 'Xray-core itself must be provided separately; see README.fa.md.\n'
