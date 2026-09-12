#!/usr/bin/env bash
# Independent fresh-install entrypoint. Existing panel installations are refused.
set -Eeuo pipefail
umask 077
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
[[ $EUID -eq 0 ]] || { echo 'Use sudo bash setup.sh on a separate test VPS.' >&2; exit 1; }
command -v python3 >/dev/null || { echo 'Install python3 and python3-venv first.' >&2; exit 1; }
exec python3 "$ROOT/tools/provision.py" "$@"
