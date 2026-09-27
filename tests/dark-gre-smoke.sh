#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT="$ROOT/standalone/dark-gre/dark-gre.sh"
tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT

bash -n "$SCRIPT"
sed '$d' "$SCRIPT" > "$tmp"
# shellcheck disable=SC1090
source "$tmp"

NAME=testgre
LOCAL_PUBLIC=1.2.3.4
REMOTE_PUBLIC=5.6.7.8
LOCAL_TUN=10.77.10.1
REMOTE_TUN=10.77.10.2
PREFIX=30
PROFILE=balanced
MTU=1436
TXQLEN=1000

code="$(pair_code)"
decode_pair "$code"

[ "$P_NAME" = "$NAME" ]
[ "$P_IRAN_PUBLIC" = "$LOCAL_PUBLIC" ]
[ "$P_KHAREJ_PUBLIC" = "$REMOTE_PUBLIC" ]
[ "$P_IRAN_TUN" = "$LOCAL_TUN" ]
[ "$P_KHAREJ_TUN" = "$REMOTE_TUN" ]

bad="${code/DGR1-/DGR1-0}"
if decode_pair "$bad" 2>/dev/null; then
  echo "checksum test unexpectedly passed" >&2
  exit 1
fi

echo "dark-gre smoke: PASS"
