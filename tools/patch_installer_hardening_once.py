#!/usr/bin/env python3
from pathlib import Path

def once(s,old,new):
    if new in s:return s
    if old not in s:raise RuntimeError('anchor missing: '+old[:140])
    return s.replace(old,new,1)

p=Path('install-online.sh');s=p.read_text()
s=once(s,'BRANCH="main"\n','SOURCE_REF="${DARK_XRAY_REF:-main}"\n')
anchor="""public_ipv4(){ curl -4fsS --max-time 5 https://api.ipify.org 2>/dev/null || ip -4 route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++)if($i=="src"){print $(i+1);exit}}'; }
ssh_port(){ if command -v sshd >/dev/null 2>&1; then sshd -T 2>/dev/null | awk '/^port /{print $2;exit}'; else echo 22; fi; }
"""
insert="""public_ipv4(){ curl -4fsS --max-time 5 https://api.ipify.org 2>/dev/null || ip -4 route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++)if($i=="src"){print $(i+1);exit}}'; }
ssh_port(){ if command -v sshd >/dev/null 2>&1; then sshd -T 2>/dev/null | awk '/^port /{print $2;exit}'; else echo 22; fi; }
valid_source_ref(){ [[ -n "$1" && ${#1} -le 200 && "$1" != -* && "$1" =~ ^[A-Za-z0-9._/@+-]+$ ]]; }
fetch_source(){
  local dest="$1" ref="$SOURCE_REF"
  valid_source_ref "$ref" || fail "Invalid DARK_XRAY_REF"
  mkdir -p "$dest"
  git -C "$dest" init -q
  git -C "$dest" remote add origin "$REPO"
  git -C "$dest" fetch -q --depth 1 origin "$ref" || fail "GitHub fetch failed for ref: $ref"
  git -C "$dest" checkout -q --detach FETCH_HEAD
  git -C "$dest" rev-parse HEAD
}
"""
s=once(s,anchor,insert)
old="""  valid_user dark || fail "valid_user rejected dark"; valid_domain panel.example.com || fail "valid_domain rejected panel.example.com"
"""
new="""  valid_user dark || fail "valid_user rejected dark"; valid_domain panel.example.com || fail "valid_domain rejected panel.example.com"
  valid_source_ref main || fail "valid_source_ref rejected main"; valid_source_ref 0123456789abcdef || fail "valid_source_ref rejected commit"; ! valid_source_ref --upload-pack=x || fail "valid_source_ref accepted option injection"
"""
s=once(s,old,new)
old="""      progress 15 "Downloading verified project source"; git clone --depth 1 --branch "$BRANCH" "$REPO" "$TMP/src" >/dev/null 2>&1 || fail "GitHub clone failed"
      progress 35 "Creating rollback snapshot and applying safe update"
      python3 "$TMP/src/tools/update.py" --source "$TMP/src" --non-interactive || fail "Update failed; see messages above"
"""
new="""      progress 15 "Downloading pinned project source"; FETCHED_SHA="$(fetch_source "$TMP/src")"; printf '  Source commit: %s\\n' "$FETCHED_SHA"
      progress 35 "Creating rollback snapshot and applying safe update"
      python3 "$TMP/src/tools/update.py" --source "$TMP/src" --non-interactive || fail "Update failed; see messages above"
"""
s=once(s,old,new)
old="""progress 25 "Cloning DARK XRAY from GitHub"; git clone --depth 1 --branch "$BRANCH" "$REPO" "$TMP/src" >/dev/null 2>&1 || fail "GitHub clone failed"
cd "$TMP/src"
"""
new="""progress 25 "Fetching DARK XRAY source"; FETCHED_SHA="$(fetch_source "$TMP/src")"; printf '  Source commit: %s\\n' "$FETCHED_SHA"
cd "$TMP/src"
"""
s=once(s,old,new)
# Surface the source ref in the plan without changing the 100-step UX.
old='printf "  SSH port     : %s\\n" "$DETECTED_SSH"\n'
new='printf "  SSH port     : %s\\n  Source ref   : %s\\n" "$DETECTED_SSH" "$SOURCE_REF"\n'
s=once(s,old,new)
p.write_text(s)
print('Installer ref-pinning patch applied')
