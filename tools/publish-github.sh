#!/usr/bin/env bash
# Run locally with your own authenticated GitHub CLI. Never paste tokens into chat.
set -Eeuo pipefail
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
visibility=private
case "${1:-}" in
  ""|--private) ;;
  --public) visibility=public ;;
  *) printf 'Usage: bash tools/publish-github.sh [--private|--public]\n' >&2; exit 2 ;;
esac
[[ $# -le 1 ]] || { echo 'Too many arguments.' >&2; exit 2; }
for tool in git gh python3; do
  command -v "$tool" >/dev/null 2>&1 || { echo "Install $tool locally first." >&2; exit 127; }
done
gh auth status --hostname github.com >/dev/null 2>&1 || {
  echo 'Authenticate locally first: gh auth login --hostname github.com --web' >&2
  exit 1
}
login="$(gh api --hostname github.com user --jq .login)"
[[ "$login" == darktunnelmika ]] || { echo 'Wrong GitHub account; expected darktunnelmika. No repository was created.' >&2; exit 1; }
repo="$login/dark-xray"
[[ ! -e "$root/.git" ]] || { echo 'Local Git history already exists. Refusing to overwrite it; use standard git push after reviewing the remote.' >&2; exit 1; }
if gh repo view "$repo" >/dev/null 2>&1; then
  echo "Repository $repo already exists. Nothing will be overwritten." >&2
  exit 1
fi
python3 "$root/tools/repo-check.py"
printf 'Creating %s with %s visibility.\n' "$repo" "$visibility"
read -r -p 'Type CREATE to authorize creation and upload: ' answer
[[ "$answer" == CREATE ]] || { echo 'Cancelled.'; exit 1; }
id="$(gh api --hostname github.com user --jq .id)"
[[ "$id" =~ ^[0-9]+$ ]] || { echo 'Could not verify account ID.' >&2; exit 1; }
cd "$root"
git init -b main
git config user.name "$login"
git config user.email "$id+$login@users.noreply.github.com"
git add .
# Browser ZIP uploads lose executable bits. CLI publication restores them.
while IFS= read -r -d '' file; do
  case "$file" in *.sh|darkxray|tests/fixtures/fake_xray.py) git update-index --chmod=+x -- "$file" ;; esac
done < <(git ls-files -z)
git commit -m 'Initial import: DARK XRAY v0.6 standalone lab'
if ! gh repo create "$repo" "--$visibility" \
  --description 'Independent cyber-dark Xray control panel with reseller access control and IP policies — experimental single-server build' \
  --source "$root" --remote origin --push; then
  echo 'Publication did not finish. The repository may already have been created. Inspect it and the local Git remote before retrying; do not force-push.' >&2
  exit 1
fi
gh repo view "$repo" --json nameWithOwner,url,visibility,defaultBranchRef
